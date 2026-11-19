"""Load a world into an application: ontology, resources and memories (superseded, linked and
retracted as the world plants them), then the derived index."""

from __future__ import annotations

from ontyra.app import Application
from ontyra.calibration.models import World
from ontyra.models import RecordId
from ontyra.models.ontology import RelationRequest
from ontyra.models.proposals import (
    AddMemory,
    LinkMemories,
    ProposalRequest,
    RetractMemory,
    SupersedeMemory,
)
from ontyra.services import Caller


async def populate(
    app: Application, curator: Caller, writer: Caller, world: World
) -> dict[str, str]:
    """Create the ontology, resources and memories; returns `memory id -> world key`."""
    for concept in world.concepts:
        await app.ontology.define(curator, concept)
    for rel in world.relations:
        await app.ontology.relate(
            curator, RelationRequest(source=rel.source, relation=rel.relation, target=rel.target)
        )
    for resource in world.resources:
        await app.anchors.register(resource)

    ids: dict[str, RecordId] = {}
    for mem in world.memories:
        ops: list[AddMemory | SupersedeMemory | LinkMemories] = [
            AddMemory(kind=mem.kind, ref=mem.key, fields={"text": mem.text, **world.fields_of(mem)})
        ]
        if mem.supersedes:
            ops.append(
                SupersedeMemory(new=mem.key, target=ids[mem.supersedes], reason_code="outdated")
            )
        ops += [
            LinkMemories(relation=link.relation, source=mem.key, target=ids[link.target])
            for link in mem.links
        ]
        result = await app.memory.submit(writer, ProposalRequest(ops=ops))  # type: ignore[arg-type]
        assert result.status == "committed", (mem.key, result.findings)
        ids[mem.key] = result.created[mem.key]
        if mem.retracted:
            gone = await app.memory.submit(
                writer,
                ProposalRequest(ops=[RetractMemory(memory=ids[mem.key], reason="planted error")]),
            )
            assert gone.status == "committed", (mem.key, gone.findings)
    await app.indexer.backfill()
    return {str(v): k for k, v in ids.items()}
