from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ontyra.models.sources import SourceCheckRequest, SourceCheckResult
from ontyra.transports.rest.deps import CallerDep, SourceServiceDep

router = APIRouter(prefix="/sources", tags=["knowledge"])


@router.post(
    "/check",
    summary="Report what you found when you went back to a cited source",
    description="The daemon never reads sources; clients tell it. `ok`: as cited. `changed` or "
    "`missing`: the statements that cite it may no longer hold, so a review task is opened for "
    "each live memory that cites it, and recall flags them. Nothing is changed in the memories.",
)
async def check(
    request: SourceCheckRequest, caller: CallerDep, service: SourceServiceDep
) -> SourceCheckResult:
    return await service.check(caller, request)


@router.get(
    "",
    summary="The memories that cite a source, and what was last reported about it",
)
async def citing(uri: str, caller: CallerDep, service: SourceServiceDep) -> dict[str, Any]:
    return await service.citing(caller, uri)
