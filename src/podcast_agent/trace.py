"""Readable agent trace: coloured console output, a plain-text run log and a JSONL event stream.

The console shows the agent's plan, its summarised thinking, every tool call and
result (including guardrail rejections and web searches) as they happen. The same
events are written to `<output>/<episode>/run.log` and `trace.jsonl`.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from rich.console import Console
from rich.text import Text

_STYLES = {
    "PLAN": "bold cyan",
    "THINK": "dim italic",
    "AGENT": "white",
    "TOOL": "bold yellow",
    "RESULT": "green",
    "REJECT": "bold red",
    "SEARCH": "magenta",
    "INFO": "blue",
    "WARN": "bold red",
    "DONE": "bold green",
}


class Tracer:
    _console_lock = threading.Lock()

    def __init__(self, episode_id: str, out_dir: Path, console: Console, max_preview: int = 600):
        self.episode_id = episode_id
        self.console = console
        self.max_preview = max_preview
        out_dir.mkdir(parents=True, exist_ok=True)
        self._log = (out_dir / "run.log").open("w", encoding="utf-8")
        self._jsonl = (out_dir / "trace.jsonl").open("w", encoding="utf-8")
        self._t0 = time.monotonic()

    def close(self) -> None:
        self._log.close()
        self._jsonl.close()

    def event(self, kind: str, message: str, data: Optional[Dict[str, Any]] = None, turn: Optional[int] = None) -> None:
        elapsed = time.monotonic() - self._t0
        record = {"t": round(elapsed, 2), "episode": self.episode_id, "turn": turn, "kind": kind, "message": message}
        if data is not None:
            record["data"] = data
        self._jsonl.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._jsonl.flush()

        turn_txt = f"t{turn:02d}" if turn is not None else "   "
        prefix = f"[{elapsed:6.1f}s] {self.episode_id} {turn_txt} {kind:<6}"
        self._log.write(f"{prefix} | {message}\n")
        self._log.flush()

        shown = message if kind in ("PLAN", "AGENT", "REJECT", "DONE") else self._clip(message)
        line = Text.assemble((prefix, "dim"), " ", (shown, _STYLES.get(kind, "white")))
        with self._console_lock:
            self.console.print(line, markup=False, highlight=False)

    def _clip(self, s: str) -> str:
        s = " ".join(s.split())
        return s if len(s) <= self.max_preview else s[: self.max_preview] + " …"
