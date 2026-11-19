"""Suggest recall settings from recorded lane scores: pure, deterministic, no model calls.

The fusion engine keeps the candidates whose fused score is within `fusion_margin` of the best,
and stays silent when the best cosine is below `abstain_cosine`. Candidates are a prefix of the
fused order, so for each request and each (alpha, baseline) the question "does this margin keep
every relevant memory and no negative one?" reduces to two ranks and a bisect; the whole grid is
searched in well under a second. The choice is made on the `tune` split; `holdout` is reported
next to it and next to the current settings, and the suggestion is the middle of the plateau of
best settings, not the first one found (a knife edge is flagged).
"""

from __future__ import annotations

import bisect
import statistics
from dataclasses import dataclass, field
from typing import Any, Literal

from ontyra.calibration.models import Case
from ontyra.calibration.ranking import Row, labels

OVERFETCH = 150  # per lane, as the fusion engine's default
ALPHAS = (0.0, 0.1, 0.2, 0.3, 0.4, 0.6)
MARGINS = tuple(round(0.02 * i, 2) for i in range(1, 21))  # 0.02 .. 0.40
Baseline = Literal["mean", "min"]


@dataclass(frozen=True)
class Setting:
    alpha: float
    baseline: Baseline
    margin: float
    tau: float  # abstain when the best cosine is below this (0 = never)

    def env(self) -> dict[str, str]:
        return {
            "ONTYRA_RECALL__FUSION_ALPHA": f"{self.alpha:g}",
            "ONTYRA_RECALL__FUSION_BASELINE": self.baseline,
            "ONTYRA_RECALL__FUSION_MARGIN": f"{self.margin:g}",
            "ONTYRA_RECALL__ABSTAIN_COSINE": f"{self.tau:.3f}",
        }


@dataclass
class Request:
    """One recorded request: the applicable memories with their lane scores, and its labels."""

    id: str
    split: str
    silent: bool
    pool: list[Row]
    relevant: set[str]
    negative: set[str]
    best_cos: float | None = None
    _ranked: dict[tuple[float, str], tuple[list[float], list[str]]] = field(
        default_factory=lambda: {}
    )

    def ranked(self, alpha: float, baseline: Baseline) -> tuple[list[float], list[str]]:
        """Fused scores, best first (negated, so `bisect` can search them) and the keys."""
        if (alpha, baseline) not in self._ranked:
            scores = fused(self.pool, alpha, baseline)
            keys = sorted(scores, key=lambda k: (-scores[k], k))
            self._ranked[(alpha, baseline)] = ([-scores[k] for k in keys], keys)
        return self._ranked[(alpha, baseline)]


def fused(pool: list[Row], alpha: float, baseline: Baseline) -> dict[str, float]:
    """The fusion engine's score: `alpha * lexical + (1 - alpha) * dense`."""
    top_bm25 = max((r.bm25 or 0.0 for r in pool), default=0.0) or 1.0
    cosines = [r.cos for r in pool if r.cos is not None]
    base = top = 0.0
    if cosines:
        top = max(cosines)
        base = statistics.mean(cosines) if baseline == "mean" else min(cosines)
    span = (top - base) or 1.0
    a = alpha if cosines else 1.0
    out: dict[str, float] = {}
    for r in pool:
        lexical = (r.bm25 or 0.0) / top_bm25 * r.cov if r.bm25 is not None else 0.0
        dense = max(0.0, (r.cos - base) / span) if r.cos is not None else 0.0
        out[r.key] = a * lexical + (1 - a) * dense
    return out


def requests(
    recorded: dict[str, list[list[Any]]], cases: list[Case], domains: dict[str, str]
) -> list[Request]:
    """Turn recorded rows (`case id -> rows`) into labelled requests (recall cases that expect
    something, or silence)."""
    by_id = {c.id: c for c in cases}
    out: list[Request] = []
    for case_id, raw in recorded.items():
        case = by_id.get(case_id)
        if case is None or case.mode != "recall" or not case.query:
            continue
        if not (case.silent or case.include):
            continue
        pool = [Row.from_list(r) for r in raw if r[5]]  # applicable only
        by_bm25 = sorted((r for r in pool if r.bm25 is not None), key=lambda r: -(r.bm25 or 0))
        by_cos = sorted((r for r in pool if r.cos is not None), key=lambda r: -(r.cos or 0))
        keep = {r.key for r in by_bm25[:OVERFETCH]} | {r.key for r in by_cos[:OVERFETCH]}
        pool = [r for r in pool if r.key in keep]
        keys = [r.key for r in pool]
        relevant, _, negative = labels(case, keys, domains)
        cosines = [r.cos for r in pool if r.cos is not None]
        out.append(
            Request(
                id=case.id,
                split=case.split,
                silent=case.silent,
                pool=pool,
                relevant=relevant & set(keys),
                negative=negative,
                best_cos=max(cosines) if cosines else None,
            )
        )
    return out


