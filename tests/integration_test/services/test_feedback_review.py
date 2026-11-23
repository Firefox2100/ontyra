"""Reviewing the examples learning uses: outliers, veto, quarantine and opting out of training."""

from __future__ import annotations

import pytest

from ontyra.app import Application
from ontyra.models import RecordId
from ontyra.models.feedback import (
    ExclusionRequest,
    FeedbackLabel,
    FeedbackRequest,
    LearningSettings,
)
from ontyra.models.proposals import AddMemory, ProposalRequest
from ontyra.models.recall import RecallRequest, RecallSettings
from ontyra.services import Caller
from ontyra.services.errors import InvalidRequestError, NotFoundError, PermissionDeniedError

QUERIES = ["run the tests with pytest", "format the code before commit", "how to run tests"]


@pytest.fixture
async def learning(make_app) -> Application:
    return await make_app(
        recall=RecallSettings(engine="fusion", fusion_margin=0.9),
        learning=LearningSettings(
            enabled=True, min_examples=4, canary_fraction=0.0, canary_min_groups=0
        ),
    )


async def _memories(app: Application, caller: Caller) -> dict[str, RecordId]:
    out: dict[str, RecordId] = {}
    for key, text in {
        "pytest": "Run the tests with pytest before every commit",
        "format": "Format the code with ruff before commit",
    }.items():
        body = {"text": text, "strength": "should", "rule": text}
        result = await app.memory.submit(
            caller, ProposalRequest(ops=[AddMemory(kind="directive", ref="m", fields=body)])
        )
        out[key] = result.created["m"]
    await app.indexer.backfill()
    return out


async def _judge(
    app: Application, caller: Caller, ids: dict[str, RecordId], label: FeedbackLabel = "applied"
) -> list[RecordId]:
    events: list[RecordId] = []
    for query in QUERIES:
        pack = await app.recall.recall(caller, RecallRequest(query=query))
        for memory in ids.values():
            event = await app.feedback.record(
                caller, FeedbackRequest(memory=memory, query=query, label=label, trace=pack.trace)
            )
            events.append(event.id)
    return events


async def test_the_example_the_ranking_disagrees_with_comes_first(
    learning: Application, human: Caller
) -> None:
    ids = await _memories(learning, human)
    pack = await learning.recall.recall(human, RecallRequest(query=QUERIES[0]))
    await learning.feedback.record(
        human,
        FeedbackRequest(
            memory=ids["pytest"], query=QUERIES[0], label="irrelevant", trace=pack.trace
        ),
    )
    pack = await learning.recall.recall(human, RecallRequest(query=QUERIES[1]))
    await learning.feedback.record(
        human,
        FeedbackRequest(memory=ids["format"], query=QUERIES[1], label="applied", trace=pack.trace),
    )
    ranked = await learning.feedback_review.outliers(human)
    assert ranked[0].feedback.label == "irrelevant"
    assert ranked[0].disagreement is not None and ranked[0].disagreement > 0.5
    assert "disagrees" in ranked[0].reasons
    assert ranked[0].text.startswith("Run the tests")


async def test_a_veto_replays_the_layer_without_the_example_and_survives_a_resend(
    learning: Application, human: Caller
) -> None:
    ids = await _memories(learning, human)
    events = await _judge(learning, human, ids)
    before = await learning.feedback.layer_state(human)
    assert before.status == "active"
    vetoed = await learning.feedback_review.veto(human, events[0], "a slip")
    assert vetoed.state == "vetoed" and vetoed.veto_reason == "a slip"
    after = await learning.feedback.layer_state(human)
    assert after.examples == before.examples - 1
    assert len(await learning.feedback.examples()) == len(events) - 1
    assert events[0] not in {o.feedback.id for o in await learning.feedback_review.outliers(human)}
    # the same verdict sent again stays out
    again = await learning.feedback.record(
        human, FeedbackRequest(memory=vetoed.memory, query=vetoed.query, label="applied")
    )
    assert again.state == "vetoed"
    assert len(await learning.feedback.examples()) == len(events) - 1
    # and a veto can be undone
    back = await learning.feedback_review.unveto(human, events[0])
    assert back.state == "active"
    assert len(await learning.feedback.examples()) == len(events)


