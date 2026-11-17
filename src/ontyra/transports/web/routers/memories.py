from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from ontyra.models import RecordId
from ontyra.transports.rest.deps import ExplorationServiceDep, MemoryServiceDep
from ontyra.transports.web.deps import UiCallerDep, templates

router = APIRouter(prefix="/ui", tags=["ui"], include_in_schema=False)

KINDS = ["fact", "directive", "decision", "event", "procedure", "task", "lesson"]
STATUSES = ["active", "superseded", "retracted", "expired"]


@router.get("/memories", response_class=HTMLResponse)
async def memories(
    request: Request,
    caller: UiCallerDep,
    memory: MemoryServiceDep,
    kind: Annotated[str, Query()] = "",
    status: Annotated[str, Query()] = "active",
) -> HTMLResponse:
    rows = await memory.list_memories(
        caller,
        kind=kind if kind in KINDS else None,  # type: ignore[arg-type]
        status=status if status in STATUSES else "active",  # type: ignore[arg-type]
        limit=500,
    )
    return templates.TemplateResponse(
        request,
        "memories.html",
        {"rows": rows, "kind": kind, "status": status, "kinds": KINDS, "statuses": STATUSES,
         "nav": "memories"},
    )  # fmt: skip


@router.get("/memories/{memory_id}", response_class=HTMLResponse)
async def memory_detail(
    request: Request, memory_id: str, caller: UiCallerDep, explore: ExplorationServiceDep
) -> HTMLResponse:
    from ontyra.models.recall import RecallContext

    detail = await explore.get(
        caller, RecordId("memory", memory_id), ["history", "links", "evidence"], RecallContext()
    )
    return templates.TemplateResponse(request, "memory.html", {"d": detail, "nav": "memories"})
