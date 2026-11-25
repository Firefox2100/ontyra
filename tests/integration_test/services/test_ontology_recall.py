"""Recall after the ontology changed: memories written against the old shape are still
found for the new one, and nothing about the wrong sibling comes back."""

from __future__ import annotations

import pytest

from ontyra.app import Application
from ontyra.models import RecordId
from ontyra.models.ontology import ConceptDefinition, RelationRequest
from ontyra.models.ontology_change import (
    ChangeRequest,
    DefineChange,
    EditChange,
    MergeChange,
    RelateChange,
    ReparentChange,
    SplitChange,
)
from ontyra.models.recall import RecallRequest, RecallSettings
from ontyra.services import Caller


@pytest.fixture(params=["legacy", "fusion"])
async def world(request: pytest.FixtureRequest, make_app, human: Caller) -> Application:
    app: Application = await make_app(recall=RecallSettings(engine=request.param))
    return app


async def change(app: Application, who: Caller, c) -> None:  # type: ignore[no-untyped-def]
    result = await app.ontology_changes.propose(who, ChangeRequest(change=c, rationale="test"))
    assert result.status == "applied"


async def memory(app: Application, who: Caller, text: str, concept: str) -> RecordId:
    from ontyra.models.proposals import AddMemory, ProposalRequest

    res = await app.memory.submit(
        who,
        ProposalRequest(
            ops=[
                AddMemory(
                    kind="fact",
                    ref="m",
                    fields={
                        "text": text,
                        "subject": f"concept:{concept}",
                        "predicate": "is",
                        "object": "noted",
                        "about": [f"concept:{concept}"],
                        "scope": "universal",
                    },
                )
            ]
        ),
    )
    assert res.status == "committed", res.findings
    return res.created["m"]


async def found(app: Application, who: Caller, query: str) -> set[RecordId]:
    await app.indexer.backfill()
    pack = await app.recall.recall(who, RecallRequest(query=query))
    return {i.memory for i in pack.items()}


def needs_concepts(app: Application) -> None:
    """The fusion engine adds the concept lane's candidates but gives them no score of their own
    (`concept_boost` is 0 by default), so ontology expansion is not observable there."""
    if app.settings.recall.engine == "fusion":
        pytest.skip("fusion: concept_boost is 0 by default")


async def test_a_merge_keeps_memories_about_the_old_concept_findable(
    world: Application, human: Caller
) -> None:
    needs_concepts(world)
    await change(world, human, DefineChange(concept=ConceptDefinition(label="Gitlab ci")))
    await change(world, human, DefineChange(concept=ConceptDefinition(label="Pipelines")))
    old = await memory(world, human, "gitlab ci runs must be green before merging", "gitlab_ci")
    await change(world, human, MergeChange(source="gitlab ci", into="pipelines"))
    assert old in await found(world, human, "can I merge while the pipelines are red")
    assert old in await found(world, human, "gitlab ci is red")  # the old name still works


async def test_a_split_finds_the_old_memory_for_either_successor_but_not_the_sibling(
    world: Application, human: Caller
) -> None:
    await change(world, human, DefineChange(concept=ConceptDefinition(label="Cloud")))
    old = await memory(world, human, "cloud accounts are billed monthly", "cloud")
    await change(
        world,
        human,
        SplitChange(
            source="cloud",
            into=[
                ConceptDefinition(label="Public cloud"),
                ConceptDefinition(label="Private cloud"),
            ],
        ),
    )
    public = await memory(
        world, human, "public cloud egress is charged per gigabyte", "public_cloud"
    )
    private = await memory(
        world, human, "private cloud capacity is reviewed every quarter", "private_cloud"
    )
    got = await found(world, human, "how are the public cloud accounts billed")
    assert old in got and private not in got
    again = await found(world, human, "what do we pay for the cloud")
    assert old in again
    assert public not in await found(world, human, "private cloud capacity review")


async def test_a_renamed_concept_is_found_by_its_new_and_old_names(
    world: Application, human: Caller
) -> None:
    await change(world, human, DefineChange(concept=ConceptDefinition(label="Linter")))
    lint = await memory(world, human, "run the linter before every commit", "linter")
    await change(world, human, EditChange(concept="linter", label="Static analysis"))
    assert lint in await found(world, human, "run the static analysis before committing")
    assert lint in await found(world, human, "run the linter before committing")


async def test_a_reparented_concept_no_longer_answers_for_its_old_parent(
    world: Application, human: Caller
) -> None:
    needs_concepts(world)
    for label in ("Container engine", "Virtual tooling", "Podman"):
        await change(world, human, DefineChange(concept=ConceptDefinition(label=label)))
    await change(
        world,
        human,
        RelateChange(
            relation=RelationRequest(source="podman", relation="is_a", target="container engine")
        ),
    )
    engine = await memory(
        world, human, "container engines are updated on the first of the month", "container_engine"
    )
    child = await memory(
        world, human, "podman machines need a restart after the vm resumes", "podman"
    )
    before = await found(world, human, "when do we update the container engine")
    assert engine in before and child in before  # a kind of container engine
    await change(
        world,
        human,
        ReparentChange(
            concept="podman", from_parent="container engine", to_parent="virtual tooling"
        ),
    )
    after = await found(world, human, "when do we update the container engine")
    assert engine in after and child not in after


async def test_a_flood_about_a_related_concept_cannot_push_out_the_mentioned_one(
    make_app, human: Caller
) -> None:
    app: Application = await make_app(recall=RecallSettings(engine="legacy"))
    await change(app, human, DefineChange(concept=ConceptDefinition(label="Vehicle")))
    await change(app, human, DefineChange(concept=ConceptDefinition(label="Bicycle")))
    await change(
        app,
        human,
        RelateChange(relation=RelationRequest(source="bicycle", relation="is_a", target="vehicle")),
    )
    wanted = await memory(app, human, "the vehicle log is kept by the garage", "vehicle")
    for i in range(6):  # newer, and only related (a kind of the mentioned concept)
        await memory(app, human, f"bicycle number {i} has a bell", "bicycle")
    await app.indexer.backfill()
    ctx = await app.recall.resolve_context(RecallRequest().context)
    lane = await app.recall.concept_lane(
        "what about the vehicle", ctx, human.visible_owners(), [], limit=3
    )
    assert wanted in {d.memory for _, d, _ in lane}
