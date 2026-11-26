"""A topical memory reaches an agent only when the context ties to its subject."""

from __future__ import annotations

from typing import Any

import pytest

from ontyra.app import Application
from ontyra.models import RecordId
from ontyra.models.ontology import ConceptDefinition, ResourceRequest
from ontyra.models.proposals import AddMemory, ProposalRequest
from ontyra.models.recall import RecallContext, RecallRequest, RecallSettings
from ontyra.services import Caller


@pytest.fixture(params=["legacy", "fusion"])
async def world(request: pytest.FixtureRequest, make_app, human: Caller) -> Application:
    app: Application = await make_app(recall=RecallSettings(engine=request.param))
    await app.ontology.define(
        human, ConceptDefinition(label="Frob module", aliases=["FROB-1", "frobnicator"])
    )
    await app.anchors.register(
        ResourceRequest(uri="file://box/work/frob-firmware", instance_of=["frob module"])
    )
    return app


async def add(app: Application, who: Caller, scope: str, *, about: bool = True) -> ProposalRequest:
    fields: dict[str, Any] = {
        "text": "the frob module draws 240 microamps in light sleep",
        "subject": "concept:frob_module",
        "predicate": "draws",
        "object": "240 microamps",
        "scope": scope,
    }
    if about:
        fields["about"] = ["concept:frob_module"]
    return ProposalRequest(ops=[AddMemory(kind="fact", ref="m", fields=fields)])


async def stored(app: Application, who: Caller, scope: str) -> RecordId:
    res = await app.memory.submit(who, await add(app, who, scope))
    assert res.status == "committed", res.findings
    await app.indexer.backfill()
    return res.created["m"]


async def recalled(app: Application, who: Caller, query: str, *uris: str) -> set[RecordId]:
    request = RecallRequest(query=query, context=RecallContext(uris=list(uris)))
    return {i.memory for i in (await app.recall.recall(who, request)).items()}


QUERY = "what current does it draw in light sleep"


async def test_topical_stays_silent_elsewhere_but_a_contextual_one_does_not(
    world: Application, human: Caller
) -> None:
    topical = await stored(world, human, "topical")
    contextual = await stored(world, human, "contextual")
    got = await recalled(world, human, QUERY, "file://box/work/other-project")
    assert topical not in got
    assert contextual in got


async def test_topical_is_delivered_inside_the_project_that_uses_it(
    world: Application, human: Caller
) -> None:
    topical = await stored(world, human, "topical")
    assert topical in await recalled(world, human, QUERY, "file://box/work/frob-firmware/src")


async def test_topical_is_delivered_when_the_message_names_the_subject(
    world: Application, human: Caller
) -> None:
    topical = await stored(world, human, "topical")
    query = "what current does the frobnicator draw in light sleep"
    assert topical in await recalled(world, human, query, "file://box/work/other-project")


async def test_topical_without_a_subject_is_refused(world: Application, human: Caller) -> None:
    res = await world.memory.submit(human, await add(world, human, "topical", about=False))
    assert res.status == "rejected"
    assert any(f.code == "topical_needs_subject" for f in res.findings)
