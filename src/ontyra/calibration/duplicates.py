"""Suggest the duplicate scan's cosine thresholds from labelled statement pairs.

`data/pairs.json`: pairs labelled `dup` (the same statement, reworded), `update` (the same
statement with a changed value) and `distinct` (close but different). The embedder's cosine of
each pair is measured; `duplicate_cosine` is the lowest cosine at which at most 5% of what the
scan would merge is not a duplicate, `candidate_cosine` is low enough to still offer 95% of the
duplicates to the decision model. `confirm` belongs to the decision model (`tests/decision_test`).
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from ontyra.ports import Embedder

DATA = Path(__file__).resolve().parent / "data"
PRECISION = 0.95
COVERAGE = 0.95


class Pair(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    a: str
    b: str
    label: Literal["dup", "update", "distinct"]
    source: str


def load_pairs() -> list[Pair]:
    return [
        Pair.model_validate(raw) for raw in json.loads((DATA / "pairs.json").read_text("utf-8"))
    ]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


async def measure(embedder: Embedder, pairs: list[Pair]) -> list[float]:
    """The cosine of every pair, both sides embedded as stored documents."""
    texts = sorted({t for p in pairs for t in (p.a, p.b)})
    vectors = dict(zip(texts, await embedder.embed_documents(texts), strict=True))
    return [cosine(vectors[p.a], vectors[p.b]) for p in pairs]


def precision_threshold(dup: list[float], rest: list[float]) -> tuple[float, float] | None:
    """The lowest threshold whose merged set (scores at or above it) is at least `PRECISION`
    duplicates, and the share of the duplicates it merges; None when there is none."""
    for t in sorted({*dup, *rest}):
        merged_dup = sum(c >= t for c in dup)
        merged_rest = sum(c >= t for c in rest)
        if merged_dup and merged_dup / (merged_dup + merged_rest) >= PRECISION:
            return t - 1e-4, merged_dup / len(dup)
    return None


def suggest(pairs: list[Pair], cosines: list[float]) -> dict[str, Any]:
    dup = sorted(c for c, p in zip(cosines, pairs, strict=True) if p.label == "dup")
    rest = sorted(c for c, p in zip(cosines, pairs, strict=True) if p.label != "dup")
    out: dict[str, Any] = {
        "pairs": len(pairs),
        "dup_median": round(dup[len(dup) // 2], 3) if dup else None,
        "rest_median": round(rest[len(rest) // 2], 3) if rest else None,
        "rest_p95": round(rest[int(len(rest) * 0.95)], 3) if rest else None,
        "duplicate_cosine": None,
        "candidate_cosine": None,
    }
    if not dup or not rest:
        return out
    found = precision_threshold(dup, rest)
    if found is not None:
        out["duplicate_cosine"] = round(found[0], 3)
        out["recall_at_duplicate_cosine"] = round(found[1], 3)
    out["candidate_cosine"] = round(dup[int(len(dup) * (1 - COVERAGE))] - 1e-4, 3)
    return out
