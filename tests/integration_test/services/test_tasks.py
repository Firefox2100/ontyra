"""The maintenance queue: findings become tasks once, are claimed and completed with a validated
result, close themselves when their memories change, and stay inside their owner."""

from __future__ import annotations

import asyncio

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


async def test_concurrent_claims_have_one_winner_and_only_it_can_release(
    world, agent: Caller
) -> None:
    app, _ = world
    await app.tasks.detect(agent)
    task = by_kind(await app.tasks.list(agent), "consolidate")
    other = await app.actors.caller(kind="agent", name="other-worker")

    outcomes = await asyncio.gather(
        app.tasks.claim(agent, task.id),
        app.tasks.claim(other, task.id),
        return_exceptions=True,
    )
    winners = [result for result in outcomes if isinstance(result, TaskView)]
    conflicts = [result for result in outcomes if isinstance(result, errors.ConflictError)]
    assert len(winners) == 1 and len(conflicts) == 1
    winner = agent if winners[0].claimed_by == agent.actor_id else other
    loser = other if winner is agent else agent
    with pytest.raises(errors.ConflictError, match="another actor"):
        await app.tasks.release(loser, task.id)
    assert (await app.tasks.release(winner, task.id)).status == "pending"


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


async def memory_status(app: Application, memory: RecordId) -> str:
    row = await app.memory.store.get(memory)  # type: ignore[attr-defined]
    return str(row.status)  # type: ignore[union-attr]


async def test_resolve_merges_a_cluster_and_replaces_an_update(world, agent: Caller) -> None:
    app, ids = world
    await app.tasks.detect(agent)
    tasks = await app.tasks.list(agent)
    dup, upd = by_kind(tasks, "consolidate"), by_kind(tasks, "review_contradiction")
    merged = await app.tasks.resolve(agent, dup.id, "merge")
    assert merged.status == "done" and merged.result["decision"] == "merge"
    assert merged.result["proposal"].startswith("proposal:")
    assert {await memory_status(app, ids["a"]), await memory_status(app, ids["b"])} == {
        "active",
        "superseded",
    }  # the oldest is kept
    assert await memory_status(app, ids["a"]) == "active"
    replaced = await app.tasks.resolve(agent, upd.id, "supersede")
    assert replaced.status == "done"
    assert await memory_status(app, ids["old"]) == "superseded"
    assert await memory_status(app, ids["new"]) == "active"
    assert (await app.tasks.detect(agent)).created == []


async def test_resolve_refuses_a_decision_the_task_does_not_have(world, agent: Caller) -> None:
    app, _ = world
    await app.tasks.detect(agent)
    upd = by_kind(await app.tasks.list(agent), "review_contradiction")
    with pytest.raises(errors.InvalidRequestError, match="cannot be resolved"):
        await app.tasks.resolve(agent, upd.id, "merge")


async def test_a_merge_held_for_review_keeps_the_task_open_until_decided(make_app) -> None:
    app: Application = await make_app(
        PolicyConfig.model_validate(
            {"default": "review", "rules": [{"actor_kinds": ["human"], "decision": "auto_commit"}]}
        ),
        consolidation=SETTINGS,
    )
    human = await app.actors.caller(kind="human", name="owner")
    agent = await app.actors.caller(kind="agent", name="claude", harness="test")
    ids = [await add(app, human, A), await add(app, human, B)]
    await app.indexer.backfill()
    await app.tasks.detect(agent)
    (task,) = await app.tasks.list(agent)
    held = await app.tasks.resolve(agent, task.id, "merge")
    # the agent's proposal waits for a human, so the task is not finished
    assert held.status == "claimed" and held.result["awaiting_review"] is True
    assert {await memory_status(app, i) for i in ids} == {"active"}
    proposal = RecordId.parse(held.result["proposal"])
    await app.memory.reject(human, proposal, "not the same")
    again = await app.tasks.detect(human)
    assert again.reopened == 1
    reopened = await app.tasks.get(human, task.id)
    assert reopened.status == "pending" and reopened.result["outcome"] == "proposal_rejected"
    # a second try, approved this time
    held = await app.tasks.resolve(agent, task.id, "merge")
    await app.memory.approve(human, RecordId.parse(held.result["proposal"]))
    closed = await app.tasks.detect(human)
    assert closed.closed == 1
    final = (await app.tasks.get(human, task.id)).result
    assert final["outcome"] == "obsolete" and final["awaiting_review"] is False


# ---- feedback and expiry ---------------------------------------------------------------------


async def add_with(app: Application, who: Caller, text: str, **fields: object) -> RecordId:
    body = {"text": text, "strength": "should", "rule": text, **fields}
    result = await app.memory.submit(
        who, ProposalRequest(ops=[AddMemory(kind="directive", ref="m", fields=body)])
    )
    assert result.status == "committed", result.findings
    return result.created["m"]


