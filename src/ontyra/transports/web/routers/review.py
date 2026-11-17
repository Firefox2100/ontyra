from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ontyra.models import RecordId
from ontyra.transports.rest.deps import MemoryServiceDep, OntologyChangeServiceDep
from ontyra.transports.web.deps import SameOriginDep, UiCallerDep, templates

router = APIRouter(prefix="/ui", tags=["ui"], include_in_schema=False)


def _back() -> RedirectResponse:
    return RedirectResponse("/ui/review", status_code=303)


@router.get("/review", response_class=HTMLResponse)
async def review(
    request: Request, memory: MemoryServiceDep, changes: OntologyChangeServiceDep
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "review.html",
        {
            "proposals": await memory.pending(200),
            "changes": await changes.pending(200),
            "recent": await changes.history(15),
            "nav": "review",
        },
    )


@router.get("/proposals/{proposal_id}", response_class=HTMLResponse)
async def proposal(request: Request, proposal_id: str, memory: MemoryServiceDep) -> HTMLResponse:
    found = await memory.get_proposal(RecordId("proposal", proposal_id))
    return templates.TemplateResponse(request, "proposal.html", {"p": found, "nav": "review"})


@router.post("/proposals/{proposal_id}/approve", dependencies=[SameOriginDep])
async def approve(
    proposal_id: str, caller: UiCallerDep, memory: MemoryServiceDep
) -> RedirectResponse:
    await memory.approve(caller, RecordId("proposal", proposal_id))
    return _back()


@router.post("/proposals/{proposal_id}/reject", dependencies=[SameOriginDep])
async def reject(
    proposal_id: str,
    caller: UiCallerDep,
    memory: MemoryServiceDep,
    reason: Annotated[str, Form()] = "rejected in the console",
) -> RedirectResponse:
    await memory.reject(caller, RecordId("proposal", proposal_id), reason or "rejected")
    return _back()


@router.post("/ontology/changes/{change_id}/approve", dependencies=[SameOriginDep])
async def approve_change(
    change_id: str, caller: UiCallerDep, changes: OntologyChangeServiceDep
) -> RedirectResponse:
    await changes.approve(caller, RecordId("ontology_change", change_id))
    return _back()


@router.post("/ontology/changes/{change_id}/reject", dependencies=[SameOriginDep])
async def reject_change(
    change_id: str,
    caller: UiCallerDep,
    changes: OntologyChangeServiceDep,
    reason: Annotated[str, Form()] = "rejected in the console",
) -> RedirectResponse:
    await changes.reject(caller, RecordId("ontology_change", change_id), reason or "rejected")
    return _back()
