"""Task `same`: do two memories state the same thing? (The duplicate scan's question.)

Pairs (`src/ontyra/calibration/data/pairs.json`, all from the invented benchmark world) are labelled `dup` (a
paraphrase or light edit of the same statement), `update` (the same statement with a changed
value: a new version, not a duplicate) or `distinct` (a different statement that is close enough
to be a candidate). A variant turns a pair into the probability that they are the same; what is
measured is how well that separates `dup` from the rest, and what the scan's threshold
(`ConsolidationSettings.confirm`) would do with it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from decision_test.framework.client import Kev
from decision_test.framework.metrics import auc, best_threshold, brier, mean, prf, rounded
from ontyra.adapters.decider import clip
from ontyra.calibration.duplicates import Pair
from ontyra.models.consolidation import ConsolidationSettings

Scorer = Callable[[str, str], Awaitable[float]]


CHOICE3 = "How do memory A and memory B relate?"
OPTIONS3 = {
    "same": "Both memories state the same thing, only worded differently.",
    "update": "They state the same thing but with different values: one updates or contradicts the other.",
    "distinct": "They state different things, even if on a related topic.",
}


def variants(kev: Kev, production: Any, chars: int = 300) -> dict[str, Scorer]:
    """`production` is the daemon's `SystemOneDecider` (its `same` is what the scan calls)."""

    async def noul(a: str, b: str) -> float:
        return await kev.timed(lambda: production.same(a, b))

    async def noul_min(a: str, b: str) -> float:  # as the scan: both orders, the lower counts
        ab, ba = await asyncio.gather(noul(a, b), noul(b, a))
        return min(ab, ba)

    async def choice3(a: str, b: str) -> float:
        state = f"Memory A: {clip(a, chars)}\nMemory B: {clip(b, chars)}"
        return (await kev.choice(state, CHOICE3, OPTIONS3))["same"]

    async def choice3_min(a: str, b: str) -> float:
        ab, ba = await asyncio.gather(choice3(a, b), choice3(b, a))
        return min(ab, ba)

    return {
        "noul": noul,
        "noul-min": noul_min,
        "choice3": choice3,
        "choice3-min": choice3_min,
    }


async def evaluate(
    pairs: list[Pair], scorer: Scorer, *, confirm: float | None = None
) -> dict[str, Any]:
    """Score every pair; metrics against `dup`, at the scan's `confirm` and at the best cut-off."""
    threshold = ConsolidationSettings().confirm if confirm is None else confirm
    scores = await asyncio.gather(*(scorer(p.a, p.b) for p in pairs))
    actual = [p.label == "dup" for p in pairs]

    def by(label: str) -> list[float]:
        return [s for s, p in zip(scores, pairs, strict=True) if p.label == label]

    at = prf([s >= threshold for s in scores], actual)
    best = best_threshold(list(scores), actual)
    per_source: dict[str, Any] = {}
    for source in sorted({p.source for p in pairs}):
        chosen = [(s, p) for s, p in zip(scores, pairs, strict=True) if p.source == source]
        dups = [s for s, p in chosen if p.label == "dup"]
        per_source[source] = {
            "dup_found": rounded(mean([float(s >= threshold) for s in dups])),
            "wrongly_accepted": sum(s >= threshold for s, p in chosen if p.label != "dup"),
            "pairs": len(chosen),
        }
    return {
        "auc_dup_vs_rest": rounded(auc(by("dup"), by("update") + by("distinct"))),
        "auc_dup_vs_update": rounded(auc(by("dup"), by("update"))),
        "auc_dup_vs_distinct": rounded(auc(by("dup"), by("distinct"))),
        "brier": rounded(brier(list(scores), actual)),
        "threshold": threshold,
        "at_threshold": {k: rounded(float(v)) for k, v in at.items()},
        "updates_accepted": sum(s >= threshold for s in by("update")),
        "distinct_accepted": sum(s >= threshold for s in by("distinct")),
        "best_cutoff": {k: rounded(v) for k, v in best.items()},
        "by_source": per_source,
        "rows": [
            {
                "id": p.id,
                "label": p.label,
                "source": p.source,
                "same": rounded(s, 4),
                "a": p.a,
                "b": p.b,
            }
            for s, p in zip(scores, pairs, strict=True)
        ],
    }
