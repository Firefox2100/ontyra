"""The propose → validate → commit service against a real database (embedded and server)."""

import asyncio
from typing import Any

import pytest

from ontyra.app import Application
from ontyra.models import PolicyConfig, PolicyRule, RecordId
from ontyra.models.core import Annotation, Task
from ontyra.models.proposals import ProposalRequest
from ontyra.services import Caller
from ontyra.services.errors import (
    ConflictError,
    InvalidProposalError,
    NotFoundError,
    ProposalStateError,
)


def req(*ops: dict[str, Any], key: str | None = None) -> ProposalRequest:
    return ProposalRequest.model_validate({"ops": list(ops), "idempotency_key": key})


def add_directive(text: str, *, ref: str | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "op": "add",
        "ref": ref,
        "kind": "directive",
        "fields": {"text": text, "strength": "must", "rule": text, **extra},
    }


def add_fact(text: str, *, ref: str | None = None) -> dict[str, Any]:
    return {
        "op": "add",
        "ref": ref,
        "kind": "fact",
        "fields": {
            "text": text,
            "subject": "concept:project",
            "predicate": "has_port",
            "object": "8443",
        },
    }


async def commit(app: Application, caller: Caller, *ops: dict[str, Any]) -> dict[str, RecordId]:
    result = await app.memory.submit(caller, req(*ops))
    assert result.status == "committed", result.findings
    return result.created


# ---- adding ---------------------------------------------------------------------------


async def test_auto_commit_creates_a_readable_memory(app: Application, agent: Caller) -> None:
    created = await commit(app, agent, add_directive("use pytest, not unittest", ref="d"))
    view = await app.memory.get_memory(agent, created["d"])
    assert (view.kind, view.status, view.state, view.lifecycle) == (
        "directive",
        "active",
        "committed",
        "active",
    )
    assert view.text == "use pytest, not unittest" and view.seq == 1
    assert view.content["strength"] == "must" and view.content["polarity"] == "do"


async def test_every_kind_can_be_added_through_the_service(app: Application, agent: Caller) -> None:
    ops = [
        add_fact("a fact"),
        add_directive("a directive"),
        {"op": "add", "kind": "decision", "fields": {
            "text": "chose SurrealDB", "chosen": "SurrealDB", "rationale": "graph+vector",
            "decided_at": "2026-11-01T00:00:00Z"}},
        {"op": "add", "kind": "event", "fields": {
            "text": "migrated", "occurred_at": "2026-11-02T00:00:00Z"}},
        {"op": "add", "kind": "procedure", "fields": {
            "text": "release", "trigger": "release", "steps": [{"text": "tag"}, {"text": "push"}]}},
        {"op": "add", "kind": "task", "fields": {"text": "rotate keys"}},
        {"op": "add", "kind": "lesson", "fields": {
            "text": "pin versions", "situation": "build broke", "insight": "unpinned dep"}},
    ]  # fmt: skip
    created = await commit(app, agent, *ops)
    assert len(created) == 7
    kinds = sorted(v.kind for v in await app.memory.list_memories(agent, limit=50))
    assert kinds == sorted(
        ["fact", "directive", "decision", "event", "procedure", "task", "lesson"]
    )


async def test_invalid_fields_reject_the_whole_proposal_and_store_nothing(
    app: Application, agent: Caller
) -> None:
    result = await app.memory.submit(
        agent, req(add_fact("fine"), add_directive("bad", strength="always"))
    )
    assert result.status == "rejected" and result.decision == "reject"
    errors = [f for f in result.findings if f.severity == "error"]
    assert errors and errors[0].code == "invalid_field" and errors[0].op == 1
    assert await app.memory.list_memories(agent) == []  # all-or-nothing, incl. the valid op
    stored = await app.memory.get_proposal(agent, result.proposal)
    assert stored.status == "rejected"  # the attempt itself is recorded


async def test_service_owned_fields_cannot_be_set_by_the_agent(
    app: Application, agent: Caller
) -> None:
    result = await app.memory.submit(agent, req(add_directive("x", seq=99)))
    assert result.status == "rejected"
    assert any(f.code == "reserved_field" for f in result.findings)


