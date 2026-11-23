"""Feedback is kept, resolved and expired; retraining is explicit and needs a trainable reranker."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

import pytest

from ontyra.app import Application
from ontyra.models import RecordId
from ontyra.models.feedback import (
    FeedbackRequest,
    GateCandidate,
    GateRequest,
    GateVerdict,
    LiveResult,
    RerankExample,
    TrainOptions,
    TrainResult,
)
from ontyra.ports.adapters import TrainableReranker
from ontyra.services import Caller
from ontyra.services.errors import InvalidRequestError, NotFoundError


class FakeTrainable:
    """A reranker that records what it was trained on. Like the worker, training saves a version
    and does not activate it; its metrics carry the trusted requests scored before and after."""

    name = "fake"

    def __init__(self, gate_after: float = 0.95) -> None:
        self.version = "base"
        self.trained: list[RerankExample] = []
        self.gate_seen: list[GateRequest] = []
        self.gate_after = gate_after

    async def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        return [0.0] * len(texts)

    def versions(self) -> list[str]:
        return ["base", "v1"]

    async def refresh(self) -> None:
        return None

    async def train(
        self,
        examples: Sequence[RerankExample],
        options: TrainOptions,
        gate: Sequence[GateRequest] = (),
    ) -> TrainResult:
        self.trained = list(examples)
        self.gate_seen = list(gate)
        before = {"mrr": 0.9, "pairwise": 0.9}
        after = {"mrr": self.gate_after, "pairwise": self.gate_after}
        return TrainResult(
            version="v1",
            examples=len(examples),
            metrics={"auc_after": 0.9, "gate_before": before, "gate_after": after},
        )

    async def activate(self, version: str) -> None:
        self.version = version


@pytest.fixture
async def memory_id(app: Application, remember) -> RecordId:
    return await remember("directive", "use pytest for tests", strength="must")


async def test_feedback_is_recorded_with_its_trust_and_current_revision(
    app: Application, agent: Caller, memory_id: RecordId
) -> None:
    event = await app.feedback.record(
        agent, FeedbackRequest(memory=memory_id, query="write tests", label="applied")
    )
    assert (
        event.state == "active" and event.label == "applied" and event.weight == agent.actor.trust
    )
    assert (await app.feedback.summary()).trainable_examples == 1


async def test_a_human_verdict_replaces_an_agents_for_the_same_query(
    app: Application, agent: Caller, human: Caller, memory_id: RecordId
) -> None:
    mine = await app.feedback.record(
        agent, FeedbackRequest(memory=memory_id, query="Write  tests", label="applied")
    )
    yours = await app.feedback.record(
        human, FeedbackRequest(memory=memory_id, query="write tests", label="irrelevant")
    )
    by_id = {f.id: f for f in await app.feedback.list(agent)}
    assert by_id[mine.id].state == "superseded" and by_id[yours.id].state == "active"
    again = await app.feedback.record(
        agent, FeedbackRequest(memory=memory_id, query="write tests", label="applied")
    )
    states = {f.id: f.state for f in await app.feedback.list(agent)}
    assert states[again.id] == "superseded" and states[yours.id] == "active"  # trust wins, not age


async def test_feedback_for_other_queries_is_independent(
    app: Application, agent: Caller, memory_id: RecordId
) -> None:
    for query in ("write tests", "run the linter"):
        await app.feedback.record(
            agent, FeedbackRequest(memory=memory_id, query=query, label="applied")
        )
    assert (await app.feedback.summary()).active == 2


async def test_expired_feedback_no_longer_counts(
    app: Application, agent: Caller, memory_id: RecordId, clock
) -> None:
    await app.feedback.record(
        agent, FeedbackRequest(memory=memory_id, query="q", label="applied", expires_in_days=1)
    )
    assert (await app.feedback.summary()).active == 1
    clock._t += timedelta(days=2)
    summary = await app.feedback.summary()
    assert summary.active == 0 and summary.expired == 1 and summary.trainable_examples == 0


async def test_unknown_memory_and_foreign_retraction_are_refused(
    app: Application, agent: Caller, human: Caller, memory_id: RecordId
) -> None:
    with pytest.raises(NotFoundError):
        await app.feedback.record(
            agent, FeedbackRequest(memory=RecordId("memory", "nope"), query="q", label="applied")
        )
    event = await app.feedback.record(
        human, FeedbackRequest(memory=memory_id, query="q", label="applied")
    )
    with pytest.raises(InvalidRequestError):
        await app.feedback.retract(agent, event.id)
    assert (await app.feedback.retract(human, event.id)).state == "retracted"


async def test_examples_carry_the_judged_revisions_text_and_the_label(
    app: Application, agent: Caller, memory_id: RecordId
) -> None:
    await app.feedback.record(agent, FeedbackRequest(memory=memory_id, query="q1", label="applied"))
    await app.feedback.record(
        agent, FeedbackRequest(memory=memory_id, query="q2", label="irrelevant")
    )
    pairs = {e.query: e for e in await app.feedback.examples()}
    assert "pytest" in pairs["q1"].text and pairs["q1"].relevance == 1.0
    assert pairs["q2"].relevance == 0.0


async def test_a_hosted_reranker_cannot_be_retrained(app: Application, human: Caller) -> None:
    with pytest.raises(InvalidRequestError, match="no reranker"):
        await app.reranking.train(human)
    status = await app.reranking.status()
    assert not status.trainable and status.reranker is None


async def test_retraining_is_human_started_and_needs_enough_two_class_feedback(
    app: Application, agent: Caller, human: Caller, memory_id: RecordId
) -> None:
    fake = FakeTrainable()
    app.reranking.reranker = fake
    app.reranking.min_examples = 3
    with pytest.raises(InvalidRequestError, match="human"):
        await app.reranking.train(agent)
    with pytest.raises(InvalidRequestError, match="at least 3"):
        await app.reranking.train(human)
    for i, label in enumerate(("applied", "applied", "applied")):
        await app.feedback.record(
            agent, FeedbackRequest(memory=memory_id, query=f"q{i}", label=label)
        )
    with pytest.raises(InvalidRequestError, match="both relevant"):
        await app.reranking.train(human)
    await app.feedback.record(agent, FeedbackRequest(memory=memory_id, query="q9", label="wrong"))
    run = await app.reranking.train(human)
    assert run.activated and run.version == "v1" and run.examples == 4
    assert fake.version == "v1" and len(fake.trained) == 4
    status = await app.reranking.status()
    assert status.trainable and status.runs and status.runs[0]["version"] == "v1"


class Gate:
    def __init__(self, passed: bool) -> None:
        self.passed = passed
        self.seen: tuple[str, str] | None = None

    async def check(
        self, reranker: TrainableReranker, candidate: str, previous: str
    ) -> GateVerdict:
        self.seen = (candidate, previous)
        assert reranker.version == candidate  # the gate judges the candidate while it is active
        return GateVerdict(passed=self.passed, detail={"regressions": 0 if self.passed else 3})


@pytest.mark.parametrize("passes", [True, False])
async def test_a_version_the_gate_rejects_is_rolled_back(
    app: Application, agent: Caller, human: Caller, memory_id: RecordId, passes: bool
) -> None:
    fake = FakeTrainable()
    gate = Gate(passes)
    app.reranking.reranker = fake
    app.reranking.gate = gate
    app.reranking.min_examples = 2
    await app.feedback.record(agent, FeedbackRequest(memory=memory_id, query="a", label="applied"))
    await app.feedback.record(agent, FeedbackRequest(memory=memory_id, query="b", label="wrong"))
    run = await app.reranking.train(human)
    assert gate.seen == ("v1", "base")
    assert run.activated is passes and fake.version == ("v1" if passes else "base")
    assert run.gate is not None and run.gate["passed"] is passes
    assert ("promotion gate" in (run.note or "")) is (not passes)


async def test_a_version_that_does_worse_on_the_trusted_requests_is_not_activated(
    app: Application, agent: Caller, human: Caller, memory_id: RecordId
) -> None:
    fake = FakeTrainable(gate_after=0.5)
    app.reranking.reranker = fake
    app.reranking.min_examples = 2
    await app.feedback.record(agent, FeedbackRequest(memory=memory_id, query="a", label="applied"))
    await app.feedback.record(agent, FeedbackRequest(memory=memory_id, query="b", label="wrong"))
    run = await app.reranking.train(human)
    assert not run.activated and fake.version == "base"
    assert run.gate is not None and run.gate["passed"] is False
    assert run.gate["detail"]["after"]["mrr"] == 0.5
    assert "promotion gate" in (run.note or "")
    saved = await app.reranking.train(human, TrainOptions(activate=False))  # also kept, not used
    assert not saved.activated and fake.version == "base"


async def test_anchors_are_mixed_in_and_the_gate_requests_go_with_the_training(
    app: Application, agent: Caller, human: Caller, memory_id: RecordId
) -> None:
    fake = FakeTrainable()
    app.reranking.reranker = fake
    app.reranking.min_examples = 2
    app.reranking.cfg = app.reranking.cfg.model_copy(update={"anchor_ratio": 2.0})
    anchors = [
        RerankExample(query=f"x{i}", text="t", relevance=float(i % 2), source="anchor")
        for i in range(10)
    ]
    gate = [GateRequest(query="g", candidates=[GateCandidate(text="a", relevant=True)])]
    app.reranking.anchors = lambda: (anchors, gate)
    await app.feedback.record(agent, FeedbackRequest(memory=memory_id, query="a", label="applied"))
    await app.feedback.record(agent, FeedbackRequest(memory=memory_id, query="b", label="wrong"))
    run = await app.reranking.train(human)
    assert run.examples == 2 and run.metrics["anchors"] == 4  # twice the feedback, from the anchors
    assert len(fake.trained) == 6 and sum(e.source == "anchor" for e in fake.trained) == 4
    assert fake.gate_seen == gate


async def test_only_humans_train_when_the_policy_says_so_and_no_source_dominates(
    app: Application, agent: Caller, human: Caller, memory_id: RecordId
) -> None:
    fake = FakeTrainable()
    app.reranking.reranker = fake
    app.reranking.min_examples = 2
    await app.feedback.record(human, FeedbackRequest(memory=memory_id, query="h1", label="applied"))
    await app.feedback.record(human, FeedbackRequest(memory=memory_id, query="h2", label="wrong"))
    for i in range(6):
        await app.feedback.record(
            agent, FeedbackRequest(memory=memory_id, query=f"a{i}", label="applied")
        )
    await app.reranking.train(human)
    weights: dict[str, float] = {}
    for e in fake.trained:
        weights[e.source] = weights.get(e.source, 0.0) + e.weight
    assert abs(weights[str(agent.actor_id)] - weights[str(human.actor_id)]) < 1e-6  # capped at half
    app.reranking.cfg = app.reranking.cfg.model_copy(update={"train_sources": "human"})
    await app.reranking.train(human)
    assert {e.source for e in fake.trained} == {str(human.actor_id)}


class FakeLive(FakeTrainable):
    """A reranker with a live head edit: records the examples each refit was given."""

    def __init__(self, revert: bool = False) -> None:
        super().__init__()
        self.fits: list[list[RerankExample]] = []
        self.resets = 0
        self.revert = revert
        self.limits: tuple[float, float] | None = None

    async def live_status(self) -> dict[str, object]:
        return {"enabled": True, "version": self.version, "distance": 0.1}

    async def live_update(
        self,
        examples: Sequence[RerankExample],
        gate: Sequence[GateRequest],
        *,
        max_distance: float,
        tolerance: float,
    ) -> LiveResult:
        self.fits.append(list(examples))
        self.limits = (max_distance, tolerance)
        return LiveResult(applied=not self.revert, reverted=self.revert, distance=0.1)

    async def live_reset(self) -> None:
        self.resets += 1


@pytest.fixture
def live_app(app: Application) -> Application:
    app.reranking.cfg = app.reranking.cfg.model_copy(
        update={"live": True, "pool": "all", "live_min_examples": 3, "anchor_ratio": 0.0}
    )
    return app


async def _verdicts(
    app: Application, caller: Caller, memory: RecordId, n: int = 4
) -> list[RecordId]:
    ids: list[RecordId] = []
    for i in range(n):
        event = await app.feedback.record(
            caller,
            FeedbackRequest(memory=memory, query=f"q{i}", label="wrong" if i % 2 else "applied"),
        )
        ids.append(event.id)
    return ids


async def test_a_live_edit_is_off_unless_configured_and_needs_a_shared_pool(
    app: Application, human: Caller, memory_id: RecordId
) -> None:
    app.reranking.reranker = FakeLive()
    with pytest.raises(InvalidRequestError, match="LIVE"):
        await app.reranking.live_update(human)
    app.reranking.cfg = app.reranking.cfg.model_copy(update={"live": True})  # pool is still own
    with pytest.raises(InvalidRequestError, match="POOL=all"):
        await app.reranking.live_update(human)
    app.reranking.cfg = app.reranking.cfg.model_copy(update={"pool": "all"})
    app.reranking.reranker = FakeTrainable()
    with pytest.raises(InvalidRequestError, match="no live edit"):
        await app.reranking.live_update(human)


async def test_the_live_edit_is_a_replay_so_a_veto_changes_what_it_is_fitted_on(
    live_app: Application, human: Caller, agent: Caller, memory_id: RecordId
) -> None:
    fake = FakeLive()
    live_app.reranking.reranker = fake
    events = await _verdicts(live_app, human, memory_id)
    with pytest.raises(InvalidRequestError, match="human"):
        await live_app.reranking.live_update(agent)
    result = await live_app.reranking.live_update(human)
    assert result.applied and result.examples == 4 and len(fake.fits[-1]) == 4
    assert fake.limits == (0.25, 0.01)
    await live_app.feedback_review.veto(human, events[0], "a slip")
    assert live_app.reranking._stale  # pyright: ignore[reportPrivateUsage]
    again = await live_app.reranking.live_update(human)
    assert again.examples == 3 and len(fake.fits[-1]) == 3  # refitted without it, from scratch
    assert not live_app.reranking._stale  # pyright: ignore[reportPrivateUsage]
    runs = (await live_app.reranking.status()).runs
    assert [r["kind"] for r in runs][:2] == ["live", "live"]


async def test_too_little_evidence_removes_the_edit_and_a_reverted_fit_says_so(
    live_app: Application, human: Caller, memory_id: RecordId
) -> None:
    fake = FakeLive()
    live_app.reranking.reranker = fake
    await _verdicts(live_app, human, memory_id, n=2)
    thin = await live_app.reranking.live_update(human)
    assert not thin.applied and fake.resets == 1 and "needed" in (thin.note or "")
    await _verdicts(live_app, human, memory_id, n=4)
    fake.revert = True
    reverted = await live_app.reranking.live_update(human)
    assert reverted.reverted and "worse" in (reverted.note or "")
    removed = await live_app.reranking.live_reset(human)
    assert not removed.applied and fake.resets == 2


async def test_feedback_changes_schedule_one_batched_refit(
    live_app: Application, human: Caller, memory_id: RecordId
) -> None:
    import asyncio

    fake = FakeLive()
    live_app.reranking.reranker = fake
    live_app.reranking.cfg = live_app.reranking.cfg.model_copy(update={"live_every_seconds": 1.5})
    await _verdicts(live_app, human, memory_id, n=6)  # six changes, one timer
    assert fake.fits == []
    await asyncio.sleep(2.0)
    assert len(fake.fits) == 1 and len(fake.fits[0]) == 6
    live_app.reranking.close()
