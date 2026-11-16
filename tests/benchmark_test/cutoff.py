"""Tune the fusion cut-off offline: `PYTHONPATH=tests uv run python -m benchmark_test.cutoff RESULT...`.

Replays the fusion engine's candidate selection on the recorded lane scores (no model calls) and
searches its settings: lexical weight (alpha), the dense baseline, the relative margin, a floor
of candidates that are always kept (`min_keep`), pinning of memories that lead either lane
(`pin`), and the abstention cosine. A request passes when every expected memory is kept and no
memory that must not appear is (or, for a silence request, nothing is). The choice is made on
the tune split only; holdout is reported next to it and next to the engine's current settings.

The replay selects candidates only. Precedence, collapse and packing are not simulated, so these
numbers are for choosing settings, not for reporting a result: confirm with the recall suite.
"""

from __future__ import annotations

import itertools
import json
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

from benchmark_test.framework.models import Step, load_cases, load_world
from benchmark_test.framework.ranking import Row, labels
from benchmark_test.framework.results import read_rows

OVERFETCH = 150


@dataclass(frozen=True)
class Setting:
    alpha: float
    baseline: str  # mean | min
    margin: float
    min_keep: int
    pin: int  # keep the top-N of either lane (0 = off)
    tau: float  # abstain when the best cosine is below this
    knee: float = 0.0  # >0: cut at the largest drop among the top candidates when it is this big


@dataclass
class Request:
    split: str
    step: Step
    pool: list[Row]
    cache: dict[tuple[float, str], tuple[dict[str, float], list[str]]] = field(
        default_factory=lambda: {}
    )
    leaders: dict[int, set[str]] = field(default_factory=lambda: {})


def requests(result: Path, domains_cases: dict[str, object]) -> list[Request]:
    cases = domains_cases
    out: list[Request] = []
    for (case_id, index), raw in read_rows(result).items():
        case = cases[case_id]
        step = [case, *case.then][index]  # type: ignore[attr-defined]
        if step.mode == "briefing" or not step.query or not (step.silent or step.include):
            continue
        pool = [Row.from_list(r) for r in raw if r[5]]  # applicable only
        bm = sorted((r for r in pool if r.bm25 is not None), key=lambda r: -(r.bm25 or 0))
        cs = sorted((r for r in pool if r.cos is not None), key=lambda r: -(r.cos or 0))
        keep = {r.key for r in bm[:OVERFETCH]} | {r.key for r in cs[:OVERFETCH]}
        out.append(Request(case.split, step, [r for r in pool if r.key in keep]))  # type: ignore[attr-defined]
    return out


def fused(pool: list[Row], alpha: float, baseline: str) -> dict[str, float]:
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


def select(req: Request, s: Setting) -> set[str]:
    pool = req.pool
    cosines = [r.cos for r in pool if r.cos is not None]
    if cosines and max(cosines) < s.tau:
        return set()
    if (s.alpha, s.baseline) not in req.cache:
        scores = fused(pool, s.alpha, s.baseline)
        req.cache[(s.alpha, s.baseline)] = (scores, sorted(scores, key=lambda k: -scores[k]))
    score, ranked = req.cache[(s.alpha, s.baseline)]
    best = score[ranked[0]] if ranked else 0.0
    kept = {k for k, v in score.items() if v >= best - s.margin and v > 0}
    if s.knee:
        drops = [
            (score[a] - score[b], i) for i, (a, b) in enumerate(itertools.pairwise(ranked[:6]))
        ]
        if drops:
            drop, at = max(drops)
            kept = set(ranked[: at + 1]) if drop >= s.knee else kept
    kept |= set(ranked[: s.min_keep])
    if s.pin:
        if s.pin not in req.leaders:
            by_bm25 = sorted((r for r in pool if r.bm25 is not None), key=lambda r: -(r.bm25 or 0))
            by_cos = sorted((r for r in pool if r.cos is not None), key=lambda r: -(r.cos or 0))
            req.leaders[s.pin] = {r.key for r in by_bm25[: s.pin]} | {
                r.key for r in by_cos[: s.pin]
            }
        kept |= req.leaders[s.pin]
    return kept


def passes(req: Request, kept: set[str], domains: dict[str, str]) -> bool:
    keys = [r.key for r in req.pool]
    relevant, _, negative = labels(req.step, keys, domains)
    relevant &= set(keys)
    if req.step.silent:
        return not kept
    return relevant <= kept and not (kept & negative)


def score(reqs: list[Request], s: Setting, domains: dict[str, str], split: str) -> tuple[int, int]:
    mine = [r for r in reqs if r.split == split]
    return sum(passes(r, select(r, s), domains) for r in mine), len(mine)


def search(reqs: list[Request], domains: dict[str, str]) -> tuple[Setting, tuple[int, int]]:
    tops = sorted(max((r.cos or 0.0 for r in q.pool), default=0.0) for q in reqs)
    taus = [0.0] + [tops[int(len(tops) * f)] for f in (0.05, 0.1, 0.15, 0.2, 0.3)]
    best: tuple[int, Setting] | None = None
    grid = itertools.product(
        (0.0, 0.1, 0.2, 0.4),
        ("mean", "min"),
        (0.05, 0.1, 0.2, 0.3, 0.4),
        (0, 1, 2, 3),
        (0, 1, 2),
        taus,
        (0.0, 0.05, 0.1, 0.15, 0.2),
    )
    for alpha, baseline, margin, min_keep, pin, tau, knee in grid:
        s = Setting(alpha, baseline, margin, min_keep, pin, tau, knee)
        ok, _ = score(reqs, s, domains, "tune")
        if best is None or ok > best[0]:
            best = (ok, s)
    assert best is not None
    return best[1], score(reqs, best[1], domains, "tune")


def main(paths: list[str]) -> int:
    cases = {c.id: c for c in load_cases("recall")}
    domains = {m.key: m.domain for m in load_world("commons").memories}
    for path in map(Path, paths):
        result = json.loads(path.read_text(encoding="utf-8"))
        reqs = requests(path, cases)  # type: ignore[arg-type]
        if not reqs:
            print(f"{path.name}: no recorded lane scores")
            continue
        recall = result.get("recall", {})
        current = Setting(
            recall.get("fusion_alpha", 0.2),
            recall.get("fusion_baseline", "mean"),
            recall.get("fusion_margin", 0.1),
            0,
            0,
            recall.get("abstain_cosine", 0.0),
        )
        print(f"\n== {result.get('profile', path.stem)}  ({len(reqs)} requests)")
        t, h = score(reqs, current, domains, "tune"), score(reqs, current, domains, "holdout")
        print(f"  current  {current}\n           tune {t[0]}/{t[1]}  holdout {h[0]}/{h[1]}")
        best, t = search(reqs, domains)
        h = score(reqs, best, domains, "holdout")
        print(f"  best     {best}\n           tune {t[0]}/{t[1]}  holdout {h[0]}/{h[1]}")
        only = Setting(
            current.alpha, current.baseline, current.margin, 2, 0, current.tau
        )  # the floor alone
        t, h = score(reqs, only, domains, "tune"), score(reqs, only, domains, "holdout")
        print(f"  floor 2  tune {t[0]}/{t[1]}  holdout {h[0]}/{h[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
