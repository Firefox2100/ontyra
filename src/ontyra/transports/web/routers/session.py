from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ontyra.transports.rest.deps import ServicesDep
from ontyra.transports.web.deps import (
    SESSION_COOKIE,
    SameOriginDep,
    open_session,
    sessions,
    templates,
)

router = APIRouter(prefix="/ui", include_in_schema=False)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, services: ServicesDep) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "login.html", {"nav": "", "needed": services.auth.required, "error": None}
    )


@router.post("/login", dependencies=[SameOriginDep], response_model=None)
async def login(
    request: Request, services: ServicesDep, token: Annotated[str, Form()]
) -> RedirectResponse | HTMLResponse:
    principal = await services.auth.authenticate(token.strip())
    error = None
    if principal is None:
        error = "That token is not valid."
    elif principal.credential is not None and principal.credential.kind != "human":
        error = "That is an agent credential. Sign in with a human credential."
    if error is not None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"nav": "", "needed": True, "error": error},
            status_code=401,
        )
    assert principal is not None
    sid, max_age = open_session(request, principal)
    response = RedirectResponse("/ui/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        sid,
        max_age=max_age,
        httponly=True,
        secure=getattr(request.app.state, "ui_cookie_secure", False),
        samesite="strict",
        path="/ui",
    )
    return response


@router.post("/logout", dependencies=[SameOriginDep])
async def logout(request: Request) -> RedirectResponse:
    sessions(request).pop(request.cookies.get(SESSION_COOKIE, ""), None)
    response = RedirectResponse("/ui/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/ui")
    return response
