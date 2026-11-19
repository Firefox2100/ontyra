"""The threshold-free ranking metrics: lane extraction, labels, pessimistic ranks, aggregates.

These are pure functions over recorded scores, so the tests pin the semantics that the search
(and the reported numbers) depend on: a tie against a negative never helps, a lane that did not
retrieve a memory ranks it last, and labels follow the same relevance rules as the pass/fail gate.
"""

from __future__ import annotations

import math

import pytest

from ontyra.calibration.models import Step
from ontyra.calibration.ranking import (
    Row,
    labels,
    lane_value,
    mean_metrics,
    metrics_for,
    rank_lane,
    step_metrics,
)


def row(
    key: str,
    *,
    bm25: float | None = 1.0,
    cos: float | None = None,
    concept: float | None = None,
    cov: float = 1.0,
    applies: bool = True,
    relevance: float | None = None,
    score: float | None = None,
    fused: float | None = None,
    rerank: float | None = None,
    final: float | None = None,
) -> Row:
    return Row(key, bm25, cos, concept, cov, applies, relevance, score, fused, rerank, final)


class TestRowFromList:
    def test_eight_columns_have_no_extra_lanes(self) -> None:
        parsed = Row.from_list(["a", 1.0, 0.5, 0.25, 0.75, 1, 0.8, 0.6])
        assert parsed == Row("a", 1.0, 0.5, 0.25, 0.75, True, 0.8, 0.6)

    def test_extra_columns_are_fused_rerank_final(self) -> None:
        parsed = Row.from_list(["a", 1.0, 0.5, 0.25, 0.75, 0, None, None, 0.9, 0.4, 0.3])
        assert (parsed.fused, parsed.rerank, parsed.final) == (0.9, 0.4, 0.3)
        assert parsed.applies is False

    def test_a_missing_extra_column_stays_none(self) -> None:
        parsed = Row.from_list(["a", 1.0, None, None, 0.0, 1, None, None, 0.9])
        assert (parsed.fused, parsed.rerank, parsed.final) == (0.9, None, None)


class TestLaneValue:
    def test_bm25_and_cosine_are_raw(self) -> None:
        asserted = row("a", bm25=3.0, cos=0.2)
        assert lane_value(asserted, "bm25", 10.0) == 3.0
        assert lane_value(asserted, "cosine", 10.0) == 0.2

    def test_concept_weight_is_raw_and_keeps_a_zero(self) -> None:
        assert lane_value(row("a", concept=0.4), "concept", 10.0) == 0.4
        assert lane_value(row("a", concept=0.0), "concept", 10.0) == 0.0

    def test_lexical_is_relative_bm25_times_coverage(self) -> None:
        assert lane_value(row("a", bm25=5.0, cov=0.5), "lexical", 10.0) == pytest.approx(0.25)

    def test_lexical_is_none_without_bm25_or_a_positive_top(self) -> None:
        assert lane_value(row("a", bm25=None), "lexical", 10.0) is None
        assert lane_value(row("a", bm25=5.0), "lexical", 0.0) is None

    def test_zero_relevance_and_score_mean_not_retrieved(self) -> None:
        assert lane_value(row("a", relevance=0.0), "relevance", 1.0) is None
        assert lane_value(row("a", score=0.0), "score", 1.0) is None

    def test_fused_lanes_read_their_own_field(self) -> None:
        asserted = row("a", fused=0.9, rerank=0.4, final=0.3)
        assert lane_value(asserted, "fused", 1.0) == 0.9
        assert lane_value(asserted, "rerank", 1.0) == 0.4
        assert lane_value(asserted, "final", 1.0) == 0.3

    def test_unknown_lane_is_a_programming_error(self) -> None:
        with pytest.raises(ValueError, match="nope"):
            lane_value(row("a"), "nope", 1.0)


class TestLabels:
    KEYS = ("a", "b", "c", "rule-1", "other")
    DOMAINS = {"a": "d1", "b": "d1", "c": "d2", "rule-1": "d3", "other": "d4"}

    def test_same_domain_neighbours_are_neutral_unless_strict(self) -> None:
        step = Step(include=["a"])
        relevant, neutral, negative = labels(step, self.KEYS, self.DOMAINS)
        assert relevant == {"a"}
        assert neutral == {"b"}  # same domain as a relevant memory
        assert negative == {"c", "rule-1", "other"}

    def test_strict_makes_unlisted_same_domain_memories_negative(self) -> None:
        _, neutral, negative = labels(Step(include=["a"], strict=True), self.KEYS, self.DOMAINS)
        assert neutral == set()
        assert negative == {"b", "c", "rule-1", "other"}

    def test_allow_prefixes_and_excludes(self) -> None:
        step = Step(include=["a"], allow=["b"], allow_prefix=["rule-"], exclude=["c"])
        relevant, neutral, negative = labels(step, self.KEYS, self.DOMAINS)
        assert relevant == {"a"}
        assert neutral == {"b", "rule-1"}
        assert negative == {"c", "other"}  # excluded is never neutral

    def test_allow_is_removed_when_it_is_relevant_or_excluded(self) -> None:
        step = Step(include=["a"], allow=["a", "b"], exclude=["b"])
        _, neutral, negative = labels(step, ("a", "b"), {})
        assert neutral == set()
        assert negative == {"b"}

    def test_a_memory_without_a_domain_is_plain_negative(self) -> None:
        _, neutral, _ = labels(Step(include=["a"]), ("a", "x"), {"a": None})  # type: ignore[dict-item]
        assert neutral == set()