async def test_idempotency_key_returns_the_original_result(app: Application, agent: Caller) -> None:
    first = await app.memory.submit(agent, req(add_directive("once", ref="d"), key="k1"))
    again = await app.memory.submit(agent, req(add_directive("once", ref="d"), key="k1"))
    assert again.duplicate_of_idempotency_key and again.proposal == first.proposal
    assert again.created == first.created
    assert len(await app.memory.list_memories(agent)) == 1


# ---- review flow ----------------------------------------------------------------------


async def test_default_policy_holds_proposals_for_review(
    review_app: Application, human: Caller
) -> None:
    agent = await review_app.actors.caller(kind="agent", name="claude")
    result = await review_app.memory.submit(agent, req(add_directive("wait for me")))
    assert (result.status, result.decision) == ("pending", "review")
    assert await review_app.memory.list_memories(agent) == []
    pending = await review_app.memory.pending(human)
    assert [p.id for p in pending] == [result.proposal]
    tasks = await review_app.memory.store.select("task", {"kind": "review_proposal"})
    assert len(tasks) == 1 and isinstance(tasks[0], Task) and tasks[0].status == "pending"


async def test_approve_commits_and_closes_the_review_task(
    review_app: Application, human: Caller
) -> None:
    agent = await review_app.actors.caller(kind="agent", name="claude")
    submitted = await review_app.memory.submit(agent, req(add_directive("approved", ref="d")))
    approved = await review_app.memory.approve(human, submitted.proposal)
    assert approved.status == "committed"
    view = await review_app.memory.get_memory(agent, approved.created["d"])
    assert view.text == "approved"
    assert await review_app.memory.pending(human) == []
    task = (await review_app.memory.store.select("task", {"kind": "review_proposal"}))[0]
    assert isinstance(task, Task) and task.status == "done"
    with pytest.raises(ProposalStateError):
        await review_app.memory.approve(human, submitted.proposal)


async def test_reject_discards_without_creating_anything(
    review_app: Application, human: Caller
) -> None:
    agent = await review_app.actors.caller(kind="agent", name="claude")
    submitted = await review_app.memory.submit(agent, req(add_directive("no")))
    rejected = await review_app.memory.reject(human, submitted.proposal, "not wanted")
    assert rejected.status == "rejected" and rejected.reason == "not wanted"
    assert await review_app.memory.list_memories(agent) == []
    assert (
        await review_app.memory.get_proposal(human, submitted.proposal)
    ).decided_by == human.actor_id


async def test_approval_revalidates_against_the_current_data(
    review_app: Application, human: Caller
) -> None:
    agent = await review_app.actors.caller(kind="agent", name="claude")
    # a memory exists; a revise proposal waits; meanwhile the memory is retracted
    first = await review_app.memory.submit(agent, req(add_directive("v1", ref="d")))
    mem = (await review_app.memory.approve(human, first.proposal)).created["d"]
    revise = await review_app.memory.submit(
        agent, req({"op": "revise", "memory": str(mem), "fields": {"text": "v2", "rule": "v2"}})
    )
    retract = await review_app.memory.submit(
        agent, req({"op": "retract", "memory": str(mem), "reason": "wrong"})
    )
    await review_app.memory.approve(human, retract.proposal)
    with pytest.raises(InvalidProposalError) as err:
        await review_app.memory.approve(human, revise.proposal)
    assert any(f.code == "not_active" for f in err.value.findings)


async def test_policy_rules_split_by_actor_kind_and_memory_kind(make_app) -> None:
    policy = PolicyConfig(
        default="review",
        rules=[
            PolicyRule(actor_kinds=["human"], decision="auto_commit"),
            PolicyRule(kinds=["fact", "event"], min_trust=0.5, decision="auto_commit"),
        ],
    )
    app = await make_app(policy)
    agent = await app.actors.caller(kind="agent", name="claude")
    human = await app.actors.caller(kind="human", name="owner")
    assert (await app.memory.submit(human, req(add_directive("by human")))).status == "committed"
    assert (await app.memory.submit(agent, req(add_fact("by agent")))).status == "committed"
    assert (await app.memory.submit(agent, req(add_directive("by agent")))).status == "pending"
    mixed = await app.memory.submit(agent, req(add_fact("f2"), add_directive("d2")))
    assert mixed.status == "pending"  # a rule matches only if *every* op fits it


