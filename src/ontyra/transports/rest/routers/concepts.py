from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from ontyra.models import RecordId
from ontyra.models.ontology import ConceptDefinition, ConceptView, RelationRequest
from ontyra.transports.rest.deps import CallerDep, OntologyServiceDep

router = APIRouter(prefix="/concepts", tags=["ontology"])


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create a concept")
async def define(
    request: ConceptDefinition, caller: CallerDep, service: OntologyServiceDep
) -> ConceptView:
    return await service.define(caller, request)


@router.get("", summary="List concepts, or resolve a term by label/alias with `term`")
async def list_concepts(
    caller: CallerDep,
    service: OntologyServiceDep,
    term: Annotated[str | None, Query()] = None,
    scheme: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[ConceptView]:
    if term:
        return await service.lookup(caller, term)
    return await service.list(caller, scheme=scheme, limit=limit)


@router.get("/{concept_id}", summary="One concept with its relations")
async def get_concept(
    concept_id: str, caller: CallerDep, service: OntologyServiceDep
) -> ConceptView:
    return await service.get(caller, RecordId("concept", concept_id))


@router.post("/relations", summary="Relate two concepts (is_a, part_of, same_as, …)")
async def relate(
    request: RelationRequest, caller: CallerDep, service: OntologyServiceDep
) -> ConceptView:
    return await service.relate(caller, request)
