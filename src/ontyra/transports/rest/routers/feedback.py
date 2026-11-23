from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from ontyra.models import RecordId
from ontyra.models.feedback import (
    ExclusionRequest,
    ExclusionView,
    FeedbackRequest,
    FeedbackSummary,
    FeedbackView,
    LayerState,
    OptOutRequest,
    OutlierView,
    VetoRequest,
)
from ontyra.transports.rest.deps import CallerDep, FeedbackReviewDep, FeedbackServiceDep

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Record feedback on a recalled memory",
    description="Judge one memory for one query: `applied`, `helpful`, `missed` (it should have "
    "come back), `irrelevant`, `outdated` or `wrong`. Events are kept; a more trusted verdict "
    "for the same memory and query replaces a less trusted one, and events expire.",
)
async def record(
    request: FeedbackRequest, caller: CallerDep, service: FeedbackServiceDep
) -> FeedbackView:
    return await service.record(caller, request)


@router.get("", summary="List feedback events")
async def list_feedback(
    caller: CallerDep,
    service: FeedbackServiceDep,
    state: str | None = None,
    memory: str | None = None,
) -> list[FeedbackView]:
    return await service.list(caller, state, RecordId.parse(f"memory:{memory}") if memory else None)


@router.get("/summary", summary="Counts by state and label, and how many pairs can train")
async def summary(caller: CallerDep, service: FeedbackServiceDep) -> FeedbackSummary:
    return await service.summary(caller)


@router.get(
    "/layer",
    summary="What the score layer does with your feedback",
    description="The score layer corrects recall's ranking from your own feedback (a prior per "
    "memory and a blend of the lane scores), within bounds, and only while a held-out canary "
    "says it helps. `status` is `off`, `learning` (not enough feedback yet), `active` or "
    "`reverted` (the canary got worse; the plain ranking stands).",
)
async def layer(caller: CallerDep, service: FeedbackServiceDep) -> LayerState:
    return await service.layer_state(caller)


@router.post("/{feedback_id}/retract", summary="Withdraw a feedback event")
async def retract(feedback_id: str, caller: CallerDep, service: FeedbackServiceDep) -> FeedbackView:
    return await service.retract(caller, RecordId("feedback", feedback_id))


@router.get(
    "/outliers",
    summary="Examples that most deserve a look",
    description="Your active feedback, ranked by how likely it is to be a mistake or poison: it "
    "disagrees with what the ranking believed at recall time, a verdict on the same memory and "
    "query flipped, other owners judged the shared memory the other way (`everyone`, admin only) "
    "or one source sent a burst. Veto what is wrong. Needs a human credential.",
)
async def outliers(
    caller: CallerDep,
    service: FeedbackReviewDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    everyone: bool = False,
) -> list[OutlierView]:
    return await service.outliers(caller, limit=limit, everyone=everyone)


@router.post(
    "/{feedback_id}/veto",
    summary="Take an example out of learning",
    description="The example no longer counts for the score layer or any training, and stays out "
    "if the same source sends it again; the layer is replayed without it. Your own events, or any "
    "for an administrator.",
)
async def veto(
    feedback_id: str, request: VetoRequest, caller: CallerDep, service: FeedbackReviewDep
) -> FeedbackView:
    return await service.veto(caller, RecordId("feedback", feedback_id), request.reason)


@router.post("/{feedback_id}/unveto", summary="Put a vetoed example back")
async def unveto(feedback_id: str, caller: CallerDep, service: FeedbackReviewDep) -> FeedbackView:
    return await service.unveto(caller, RecordId("feedback", feedback_id))


@router.get("/exclusions", summary="Who is kept out of learning")
async def exclusions(caller: CallerDep, service: FeedbackReviewDep) -> list[ExclusionView]:
    return await service.exclusions(caller)


@router.post(
    "/exclusions",
    status_code=status.HTTP_201_CREATED,
    summary="Quarantine a source or an owner, or opt an owner out of training",
    description="`quarantine` (administrator): the source's or owner's events count nowhere. "
    "`opt_out` (an owner, for themselves): their feedback and text never enter training; their "
    "own score layer still learns from it.",
)
async def exclude(
    request: ExclusionRequest, caller: CallerDep, service: FeedbackReviewDep
) -> ExclusionView:
    return await service.exclude(caller, request)


@router.post("/exclusions/{exclusion_id}/lift", summary="End an exclusion")
async def lift(exclusion_id: str, caller: CallerDep, service: FeedbackReviewDep) -> ExclusionView:
    return await service.lift(caller, RecordId("learning_exclusion", exclusion_id))


@router.post(
    "/opt-out",
    summary="Keep your own feedback out of training (or stop)",
    description="Your text and verdicts never enter a training pool. Your own score layer still "
    "uses them, since it changes only your own ranking.",
)
async def opt_out(
    request: OptOutRequest, caller: CallerDep, service: FeedbackReviewDep
) -> ExclusionView | None:
    return await service.opt_out(caller, request.enabled)
