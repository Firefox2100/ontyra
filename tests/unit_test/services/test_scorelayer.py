"""The score layer's pure core: priors, source caps, the bounded fit, the canary, replay."""

from __future__ import annotations

import random

from ontyra.models.feedback import LearningSettings
from ontyra.services.scorelayer import (
    FEATURES,
    Observation,
    Verdict,
    build,
    capped,
    fit,
    is_canary,
    pairwise,
    priors,
)

CFG = LearningSettings(
    enabled=True, min_examples=20, canary_min_groups=4, canary_fraction=0.3, max_shift=1.0
)


def group_ids(canary: bool, n: int) -> list[str]:
    out: list[str] = []
    i = 0
    while len(out) < n:
        g = f"recall_trace:{i}"
        if is_canary(g, CFG.canary_fraction) == canary:
            out.append(g)
        i += 1
    return out


def world(
    groups: list[str], *, trust_dense: bool = True, source: str = "actor:a"
) -> list[Observation]:
    """Each recall has a relevant and an irrelevant candidate. The plain score is a coin flip
    between them; the dense feature tells them apart (or, with `trust_dense=False`, misleads)."""
    rng = random.Random(7)
    out: list[Observation] = []
    for g in groups:
        for rel in (1.0, 0.0):
            dense = (0.9 if rel else 0.1) if trust_dense else (0.1 if rel else 0.9)
            base = 0.4 + 0.1 * rng.random()
            feats = tuple(dense if n == "dense" else 0.0 for n in FEATURES)
            out.append(Observation(g, f"memory:{g}:{rel}", source, feats, base, rel, 1.0))
    return out


def test_it_is_off_until_enabled() -> None:
    state, layer = build([], world(group_ids(False, 20)), LearningSettings())
    assert state.status == "off" and not layer.active


def test_it_waits_for_enough_examples_and_canary_recalls() -> None:
    few = world(group_ids(False, 3))
    state, layer = build([], few, CFG)
    assert state.status == "learning" and not layer.active and "judged" in state.reason
    train_only = world(group_ids(False, 20))
    state, _ = build([], train_only, CFG)
    assert state.status == "learning" and "canary" in state.reason


def test_a_useful_signal_is_learned_and_the_canary_confirms_it() -> None:
    obs = world(group_ids(False, 30)) + world(group_ids(True, 8))
    state, layer = build([], obs, CFG)
    assert state.status == "active" and layer.active
    assert state.canary_layer is not None and state.canary_base is not None
    assert state.canary_layer > state.canary_base
    assert state.coefficients["dense"] > 0
    assert all(abs(state.coefficients[n]) <= CFG.max_shift for n in FEATURES)
    hit = layer.score("memory:x", (0, 0.9, 0, 0, 0), 0.45)
    miss = layer.score("memory:x", (0, 0.1, 0, 0, 0), 0.45)
    assert hit > miss


def test_misleading_feedback_fails_the_canary_and_the_layer_stays_off() -> None:
    wide = CFG.model_copy(update={"max_shift": 6.0, "l2": 0.0})
    obs = world(group_ids(False, 40), trust_dense=False) + world(group_ids(True, 8))
    state, layer = build([], obs, wide)
    assert state.status == "reverted" and not layer.active
    assert layer.score("memory:x", (0, 0.9, 0, 0, 0), 0.45) == 0.45  # the plain score stands


def test_one_sources_share_of_the_fit_is_capped() -> None:
    mine = world(group_ids(False, 5), source="actor:honest")
    flood = world(group_ids(False, 40), source="actor:flood")
    flood = [o for o in flood if o.group not in {m.group for m in mine}]
    weights = capped([*mine, *flood], 0.5)
    by_source: dict[str, float] = {}
    for o, w in zip([*mine, *flood], weights, strict=True):
        by_source[o.source] = by_source.get(o.source, 0.0) + w
    assert by_source["actor:flood"] <= 0.5 * sum(by_source.values()) * (1 + 1e-6)
    alone = capped(flood, 0.5)
    assert alone == [o.weight for o in flood]  # a single source is not diluted by itself


def test_the_fit_does_not_depend_on_the_order_of_the_feedback() -> None:
    obs = world(group_ids(False, 25))
    shuffled = list(obs)
    random.Random(1).shuffle(shuffled)
    assert fit(obs, CFG) == fit(shuffled, CFG)


def test_priors_follow_net_verdicts_decay_and_stay_capped() -> None:
    good = [Verdict("memory:a", 1.0, 1.0, 0.0, "s") for _ in range(50)]
    assert priors(good, CFG)["memory:a"] <= CFG.max_prior
    mixed = [Verdict("memory:b", 1.0, 1.0, 0.0, "s"), Verdict("memory:b", 0.0, 1.0, 0.0, "t")]
    assert "memory:b" not in priors(mixed, CFG)  # they cancel
    fresh = priors([Verdict("memory:c", 0.0, 1.0, 0.0, "s")], CFG)["memory:c"]
    old = priors([Verdict("memory:c", 0.0, 1.0, 900.0, "s")], CFG)["memory:c"]
    assert fresh < old < 0  # an old verdict counts for less


def test_canary_recalls_are_never_trained_on_or_counted_in_priors() -> None:
    canary = group_ids(True, 1)[0]
    verdicts = [Verdict("memory:z", 0.0, 1.0, 0.0, "s", canary)]
    state, _ = build(verdicts, [], CFG)
    assert state.priors == 0
    assert is_canary(canary, CFG.canary_fraction) and not is_canary(canary, 0.0)


def test_removing_an_example_is_a_replay_without_it() -> None:
    obs = world(group_ids(False, 30)) + world(group_ids(True, 8))
    with_all, _ = build([], obs, CFG)
    poisoned = [o for o in obs if o.group != group_ids(False, 1)[0]]
    without, _ = build([], poisoned, CFG)
    assert with_all.examples == without.examples + 2


def test_pairwise_scores_orderings_and_ties() -> None:
    assert pairwise({"g": [(0.9, 1.0), (0.1, 0.0)]}) == (1.0, 1)
    assert pairwise({"g": [(0.1, 1.0), (0.9, 0.0)]}) == (0.0, 1)
    assert pairwise({"g": [(0.5, 1.0), (0.5, 0.0)]}) == (0.5, 1)
    assert pairwise({"g": [(0.5, 1.0)]}) == (0.0, 0)  # one class only: not judged
