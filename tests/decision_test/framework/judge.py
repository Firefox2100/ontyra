"""Task `judge`: which of the retrieved candidates matter for the situation? (Recall's question.)

The candidates are what the fusion engine would send the decision model: the top applicable
memories of a real recall request (`Probe` records the fused score of every memory). Each
request is labelled by the recall cases (`include` = matters, `exclude` and other domains =
does not, same-domain neighbours are neutral). Three things are measured per variant:

* **ranking**: does the model put a relevant candidate first (top-1) and above the irrelevant
  ones (AUC), compared with the pipeline's own order and with the 50/50 blend recall uses;
* **abstention**: on requests that must stay silent, is "none of these" more likely than on the
  others (AUC of the none probability);
* **cost**: latency per request.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from decision_test.framework.metrics import auc, mean, rounded
from ontyra.app import Application
from ontyra.calibration.models import Case, World, load_cases
from ontyra.calibration.probe import Probe
from ontyra.calibration.ranking import Row, labels
from ontyra.models.decision import Judgement
from ontyra.models.recall import RecallRequest
from ontyra.services import Caller
from ontyra.services.recall_fusion import situation_text

Judge = Callable[[str, Sequence[str]], Awaitable[Judgement]]
BLEND = 0.5  # `decision_weight` of the deployed profile


@dataclass
class Candidate:
    key: str
    text: str
    fused: float


@dataclass
class JudgeRequest:
    id: str
    tags: list[str]
    situation: str
    silent: bool
    candidates: list[Candidate]
    relevant: set[str]
    negative: set[str]
    extra: dict[str, Any] = field(default_factory=dict)


def sample(cases: list[Case], limit: int) -> list[Case]:
    """Every n-th case so that about `limit` remain (all when `limit` is 0): stable, spread over
    the tags rather than the first files."""
    if not limit or len(cases) <= limit:
        return cases
    step = len(cases) / limit
    return [cases[int(i * step)] for i in range(limit)]


async def collect(
    app: Application,
    caller: Caller,
    keys: dict[str, str],
    world: World,
    *,
    limit: int,
    candidates: int,
    only: str | None = None,
) -> list[JudgeRequest]:
    texts = {m.key: m.text for m in world.memories}
    domains = {m.key: m.domain for m in world.memories}
    chosen = [
        c
        for c in load_cases("recall")
        if c.world == world.id
        and c.mode == "recall"
        and c.query
        and (c.include or c.silent)
        and (not only or only in c.id)
    ]
    probe = Probe(app, caller, keys)
    await probe.prepare()
    out: list[JudgeRequest] = []
    for case in sample(chosen, limit):
        rows = await probe.rows(case, None)
        if not rows:
            continue
        parsed = [Row.from_list(r) for r in rows]
        ranked = sorted(
            (r for r in parsed if r.applies and (r.fused or r.relevance or 0) > 0),
            key=lambda r: -(r.fused if r.fused is not None else (r.relevance or 0)),
        )[:candidates]
        if not ranked:
            continue
        cands = [
            Candidate(r.key, texts[r.key], r.fused if r.fused is not None else r.relevance or 0.0)
            for r in ranked
        ]
        relevant, _, negative = labels(case, [c.key for c in cands], domains)
        request = RecallRequest(context=case.context, query=case.query)
        out.append(
            JudgeRequest(
                id=case.id,
                tags=case.tags,
                situation=situation_text(request, case.query or ""),
                silent=case.silent,
                candidates=cands,
                relevant=relevant,
                negative=negative,
            )
        )
    return out


def shuffled(request: JudgeRequest) -> list[int]:
    """A fixed permutation of the candidates of a request (position bias is measured by asking
    in this order and mapping the answers back)."""
    seed = int(hashlib.sha1(request.id.encode()).hexdigest()[:8], 16)
    order = list(range(len(request.candidates)))
    random.Random(seed).shuffle(order)
    return order


async def ask(judge: Judge, request: JudgeRequest, *, shuffle: bool = False) -> Judgement:
    order = shuffled(request) if shuffle else list(range(len(request.candidates)))
    answer = await judge(request.situation, [request.candidates[i].text for i in order])
    scores: list[float | None] = [None] * len(order)
    for position, index in enumerate(order):
        if position < len(answer.scores):
            scores[index] = answer.scores[position]
    return answer.model_copy(update={"scores": scores})


def _probabilities(answer: Judgement) -> list[float]:
    return [s or 0.0 for s in answer.scores]


def score_request(request: JudgeRequest, answer: Judgement) -> dict[str, Any]:
    keys = [c.key for c in request.candidates]
    p = _probabilities(answer)
    fused = [c.fused for c in request.candidates]
    blended = [(1 - BLEND) * f + BLEND * q for f, q in zip(fused, p, strict=True)]
    # without a "none" outcome (noul) the abstention signal is how little the best candidate gets
    nothing = answer.none if answer.none is not None else 1.0 - max(p, default=0.0)
    row: dict[str, Any] = {
        "id": request.id,
        "tags": request.tags,
        "silent": request.silent,
        "candidates": len(keys),
        "nothing": rounded(nothing, 4),
        "scores": {k: rounded(q, 4) for k, q in zip(keys, p, strict=True)},
    }
    relevant = [k in request.relevant for k in keys]
    if request.silent or not any(relevant):
        return row

    def first(values: list[float]) -> bool:
        return keys[max(range(len(keys)), key=lambda i: (values[i], -i))] in request.relevant

    def pos_neg(values: list[float]) -> float | None:
        return auc(
            [v for v, k in zip(values, keys, strict=True) if k in request.relevant],
            [v for v, k in zip(values, keys, strict=True) if k in request.negative],
        )

    row |= {
        "top1": {"pipeline": first(fused), "model": first(p), "blend": first(blended)},
        "auc": {
            "pipeline": pos_neg(fused),
            "model": pos_neg(p),
            "blend": pos_neg(blended),
        },
        "picked": keys[max(range(len(keys)), key=lambda i: (p[i], -i))],
    }
    return row


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ranked = [r for r in rows if "top1" in r]
    silent = [r["nothing"] for r in rows if r["silent"]]
    other = [r["nothing"] for r in rows if not r["silent"]]
    out: dict[str, Any] = {
        "requests": len(rows),
        "ranked": len(ranked),
        "silent": len(silent),
        "abstention_auc": rounded(auc(silent, other)),
    }
    for who in ("pipeline", "model", "blend"):
        out[f"top1_{who}"] = rounded(mean([float(r["top1"][who]) for r in ranked]))
        out[f"auc_{who}"] = rounded(mean([r["auc"][who] for r in ranked]))
    by_tag: dict[str, Any] = {}
    for tag in sorted({t for r in ranked for t in r["tags"]}):
        group = [r for r in ranked if tag in r["tags"]]
        if len(group) >= 3:
            by_tag[tag] = {
                "n": len(group),
                **{
                    f"top1_{who}": rounded(mean([float(r["top1"][who]) for r in group]))
                    for who in ("pipeline", "model", "blend")
                },
            }
    out["by_tag"] = by_tag
    return out


async def evaluate(
    judge: Judge, requests: list[JudgeRequest], *, shuffle: bool = False
) -> dict[str, Any]:
    answers = await asyncio.gather(*(ask(judge, r, shuffle=shuffle) for r in requests))
    rows = [score_request(r, a) for r, a in zip(requests, answers, strict=True)]
    return {**summarise(rows), "rows": rows}
