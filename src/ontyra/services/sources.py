"""Cited sources and vetting.

The daemon never reads a source. It keeps what clients report about one (`SourceCheck`) and, when
a source is reported `changed` or `missing`, opens a review task for each live memory that cites
it. Vetting is the other half: memories committed by policy without a human looking are `auto`
until a human vets them.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from ontyra.models import RecordId, new_id
from ontyra.models.core import Memory, RevState, Task
from ontyra.models.kinds import RevisionBase
from ontyra.models.sources import (
    SourceCheck,
    SourceCheckRequest,
    SourceCheckResult,
    SourceRef,
    source_check_id,
)
from ontyra.models.uri import canonical_source
from ontyra.ports import Create, Store, Update
from ontyra.services.context import GLOBAL_OWNER, Caller
from ontyra.services.errors import InvalidRequestError, NotFoundError, PermissionDeniedError
from ontyra.services.validation import rev_state_id

INSTRUCTIONS = (
    "A source this memory cites was reported {state}. Read it (it is cited below) and compare: "
    "if the memory no longer holds, retract it; if it needs correcting, supersede or revise it "
    "and complete with `revise` and that proposal; if it still stands, complete with `keep` "
    "(not raised again for this revision). `defer` parks it."
)


class SourceService:
    def __init__(
        self, store: Store, now: Callable[[], datetime], machine: str = "localhost"
    ) -> None:
        self.store = store
        self.now = now
        self.machine = machine

    def canonical(self, uri: str) -> str:
        try:
            return canonical_source(uri, self.machine)
        except ValueError as exc:
            raise InvalidRequestError(str(exc)) from exc

    async def check(self, caller: Caller, request: SourceCheckRequest) -> SourceCheckResult:
        """Record what was found at a source; for `changed` and `missing`, open one review task
        per live memory (visible to the caller) that cites it."""
        uri = self.canonical(request.uri)
        now = self.now()
        owner = GLOBAL_OWNER if request.space == "global" else caller.owner
        if not caller.can_write(owner):
            raise PermissionDeniedError("you cannot report checks for this memory space")
        check_id = source_check_id(owner, uri)
        row = SourceCheck(
            owner=owner,
            uri=uri,
            state=request.state,
            checked_at=now,
            checked_by=caller.actor_id,
            content_hash=request.content_hash,
            note=request.note,
        )
        writes: list[Any] = [
            Update(check_id, row.model_dump(mode="python", exclude={"id"}))
            if await self.store.get(check_id) is not None
            else Create.of(check_id, row)
        ]
        docs = await self.store.docs([owner], sources=[uri], limit=1000)
        opened = 0
        if request.state != "ok":
            revisions = await self.store.get_many([d.rev for d in docs])
            for d in docs:
                rev = revisions.get(d.rev)
                if not isinstance(rev, RevisionBase):
                    continue
                key = f"source:{d.rev}"
                if await self.store.select("task", {"dedupe_key": key}, limit=1):
                    continue  # raised already for this revision (a `keep` is not reopened)
                writes.append(
                    Create.of(
                        RecordId("task", new_id()),
                        Task(
                            kind="review_source",
                            owner=d.owner,
                            target=d.memory,
                            dedupe_key=key,
                            payload=self._card(d.memory, d.rev, d.kind, rev, uri, request, row),
                            created_by=caller.actor_id,
                            created_at=now,
                        ),
                    )
                )
                opened += 1
        await self.store.apply(writes)
        return SourceCheckResult(uri=uri, state=request.state, memories=len(docs), tasks=opened)

    @staticmethod
    def _card(
        memory: RecordId,
        revision: RecordId,
        kind: str,
        rev: RevisionBase,
        uri: str,
        request: SourceCheckRequest,
        row: SourceCheck,
    ) -> dict[str, Any]:
        cited: list[SourceRef] = [s for s in rev.sources if s.uri == uri]
        return {
            "finding": "source",
            "instructions": INSTRUCTIONS.format(state=request.state),
            "memory": str(memory),
            "revision": str(revision),
            "kind": kind,
            "text": rev.text,
            "source": uri,
            "cited": [s.model_dump(mode="json") for s in cited],
            "reported": {
                "state": request.state,
                "note": request.note,
                "hash": request.content_hash,
            },
            "choices": ["retract", "revise", "keep", "defer"],
        }

    async def citing(self, caller: Caller, uri: str) -> dict[str, Any]:
        """The memories that cite a source and what was last reported about it."""
        canonical = self.canonical(uri)
        owners = caller.visible_owners()
        docs = await self.store.docs(owners, sources=[canonical], limit=1000)
        found = await self.store.get_many([source_check_id(owner, canonical) for owner in owners])
        checks = {
            str(owner): checked.model_dump(mode="json")
            for owner in owners
            if isinstance((checked := found.get(source_check_id(owner, canonical))), SourceCheck)
        }
        preferred = checks.get(str(caller.owner)) or checks.get(str(GLOBAL_OWNER))
        return {
            "uri": canonical,
            "check": preferred,
            "checks": checks,
            "memories": [
                {"memory": str(d.memory), "owner": str(d.owner), "kind": d.kind, "text": d.text}
                for d in docs
            ],
        }

    async def reported(self, caller: Caller, limit: int = 100) -> list[dict[str, Any]]:
        """Sources last reported `changed` or `missing` that a visible memory still cites."""
        out: list[dict[str, Any]] = []
        for state in ("changed", "missing"):
            for row in await self.store.select(
                "source_check",
                {"state": state},
                where_in={"owner": caller.visible_owners()},
                limit=limit,
            ):
                if not isinstance(row, SourceCheck):
                    continue
                docs = await self.store.docs([row.owner], sources=[row.uri], limit=1000)
                if docs:
                    out.append(
                        {
                            "uri": row.uri,
                            "check": row.model_dump(mode="json"),
                            "checks": {str(row.owner): row.model_dump(mode="json")},
                            "memories": [
                                {
                                    "memory": str(d.memory),
                                    "owner": str(d.owner),
                                    "kind": d.kind,
                                    "text": d.text,
                                }
                                for d in docs
                            ],
                        }
                    )
        return out[:limit]

    # -- vetting ----------------------------------------------------------------------------

    @staticmethod
    def _human(caller: Caller) -> None:
        if caller.actor.kind != "human":
            raise PermissionDeniedError("vetting needs a human credential")

    async def unvetted(self, caller: Caller, limit: int = 100) -> list[dict[str, Any]]:
        """Live memories committed by policy that no human has vetted yet."""
        self._human(caller)
        states = await self.store.select(
            "rev_state",
            {"vetting": "auto", "state": "committed", "lifecycle": "active"},
            limit=limit,
        )
        revs = await self.store.get_many(
            [RecordId(s.rev.table, s.rev.id) for s in states if isinstance(s, RevState)]
        )
        out: list[dict[str, Any]] = []
        visible = set(caller.visible_owners())
        for rid, rev in revs.items():
            if not isinstance(rev, RevisionBase):
                continue
            memory = await self.store.get(rev.memory)
            if isinstance(memory, Memory) and memory.owner in visible and memory.current == rid:
                out.append(
                    {
                        "memory": str(rev.memory),
                        "kind": memory.kind,
                        "text": rev.text,
                        "proposed_by": str(rev.proposed_by),
                        "sources": [s.model_dump(mode="json") for s in rev.sources],
                    }
                )
        return out

    async def vet(self, caller: Caller, memory: RecordId) -> dict[str, Any]:
        """A human confirms the memory's current revision: it is `vetted` from now on."""
        self._human(caller)
        found = await self.store.get(memory)
        if not isinstance(found, Memory) or found.owner not in caller.visible_owners():
            raise NotFoundError(f"memory {memory} not found")
        if not caller.can_write(found.owner):
            raise PermissionDeniedError("you cannot vet someone else's memory")
        if found.current is None:
            raise InvalidRequestError("the memory has no current revision")
        state_id = rev_state_id(found.current)
        state = await self.store.get(state_id)
        if not isinstance(state, RevState):
            raise NotFoundError(f"memory {memory} has no status record")
        if state.vetting != "vetted":
            await self.store.apply([Update(state_id, {"vetting": "vetted"})])
        return {"memory": str(memory), "revision": str(found.current), "vetting": "vetted"}
