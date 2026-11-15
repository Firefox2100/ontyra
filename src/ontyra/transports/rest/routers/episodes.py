from __future__ import annotations

from typing import Any

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from ontyra.models import RecordId
from ontyra.transports.rest.deps import CallerDep, MemoryServiceDep

router = APIRouter(prefix="/episodes", tags=["episodes"])


class EpisodeIn(BaseModel):
    session: str = Field(description="Session or conversation identifier.")
    role: str = Field(description="Who produced this content, e.g. `user`, `assistant`, `tool`.")
    content: str
    meta: dict[str, Any] = {}


class EpisodeOut(BaseModel):
    id: RecordId


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Record an interaction event",
    description="Episodes are the lossless, immutable interaction log. Proposals can cite an "
    "episode as evidence, and a quoted passage must literally occur in it.",
)
async def record_episode(
    body: EpisodeIn, caller: CallerDep, memory: MemoryServiceDep
) -> EpisodeOut:
    rid = await memory.record_episode(
        caller, session=body.session, role=body.role, content=body.content, meta=body.meta
    )
    return EpisodeOut(id=rid)
