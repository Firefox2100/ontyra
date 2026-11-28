"""Scoring retrieval over ingested knowledge: did the entries a lookup returned contain what the
answer needs? No model is involved, so a run is cheap and repeatable."""

from __future__ import annotations

import re

from evaluation_test.framework.models import KnowledgeCase

_FOLD = str.maketrans({"\u2013": "-", "\u2212": "-", "\u2014": "-", "\u00a0": " ", "\u00d7": "x"})


def fold(text: str) -> str:
    """Case, spacing and dash variants folded, so a hyphen and an en dash compare equal."""
    return re.sub(r"\s+", " ", text.translate(_FOLD)).strip().lower()


def missing_groups(case: KnowledgeCase, texts: list[str]) -> list[list[str]]:
    """The groups of `case.must` that no entry satisfies."""
    folded = [fold(t) for t in texts]
    return [g for g in case.must if not any(fold(s) in t for s in g for t in folded)]


def first_rank(case: KnowledgeCase, texts: list[str]) -> int | None:
    """1-based rank by which the returned entries (best first) contain every group, or None."""
    for k in range(1, len(texts) + 1):
        if not missing_groups(case, texts[:k]):
            return k
    return None
