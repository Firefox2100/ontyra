from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from ontyra.models.recall import RecallContext, RecallRequest, Signal
from ontyra.transports.rest.deps import RecallServiceDep
from ontyra.transports.web.deps import UiCallerDep, templates

router = APIRouter(prefix="/ui", tags=["ui"], include_in_schema=False)


@router.get("/recall", response_class=HTMLResponse)
async def playground(
    request: Request,
    caller: UiCallerDep,
    recall: RecallServiceDep,
    query: Annotated[str, Query()] = "",
    uri: Annotated[str, Query()] = "",
    signals: Annotated[str, Query()] = "",
    mode: Annotated[str, Query()] = "recall",
) -> HTMLResponse:
    """Run a recall or briefing as a human would see it. No session is sent, so it never
    touches the delivery ledger of a real session."""
    pack = None
    if query.strip() or mode == "briefing":
        context = RecallContext(
            uris=[u.strip() for u in uri.split(",") if u.strip()],
            signals=[Signal(concept=s.strip()) for s in signals.split(",") if s.strip()],
        )
        req = RecallRequest(context=context, query=query.strip() or None)
        pack = await (
            recall.briefing(caller, req) if mode == "briefing" else recall.recall(caller, req)
        )
    return templates.TemplateResponse(
        request,
        "recall.html",
        {"pack": pack, "query": query, "uri": uri, "signals": signals, "mode": mode,
         "nav": "recall"},
    )  # fmt: skip
