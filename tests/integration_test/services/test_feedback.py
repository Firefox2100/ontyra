"""Feedback is kept, resolved and expired; retraining is explicit and needs a trainable reranker."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

import pytest

from ontyra.app import Application
from ontyra.models import RecordId
from ontyra.models.feedback import (
    FeedbackRequest,
    GateVerdict,
    RerankExample,
    TrainOptions,
    TrainResult,
)
from ontyra.ports.adapters import TrainableReranker
from ontyra.services import Caller
from ontyra.services.errors import InvalidRequestError, NotFoundError


class FakeTrainable:
    """A reranker that records what it was trained on."""

    name = "fake"
    version = "base"

    def __init__(self) -> None:
        self.trained: list[RerankExample] = []

    async def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        return [0.0] * len(texts)

    def versions(self) -> list[str]:
        return ["base", "v1"]

    async def train(self, examples: Sequence[RerankExample], options: TrainOptions) -> TrainResult:
        self.trained = list(examples)
        self.version = "v1"  # like the real adapters, training activates what beats the start
        return TrainResult(
            version="v1", examples=len(examples), metrics={"auc_after": 0.9}, activated=True
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
    by_id = {f.id: f for f in await app.feedback.list()}
    assert by_id[mine.id].state == "superseded" and by_id[yours.id].state == "active"
    again = await app.feedback.record(
        agent, FeedbackRequest(memory=memory_id, query="write tests", label="applied")
    )
    states = {f.id: f.state for f in await app.feedback.list()}
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
    assert len(fake.trained) == 4
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
