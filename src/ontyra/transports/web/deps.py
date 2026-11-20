"""Dependencies for the operator console: the human caller and a cross-site POST guard."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates

from ontyra.services import Caller, Principal
from ontyra.transports.rest.deps import ServicesDep

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


SESSION_COOKIE = "ontyra_session"


def sessions(request: Request) -> dict[str, tuple[Principal, datetime]]:
    """Signed-in console sessions (id -> who, until when), kept in memory: a restart signs
    everyone out."""
    store: dict[str, tuple[Principal, datetime]] = request.app.state.__dict__.setdefault(
        "ui_sessions", {}
    )
    now = datetime.now(UTC)
    for sid, (_, expires) in list(store.items()):
        if expires < now:
            store.pop(sid, None)
    return store


def _principal_key(principal: Principal) -> str:
    if principal.credential is not None and principal.credential.id is not None:
        return str(principal.credential.id)
    return "single-user"


def open_session(request: Request, principal: Principal) -> tuple[str, int]:
    hours = getattr(request.app.state, "ui_session_hours", 12)
    expires = datetime.now(UTC) + timedelta(hours=hours)
    sid = secrets.token_urlsafe(32)
    store = sessions(request)
    key = _principal_key(principal)
    for old_sid, (old_principal, _) in list(store.items()):
        if _principal_key(old_principal) == key:
            store.pop(old_sid, None)
    store[sid] = (principal, expires)
    return sid, hours * 3600


async def get_ui_caller(request: Request, services: ServicesDep) -> Caller:
    """The console's caller. With no credential required it acts as the configured `human`.
    Otherwise it needs a signed-in session (`/ui/login`, a human credential or the token);
    without one the browser is sent to the sign-in page."""
    name = getattr(request.app.state, "ui_actor", "owner")
    if not services.auth.required:
        return await services.auth.caller(Principal(), label=name, kind="human", harness="web-ui")
    entry = sessions(request).get(request.cookies.get(SESSION_COOKIE, ""))
    if entry is None or entry[1] < datetime.now(UTC):
        raise HTTPException(303, headers={"Location": "/ui/login"})
    principal = await services.auth.refresh(entry[0])
    if principal is None:
        sessions(request).pop(request.cookies.get(SESSION_COOKIE, ""), None)
        raise HTTPException(303, headers={"Location": "/ui/login"})
    return await services.auth.caller(principal, label=name, kind="human", harness="web-ui")


UiCallerDep = Annotated[Caller, Depends(get_ui_caller)]


async def same_origin(request: Request) -> None:
    """Refuse a state-changing request that a browser sends from another site's page."""
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        raise HTTPException(403, "origin header required")
    parsed = urlparse(origin)
    candidate = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    configured: set[str] = getattr(request.app.state, "ui_allowed_origins", set())
    host = request.headers.get("host", "").lower()
    request_origin = f"{request.url.scheme}://{host}"
    if configured:
        allowed = candidate in configured
    else:
        # no allowlist: the console is for the machine itself, so the page must have been loaded
        # from a loopback name (a DNS-rebinding page reaches 127.0.0.1 under its own hostname)
        allowed = candidate == request_origin and _loopback(host)
    if not allowed:
        raise HTTPException(403, "cross-site request refused")


def _loopback(host: str) -> bool:
    name = host.rsplit(":", 1)[0] if not host.startswith("[") else host.partition("]")[0] + "]"
    return name in ("localhost", "[::1]") or name.startswith("127.")


SameOriginDep = Depends(same_origin)
