from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ontyra.models import RecordId
from ontyra.models.tasks import TaskResult
from ontyra.services import errors
from ontyra.transports.rest.deps import TaskServiceDep
from ontyra.transports.web.deps import SameOriginDep, UiCallerDep, templates

router = APIRouter(prefix="/ui", tags=["ui"], include_in_schema=False)

OPEN = ("pending", "claimed", "parked")


def _back(message: str | None = None) -> RedirectResponse:
    target = "/ui/tasks" + (f"?note={quote(message)}" if message else "")
    return RedirectResponse(target, status_code=303)


@router.get("/tasks", response_class=HTMLResponse)
async def tasks(
    request: Request, caller: UiCallerDep, service: TaskServiceDep, note: str | None = None
) -> HTMLResponse:
    maintenance = (
        "consolidate",
        "review_contradiction",
        "review_stale_dependent",
        "review_feedback",
        "review_source",
        "expire_check",
    )
    return templates.TemplateResponse(
        request,
        "tasks.html",
        {
            "tasks": await service.list(caller, kinds=maintenance, statuses=OPEN, limit=200),
            "recent": await service.list(caller, kinds=maintenance, statuses=("done",), limit=10),
            "note": note,
            "nav": "tasks",
        },
    )


@router.post("/tasks/detect", dependencies=[SameOriginDep])
async def detect(caller: UiCallerDep, service: TaskServiceDep) -> RedirectResponse:
    report = await service.detect(caller)
    return _back(
        f"{len(report.created)} new, {report.existing} already queued, {report.closed} closed, "
        f"{report.reopened} reopened"
    )


@router.post("/tasks/{task_id}/resolve", dependencies=[SameOriginDep])
async def resolve(
    task_id: str, caller: UiCallerDep, service: TaskServiceDep, decision: Annotated[str, Form()]
) -> RedirectResponse:
    if decision not in ("merge", "supersede", "retract"):
        raise errors.InvalidRequestError("unknown decision")
    done = await service.resolve(caller, RecordId("task", task_id), decision)  # type: ignore[arg-type]
    return _back("done" if done.status == "done" else "held for review")


@router.post("/tasks/{task_id}/complete", dependencies=[SameOriginDep])
async def complete(
    task_id: str,
    caller: UiCallerDep,
    service: TaskServiceDep,
    decision: Annotated[str, Form()],
    note: Annotated[str, Form()] = "",
) -> RedirectResponse:
    if decision not in ("distinct", "keep", "defer"):
        raise errors.InvalidRequestError("unknown decision")
    await service.complete(
        caller,
        RecordId("task", task_id),
        TaskResult(decision=decision, note=note or None),  # type: ignore[arg-type]
    )
    return _back()
