"""Ontology change proposals: agents propose, humans decide, applying keeps the ontology sound."""

import pytest

from ontyra.app import Application
from ontyra.models.ontology import ConceptDefinition, RelationRequest
from ontyra.models.ontology_change import (
    AliasChange,
    ChangeRequest,
    DefineChange,
    DeprecateChange,
    MergeChange,
    RelateChange,
)
from ontyra.services import Caller, errors


def req(change, why: str = "seen in the session") -> ChangeRequest:  # type: ignore[no-untyped-def]
    return ChangeRequest(change=change, rationale=why)


async def define(
    app: Application, who: Caller, label: str, aliases: list[str] | None = None
) -> None:
    concept = ConceptDefinition(label=label, aliases=aliases or [])
    await app.ontology_changes.propose(who, req(DefineChange(concept=concept)))


async def test_an_agent_proposal_waits_and_a_human_applies_it(
    app: Application, agent: Caller, human: Caller
) -> None:
    held = await app.ontology_changes.propose(
        agent, req(DefineChange(concept=ConceptDefinition(label="Poetry")))
    )
    assert held.status == "pending"
    assert (await app.ontology.lookup(human, "poetry")) == []
    tasks = await app.ontology_changes.store.select("task", {"kind": "review_ontology_change"})
    assert len(tasks) == 1

    done = await app.ontology_changes.approve(human, held.id)
    assert done.status == "applied"
    assert [c.label for c in await app.ontology.lookup(human, "poetry")] == ["Poetry"]
    assert not await app.ontology_changes.pending(human)
    after = await app.ontology_changes.store.select("task", {"kind": "review_ontology_change"})
    assert after[0].status == "done"  # type: ignore[attr-defined]


async def test_a_human_change_applies_at_once(app: Application, human: Caller) -> None:
    result = await app.ontology_changes.propose(
        human, req(DefineChange(concept=ConceptDefinition(label="Rust")))
    )
    assert result.status == "applied"
    assert await app.ontology.lookup(human, "rust")


async def test_an_agent_cannot_approve_and_a_rejection_keeps_the_ontology(
    app: Application, agent: Caller, human: Caller
) -> None:
    held = await app.ontology_changes.propose(
        agent, req(DefineChange(concept=ConceptDefinition(label="Nonsense")))
    )
    with pytest.raises(errors.InvalidRequestError):
        await app.ontology_changes.approve(agent, held.id)
    rejected = await app.ontology_changes.reject(human, held.id, "not a thing")
    assert rejected.status == "rejected"
    assert await app.ontology.lookup(human, "nonsense") == []
    with pytest.raises(errors.ProposalStateError):
        await app.ontology_changes.approve(human, held.id)


async def test_a_duplicate_label_or_alias_is_refused(
    app: Application, agent: Caller, human: Caller
) -> None:
    await define(app, human, "Python", aliases=["py"])
    with pytest.raises(errors.InvalidRequestError, match="already"):
        await app.ontology_changes.propose(
            agent, req(DefineChange(concept=ConceptDefinition(label="py")))
        )
    with pytest.raises(errors.InvalidRequestError, match="already"):
        await app.ontology_changes.propose(
            agent, req(DefineChange(concept=ConceptDefinition(label="PYTHON")))
        )


async def test_similar_concepts_are_advice_not_an_error(
    app: Application, agent: Caller, human: Caller
) -> None:
    await define(app, human, "Docker Compose")
    result = await app.ontology_changes.propose(
        agent, req(DefineChange(concept=ConceptDefinition(label="Docker Compose V2")))
    )
    assert result.status == "pending"
    assert any("docker compose" in a for a in result.advice)


async def test_alias_and_merge_move_names_and_deprecate_the_duplicate(
    app: Application, human: Caller
) -> None:
    await define(app, human, "Postgres")
    await define(app, human, "PostgreSQL")
    await app.ontology_changes.propose(human, req(AliasChange(concept="PostgreSQL", alias="pg")))
    merged = await app.ontology_changes.propose(
        human, req(MergeChange(source="Postgres", into="PostgreSQL"))
    )
    assert merged.status == "applied"
    found = await app.ontology.lookup(human, "postgres")
    assert [c.label for c in found] == ["PostgreSQL"]  # the old label now resolves to the survivor
    survivor = found[0]
    assert {"pg", "Postgres"} <= set(survivor.aliases)
    old = await app.ontology.get(human, (await app.ontology.resolve("concept:postgres")))
    assert old.status == "deprecated" and old.replaced_by == survivor.id


async def test_merge_validation(app: Application, human: Caller) -> None:
    await define(app, human, "Alpha")
    with pytest.raises(errors.InvalidRequestError):
        await app.ontology_changes.propose(human, req(MergeChange(source="Alpha", into="Alpha")))
    with pytest.raises(errors.NotFoundError):
        await app.ontology_changes.propose(human, req(MergeChange(source="Alpha", into="Ghost")))


async def test_relate_and_deprecate_apply(app: Application, human: Caller) -> None:
    await define(app, human, "Editor")
    await define(app, human, "Vim")
    await app.ontology_changes.propose(
        human,
        req(RelateChange(relation=RelationRequest(source="Vim", relation="is_a", target="Editor"))),
    )
    vim = (await app.ontology.lookup(human, "vim"))[0]
    assert any(r.relation == "is_a" for r in vim.relations)
    await app.ontology_changes.propose(human, req(DeprecateChange(concept="Vim")))
    assert (await app.ontology.lookup(human, "vim"))[0].status == "deprecated"


async def test_configured_ops_apply_without_review(
    app: Application, agent: Caller, human: Caller
) -> None:
    await define(app, human, "Node")
    app.ontology_changes.auto_apply = {"add_alias"}
    alias = await app.ontology_changes.propose(
        agent, req(AliasChange(concept="Node", alias="nodejs"))
    )
    assert alias.status == "applied"
    assert (await app.ontology.lookup(human, "nodejs"))[0].label == "Node"
    other = await app.ontology_changes.propose(
        agent, req(DefineChange(concept=ConceptDefinition(label="Deno")))
    )
    assert other.status == "pending"


async def test_a_change_needs_a_rationale(app: Application, agent: Caller) -> None:
    change = DefineChange(concept=ConceptDefinition(label="X1"))
    with pytest.raises(errors.InvalidRequestError):
        await app.ontology_changes.propose(agent, ChangeRequest(change=change, rationale=" "))
