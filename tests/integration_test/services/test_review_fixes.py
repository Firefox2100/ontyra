"""Regressions found by the first full code review (services over a real database)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from ontyra.app import Application
from ontyra.models import RecordId
from ontyra.models.ontology import ConceptDefinition, RelationRequest
from ontyra.models.ontology_change import ChangeRequest, DefineChange, EditChange, MergeChange
from ontyra.models.proposals import AddMemory, ProposalRequest, RetractMemory, SupersedeMemory
from ontyra.models.recall import RecallContext, RecallRequest, RecallSettings
from ontyra.services import Caller, errors


def add(text: str, ref: str | None = None, **extra: object) -> AddMemory:
    return AddMemory(
        kind="directive",
        ref=ref,
        fields={"text": text, "strength": "should", "rule": text, "scope": "universal", **extra},
    )


async def test_an_actor_row_cannot_lend_its_kind_to_another_caller(app: Application) -> None:
    human = await app.actors.caller(kind="human", name="owner")
    assert human.actor.kind == "human"
    same_name = await app.actors.caller(kind="agent", name="owner")  # e.g. single-token mode
    assert same_name.actor.kind == "agent" and same_name.actor.trust < human.actor.trust
    assert not same_name.can_review(same_name.owner)


async def test_trust_follows_the_configuration_not_the_row_made_earlier(make_app) -> None:
    app = await make_app()
    first = await app.actors.caller(kind="agent", name="claude")
    app.actors.trust["agent"] = 0.95
    later = await app.actors.caller(kind="agent", name="claude")
    assert later.actor.trust == 0.95 != first.actor.trust


async def test_a_reissued_credential_name_gets_its_own_actor(app: Application) -> None:
    one = await app.actors.ensure_actor("agent", "alice.codex", rid=RecordId("actor", "cred1"))
    two = await app.actors.ensure_actor("agent", "alice.codex", rid=RecordId("actor", "cred2"))
    assert one.name == "alice.codex" and two.name.startswith("alice.codex#")


async def test_derived_from_may_name_a_memory_added_in_the_same_proposal(
    app: Application, agent: Caller
) -> None:
    derived = AddMemory.model_validate(
        {
            "op": "add", "kind": "directive", "ref": "sum",
            "fields": {"text": "derived summary", "strength": "should", "rule": "r", "scope": "universal"},
            "derived_from": ["src"],
        }
    )  # fmt: skip
    result = await app.memory.submit(
        agent, ProposalRequest(ops=[add("source statement", "src"), derived])
    )
    assert result.status == "committed", result.findings


async def test_a_memory_cannot_be_both_the_replacement_and_retracted(
    app: Application, agent: Caller, remember
) -> None:
    old = await remember("directive", "use tabs")
    new = await remember("directive", "use spaces")
    result = await app.memory.submit(
        agent,
        ProposalRequest(
            ops=[
                SupersedeMemory(new=new, target=old, reason_code="corrected"),
                RetractMemory(memory=new, reason="wrong"),
            ]
        ),
    )
    assert result.status == "rejected"
    assert any(f.code == "duplicate_target" for f in result.findings)


async def test_the_episode_sweep_spares_what_a_waiting_proposal_cites(make_app, clock) -> None:
    from ontyra.models import PolicyConfig

    app = await make_app(
        PolicyConfig(), episodes={"retention_days": 1, "prune_interval_minutes": 0}
    )
    agent = await app.actors.caller(kind="agent", name="claude")
    cited = await app.memory.record_episode(
        agent, session="s", role="user", content="always sign commits"
    )
    other = await app.memory.record_episode(
        agent, session="s", role="user", content="unrelated chatter"
    )
    held = await app.memory.submit(
        agent,
        ProposalRequest(
            ops=[
                AddMemory.model_validate(
                    {
                        "op": "add", "kind": "directive",
                        "fields": {"text": "sign commits", "strength": "should", "rule": "sign", "scope": "universal"},
                        "evidence": [{"quote": "always sign commits"}],
                    }
                )
            ]
        ),
    )  # fmt: skip
    assert held.status == "pending"
    clock._t += timedelta(days=3)
    await app.memory.record_episode(agent, session="s", role="user", content="a new message")
    assert await app.memory.store.get(cited) is not None  # a reviewer may still need it
    assert await app.memory.store.get(other) is None


class AlwaysFiller:
    name = "fake"

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def judge(self, situation, memories):  # type: ignore[no-untyped-def]
        from ontyra.models.decision import Judgement

        return Judgement(scores=[0.5 for _ in memories], none=0.1, model="fake")

    async def filler(self, text: str) -> float:
        self.asked.append(text)
        return 0.99


async def test_the_filler_gate_never_skips_a_check(make_app) -> None:
    app = await make_app(recall=RecallSettings(engine="fusion", filler_gate=True))
    agent = await app.actors.caller(kind="agent", name="claude")
    decider = AlwaysFiller()
    app.recall.decider = decider  # type: ignore[attr-defined]
    await app.recall.check(agent, "ok", RecallContext())
    assert decider.asked == []  # an action is never a pleasantry
    await app.recall.recall(agent, RecallRequest(query="ok"))
    assert decider.asked == ["ok"]  # while an ordinary message still is asked


async def test_the_direct_relate_cannot_say_what_replaced_a_concept(
    app: Application, human: Caller
) -> None:
    for label in ("Alpha", "Beta"):
        await app.ontology.define(human, ConceptDefinition(label=label))
    with pytest.raises(errors.InvalidRequestError):
        await app.ontology.relate(
            human, RelationRequest(source="alpha", relation="split_into", target="beta")
        )
    with pytest.raises(errors.InvalidRequestError):
        await app.ontology.relate(
            human, RelationRequest(source="alpha", relation="replaced_by", target="beta")
        )


async def test_a_direct_define_cannot_reuse_a_name_or_a_deprecated_parent(
    app: Application, human: Caller
) -> None:
    await app.ontology.define(human, ConceptDefinition(label="Postgres", aliases=["pg"]))
    with pytest.raises(errors.InvalidRequestError, match="already names"):
        await app.ontology.define(human, ConceptDefinition(label="Other", aliases=["PG"]))
    await app.ontology.define(human, ConceptDefinition(label="Old"))
    await app.ontology.define(human, ConceptDefinition(label="New"))
    await app.ontology.deprecate(human, RecordId("concept", "old"), RecordId("concept", "new"))
    with pytest.raises(errors.InvalidRequestError, match="deprecated"):
        await app.ontology.define(human, ConceptDefinition(label="Child", is_a=["concept:old"]))
    with pytest.raises(errors.NotFoundError):  # a name with no letters is not a concept
        await app.ontology.resolve("???")


async def test_a_merged_alias_still_resolves_and_the_health_check_accepts_it(
    app: Application, human: Caller
) -> None:
    for label, aliases in (("Ubuntu", []), ("Ubuntu Linux", ["Ubuntu LTS"])):
        result = await app.ontology_changes.propose(
            human,
            ChangeRequest(
                change=DefineChange(concept=ConceptDefinition(label=label, aliases=aliases)),
                rationale="x",
            ),
        )
        assert result.status == "applied"
    await app.ontology_changes.propose(
        human,
        ChangeRequest(change=MergeChange(source="ubuntu linux", into="ubuntu"), rationale="x"),
    )
    assert await app.ontology.resolve("Ubuntu LTS") == RecordId("concept", "ubuntu")
    report = await app.ontology_check.check(human)
    assert not [f for f in report.findings if f.code == "shared_name"]


async def test_a_decision_on_a_change_that_moved_in_the_meantime_is_refused(
    app: Application, agent: Caller, human: Caller
) -> None:
    from ontyra.models.ontology_change import AmendChange

    held = await app.ontology_changes.propose(
        agent,
        ChangeRequest(change=DefineChange(concept=ConceptDefinition(label="Pnpm")), rationale="x"),
    )
    seen = await app.ontology_changes._get(held.id)  # what a reviewer's page showed
    await app.ontology_changes.amend(
        agent,
        held.id,
        AmendChange(
            change=DefineChange(concept=ConceptDefinition(label="Pnpm", definition="a tool")),
            why="more precise",
        ),
    )
    async with app.ontology.lock:
        with pytest.raises(errors.ConflictError, match="changed while"):
            await app.ontology_changes._unchanged(held.id, seen)


async def test_an_edit_keeps_working_after_the_snapshot_race_guard(
    app: Application, human: Caller
) -> None:
    await app.ontology.define(human, ConceptDefinition(label="Linter"))
    done = await app.ontology_changes.propose(
        human,
        ChangeRequest(change=EditChange(concept="linter", label="Static analysis"), rationale="x"),
    )
    assert done.status == "applied"
    assert (await app.ontology.lookup(human, "static analysis"))[0].id == RecordId(
        "concept", "linter"
    )
