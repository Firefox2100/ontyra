from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from ontyra.models import RecordId
from ontyra.transports.rest.deps import CallerDep, SourceServiceDep

router = APIRouter(prefix="/vetting", tags=["knowledge"])


@router.get(
    "/pending",
    summary="Memories committed by policy that no human has vetted",
    description="Agent-written memories that policy committed without review are `auto`: recall "
    "uses them (marked unvetted) unless `ONTYRA_RECALL__MIN_VETTING=vetted`. A human vets "
    "them here.",
)
async def pending(
    caller: CallerDep,
    service: SourceServiceDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict[str, Any]]:
    return await service.unvetted(caller, limit)


@router.post("/{memory_id}/vet", summary="Confirm a memory: it is vetted from now on")
async def vet(memory_id: str, caller: CallerDep, service: SourceServiceDep) -> dict[str, Any]:
    return await service.vet(caller, RecordId("memory", memory_id))
