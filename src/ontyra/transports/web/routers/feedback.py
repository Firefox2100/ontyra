from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ontyra.models import RecordId
from ontyra.models.feedback import ExclusionRequest
from ontyra.transports.rest.deps import FeedbackReviewDep, FeedbackServiceDep
from ontyra.transports.web.deps import SameOriginDep, UiCallerDep, templates

router = APIRouter(prefix="/ui", tags=["ui"], include_in_schema=False)


def _back(message: str | None = None) -> RedirectResponse:
    target = "/ui/feedback" + (f"?note={quote(message)}" if message else "")
    return RedirectResponse(target, status_code=303)


@router.get("/feedback", response_class=HTMLResponse)
async def feedback_page(
    request: Request,
    caller: UiCallerDep,
    review: FeedbackReviewDep,
    service: FeedbackServiceDep,
    note: str | None = None,
    everyone: bool = False,
) -> HTMLResponse:
    admin = caller.has("admin")
    exclusions = await review.exclusions(caller)
    return templates.TemplateResponse(
        request,
        "feedback.html",
        {
            "outliers": await review.outliers(caller, limit=100, everyone=everyone and admin),
            "vetoed": await service.list(caller, "vetoed"),
            "exclusions": exclusions,
            "opted_out": any(e.kind == "opt_out" and e.target == caller.owner for e in exclusions),
            "summary": await service.summary(caller),
            "layer": await service.layer_state(caller),
            "admin": admin,
            "everyone": everyone and admin,
            "note": note,
            "nav": "feedback",
        },
    )


@router.post("/feedback/{feedback_id}/veto", dependencies=[SameOriginDep])
async def veto(
    feedback_id: str,
    caller: UiCallerDep,
    review: FeedbackReviewDep,
    reason: Annotated[str, Form()] = "",
) -> RedirectResponse:
    await review.veto(caller, RecordId("feedback", feedback_id), reason)
    return _back("vetoed: it no longer counts")


@router.post("/feedback/{feedback_id}/unveto", dependencies=[SameOriginDep])
async def unveto(
    feedback_id: str, caller: UiCallerDep, review: FeedbackReviewDep
) -> RedirectResponse:
    await review.unveto(caller, RecordId("feedback", feedback_id))
    return _back("put back")


@router.post("/feedback/quarantine", dependencies=[SameOriginDep])
async def quarantine(
    caller: UiCallerDep,
    review: FeedbackReviewDep,
    scope: Annotated[str, Form()],
    target: Annotated[str, Form()],
    reason: Annotated[str, Form()] = "",
) -> RedirectResponse:
    if scope not in ("source", "owner"):
        return _back("unknown scope")
    await review.exclude(
        caller,
        ExclusionRequest(
            kind="quarantine",
            scope=scope,
            target=RecordId.parse(target.strip()),
            reason=reason,  # type: ignore[arg-type]
        ),
    )
    return _back("quarantined")


@router.post("/feedback/exclusions/{exclusion_id}/lift", dependencies=[SameOriginDep])
async def lift(
    exclusion_id: str, caller: UiCallerDep, review: FeedbackReviewDep
) -> RedirectResponse:
    await review.lift(caller, RecordId("learning_exclusion", exclusion_id))
    return _back("lifted")


@router.post("/feedback/opt-out", dependencies=[SameOriginDep])
async def opt_out(
    caller: UiCallerDep, review: FeedbackReviewDep, enabled: Annotated[bool, Form()] = True
) -> RedirectResponse:
    await review.opt_out(caller, enabled)
    return _back("opted out of training" if enabled else "opted back in")