# ---- revising, superseding, retracting ----------------------------------------------


async def test_revise_appends_a_revision_and_keeps_the_old_one_intact(
    app: Application, agent: Caller
) -> None:
    mem = (await commit(app, agent, add_directive("v1", ref="d")))["d"]
    await commit(
        app,
        agent,
        {
            "op": "revise", "memory": str(mem), "fields": {"text": "v2", "rule": "v2"},
            "reason_code": "corrected", "reason": "typo",
        },
    )  # fmt: skip
    view = await app.memory.get_memory(agent, mem)
    assert (view.seq, view.text) == (2, "v2")
    history = await app.memory.history(agent, mem)
    first, second = history.revisions
    assert (first.text, first.lifecycle, first.superseded_by) == (
        "v1",
        "superseded",
        [second.revision],
    )
    assert first.valid_to is not None and second.valid_to is None and second.lifecycle == "active"
    assert first.reasons[0].reason_code == "corrected" and first.reasons[0].reason == "typo"


async def test_revise_unchanged_content_is_an_error(app: Application, agent: Caller) -> None:
    mem = (await commit(app, agent, add_directive("same", ref="d")))["d"]
    result = await app.memory.submit(
        agent, req({"op": "revise", "memory": str(mem), "fields": {"text": "same"}})
    )
    assert result.status == "rejected" and any(f.code == "no_change" for f in result.findings)


async def test_supersede_with_a_new_memory_in_the_same_proposal(
    app: Application, agent: Caller
) -> None:
    old = (await commit(app, agent, add_fact("port is 8000", ref="f")))["f"]
    created = await commit(
        app,
        agent,
        add_fact("port is 8443", ref="new"),
        {"op": "supersede", "new": "new", "target": str(old), "reason_code": "outdated",
         "reason": "moved behind TLS"},
    )  # fmt: skip
    active = await app.memory.list_memories(agent)
    assert [v.memory for v in active] == [created["new"]]  # superseded ones are not current
    gone = await app.memory.get_memory(agent, old)
    assert (gone.status, gone.lifecycle) == ("superseded", "superseded")
    edge = (await app.memory.history(agent, old)).revisions[0]
    assert edge.reasons[0].reason_code == "outdated"
    assert edge.superseded_by == [(await app.memory.get_memory(agent, created["new"])).revision]


async def test_supersede_works_across_kinds(app: Application, agent: Caller) -> None:
    old = (await commit(app, agent, add_directive("use unittest", ref="d")))["d"]
    await commit(
        app,
        agent,
        add_fact("pytest replaced unittest here", ref="f"),
        {"op": "supersede", "new": "f", "target": str(old), "reason_code": "refined"},
    )
    assert (await app.memory.get_memory(agent, old)).status == "superseded"


async def test_a_memory_cannot_be_targeted_twice_or_supersede_itself(
    app: Application, agent: Caller
) -> None:
    mem = (await commit(app, agent, add_directive("x", ref="d")))["d"]
    twice = await app.memory.submit(
        agent,
        req(
            {"op": "revise", "memory": str(mem), "fields": {"text": "y", "rule": "y"}},
            {"op": "retract", "memory": str(mem), "reason": "r"},
        ),
    )
    assert any(f.code == "duplicate_target" for f in twice.findings)
    me = await app.memory.submit(
        agent,
        req({"op": "supersede", "new": str(mem), "target": str(mem), "reason_code": "refined"}),
    )
    assert any(f.code == "self_supersede" for f in me.findings)


