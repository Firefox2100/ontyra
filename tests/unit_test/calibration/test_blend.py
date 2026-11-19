"""Blending an outside model's probability into the fused score, and the duplicate `confirm`."""

from __future__ import annotations

from ontyra.calibration import blend, duplicates, runner, search
from ontyra.calibration.ranking import Row


def row(key: str, cos: float) -> Row:
    return Row(key, None, cos, None, 0.0, True, None, None)


def request(i: int, split: str = "tune") -> search.Request:
    # the cosine ranks the wrong memory first; the model knows better
    pool = [row("wrong", 0.80), row("right", 0.78), row("far", 0.2)]
    return search.Request(
        id=f"r{i}",
        split=split,
        silent=False,
        pool=pool,
        relevant={"right"},
        negative={"wrong", "far"},
        best_cos=0.8,
    )


def test_a_model_that_knows_better_earns_a_weight() -> None:
    reqs = [request(i, "tune" if i % 2 else "holdout") for i in range(24)]
    signals = {r.id: {"right": 0.95, "wrong": 0.05, "far": 0.0} for r in reqs}
    s = search.Setting(0.0, "min", 0.1, 0.0)
    found = blend.suggest(reqs, s, signals, current=(0.5, 0.15))
    assert found is not None
    assert found.without["tune"][0] == 0  # the plain ranking is wrong everywhere
    assert found.tune[0] == found.tune[1] and found.holdout[0] == found.holdout[1]
    assert found.weight >= 0.5


def test_too_few_requests_give_no_suggestion() -> None:
    reqs = [request(i) for i in range(5)]
    signals = {r.id: {"right": 1.0} for r in reqs}
    assert (
        blend.suggest(reqs, search.Setting(0.0, "min", 0.1, 0.0), signals, current=(0.5, 0.1))
        is None
    )


def test_the_confirm_threshold_is_the_lowest_with_enough_precision() -> None:
    dup = [0.9, 0.95, 0.97, 0.99]
    rest = [0.1, 0.2, 0.5, 0.6]
    threshold, recall = duplicates.precision_threshold(dup, rest) or (None, None)
    assert threshold is not None and 0.6 < threshold <= 0.9 and recall == 1.0
    assert duplicates.precision_threshold([0.5], [0.5, 0.5, 0.9]) is None


def test_the_estimate_counts_model_requests_only_when_they_are_configured() -> None:
    from ontyra.config import Settings

    plain = runner.estimate(Settings(_env_file=None))  # type: ignore[call-arg]
    assert plain["decision_requests"] == 0 and plain["rerank_requests"] == 0
    both = runner.estimate(
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            decision={"provider": "systemone", "url": "http://x", "model": "m"},
            rerank={"provider": "http", "url": "http://x", "model": "m"},
        )
    )
    assert both["decision_requests"] > both["recall_requests"] and both["rerank_requests"] > 0
