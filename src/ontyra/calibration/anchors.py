"""Clean, labelled reranking data from the bundled worlds: anchors for training and a gate set.

Retraining from feedback is small and noisy, and a poisoned pool would pass any check made on
held-out pairs of the same pool. So two things come from the bundled benchmark instead: *anchors*
(labelled pairs from the `tune` requests, mixed into the training batch so the model keeps what it
knew) and the *gate set* (the `holdout` requests with their candidates, which a retrained version
must not rank worse than the one it started from). No database or model is involved: a request's
relevant memories are positives and the memories of the world that share the most words with it
but are not relevant are the negatives.
"""

from __future__ import annotations

import re
from typing import Any

from ontyra.calibration.models import Case, World, load_cases, load_world
from ontyra.calibration.ranking import labels

NEGATIVES = 6  # hard negatives per request
WORD = re.compile(r"[a-z0-9]+")


def _words(text: str) -> set[str]:
    return set(WORD.findall(text.casefold()))


def _requests(split: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cases: list[Case] = [c for c in load_cases("recall") if c.split == split and c.query]
    worlds: dict[str, World] = {}
    for case in cases:
        if not case.include:
            continue
        world = worlds.get(case.world) or worlds.setdefault(case.world, load_world(case.world))
        keys = [m.key for m in world.memories if not m.retracted]
        domains = {m.key: m.domain for m in world.memories}
        relevant, _, negative = labels(case, keys, domains)
        text = {m.key: m.text for m in world.memories}
        query = case.query or ""
        words = _words(query)
        hard = sorted(negative, key=lambda k: (-len(words & _words(text[k])), k))[:NEGATIVES]
        out.append(
            {
                "id": case.id,
                "query": query,
                "candidates": [
                    {"text": text[k], "relevant": k in relevant}
                    for k in [*sorted(relevant), *hard]
                    if k in text
                ],
            }
        )
    return out


def anchor_examples() -> list[dict[str, Any]]:
    """Labelled (query, text) pairs from the `tune` requests of every bundled world."""
    return [
        {"query": r["query"], "text": c["text"], "relevance": 1.0 if c["relevant"] else 0.0}
        for r in _requests("tune")
        for c in r["candidates"]
    ]


def gate_requests() -> list[dict[str, Any]]:
    """The `holdout` requests with their candidates: the trusted set a new version is judged on."""
    return [r for r in _requests("holdout") if any(c["relevant"] for c in r["candidates"])]


__all__ = ["anchor_examples", "gate_requests"]
