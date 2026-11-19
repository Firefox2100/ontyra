"""Ontology management (plans/23): versions, ended edges, tombstones, split, restore, preview."""

from __future__ import annotations

import pytest

from ontyra.app import Application
from ontyra.models import RecordId
from ontyra.models.ontology import ConceptDefinition, RelationRequest
from ontyra.models.ontology_change import (
    ChangeRequest,
    DefineChange,
    DeprecateChange,
    EditChange,
    MergeChange,
    OntologyChange,
    RelateChange,
    ReparentChange,
    RestoreChange,
    SplitChange,
    UnrelateChange,
)
from ontyra.services import Caller, TaskService, errors


async def do(app: Application, who: Caller, change, why: str = "seen in the session"):  # type: ignore[no-untyped-def]
    result = await app.ontology_changes.propose(who, ChangeRequest(change=change, rationale=why))
    assert result.status == "applied"
    return result


async def define(app: Application, who: Caller, label: str, **kw) -> RecordId:  # type: ignore[no-untyped-def]
    await do(app, who, DefineChange(concept=ConceptDefinition(label=label, **kw)))
    return await app.ontology.resolve(label)


async def test_an_edit_writes_a_hidden_version_and_keeps_the_id(
    app: Application, human: Caller
) -> None:
    cid = await define(app, human, "Container runtime", definition="runs containers")
    await do(
        app,
        human,
        EditChange(concept="container runtime", label="Container engine", add_aliases=["engine"]),
    )
    view = (await app.ontology.lookup(human, "engine"))[0]
    assert view.id == cid and view.label == "Container engine"
    assert "Container runtime" in view.aliases  # the old label is now an alias
    history = await app.ontology.history(cid)
    assert [v.seq for v in history.versions] == [1, 2]
    assert history.versions[0].label == "Container runtime"
    assert history.versions[1].label == "Container engine"
    assert history.current == 2 and len(history.changes) == 2


async def test_an_edit_that_changes_nothing_or_steals_a_name_is_refused(
    app: Application, human: Caller
) -> None:
    await define(app, human, "Alpha")
    await define(app, human, "Beta", aliases=["bee"])
    for change in (
        EditChange(concept="alpha"),
        EditChange(concept="alpha", label="Bee"),
        EditChange(concept="alpha", remove_aliases=["nothing"]),
    ):
        with pytest.raises(errors.InvalidRequestError):
            await app.ontology_changes.propose(human, ChangeRequest(change=change, rationale="x"))


async def test_a_rewritten_definition_that_shares_nothing_is_flagged(
    app: Application, human: Caller
) -> None:
    await define(app, human, "Scheduler", definition="runs jobs at fixed times of day")
    result = await app.ontology_changes.propose(
        human,
        ChangeRequest(
            change=EditChange(
                concept="scheduler", definition="a person who plans conference rooms"
            ),
            rationale="clearer",
        ),
    )
    assert any("meaning" in a for a in result.advice)


async def test_relations_are_ended_not_deleted_and_the_reasoner_ignores_them(
    app: Application, human: Caller
) -> None:
    child = await define(app, human, "Podman")
    parent = await define(app, human, "Container tool")
    await do(
        app,
        human,
        RelateChange(
            relation=RelationRequest(source="podman", relation="is_a", target="container tool")
        ),
    )
    onto = await app.ontology.ontology()
    assert parent in onto.ancestors(child)
    await do(
        app,
        human,
        UnrelateChange(
            relation=RelationRequest(source="podman", relation="is_a", target="container tool")
        ),
    )
    assert parent not in (await app.ontology.ontology()).ancestors(child)
    events = (await app.ontology.history(child)).relations
    assert len(events) == 1 and events[0].ended_at is not None  # kept as history
    with pytest.raises(errors.InvalidRequestError):
        await app.ontology_changes.propose(
            human,
            ChangeRequest(
                change=UnrelateChange(
                    relation=RelationRequest(
                        source="podman", relation="is_a", target="container tool"
                    )
                ),
                rationale="again",
            ),
        )


async def test_reparent_moves_atomically_and_refuses_a_cycle(
    app: Application, human: Caller
) -> None:
    a = await define(app, human, "Aa")
    b = await define(app, human, "Bb")
    c = await define(app, human, "Cc")
    for src, dst in (("aa", "bb"), ("bb", "cc")):
        await do(
            app,
            human,
            RelateChange(relation=RelationRequest(source=src, relation="is_a", target=dst)),
        )
    with pytest.raises(errors.ConflictError):  # cc under aa would close a loop
        await app.ontology_changes.propose(
            human,
            ChangeRequest(
                change=ReparentChange(concept="bb", from_parent="cc", to_parent="aa"),
                rationale="x",
            ),
        )
    d = await define(app, human, "Dd")
    await do(app, human, ReparentChange(concept="bb", from_parent="cc", to_parent="dd"))
    onto = await app.ontology.ontology()
    assert d in onto.ancestors(b) and c not in onto.ancestors(b) and b in onto.ancestors(a)


