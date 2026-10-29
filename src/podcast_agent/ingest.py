"""Load podcast transcripts (.json or .txt) into a normalised `Episode`.

Supported inputs:
  * JSON with `episode_id`, `title`, `host`, `guests` and a `transcript` list of
    `{timestamp, speaker, section?, text}` entries.
  * Plain text with one utterance per line: `[00:02:14] Host: So NASA ...`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, List, Optional

from .models import Episode, Segment

# Disfluencies that carry no meaning. Kept deliberately conservative: "like" and
# "you know" are often meaningful, so they are left for the model to judge.
_FILLERS = re.compile(r"(?<![\w'])(?:um+|uh+|erm+|uhm+|hmm+|mm+)(?![\w'])[,.]?\s*", re.IGNORECASE)
_TXT_LINE = re.compile(r"^\s*\[(?P<ts>[\d:.]+)\]\s*(?P<speaker>[^:]+?)\s*:\s*(?P<text>.+)$")


def normalise_timestamp(ts: str) -> str:
    """'1:20' -> '00:01:20', '00:02:14' -> '00:02:14', '83' -> '00:01:23'."""
    parts = [int(float(p)) for p in ts.strip().split(":") if p != ""]
    if len(parts) == 1:
        total = parts[0]
    elif len(parts) == 2:
        total = parts[0] * 60 + parts[1]
    elif len(parts) == 3:
        total = parts[0] * 3600 + parts[1] * 60 + parts[2]
    else:
        raise ValueError(f"Unrecognised timestamp: {ts!r}")
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def clean_text(text: str) -> str:
    text = _FILLERS.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Collapse immediate word stutters ("the the" -> "the").
    text = re.sub(r"\b(\w+)( \1\b)+", r"\1", text, flags=re.IGNORECASE)
    return text


def resolve_speaker(label: str, people: Iterable[str]) -> str:
    """Map a transcript label ('Dr. Priya', 'Sarah') to a full name when unambiguous."""
    label_norm = label.strip().lower()
    matches = [p for p in people if p and (p.lower().startswith(label_norm) or label_norm in p.lower().split())]
    return matches[0] if len(matches) == 1 else label.strip()


def _load_json(path: Path) -> Episode:
    data = json.loads(path.read_text(encoding="utf-8"))
    host = data.get("host")
    guests = data.get("guests") or []
    people = [host, *guests]
    segments: List[Segment] = []
    for entry in data.get("transcript", []):
        raw = str(entry.get("text", "")).strip()
        if not raw:
            continue
        label = str(entry.get("speaker", "Unknown"))
        segments.append(
            Segment(
                timestamp=normalise_timestamp(str(entry.get("timestamp", "0"))),
                speaker=resolve_speaker(label, people),
                speaker_label=label,
                section=entry.get("section"),
                text=clean_text(raw),
                raw_text=raw,
            )
        )
    return Episode(
        episode_id=str(data.get("episode_id") or path.stem),
        title=str(data.get("title") or path.stem),
        host=host,
        guests=guests,
        segments=segments,
        source_file=path.name,
    )


def _load_txt(path: Path) -> Episode:
    segments: List[Segment] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _TXT_LINE.match(line)
        if m:
            raw = m.group("text").strip()
            label = m.group("speaker").strip()
            segments.append(
                Segment(
                    timestamp=normalise_timestamp(m.group("ts")),
                    speaker=label,
                    speaker_label=label,
                    text=clean_text(raw),
                    raw_text=raw,
                )
            )
        elif line.strip() and segments:
            # Wrapped continuation of the previous utterance.
            prev = segments[-1]
            prev.raw_text = f"{prev.raw_text} {line.strip()}"
            prev.text = clean_text(prev.raw_text)
    return Episode(
        episode_id=path.stem.split("_")[0],
        title=path.stem.replace("_", " "),
        segments=segments,
        source_file=path.name,
    )


def load_episode(path: Path) -> Episode:
    suffix = path.suffix.lower()
    if suffix == ".json":
        episode = _load_json(path)
    elif suffix == ".txt":
        episode = _load_txt(path)
    else:
        raise ValueError(f"Unsupported transcript format: {path.name}")
    if not episode.segments:
        raise ValueError(f"No transcript segments found in {path.name}")
    return episode


def discover_transcripts(target: Path, only: Optional[List[str]] = None) -> List[Path]:
    if target.is_file():
        return [target]
    files = sorted(p for p in target.iterdir() if p.suffix.lower() in {".json", ".txt"})
    if only:
        files = [p for p in files if any(p.stem.startswith(o) for o in only)]
    return files
