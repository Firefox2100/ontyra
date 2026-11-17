"""Dependencies for the operator console: the human caller and a cross-site POST guard."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates

from ontyra.services import Caller
from ontyra.transports.rest.deps import ServicesDep

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


async def get_ui_caller(request: Request, services: ServicesDep) -> Caller:
    """The console acts as a `human` (it can approve and author); there is no login yet."""
    name = getattr(request.app.state, "ui_actor", "owner")
    return await services.actors.caller(kind="human", name=name, harness="web-ui")


UiCallerDep = Annotated[Caller, Depends(get_ui_caller)]


async def same_origin(request: Request) -> None:
    """Refuse a state-changing request that a browser sends from another site's page."""
    origin = request.headers.get("origin") or request.headers.get("referer")
    if origin and urlparse(origin).netloc != request.headers.get("host"):
        raise HTTPException(403, "cross-site request refused")


SameOriginDep = Depends(same_origin)