async def test_a_merge_is_one_batch_and_a_revert_undoes_it(app: Application, human: Caller) -> None:
    keep = await define(app, human, "Postgres", aliases=["pg"])
    gone = await define(app, human, "PostgreSQL")
    done = await do(app, human, MergeChange(source="postgresql", into="postgres"))
    onto = await app.ontology.ontology()
    assert onto.concepts[gone].status == "deprecated" and onto.concepts[gone].replaced_by == keep
    assert "PostgreSQL" in onto.concepts[keep].aliases
    stored = await app.ontology_changes.store.get(done.id)
    assert isinstance(stored, OntologyChange)
    assert set(stored.touched or []) == {keep, gone} and len(stored.undo or []) == 2
    reverted = await app.ontology_changes.revert(human, done.id, "the merge was wrong")
    assert reverted.op == "revert"
    onto = await app.ontology.ontology()
    assert onto.concepts[gone].status == "active" and onto.concepts[gone].replaced_by is None
    assert "PostgreSQL" not in onto.concepts[keep].aliases


async def test_a_deprecated_concept_is_hidden_from_use_but_still_there(
    app: Application, human: Caller, agent: Caller, remember
) -> None:
    old = await define(app, human, "Py2")
    new = await define(app, human, "Py3")
    await do(app, human, MergeChange(source="py2", into="py3"))
    assert [c.id for c in await app.ontology.lookup(human, "py2")] == [new]  # its name moved
    assert old not in [c.id for c in await app.ontology.list(human)]
    assert old in [c.id for c in await app.ontology.list(human, include_deprecated=True)]
    live, note = await app.ontology.use(old)
    assert live == new and "replaces" in (note or "")
    # a new memory that names the old id is moved to the successor, with a warning
    from ontyra.models.proposals import AddMemory, ProposalRequest

    result = await app.memory.submit(
        agent,
        ProposalRequest(
            ops=[
                AddMemory(
                    kind="fact",
                    ref="m",
                    fields={
                        "text": "this repo targets the new runtime",
                        "subject": str(old),
                        "predicate": "targets",
                        "object": "py3",
                        "about": [str(old)],
                        "scope": "universal",
                    },
                )
            ]
        ),
    )
    assert any(f.code == "deprecated_concept" for f in result.findings)
    view = await app.memory.get_memory(agent, result.created["m"])
    assert view.content["about"] == [str(new)]


async def test_a_split_keeps_a_tombstone_and_points_at_what_it_became(
    app: Application, human: Caller, agent: Caller
) -> None:
    parent = await define(app, human, "Databases")
    src = await define(app, human, "Sql thing", is_a=["databases"])
    other = await define(app, human, "Existing store")
    await do(
        app,
        human,
        SplitChange(
            source="sql thing",
            into=[ConceptDefinition(label="Relational engine"), "existing store"],
        ),
    )
    onto = await app.ontology.ontology()
    assert onto.concepts[src].status == "deprecated" and onto.concepts[src].replaced_by is None
    new = await app.ontology.resolve("relational engine")
    assert onto.successors(src) == sorted([new, other], key=str)
    assert parent in onto.ancestors(new)  # inherited the source's parent
    how, targets = onto.live(src)
    assert how == "split" and set(targets) == {new, other}
    live, note = await app.ontology.use(src)
    assert live is None and "split" in (note or "")
    assert {c.id for c in await app.ontology.lookup(human, "sql thing")} == {new, other}
    # a memory left on the tombstone is still found when a successor is mentioned
    assert src in onto.expand(new)


async def test_restore_makes_a_new_version_and_a_split_can_be_undone(
    app: Application, human: Caller
) -> None:
    src = await define(app, human, "Thing")
    done = await do(
        app,
        human,
        SplitChange(
            source="thing",
            into=[ConceptDefinition(label="Thing a"), ConceptDefinition(label="Thing b")],
        ),
    )
    await app.ontology_changes.revert(human, done.id, "wrong split")
    onto = await app.ontology.ontology()
    assert onto.concepts[src].status == "active" and not onto.successors(src)
    a = await app.ontology.resolve("thing a")
    assert onto.concepts[a].status == "deprecated"  # a new concept is retired, never deleted
    versions = [v.seq for v in (await app.ontology.history(src)).versions]
    assert versions == [1, 2, 3]
    with pytest.raises(errors.NotFoundError):
        await app.ontology_changes.propose(
            human,
            ChangeRequest(change=RestoreChange(concept="thing", version=99), rationale="x"),
        )


