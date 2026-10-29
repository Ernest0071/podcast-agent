"""Tools the agent can call, plus the guardrails that validate what it submits.

Every `submit_*` tool checks its input against the transcript and the configured
limits. A failed check returns an error result that names the exact problem, so
the agent revises and resubmits instead of the run ending with bad output.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import ValidationError

from .config import Settings
from .kb import KnowledgeBase
from .models import Episode, FactCheck, KeyNotes, Quote


class ToolInputError(Exception):
    """Raised by a handler when the agent's input fails validation."""


@dataclass
class AgentState:
    episode: Episode
    settings: Settings
    kb: KnowledgeBase
    summary: Optional[str] = None
    key_notes: Optional[KeyNotes] = None
    fact_checks: Optional[List[FactCheck]] = None
    kb_queries: List[str] = field(default_factory=list)
    finished: bool = False

    def missing(self) -> List[str]:
        out = []
        if self.summary is None:
            out.append("submit_summary")
        if self.key_notes is None:
            out.append("submit_key_notes")
        if self.fact_checks is None:
            out.append("submit_fact_checks")
        return out


# ---------------------------------------------------------------------------
# Tool schemas (strict mode: additionalProperties false, every field required)
# ---------------------------------------------------------------------------

def _obj(properties: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def tool_definitions(settings: Settings) -> List[Dict[str, Any]]:
    a = settings.agent
    tools: List[Dict[str, Any]] = [
        {
            "name": "search_knowledge_base",
            "description": (
                "Search the agency's curated reference knowledge base for statements relevant to a "
                "factual claim. Returns the best-matching entries with id, statement, source, url, "
                "as_of date and a relative relevance score (1.0 = best hit). Use short keyword-rich "
                "queries naming the entities in the claim. An entry only supports a claim if it says "
                "the same thing; a related entry is not confirmation."
            ),
            "strict": True,
            "input_schema": _obj({"query": {"type": "string", "description": "Keyword query, e.g. 'FDA autonomous AI diagnostics'"}}),
        },
        {
            "name": "submit_summary",
            "description": (
                f"Submit the episode summary: {a.summary_min_words}-{a.summary_max_words} words of plain "
                "prose covering the core themes, the key discussions, and the outcomes or opinions each "
                "speaker shared. The tool counts words and rejects summaries outside the range."
            ),
            "strict": True,
            "input_schema": _obj({"summary": {"type": "string"}}),
        },
        {
            "name": "submit_key_notes",
            "description": (
                f"Submit show notes: exactly {a.takeaways_count} takeaways, {a.min_quotes}-{a.max_quotes} "
                f"notable quotes and {a.min_topics}-{a.max_topics} topic tags. Each quote must be copied "
                "verbatim from one transcript line (you may trim it, or join two parts of the same line "
                "with '...'), with that line's [HH:MM:SS] timestamp and speaker. The tool checks every "
                "quote against the transcript."
            ),
            "strict": True,
            "input_schema": _obj(
                {
                    "takeaways": {"type": "array", "items": {"type": "string"}},
                    "quotes": {
                        "type": "array",
                        "items": _obj(
                            {
                                "timestamp": {"type": "string", "description": "HH:MM:SS as shown in the transcript"},
                                "speaker": {"type": "string"},
                                "quote": {"type": "string"},
                            }
                        ),
                    },
                    "topics": {"type": "array", "items": {"type": "string"}, "description": "Short tag labels, e.g. 'async-communication'"},
                }
            ),
        },
        {
            "name": "submit_fact_checks",
            "description": (
                f"Submit the fact-check table: {a.min_claims}-{a.max_claims} checkable claims from the "
                "episode, each with a verdict. status is 'verified' (evidence confirms it), "
                "'possibly_inaccurate' (evidence contradicts it, or it is outdated or overstated) or "
                "'unverifiable' (no evidence either way, or it is a prediction or a personal anecdote). "
                "confidence (0-1) is how sure you are of the verdict. sources lists the knowledge base "
                "ids (kb-...) and/or URLs you relied on; it must not be empty for 'verified' or "
                "'possibly_inaccurate'. A claim with counter_evidence other than 'none' cannot be 'verified'. "
                "Submit the full table in one call."
            ),
            "strict": True,
            "input_schema": _obj(
                {
                    "fact_checks": {
                        "type": "array",
                        "items": _obj(
                            {
                                "claim": {"type": "string", "description": "The claim restated as one standalone, checkable sentence"},
                                "timestamp": {"type": "string"},
                                "speaker": {"type": "string"},
                                "claim_type": {
                                    "type": "string",
                                    "enum": [
                                        "statistic",
                                        "historical_fact",
                                        "entity_fact",
                                        "regulatory_fact",
                                        "current_event",
                                        "prediction",
                                        "personal_anecdote",
                                    ],
                                },
                                "status": {"type": "string", "enum": ["verified", "possibly_inaccurate", "unverifiable"]},
                                "confidence": {"type": "number"},
                                "verification": {"type": "string", "description": "One short line for the table, e.g. 'True (FDA AI device list)'"},
                                "evidence": {"type": "string", "description": "1-3 sentences explaining the verdict"},
                                "counter_evidence": {
                                    "type": "string",
                                    "description": "Anything you found that contradicts, qualifies or dates the claim (e.g. a documented counterexample), or exactly 'none'",
                                },
                                "sources": {"type": "array", "items": {"type": "string"}},
                            }
                        ),
                    }
                }
            ),
        },
        {
            "name": "finish",
            "description": "Call once the summary, key notes and fact checks have all been accepted. Ends the run.",
            "strict": True,
            "input_schema": _obj({"notes": {"type": "string", "description": "One or two sentences on anything a human reviewer should double-check"}}),
        },
    ]
    fc = settings.fact_check
    if fc.web_search and settings.model.provider == "anthropic":
        tools.append({"type": "web_search_20260209", "name": "web_search", "max_uses": fc.web_search_max_uses})
    elif fc.web_search:
        # OpenRouter's server-side search: the model decides when to call it, OpenRouter runs it.
        tools.append(
            {
                "type": "openrouter:web_search",
                "parameters": {
                    "engine": fc.web_search_engine,
                    "max_results": fc.web_search_max_results,
                    "max_uses": fc.web_search_max_uses,
                },
            }
        )
    return tools


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_PUNCT = re.compile(r"[^\w\s]")


def _norm(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = text.replace("—", " ").replace("–", " ")
    text = _PUNCT.sub(" ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'’-]+\b", text))


def _speaker_matches(given: str, seg_speaker: str, seg_label: str) -> bool:
    g = _norm(given)
    return bool(g) and (g in _norm(seg_speaker) or g in _norm(seg_label) or _norm(seg_label) in g)


def _quote_in_text(quote: str, text: str) -> bool:
    parts = [p for p in re.split(r"\.\.\.|…", quote) if _norm(p)]
    hay = _norm(text)
    pos = 0
    for part in parts:
        idx = hay.find(_norm(part), pos)
        if idx < 0:
            return False
        pos = idx + len(_norm(part))
    return bool(parts)


def to_tag(label: str) -> str:
    tag = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return tag


def _canonical_ts(ts: str) -> str:
    from .ingest import normalise_timestamp

    try:
        return normalise_timestamp(ts.strip("[] "))
    except ValueError:
        return ts


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def handle_search_knowledge_base(state: AgentState, args: Dict[str, Any]) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        raise ToolInputError("query must not be empty.")
    state.kb_queries.append(query)
    hits = state.kb.search(query, top_k=state.settings.fact_check.kb_top_k)
    if not hits:
        return json.dumps({"results": [], "note": "No matching entries. Try other keywords or web search."})
    return json.dumps({"results": hits}, ensure_ascii=False)


def handle_submit_summary(state: AgentState, args: Dict[str, Any]) -> str:
    a = state.settings.agent
    summary = str(args.get("summary", "")).strip()
    n = word_count(summary)
    if n < a.summary_min_words or n > a.summary_max_words:
        direction = "Expand" if n < a.summary_min_words else "Tighten"
        raise ToolInputError(
            f"Summary is {n} words; it must be {a.summary_min_words}-{a.summary_max_words}. "
            f"{direction} it and resubmit."
        )
    state.summary = summary
    return json.dumps({"accepted": True, "word_count": n})


def handle_submit_key_notes(state: AgentState, args: Dict[str, Any]) -> str:
    a = state.settings.agent
    problems: List[str] = []

    takeaways = [str(t).strip() for t in args.get("takeaways", []) if str(t).strip()]
    if len(takeaways) != a.takeaways_count:
        problems.append(f"Provide exactly {a.takeaways_count} takeaways (got {len(takeaways)}).")

    quotes: List[Quote] = []
    raw_quotes = args.get("quotes", [])
    if not a.min_quotes <= len(raw_quotes) <= a.max_quotes:
        problems.append(f"Provide {a.min_quotes}-{a.max_quotes} quotes (got {len(raw_quotes)}).")
    for i, q in enumerate(raw_quotes, 1):
        ts = _canonical_ts(str(q.get("timestamp", "")))
        seg = state.episode.segment_at(ts)
        text = str(q.get("quote", "")).strip().strip('"“”')
        if seg is None:
            problems.append(f"Quote {i}: no transcript line at timestamp {q.get('timestamp')!r}.")
            continue
        if not _speaker_matches(str(q.get("speaker", "")), seg.speaker, seg.speaker_label):
            problems.append(f"Quote {i}: the line at {ts} is spoken by {seg.speaker}, not {q.get('speaker')!r}.")
            continue
        if not _quote_in_text(text, seg.text):
            problems.append(
                f"Quote {i}: text does not appear verbatim in the line at {ts}. That line reads: \"{seg.text}\""
            )
            continue
        quotes.append(Quote(timestamp=ts, speaker=seg.speaker, quote=text))

    topics: List[str] = []
    for t in args.get("topics", []):
        tag = to_tag(str(t))
        if tag and tag not in topics:
            topics.append(tag)
    if not a.min_topics <= len(topics) <= a.max_topics:
        problems.append(f"Provide {a.min_topics}-{a.max_topics} distinct topic tags (got {len(topics)}).")

    if problems:
        raise ToolInputError("Key notes rejected:\n- " + "\n- ".join(problems))
    state.key_notes = KeyNotes(takeaways=takeaways, quotes=quotes, topics=topics)
    return json.dumps({"accepted": True, "takeaways": len(takeaways), "quotes": len(quotes), "topics": topics})


_SUBJECTIVE_TYPES = {"prediction", "personal_anecdote"}


def handle_submit_fact_checks(state: AgentState, args: Dict[str, Any]) -> str:
    a = state.settings.agent
    raw = args.get("fact_checks", [])
    problems: List[str] = []
    if not a.min_claims <= len(raw) <= a.max_claims:
        problems.append(f"Provide {a.min_claims}-{a.max_claims} claims (got {len(raw)}).")
    if not state.kb_queries:
        problems.append("Search the knowledge base for evidence before submitting verdicts.")

    checks: List[FactCheck] = []
    for i, item in enumerate(raw, 1):
        item = dict(item)
        item["timestamp"] = _canonical_ts(str(item.get("timestamp", "")))
        seg = state.episode.segment_at(item["timestamp"])
        if seg is None:
            problems.append(f"Claim {i}: no transcript line at timestamp {item['timestamp']!r}.")
            continue
        item["speaker"] = seg.speaker
        try:
            fc = FactCheck.model_validate(item)
        except ValidationError as e:
            problems.append(f"Claim {i}: {e.errors()[0]['msg']}.")
            continue
        if not 0.0 <= fc.confidence <= 1.0:
            problems.append(f"Claim {i}: confidence must be between 0 and 1.")
        if fc.status != "unverifiable" and not fc.sources:
            problems.append(f"Claim {i}: '{fc.status}' needs at least one source (kb id or URL).")
        if fc.claim_type in _SUBJECTIVE_TYPES and fc.status == "verified":
            problems.append(
                f"Claim {i}: a {fc.claim_type} cannot be 'verified'. Use 'unverifiable', or "
                "'possibly_inaccurate' if evidence contradicts it."
            )
        if fc.status == "verified" and fc.has_counter_evidence():
            problems.append(
                f"Claim {i}: you recorded counter-evidence ({fc.counter_evidence!r}), so it cannot be 'verified'. "
                "Use 'possibly_inaccurate' if the claim is overstated or contradicted, or set counter_evidence to "
                "'none' if the evidence does not actually qualify it."
            )
        unknown = [s for s in fc.sources if s.startswith("kb-") and not any(e["id"] == s for e in state.kb.entries)]
        if unknown:
            problems.append(f"Claim {i}: unknown knowledge base ids {unknown}.")
        checks.append(fc)

    if problems:
        raise ToolInputError("Fact checks rejected:\n- " + "\n- ".join(problems))
    state.fact_checks = checks
    counts: Dict[str, int] = {}
    for c in checks:
        counts[c.status] = counts.get(c.status, 0) + 1
    return json.dumps({"accepted": True, "claims": len(checks), "by_status": counts})


def handle_finish(state: AgentState, args: Dict[str, Any]) -> str:
    missing = state.missing()
    if missing:
        raise ToolInputError(f"Cannot finish yet. Still needed: {', '.join(missing)}.")
    state.finished = True
    return json.dumps({"accepted": True})


HANDLERS: Dict[str, Callable[[AgentState, Dict[str, Any]], str]] = {
    "search_knowledge_base": handle_search_knowledge_base,
    "submit_summary": handle_submit_summary,
    "submit_key_notes": handle_submit_key_notes,
    "submit_fact_checks": handle_submit_fact_checks,
    "finish": handle_finish,
}


def run_tool(state: AgentState, name: str, args: Dict[str, Any]) -> Tuple[str, bool]:
    """Execute a client tool. Returns (content, is_error)."""
    handler = HANDLERS.get(name)
    if handler is None:
        return f"Unknown tool {name!r}.", True
    try:
        return handler(state, args), False
    except ToolInputError as e:
        return str(e), True
