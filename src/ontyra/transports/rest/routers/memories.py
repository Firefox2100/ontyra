from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from ontyra.models import RecordId
from ontyra.services.views import History, MemoryView
from ontyra.transports.rest.deps import CallerDep, MemoryServiceDep

router = APIRouter(prefix="/memories", tags=["memories"])

KindParam = Literal["fact", "directive", "decision", "event", "procedure", "task", "lesson"]
StatusParam = Literal["active", "superseded", "retracted", "expired"]


@router.get(
    "",
    summary="List memories",
    description="Memories visible to the caller (their own and global), newest revision each. "
    "Superseded and retracted memories are hidden unless `status` asks for them.",
)
async def list_memories(
    caller: CallerDep,
    memory: MemoryServiceDep,
    kind: Annotated[KindParam | None, Query()] = None,
    status: Annotated[StatusParam, Query()] = "active",
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[MemoryView]:
    return await memory.list_memories(caller, kind=kind, status=status, limit=limit)


@router.get("/{memory_id}", summary="Get one memory (current revision)")
async def get_memory(memory_id: str, caller: CallerDep, memory: MemoryServiceDep) -> MemoryView:
    return await memory.get_memory(caller, RecordId("memory", memory_id))


@router.get(
    "/{memory_id}/history",
    summary="Revision history with supersession reasons",
    description="Every revision, oldest first, with its validity interval and which revisions "
    "superseded it and why.",
)
async def history(memory_id: str, caller: CallerDep, memory: MemoryServiceDep) -> History:
    return await memory.history(caller, RecordId("memory", memory_id))
