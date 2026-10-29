"""Write each episode report as JSON and Markdown, plus a run index."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

from .models import EpisodeReport

STATUS_ICON = {"verified": "✅", "possibly_inaccurate": "⚠️", "unverifiable": "❓"}
STATUS_LABEL = {"verified": "Verified true", "possibly_inaccurate": "Possibly outdated/inaccurate", "unverifiable": "Unverifiable"}


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _source_link(s: str) -> str:
    return f"[link]({s})" if s.startswith("http") else f"`{s}`"


def to_markdown(r: EpisodeReport) -> str:
    guests = ", ".join(r.guests) or "n/a"
    lines = [
        f"# {r.episode_id}: {r.title}",
        "",
        f"**Host:** {r.host or 'n/a'} · **Guests:** {guests} · **Source:** `{r.source_file}`",
        "",
    ]
    if not r.run.completed:
        lines += ["> **Incomplete run.** The agent ran out of turns; some sections may be missing.", ""]
    lines += ["## Summary", "", r.summary or "_Not produced._", ""]

    lines += ["## Key notes", "", "### 🔹 Top takeaways", ""]
    lines += [f"{i}. {t}" for i, t in enumerate(r.key_notes.takeaways, 1)] or ["_None._"]
    lines += ["", "### 💬 Notable quotes", ""]
    lines += [f"- **[{q.timestamp}] {q.speaker}:** “{q.quote}”" for q in r.key_notes.quotes] or ["_None._"]
    lines += ["", "### 🧭 Topics", ""]
    lines += [" ".join(f"`#{t}`" for t in r.key_notes.topics) or "_None._", ""]

    lines += [
        "## Fact-check",
        "",
        "| | Claim | Said by | Verification | Confidence |",
        "|---|---|---|---|---|",
    ]
    for c in r.fact_checks:
        lines.append(
            f"| {STATUS_ICON[c.status]} | {_cell(c.claim)} | {c.speaker} [{c.timestamp}] | "
            f"{_cell(c.verification)} | {c.confidence:.2f} |"
        )
    lines += ["", "✅ Verified true · ⚠️ Possibly outdated/inaccurate · ❓ Unverifiable", "", "### Evidence", ""]
    for i, c in enumerate(r.fact_checks, 1):
        sources = ", ".join(_source_link(s) for s in c.sources) or "none"
        counter = f" Counter-evidence: {c.counter_evidence}" if c.has_counter_evidence() else ""
        lines.append(f"{i}. {STATUS_ICON[c.status]} **{_cell(c.claim)}** ({c.claim_type.replace('_', ' ')}). {c.evidence}{counter} Sources: {sources}")

    m = r.run
    lines += [
        "",
        "## Run details",
        "",
        f"- Model: `{m.model}` (effort `{m.effort}`), web search {'on' if m.web_search_enabled else 'off'}",
        f"- {m.agent_turns} agent turns, {m.tool_calls} tool calls, {m.validation_rejections} guardrail rejections, {m.web_searches} web searches",
        f"- Tokens: {m.input_tokens:,} in / {m.output_tokens:,} out / {m.cache_read_input_tokens:,} cache read; est. cost ${m.estimated_cost_usd:.4f}",
        f"- Duration: {m.duration_seconds}s · started {m.started_at}",
        "- Agent reasoning trace: `run.log` (readable) and `trace.jsonl` (structured)",
    ]
    if m.plan:
        lines += ["", "<details><summary>Agent plan</summary>", "", m.plan, "", "</details>"]
    return "\n".join(lines) + "\n"


def write_report(r: EpisodeReport, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(r.model_dump(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "report.md").write_text(to_markdown(r), encoding="utf-8")


def write_index(reports: List[EpisodeReport], out_root: Path) -> None:
    lines = [
        "# Podcast agent run",
        "",
        "| Episode | Summary words | Quotes | Claims (✅/⚠️/❓) | Turns | Rejections | Cost | Status |",
        "|---|---|---|---|---|---|---|---|",
    ]
    total = 0.0
    for r in reports:
        counts = {k: sum(1 for c in r.fact_checks if c.status == k) for k in STATUS_ICON}
        total += r.run.estimated_cost_usd
        lines.append(
            f"| [{r.episode_id}: {r.title}]({r.episode_id}/report.md) | {len(r.summary.split())} | {len(r.key_notes.quotes)} | "
            f"{counts['verified']}/{counts['possibly_inaccurate']}/{counts['unverifiable']} | {r.run.agent_turns} | "
            f"{r.run.validation_rejections} | ${r.run.estimated_cost_usd:.3f} | {'done' if r.run.completed else 'INCOMPLETE'} |"
        )
    lines += ["", f"Total estimated cost: ${total:.3f}", ""]
    (out_root / "index.md").write_text("\n".join(lines), encoding="utf-8")
    (out_root / "all_reports.json").write_text(
        json.dumps([r.model_dump() for r in reports], indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
