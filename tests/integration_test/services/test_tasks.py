"""The maintenance queue: findings become tasks once, are claimed and completed with a validated
result, close themselves when their memories change, and stay inside their owner."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from ontyra.app import Application
from ontyra.config import AuthSettings
from ontyra.models import PolicyConfig, RecordId
from ontyra.models.consolidation import ConsolidationSettings
from ontyra.models.proposals import AddMemory, ProposalRequest, SupersedeMemory
from ontyra.models.tasks import TaskResult, TaskView
from ontyra.services import Caller, errors

SETTINGS = ConsolidationSettings(candidate_cosine=0.4, duplicate_cosine=0.75, confirm=0.85)
A = "Use uv for Python dependency management in tidewatch and never run pip install directly."
B = "Use uv for Python dependency management in tidewatch, never run pip install directly."
OLD = "Tidewatch production runs PostgreSQL 14 on the main database host."
NEW = "Tidewatch production runs PostgreSQL 16 on the main database host."


async def add(app: Application, who: Caller, text: str) -> RecordId:
    body = {"text": text, "strength": "should", "rule": text}
    result = await app.memory.submit(
        who, ProposalRequest(ops=[AddMemory(kind="directive", ref="m", fields=body)])
    )
    assert result.status == "committed", result.findings
    return result.created["m"]


@pytest.fixture
async def world(make_app, agent: Caller):
    app: Application = await make_app(consolidation=SETTINGS)
    ids = {k: await add(app, agent, t) for k, t in {"a": A, "b": B, "old": OLD, "new": NEW}.items()}
    await app.indexer.backfill()
    return app, ids


def by_kind(tasks: list[TaskView], kind: str) -> TaskView:
    (found,) = [t for t in tasks if t.kind == kind]
    return found


async def test_detect_queues_each_finding_once(world, agent: Caller) -> None:
    app, _ = world
    first = await app.tasks.detect(agent)
    assert {t.kind for t in first.created} == {"consolidate", "review_contradiction"}
    assert first.existing == 0
    again = await app.tasks.detect(agent)
    assert again.created == [] and again.existing == 2
    tasks = await app.tasks.list(agent)
    assert len(tasks) == 2 and all(t.status == "pending" for t in tasks)
    card = by_kind(tasks, "consolidate").card
    assert card["choices"] == ["merge", "distinct", "defer"] and len(card["members"]) == 2
    assert "supersede" in by_kind(tasks, "review_contradiction").card["choices"]


async def test_claim_and_release(world, agent: Caller) -> None:
    app, _ = world
    await app.tasks.detect(agent)
    task = by_kind(await app.tasks.list(agent), "consolidate")
    claimed = await app.tasks.claim(agent, task.id)
    assert claimed.status == "claimed" and claimed.claimed_by == agent.actor_id
    with pytest.raises(errors.ConflictError):
        await app.tasks.claim(agent, task.id)
    assert (await app.tasks.release(agent, task.id)).status == "pending"


async def test_merge_must_cite_a_real_proposal_and_a_choice_the_card_offers(
    world, agent: Caller
) -> None:
    app, ids = world
    await app.tasks.detect(agent)
    task = by_kind(await app.tasks.list(agent), "consolidate")
    await app.tasks.claim(agent, task.id)
    with pytest.raises(errors.InvalidRequestError, match="must cite"):
        await app.tasks.complete(agent, task.id, TaskResult(decision="merge"))
    with pytest.raises(errors.InvalidRequestError, match="one of"):
        await app.tasks.complete(agent, task.id, TaskResult(decision="supersede"))
    with pytest.raises(errors.InvalidRequestError, match="takes no proposal"):
        await app.tasks.complete(
            agent, task.id, TaskResult(decision="distinct", proposal=RecordId("proposal", "x"))
        )
    with pytest.raises(errors.NotFoundError):
        await app.tasks.complete(
            agent, task.id, TaskResult(decision="merge", proposal=RecordId("proposal", "nope"))
        )
    done = await app.memory.submit(
        agent,
        ProposalRequest(ops=[SupersedeMemory(new=ids["a"], target=ids["b"], reason_code="merged")]),
    )
    assert done.status == "committed", done.findings
    finished = await app.tasks.complete(
        agent, task.id, TaskResult(decision="merge", proposal=done.proposal, note="same rule")
    )
    assert finished.status == "done" and finished.result["decision"] == "merge"
    assert by_kind(await app.tasks.list(agent, statuses=["done"]), "consolidate").id == task.id


async def test_distinct_is_remembered_and_defer_parks(world, agent: Caller) -> None:
    app, _ = world
    await app.tasks.detect(agent)
    tasks = await app.tasks.list(agent)
    dup, upd = by_kind(tasks, "consolidate"), by_kind(tasks, "review_contradiction")
    assert (
        await app.tasks.complete(agent, dup.id, TaskResult(decision="distinct"))
    ).status == "done"
    assert (
        await app.tasks.complete(agent, upd.id, TaskResult(decision="defer"))
    ).status == "parked"
    assert await app.tasks.list(agent) == []  # neither is open any more
    again = await app.tasks.detect(agent)
    assert again.created == [] and again.existing == 2  # a decision is not asked twice


async def test_a_task_closes_when_its_memories_change(world, agent: Caller) -> None:
    app, ids = world
    await app.tasks.detect(agent)
    task = by_kind(await app.tasks.list(agent), "consolidate")
    done = await app.memory.submit(
        agent,
        ProposalRequest(ops=[SupersedeMemory(new=ids["a"], target=ids["b"], reason_code="merged")]),
    )
    assert done.status == "committed", done.findings
    report = await app.tasks.detect(agent)
    assert report.closed == 1
    closed = await app.tasks.get(agent, task.id)
    assert closed.status == "done" and closed.result == {"outcome": "obsolete"}


async def test_review_tasks_are_not_worked_here(make_app, agent: Caller) -> None:
    app: Application = await make_app(PolicyConfig(default="review"))
    human = await app.actors.caller(kind="human", name="owner")
    await app.memory.submit(
        agent,
        ProposalRequest(
            ops=[
                AddMemory(
                    kind="directive",
                    ref="m",
                    fields={"text": A, "strength": "should", "rule": A},
                )
            ]
        ),
    )
    assert await app.tasks.list(agent) == []  # an agent cannot decide it, so it is not shown
    (review,) = await app.tasks.list(human)
    assert review.kind == "review_proposal"
    with pytest.raises(errors.InvalidRequestError, match="own endpoint"):
        await app.tasks.claim(human, review.id)


async def test_tasks_stay_inside_their_owner(make_app) -> None:
    app: Application = await make_app(
        PolicyConfig(default="auto_commit"),
        consolidation=SETTINGS,
        auth=AuthSettings(
            multi_user=True, admin_token=SecretStr("admin-token-for-tests-0123456789")
        ),
    )
    await app.auth.bootstrap()

    async def user(name: str) -> Caller:
        await app.auth.create_user(name, [])
        _, token = await app.auth.issue_credential(name, "cli", "human")
        principal = await app.auth.authenticate(token)
        assert principal is not None
        return await app.auth.caller(principal, label="x", kind="x", harness=None)

    ann, bob = await user("ann"), await user("bob")
    await add(app, ann, A)
    await add(app, ann, B)
    await app.indexer.backfill()
    (task,) = (await app.tasks.detect(ann)).created
    assert await app.tasks.list(bob) == []
    with pytest.raises(errors.NotFoundError):
        await app.tasks.get(bob, task.id)
    with pytest.raises(errors.NotFoundError):
        await app.tasks.claim(bob, task.id)
    assert (await app.tasks.detect(bob)).created == []  # bob's scan does not see ann's memories