async def test_a_preview_changes_nothing_and_says_what_would_happen(
    app: Application, human: Caller, agent: Caller, remember
) -> None:
    src = await define(app, human, "Old name")
    await remember("fact", "something about it", about=[str(src)])
    before = await app.ontology.ontology()
    preview = await app.ontology_changes.preview(
        human,
        ChangeRequest(change=DeprecateChange(concept="old name", reason="obsolete"), rationale="x"),
    )
    assert preview.op == "deprecate" and src in preview.effects.touched
    assert preview.effects.memories[str(src)] == 1
    after = await app.ontology.ontology()
    assert after.concepts[src].status == before.concepts[src].status == "active"
    assert not await app.ontology_changes.pending(human)


async def test_a_revert_is_refused_while_a_later_change_touched_the_same_concept(
    app: Application, human: Caller, agent: Caller
) -> None:
    await define(app, human, "Gamma", definition="the third letter")
    first = await do(app, human, EditChange(concept="gamma", add_aliases=["g3"]))
    second = await do(app, human, EditChange(concept="gamma", definition="third greek letter"))
    with pytest.raises(errors.ConflictError, match="revert them first"):
        await app.ontology_changes.revert(human, first.id, "undo")
    with pytest.raises(errors.InvalidRequestError):  # an agent never reverts
        await app.ontology_changes.revert(agent, second.id, "undo")
    await app.ontology_changes.revert(human, second.id, "undo the definition")
    await app.ontology_changes.revert(human, first.id, "undo the alias")
    view = (await app.ontology.lookup(human, "gamma"))[0]
    assert view.aliases == [] and view.definition == "the third letter"
    with pytest.raises(errors.ProposalStateError):
        await app.ontology_changes.revert(human, first.id, "again")


async def test_a_waiting_ontology_change_can_be_amended_sent_back_and_withdrawn(
    app: Application, agent: Caller, human: Caller
) -> None:
    from ontyra.models.ontology_change import AmendChange
    from ontyra.models.proposals import RequestChanges, WithdrawRequest

    held = await app.ontology_changes.propose(
        agent,
        ChangeRequest(change=DefineChange(concept=ConceptDefinition(label="Pnpm")), rationale="x"),
    )
    assert held.status == "pending"
    other = await app.actors.caller(kind="agent", name="other")
    with pytest.raises(errors.PermissionDeniedError):
        await app.ontology_changes.amend(
            other,
            held.id,
            AmendChange(
                change=DefineChange(concept=ConceptDefinition(label="Pnpm tool")), why="mine"
            ),
        )
    sent = await app.ontology_changes.request_changes(
        human, held.id, RequestChanges(comment="add the definition")
    )
    assert sent.status == "changes_requested"
    assert held.id in [c.id for c in await app.ontology_changes.pending(agent)]
    amended = await app.ontology_changes.amend(
        agent,
        held.id,
        AmendChange(
            change=DefineChange(
                concept=ConceptDefinition(label="Pnpm", definition="a package manager")
            ),
            why="added the definition",
            expect_version=1,
        ),
    )
    assert amended.status == "pending"
    with pytest.raises(errors.ConflictError):
        await app.ontology_changes.approve(human, held.id, expect_version=1)
    done = await app.ontology_changes.approve(human, held.id, expect_version=2)
    assert done.status == "applied"
    view = (await app.ontology.lookup(human, "pnpm"))[0]
    assert view.definition == "a package manager"
    second = await app.ontology_changes.propose(
        agent,
        ChangeRequest(change=DefineChange(concept=ConceptDefinition(label="Bun")), rationale="x"),
    )
    gone = await app.ontology_changes.withdraw(agent, second.id, WithdrawRequest(reason="oops"))
    assert gone.status == "withdrawn" and not await app.ontology_changes.pending(human)


async def test_a_second_define_is_told_about_the_waiting_one(
    app: Application, agent: Caller
) -> None:
    await app.ontology_changes.propose(
        agent,
        ChangeRequest(
            change=DefineChange(concept=ConceptDefinition(label="Rust compiler")), rationale="x"
        ),
    )
    second = await app.ontology_changes.propose(
        agent,
        ChangeRequest(
            change=DefineChange(concept=ConceptDefinition(label="Rust compiler toolchain")),
            rationale="x",
        ),
    )
    assert any("pending_similar" in a for a in second.advice)


