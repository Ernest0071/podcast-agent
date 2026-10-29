"""Domain models shared by ingestion, the agent tools, and report rendering."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class Segment(BaseModel):
    timestamp: str  # normalised to HH:MM:SS
    speaker: str  # full name where resolvable, e.g. "Dr. Priya Patel"
    speaker_label: str  # the label exactly as it appeared in the transcript
    section: Optional[str] = None
    text: str  # cleaned text (filler words removed, whitespace normalised)
    raw_text: str


class Episode(BaseModel):
    episode_id: str
    title: str
    host: Optional[str] = None
    guests: List[str] = Field(default_factory=list)
    segments: List[Segment]
    source_file: str

    def render_transcript(self) -> str:
        """Transcript in the `[HH:MM:SS] Speaker (Section): text` form the agent reads."""
        lines = []
        for s in self.segments:
            section = f" ({s.section})" if s.section else ""
            lines.append(f"[{s.timestamp}] {s.speaker}{section}: {s.text}")
        return "\n".join(lines)

    def segment_at(self, timestamp: str) -> Optional[Segment]:
        for s in self.segments:
            if s.timestamp == timestamp:
                return s
        return None


class Quote(BaseModel):
    timestamp: str
    speaker: str
    quote: str


class KeyNotes(BaseModel):
    takeaways: List[str]
    quotes: List[Quote]
    topics: List[str]


Status = Literal["verified", "possibly_inaccurate", "unverifiable"]
ClaimType = Literal[
    "statistic",
    "historical_fact",
    "entity_fact",
    "regulatory_fact",
    "current_event",
    "prediction",
    "personal_anecdote",
]


class FactCheck(BaseModel):
    claim: str
    timestamp: str
    speaker: str
    claim_type: ClaimType
    status: Status
    confidence: float
    verification: str  # one-line verdict, shown in the table
    evidence: str  # the reasoning behind the verdict
    counter_evidence: str  # anything found that contradicts or qualifies the claim, or "none"
    sources: List[str]

    def has_counter_evidence(self) -> bool:
        return self.counter_evidence.strip().lower().rstrip(".") not in {"", "none", "n/a", "no"}


class RunMetadata(BaseModel):
    model: str
    effort: str
    web_search_enabled: bool
    started_at: str
    duration_seconds: float
    agent_turns: int
    tool_calls: int
    validation_rejections: int
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    web_searches: int
    estimated_cost_usd: float
    completed: bool
    plan: Optional[str] = None


class EpisodeReport(BaseModel):
    episode_id: str
    title: str
    host: Optional[str]
    guests: List[str]
    source_file: str
    summary: str
    key_notes: KeyNotes
    fact_checks: List[FactCheck]
    run: RunMetadata
