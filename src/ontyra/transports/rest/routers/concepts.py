from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from ontyra.models import RecordId
from ontyra.models.ontology import (
    ConceptDefinition,
    ConceptHistory,
    ConceptView,
    RelationRequest,
)
from ontyra.transports.rest.deps import CallerDep, GlobalWriterDep, OntologyServiceDep

router = APIRouter(prefix="/concepts", tags=["ontology"])


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create a concept")
async def define(
    request: ConceptDefinition, caller: GlobalWriterDep, service: OntologyServiceDep
) -> ConceptView:
    return await service.define(caller, request)


@router.get("", summary="List concepts, or resolve a term by label/alias with `term`")
async def list_concepts(
    caller: CallerDep,
    service: OntologyServiceDep,
    term: Annotated[str | None, Query()] = None,
    scheme: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    include_deprecated: Annotated[bool, Query()] = False,
) -> list[ConceptView]:
    if term:
        return await service.lookup(caller, term)
    return await service.list(
        caller, scheme=scheme, limit=limit, include_deprecated=include_deprecated
    )


@router.get("/{concept_id}", summary="One concept with its relations")
async def get_concept(
    concept_id: str, caller: CallerDep, service: OntologyServiceDep
) -> ConceptView:
    return await service.get(caller, RecordId("concept", concept_id))


@router.get(
    "/{concept_id}/history",
    summary="A concept's versions and relation history (investigation)",
    description="Earlier versions of its label, aliases, definition and status, the relations it "
    "has had (ended ones included), and the ontology changes that touched it. Ordinary reads "
    "never show these.",
)
async def concept_history(
    concept_id: str, caller: CallerDep, service: OntologyServiceDep
) -> ConceptHistory:
    return await service.history(RecordId("concept", concept_id))


@router.post("/relations", summary="Relate two concepts (is_a, part_of, same_as, …)")
async def relate(
    request: RelationRequest, caller: GlobalWriterDep, service: OntologyServiceDep
) -> ConceptView:
    return await service.relate(caller, request)