async def test_the_rest_surface_previews_reverts_and_shows_history(
    client,
    human: Caller,
    app: Application,  # type: ignore[no-untyped-def]
) -> None:
    body = {"change": {"op": "define", "concept": {"label": "Zed"}}, "rationale": "new editor"}
    preview = await client.post("/v1/ontology/changes/preview", json=body)
    assert preview.status_code == 200 and preview.json()["effects"]["summary"]
    assert (await client.get("/v1/concepts?term=zed")).json() == []
    made = (await client.post("/v1/ontology/changes", json=body, headers=HUMAN)).json()
    assert made["status"] == "applied"
    edit = {
        "change": {"op": "edit", "concept": "zed", "definition": "a code editor"},
        "rationale": "define it",
    }
    edited = (await client.post("/v1/ontology/changes", json=edit, headers=HUMAN)).json()
    history = (await client.get("/v1/concepts/zed/history")).json()
    assert [v["seq"] for v in history["versions"]] == [1, 2]
    changes = (await client.get("/v1/ontology/changes?pending=false&concept=zed")).json()
    assert {c["id"] for c in changes} == {made["id"], edited["id"]}
    bare = edited["id"].split(":", 1)[1]
    undone = await client.post(
        f"/v1/ontology/changes/{bare}/revert", json={"reason": "not yet"}, headers=HUMAN
    )
    assert undone.status_code == 200 and undone.json()["op"] == "revert"
    assert (await client.get("/v1/concepts?term=zed")).json()[0]["definition"] is None
    assert (await client.get("/v1/ontology/changes/check")).json()["findings"] is not None


HUMAN = {"X-Ontyra-Actor": "owner", "X-Ontyra-Actor-Kind": "human"}


async def test_maintenance_tidies_the_index_after_a_merge_without_touching_the_memory(
    app: Application, human: Caller, remember
) -> None:
    old = await define(app, human, "Gitlab ci")
    new = await define(app, human, "Pipelines")
    memory = await remember("fact", "builds run in the shared pipeline", about=[str(old)])
    await app.indexer.backfill()
    await do(app, human, MergeChange(source="gitlab ci", into="pipelines"))
    docs = await app.recall.store.docs(human.visible_owners(), about=[old], limit=10)
    assert len(docs) == 1
    assert await app.ontology.canonicalise_index(human) == 1
    assert not await app.recall.store.docs(human.visible_owners(), about=[old], limit=10)
    assert len(await app.recall.store.docs(human.visible_owners(), about=[new], limit=10)) == 1
    view = await app.memory.get_memory(human, memory)
    assert view.content["about"] == [str(old)]  # the revision keeps what it was written with
    assert await app.ontology.canonicalise_index(human) == 0


async def test_a_split_queues_capped_refinement_tasks_that_close_when_revised(
    app: Application, human: Caller, remember
) -> None:
    await define(app, human, "Cloud")
    memories = [
        await remember("fact", f"the cloud account {n} is billed monthly", about=["concept:cloud"])
        for n in range(3)
    ]
    await app.indexer.backfill()
    await do(
        app,
        human,
        SplitChange(
            source="cloud",
            into=[
                ConceptDefinition(label="Public cloud"),
                ConceptDefinition(label="Private cloud"),
            ],
        ),
    )
    report = await app.tasks.detect(human)
    drift = [t for t in report.created if t.kind == "review_concept_drift"]
    assert len(drift) == 3 and {t.target for t in drift} == set(memories)
    assert {s["label"] for s in drift[0].card["successors"]} == {"Public cloud", "Private cloud"}
    assert not [t for t in (await app.tasks.detect(human)).created]  # queued once
    capped = TaskService(
        app.tasks.store, app.tasks.now, app.tasks.consolidation, app.memory, 14, app.ontology, 1
    )
    assert len([f for f in await capped._refinement_findings(human)]) == 1
    from ontyra.models.proposals import ProposalRequest, ReviseMemory

    await app.memory.submit(
        human,
        ProposalRequest(
            ops=[
                ReviseMemory(
                    memory=memories[0],
                    fields={"about": [str(await app.ontology.resolve("public cloud"))]},
                    reason="it is about the public one",
                )
            ]
        ),
    )
    again = await app.tasks.detect(human)
    assert again.closed >= 1  # the revised memory's task is obsolete
