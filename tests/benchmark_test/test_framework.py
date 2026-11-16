"""The benchmark framework itself: scoring, result files, configuration, data validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmark_test.framework.config import BenchSettings, Profile
from benchmark_test.framework.models import Case, MemorySpec, Step, World, memory_fields
from benchmark_test.framework.results import summarise, write_result
from benchmark_test.framework.score import Cell, score_step
from benchmark_test.framework.validate import case_problems, world_problems
from ontyra.models.recall import Caution, RecallItem, RecallPack

KEYS = {"memory:a": "a", "memory:b": "b", "memory:c": "c"}


def item(mem: str, score: float, **kw: object) -> RecallItem:
    return RecallItem(
        memory=mem,  # type: ignore[arg-type]
        revision=f"fact_rev:{mem[-1]}",  # type: ignore[arg-type]
        kind="fact",
        text=mem,
        score=score,
        relevance=score,
        **kw,  # type: ignore[arg-type]
    )


def pack(*items: RecallItem, cautions: int = 0) -> RecallPack:
    return RecallPack(
        path="recall",
        applicable=list(items),
        cautions=[Caution(message="x") for _ in range(cautions)],
        silent=not items,
    )


def test_a_step_passes_when_it_returns_exactly_what_it_expects() -> None:
    step = Step(query="q", include=["a"])
    assert score_step(step, pack(item("memory:a", 0.9)), KEYS).passed


def test_each_kind_of_deviation_is_a_distinct_problem() -> None:
    step = Step(query="q", include=["a"], exclude=["b"], allow=["c"])
    scored = score_step(step, pack(item("memory:b", 0.5)), KEYS)
    assert scored.missing == ["a"] and scored.leaked == ["b"] and scored.extra == []
    assert not scored.passed
    extra = score_step(
        Step(query="q", include=["a"]), pack(item("memory:a", 1), item("memory:c", 0.1)), KEYS
    )
    assert extra.extra == ["c"] and extra.tp == 1


def test_allowed_items_are_not_false_positives() -> None:
    step = Step(query="q", include=["a"], allow=["c"])
    assert score_step(step, pack(item("memory:a", 1), item("memory:c", 0.1)), KEYS).passed


def test_rank_follows_score_not_section_order() -> None:
    step = Step(query="q", include=["a"], top=1)
    low_first = pack(item("memory:b", 0.2), item("memory:a", 0.9))
    scored = score_step(step, low_first, KEYS)
    assert [r.key for r in scored.returned] == ["a", "b"]
    assert "ranked below" in " ".join(scored.problems) is False or scored.ranked_low == []


def test_silence_and_cautions_and_verdict() -> None:
    assert score_step(Step(query="q", silent=True), pack(), KEYS).passed
    assert not score_step(Step(query="q", silent=True), pack(item("memory:a", 1)), KEYS).passed
    need = Step(query="q", include=["a"], min_cautions=1)
    assert not score_step(need, pack(item("memory:a", 1)), KEYS).passed
    assert score_step(need, pack(item("memory:a", 1), cautions=1), KEYS).passed
    verdict = Step(query="q", mode="check", verdict="caution")
    assert not score_step(verdict, pack(), KEYS, verdict="clear").passed


def test_checks_and_caveats_are_matched_by_substring() -> None:
    step = Step(query="q", include=["a"], checks={"a": ["conda"]}, caveats={"a": ["unknown"]})
    good = item("memory:a", 1, checks=["verify prerequisite: conda"], caveats=["os unknown"])
    assert score_step(step, pack(good), KEYS).passed
    assert not score_step(step, pack(item("memory:a", 1)), KEYS).passed


def test_cells_count_passes_and_item_level_totals() -> None:
    ok = score_step(Step(query="q", include=["a"]), pack(item("memory:a", 1)), KEYS)
    bad = score_step(Step(query="q", include=["a"]), pack(), KEYS)
    cell = Cell()
    cell.add([ok])
    cell.add([bad])
    assert (cell.cases, cell.passed, cell.tp, cell.fn) == (2, 1, 1, 1)
    assert cell.recall == pytest.approx(1 / 2)
    assert Cell().precision is None  # nothing to judge is not a perfect score


def case(**kw: object) -> Case:
    base = {"id": "c1", "world": "w", "tags": ["t"], "why": "because", "query": "q"}
    return Case.model_validate({**base, **kw})


def test_results_are_immutable_files(tmp_path: Path) -> None:
    scored = score_step(Step(query="q", include=["a"]), pack(item("memory:a", 1)), KEYS)
    outcomes = [(case(include=["a"]), [scored])]
    path = write_result("recall", "run1", {"embedding": {}}, outcomes, tmp_path)
    assert path.exists() and "run1" in path.name
    with pytest.raises(FileExistsError):
        write_result("recall", "run1", {}, outcomes, tmp_path)
    assert summarise(outcomes)["by_tag"]["t"]["passed"] == 1


def test_the_default_profile_is_the_asserting_hash_baseline() -> None:
    (profile,) = BenchSettings().profile_list()
    assert profile.embedding().provider == "hash" and profile.baseline


def test_env_variables_make_one_openai_compatible_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BENCH_EMBED_URL", "http://localhost:8080/v1")
    monkeypatch.setenv("BENCH_EMBED_MODEL", "qwen3-embedding-4b")
    monkeypatch.setenv("BENCH_EMBED_DIM", "2560")
    monkeypatch.setenv("BENCH_EMBED_QUERY_PREFIX", "Instruct: find the memory\nQuery:")
    (profile,) = BenchSettings().profile_list()
    e = profile.embedding()
    assert e.provider == "langchain" and e.dimension == 2560 and e.name == "qwen3-embedding-4b"
    assert e.kwargs["base_url"] == "http://localhost:8080/v1"
    assert e.kwargs["check_embedding_ctx_length"] is False
    assert e.query_prefix.startswith("Instruct:")
    assert not profile.baseline  # a real model is measured, not asserted


def test_a_model_without_a_dimension_is_refused() -> None:
    with pytest.raises(ValueError, match="model` and `dim"):
        Profile(name="x", url="http://localhost:8080/v1", model="m").embedding()


def test_profiles_come_from_a_file_and_sweeps_expand(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "profiles.json"
    path.write_text(
        json.dumps(
            [
                {"name": "hash"},
                {
                    "name": "m",
                    "model": "m",
                    "dim": 8,
                    "url": "http://x/v1",
                    "sweep": {"vector_floor": [0.3, 0.5], "relevance_threshold": [0.2]},
                },
            ]
        )
    )
    monkeypatch.setenv("BENCH_PROFILES", str(path))
    profiles = BenchSettings().profile_list()
    assert [p.name for p in profiles] == [
        "hash",
        "m.relevance_threshold-0.2.vector_floor-0.3",
        "m.relevance_threshold-0.2.vector_floor-0.5",
    ]
    last = profiles[-1].recall_settings()
    assert last.vector_floor == 0.5 and last.relevance_threshold == 0.2
    assert profiles[0].baseline and not profiles[1].baseline


def test_result_labels_name_the_profile() -> None:
    settings = BenchSettings(label="trial")
    assert settings.run_label(Profile(name="a b"), "stamp") == "trial-a_b"


def world(**kw: object) -> World:
    base: dict[str, object] = {
        "id": "w",
        "description": "d",
        "concepts": [{"label": "Linux"}, {"label": "Ubuntu", "aliases": ["u"]}],
        "memories": [
            {"key": "a", "kind": "fact", "text": "t", "fields": {"about": ["concept:linux"]}},
            {"key": "b", "kind": "fact", "text": "t", "supersedes": "a"},
        ],
    }
    return World.model_validate({**base, **kw})


def test_a_valid_world_has_no_problems() -> None:
    assert world_problems(world()) == []


def test_world_problems_name_the_defect() -> None:
    memories = [
        {"key": "a", "kind": "fact", "text": "t", "fields": {"about": ["concept:nope"]}},
        {"key": "a", "kind": "fact", "text": "t", "supersedes": "later"},
    ]
    problems = " | ".join(world_problems(world(memories=memories)))
    assert "unknown concept 'concept:nope'" in problems
    assert "duplicate memory key a" in problems
    assert "'later', which is not an earlier memory" in problems


def test_case_problems_name_the_defect() -> None:
    w = world()
    bad = [
        case(id="x", include=["zzz"]),
        case(id="y", include=["a"], exclude=["a"]),
        case(id="z", world="other"),
        case(id="s", silent=True, include=["a"]),
        case(id="x", include=["a"]),
    ]
    problems = " | ".join(case_problems(bad, {"w": w}))
    assert "unknown memory key 'zzz'" in problems
    assert "both included and excluded" in problems
    assert "unknown world 'other'" in problems
    assert "silent but includes keys" in problems
    assert "duplicate case id x" in problems


def test_same_domain_extras_are_tolerated_unless_the_step_is_strict() -> None:
    domains = {"a": "d1", "b": "d1", "c": "d2"}
    step = Step(query="q", include=["a"])
    scored = score_step(
        step, pack(item("memory:a", 1), item("memory:b", 0.5)), KEYS, domains=domains
    )
    assert scored.tolerated == ["b"] and scored.extra == [] and scored.passed
    leak = score_step(step, pack(item("memory:a", 1), item("memory:c", 0.5)), KEYS, domains=domains)
    assert leak.extra == ["c"] and not leak.passed
    strict = Step(query="q", include=["a"], strict=True)
    both = pack(item("memory:a", 1), item("memory:b", 0.5))
    assert not score_step(strict, both, KEYS, domains=domains).passed
    silent = Step(query="q", silent=True)
    assert not score_step(silent, pack(item("memory:b", 0.5)), KEYS, domains=domains).passed


def test_allow_prefix_allows_a_whole_family_of_keys() -> None:
    keys = {"memory:a": "fi-a", "memory:b": "fi-b", "memory:c": "ad-c"}
    step = Step(query="q", include=["fi-a"], allow_prefix=["fi-"])
    assert score_step(step, pack(item("memory:a", 1), item("memory:b", 0.5)), keys).passed
    assert not score_step(step, pack(item("memory:a", 1), item("memory:c", 0.5)), keys).passed


def test_memory_shorthands_expand_to_the_kinds_own_fields() -> None:
    mem = MemorySpec.model_validate(
        {
            "key": "m",
            "kind": "directive",
            "text": "Limit workers.",
            "about": ["pytest"],
            "at": ["file:///x", "=file:///y"],
            "q": ["necessary:ram < 16 GB", "necessary:python >= 3.11", "exclusion:nvidia_gpu"],
        }
    )
    fields = memory_fields(mem, {"ram": "number", "python": "version"})
    assert fields["about"] == ["concept:pytest"]
    assert fields["anchors"] == [{"uri": "file:///x"}, {"uri": "file:///y", "match": "exact"}]
    assert fields["qualifiers"][0] == {
        "concept": "concept:ram",
        "role": "necessary",
        "op": "<",
        "value": 16,
        "unit": "GB",
    }
    assert fields["qualifiers"][1]["value"] == "3.11"  # a version stays a string
    assert fields["qualifiers"][2] == {"concept": "concept:nvidia_gpu", "role": "exclusion"}
    assert fields["strength"] == "should" and fields["rule"] == "Limit workers."


def test_shorthand_and_explicit_fields_must_not_clash() -> None:
    mem = MemorySpec.model_validate(
        {
            "key": "m",
            "kind": "fact",
            "text": "t",
            "about": ["x"],
            "fields": {"about": ["concept:y"]},
        }
    )
    with pytest.raises(ValueError, match="both as shorthand"):
        memory_fields(mem, {})
    bad = MemorySpec.model_validate({"key": "m", "kind": "fact", "text": "t", "q": ["maybe:linux"]})
    with pytest.raises(ValueError, match="bad qualifier"):
        memory_fields(bad, {})


def test_a_paraphrase_may_not_reuse_the_memorys_words() -> None:
    w = world(
        memories=[
            {"key": "a", "kind": "directive", "text": "Wash the dishes every evening."},
        ]
    )
    reuse = case(id="p", tags=["paraphrase"], query="wash dishes", include=["a"])
    fresh = case(
        id="q", tags=["paraphrase"], query="keep the kitchen clean at night", include=["a"]
    )
    assert "must not reuse" in " ".join(case_problems([reuse], {"w": w}))
    assert case_problems([fresh], {"w": w}) == []


def test_the_big_world_loads_from_its_directory() -> None:
    from benchmark_test.framework.models import load_world, world_ids

    assert "commons" in world_ids()
    big = load_world("commons")
    assert len(big.memories) > 300 and {m.domain.split("-")[0] for m in big.memories} >= {
        "dev",
        "office",
        "research",
        "daily",
    }


def test_rank_lane_is_pessimistic_about_ties_and_ranks_unretrieved_last() -> None:
    from benchmark_test.framework.ranking import rank_lane

    values: dict[str, float | None] = {"a": 0.5, "b": 0.9, "c": 0.5, "d": None}
    # relevant a; negatives b (above), c (tied), d (not retrieved): a is third
    assert rank_lane(values, {"a"}, {"b", "c", "d"}) == {"a": 3}
    assert rank_lane({"a": None, "b": None}, {"a"}, {"b"}) == {"a": 2}  # nothing retrieved


def test_metrics_follow_the_ranks_of_the_relevant_memories() -> None:
    from benchmark_test.framework.ranking import metrics_for

    perfect = metrics_for({"a": 1, "b": 2}, n_negative=10)
    assert perfect["r@1"] == 0.5 and perfect["r@3"] == 1.0 and perfect["auc"] == 1.0
    assert perfect["mrr"] == 1.0 and perfect["ndcg@10"] == pytest.approx(1.0)
    worst = metrics_for({"a": 11}, n_negative=10)
    assert worst["r@10"] == 0.0 and worst["auc"] == 0.0 and worst["mrr"] == pytest.approx(1 / 11)


def test_step_metrics_skip_memories_that_do_not_apply_and_treat_same_domain_as_neutral() -> None:
    from benchmark_test.framework.ranking import Row, step_metrics

    def row(key: str, bm25: float, applies: bool = True) -> Row:
        return Row(key, bm25, None, None, 1.0, applies, None, None)

    rows = [row("a", 1.0), row("near", 5.0), row("far", 3.0), row("gone", 9.0, applies=False)]
    domains = {"a": "d1", "near": "d1", "far": "d2", "gone": "d2"}
    step = Step(query="q", include=["a"])
    got = step_metrics(rows, step, domains)
    # `gone` does not apply (dropped); `near` shares a's domain (neutral); only `far` outranks a
    assert got["bm25"]["mrr"] == pytest.approx(0.5)
    strict = step_metrics(rows, Step(query="q", include=["a"], strict=True), domains)
    assert strict["bm25"]["mrr"] == pytest.approx(1 / 3)
    assert step_metrics(rows, Step(query="q", silent=True), domains) == {}


def test_fusion_methods_combine_lanes_per_request() -> None:
    from benchmark_test.framework.fusion import linear, rrf
    from benchmark_test.framework.ranking import Row

    rows = [
        Row("lexical-only", 10.0, None, None, 1.0, True, None, None),
        Row("vector-only", None, 0.9, None, 1.0, True, None, None),
        Row("both", 8.0, 0.8, None, 1.0, True, None, None),
    ]
    fused = rrf("lexical", "cosine")(rows)
    assert max(fused, key=lambda k: fused[k] or 0) == "both"  # agreement beats a single high rank
    lex = linear(1.0)(rows)
    assert lex["lexical-only"] == 1.0 and lex["vector-only"] == 0.0
    assert linear(0.0)(rows)["vector-only"] == 1.0


def test_lane_scores_are_stored_in_a_gzipped_sidecar(tmp_path: Path) -> None:
    from benchmark_test.framework.results import read_rows

    rows = [["a", 1.5, 0.4, None, 0.5, 1, 0.3, 0.2]]
    scored = score_step(Step(query="q", include=["a"]), pack(item("memory:a", 1)), KEYS)
    scored.rows = rows
    path = write_result("recall", "r", {}, [(case(include=["a"]), [scored])], tmp_path)
    assert read_rows(path) == {("c1", 0): rows}
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["columns"][0] == "key" and "retrieval" in body


def test_a_universal_memory_is_never_a_false_positive() -> None:
    step = Step(query="q", include=["a"])
    extra = pack(item("memory:a", 1), item("memory:c", 0.5))
    assert not score_step(step, extra, KEYS).passed
    assert score_step(step, extra, KEYS, universal=frozenset({"c"})).passed
    assert not score_step(
        step, pack(item("memory:c", 0.5)), KEYS, universal=frozenset({"c"})
    ).passed
