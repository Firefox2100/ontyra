from __future__ import annotations

import difflib
import itertools
import json
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ontyra.models import RecordId
from ontyra.models.ontology_change import ChangeRequest
from ontyra.models.proposals import ApproveRequest, OverlayRequest, RequestChanges
from ontyra.services.errors import ServiceError
from ontyra.services.review import depends_on
from ontyra.transports.rest.deps import MemoryServiceDep, OntologyChangeServiceDep
from ontyra.transports.web.deps import SameOriginDep, UiCallerDep, templates

router = APIRouter(prefix="/ui", tags=["ui"], include_in_schema=False)


def _back() -> RedirectResponse:
    return RedirectResponse("/ui/review", status_code=303)


@router.get("/review", response_class=HTMLResponse)
async def review(
    request: Request,
    caller: UiCallerDep,
    memory: MemoryServiceDep,
    changes: OntologyChangeServiceDep,
) -> HTMLResponse:
    waiting = await changes.pending(caller, 200)
    effects: dict[str, Any] = {}
    for c in waiting:  # what approving each one would do, as of now
        try:
            shown = await changes.preview(
                caller,
                ChangeRequest.model_validate({"change": c.change, "rationale": c.rationale}),
            )
            effects[str(c.id)] = shown.effects
        except (ServiceError, ValueError):
            effects[str(c.id)] = None
    return templates.TemplateResponse(
        request,
        "review.html",
        {
            "proposals": await memory.pending(caller, 200),
            "changes": waiting,
            "effects": effects,
            "recent": await changes.history(caller, 15),
            "nav": "review",
        },
    )


@router.get("/proposals/{proposal_id}", response_class=HTMLResponse)
async def proposal(
    request: Request, proposal_id: str, caller: UiCallerDep, memory: MemoryServiceDep
) -> HTMLResponse:
    found = await memory.get_proposal(caller, RecordId("proposal", proposal_id))
    deps = {i: sorted(d) for i, d in depends_on(found.ops).items() if d}
    return templates.TemplateResponse(
        request, "proposal.html", {"p": found, "deps": deps, "nav": "review"}
    )


def _diff(old: object, new: object) -> list[tuple[str, str]]:
    """Lines of a unified diff between two JSON-able values, tagged for colouring."""
    a = json.dumps(old, indent=2, sort_keys=True).splitlines()
    b = json.dumps(new, indent=2, sort_keys=True).splitlines()
    out: list[tuple[str, str]] = []
    for line in difflib.unified_diff(a, b, lineterm="", n=1):
        if line.startswith(("---", "+++")):
            continue
        out.append(("add" if line.startswith("+") else "del" if line.startswith("-") else "", line))
    return out


@router.get("/proposals/{proposal_id}/versions", response_class=HTMLResponse)
async def versions(
    request: Request, proposal_id: str, caller: UiCallerDep, memory: MemoryServiceDep
) -> HTMLResponse:
    p = await memory.get_proposal(caller, RecordId("proposal", proposal_id))
    steps = [*(p.history or []), {"version": p.version or 1, "ops": p.ops, "why": "current"}]
    pairs = [
        {
            "from": a["version"],
            "to": b["version"],
            "why": a.get("why"),
            "by": a.get("by"),
            "diff": _diff(a["ops"], b["ops"]),
        }
        for a, b in itertools.pairwise(steps)
    ]
    return templates.TemplateResponse(
        request, "proposal_versions.html", {"p": p, "pairs": pairs, "nav": "review"}
    )


@router.post("/proposals/{proposal_id}/approve-ops", dependencies=[SameOriginDep])
async def approve_ops(
    proposal_id: str,
    caller: UiCallerDep,
    memory: MemoryServiceDep,
    ops: Annotated[list[int], Form()],
    version: Annotated[int | None, Form()] = None,
    reason: Annotated[str, Form()] = "",
) -> RedirectResponse:
    await memory.approve(
        caller,
        RecordId("proposal", proposal_id),
        ApproveRequest(
            ops=ops, reason=reason or "not approved in the console", expect_version=version
        ),
    )
    return _back()


@router.post("/proposals/{proposal_id}/reject-ops", dependencies=[SameOriginDep])
async def reject_ops(
    proposal_id: str,
    caller: UiCallerDep,
    memory: MemoryServiceDep,
    ops: Annotated[list[int], Form()],
    version: Annotated[int | None, Form()] = None,
    reason: Annotated[str, Form()] = "rejected in the console",
) -> RedirectResponse:
    await memory.reject(
        caller, RecordId("proposal", proposal_id), reason or "rejected", ops, version
    )
    return RedirectResponse(f"/ui/proposals/{proposal_id}", status_code=303)


@router.post("/proposals/overlay", dependencies=[SameOriginDep])
async def overlay(
    caller: UiCallerDep,
    memory: MemoryServiceDep,
    ids: Annotated[list[str], Form()],
    scope: Annotated[str, Form()] = "",
    expires_at: Annotated[str, Form()] = "",
    anchor: Annotated[str, Form()] = "",
    about: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """The same fields on every `add` and `revise` of the chosen proposals, as a human's
    amendment of each (a new version)."""
    await memory.overlay(
        caller,
        OverlayRequest(
            proposals=[RecordId("proposal", pid) for pid in ids],
            scope=scope if scope in ("contextual", "universal") else None,  # type: ignore[arg-type]
            expires_at=expires_at.strip() or None,
            anchor=anchor.strip() or None,
            about=[a.strip() for a in about.split(",") if a.strip()],
        ),
    )
    return _back()


@router.post("/proposals/{proposal_id}/approve", dependencies=[SameOriginDep])
async def approve(
    proposal_id: str, caller: UiCallerDep, memory: MemoryServiceDep
) -> RedirectResponse:
    await memory.approve(caller, RecordId("proposal", proposal_id))
    return _back()


@router.post("/proposals/{proposal_id}/request-changes", dependencies=[SameOriginDep])
async def request_changes(
    proposal_id: str,
    caller: UiCallerDep,
    memory: MemoryServiceDep,
    comment: Annotated[str, Form()],
) -> RedirectResponse:
    await memory.request_changes(
        caller, RecordId("proposal", proposal_id), RequestChanges(comment=comment)
    )
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


@router.post("/ontology/changes/{change_id}/request-changes", dependencies=[SameOriginDep])
async def request_ontology_changes(
    change_id: str,
    caller: UiCallerDep,
    changes: OntologyChangeServiceDep,
    comment: Annotated[str, Form()],
) -> RedirectResponse:
    await changes.request_changes(
        caller, RecordId("ontology_change", change_id), RequestChanges(comment=comment)
    )
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
