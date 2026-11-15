"""The memory service: propose → validate → commit, review, and reading memories."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import Any

from ontyra.models import RecordId, new_id
from ontyra.models.core import Actor, Episode, Memory, Proposal, RevState, Task
from ontyra.models.kinds import RevisionBase
from ontyra.models.proposals import Finding, ProposalRequest, ProposalResult
from ontyra.ports import Create, Store, StoreConflictError, Update, Write
from ontyra.services import errors
from ontyra.services.anchors import AnchorService
from ontyra.services.context import Caller
from ontyra.services.ontology import OntologyService
from ontyra.services.planner import memory_writes, proposal_write
from ontyra.services.policy import OpSummary, PolicyEngine
from ontyra.services.prepared import (
    Prepared,
    PreparedAdd,
    PreparedLink,
    PreparedRetract,
    PreparedRevise,
    PreparedSupersede,
)
from ontyra.services.validation import ProposalValidator, Validation, rev_state_id
from ontyra.services.views import History, MemoryView, RevisionView, SupersessionReason


def _summaries(prepared: Sequence[Prepared]) -> list[OpSummary]:
    out: list[OpSummary] = []
    for p in prepared:
        if isinstance(p, PreparedAdd):
            out.append(OpSummary("add", p.kind))
        elif isinstance(p, PreparedRevise):
            out.append(OpSummary("revise", p.memory.kind))
        elif isinstance(p, PreparedSupersede):
            out.append(OpSummary("supersede", p.target.kind))
        elif isinstance(p, PreparedRetract):
            out.append(OpSummary("retract", p.memory.kind))
        elif isinstance(p, PreparedLink):
            out.append(OpSummary("link"))
        else:
            out.append(OpSummary("annotate"))
    return out


class MemoryService:
    def __init__(
        self,
        store: Store,
        policy: PolicyEngine,
        now: Callable[[], datetime],
        ontology: OntologyService,
        anchors: AnchorService,
        *,
        on_commit: Callable[[list[RecordId]], Awaitable[object]] | None = None,
        max_attempts: int = 5,
    ) -> None:
        self.store = store
        self.policy = policy
        self.now = now
        self.max_attempts = max_attempts
        self.on_commit = on_commit  # e.g. embed the new revisions (best effort)
        self.validator = ProposalValidator(store, now, ontology, anchors)

    # ---- episodes -----------------------------------------------------------

    async def record_episode(
        self,
        caller: Caller,
        *,
        session: str,
        role: str,
        content: str,
        meta: dict[str, Any] | None = None,
    ) -> RecordId:
        """Log an interaction event (lossless, immutable, no model involved)."""
        rid = RecordId("episode", new_id())
        await self.store.apply(
            [
                Create.of(
                    rid,
                    Episode(
                        occurred_at=self.now(),
                        actor=caller.actor_id,
                        owner=caller.owner,
                        session=session,
                        role=role,
                        content=content,
                        meta=meta or {},
                    ),
                )
            ]
        )
        return rid

    # ---- proposals ----------------------------------------------------------

    async def submit(self, caller: Caller, request: ProposalRequest) -> ProposalResult:
        """Validate and decide. Commits atomically when policy allows; otherwise the proposal
        waits for review (or is recorded as rejected).

        If the data changes between validation and commit (another writer got there first)
        the proposal is re-validated against the new state and retried.
        """
        key = request.idempotency_key
        if key:
            prior = await self.store.select(
                "proposal", {"actor": caller.actor_id, "idempotency_key": key}, limit=1
            )
            if prior and isinstance(prior[0], Proposal):
                return self._result(prior[0], duplicate=True)
        for _ in range(self.max_attempts):
            try:
                return await self._submit_once(caller, request)
            except StoreConflictError:
                continue
        raise errors.ConflictError(
            "memories kept changing while this proposal was being applied; re-submit"
        )

    async def _submit_once(self, caller: Caller, request: ProposalRequest) -> ProposalResult:
        key = request.idempotency_key

        v = await self.validator.validate(caller, request)
        decision, reason = self.policy.decide(caller, _summaries(v.prepared), v.findings)
        pid = RecordId("proposal", new_id())
        now = self.now()
        # stored JSON-safe (ids as "table:id", datetimes as ISO text); parsed back on replay
        ops = [op.model_dump(mode="json") for op in request.ops]
        created = {k: mem for k, (mem, _rev) in v.refs.items()}

        def record(status: str, result: dict[str, Any]) -> Write:
            return proposal_write(
                proposal=pid,
                actor=caller.actor_id,
                owner=caller.owner,
                status=status,
                decision=decision,
                reason=reason,
                ops=ops,
                findings=v.findings,
                now=now,
                key=key,
                note=request.note,
                result=result,
            )

        if decision == "reject":
            await self.store.apply([record("rejected", {})])
            status = "rejected"
        elif decision == "review":
            await self.store.apply(
                [
                    record("pending", {}),
                    Create.of(
                        RecordId("task", new_id()),
                        Task(
                            kind="review_proposal",
                            target=pid,
                            payload={"actor": caller.actor.name, "ops": len(ops)},
                            created_by=caller.actor_id,
                            created_at=now,
                        ),
                    ),
                ]
            )
            status = "pending"
        else:
            result = {"created": {k: str(m) for k, m in created.items()}}
            writes = [*memory_writes(v.prepared, caller.actor_id, now), record("committed", result)]
            await self.store.apply(writes)
            await self._committed(v.prepared)
            status = "committed"
        return ProposalResult(
            proposal=pid,
            status=status,  # type: ignore[arg-type]
            decision=decision,
            reason=reason,
            findings=v.findings,
            created=created if status == "committed" else {},
        )

    async def approve(self, reviewer: Caller, proposal: RecordId) -> ProposalResult:
        """Commit a pending proposal (re-validated against the data as it is now)."""
        p = await self._pending(proposal)
        submitter = await self._caller_of(p)
        v = await self._revalidate(submitter, p)
        if not v.ok:
            raise errors.InvalidProposalError(v.findings)
        now = self.now()
        created = {k: mem for k, (mem, _rev) in v.refs.items()}
        writes = [
            *memory_writes(v.prepared, submitter.actor_id, now),
            Update(
                proposal,
                {
                    "status": "committed",
                    "decided_at": now,
                    "decided_by": reviewer.actor_id,
                    "findings": [f.model_dump(mode="json") for f in v.findings],
                    "result": {"created": {k: str(m) for k, m in created.items()}},
                },
            ),
            *(await self._close_task(proposal, "approved", now)),
        ]
        await self._apply(writes)
        await self._committed(v.prepared)
        return ProposalResult(
            proposal=proposal,
            status="committed",
            decision="review",
            reason=f"approved by {reviewer.actor.name}",
            findings=v.findings,
            created=created,
        )

    async def reject(self, reviewer: Caller, proposal: RecordId, reason: str) -> ProposalResult:
        await self._pending(proposal)
        now = self.now()
        await self.store.apply(
            [
                Update(
                    proposal,
                    {
                        "status": "rejected",
                        "decided_at": now,
                        "decided_by": reviewer.actor_id,
                        "decision_reason": reason,
                    },
                ),
                *await self._close_task(proposal, "rejected", now),
            ]
        )
        return ProposalResult(
            proposal=proposal, status="rejected", decision="review", reason=reason
        )

    async def pending(self, limit: int = 50) -> list[Proposal]:
        rows = await self.store.select(
            "proposal", {"status": "pending"}, limit=limit, order_by="submitted_at"
        )
        return [r for r in rows if isinstance(r, Proposal)]

    async def get_proposal(self, proposal: RecordId) -> Proposal:
        p = await self.store.get(proposal)
        if not isinstance(p, Proposal):
            raise errors.NotFoundError(f"{proposal} does not exist")
        return p

    # ---- reading ------------------------------------------------------------

    async def get_memory(self, caller: Caller, memory: RecordId) -> MemoryView:
        mem = await self.store.get(memory)
        if not isinstance(mem, Memory) or mem.owner not in caller.visible_owners():
            raise errors.NotFoundError(f"{memory} does not exist")
        return await self._view(mem)

    async def list_memories(
        self,
        caller: Caller,
        *,
        kind: str | None = None,
        status: str = "active",
        limit: int = 50,
    ) -> list[MemoryView]:
        where: dict[str, Any] = {"status": status}
        if kind:
            where["kind"] = kind
        rows = await self.store.select("memory", where, limit=limit, order_by="created_at")
        owners = caller.visible_owners()
        return [await self._view(m) for m in rows if isinstance(m, Memory) and m.owner in owners]

    async def history(self, caller: Caller, memory: RecordId) -> History:
        mem = await self.store.get(memory)
        if not isinstance(mem, Memory) or mem.owner not in caller.visible_owners():
            raise errors.NotFoundError(f"{memory} does not exist")
        views: list[RevisionView] = []
        for rev in await self.store.revisions(memory):
            if not isinstance(rev, RevisionBase) or rev.id is None:
                continue
            state = await self.store.get(rev_state_id(rev.id))
            edges = await self.store.edges("supersedes", target=rev.id)
            views.append(
                RevisionView(
                    revision=rev.id,
                    seq=rev.seq,
                    text=rev.text,
                    state=state.state if isinstance(state, RevState) else "unknown",
                    lifecycle=state.lifecycle if isinstance(state, RevState) else "unknown",
                    recorded_at=rev.recorded_at,
                    valid_from=state.valid_from if isinstance(state, RevState) else None,
                    valid_to=state.valid_to if isinstance(state, RevState) else None,
                    superseded_by=[e["in"] for e in edges],
                    reasons=[
                        SupersessionReason(
                            reason_code=e["reason_code"],
                            reason=e.get("reason"),
                            by=e.get("by"),
                            at=e.get("at"),
                        )
                        for e in edges
                    ],
                )
            )
        return History(memory=memory, kind=mem.kind, status=mem.status, revisions=views)

    # ---- internals ----------------------------------------------------------

    @staticmethod
    def _result(p: Proposal, *, duplicate: bool) -> ProposalResult:
        """Rebuild a result from a stored proposal (idempotent resubmission)."""
        assert p.id is not None
        created = {k: RecordId.parse(v) for k, v in p.result.get("created", {}).items()}
        return ProposalResult(
            proposal=p.id,
            status=p.status,
            decision=p.decision,
            reason=p.decision_reason,
            findings=[Finding.model_validate(f) for f in p.findings],
            created=created,
            duplicate_of_idempotency_key=duplicate,
        )

    async def _committed(self, prepared: Sequence[Prepared]) -> None:
        if self.on_commit is None:
            return
        revs = [p.rev for p in prepared if isinstance(p, PreparedAdd)]
        revs += [p.new_rev for p in prepared if isinstance(p, PreparedRevise)]
        if revs:
            await self.on_commit(revs)

    async def _apply(self, writes: Sequence[Write]) -> None:
        try:
            await self.store.apply(writes)
        except StoreConflictError as exc:
            raise errors.ConflictError(
                "a memory changed while this proposal was being applied; re-submit"
            ) from exc

    async def _view(self, mem: Memory) -> MemoryView:
        assert mem.id is not None and mem.current is not None
        rev = await self.store.get(mem.current)
        state = await self.store.get(rev_state_id(mem.current))
        if not isinstance(rev, RevisionBase) or not isinstance(state, RevState):
            raise errors.NotFoundError(f"{mem.id} is missing its revision or state")
        bookkeeping = {"id", "memory", "seq", "text", "recorded_at", "proposed_by", "confidence"}
        return MemoryView(
            memory=mem.id,
            kind=mem.kind,
            owner=mem.owner,
            status=mem.status,
            revision=mem.current,
            seq=rev.seq,
            text=rev.text,
            content=rev.model_dump(mode="json", exclude=bookkeeping),
            state=state.state,
            lifecycle=state.lifecycle,
            valid_from=state.valid_from,
            valid_to=state.valid_to,
            stale=state.stale,
            confidence=rev.confidence,
        )

    async def _pending(self, proposal: RecordId) -> Proposal:
        p = await self.get_proposal(proposal)
        if p.status != "pending":
            raise errors.ProposalStateError(f"{proposal} is {p.status}, not pending")
        return p

    async def _caller_of(self, p: Proposal) -> Caller:
        actor = await self.store.get(p.actor)
        if not isinstance(actor, Actor):
            raise errors.NotFoundError(f"submitting actor {p.actor} no longer exists")
        return Caller(actor=actor, owner=p.owner)

    async def _revalidate(self, caller: Caller, p: Proposal) -> Validation:
        request = ProposalRequest.model_validate({"ops": p.ops, "note": p.note})
        return await self.validator.validate(caller, request)

    async def _close_task(self, proposal: RecordId, outcome: str, now: datetime) -> list[Write]:
        tasks = await self.store.select(
            "task", {"target": proposal, "kind": "review_proposal", "status": "pending"}, limit=5
        )
        return [
            Update(t.id, {"status": "done", "updated_at": now, "result": {"outcome": outcome}})
            for t in tasks
            if t.id is not None
        ]
