from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ontyra.models import RecordId
from ontyra.models.ontology import OntologyCheck
from ontyra.models.ontology_change import (
    AmendChange,
    ChangePreview,
    ChangeRequest,
    ChangeResult,
    OntologyChange,
)
from ontyra.models.proposals import RequestChanges, WithdrawRequest
from ontyra.transports.rest.deps import (
    CallerDep,
    OntologyChangeServiceDep,
    OntologyCheckerDep,
)

router = APIRouter(prefix="/ontology/changes", tags=["ontology"])


class RejectBody(BaseModel):
    reason: str
    expect_version: int | None = None


def _id(change_id: str) -> RecordId:
    return RecordId("ontology_change", change_id)


@router.post(
    "",
    summary="Propose an ontology change",
    description="Define, relate, alias, edit, merge, split, reparent or deprecate concepts. A "
    "human's change applies at once; an agent's is held for review (unless its op is configured "
    "to auto-apply). The "
    "result's `advice` lists similar existing concepts: prefer an alias or a merge to a duplicate.",
)
async def propose(
    body: ChangeRequest, caller: CallerDep, service: OntologyChangeServiceDep
) -> ChangeResult:
    return await service.propose(caller, body)


@router.post(
    "/preview",
    summary="Say what an ontology change would do, without proposing it",
    description="Validates the change and returns its effects: the concepts it touches, the live "
    "memories about them, one line per effect, and advice (for example a definition that "
    "reads like a different meaning). Nothing is written.",
)
async def preview(
    body: ChangeRequest, caller: CallerDep, service: OntologyChangeServiceDep
) -> ChangePreview:
    return await service.preview(caller, body)


@router.get(
    "",
    summary="Ontology changes: held for review (default) or the history",
    description="`pending=false` is the investigation view: applied and decided changes, newest "
    "first, optionally for one `concept` or one `op`.",
)
async def list_changes(
    caller: CallerDep,
    service: OntologyChangeServiceDep,
    pending: Annotated[bool, Query()] = True,
    concept: Annotated[str | None, Query()] = None,
    op: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[OntologyChange]:
    if pending:
        return await service.pending(caller, limit)
    target = RecordId("concept", concept.removeprefix("concept:")) if concept else None
    return await service.history(caller, limit, concept=target, op=op)


@router.get(
    "/check",
    summary="Ontology health report",
    description="Read-only: loops, dangling or missing links, shared names, deprecated concepts "
    "still in use, unused concepts. Each finding names the change that most likely caused it.",
)
async def check(caller: CallerDep, checker: OntologyCheckerDep) -> OntologyCheck:
    return await checker.check(caller)


class ApproveBody(BaseModel):
    expect_version: int | None = None


@router.post("/{change_id}/approve", summary="Approve and apply a held change (human only)")
async def approve(
    change_id: str,
    caller: CallerDep,
    service: OntologyChangeServiceDep,
    body: ApproveBody | None = None,
) -> ChangeResult:
    return await service.approve(caller, _id(change_id), body.expect_version if body else None)


@router.post(
    "/{change_id}/amend",
    summary="Amend a waiting ontology change",
    description="Replaces the change with a new version, validated again (its proposer or a "
    "human reviewer); the older version is kept in its history.",
)
async def amend(
    change_id: str, body: AmendChange, caller: CallerDep, service: OntologyChangeServiceDep
) -> ChangeResult:
    return await service.amend(caller, _id(change_id), body)


@router.post("/{change_id}/withdraw", summary="Withdraw a waiting ontology change")
async def withdraw(
    change_id: str,
    body: WithdrawRequest,
    caller: CallerDep,
    service: OntologyChangeServiceDep,
) -> ChangeResult:
    return await service.withdraw(caller, _id(change_id), body)


@router.post(
    "/{change_id}/request-changes",
    summary="Send a waiting ontology change back to its proposer (human only)",
)
async def request_changes(
    change_id: str,
    body: RequestChanges,
    caller: CallerDep,
    service: OntologyChangeServiceDep,
) -> ChangeResult:
    return await service.request_changes(caller, _id(change_id), body)


@router.post(
    "/{change_id}/revert",
    summary="Undo an applied change (human only)",
    description="Applies the change's stored undo as one new change. Refused while a later "
    "change touched the same concepts: revert those first.",
)
async def revert(
    change_id: str, body: RejectBody, caller: CallerDep, service: OntologyChangeServiceDep
) -> ChangeResult:
    return await service.revert(caller, _id(change_id), body.reason)


@router.post("/{change_id}/reject", summary="Reject a held change (human only)")
async def reject(
    change_id: str, body: RejectBody, caller: CallerDep, service: OntologyChangeServiceDep
) -> ChangeResult:
    return await service.reject(caller, _id(change_id), body.reason, body.expect_version)