def passes(request: Request, s: Setting) -> bool:
    """Would the engine, with these settings, answer this request correctly?"""
    if request.best_cos is not None and request.best_cos < s.tau:
        return request.silent  # abstained: right only when silence was expected
    negated, keys = request.ranked(s.alpha, s.baseline)
    if not keys:
        return request.silent
    best = -negated[0]
    # kept = fused >= best - margin and > 0: a prefix of the order
    n = min(
        bisect.bisect_right(negated, -(best - s.margin) + 1e-12), bisect.bisect_left(negated, 0.0)
    )
    kept = set(keys[:n])
    if request.silent:
        return not kept
    return request.relevant <= kept and not kept & request.negative


def score(reqs: list[Request], s: Setting, split: str) -> tuple[int, int]:
    mine = [r for r in reqs if r.split == split]
    return sum(passes(r, s) for r in mine), len(mine)


@dataclass
class Suggestion:
    setting: Setting
    tune: tuple[int, int]
    holdout: tuple[int, int]
    plateau: int  # settings that tie for the best tune score
    knife_edge: bool  # the best score is reached by a single setting
    best_cosine_percentiles: dict[str, float]


def thresholds(reqs: list[Request]) -> list[float]:
    """Candidate abstention cosines: 0 and the quantiles of the best cosine per request, so the
    grid follows the model's own scale."""
    tops = sorted(r.best_cos for r in reqs if r.best_cos is not None)
    if not tops:
        return [0.0]
    cuts = {0.0}
    for i in range(0, 41):
        value = tops[min(len(tops) - 1, int(len(tops) * i / 100))]
        cuts |= {round(value - 1e-4, 4), round(value + 1e-4, 4)}
    return sorted(cuts)


def suggest(reqs: list[Request]) -> Suggestion:
    tune = [r for r in reqs if r.split == "tune"] or reqs
    taus = thresholds(reqs)
    grid = [
        Setting(a, b, m, t) for a in ALPHAS for b in ("mean", "min") for m in MARGINS for t in taus
    ]
    scored = [(sum(passes(r, s) for r in tune), s) for s in grid]
    top = max(score for score, _ in scored)
    ties = [s for score, s in scored if score == top]
    middle = Setting(
        alpha=statistics.median_low(sorted({s.alpha for s in ties})),
        baseline="mean" if sum(s.baseline == "mean" for s in ties) * 2 >= len(ties) else "min",
        margin=statistics.median_low(sorted({s.margin for s in ties})),
        tau=statistics.median_low(sorted({s.tau for s in ties})),
    )
    chosen = middle if sum(passes(r, middle) for r in tune) == top else ties[0]
    tops = sorted(r.best_cos for r in reqs if r.best_cos is not None)
    pct = (
        {
            f"p{q}": round(tops[min(len(tops) - 1, int(len(tops) * q / 100))], 3)
            for q in (5, 25, 50, 75)
        }
        if tops
        else {}
    )
    return Suggestion(
        setting=chosen,
        tune=score(reqs, chosen, "tune"),
        holdout=score(reqs, chosen, "holdout"),
        plateau=len(ties),
        knife_edge=len(ties) == 1,
        best_cosine_percentiles=pct,
    )


def separation(reqs: list[Request]) -> dict[str, float | None]:
    """How well the cosine alone separates relevant from irrelevant memories of the same request
    (mean AUC), and how often the best cosine of a silent request is below that of the others."""
    aucs: list[float] = []
    for r in reqs:
        pos = [x.cos for x in r.pool if x.key in r.relevant and x.cos is not None]
        neg = [x.cos for x in r.pool if x.key in r.negative and x.cos is not None]
        if pos and neg:
            aucs.append(
                sum((p > n) + 0.5 * (p == n) for p in pos for n in neg) / (len(pos) * len(neg))
            )
    silent = [r.best_cos for r in reqs if r.silent and r.best_cos is not None]
    other = [r.best_cos for r in reqs if not r.silent and r.best_cos is not None]
    silence = (
        sum((s < o) + 0.5 * (s == o) for s in silent for o in other) / (len(silent) * len(other))
        if silent and other
        else None
    )
    return {
        "relevant_vs_irrelevant_auc": round(sum(aucs) / len(aucs), 3) if aucs else None,
        "silence_auc": None if silence is None else round(silence, 3),
    }
