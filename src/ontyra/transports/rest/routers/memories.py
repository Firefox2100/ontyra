from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from ontyra.models import RecordId
from ontyra.models.proposals import (
    CorrectionRequest,
    ForgetRequest,
    ForgetResult,
    ProposalResult,
)
from ontyra.services.views import History, MemoryView
from ontyra.transports.rest.deps import CallerDep, MemoryServiceDep

router = APIRouter(prefix="/memories", tags=["memories"])

KindParam = Literal["fact", "directive", "decision", "event", "procedure", "task", "lesson"]
StatusParam = Literal["active", "superseded", "retracted", "expired"]


def _memory_id(value: str) -> RecordId:
    """Accept the id with or without the `memory:` prefix clients are shown."""
    table, sep, ident = value.partition(":")
    return RecordId("memory", ident if sep and table == "memory" else value)


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


@router.post(
    "/{memory_id}/correct",
    summary="Correct a memory",
    description="The one call to fix a memory: `fields` overlay its current revision, so send "
    "only what changes. The result is an ordinary proposal — validated, then committed or held "
    "for review — and its findings say what happened.",
)
async def correct(
    memory_id: str,
    body: CorrectionRequest,
    caller: CallerDep,
    memory: MemoryServiceDep,
) -> ProposalResult:
    return await memory.correct(caller, _memory_id(memory_id), body)


@router.post(
    "/forget",
    summary="Forget a memory, or everything that applies under a path",
    description="Retire memories: give `memory` for one, or `uri` for every active memory "
    "anchored under that path. Nothing is deleted — each target is retracted with your reason, "
    "so the history stays and the policy still decides whether it commits now or waits.",
)
async def forget(body: ForgetRequest, caller: CallerDep, memory: MemoryServiceDep) -> ForgetResult:
    return await memory.forget(caller, body)
