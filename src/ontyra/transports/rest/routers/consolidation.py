from __future__ import annotations

from fastapi import APIRouter

from ontyra.models.consolidation import ScanReport, ScanRequest
from ontyra.transports.rest.deps import CallerDep, ConsolidationServiceDep

router = APIRouter(tags=["consolidation"])


@router.post(
    "/consolidation/scan",
    summary="Find duplicate memories",
    description="Read-only. Reports clusters of memories of one kind that say the same thing "
    "(by embedding similarity, confirmed by the decision model when one is configured), and "
    "restatements with a changed value, which are updates, not duplicates. Nothing is merged: "
    "resolve a cluster with an ordinary proposal (`supersede` or `retract`).",
)
async def scan(
    request: ScanRequest, caller: CallerDep, service: ConsolidationServiceDep
) -> ScanReport:
    return await service.scan(caller, request)
