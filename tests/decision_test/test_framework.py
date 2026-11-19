"""The client, metrics and scorers of the decision benchmark, with a scripted model (offline)."""

from __future__ import annotations

import json

import httpx
import pytest

from benchmark_test.framework.dialogue import Turn
from decision_test.framework import gate, judge, metrics, same
from decision_test.framework.client import Kev
from ontyra.calibration.duplicates import load_pairs
from ontyra.models.decision import Judgement
from ontyra.models.recall import RecallContext
from ontyra.ports import DecisionError


def kev_with(handler) -> Kev:
    return Kev("http://model/v1", "m", transport=httpx.MockTransport(handler))


# ---- metrics --------------------------------------------------------------------------------


def test_auc_counts_ties_half_and_needs_both_sides() -> None:
    assert metrics.auc([0.9, 0.8], [0.1, 0.2]) == 1.0
    assert metrics.auc([0.5], [0.5]) == 0.5
    assert metrics.auc([0.1], [0.9]) == 0.0
    assert metrics.auc([], [0.5]) is None


def test_best_threshold_finds_the_separating_cut() -> None:
    best = metrics.best_threshold([0.1, 0.2, 0.8, 0.9], [False, False, True, True])
    assert best["threshold"] == 0.8 and best["f1"] == 1.0


def test_prf_latency_and_confusion() -> None:
    r = metrics.prf([True, True, False], [True, False, True])
    assert (r["tp"], r["fp"], r["fn"]) == (1, 1, 1)
    assert metrics.latency([1.0, 2.0, 3.0])["p50"] == 2.0
    assert metrics.confusion(["a", "b"], ["a", "a"]) == {"a": {"a": 1, "b": 1}}


# ---- client ---------------------------------------------------------------------------------


async def test_choice_and_yes_read_the_answers_and_record_latency() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        questions = json.loads(request.content)["questions"]
        if "pick" in questions:
            return httpx.Response(
                200, json={"answers": {"pick": {"probabilities": {"a": 0.7, "b": 0.3}}}}
            )
        return httpx.Response(200, json={"answers": {"q": {"noul": 0.4, "noul_raw": 0.9}}})

    kev = kev_with(handler)
    assert await kev.choice("s", "which?", {"a": "x", "b": "y"}) == {"a": 0.7, "b": 0.3}
    assert await kev.yes("s", "really?") == 0.9
    assert len(kev.seconds) == 2


async def test_failures_become_decision_errors() -> None:
    assert_error = pytest.raises(DecisionError)
    with assert_error:
        await kev_with(lambda r: httpx.Response(500)).yes("s", "q")
    with assert_error:
        await kev_with(lambda r: httpx.Response(200, json={"nope": 1})).yes("s", "q")
    with assert_error:
        await kev_with(lambda r: httpx.Response(200, json={"answers": {"pick": {}}})).choice(
            "s", "q", {"a": "x"}
        )


# ---- same -----------------------------------------------------------------------------------


async def test_same_scores_pairs_at_the_scan_threshold() -> None:
    pairs = load_pairs()
    truth = {(p.a, p.b): p.label for p in pairs}

    async def oracle(a: str, b: str) -> float:
        return 0.95 if truth[(a, b)] == "dup" else 0.05

    r = await same.evaluate(pairs, oracle)
    assert r["auc_dup_vs_rest"] == 1.0
    assert r["at_threshold"]["precision"] == 1.0 and r["at_threshold"]["recall"] == 1.0
    assert r["updates_accepted"] == 0 and len(r["rows"]) == len(pairs)

    async def eager(a: str, b: str) -> float:
        return 0.99

    assert (await same.evaluate(pairs, eager))["updates_accepted"] == sum(
        p.label == "update" for p in pairs
    )


# ---- judge ----------------------------------------------------------------------------------


def request(silent: bool = False) -> judge.JudgeRequest:
    return judge.JudgeRequest(
        id="r1",
        tags=["x"],
        situation="s",
        silent=silent,
        candidates=[
            judge.Candidate("wrong", "w", 0.9),
            judge.Candidate("right", "r", 0.8),
            judge.Candidate("other", "o", 0.1),
        ],
        relevant={"right"},
        negative={"wrong", "other"},
    )


