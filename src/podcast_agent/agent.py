"""The agent loop.

Claude gets the transcript and a toolset, writes its own plan, then decides the
order of work: which claims to check, which knowledge base queries to run, when to
fall back to web search, and how to fix anything a guardrail rejects. The loop
below runs tools, records every step, and stops when the agent calls `finish` or
the turn budget runs out.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, List

import anthropic

from .config import Settings
from .kb import KnowledgeBase
from .models import EpisodeReport, KeyNotes, RunMetadata
from .tools import AgentState, run_tool, tool_definitions
from .trace import Tracer

SYSTEM_PROMPT = """\
You are a podcast content analyst for an ad agency's content team. For each episode you produce three \
publishable artifacts by calling tools: a summary, show notes, and a fact-check table. These go out under \
clients' names, so accuracy beats polish.

How to work
- Open with a short numbered plan (3-6 lines): what you will produce and which claims you expect to check \
and how. Reviewers read it in the run log.
- Before each step, say in one sentence what you are about to do and why. That narration is the run log.
- Every artifact goes through its submit tool, which validates it. If a tool rejects your input, fix exactly \
what the error names and resubmit.
- Call `finish` once all three artifacts are accepted.

Summary
Plain prose for a show-notes page or newsletter. Cover the core themes, the key discussions, and the \
outcomes or opinions each speaker shared, attributed by name. No headings or bullet points.

Key notes
- Takeaways: each a standalone, specific sentence a listener could act on or share. No duplicates of each other.
- Quotes: short, punchy lines that work as social posts, copied verbatim from a single transcript line. \
Filler words have already been removed from the transcript. Prefer a mix of speakers.
- Topics: short tag-style labels (e.g. "async-communication", "fda-regulation").

