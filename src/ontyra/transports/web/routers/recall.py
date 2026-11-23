from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ontyra.models import RecordId
from ontyra.models.feedback import FeedbackLabel, FeedbackRequest
from ontyra.models.recall import RecallContext, RecallRequest, Signal
from ontyra.services import errors
from ontyra.transports.rest.deps import FeedbackServiceDep, RecallServiceDep
from ontyra.transports.web.deps import SameOriginDep, UiCallerDep, templates

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
         "back": str(request.url.path) + ("?" + request.url.query if request.url.query else ""),
         "nav": "recall"},
    )  # fmt: skip


LABELS: tuple[FeedbackLabel, ...] = ("helpful", "irrelevant", "outdated", "wrong")


@router.post("/recall/feedback", dependencies=[SameOriginDep])
async def judge(
    caller: UiCallerDep,
    service: FeedbackServiceDep,
    memory: Annotated[str, Form()],
    query: Annotated[str, Form()],
    label: Annotated[str, Form()],
    trace: Annotated[str, Form()] = "",
    back: Annotated[str, Form()] = "/ui/recall",
) -> RedirectResponse:
    """A verdict on one recalled memory, tied to the recall (its trace) so the lane scores that
    produced it can be learned from."""
    if label not in LABELS:
        raise errors.InvalidRequestError("unknown label")
    await service.record(
        caller,
        FeedbackRequest(
            memory=RecordId.parse(memory),
            query=query,
            label=label,  # type: ignore[arg-type]
            trace=RecordId.parse(trace) if trace else None,
        ),
    )
    target = back if back.startswith("/ui/recall") else "/ui/recall"
    return RedirectResponse(target, status_code=303)