async def test_retract_hides_the_memory_and_flags_dependents_stale(
    app: Application, agent: Caller
) -> None:
    created = await commit(app, agent, add_fact("source fact", ref="src"))
    derived = await commit(
        app,
        agent,
        {"op": "add", "ref": "sum", "kind": "event",
         "fields": {"text": "summary built from the fact", "occurred_at": "2026-11-01T00:00:00Z"},
         "derived_from": [str(created["src"])]},
    )  # fmt: skip
    result = await app.memory.submit(
        agent, req({"op": "retract", "memory": str(created["src"]), "reason": "was a tool error"})
    )
    assert result.status == "committed"
    assert any(f.code == "has_dependents" for f in result.findings)
    assert [v.memory for v in await app.memory.list_memories(agent)] == [derived["sum"]]
    assert (await app.memory.get_memory(agent, created["src"])).status == "retracted"
    dependent = await app.memory.get_memory(agent, derived["sum"])
    assert dependent.stale is True
    tasks = await app.memory.store.select("task", {"kind": "review_stale_dependent"})
    assert (
        len(tasks) == 1
        and isinstance(tasks[0], Task)
        and tasks[0].payload["reason"] == "was a tool error"
    )


# ---- links, annotations, evidence -------------------------------------------------------


async def test_link_and_annotate(app: Application, agent: Caller) -> None:
    created = await commit(app, agent, add_directive("a", ref="a"), add_directive("b", ref="b"))
    await commit(
        app,
        agent,
        {"op": "link", "relation": "contradicts", "source": str(created["a"]), "target": str(created["b"]),
         "reason": "disagree about unittest"},
        {"op": "annotate", "target": str(created["a"]), "kind": "flag", "text": "needs review"},
    )  # fmt: skip
    edges = await app.memory.store.edges("contradicts")
    assert len(edges) == 1 and edges[0]["reason"] == "disagree about unittest"
    notes = await app.memory.store.select("annotation")
    assert len(notes) == 1 and isinstance(notes[0], Annotation) and notes[0].text == "needs review"


async def test_promoted_to_must_go_from_a_lesson_to_a_directive_or_procedure(
    app: Application, agent: Caller
) -> None:
    created = await commit(
        app,
        agent,
        {"op": "add", "ref": "l", "kind": "lesson",
         "fields": {"text": "pin deps", "situation": "s", "insight": "i"}},
        add_directive("pin deps", ref="d"),
        add_fact("a fact", ref="f"),
    )  # fmt: skip
    bad = await app.memory.submit(
        agent,
        req(
            {
                "op": "link",
                "relation": "promoted_to",
                "source": str(created["f"]),
                "target": str(created["d"]),
            }
        ),
    )
    assert any(f.code == "bad_link" for f in bad.findings)
    await commit(
        app, agent,
        {"op": "link", "relation": "promoted_to", "source": str(created["l"]), "target": str(created["d"])},
    )  # fmt: skip
    assert len(await app.memory.store.edges("promoted_to")) == 1


async def test_quoted_evidence_must_occur_in_the_episode(app: Application, agent: Caller) -> None:
    ep = await app.memory.record_episode(
        agent, session="s1", role="user", content="Please always use  pytest\nfor tests."
    )
    ok = await app.memory.submit(
        agent,
        req(
            {**add_directive("use pytest", ref="d"),
             "evidence": [{"episode": str(ep), "quote": "ALWAYS use pytest for tests"}]}
        ),  # whitespace/case-insensitive
    )  # fmt: skip
    assert ok.status == "committed"
    rev = (await app.memory.get_memory(agent, ok.created["d"])).revision
    edges = await app.memory.store.edges("derived_from", source=rev)
    assert [e["out"] for e in edges] == [ep]
    bad = await app.memory.submit(
        agent,
        req(
            {
                **add_directive("never use unittest"),
                "evidence": [{"episode": str(ep), "quote": "never"}],
            }
        ),
    )
    assert bad.status == "rejected" and any(f.code == "grounding_failed" for f in bad.findings)
    missing = await app.memory.submit(
        agent, req({**add_directive("z"), "evidence": [{"episode": "episode:nope"}]})
    )
    assert any(f.code == "unknown_episode" for f in missing.findings)


