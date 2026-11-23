from __future__ import annotations

from fastapi import APIRouter

from ontyra.models.feedback import LiveResult, RerankerRun, RerankerStatus, TrainOptions
from ontyra.transports.rest.deps import AdminDep, RerankingServiceDep

router = APIRouter(prefix="/reranker", tags=["feedback"])


@router.get(
    "",
    summary="Reranker status",
    description="Which reranker is configured, whether it can be retrained (hosted ones "
    "cannot), its active version, the feedback available and recent training runs.",
)
async def status(_caller: AdminDep, service: RerankingServiceDep) -> RerankerStatus:
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


@router.post(
    "/live",
    summary="Refit the live head edit from the feedback that counts now",
    description="The reranker's one-logit head is nudged from feedback, within a cap on how far it "
    "may move from the version's own head, and only if the edit does not rank the benchmark's "
    "trusted requests worse. It is a replay: refitted from zero every time, so a vetoed example "
    "simply stops contributing. Needs `ONTYRA_RERANK__LIVE` and `POOL=all`. With live edits on "
    "this also runs by itself, batched, after feedback changes.",
)
async def live(caller: AdminDep, service: RerankingServiceDep) -> LiveResult:
    return await service.live_update(caller)


@router.post("/live/reset", summary="Remove the live head edit")
async def live_reset(caller: AdminDep, service: RerankingServiceDep) -> LiveResult:
    return await service.live_reset(caller)
