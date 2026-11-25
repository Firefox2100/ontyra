from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from ontyra.transports.rest.deps import (
    MemoryServiceDep,
    OntologyChangeServiceDep,
    RecallServiceDep,
)
from ontyra.transports.web.deps import UiCallerDep, templates

router = APIRouter(prefix="/ui", tags=["ui"], include_in_schema=False)
_LOGO = Path(__file__).parents[1] / "assets" / "ontyra-mark.svg"


@router.get("")
async def root() -> RedirectResponse:
    return RedirectResponse("/ui/")


@router.get("/assets/ontyra-mark.svg", response_class=FileResponse)
async def logo() -> FileResponse:
    return FileResponse(_LOGO, media_type="image/svg+xml")


@router.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    caller: UiCallerDep,
    memory: MemoryServiceDep,
    changes: OntologyChangeServiceDep,
    recall: RecallServiceDep,
) -> HTMLResponse:
    pending = await memory.pending(caller, 200)
    held = await changes.pending(caller, 200)
    kinds = {}
    for kind in ("directive", "fact", "decision", "event", "procedure", "task", "lesson"):
        kinds[kind] = len(await memory.list_memories(caller, kind=kind, limit=500))  # type: ignore[arg-type]
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "pending": len(pending),
            "held": len(held),
            "kinds": kinds,
            "nav": "home",
            "calibration": recall.calibration_note(),
        },
    )
