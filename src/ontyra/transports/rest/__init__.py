"""REST transport (FastAPI): one application, routers mounted from `routers/`."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI

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
) -> FastAPI:
    """Build the application.

    Pass ready `services` (tests, embedding) or `open_services`, an async context manager
    factory the lifespan enters and leaves (the daemon's connect/close).
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        if open_services is None:
            yield
            return
        async with open_services() as opened:
            app.state.services = opened
            yield

    app = FastAPI(title="Ontyra", version=__version__, description=DESCRIPTION, lifespan=lifespan)
    if services is not None:
        app.state.services = services
    install_error_handlers(app)
    for router in UNVERSIONED:
        app.include_router(router)
    for router in VERSIONED:
        app.include_router(router, prefix="/v1")
    return app


__all__ = ["Services", "create_app"]
