from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ontyra.models import RecordId
from ontyra.models.ontology_change import ChangeRequest, ChangeResult, OntologyChange
from ontyra.transports.rest.deps import CallerDep, OntologyChangeServiceDep

router = APIRouter(prefix="/ontology/changes", tags=["ontology"])


class RejectBody(BaseModel):
    reason: str


def _id(change_id: str) -> RecordId:
    return RecordId("ontology_change", change_id)


@router.post(
    "",
    summary="Propose an ontology change",
    description="Add, relate, alias, merge or deprecate concepts. A human's change applies at "
    "once; an agent's is held for review (unless its op is configured to auto-apply). The "
    "result's `advice` lists similar existing concepts: prefer an alias or a merge to a duplicate.",
)
async def propose(
    body: ChangeRequest, caller: CallerDep, service: OntologyChangeServiceDep
) -> ChangeResult:
    return await service.propose(caller, body)


@router.get("", summary="Ontology changes: held for review (default) or the recent history")
async def list_changes(
    caller: CallerDep,
    service: OntologyChangeServiceDep,
    pending: Annotated[bool, Query()] = True,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[OntologyChange]:
    return await (service.pending(caller, limit) if pending else service.history(caller, limit))


@router.post("/{change_id}/approve", summary="Approve and apply a held change (human only)")
async def approve(
    change_id: str, caller: CallerDep, service: OntologyChangeServiceDep
) -> ChangeResult:
    return await service.approve(caller, _id(change_id))


@router.post("/{change_id}/reject", summary="Reject a held change (human only)")
async def reject(
    change_id: str, body: RejectBody, caller: CallerDep, service: OntologyChangeServiceDep
) -> ChangeResult:
    return await service.reject(caller, _id(change_id), body.reason)
