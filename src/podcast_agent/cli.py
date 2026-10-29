"""Command-line entry point: `podcast-agent run ...`."""

from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional

import anthropic
from rich.console import Console

from .agent import AgentRunError, make_state, run_episode
from .config import API_KEY_ENV, Settings, load_settings
from .ingest import discover_transcripts, load_episode
from .kb import KnowledgeBase
from .models import EpisodeReport
from .render import write_index, write_report
from .trace import Tracer

console = Console(highlight=False)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="podcast-agent", description="Summarise, extract show notes and fact-check podcast transcripts.")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Process transcripts")
    run.add_argument("--input", "-i", type=Path, default=Path("data/transcripts"), help="Transcript file or directory (.json/.txt)")
    run.add_argument("--output", "-o", type=Path, default=Path("outputs"), help="Output directory")
    run.add_argument("--config", "-c", type=Path, default=Path("config.yaml"))
    run.add_argument("--episodes", nargs="*", help="Only these episode ids / filename prefixes, e.g. ep001 ep003")
    run.add_argument("--provider", choices=["openrouter", "anthropic"], help="Override model.provider")
    run.add_argument("--model", help="Override model.name, e.g. z-ai/glm-5.3-flash or anthropic/claude-sonnet-5.5")
    run.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], help="Override model.effort")
    run.add_argument("--no-web", action="store_true", help="Fact-check with the knowledge base only")
    run.add_argument("--concurrency", type=int, help="Episodes processed in parallel")

    kb = sub.add_parser("kb-search", help="Query the knowledge base directly (debugging)")
    kb.add_argument("query")
    kb.add_argument("--config", "-c", type=Path, default=Path("config.yaml"))
    return p


def make_client(settings: Settings) -> anthropic.Anthropic:
    m = settings.model
    env = API_KEY_ENV[m.provider]
    key = os.environ.get(env)
    if not key:
        raise RuntimeError(f"{env} is not set. Add it to .env (provider: {m.provider}).")
    if m.provider == "openrouter":
        # OpenRouter serves the Anthropic Messages API and authenticates with a bearer token.
        return anthropic.Anthropic(
            base_url=m.resolved_base_url(),
            api_key=None,
            auth_token=key,
            max_retries=4,
            default_headers={"X-Title": "podcast-agent"},
        )
    return anthropic.Anthropic(api_key=key, base_url=m.resolved_base_url(), max_retries=4)


def _process(client, path: Path, settings, kb, out_root: Path) -> EpisodeReport:
    episode = load_episode(path)
    out_dir = out_root / episode.episode_id
    tracer = Tracer(episode.episode_id, out_dir, console)
    try:
        report = run_episode(client, make_state(episode, settings, kb), tracer)
        write_report(report, out_dir)
        return report
    finally:
        tracer.close()


def cmd_run(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    if args.provider:
        settings.model.provider = args.provider
    if args.model:
        settings.model.name = args.model
    if args.effort:
        settings.model.effort = args.effort
    if args.no_web:
        settings.fact_check.web_search = False
    if args.concurrency:
        settings.concurrency = args.concurrency

    files = discover_transcripts(args.input, args.episodes)
    if not files:
        console.print(f"[red]No transcripts found in {args.input}[/red]")
        return 1
    kb = KnowledgeBase.load(Path(settings.fact_check.knowledge_base_path))
    try:
        client = make_client(settings)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        return 2
    args.output.mkdir(parents=True, exist_ok=True)

    console.rule(f"[bold]podcast-agent[/bold] · {len(files)} episode(s) · {settings.model.name}")
    reports: List[EpisodeReport] = []
    failures = 0
    with ThreadPoolExecutor(max_workers=max(1, settings.concurrency)) as pool:
        futures = {pool.submit(_process, client, f, settings, kb, args.output): f for f in files}
        for fut in as_completed(futures):
            f = futures[fut]
            try:
                reports.append(fut.result())
            except (AgentRunError, anthropic.APIError, ValueError) as e:
                failures += 1
                console.print(f"[bold red]✗ {f.name}: {type(e).__name__}: {e}[/bold red]")

    reports.sort(key=lambda r: r.episode_id)
    if reports:
        write_index(reports, args.output)
    console.rule("[bold]Done[/bold]")
    for r in reports:
        state = "[green]done[/green]" if r.run.completed else "[red]incomplete[/red]"
        console.print(f"{r.episode_id}  {state}  → {args.output / r.episode_id / 'report.md'}")
    if reports:
        console.print(f"Index: {args.output / 'index.md'}")
    return 1 if failures or any(not r.run.completed for r in reports) else 0


def cmd_kb_search(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    kb = KnowledgeBase.load(Path(settings.fact_check.knowledge_base_path))
    for hit in kb.search(args.query, top_k=settings.fact_check.kb_top_k):
        console.print(f"[bold]{hit['id']}[/bold] ({hit['relevance']}) {hit['statement']}\n  [dim]{hit['source']} · as of {hit['as_of']}[/dim]")
    return 0


def load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal .env loader for local runs; Docker passes the file with --env-file."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main(argv: Optional[List[str]] = None) -> int:
    load_dotenv()
    args = _parser().parse_args(argv)
    if args.command == "run":
        return cmd_run(args)
    return cmd_kb_search(args)


if __name__ == "__main__":
    sys.exit(main())
