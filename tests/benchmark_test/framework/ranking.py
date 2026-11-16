"""Threshold-free retrieval metrics over the recorded per-memory scores of a request.

A request's *rows* (see `probe.py`) carry, for every memory in the pool, each lane's raw score.
Ranking a lane's scores against the labels tells how good the lane is *before* any cut-off, which
is what a hybrid (lexical + embedding, then rerank) design needs to optimise.

Labels: `include` = relevant. `allow` (and `allow_prefix`) = neutral. `exclude` = negative.
Everything else is negative, except memories of the same domain as a relevant one, which are
neutral (a neighbouring rule is not a leak: the same policy as the pass/fail scoring).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from benchmark_test.framework.models import Step

COLUMNS = (
    "key",
    "bm25",
    "cos",
    "concept",
    "cov",
    "applies",
    "relevance",
    "score",
    "fused",
    "rerank",
    "final",
)
LANES = ("bm25", "lexical", "cosine", "concept", "relevance", "score", "fused", "rerank", "final")
KS = (1, 3, 5, 10)


@dataclass
class Row:
    key: str
    bm25: float | None
    cos: float | None
    concept: float | None
    cov: float
    applies: bool
    relevance: float | None
    score: float | None
    fused: float | None = None  # fusion engine only
    rerank: float | None = None
    final: float | None = None

    @classmethod
    def from_list(cls, raw: Sequence[Any]) -> Row:
        key, bm25, cos, concept, cov, applies, relevance, score, *extra = raw
        fused, rerank, final = [*list(extra), None, None, None][:3]
        return cls(
            key, bm25, cos, concept, cov, bool(applies), relevance, score, fused, rerank, final
        )


def lane_value(row: Row, lane: str, top_bm25: float) -> float | None:
    """The score of one lane for one memory; None = the lane did not retrieve it."""
    if lane == "bm25":
        return row.bm25
    if lane == "lexical":  # what the pipeline uses: relative BM25 x query coverage
        return None if row.bm25 is None or top_bm25 <= 0 else row.bm25 / top_bm25 * row.cov
    if lane == "cosine":
        return row.cos
    if lane == "concept":
        return row.concept
    if lane == "relevance":
        return row.relevance if row.relevance else None
    if lane == "score":
        return row.score if row.score else None
    if lane in ("fused", "rerank", "final"):
        return getattr(row, lane)
    raise ValueError(lane)


def labels(
    step: Step, keys: Sequence[str], domains: dict[str, str]
) -> tuple[set[str], set[str], set[str]]:
    """(relevant, neutral, negative) memory keys of a step."""
    relevant = set(step.include)
    neutral = set(step.allow) | {k for k in keys if k.startswith(tuple(step.allow_prefix))}
    near = {domains.get(k) for k in relevant} - {None}
    if not step.strict:
        neutral |= {k for k in keys if domains.get(k) in near}
    neutral -= relevant | set(step.exclude)
    negative = set(keys) - relevant - neutral
    return relevant, neutral, negative


def rank_lane(
    values: dict[str, float | None], relevant: set[str], negative: set[str]
) -> dict[str, int]:
    """Pessimistic rank of each relevant memory among relevant and negative ones.

    Ties count against the memory (a lane that gives everything the same score ranks nothing);
    memories the lane did not retrieve rank after everything it did.
    """
    pool = relevant | negative
    floor = -math.inf
    score: dict[str, float] = {}
    for k in pool:
        v = values.get(k)
        score[k] = floor if v is None else v
    out: dict[str, int] = {}
    for k in relevant:
        better = sum(1 for o in pool if o != k and score[o] > score[k])
        tied_neg = sum(1 for o in negative if score[o] == score[k])
        out[k] = 1 + better + tied_neg
    return out


def metrics_for(ranks: dict[str, int], n_negative: int) -> dict[str, float]:
    """Recall@k, MRR, nDCG@10 and AUC of one request from its relevant memories' ranks."""
    n_rel = len(ranks)
    ordered = sorted(ranks.values())
    out: dict[str, float] = {f"r@{k}": sum(r <= k for r in ordered) / n_rel for k in KS}
    out["mrr"] = 1.0 / ordered[0]
    dcg = sum(1.0 / math.log2(r + 1) for r in ordered if r <= 10)
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(n_rel, 10)))
    out["ndcg@10"] = dcg / ideal
    # AUC: a relevant memory at pessimistic rank r has r - 1 - (relevant ahead of it) negatives above it
    ahead = [r - 1 - i for i, r in enumerate(ordered)]
    out["auc"] = (
        1.0 if n_negative == 0 else 1.0 - sum(max(a, 0) for a in ahead) / (n_rel * n_negative)
    )
    return out


def step_metrics(
    rows: list[Row],
    step: Step,
    domains: dict[str, str],
    *,
    lanes: Sequence[str] = LANES,
    applicable_only: bool = True,
    value: Callable[[Row, str, float], float | None] = lane_value,
) -> dict[str, dict[str, float]]:
    """Every lane's metrics for one request (empty when the step expects nothing)."""
    if not step.include:
        return {}
    pool = [r for r in rows if r.applies or not applicable_only]
    keys = [r.key for r in pool]
    relevant, _, negative = labels(step, keys, domains)
    relevant &= set(keys)
    if not relevant:
        return {}
    top = max((r.bm25 or 0.0 for r in pool), default=0.0)
    out: dict[str, dict[str, float]] = {}
    for lane in lanes:
        values = {r.key: value(r, lane, top) for r in pool}
        out[lane] = metrics_for(rank_lane(values, relevant, negative), len(negative))
    return out


def mean_metrics(per_step: list[dict[str, dict[str, float]]]) -> dict[str, dict[str, float]]:
    """Average of per-request metrics, lane by lane (requests with no relevant memory skipped)."""
    out: dict[str, dict[str, float]] = {}
    steps = [s for s in per_step if s]
    for lane in LANES:
        got = [s[lane] for s in steps if lane in s]
        if got:
            out[lane] = {m: sum(g[m] for g in got) / len(got) for m in got[0]}
            out[lane]["n"] = float(len(got))
    return out
