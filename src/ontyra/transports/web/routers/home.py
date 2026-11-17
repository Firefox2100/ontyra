from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ontyra.transports.rest.deps import MemoryServiceDep, OntologyChangeServiceDep
from ontyra.transports.web.deps import UiCallerDep, templates

router = APIRouter(prefix="/ui", tags=["ui"], include_in_schema=False)


@router.get("")
async def root() -> RedirectResponse:
    return RedirectResponse("/ui/")


@router.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    caller: UiCallerDep,
    memory: MemoryServiceDep,
    changes: OntologyChangeServiceDep,
) -> HTMLResponse:
    pending = await memory.pending(caller, 200)
    held = await changes.pending(200)
    kinds = {}
    for kind in ("directive", "fact", "decision", "event", "procedure", "task", "lesson"):
        kinds[kind] = len(await memory.list_memories(caller, kind=kind, limit=500))  # type: ignore[arg-type]
    return templates.TemplateResponse(
        request,
        "home.html",
        {"pending": len(pending), "held": len(held), "kinds": kinds, "nav": "home"},
    )
