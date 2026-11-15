from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel

from ontyra.models import RecordId
from ontyra.models.core import Proposal
from ontyra.models.proposals import ProposalRequest, ProposalResult
from ontyra.transports.rest.deps import CallerDep, MemoryServiceDep

router = APIRouter(prefix="/proposals", tags=["proposals"])

_STATUS_CODE = {"committed": 201, "pending": 202, "rejected": 422}


def _id(proposal_id: str) -> RecordId:
    return RecordId("proposal", proposal_id)


@router.post(
    "",
    summary="Propose changes to memory",
    description=(
        "The only way to write memories. A proposal is an all-or-nothing batch of operations "
        "(`add`, `revise`, `supersede`, `retract`, `link`, `annotate`). It is validated, then "
        "the commit policy decides: **201** committed, **202** held for review, **422** "
        "rejected (the body lists the findings). Warnings and info findings are advice, e.g. "
        "a possible duplicate you may want to supersede."
    ),
    response_model=ProposalResult,
    responses={201: {"model": ProposalResult}, 202: {"model": ProposalResult},
               422: {"model": ProposalResult}},
)  # fmt: skip
async def submit(
    body: ProposalRequest, caller: CallerDep, memory: MemoryServiceDep, response: Response
) -> ProposalResult:
    result = await memory.submit(caller, body)
    response.status_code = _STATUS_CODE[result.status]
    return result


@router.get("", summary="List proposals")
async def list_proposals(
    memory: MemoryServiceDep,
    status: Annotated[Literal["pending"], Query()] = "pending",
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[Proposal]:
    return await memory.pending(limit)


@router.get("/{proposal_id}", summary="Get one proposal")
async def get_proposal(proposal_id: str, memory: MemoryServiceDep) -> Proposal:
    return await memory.get_proposal(_id(proposal_id))


@router.post(
    "/{proposal_id}/approve",
    summary="Approve a pending proposal",
    description="Re-validates against the data as it is now, then commits atomically.",
)
async def approve(proposal_id: str, caller: CallerDep, memory: MemoryServiceDep) -> ProposalResult:
    return await memory.approve(caller, _id(proposal_id))


class RejectIn(BaseModel):
    reason: str


@router.post("/{proposal_id}/reject", summary="Reject a pending proposal")
async def reject(
    proposal_id: str, body: RejectIn, caller: CallerDep, memory: MemoryServiceDep
) -> ProposalResult:
    return await memory.reject(caller, _id(proposal_id), body.reason)
