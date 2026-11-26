from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from ontyra.models import RecordId
from ontyra.models.recall import (
    AskRequest,
    AskResult,
    ConsultRequest,
    ConsultResult,
    Overview,
    RecallContext,
    SearchRequest,
    SearchResult,
)
from ontyra.services.views import MemoryDetail, Neighbors
from ontyra.transports.rest.deps import CallerDep, ExplorationServiceDep

router = APIRouter(tags=["explore"])


@router.post(
    "/search",
    summary="Search memories",
    description="Hybrid search (full-text, vector, concept). Results carry facet counts so the "
    "caller narrows by choosing a facet instead of composing a query.",
)
async def search(
    request: SearchRequest, caller: CallerDep, service: ExplorationServiceDep
) -> SearchResult:
    return await service.search(caller, request)


@router.post(
    "/consult",
    summary="Look something up in the reference material",
    description="Documentation and notes taken from sources are never delivered by recall: they "
    "are looked up. Limit it to a `topic` (a concept, label or alias), or leave it out to use "
    "what the context ties to. Hits carry their sources and quotes.",
)
async def consult(
    request: ConsultRequest, caller: CallerDep, service: ExplorationServiceDep
) -> ConsultResult:
    return await service.consult(caller, request)


@router.post("/ask", summary="Structural questions answered exactly")
async def ask(request: AskRequest, caller: CallerDep, service: ExplorationServiceDep) -> AskResult:
    return await service.ask(caller, request)


@router.post(
    "/overview",
    summary="Orientation card",
    description="Counts per kind, top concepts, the anchor chain and qualifiers in force, "
    "recent changes and the available verbs.",
)
async def overview(
    context: RecallContext, caller: CallerDep, service: ExplorationServiceDep
) -> Overview:
    return await service.overview(caller, context)


@router.get(
    "/memories/{memory_id}/detail",
    summary="One memory with expansions",
    description="`expand` may repeat: history, links, evidence, applies.",
)
async def detail(
    memory_id: str,
    caller: CallerDep,
    service: ExplorationServiceDep,
    expand: Annotated[list[str] | None, Query()] = None,
) -> MemoryDetail:
    return await service.get(caller, RecordId("memory", memory_id), expand or [], RecallContext())


@router.get("/neighbors/{table}/{item_id}", summary="What is connected to a memory or concept")
async def neighbors(
    table: str, item_id: str, caller: CallerDep, service: ExplorationServiceDep
) -> Neighbors:
    return await service.neighbors(caller, RecordId(table, item_id))