async def test_only_a_human_of_the_owner_can_veto(
    learning: Application, human: Caller, agent: Caller
) -> None:
    ids = await _memories(learning, human)
    events = await _judge(learning, human, ids)
    with pytest.raises(PermissionDeniedError):
        await learning.feedback_review.veto(agent, events[0])
    bob = await learning.actors.caller(kind="human", name="bob", owner=RecordId("user", "bob"))
    with pytest.raises(NotFoundError):
        await learning.feedback_review.veto(bob, events[0])
    assert await learning.feedback_review.outliers(bob) == []
    with pytest.raises(PermissionDeniedError):
        await learning.feedback_review.outliers(bob, everyone=True)


async def test_a_quarantined_source_counts_nowhere_until_lifted(
    learning: Application, human: Caller, agent: Caller
) -> None:
    ids = await _memories(learning, human)
    await _judge(learning, agent, ids)
    assert (await learning.feedback.layer_state(agent)).status == "active"
    held = await learning.feedback_review.exclude(
        human,
        ExclusionRequest(kind="quarantine", scope="source", target=agent.actor_id, reason="noisy"),
    )
    state = await learning.feedback.layer_state(agent)
    assert state.status == "learning" and state.examples == 0
    assert await learning.feedback.examples() == []
    again = await learning.feedback_review.exclude(
        human, ExclusionRequest(kind="quarantine", scope="source", target=agent.actor_id)
    )
    assert again.id == held.id  # not duplicated
    await learning.feedback_review.lift(human, held.id)
    assert (await learning.feedback.layer_state(agent)).status == "active"
    assert await learning.feedback.examples()


async def test_quarantine_needs_an_administrator_and_a_right_target(
    learning: Application, human: Caller
) -> None:
    bob = await learning.actors.caller(kind="human", name="bob", owner=RecordId("user", "bob"))
    with pytest.raises(PermissionDeniedError):
        await learning.feedback_review.exclude(
            bob, ExclusionRequest(kind="quarantine", scope="owner", target=human.owner)
        )
    with pytest.raises(InvalidRequestError):
        await learning.feedback_review.exclude(
            human, ExclusionRequest(kind="quarantine", scope="owner", target=human.actor_id)
        )
    with pytest.raises(InvalidRequestError):
        await learning.feedback_review.exclude(human, ExclusionRequest(kind="quarantine"))


async def test_opting_out_keeps_feedback_out_of_training_but_not_my_own_layer(
    learning: Application, human: Caller
) -> None:
    ids = await _memories(learning, human)
    await _judge(learning, human, ids)
    assert await learning.feedback.examples()
    opt = await learning.feedback_review.opt_out(human, True)
    assert opt is not None and opt.kind == "opt_out"
    assert await learning.feedback.examples() == []
    assert (await learning.feedback.summary(human)).trainable_examples == 0
    assert (await learning.feedback.layer_state(human)).status == "active"  # my layer still learns
    assert await learning.feedback_review.opt_out(human, False) is not None
    assert await learning.feedback.examples()
    assert await learning.feedback_review.opt_out(human, False) is None  # already back in


async def test_anyone_may_lift_only_their_own_opt_out(learning: Application, human: Caller) -> None:
    bob = await learning.actors.caller(kind="human", name="bob", owner=RecordId("user", "bob"))
    mine = await learning.feedback_review.opt_out(bob, True)
    assert mine is not None
    other = await learning.actors.caller(
        kind="human", name="carol", owner=RecordId("user", "carol")
    )
    with pytest.raises(PermissionDeniedError):
        await learning.feedback_review.lift(other, mine.id)
    assert [e.id for e in await learning.feedback_review.exclusions(bob)] == [mine.id]
    assert await learning.feedback_review.exclusions(other) == []
    await learning.feedback_review.lift(bob, mine.id)
