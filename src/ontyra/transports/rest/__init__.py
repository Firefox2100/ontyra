"""REST transport (FastAPI): one application, routers mounted from `routers/`."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI

from ontyra import __version__
from ontyra.transports.auth import AuthMiddleware
from ontyra.transports.rest.deps import Services
from ontyra.transports.rest.errors import install_error_handlers
from ontyra.transports.rest.routers import UNVERSIONED, VERSIONED

DESCRIPTION = """\
Structured, typed memory for agents. Writes go through **proposals** (validated, then
committed or held for review by policy); reads return current memories with their history.

Authentication: off by default (run on localhost only; the caller is then a label from
`X-Ontyra-Actor`, `X-Ontyra-Actor-Kind`, `X-Ontyra-Harness`). With `ONTYRA_AUTH__TOKEN` every
request needs `Authorization: Bearer <token>`. With `ONTYRA_AUTH__MULTI_USER` each account has
credentials (see `/v1/admin`); the account decides the owner, actor kind and permissions, and
the headers are ignored. An owner is never a request parameter.
"""


def create_app(
    services: Services | None = None,
    *,
    open_services: Callable[[], AbstractAsyncContextManager[Services]] | None = None,
    mounts: Mapping[str, Any] | None = None,
    extra_routers: Sequence[APIRouter] = (),
    ui_actor: str | None = None,
    ui_session_hours: int = 12,
    ui_cookie_secure: bool = False,
    ui_allowed_origins: Sequence[str] = (),
) -> FastAPI:
    """Build the application.

    Pass ready `services` (tests, embedding) or `open_services`, an async context manager
    factory the lifespan enters and leaves (the daemon's connect/close). `extra_routers` are
    mounted as given (the operator console). `mounts` are other ASGI apps (the MCP transport)
    served under a path; their own lifespans run with this one.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        async with AsyncExitStack() as stack:
            if open_services is not None:
                app.state.services = await stack.enter_async_context(open_services())
            for mounted in (mounts or {}).values():
                await stack.enter_async_context(mounted.router.lifespan_context(mounted))
            yield

    app = FastAPI(title="Ontyra", version=__version__, description=DESCRIPTION, lifespan=lifespan)
    if services is not None:
        app.state.services = services
    if ui_actor:
        app.state.ui_actor = ui_actor
    app.state.ui_session_hours = ui_session_hours
    app.state.ui_cookie_secure = ui_cookie_secure
    app.state.ui_allowed_origins = set(ui_allowed_origins)
    app.add_middleware(AuthMiddleware)
    install_error_handlers(app)
    for router in UNVERSIONED:
        app.include_router(router)
    for router in VERSIONED:
        app.include_router(router, prefix="/v1")
    for router in extra_routers:
        app.include_router(router)
    for path, mounted in (mounts or {}).items():
        app.mount(path, mounted)
    return app


__all__ = ["Services", "create_app"]
