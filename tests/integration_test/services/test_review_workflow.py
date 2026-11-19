"""The review workflow (plans/24): amend, withdraw, request changes, per-op decisions, and
waiting proposals seen by new writes."""

from __future__ import annotations

import pytest

from ontyra.app import Application
from ontyra.models import RecordId
from ontyra.models.core import Task
from ontyra.models.proposals import (
    AddMemory,
    AmendRequest,
    ApproveRequest,
    LinkMemories,
    ProposalRequest,
    RequestChanges,
    WithdrawRequest,
)
from ontyra.services.context import Caller
from ontyra.services.errors import (
    ConflictError,
    InvalidRequestError,
    PermissionDeniedError,
    ProposalStateError,
)


def add(text: str, ref: str | None = None) -> AddMemory:
    return AddMemory(
        kind="directive",
        ref=ref,
        fields={"text": text, "strength": "should", "rule": text, "scope": "universal"},
    )


def request(*ops: AddMemory | LinkMemories) -> ProposalRequest:
    return ProposalRequest(ops=list(ops))


async def agent_of(app: Application, name: str = "claude") -> Caller:
    return await app.actors.caller(kind="agent", name=name)


async def test_amend_makes_a_new_version_and_keeps_the_old_one(
    review_app: Application, human: Caller
) -> None:
    agent = await agent_of(review_app)
    first = await review_app.memory.submit(agent, request(add("run the linter", "a")))
    amended = await review_app.memory.amend(
        agent,
        first.proposal,
        AmendRequest(ops=[add("run the linter before every commit", "a")], why="more precise"),
    )
    assert amended.status == "pending" and amended.version == 2
    stored = await review_app.memory.get_proposal(agent, first.proposal)
    assert stored.version == 2 and stored.history is not None and len(stored.history) == 1
    assert stored.history[0]["version"] == 1 and "linter" in str(stored.history[0]["ops"])
    approved = await review_app.memory.approve(human, first.proposal)
    view = await review_app.memory.get_memory(agent, approved.created["a"])
    assert view.text == "run the linter before every commit"


async def test_a_stale_version_is_refused_and_only_the_submitter_or_a_reviewer_may_amend(
    review_app: Application, human: Caller
) -> None:
    agent, other = await agent_of(review_app), await agent_of(review_app, "other")
    first = await review_app.memory.submit(agent, request(add("keep notes short")))
    change = AmendRequest(ops=[add("keep notes very short")], why="tighter")
    with pytest.raises(PermissionDeniedError):
        await review_app.memory.amend(other, first.proposal, change)
    await review_app.memory.amend(agent, first.proposal, change)
    with pytest.raises(ConflictError):
        await review_app.memory.amend(
            agent, first.proposal, change.model_copy(update={"expect_version": 1})
        )
    with pytest.raises(ConflictError):
        await review_app.memory.approve(human, first.proposal, ApproveRequest(expect_version=1))
    by_reviewer = await review_app.memory.amend(
        human, first.proposal, AmendRequest(ops=[add("keep notes short")], why="fix wording")
    )
    assert by_reviewer.version == 3


async def test_withdraw_is_final_and_closes_the_task(
    review_app: Application, human: Caller
) -> None:
    agent = await agent_of(review_app)
    first = await review_app.memory.submit(agent, request(add("never mind")))
    done = await review_app.memory.withdraw(agent, first.proposal, WithdrawRequest(reason="oops"))
    assert done.status == "withdrawn"
    task = (await review_app.memory.store.select("task", {"kind": "review_proposal"}))[0]
    assert isinstance(task, Task) and task.status == "done"
    assert await review_app.memory.pending(human) == []
    for call in (
        review_app.memory.approve(human, first.proposal),
        review_app.memory.amend(agent, first.proposal, AmendRequest(ops=[add("x")], why="y")),
    ):
        with pytest.raises(ProposalStateError):
            await call


async def test_request_changes_round_trip(review_app: Application, human: Caller) -> None:
    agent = await agent_of(review_app)
    first = await review_app.memory.submit(agent, request(add("be nice"), add("be brief")))
    with pytest.raises(PermissionDeniedError):
        await review_app.memory.request_changes(agent, first.proposal, RequestChanges(comment="no"))
    sent = await review_app.memory.request_changes(
        human, first.proposal, RequestChanges(comment="too vague", op_comments={0: "which tone?"})
    )
    assert sent.status == "changes_requested"
    mine = await review_app.memory.pending(agent)
    assert [p.id for p in mine] == [first.proposal]
    assert mine[0].status == "changes_requested"
    assert [c["text"] for c in mine[0].comments or []] == ["too vague", "which tone?"]
    with pytest.raises(InvalidRequestError):
        await review_app.memory.request_changes(
            human, first.proposal, RequestChanges(comment="x", op_comments={9: "nope"})
        )
    back = await review_app.memory.amend(
        agent,
        first.proposal,
        AmendRequest(ops=[add("answer in a friendly tone")], why="made it specific"),
    )
    assert back.status == "pending" and back.version == 2


