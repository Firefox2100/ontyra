from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ontyra import __version__

router = APIRouter(tags=["health"])


class Health(BaseModel):
    status: str
    version: str


@router.get("/health", summary="Liveness probe")
async def health() -> Health:
    return Health(status="ok", version=__version__)
