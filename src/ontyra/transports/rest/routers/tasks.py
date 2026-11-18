from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from ontyra.models import RecordId
from ontyra.models.core import TaskKind
from ontyra.models.tasks import DetectReport, TaskResult, TaskView
from ontyra.transports.rest.deps import CallerDep, TaskServiceDep

router = APIRouter(tags=["tasks"])


def _task(task_id: str) -> RecordId:
    return RecordId("task", task_id.removeprefix("task:"))


@router.get(
    "/tasks",
    summary="Open maintenance tasks",
    description="Work the daemon found but cannot judge: duplicate clusters, updates to confirm, "
    "memories gone stale. Each task carries a card with the choices. `status` may repeat "
    "(default: pending and claimed).",
)
async def list_tasks(
    caller: CallerDep,
    service: TaskServiceDep,
    kind: Annotated[list[TaskKind] | None, Query()] = None,
    status: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[TaskView]:
    return await service.list(
        caller, kinds=kind or [], statuses=status or ("pending", "claimed"), limit=limit
    )


@router.post(
    "/tasks/detect",
    summary="Look for new maintenance work",
    description="Scans for duplicates and updates, queues each finding once, and closes tasks "
    "whose memories changed. Changes no memory.",
)
async def detect(caller: CallerDep, service: TaskServiceDep) -> DetectReport:
    return await service.detect(caller)


@router.get("/tasks/{task_id}", summary="One task with its card")
async def get_task(task_id: str, caller: CallerDep, service: TaskServiceDep) -> TaskView:
    return await service.get(caller, _task(task_id))


@router.post("/tasks/{task_id}/claim", summary="Claim a task")
async def claim(task_id: str, caller: CallerDep, service: TaskServiceDep) -> TaskView:
    return await service.claim(caller, _task(task_id))


@router.post("/tasks/{task_id}/release", summary="Give a claimed task back")
async def release(task_id: str, caller: CallerDep, service: TaskServiceDep) -> TaskView:
    return await service.release(caller, _task(task_id))


@router.post(
    "/tasks/{task_id}/complete",
    summary="Finish a task",
    description="`merge` and `supersede` must cite the proposal that did it; `distinct` records "
    "that the memories differ (not raised again); `defer` parks the task.",
)
async def complete(
    task_id: str, result: TaskResult, caller: CallerDep, service: TaskServiceDep
) -> TaskView:
    return await service.complete(caller, _task(task_id), result)
