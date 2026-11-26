from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Header, Query, Response

from ontyra.models import RecordId
from ontyra.models.core import Proposal
from ontyra.models.proposals import (
    AmendRequest,
    ApproveRequest,
    ProposalRequest,
    ProposalResult,
    RejectRequest,
    RequestChanges,
    WithdrawRequest,
)
from ontyra.services.anchors import anchored_to
from ontyra.transports.rest.deps import CallerDep, MemoryServiceDep

router = APIRouter(prefix="/proposals", tags=["proposals"])

_STATUS_CODE = {"committed": 201, "pending": 202, "rejected": 422}  # submit only


def _id(proposal_id: str) -> RecordId:
    """The record a path id names, whether or not it still carries the `proposal:` prefix.

    Proposal ids are shown to clients as `proposal:abc`; pasting one back into a path used to build
    `proposal:proposal:abc` and answer 404 for a proposal that exists.
    """
    table, sep, ident = proposal_id.partition(":")
    return RecordId("proposal", ident if sep and table == "proposal" else proposal_id)


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
    body: ProposalRequest,
    caller: CallerDep,
    memory: MemoryServiceDep,
    response: Response,
    x_ontyra_machine: Annotated[
        str | None,
        Header(
            description="The machine the caller runs on (its hostname). Every `file:` anchor in "
            "the proposal is recorded against it, so a path anchor copied from elsewhere cannot "
            "make the memory quietly unmatchable."
        ),
    ] = None,
) -> ProposalResult:
    if x_ontyra_machine:
        body = anchored_to(body, x_ontyra_machine)
    result = await memory.submit(caller, body)
    response.status_code = _STATUS_CODE[result.status]
    return result


@router.get("", summary="List proposals")
async def list_proposals(
    caller: CallerDep,
    memory: MemoryServiceDep,
    status: Annotated[Literal["pending"], Query()] = "pending",  # also lists changes_requested
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[Proposal]:
    return await memory.pending(caller, limit)


@router.get("/{proposal_id}", summary="Get one proposal")
async def get_proposal(proposal_id: str, caller: CallerDep, memory: MemoryServiceDep) -> Proposal:
    return await memory.get_proposal(caller, _id(proposal_id))


@router.post(
    "/{proposal_id}/approve",
    summary="Approve a waiting proposal (all of it, or chosen ops)",
    description="Re-validates against the data as it is now, then commits atomically. With "
    "`ops` only those ops (and the ops they need) commit and the rest are rejected with `reason`. "
    "`expect_version` refuses the approval when the proposal was amended since you read it.",
)
async def approve(
    proposal_id: str,
    caller: CallerDep,
    memory: MemoryServiceDep,
    body: ApproveRequest | None = None,
) -> ProposalResult:
    return await memory.approve(caller, _id(proposal_id), body)


@router.post(
    "/{proposal_id}/reject",
    summary="Reject a waiting proposal (all of it, or chosen ops)",
    description="With `ops`, only those ops (and what needs them) are rejected; the rest keeps "
    "waiting as a new version.",
)
async def reject(
    proposal_id: str, body: RejectRequest, caller: CallerDep, memory: MemoryServiceDep
) -> ProposalResult:
    return await memory.reject(caller, _id(proposal_id), body.reason, body.ops, body.expect_version)


@router.post(
    "/{proposal_id}/amend",
    summary="Amend a waiting proposal",
    description="Replaces the ops with a new version, validated again; the older version is kept "
    "in the proposal's history. The submitter or a reviewer may amend while it is waiting "
    "(pending or changes requested).",
)
async def amend(
    proposal_id: str, body: AmendRequest, caller: CallerDep, memory: MemoryServiceDep
) -> ProposalResult:
    return await memory.amend(caller, _id(proposal_id), body)


@router.post(
    "/{proposal_id}/withdraw",
    summary="Withdraw a waiting proposal",
    description="Final: nothing is committed. Its submitter or a reviewer.",
)
async def withdraw(
    proposal_id: str, body: WithdrawRequest, caller: CallerDep, memory: MemoryServiceDep
) -> ProposalResult:
    return await memory.withdraw(caller, _id(proposal_id), body)


@router.post(
    "/{proposal_id}/request-changes",
    summary="Send a waiting proposal back to its submitter with comments",
    description="A human reviewer only. The proposal leaves the approve queue until it is amended.",
)
async def request_changes(
    proposal_id: str, body: RequestChanges, caller: CallerDep, memory: MemoryServiceDep
) -> ProposalResult:
    return await memory.request_changes(caller, _id(proposal_id), body)
