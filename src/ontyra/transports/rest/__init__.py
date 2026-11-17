"""REST transport (FastAPI): one application, routers mounted from `routers/`."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI

from ontyra import __version__
from ontyra.transports.rest.deps import Services
from ontyra.transports.rest.errors import install_error_handlers
from ontyra.transports.rest.routers import UNVERSIONED, VERSIONED

DESCRIPTION = """\
Structured, typed memory for agents. Writes go through **proposals** (validated, then
committed or held for review by policy); reads return current memories with their history.

There is no authentication yet. Identify the caller with `X-Ontyra-Actor` (and optionally
`X-Ontyra-Actor-Kind`, `X-Ontyra-Harness`, `X-Ontyra-Owner`); run on localhost only.
"""


def create_app(
    services: Services | None = None,
    *,
    open_services: Callable[[], AbstractAsyncContextManager[Services]] | None = None,
    mounts: Mapping[str, Any] | None = None,
    extra_routers: Sequence[APIRouter] = (),
    ui_actor: str | None = None,
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
