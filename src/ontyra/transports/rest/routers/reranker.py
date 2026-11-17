from __future__ import annotations

from fastapi import APIRouter

from ontyra.models.feedback import RerankerRun, RerankerStatus, TrainOptions
from ontyra.transports.rest.deps import AdminDep, RerankingServiceDep

router = APIRouter(prefix="/reranker", tags=["feedback"])


@router.get(
    "",
    summary="Reranker status",
    description="Which reranker is configured, whether it can be retrained (hosted ones "
    "cannot), its active version, the feedback available and recent training runs.",
)
async def status(service: RerankingServiceDep) -> RerankerStatus:
    return await service.status()


@router.post(
    "/train",
    summary="Retrain the reranker from the feedback that still counts",
    description="Started by a human, never automatically. Needs a trainable reranker and enough "
    "feedback pairs. The result is activated only if it beats the version it started from on "
    "held-out pairs; every run is recorded.",
)
async def train(
    caller: AdminDep, service: RerankingServiceDep, options: TrainOptions | None = None
) -> RerankerRun:
    return await service.train(caller, options)