async def test_partial_approval_commits_the_chosen_ops_and_what_they_need(
    review_app: Application, human: Caller
) -> None:
    agent = await agent_of(review_app)
    ops = (
        add("use tabs in makefiles", "a"),
        add("prefer small commits", "b"),
        LinkMemories(relation="refines", source="b", target="a"),
    )
    first = await review_app.memory.submit(agent, request(*ops))
    with pytest.raises(InvalidRequestError):  # the others need a reason
        await review_app.memory.approve(human, first.proposal, ApproveRequest(ops=[0]))
    done = await review_app.memory.approve(
        human, first.proposal, ApproveRequest(ops=[0], reason="the second is a style opinion")
    )
    assert done.status == "committed" and done.rejected_ops == [1, 2]
    assert set(done.created) == {"a"}
    stored = await review_app.memory.get_proposal(agent, first.proposal)
    states = {d["op"]: d["status"] for d in stored.decisions or []}
    assert states == {0: "approved", 1: "rejected", 2: "rejected"}


async def test_approving_a_dependent_op_brings_the_ops_it_needs(
    review_app: Application, human: Caller
) -> None:
    agent = await agent_of(review_app)
    first = await review_app.memory.submit(
        agent,
        request(
            add("use tabs in makefiles", "a"),
            add("prefer small commits", "b"),
            LinkMemories(relation="refines", source="b", target="a"),
        ),
    )
    done = await review_app.memory.approve(human, first.proposal, ApproveRequest(ops=[2]))
    assert done.rejected_ops == [] and set(done.created) == {"a", "b"}
    assert "also op(s)" in (done.reason or "")


async def test_rejecting_one_op_rejects_what_needs_it_and_the_rest_keeps_waiting(
    review_app: Application, human: Caller
) -> None:
    agent = await agent_of(review_app)
    first = await review_app.memory.submit(
        agent,
        request(
            add("use tabs in makefiles", "a"),
            add("prefer small commits", "b"),
            LinkMemories(relation="refines", source="b", target="a"),
        ),
    )
    result = await review_app.memory.reject(human, first.proposal, "wrong about tabs", ops=[0])
    assert result.status == "pending" and result.rejected_ops == [0, 2] and result.version == 2
    stored = await review_app.memory.get_proposal(agent, first.proposal)
    assert len(stored.ops) == 1 and "small commits" in str(stored.ops[0])
    assert stored.history is not None and len(stored.history[0]["ops"]) == 3
    done = await review_app.memory.approve(human, first.proposal)
    assert done.status == "committed" and set(done.created) == {"b"}
    # rejecting everything that is left ends the proposal
    second = await review_app.memory.submit(agent, request(add("only one")))
    ended = await review_app.memory.reject(human, second.proposal, "no", ops=[0])
    assert ended.status == "rejected"


async def test_a_new_write_is_told_about_a_similar_waiting_proposal(
    review_app: Application,
) -> None:
    agent = await agent_of(review_app)
    first = await review_app.memory.submit(
        agent, request(add("always run the formatter before committing"))
    )
    second = await review_app.memory.submit(
        agent, request(add("always run the formatter before you commit"))
    )
    codes = {f.code: f for f in second.findings}
    assert "pending_similar" in codes
    assert codes["pending_similar"].data["proposal"] == str(first.proposal)
    # amending the first with the same text is not compared with itself
    amended = await review_app.memory.amend(
        agent,
        first.proposal,
        AmendRequest(ops=[add("always run the formatter before committing")], why="same"),
    )
    assert [f.data.get("proposal") for f in amended.findings if f.code == "pending_similar"] == [
        str(second.proposal)
    ]


async def test_a_second_change_to_the_same_memory_is_flagged(
    app: Application, review_app: Application, human: Caller, remember
) -> None:
    from ontyra.models.proposals import ReviseMemory

    memory = await remember("directive", "run the linter")
    agent = await agent_of(review_app)
    first = await review_app.memory.submit(
        agent,
        ProposalRequest(ops=[ReviseMemory(memory=memory, fields={"rule": "run ruff"}, reason="x")]),
    )
    second = await review_app.memory.submit(
        agent,
        ProposalRequest(ops=[ReviseMemory(memory=memory, fields={"rule": "run flake8"})]),
    )
    conflict = [f for f in second.findings if f.code == "pending_conflict"]
    assert conflict and conflict[0].data["proposal"] == str(first.proposal)


async def test_waiting_proposals_of_another_owner_are_not_visible(
    review_app: Application,
) -> None:
    agent = await agent_of(review_app)
    await review_app.memory.submit(agent, request(add("only my space knows this rule")))
    stranger = await review_app.actors.caller(
        kind="agent",
        name="stranger",
        owner=RecordId("owner", "someone-else"),
        permissions=frozenset(),
    )
    result = await review_app.memory.submit(stranger, request(add("only my space knows this rule")))
    assert not [f for f in result.findings if f.code.startswith("pending_")]
