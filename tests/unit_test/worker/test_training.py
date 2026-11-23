from ontyra.worker.training import (
    Example,
    auc,
    class_weights,
    mrr_and_accuracy,
    split_by_query,
)


def ex(query: str, relevance: float, weight: float = 1.0) -> Example:
    return Example(query, f"text of {query} {relevance}", relevance, weight)


def test_validation_queries_are_wholly_held_out() -> None:
    examples = [ex(f"q{i}", float(j)) for i in range(40) for j in (0, 1)]
    train, valid = split_by_query(examples, 0.25, seed=1)
    assert valid and train
    assert {e.query for e in train}.isdisjoint({e.query for e in valid})
    assert len(train) + len(valid) == len(examples)
    assert split_by_query(examples, 0.25, seed=1) == (train, valid)  # deterministic


def test_a_split_that_would_lose_a_class_uses_everything_for_training() -> None:
    only_negative = [ex(f"q{i}", 0.0) for i in range(10)] + [ex("p", 1.0)]
    train, valid = split_by_query(only_negative, 0.5, seed=3)
    assert valid == [] and len(train) == len(only_negative)
    assert split_by_query(only_negative, 0.0, seed=3)[1] == []


def test_auc_counts_ties_as_half_and_needs_both_classes() -> None:
    assert auc([0.9, 0.1], [1.0, 0.0]) == 1.0
    assert auc([0.5, 0.5], [1.0, 0.0]) == 0.5
    assert auc([0.1, 0.9], [1.0, 0.0]) == 0.0
    assert auc([0.1, 0.9], [1.0, 1.0]) is None


def test_class_weights_balance_the_classes_and_keep_the_feedback_weight() -> None:
    examples = [ex("a", 1.0), ex("b", 0.0, 1.0), ex("c", 0.0, 1.0), ex("d", 0.0, 1.0)]
    w = class_weights(examples)
    assert abs(w[0] - 2.0) < 1e-9 and abs(w[1] - 2 / 3) < 1e-9
    assert abs(sum(w[:1]) - sum(w[1:])) < 1e-9
    trusted = class_weights([ex("a", 1.0, 1.0), ex("b", 1.0, 0.5), ex("c", 0.0, 1.0)])
    assert trusted[0] > trusted[1]


def test_the_gate_set_is_scored_by_rank_and_pairs() -> None:
    requests = [
        {
            "query": "q1",
            "candidates": [{"text": "bad", "relevant": False}, {"text": "good", "relevant": True}],
        },
        {
            "query": "q2",
            "candidates": [{"text": "good", "relevant": True}, {"text": "bad", "relevant": False}],
        },
    ]
    perfect = mrr_and_accuracy(
        requests, lambda pairs: [1.0 if t == "good" else 0.0 for _, t in pairs]
    )
    assert perfect == {"mrr": 1.0, "pairwise": 1.0, "requests": 2.0}
    inverted = mrr_and_accuracy(
        requests, lambda pairs: [0.0 if t == "good" else 1.0 for _, t in pairs]
    )
    assert inverted["mrr"] == 0.5 and inverted["pairwise"] == 0.0