async def test_outdated_feedback_queues_one_task_per_revision(make_app, agent: Caller) -> None:
    from ontyra.models.feedback import FeedbackRequest

    app: Application = await make_app()
    memory = await add(app, agent, "Deploy on Fridays only with approval.")
    await app.feedback.record(
        agent, FeedbackRequest(memory=memory, query="can I deploy today", label="outdated")
    )
    await app.feedback.record(
        agent, FeedbackRequest(memory=memory, query="friday deploy", label="wrong")
    )
    await app.feedback.record(
        agent, FeedbackRequest(memory=memory, query="deploy", label="helpful")
    )  # praise queues nothing
    (task,) = await app.tasks.list(agent)
    assert task.kind == "review_feedback" and task.target == memory
    assert task.card["count"] == 2 and task.card["text"].startswith("Deploy on Fridays")
    assert {r["label"] for r in task.card["reports"]} == {"outdated", "wrong"}
    assert task.card["choices"] == ["retract", "revise", "keep", "defer"]


async def test_resolving_a_report_with_retract_retracts_the_memory(make_app, agent: Caller) -> None:
    from ontyra.models.feedback import FeedbackRequest

    app: Application = await make_app()
    memory = await add(app, agent, "Deploy on Fridays only with approval.")
    await app.feedback.record(agent, FeedbackRequest(memory=memory, query="deploy", label="wrong"))
    (task,) = await app.tasks.list(agent)
    done = await app.tasks.resolve(agent, task.id, "retract")
    assert done.status == "done" and done.result["decision"] == "retract"
    assert await memory_status(app, memory) == "retracted"


async def test_keep_is_remembered_and_later_reports_do_not_reopen_it(
    make_app, agent: Caller
) -> None:
    from ontyra.models.feedback import FeedbackRequest

    app: Application = await make_app()
    memory = await add(app, agent, "Deploy on Fridays only with approval.")
    report = FeedbackRequest(memory=memory, query="deploy", label="outdated")
    await app.feedback.record(agent, report)
    (task,) = await app.tasks.list(agent)
    await app.tasks.complete(agent, task.id, TaskResult(decision="keep", note="still true"))
    await app.feedback.record(agent, report.model_copy(update={"query": "deploy again"}))
    assert await app.tasks.list(agent) == []
    assert (await app.tasks.get(agent, task.id)).status == "done"


async def test_expiring_and_expired_memories_are_queued(make_app, agent: Caller, clock) -> None:
    from datetime import timedelta

    app: Application = await make_app()
    now = clock()
    soon = await add_with(
        app, agent, "Use the temporary VPN.", expires_at=(now + timedelta(days=5)).isoformat()
    )
    gone = await add_with(
        app, agent, "Use the old VPN.", expires_at=(now + timedelta(seconds=30)).isoformat()
    )
    await add_with(
        app, agent, "Use the VPN later.", expires_at=(now + timedelta(days=90)).isoformat()
    )
    await add_with(app, agent, "No expiry at all.")
    clock._t += timedelta(minutes=5)  # type: ignore[attr-defined]  # the old VPN has now expired
    report = await app.tasks.detect(agent)
    assert {t.target for t in report.created} == {soon, gone}
    states = {t.target: t.card["state"] for t in report.created}
    assert states == {soon: "expiring", gone: "expired"}
    assert all(t.kind == "expire_check" for t in report.created)
    assert (await app.tasks.detect(agent)).created == []  # once
    task = next(t for t in report.created if t.target == gone)
    assert (await app.tasks.resolve(agent, task.id, "retract")).status == "done"
    assert await memory_status(app, gone) == "retracted"


async def test_extending_a_memory_closes_its_expiry_task(make_app, agent: Caller, clock) -> None:
    from datetime import timedelta

    from ontyra.models.proposals import ReviseMemory

    app: Application = await make_app()
    soon = await add_with(
        app, agent, "Use the temporary VPN.", expires_at=(clock() + timedelta(days=5)).isoformat()
    )
    (task,) = (await app.tasks.detect(agent)).created
    revised = await app.memory.submit(
        agent,
        ProposalRequest(
            ops=[
                ReviseMemory(
                    memory=soon,
                    fields={"expires_at": None},
                    reason_code="refined",
                )
            ]
        ),
    )
    assert revised.status == "committed", revised.findings
    assert (await app.tasks.detect(agent)).closed == 1
    assert (await app.tasks.get(agent, task.id)).result["outcome"] == "obsolete"


async def test_a_task_cannot_be_closed_with_an_unrelated_proposal(world, agent: Caller) -> None:
    app, ids = world
    await app.tasks.detect(agent)
    task = by_kind(await app.tasks.list(agent), "consolidate")  # about a and b
    await app.tasks.claim(agent, task.id)
    elsewhere = await app.memory.submit(
        agent,
        ProposalRequest(
            ops=[SupersedeMemory(new=ids["new"], target=ids["old"], reason_code="outdated")]
        ),
    )
    assert elsewhere.status == "committed", elsewhere.findings
    with pytest.raises(errors.InvalidRequestError, match="does not touch"):
        await app.tasks.complete(
            agent, task.id, TaskResult(decision="merge", proposal=elsewhere.proposal)
        )