async def test_judge_scores_ranking_against_the_pipeline_order() -> None:
    async def model(situation: str, memories) -> Judgement:  # picks the second candidate
        return Judgement(scores=[0.1, 0.8, 0.05], none=0.05)

    result = await judge.evaluate(model, [request()])
    row = result["rows"][0]
    assert row["top1"] == {"pipeline": False, "model": True, "blend": True}
    assert result["auc_model"] == 1.0 and result["auc_pipeline"] == 0.5
    assert row["picked"] == "right"


async def test_judge_maps_shuffled_answers_back_to_their_candidates() -> None:
    asked: list[list[str]] = []

    async def model(situation: str, memories) -> Judgement:
        asked.append(list(memories))
        return Judgement(scores=[1.0 if m == "r" else 0.0 for m in memories], none=0.0)

    r = request()
    answer = await judge.ask(model, r, shuffle=True)
    assert answer.scores == [0.0, 1.0, 0.0]  # in candidate order whatever order it was asked in
    assert sorted(asked[0]) == ["o", "r", "w"]


async def test_abstention_auc_compares_silent_with_other_requests() -> None:
    async def model(situation: str, memories) -> Judgement:
        silent = situation == "quiet"
        return Judgement(scores=[0.1, 0.1, 0.1], none=0.9 if silent else 0.1)

    quiet = request(silent=True)
    quiet.situation = "quiet"
    result = await judge.evaluate(model, [quiet, request()])
    assert result["abstention_auc"] == 1.0 and result["silent"] == 1


def test_sample_is_spread_and_stable() -> None:
    cases = list(range(10))
    assert judge.sample(cases, 0) == cases  # type: ignore[arg-type]
    assert judge.sample(cases, 5) == [0, 2, 4, 6, 8]  # type: ignore[arg-type]


# ---- gate -----------------------------------------------------------------------------------


def test_gate_scoring_reports_accuracy_confusion_and_mistakes() -> None:
    items = gate.load_items()
    verdicts = [{i.label: 1.0, "refers_back": float(i.label == "refers_back")} for i in items]
    perfect = gate.score(items, verdicts)
    assert perfect["accuracy"] == 1.0 and perfect["auc_refers_back"] == 1.0 and not perfect["wrong"]
    bad = gate.score(items, [{"standalone": 1.0} for _ in items])
    assert bad["accuracy"] < 0.5 and bad["at_threshold"]["recall"] == 0.0
    assert {w["label"] for w in bad["wrong"]} == {"refers_back", "filler"}


async def test_gate_builder_widens_only_what_the_classifier_says_refers_back() -> None:
    calls: list[str] = []

    async def classify(history, say):
        calls.append(say)
        return {"refers_back": 0.9 if say.startswith("and") else 0.1, "standalone": 0.5}

    history = [("which database?", "PostgreSQL")]
    ctx = RecallContext()
    narrow = gate.gate_builder(classify)
    assert (await narrow(history, Turn(say="and who set it up?"), ctx)).query == (
        "which database? and who set it up?"
    )
    assert (await narrow(history, Turn(say="what port?"), ctx)).query == "what port?"
    assert (await narrow([], Turn(say="and who?"), ctx)).query == "and who?"  # nothing to add
    union = await gate.gate_builder(classify, widen="union")(history, Turn(say="and why?"), ctx)
    assert union.query == "and why?" and union.also[0].query == "which database? and why?"
    cached: dict = {}
    again = gate.gate_builder(classify, cache=cached)
    await again(history, Turn(say="and x?"), ctx)
    await again(history, Turn(say="and x?"), ctx)
    assert calls.count("and x?") == 1  # one model call per (history, message)


def test_state_names_the_history_or_says_there_is_none() -> None:
    assert "no earlier conversation" in gate.state_of([], "hello", turns=1)
    state = gate.state_of([("q1", "a1"), ("q2", "a2")], "and?", turns=1)
    assert "q2" in state and "q1" not in state and state.endswith("and?")
