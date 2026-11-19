"""The calibration search and duplicate thresholds, on synthetic recorded rows (no models)."""

from __future__ import annotations

from ontyra.calibration import duplicates, search
from ontyra.calibration.models import load_cases, load_world
from ontyra.calibration.ranking import Row


def row(key: str, cos: float, bm25: float | None = None, cov: float = 0.0) -> Row:
    return Row(key, bm25, cos, None, cov, True, None, None)


def request(
    pool: list[Row], relevant: set[str], *, silent: bool = False, split: str = "tune"
) -> search.Request:
    cosines = [r.cos for r in pool if r.cos is not None]
    return search.Request(
        id="r",
        split=split,
        silent=silent,
        pool=pool,
        relevant=relevant,
        negative={r.key for r in pool} - relevant,
        best_cos=max(cosines) if cosines else None,
    )


def test_margin_keeps_a_prefix_of_the_fused_order() -> None:
    req = request([row("a", 0.9), row("b", 0.8), row("c", 0.3)], {"a"})
    keep_close = search.Setting(0.0, "min", 0.2, 0.0)
    assert not search.passes(req, keep_close)  # b is within 0.2 of the best: a negative is kept
    assert search.passes(req, search.Setting(0.0, "min", 0.05, 0.0))
    assert search.passes(request(req.pool, {"a", "b"}), search.Setting(0.0, "min", 0.2, 0.0))


def test_abstention_is_right_only_for_silent_requests() -> None:
    quiet = request([row("a", 0.3), row("b", 0.2)], set(), silent=True)
    asked = request([row("a", 0.3), row("b", 0.2)], {"a"})
    assert search.passes(quiet, search.Setting(0.0, "min", 0.1, 0.5))
    assert not search.passes(asked, search.Setting(0.0, "min", 0.1, 0.5))
    assert not search.passes(quiet, search.Setting(0.0, "min", 0.1, 0.0))  # answered anyway


def test_suggestion_recovers_the_scale_of_the_model() -> None:
    # relevant memories sit at cosine ~0.7 and irrelevant ones ~0.3, silent requests top out at 0.4
    reqs = []
    for _ in range(12):
        reqs.append(request([row("hit", 0.7), row("x", 0.3), row("y", 0.28)], {"hit"}))
        reqs.append(request([row("x", 0.4), row("y", 0.3)], set(), silent=True))
    s = search.suggest(reqs)
    assert s.tune == (24, 24)
    assert 0.4 < s.setting.tau <= 0.7  # between the silent ceiling and the relevant level
    assert search.separation(reqs)["relevant_vs_irrelevant_auc"] == 1.0


def test_recorded_rows_become_labelled_requests_from_the_bundled_cases() -> None:
    world = load_world("commons")
    domains = {m.key: m.domain for m in world.memories}
    case = next(c for c in load_cases("recall") if c.mode == "recall" and c.include and c.query)
    keys = [m.key for m in world.memories[:20]] + case.include
    rows = {case.id: [[k, 1.0, 0.5, None, 0.5, 1, 0.5, 0.5] for k in keys]}
    (req,) = search.requests(rows, load_cases("recall"), domains)
    assert req.relevant == set(case.include) and req.split == case.split


def test_duplicate_thresholds_follow_the_cosines() -> None:
    pairs = [
        duplicates.Pair(id=f"d{i}", a=f"a{i}", b=f"b{i}", label="dup", source="x")
        for i in range(20)
    ] + [
        duplicates.Pair(id=f"n{i}", a=f"c{i}", b=f"d{i}", label="distinct", source="x")
        for i in range(20)
    ]
    cosines = [0.80 + i * 0.005 for i in range(20)] + [0.50 + i * 0.01 for i in range(20)]
    out = duplicates.suggest(pairs, cosines)
    assert 0.69 <= out["duplicate_cosine"] <= 0.80  # at or below the lowest duplicate
    assert out["candidate_cosine"] <= 0.81
    assert out["recall_at_duplicate_cosine"] == 1.0
    assert duplicates.cosine([1.0, 0.0], [1.0, 0.0]) == 1.0


def test_bundled_pairs_are_labelled_and_distinct() -> None:
    pairs = duplicates.load_pairs()
    assert {p.label for p in pairs} == {"dup", "update", "distinct"}
    assert all(p.a != p.b for p in pairs)


def test_calibrate_command_lists_the_cost_and_refuses_a_paid_model_without_yes(
    monkeypatch, capsys
) -> None:
    from ontyra.cli import main

    monkeypatch.setenv("ONTYRA_EMBEDDING__PROVIDER", "langchain")
    monkeypatch.setenv("ONTYRA_EMBEDDING__CLASS_PATH", "langchain_openai.OpenAIEmbeddings")
    monkeypatch.setenv("ONTYRA_EMBEDDING__DIMENSION", "8")
    assert main(["calibrate"]) == 2
    captured = capsys.readouterr()
    assert "will embed up to" in captured.out and "--yes" in captured.err
