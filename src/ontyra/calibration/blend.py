"""Suggest how much to trust an outside model's probability: the reranker (`rerank_weight`) or the
decision model (`decision_weight`, `decision_margin`). Pure, no model calls.

The engine blends such a probability `p` into the fused score, `final = (1 - w) * fused + w * p`
for the candidates the model was asked about (the others keep `(1 - w) * fused`), and keeps what
is within a margin of the best final score. Given the probabilities recorded per request, the
weight and margin are searched the same way as the recall settings: the choice is made on the
`tune` split, `holdout` is reported next to it, and the baseline is the engine without the model
(`w = 0`, the recall margin) on the same requests.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any

from ontyra.calibration.search import MARGINS, Request, Setting, fused

WEIGHTS = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
Signals = dict[str, dict[str, float]]  # request id -> memory key -> probability


def passes(
    request: Request, s: Setting, signal: dict[str, float], weight: float, margin: float
) -> bool:
    """Would the engine answer this request correctly with the model blended in?"""
    if request.best_cos is not None and request.best_cos < s.tau:
        return request.silent
    base = fused(request.pool, s.alpha, s.baseline)
    final = {k: (1 - weight) * f + weight * signal.get(k, 0.0) for k, f in base.items()}
    if not final:
        return request.silent
    best = max(final.values())
    kept = {k for k, v in final.items() if v >= best - margin and v > 0}
    if request.silent:
        return not kept
    return request.relevant <= kept and not kept & request.negative


def _score(
    reqs: list[Request], s: Setting, signals: Signals, weight: float, margin: float, split: str
) -> tuple[int, int]:
    mine = [r for r in reqs if r.split == split and r.id in signals]
    return sum(passes(r, s, signals[r.id], weight, margin) for r in mine), len(mine)


@dataclass
class Blend:
    weight: float
    margin: float
    tune: tuple[int, int]
    holdout: tuple[int, int]
    plateau: int
    without: dict[str, tuple[int, int]]  # the engine without the model, same requests
    current: dict[str, Any]


def suggest(
    reqs: list[Request],
    s: Setting,
    signals: Signals,
    *,
    current: tuple[float, float],
) -> Blend | None:
    """The weight and margin that fit best; None when there are too few requests with signals."""
    asked = [r for r in reqs if r.id in signals]
    tune = [r for r in asked if r.split == "tune"] or asked
    if len(tune) < 10:
        return None
    grid = [(w, m) for w in WEIGHTS for m in MARGINS]
    scored = [(sum(passes(r, s, signals[r.id], w, m) for r in tune), w, m) for w, m in grid]
    top = max(c for c, _, _ in scored)
    ties = [(w, m) for c, w, m in scored if c == top]
    weight = statistics.median_low(sorted({w for w, _ in ties}))
    margin = statistics.median_low(sorted({m for _, m in ties}))
    if sum(passes(r, s, signals[r.id], weight, margin) for r in tune) != top:
        weight, margin = ties[0]
    without = {
        split: _score(reqs, s, signals, 0.0, s.margin, split) for split in ("tune", "holdout")
    }
    return Blend(
        weight=weight,
        margin=margin,
        tune=_score(reqs, s, signals, weight, margin, "tune"),
        holdout=_score(reqs, s, signals, weight, margin, "holdout"),
        plateau=len(ties),
        without=without,
        current={
            "weight": current[0],
            "margin": current[1],
            "tune": _score(reqs, s, signals, *current, "tune"),
            "holdout": _score(reqs, s, signals, *current, "holdout"),
        },
    )