class TestRankLane:
    def test_a_negative_above_the_relevant_memory_counts(self) -> None:
        ranks = rank_lane({"a": 1.0, "n": 5.0}, {"a"}, {"n"})
        assert ranks == {"a": 2}

    def test_a_tie_with_a_negative_counts_against_the_memory(self) -> None:
        assert rank_lane({"a": 5.0, "n": 5.0}, {"a"}, {"n"}) == {"a": 2}

    def test_a_missing_memory_ranks_after_everything_retrieved(self) -> None:
        ranks = rank_lane({"a": None, "n": 5.0}, {"a"}, {"n"})
        assert ranks == {"a": 2}

    def test_negatives_are_never_ranked(self) -> None:
        assert rank_lane({"a": 1.0, "n": 2.0}, {"a"}, {"n"}) == {"a": 2}


class TestMetricsFor:
    def test_a_perfect_rank(self) -> None:
        out = metrics_for({"a": 1}, 0)
        assert out["r@1"] == 1.0
        assert out["mrr"] == 1.0
        assert out["ndcg@10"] == 1.0
        assert out["auc"] == 1.0  # no negatives: nothing can be above it

    def test_recall_at_k_mrr_and_auc_of_a_second_place(self) -> None:
        out = metrics_for({"a": 2}, 1)
        assert out["r@1"] == 0.0
        assert out["r@3"] == 1.0
        assert out["mrr"] == 0.5
        assert out["auc"] == 0.0  # the only negative is above it
        assert out["ndcg@10"] == pytest.approx(1 / math.log2(3))

    def test_ndcg_uses_the_ideal_ordering_of_the_relevant_memories(self) -> None:
        out = metrics_for({"a": 1, "b": 3}, 2)
        dcg = 1.0 / math.log2(2) + 1.0 / math.log2(4)
        ideal = 1.0 / math.log2(2) + 1.0 / math.log2(3)
        assert out["ndcg@10"] == pytest.approx(dcg / ideal)

    def test_auc_averages_over_every_relevant_negative_pair(self) -> None:
        # two relevant memories at ranks 1 and 2, one negative: no negative above either
        assert metrics_for({"a": 1, "b": 2}, 1)["auc"] == 1.0


class TestStepMetrics:
    def test_no_expected_memory_means_no_metrics(self) -> None:
        assert step_metrics([row("a")], Step(), {}) == {}

    def test_a_step_whose_relevant_memory_was_not_retrieved_is_skipped(self) -> None:
        assert step_metrics([row("other")], Step(include=["a"]), {}) == {}

    def test_each_lane_ranks_the_applicable_pool(self) -> None:
        rows = [
            row("a", bm25=10.0),
            row("n", bm25=5.0),
            row("hidden", bm25=99.0, applies=False),
        ]
        out = step_metrics(
            rows, Step(include=["a"], exclude=["n"]), {}, lanes=("bm25",), applicable_only=True
        )
        assert out["bm25"]["r@1"] == 1.0
        assert out["bm25"]["mrr"] == 1.0

    def test_a_non_applicable_memory_is_pooled_when_asked(self) -> None:
        rows = [row("a", bm25=1.0), row("n", bm25=99.0, applies=False)]
        only = step_metrics(rows, Step(include=["a"], exclude=["n"]), {}, lanes=("bm25",))
        all_rows = step_metrics(
            rows, Step(include=["a"], exclude=["n"]), {}, lanes=("bm25",), applicable_only=False
        )
        assert only["bm25"]["mrr"] == 1.0
        assert all_rows["bm25"]["mrr"] == 0.5

    def test_a_custom_value_function_can_stand_in_for_a_lane(self) -> None:
        rows = [row("a", bm25=1.0), row("n", bm25=2.0)]
        out = step_metrics(
            rows,
            Step(include=["a"], exclude=["n"]),
            {},
            lanes=("custom",),
            value=lambda r, lane, top: 1.0 if r.key == "a" else 0.0,
        )
        assert out["custom"]["mrr"] == 1.0


class TestMeanMetrics:
    def test_averages_lane_by_lane_and_counts_the_requests(self) -> None:
        per_step = [
            {"bm25": {"r@1": 1.0, "mrr": 1.0}},
            {"bm25": {"r@1": 0.0, "mrr": 0.5}},
        ]
        out = mean_metrics(per_step)
        assert out == {"bm25": {"r@1": 0.5, "mrr": 0.75, "n": 2.0}}

    def test_empty_requests_are_skipped_and_absent_lanes_are_not_reported(self) -> None:
        out = mean_metrics([{}, {"bm25": {"r@1": 1.0}}, {"cosine": {"r@1": 0.0}}])
        assert out["bm25"]["n"] == 1.0
        assert out["cosine"]["n"] == 1.0
        assert "concept" not in out

    def test_no_requests_means_no_metrics(self) -> None:
        assert mean_metrics([]) == {}
