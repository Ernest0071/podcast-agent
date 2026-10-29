"""Local knowledge base with BM25 retrieval.

Stands in for a document store or search index (OpenSearch, pgvector, etc). Each
entry is a curated reference statement with a source and an as-of date, so the
agent can tell a confirmed fact from one that may have gone stale.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "is", "are", "was",
    "were", "be", "it", "its", "that", "this", "with", "as", "by", "at", "from", "has",
    "have", "had", "will", "can", "do", "does", "not", "but", "they", "their", "about",
}


def tokenize(text: str) -> List[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


class KnowledgeBase:
    def __init__(self, entries: List[Dict], k1: float = 1.5, b: float = 0.75):
        self.entries = entries
        self.k1, self.b = k1, b
        self._docs = [
            tokenize(" ".join([e.get("topic", ""), e["statement"], " ".join(e.get("keywords", []))]))
            for e in entries
        ]
        self._avgdl = sum(len(d) for d in self._docs) / max(len(self._docs), 1)
        df: Counter = Counter()
        for d in self._docs:
            df.update(set(d))
        n = len(self._docs)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self._tf = [Counter(d) for d in self._docs]

    @classmethod
    def load(cls, path: Path) -> "KnowledgeBase":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(data["entries"])

    def search(self, query: str, top_k: int = 3) -> List[Dict]:
        q = tokenize(query)
        scored = []
        for i, tf in enumerate(self._tf):
            dl = len(self._docs[i])
            score = 0.0
            for t in q:
                if t not in tf:
                    continue
                f = tf[t]
                score += self._idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self._avgdl))
            if score > 0:
                scored.append((score, i))
        scored.sort(reverse=True)
        best = scored[0][0] if scored else 1.0
        results = []
        for score, i in scored[:top_k]:
            e = self.entries[i]
            results.append(
                {
                    "id": e["id"],
                    "statement": e["statement"],
                    "source": e["source"],
                    "url": e.get("url"),
                    "as_of": e.get("as_of"),
                    # Relative to the best hit, so the agent can judge how strong the match is.
                    "relevance": round(score / best, 2),
                }
            )
        return results
