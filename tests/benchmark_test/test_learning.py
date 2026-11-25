"""The score layer's learning curve on simulated feedback.

Every recall case of the bundled worlds becomes a judged recall: the labels the benchmark already
has stand in for a user's verdicts on what was shown (the best candidates by plain score).
Feedback from the `tune` requests trains the layer; the `holdout` requests measure it (pairwise
accuracy: how often a relevant memory outranks a not relevant one, per request). The curve shows
how much feedback it takes, the poisoned rows what happens when a share of the verdicts is wrong.

A measurement, with one sanity assertion: the layer must not make clean feedback worse. The canary
is drawn from the same stream, so it catches noise, not consistent poisoning: that needs a trusted
canary.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass

from ontyra.calibration.models import load_cases, world_ids
from ontyra.calibration.probe import Probe
from ontyra.calibration.ranking import Row, labels
from ontyra.calibration.search import fused
from ontyra.models.feedback import LearningSettings
from ontyra.services.scorelayer import Layer, Observation, build, pairwise

SHOWN = 6  # candidates a simulated user judges per recall: the best by plain score
CFG = LearningSettings(
    enabled=True, min_examples=12, canary_min_groups=2, canary_fraction=0.25, max_mix=0.5
)


@dataclass
class Judged:
    case: str
    split: str
    obs: list[Observation]


def features(pool: list[Row]) -> dict[str, tuple[float, ...]]:
    """lexical, dense, concept, rerank, decision per memory (as the fusion engine computes them)."""
    top_bm25 = max((r.bm25 or 0.0 for r in pool), default=0.0) or 1.0
    cosines = [r.cos for r in pool if r.cos is not None]
    base = sum(cosines) / len(cosines) if cosines else 0.0
    span = ((max(cosines) - base) if cosines else 0.0) or 1.0
    out: dict[str, tuple[float, ...]] = {}
    for r in pool:
        lexical = (r.bm25 or 0.0) / top_bm25 * r.cov if r.bm25 is not None else 0.0
        dense = max(0.0, (r.cos - base) / span) if r.cos is not None else 0.0
        out[r.key] = (lexical, dense, r.concept or 0.0, 0.0, 0.0)
    return out


def flipped(case_id: str, share: float) -> bool:
    """Is this request's feedback poisoned? Stable and uniform over the requests."""
    return int(hashlib.sha1(case_id.encode()).hexdigest()[:6], 16) / 0xFFFFFF < share


def accuracy(judged: list[Judged], layer: Layer | None) -> float:
    groups: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for j in judged:
        for o in j.obs:
            score = layer.score(o.memory, o.features, o.base) if layer else o.base
            groups[o.group].append((score, o.relevance))
    return pairwise(groups)[0]


def poisoned(judged: list[Judged], share: float) -> list[Observation]:
    out: list[Observation] = []
    for j in judged:
        for o in j.obs:
            rel = 1.0 - o.relevance if flipped(j.case, share) else o.relevance
            out.append(Observation(o.group, o.memory, o.source, o.features, o.base, rel, o.weight))
    return out


async def test_learning_curve(world_app, worlds, profile, capsys) -> None:
    judged: list[Judged] = []
    for world_id in world_ids():
        cases = [c for c in load_cases("recall") if c.world == world_id and c.query and c.include]
        if not cases:
            continue
        world = worlds(world_id)
        app, caller, keys = await world_app(world)
        domains = {m.key: m.domain for m in world.memories}
        probe = Probe(app, caller, keys)
        await probe.prepare()
        for case in cases:
            raw = await probe.rows(case, None)
            pool = [Row.from_list(r) for r in raw or [] if r[5]]
            if not pool:
                continue
            relevant, _, negative = labels(case, [r.key for r in pool], domains)
            plain = fused(pool, 0.2, "mean")
            feats = features(pool)
            shown = sorted(plain, key=lambda k: (-plain[k], k))[:SHOWN]
            obs = [
                Observation(case.id, k, "actor:sim", feats[k], plain[k], float(k in relevant), 1.0)
                for k in shown
                if k in relevant or k in negative
            ]
            if obs:
                judged.append(Judged(case.id, case.split, obs))
    tune = sorted((j for j in judged if j.split == "tune"), key=lambda j: j.case)
    hold = [j for j in judged if j.split == "holdout"]
    assert tune and hold
    plain_hold = accuracy(hold, None)

    lines = [
        f"\nlearning curve [{profile.name}]: {len(tune)} tune / {len(hold)} holdout recalls, "
        f"plain holdout accuracy {plain_hold:.3f}",
        f"{'feedback recalls':>17} {'poisoned':>9} {'status':>9} {'layer':>7} {'change':>7}",
    ]
    clean_final = 0.0
    for share in (0.0, 0.2, 0.5, 1.0):
        for n in sorted({5, 10, 20, 40, len(tune)}):
            if n > len(tune):
                continue
            state, layer = build([], poisoned(tune[:n], share), CFG)
            after = accuracy(hold, layer if layer.active else None)
            if share == 0.0 and n == len(tune):
                clean_final = after
            lines.append(
                f"{n:>17} {share:>9.0%} {state.status:>9} {after:>7.3f} {after - plain_hold:>+7.3f}"
            )
    with capsys.disabled():
        print("\n".join(lines))
    assert clean_final >= plain_hold - 0.02  # clean feedback must not make ranking worse
