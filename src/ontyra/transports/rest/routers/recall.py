from __future__ import annotations

from fastapi import APIRouter

from ontyra.models import Model
from ontyra.models.recall import CheckResult, RecallContext, RecallPack, RecallRequest
from ontyra.transports.rest.deps import CallerDep, RecallServiceDep

router = APIRouter(tags=["recall"])


class CheckRequest(Model):
    intent: str
    context: RecallContext = RecallContext()


@router.post(
    "/recall",
    summary="Input-driven recall (path B)",
    description="What is relevant to this message in this context. Returns an empty pack "
    "(`silent: true`) when nothing clears the relevance threshold; `rendered` is ready to inject.",
)
async def recall(
    request: RecallRequest, caller: CallerDep, service: RecallServiceDep
) -> RecallPack:
    return await service.recall(caller, request)


@router.post(
    "/briefing",
    summary="Environment-driven briefing (path C)",
    description="Everything that applies here, with no question: the complete set of applicable "
    "directives plus facts and decisions anchored at this location. Use at session start, "
    "when the project changes, or after context compaction (`redeliver: true`).",
)
async def briefing(
    request: RecallRequest, caller: CallerDep, service: RecallServiceDep
) -> RecallPack:
    return await service.briefing(caller, request)


@router.post(
    "/check",
    summary="Pre-action check (path E)",
    description="Before an action with side effects: applicable directives, prerequisites and "
    "lessons for this intent, plus every applicable `must` directive.",
)
async def check(request: CheckRequest, caller: CallerDep, service: RecallServiceDep) -> CheckResult:
    return await service.check(caller, request.intent, request.context)
