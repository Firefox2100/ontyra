"""The memory service: propose → validate → commit, review, and reading memories."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta
from typing import Any, cast

from ontyra.models import RecordId, new_id
from ontyra.models.core import Actor, Episode, Memory, Proposal, RevState, Task
from ontyra.models.episodes import EpisodeSettings
from ontyra.models.kinds import RevisionBase
from ontyra.models.proposals import (
    AmendRequest,
    ApproveRequest,
    Evidence,
    Finding,
    OverlayRequest,
    ProposalRequest,
    ProposalResult,
    RequestChanges,
    WithdrawRequest,
)
from ontyra.ports import Create, Delete, Store, StoreConflictError, Update, Write
from ontyra.services import errors, review
from ontyra.services.anchors import AnchorService
from ontyra.services.context import GLOBAL_OWNER, Caller
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


def _sourced(evidence: Sequence[Evidence]) -> bool:
    """Does the op cite at least one external source with a literal quote?"""
    return any(ev.source is not None and bool(ev.source.quote) for ev in evidence)


def _summaries(prepared: Sequence[Prepared]) -> list[OpSummary]:
    out: list[OpSummary] = []
    for p in prepared:
        if isinstance(p, PreparedAdd):
            out.append(OpSummary("add", p.kind, _sourced(p.evidence)))
        elif isinstance(p, PreparedRevise):
            out.append(OpSummary("revise", p.memory.kind, _sourced(p.evidence)))
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
        episodes: EpisodeSettings | None = None,
    ) -> None:
        self.store = store
        self.policy = policy
        self.now = now
        self.max_attempts = max_attempts
        self.on_commit = on_commit  # e.g. embed the new revisions (best effort)
        self.episodes = episodes or EpisodeSettings()
        self._last_prune: datetime | None = None
        self.ontology = ontology
        self.validator = ProposalValidator(store, now, ontology, anchors, self.episodes)

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
        await self._prune_episodes()
        return rid

    async def _prune_episodes(self) -> int:
        """Delete episodes older than the retention that no memory cites. Runs at most once per
        `prune_interval_minutes`, after an episode is written; a failure costs nothing."""
        cfg = self.episodes
        now = self.now()
        if cfg.retention_days <= 0:
            return 0
        if self._last_prune and now - self._last_prune < timedelta(
            minutes=cfg.prune_interval_minutes
        ):
            return 0
        self._last_prune = now
        try:
            stale = await self.store.uncited_episodes(
                now - timedelta(days=cfg.retention_days), limit=1000
            )
            if stale:
                keep = await self._episodes_of_waiting_proposals()
                stale = [rid for rid in stale if rid not in keep]
            if stale:
                await self.store.apply([Delete(rid) for rid in stale])
        except Exception:
            logging.getLogger(__name__).warning("episode retention sweep failed", exc_info=True)
            return 0
        return len(stale)

    async def _episodes_of_waiting_proposals(self) -> set[RecordId]:
        """Episodes the stored ops of waiting proposals cite as evidence: their `derived_from`
        edge exists only after a commit, and the review may come after the retention."""
        waiting = await self.store.select(
            "proposal", where_in={"status": ["pending", "changes_requested"]}, limit=1000
        )
        found: set[RecordId] = set()
        for p in waiting:
            if not isinstance(p, Proposal):
                continue
            for op in p.ops:
                cited: list[Any] = op.get("evidence") or []
                for ev in cited:
                    entry = cast("dict[str, Any]", ev) if isinstance(ev, dict) else {}
                    if entry.get("episode"):
                        try:
                            found.add(RecordId.parse(str(entry["episode"])))
                        except ValueError:
                            continue
        return found

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
                space=request.space,
                permissions=sorted(caller.permissions),
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
                            owner=caller.owner,
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
            # a human's own writes are vetted; what policy commits for anyone else is not (yet)
            vetting = "vetted" if caller.actor.kind == "human" else "auto"
            writes = [
                *memory_writes(v.prepared, caller.actor_id, now, vetting),
                record("committed", result),
            ]
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

    # ---- review: approve, reject, amend, withdraw, request changes (plans/24) -------

    @staticmethod
    def _version(p: Proposal) -> int:
        return p.version or 1

    @classmethod
    def _check_version(cls, p: Proposal, expected: int | None) -> None:
        if expected is not None and expected != cls._version(p):
            raise errors.ConflictError(
                f"{p.id} is at version {cls._version(p)}, not {expected}: it changed, read it again"
            )

    def _stamp(self, who: Caller) -> dict[str, Any]:
        return {"by": str(who.actor_id), "at": self.now().isoformat()}

    def _decisions(
        self, p: Proposal, numbers: Sequence[int], status: str, reviewer: Caller, reason: str | None
    ) -> list[dict[str, Any]]:
        out = [*(p.decisions or [])]
        for n in numbers:
            out.append(
                {
                    "version": self._version(p),
                    "op": n,
                    "summary": review.summary(p.ops[n]),
                    "status": status,
                    "reason": reason,
                    **self._stamp(reviewer),
                }
            )
        return out

    async def approve(
        self, reviewer: Caller, proposal: RecordId, request: ApproveRequest | None = None
    ) -> ProposalResult:
        """Commit a waiting proposal (re-validated against the data as it is now): all its ops,
        or the chosen ones plus what they need, in which case the rest are rejected."""
        request = request or ApproveRequest()
        p = await self._pending(proposal)
        self._may_review(reviewer, p)
        self._check_version(p, request.expect_version)
        everything = list(range(len(p.ops)))
        chosen = (
            everything
            if request.ops is None
            else sorted(review.needs(p.ops, review.check_numbers(p.ops, request.ops)))
        )
        left_out = [n for n in everything if n not in chosen]
        if not chosen:
            raise errors.InvalidRequestError("approve at least one op, or reject the proposal")
        if left_out and not (request.reason and request.reason.strip()):
            raise errors.InvalidRequestError("a partial approval needs a `reason` for the others")
        submitter = await self._caller_of(p)
        v = await self._revalidate(submitter, p, [p.ops[n] for n in chosen])
        if not v.ok:
            raise errors.InvalidProposalError(v.findings)
        now = self.now()
        created = {k: mem for k, (mem, _rev) in v.refs.items()}
        decisions = [
            *self._decisions(p, chosen, "approved", reviewer, None),
        ]
        p_after = p.model_copy(update={"decisions": decisions})
        decisions = self._decisions(p_after, left_out, "rejected", reviewer, request.reason)
        writes = [
            *memory_writes(v.prepared, submitter.actor_id, now),
            Update(
                proposal,
                {
                    "status": "committed",
                    "decided_at": now,
                    "decided_by": reviewer.actor_id,
                    "findings": [f.model_dump(mode="json") for f in v.findings],
                    "result": {
                        "created": {k: str(m) for k, m in created.items()},
                        "rejected_ops": left_out,
                    },
                    "decisions": decisions,
                },
            ),
            *(await self._close_task(proposal, "approved", now)),
        ]
        await self._apply(writes)
        await self._committed(v.prepared)
        included = sorted(set(chosen) - set(request.ops or chosen))
        note = f"approved by {reviewer.actor.name}"
        if included:
            note += f" (also op(s) {included}, which the chosen ones need)"
        return ProposalResult(
            proposal=proposal,
            status="committed",
            decision="review",
            reason=note,
            findings=v.findings,
            created=created,
            version=self._version(p),
            rejected_ops=left_out,
        )

    async def reject(
        self,
        reviewer: Caller,
        proposal: RecordId,
        reason: str,
        ops: list[int] | None = None,
        expect_version: int | None = None,
    ) -> ProposalResult:
        """Reject the whole waiting proposal, or only the chosen ops (and what needs them): the
        rest stays waiting as a new version."""
        p = await self._pending(proposal)
        self._may_review(reviewer, p)
        self._check_version(p, expect_version)
        everything = list(range(len(p.ops)))
        gone = (
            everything
            if ops is None
            else sorted(review.needed_by(p.ops, review.check_numbers(p.ops, ops)))
        )
        now = self.now()
        if len(gone) == len(everything):
            await self.store.apply(
                [
                    Update(
                        proposal,
                        {
                            "status": "rejected",
                            "decided_at": now,
                            "decided_by": reviewer.actor_id,
                            "decision_reason": reason,
                            "decisions": self._decisions(p, gone, "rejected", reviewer, reason),
                        },
                    ),
                    *await self._close_task(proposal, "rejected", now),
                ]
            )
            return ProposalResult(
                proposal=proposal,
                status="rejected",
                decision="review",
                reason=reason,
                version=self._version(p),
                rejected_ops=gone,
            )
        keep = [p.ops[n] for n in everything if n not in gone]
        submitter = await self._caller_of(p)
        v = await self._revalidate(submitter, p, keep)
        if not v.ok:
            raise errors.InvalidProposalError(v.findings)
        version = self._version(p)
        entry = {
            "version": version,
            "ops": p.ops,
            "why": f"ops {gone} rejected: {reason}",
            **self._stamp(reviewer),
        }
        await self.store.apply(
            [
                Update(
                    proposal,
                    {
                        "ops": keep,
                        "version": version + 1,
                        "history": [*(p.history or []), entry],
                        "status": "pending",
                        "findings": [f.model_dump(mode="json") for f in v.findings],
                        "decisions": self._decisions(p, gone, "rejected", reviewer, reason),
                    },
                )
            ]
        )
        return ProposalResult(
            proposal=proposal,
            status="pending",
            decision="review",
            reason=f"ops {gone} rejected; the others are still waiting",
            findings=v.findings,
            version=version + 1,
            rejected_ops=gone,
        )

    async def overlay(self, caller: Caller, request: OverlayRequest) -> list[ProposalResult]:
        """Amend each chosen proposal (a new version) with the same fields on its `add` and
        `revise` ops."""
        patch: dict[str, Any] = {}
        if request.scope is not None:
            patch["scope"] = request.scope
        if request.expires_at:
            patch["expires_at"] = request.expires_at
        results: list[ProposalResult] = []
        for pid in request.proposals:
            p = await self.get_proposal(caller, pid)
            ops: list[dict[str, Any]] = []
            for op in p.ops:
                if op.get("op") in ("add", "revise"):
                    fields: dict[str, Any] = {**op.get("fields", {}), **patch}
                    if request.anchor:
                        fields["anchors"] = [
                            *fields.get("anchors", []),
                            {"uri": request.anchor, "match": "subtree"},
                        ]
                    if request.about:  # labels, slugs or ids; an unknown one is refused
                        concepts = [str(await self.ontology.resolve(t)) for t in request.about]
                        fields["about"] = [*fields.get("about", []), *concepts]
                    op = {**op, "fields": fields}
                ops.append(op)
            results.append(
                await self.amend(
                    caller,
                    pid,
                    AmendRequest(
                        ops=ProposalRequest.model_validate({"ops": ops}).ops,
                        why="bulk edit",
                        expect_version=p.version or 1,
                    ),
                )
            )
        return results

    async def amend(
        self, caller: Caller, proposal: RecordId, request: AmendRequest
    ) -> ProposalResult:
        """A new version of a waiting proposal, validated again (its submitter or a reviewer)."""
        p = await self._pending(proposal)
        if p.actor != caller.actor_id and not self._can_review(caller, p):
            raise errors.PermissionDeniedError("only its submitter or a reviewer may amend this")
        if not request.why.strip():
            raise errors.InvalidRequestError("an amendment needs a `why`")
        self._check_version(p, request.expect_version)
        submitter = await self._caller_of(p)
        v = await self.validator.validate(
            submitter,
            ProposalRequest(ops=request.ops, space=p.space, note=p.note),
            exclude=proposal,
        )
        if not v.ok:
            raise errors.InvalidProposalError(v.findings)
        decision, reason = self.policy.decide(submitter, _summaries(v.prepared), v.findings)
        if decision == "reject":
            raise errors.InvalidRequestError(f"the policy refuses this amendment: {reason}")
        version = self._version(p)
        entry = {"version": version, "ops": p.ops, "why": request.why, **self._stamp(caller)}
        await self.store.apply(
            [
                Update(
                    proposal,
                    {
                        "ops": [op.model_dump(mode="json") for op in request.ops],
                        "version": version + 1,
                        "history": [*(p.history or []), entry],
                        "status": "pending",
                        "findings": [f.model_dump(mode="json") for f in v.findings],
                    },
                )
            ]
        )
        return ProposalResult(
            proposal=proposal,
            status="pending",
            decision="review",
            reason=f"version {version + 1}, amended by {caller.actor.name}",
            findings=v.findings,
            version=version + 1,
        )

    async def withdraw(
        self, caller: Caller, proposal: RecordId, request: WithdrawRequest
    ) -> ProposalResult:
        """Take back a waiting proposal (its submitter or a reviewer): final, nothing commits."""
        p = await self._pending(proposal)
        if p.actor != caller.actor_id and not self._can_review(caller, p):
            raise errors.PermissionDeniedError("only its submitter or a reviewer may withdraw this")
        now = self.now()
        await self.store.apply(
            [
                Update(
                    proposal,
                    {
                        "status": "withdrawn",
                        "decided_at": now,
                        "decided_by": caller.actor_id,
                        "decision_reason": request.reason,
                    },
                ),
                *await self._close_task(proposal, "withdrawn", now),
            ]
        )
        return ProposalResult(
            proposal=proposal,
            status="withdrawn",
            decision="review",
            reason=request.reason,
            version=self._version(p),
        )

    async def request_changes(
        self, reviewer: Caller, proposal: RecordId, request: RequestChanges
    ) -> ProposalResult:
        """Send a waiting proposal back with comments; it leaves the approve queue until its
        submitter amends it."""
        p = await self._pending(proposal)
        self._may_review(reviewer, p)
        self._check_version(p, request.expect_version)
        if not request.comment.strip():
            raise errors.InvalidRequestError("say what should change")
        review.check_numbers(p.ops, request.op_comments)
        version, stamp = self._version(p), self._stamp(reviewer)
        added = [
            {"version": version, "text": request.comment, **stamp},
            *(
                {"version": version, "op": n, "text": text, **stamp}
                for n, text in sorted(request.op_comments.items())
            ),
        ]
        await self.store.apply(
            [
                Update(
                    proposal,
                    {"status": "changes_requested", "comments": [*(p.comments or []), *added]},
                )
            ]
        )
        return ProposalResult(
            proposal=proposal,
            status="changes_requested",
            decision="review",
            reason=request.comment,
            version=version,
        )

    @classmethod
    def _can_review(cls, who: Caller, p: Proposal) -> bool:
        return who.actor.kind == "human" and who.can_write(cls._target(p))

    @staticmethod
    def _target(p: Proposal) -> RecordId:
        return GLOBAL_OWNER if p.space == "global" else p.owner

    @classmethod
    def _may_review(cls, reviewer: Caller, p: Proposal) -> None:
        if reviewer.actor.kind != "human":
            raise errors.PermissionDeniedError("only a human credential can review proposals")
        if not reviewer.can_write(cls._target(p)):
            raise errors.PermissionDeniedError("this proposal is not yours to review")

    @classmethod
    def _sees(cls, caller: Caller, p: Proposal) -> bool:
        return p.owner == caller.owner or caller.can_write(cls._target(p))

    async def pending(self, caller: Caller, limit: int = 50) -> list[Proposal]:
        """Pending proposals the caller may see: its own owner's, plus the global ones when it
        may write the global memory."""
        if caller.can_write(GLOBAL_OWNER):
            rows = await self.store.select(
                "proposal",
                where_in={"status": ["pending", "changes_requested"]},
                where_any={"owner": caller.owner, "space": "global"},
                limit=limit,
                order_by="submitted_at",
            )
        else:
            rows = await self.store.select(
                "proposal",
                {"owner": caller.owner},
                where_in={"status": ["pending", "changes_requested"]},
                limit=limit,
                order_by="submitted_at",
            )
        return [r for r in rows if isinstance(r, Proposal)]

    async def get_proposal(self, caller: Caller, proposal: RecordId) -> Proposal:
        p = await self._proposal(proposal)
        if not self._sees(caller, p):
            raise errors.NotFoundError(f"{proposal} does not exist")
        return p

    async def _proposal(self, proposal: RecordId) -> Proposal:
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
        owners = caller.visible_owners()
        rows = await self.store.select(
            "memory", where, where_in={"owner": owners}, limit=limit, order_by="created_at"
        )
        return [await self._view(m) for m in rows if isinstance(m, Memory)]

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
        p = await self._proposal(proposal)
        if p.status not in ("pending", "changes_requested"):
            raise errors.ProposalStateError(f"{proposal} is {p.status}: it is no longer waiting")
        return p

    async def _caller_of(self, p: Proposal) -> Caller:
        actor = await self.store.get(p.actor)
        if not isinstance(actor, Actor):
            raise errors.NotFoundError(f"submitting actor {p.actor} no longer exists")
        return Caller(actor=actor, owner=p.owner, permissions=frozenset(p.permissions))

    async def _revalidate(
        self, caller: Caller, p: Proposal, ops: list[dict[str, Any]] | None = None
    ) -> Validation:
        request = ProposalRequest.model_validate(
            {"ops": p.ops if ops is None else ops, "note": p.note, "space": p.space}
        )
        return await self.validator.validate(caller, request, exclude=p.id)

    async def _close_task(self, proposal: RecordId, outcome: str, now: datetime) -> list[Write]:
        tasks = await self.store.select(
            "task", {"target": proposal, "kind": "review_proposal", "status": "pending"}, limit=5
        )
        return [
            Update(t.id, {"status": "done", "updated_at": now, "result": {"outcome": outcome}})
            for t in tasks
            if t.id is not None
        ]
