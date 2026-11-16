"""Offline evaluation of retrieval methods over recorded per-memory scores (no model, no DB).

Every method turns a request's rows into one score per memory; `ranking.rank_lane` and
`metrics_for` then rate it against the labels. Methods include the single lanes, reciprocal
rank fusion (RRF), and a convex combination of min-max normalised lexical and cosine scores.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from benchmark_test.framework.models import Case, Step
from benchmark_test.framework.ranking import (
    Row,
    labels,
    lane_value,
    mean_metrics,
    metrics_for,
    rank_lane,
)

Method = Callable[[list[Row]], dict[str, float | None]]
RRF_K = 60


def _ranks(values: dict[str, float | None]) -> dict[str, int]:
    scored = sorted(
        (k for k, v in values.items() if v is not None), key=lambda k: -(values[k] or 0)
    )
    return {k: i for i, k in enumerate(scored, start=1)}


def lane(name: str) -> Method:
    def run(rows: list[Row]) -> dict[str, float | None]:
        top = max((r.bm25 or 0.0 for r in rows), default=0.0)
        return {r.key: lane_value(r, name, top) for r in rows}

    return run


def rrf(*names: str, weights: tuple[float, ...] | None = None) -> Method:
    """Reciprocal rank fusion of the named lanes (rank 1 = best among the memories that apply)."""
    w = weights or tuple(1.0 for _ in names)

    def run(rows: list[Row]) -> dict[str, float | None]:
        per = [_ranks(lane(n)(rows)) for n in names]
        return {
            r.key: sum(wi / (RRF_K + p[r.key]) for wi, p in zip(w, per, strict=True) if r.key in p)
            or None
            for r in rows
        }

    return run


def _minmax(values: dict[str, float | None]) -> dict[str, float]:
    got = [v for v in values.values() if v is not None]
    if not got:
        return {}
    lo, hi = min(got), max(got)
    span = (hi - lo) or 1.0
    return {k: (v - lo) / span for k, v in values.items() if v is not None}


def linear(alpha: float, a: str = "lexical", b: str = "cosine") -> Method:
    """alpha * minmax(a) + (1 - alpha) * minmax(b), per request, over the memories that apply."""

    def run(rows: list[Row]) -> dict[str, float | None]:
        na, nb = _minmax(lane(a)(rows)), _minmax(lane(b)(rows))
        return {r.key: alpha * na.get(r.key, 0.0) + (1 - alpha) * nb.get(r.key, 0.0) for r in rows}

    return run


def evaluate(
    method: Method,
    requests: list[tuple[Case, Step, list[Row]]],
    domains: dict[str, str],
    *,
    split: str | None = None,
) -> dict[str, float]:
    """Mean metrics of a method over requests (those with relevant memories), optionally one split."""
    per: list[dict[str, dict[str, float]]] = []
    for case, step, rows in requests:
        if (split and case.split != split) or not step.include:
            continue
        pool = [r for r in rows if r.applies]
        keys = [r.key for r in pool]
        relevant, _, negative = labels(step, keys, domains)
        relevant &= set(keys)
        if not relevant:
            continue
        values = method(pool)
        per.append({"score": metrics_for(rank_lane(values, relevant, negative), len(negative))})
    return mean_metrics(per).get("score", {})


def standard_methods() -> dict[str, Method]:
    methods: dict[str, Method] = {
        n: lane(n) for n in ("lexical", "cosine", "concept", "relevance", "score")
    }
    methods["rrf(lexical,cosine)"] = rrf("lexical", "cosine")
    methods["rrf(lexical,cosine,concept)"] = rrf("lexical", "cosine", "concept")
    return methods


def alpha_sweep(steps: int = 10) -> dict[str, Method]:
    return {f"linear(a={i / steps:.1f})": linear(i / steps) for i in range(steps + 1)}


def pick_best(
    candidates: dict[str, Method],
    requests: list[tuple[Case, Step, list[Row]]],
    domains: dict[str, str],
    metric: str = "ndcg@10",
) -> tuple[str, dict[str, Any]]:
    """The method with the best `metric` on the tuning split, with its tune and holdout results."""
    tune = {n: evaluate(m, requests, domains, split="tune") for n, m in candidates.items()}
    best = max(tune, key=lambda n: tune[n].get(metric, 0.0))
    return best, {
        "tune": tune[best],
        "holdout": evaluate(candidates[best], requests, domains, split="holdout"),
    }
