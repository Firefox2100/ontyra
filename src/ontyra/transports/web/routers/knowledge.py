from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ontyra.models import RecordId
from ontyra.models.sources import SourceCheckRequest
from ontyra.services.errors import InvalidRequestError
from ontyra.transports.rest.deps import SourceServiceDep
from ontyra.transports.web.deps import SameOriginDep, UiCallerDep, templates

router = APIRouter(prefix="/ui", tags=["ui"], include_in_schema=False)


def _back(note: str | None = None, uri: str | None = None) -> RedirectResponse:
    query = [f"{k}={quote(v)}" for k, v in (("note", note), ("uri", uri)) if v]
    return RedirectResponse("/ui/knowledge" + ("?" + "&".join(query) if query else ""), 303)


@router.get("/knowledge", response_class=HTMLResponse)
async def knowledge_page(
    request: Request,
    caller: UiCallerDep,
    service: SourceServiceDep,
    uri: str | None = None,
    note: str | None = None,
) -> HTMLResponse:
    looked_up = None
    if uri:
        try:
            looked_up = await service.citing(caller, uri)
        except InvalidRequestError as exc:
            note = str(exc)
    return templates.TemplateResponse(
        request,
        "knowledge.html",
        {
            "pending": await service.unvetted(caller, 200),
            "reported": await service.reported(caller),
            "looked_up": looked_up,
            "note": note,
            "nav": "knowledge",
        },
    )


@router.post("/knowledge/vet", dependencies=[SameOriginDep])
async def vet(
    caller: UiCallerDep, service: SourceServiceDep, memory: Annotated[str, Form()]
) -> RedirectResponse:
    await service.vet(caller, RecordId.parse(memory.strip()))
    return _back("vetted: recall no longer marks it")


@router.post("/knowledge/check", dependencies=[SameOriginDep])
async def check(
    caller: UiCallerDep,
    service: SourceServiceDep,
    uri: Annotated[str, Form()],
    state: Annotated[str, Form()],
    note: Annotated[str, Form()] = "",
) -> RedirectResponse:
    if state not in ("ok", "changed", "missing"):
        return _back("unknown state", uri)
    result = await service.check(
        caller,
        SourceCheckRequest(uri=uri, state=state, note=note or None),  # type: ignore[arg-type]
    )
    opened = f", {result.tasks} review task(s) opened" if result.tasks else ""
    return _back(f"recorded {state}{opened}", result.uri)