async def test_similar_and_duplicate_memories_are_reported_as_advice(
    app: Application, agent: Caller
) -> None:
    first = await commit(app, agent, add_directive("Use pytest instead of unittest", ref="d"))
    dup = await app.memory.submit(agent, req(add_directive("use pytest instead of unittest")))
    assert dup.status == "committed"  # advice, not an error
    flagged = [f for f in dup.findings if f.code == "possible_duplicate"]
    assert flagged and flagged[0].data["memory"] == str(first["d"])
    related = await app.memory.submit(
        agent, req(add_directive("use pytest instead of unittest for tests"))
    )
    assert any(f.code == "similar_memory" for f in related.findings)
    unrelated = await app.memory.submit(agent, req(add_directive("serial ports live in /dev")))
    assert not [f for f in unrelated.findings if f.code in ("similar_memory", "possible_duplicate")]


# ---- visibility and ownership ----------------------------------------------------------


async def test_other_owners_memories_are_invisible_and_untouchable(
    app: Application, agent: Caller
) -> None:
    other_owner = await app.actors.ensure_owner("user", "alice")
    alice = await app.actors.caller(kind="human", name="alice", owner=other_owner)
    mem = (await commit(app, alice, add_directive("alice only", ref="d")))["d"]
    with pytest.raises(NotFoundError):
        await app.memory.get_memory(agent, mem)
    assert await app.memory.list_memories(agent) == []
    result = await app.memory.submit(
        agent, req({"op": "retract", "memory": str(mem), "reason": "not yours"})
    )
    assert result.status == "rejected" and any(f.code == "unknown_memory" for f in result.findings)


async def test_global_memories_are_visible_to_everyone_but_only_the_owner_may_change_them(
    app: Application, agent: Caller
) -> None:
    mem = (await commit(app, agent, add_directive("global rule", ref="d")))["d"]
    other_owner = await app.actors.ensure_owner("user", "alice")
    alice = await app.actors.caller(kind="human", name="alice", owner=other_owner)
    assert (await app.memory.get_memory(alice, mem)).text == "global rule"
    result = await app.memory.submit(
        alice, req({"op": "retract", "memory": str(mem), "reason": "mine now"})
    )
    assert result.status == "rejected" and any(
        f.code == "permission_denied" for f in result.findings
    )


# ---- concurrency ---------------------------------------------------------------------


async def test_concurrent_revisions_never_corrupt_the_history(
    app: Application, agent: Caller
) -> None:
    mem = (await commit(app, agent, add_directive("v0", ref="d")))["d"]

    async def revise(n: int) -> str:
        try:
            r = await app.memory.submit(
                agent,
                req(
                    {
                        "op": "revise",
                        "memory": str(mem),
                        "fields": {"text": f"v{n}", "rule": f"v{n}"},
                    }
                ),
            )
            return r.status
        except ConflictError:
            return "conflict"

    outcomes = await asyncio.gather(*(revise(n) for n in range(1, 6)))
    committed = outcomes.count("committed")
    assert committed >= 1 and set(outcomes) <= {"committed", "conflict", "rejected"}
    history = await app.memory.history(agent, mem)
    revs = history.revisions
    assert len(revs) == 1 + committed  # one revision per success, none lost or duplicated
    assert [r.seq for r in revs] == list(range(1, len(revs) + 1))
    assert [r.lifecycle for r in revs[:-1]] == ["superseded"] * (len(revs) - 1)
    assert revs[-1].lifecycle == "active"
    assert (await app.memory.get_memory(agent, mem)).revision == revs[-1].revision


async def test_a_memory_can_be_linked_in_the_proposal_that_adds_it(
    app: Application, agent: Caller, remember
) -> None:
    from ontyra.models.proposals import AddMemory, LinkMemories, ProposalRequest

    old = await remember("directive", "pay suppliers within 30 days", strength="must")
    result = await app.memory.submit(
        agent,
        ProposalRequest(
            ops=[
                AddMemory(
                    kind="directive",
                    ref="new",
                    fields={"text": "pay suppliers on day 45", "strength": "must", "rule": "x"},
                ),
                LinkMemories(relation="contradicts", source="new", target=old),
            ]
        ),
    )
    assert result.status == "committed", result.findings