Fact-checking
- Pick statements about the world that someone could check: named organisations and what they do, \
statistics, regulatory facts, historical events. Also include forward-looking predictions and personal \
anecdotes presented as fact, classified as such, so readers know they cannot be confirmed. Skip pure \
opinions and pleasantries.
- Restate each claim as one standalone sentence that names its subject.
- Search the knowledge base for every claim. Use web search when the knowledge base has nothing that \
actually addresses the claim, when the matching entry may be stale (check its as_of date against today), \
or when the claim is contested. Judge time-sensitive statements against today's date.
- verified: a source states the claim. possibly_inaccurate: a source contradicts it, shows it is outdated, \
or the claim overstates (e.g. "X isn't happening" when there is a documented counterexample). \
unverifiable: predictions, private anecdotes, or nothing found. Never upgrade a verdict on plausibility alone.
- confidence is how strongly the evidence supports your verdict: about 0.85-0.95 for a direct statement \
from an authoritative source, 0.6-0.8 for indirect or partial support, and 0.3-0.5 for unverifiable claims.
- For every claim, look for evidence that contradicts, qualifies or dates it, not only evidence that supports \
it, and record it in counter_evidence ("none" if there is none). A sweeping claim ("never", "isn't happening", \
"all") with a documented counterexample is possibly_inaccurate, not verified.
- Cite knowledge base ids (kb-...) and/or URLs in sources.
"""


def _user_prompt(state: AgentState) -> str:
    ep = state.episode
    guests = ", ".join(ep.guests) if ep.guests else "unknown"
    return (
        f"Today's date: {date.today().isoformat()}\n\n"
        f"Episode: {ep.episode_id} - {ep.title}\n"
        f"Host: {ep.host or 'unknown'}\nGuests: {guests}\nSource file: {ep.source_file}\n\n"
        f"<transcript>\n{ep.render_transcript()}\n</transcript>\n\n"
        "Process this episode."
    )


class Usage:
    def __init__(self) -> None:
        self.input = self.output = self.cache_read = self.cache_write = self.web_searches = 0
        self.reported_cost = 0.0  # OpenRouter returns the billed cost per request
        self.cost_reported = False

    def add(self, usage: Any) -> None:
        cost = getattr(usage, "cost", None)
        if cost is not None:
            self.reported_cost += float(cost)
            self.cost_reported = True
        self.input += getattr(usage, "input_tokens", 0) or 0
        self.output += getattr(usage, "output_tokens", 0) or 0
        self.cache_read += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.cache_write += getattr(usage, "cache_creation_input_tokens", 0) or 0
        stu = getattr(usage, "server_tool_use", None)
        self.web_searches += (getattr(stu, "web_search_requests", 0) or 0) if stu else 0

    def cost(self, s: Settings) -> float:
        if self.cost_reported:
            return round(self.reported_cost, 4)
        m = s.model
        return round(
            (self.input * m.input_price_per_mtok
             + self.output * m.output_price_per_mtok
             + self.cache_read * m.cache_read_price_per_mtok
             + self.cache_write * m.cache_write_price_per_mtok) / 1_000_000
            + self.web_searches * m.web_search_price_per_1k / 1000,
            4,
        )


class AgentRunError(RuntimeError):
    pass


def _request(client: anthropic.Anthropic, settings: Settings, tools: List[Dict[str, Any]], messages: List[Any]) -> Any:
    m = settings.model
    kwargs: Dict[str, Any] = dict(
        model=m.name,
        max_tokens=m.max_tokens,
        system=SYSTEM_PROMPT,
        tools=tools,
        messages=messages,
        # OpenRouter maps thinking/effort onto each model's own reasoning controls.
        output_config={"effort": m.effort},
    )
    if m.provider == "openrouter":
        kwargs["thinking"] = {"type": "adaptive"}
        return client.messages.create(**kwargs)

    kwargs["thinking"] = {"type": "adaptive", "display": "summarized" if m.show_thinking else "omitted"}
    if m.prompt_caching:
        kwargs["cache_control"] = {"type": "ephemeral"}
    if m.server_side_fallback:
        # On a safety-classifier refusal, the API re-runs the turn on a fallback model.
        kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        return client.beta.messages.create(**kwargs)
    return client.messages.create(**kwargs)


def _log_response(tracer: Tracer, response: Any, turn: int, state: Dict[str, Any]) -> None:
    for block in response.content:
        btype = block.type
        if btype == "thinking":
            if block.thinking:
                tracer.event("THINK", block.thinking, turn=turn)
        elif btype == "text":
            if block.text.strip():
                kind = "PLAN" if state["plan"] is None else "AGENT"
                if state["plan"] is None:
                    state["plan"] = block.text.strip()
                tracer.event(kind, block.text.strip(), turn=turn)
        elif btype == "server_tool_use":
            state["web_searches"] += 1
            tracer.event("SEARCH", f"{block.name} {json.dumps(block.input, ensure_ascii=False)}", data=block.input, turn=turn)
        elif btype.endswith("_tool_result"):
            content = getattr(block, "content", None)
            if isinstance(content, list):
                hits = [{"title": getattr(r, "title", None), "url": getattr(r, "url", None)} for r in content[:5]]
                tracer.event("SEARCH", "results: " + "; ".join(f"{h['title']} <{h['url']}>" for h in hits), data=hits, turn=turn)
            else:
                tracer.event("WARN", f"{btype} error: {getattr(content, 'error_code', content)}", turn=turn)
        elif btype == "fallback":
            tracer.event("WARN", f"refusal fallback: {block.from_.model} -> {block.to.model}", turn=turn)


def run_episode(client: anthropic.Anthropic, state: AgentState, tracer: Tracer) -> EpisodeReport:
    settings = state.settings
    tools = tool_definitions(settings)
    messages: List[Any] = [{"role": "user", "content": _user_prompt(state)}]
    usage = Usage()
    log_state: Dict[str, Any] = {"plan": None, "web_searches": 0}
    tool_calls = rejections = turns = 0
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()

    tracer.event(
        "INFO",
        f"Starting '{state.episode.title}' ({len(state.episode.segments)} segments) with {settings.model.name}, "
        f"effort={settings.model.effort}, web_search={'on' if settings.fact_check.web_search else 'off'}",
    )

    for turn in range(1, settings.agent.max_turns + 1):
        turns = turn
        try:
            response = _request(client, settings, tools, messages)
        except anthropic.BadRequestError as e:
            if settings.fact_check.web_search and "search" in str(e.message).lower():
                tracer.event("WARN", f"Web search unavailable for this API key ({e.message}); continuing with the knowledge base only.", turn=turn)
                settings = settings.model_copy(deep=True)
                settings.fact_check.web_search = False
                state.settings = settings
                tools = tool_definitions(settings)
                continue
            raise
        usage.add(response.usage)
        _log_response(tracer, response, turn, log_state)
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise AgentRunError(f"Model refused: {getattr(details, 'category', None)} {getattr(details, 'explanation', '')}")
        if response.stop_reason == "pause_turn":
            tracer.event("INFO", "Server tool paused the turn; resuming.", turn=turn)
            continue
        if response.stop_reason == "max_tokens":
            raise AgentRunError("Response hit max_tokens; raise model.max_tokens in config.yaml.")

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            missing = state.missing()
            nudge = (
                f"You stopped without finishing. Still needed: {', '.join(missing)}. Continue."
                if missing else "All artifacts are accepted. Call finish."
            )
            tracer.event("INFO", f"No tool call this turn; nudging agent. {nudge}", turn=turn)
            messages.append({"role": "user", "content": nudge})
            continue

        results = []
        for tu in tool_uses:
            tool_calls += 1
            args = tu.input if isinstance(tu.input, dict) else {}
            tracer.event("TOOL", f"{tu.name}({json.dumps(args, ensure_ascii=False)})", data=args, turn=turn)
            content, is_error = run_tool(state, tu.name, args)
            if is_error:
                rejections += 1
                tracer.event("REJECT", f"{tu.name}: {content}", turn=turn)
            else:
                tracer.event("RESULT", f"{tu.name} -> {content}", turn=turn)
            results.append({"type": "tool_result", "tool_use_id": tu.id, "content": content, "is_error": is_error})
        messages.append({"role": "user", "content": results})

        if state.finished:
            break

    # Not every provider reports server_tool_use counts in usage; count the blocks instead.
    usage.web_searches = max(usage.web_searches, log_state["web_searches"])
    duration = round(time.monotonic() - t0, 1)
    if not state.finished:
        tracer.event("WARN", f"Turn budget ({settings.agent.max_turns}) exhausted. Missing: {', '.join(state.missing()) or 'finish call'}")

    run = RunMetadata(
        model=settings.model.name,
        effort=settings.model.effort,
        web_search_enabled=settings.fact_check.web_search,
        started_at=started.isoformat(timespec="seconds"),
        duration_seconds=duration,
        agent_turns=turns,
        tool_calls=tool_calls,
        validation_rejections=rejections,
        input_tokens=usage.input,
        output_tokens=usage.output,
        cache_read_input_tokens=usage.cache_read,
        cache_creation_input_tokens=usage.cache_write,
        web_searches=usage.web_searches,
        estimated_cost_usd=usage.cost(settings),
        completed=state.finished,
        plan=log_state["plan"],
    )
    tracer.event(
        "DONE",
        f"{'Completed' if state.finished else 'INCOMPLETE'} in {duration}s: {turns} turns, {tool_calls} tool calls, "
        f"{rejections} guardrail rejections, {usage.web_searches} web searches, ~${run.estimated_cost_usd}",
    )
    ep = state.episode
    return EpisodeReport(
        episode_id=ep.episode_id,
        title=ep.title,
        host=ep.host,
        guests=ep.guests,
        source_file=ep.source_file,
        summary=state.summary or "",
        key_notes=state.key_notes or KeyNotes(takeaways=[], quotes=[], topics=[]),
        fact_checks=state.fact_checks or [],
        run=run,
    )


def make_state(episode, settings: Settings, kb: KnowledgeBase) -> AgentState:
    return AgentState(episode=episode, settings=settings.model_copy(deep=True), kb=kb)
