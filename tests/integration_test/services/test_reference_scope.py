"""Reference material: a sourced statement is documentation. Recall, briefing and check never
deliver it; `consult` (and `search`) look it up. A topical overview points to it."""

from __future__ import annotations

from typing import Any

import pytest

from ontyra.app import Application
from ontyra.models import RecordId
from ontyra.models.ontology import ConceptDefinition, ResourceRequest
from ontyra.models.proposals import AddMemory, Evidence, ProposalRequest
from ontyra.models.recall import (
    ConsultRequest,
    RecallContext,
    RecallRequest,
    RecallSettings,
    SearchRequest,
)
from ontyra.models.sources import SourceRef
from ontyra.services import Caller

PROJECT = "file://box/work/frob-firmware"
SHEET = "file:///docs/frob-datasheet.pdf"


@pytest.fixture(params=["legacy", "fusion"])
async def world(request: pytest.FixtureRequest, make_app, human: Caller) -> Application:
    app: Application = await make_app(recall=RecallSettings(engine=request.param))
    await app.ontology.define(human, ConceptDefinition(label="Frob module", aliases=["FROB-1"]))
    await app.ontology.define(human, ConceptDefinition(label="Other module"))
    await app.anchors.register(ResourceRequest(uri=PROJECT, instance_of=["frob module"]))
    return app


async def add(
    app: Application,
    who: Caller,
    text: str,
    *,
    concept: str = "frob_module",
    kind: str = "fact",
    source: bool = True,
    **extra: Any,
) -> RecordId:
    fields: dict[str, Any] = {"text": text, "about": [f"concept:{concept}"], **extra}
    if kind == "fact":
        fields |= {"subject": f"concept:{concept}", "predicate": "has", "object": "a value"}
    else:
        fields |= {"strength": "must", "rule": text}
    evidence = (
        [Evidence(source=SourceRef(uri=SHEET, locator="p. 4", quote=text, title="Frob datasheet"))]
        if source
        else []
    )
    res = await app.memory.submit(
        who,
        ProposalRequest(ops=[AddMemory(kind=kind, ref="m", fields=fields, evidence=evidence)]),  # type: ignore[arg-type]
    )
    assert res.status == "committed", res.findings
    await app.indexer.backfill()
    return res.created["m"]


async def scope_of(app: Application, who: Caller, memory: RecordId) -> str:
    docs = await app.recall.store.docs(who.visible_owners(), limit=1000)
    return next(d.scope for d in docs if d.memory == memory)


async def delivered(app: Application, who: Caller, query: str, uri: str = PROJECT) -> set[RecordId]:
    ctx = RecallContext(uris=[uri])
    pack = await app.recall.recall(who, RecallRequest(query=query, context=ctx))
    return {i.memory for i in pack.items()}


SLEEP = "the frob module draws 240 microamps in light sleep"


async def test_a_sourced_statement_is_reference_by_default(
    world: Application, human: Caller
) -> None:
    sourced = await add(world, human, SLEEP)
    plain = await add(world, human, "the frob module is mounted on the left", source=False)
    rule = await add(world, human, "check the frob reset line first", kind="directive")
    explicit = await add(world, human, "frob modules ship in blue", scope="contextual")
    assert await scope_of(world, human, sourced) == "reference"
    assert await scope_of(world, human, plain) == "contextual"
    assert await scope_of(world, human, rule) == "contextual"  # a rule is not reference material
    assert await scope_of(world, human, explicit) == "contextual"


async def test_recall_briefing_and_check_never_deliver_reference_material(
    world: Application, human: Caller
) -> None:
    ref = await add(world, human, SLEEP)
    for query in ("what current does the frob module draw in light sleep", SLEEP):
        assert ref not in await delivered(world, human, query)  # in the project, named, matching
    briefing = await world.recall.briefing(
        human, RecallRequest(context=RecallContext(uris=[PROJECT]))
    )
    assert ref not in {i.memory for i in briefing.items()}


async def test_consult_finds_it_with_its_source_and_search_includes_it(
    world: Application, human: Caller
) -> None:
    ref = await add(world, human, SLEEP)
    other = await add(
        world, human, "the other module draws 90 microamps in light sleep", concept="other_module"
    )
    result = await world.explore.consult(
        human, ConsultRequest(query="light sleep current", topic="frob module")
    )
    assert [h.memory for h in result.hits] == [ref]
    hit = result.hits[0]
    assert hit.sources[0].uri.endswith("/docs/frob-datasheet.pdf")
    assert hit.sources[0].locator == "p. 4"
    assert result.topics == ["Frob module"]
    # no topic: what the context ties to
    tied = await world.explore.consult(
        human,
        ConsultRequest(query="light sleep current", context=RecallContext(uris=[PROJECT])),
    )
    assert [h.memory for h in tied.hits] == [ref]
    # no topic and no tie: everything
    every = await world.explore.consult(human, ConsultRequest(query="light sleep current"))
    assert {h.memory for h in every.hits} == {ref, other}
    found = await world.explore.search(human, SearchRequest(query="light sleep"))
    assert ref in {h.memory for h in found.hits}


async def test_a_topical_overview_points_to_the_reference_material(
    world: Application, human: Caller
) -> None:
    ref = await add(world, human, SLEEP)
    overview = await add(
        world,
        human,
        "notes on the frob module: a datasheet with 1 detailed entry, found with consult",
        scope="topical",
    )
    got = await delivered(world, human, "what do we know about the frob module")
    assert overview in got and ref not in got
    assert overview not in await delivered(
        world, human, "datasheet detailed entry found", "file://box/work/x"
    )
    # at the start of work in the project it is mentioned, with no question asked; not elsewhere
    here = await world.recall.briefing(human, RecallRequest(context=RecallContext(uris=[PROJECT])))
    assert overview in {i.memory for i in here.items()} and ref not in {
        i.memory for i in here.items()
    }
    away = await world.recall.briefing(
        human, RecallRequest(context=RecallContext(uris=["file://box/work/x"]))
    )
    assert overview not in {i.memory for i in away.items()}
