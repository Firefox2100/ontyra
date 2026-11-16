from __future__ import annotations

from fastapi import APIRouter, status

from ontyra.models import RecordId
from ontyra.models.feedback import FeedbackRequest, FeedbackSummary, FeedbackView
from ontyra.transports.rest.deps import CallerDep, FeedbackServiceDep

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
    service: FeedbackServiceDep, state: str | None = None, memory: str | None = None
) -> list[FeedbackView]:
    return await service.list(state, RecordId.parse(f"memory:{memory}") if memory else None)


@router.get("/summary", summary="Counts by state and label, and how many pairs can train")
async def summary(service: FeedbackServiceDep) -> FeedbackSummary:
    return await service.summary()


@router.post("/{feedback_id}/retract", summary="Withdraw a feedback event")
async def retract(feedback_id: str, caller: CallerDep, service: FeedbackServiceDep) -> FeedbackView:
    return await service.retract(caller, RecordId("feedback", feedback_id))
