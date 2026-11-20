"""Ontology change proposals: validate, decide (apply now or hold for review), apply, record.

Applying goes through `OntologyService`, so every rule it enforces (cycles, duplicates) still
holds. Deterministic: similar-concept advice is lexical (labels and aliases); no model is called.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime

from ontyra.models import RecordId, new_id
from ontyra.models.core import Task
from ontyra.models.ontology import try_slug
from ontyra.models.ontology_change import (
    AliasChange,
    AmendChange,
    Change,
    ChangePreview,
    ChangeRequest,
    ChangeResult,
    DefineChange,
    DeprecateChange,
    Effects,
    MergeChange,
    OntologyChange,
    RelateChange,
)
from ontyra.models.proposals import RequestChanges, WithdrawRequest
from ontyra.ports import Create, Store, Update, Write
from ontyra.services import errors
from ontyra.services.context import GLOBAL_OWNER, Caller
from ontyra.services.ontology import OntologyService
from ontyra.services.ontology_plans import OntologyPlanner, Plan

SIMILAR = 0.5


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.casefold()))


def _similar(a: str, b: str) -> bool:
    x, y = _tokens(a), _tokens(b)
    return bool(x and y) and len(x & y) / len(x | y) >= SIMILAR


class OntologyChangeService:
    def __init__(
        self,
        store: Store,
        ontology: OntologyService,
        now: Callable[[], datetime],
        auto_apply: list[str] | None = None,
    ) -> None:
        self.store = store
        self.ontology = ontology
        self.now = now
        self.auto_apply = set(auto_apply or [])
        self.planner = OntologyPlanner(ontology, now)

    # ---- validation -------------------------------------------------------------------

    async def _taken(self, term: str, onto_ids: dict[str, RecordId]) -> RecordId | None:
        return onto_ids.get(term.casefold().strip())

    async def validate(
        self, change: Change, caller: Caller, *, exclude: RecordId | None = None
    ) -> list[str]:
        """Raise `InvalidRequestError`/`NotFoundError` for a bad change; return advice."""
        onto = await self.ontology.ontology()
        names: dict[str, RecordId] = {}
        for c in onto.concepts.values():
            assert c.id is not None
            for name in [c.label, *c.aliases]:
                names.setdefault(name.casefold().strip(), c.id)
        advice: list[str] = []

        async def known(term: str) -> RecordId:
            return await self.ontology.resolve(term)

        match change:
            case DefineChange(concept=d):
                label = d.label.strip()
                if not label:
                    raise errors.InvalidRequestError("a concept needs a label")
                for name in [label, *d.aliases]:
                    hit = names.get(name.casefold().strip())
                    if hit is not None:
                        raise errors.InvalidRequestError(
                            f"{name!r} already names {hit}: use it, or add an alias/merge"
                        )
                    if (
                        try_slug(name)
                        and RecordId("concept", try_slug(name) or "") in onto.concepts
                    ):
                        raise errors.InvalidRequestError(f"{name!r} is already a concept")
                for parent in d.is_a:
                    await known(parent)
                advice += [
                    f"similar to {cid} ({names_label})"
                    for names_label, cid in names.items()
                    if _similar(label, names_label)
                ][:5]
            case RelateChange(relation=r):
                await known(r.source)
                await known(r.target)
            case AliasChange(concept=term, alias=alias):
                target = await known(term)
                hit = names.get(alias.casefold().strip())
                if hit is not None and hit != target:
                    raise errors.InvalidRequestError(f"{alias!r} already names {hit}")
            case DeprecateChange(concept=term, replaced_by=other):
                await known(term)
                if other:
                    await known(other)
            case MergeChange(source=src, into=dst):
                a, b = await known(src), await known(dst)
                if a == b:
                    raise errors.InvalidRequestError("cannot merge a concept into itself")
                if onto.concepts[a].status == "deprecated":
                    raise errors.InvalidRequestError(f"{a} is already deprecated")
            case _:  # the other ops are checked by planning them
                pass
        advice += await self._waiting_advice(change, exclude)
        advice += (await self.planner.plan(change, caller, None)).advice
        return advice

    async def _waiting(self, exclude: RecordId | None) -> list[OntologyChange]:
        rows = await self.store.select(
            "ontology_change",
            where_in={"status": ["pending", "changes_requested"]},
            limit=200,
        )
        return [r for r in rows if isinstance(r, OntologyChange) and r.id != exclude]

    async def _waiting_advice(self, change: Change, exclude: RecordId | None) -> list[str]:
        """Other changes still waiting that say the same thing or touch the same concept: advice
        for the writer and the reviewer (never an error)."""
        waiting = await self._waiting(exclude)
        advice: list[str] = []
        for other in waiting[:50]:
            payload = other.change
            if isinstance(change, DefineChange) and payload.get("op") == "define":
                label = str(payload.get("concept", {}).get("label", ""))
                if _similar(change.concept.label, label):
                    advice.append(
                        f"pending_similar: {other.id} (waiting) defines {label!r}: amend it "
                        "instead of proposing another"
                    )
            else:
                mine = {str(change.model_dump().get(k)) for k in ("concept", "source", "into")}
                theirs = {str(payload.get(k)) for k in ("concept", "source", "into")}
                if (mine & theirs) - {"None"}:
                    advice.append(
                        f"pending_conflict: {other.id} (waiting) also changes the same concept"
                    )
        return advice[:5]

    # ---- decide -----------------------------------------------------------------------

    async def propose(self, caller: Caller, request: ChangeRequest) -> ChangeResult:
        if not request.rationale.strip():
            raise errors.InvalidRequestError("a change needs a rationale")
        advice = await self.validate(request.change, caller)
        now = self.now()
        cid = RecordId("ontology_change", new_id())
        record = OntologyChange(
            owner=caller.owner,
            change=request.change.model_dump(mode="json"),
            op=request.change.op,
            rationale=request.rationale,
            proposed_by=caller.actor_id,
            proposer_kind=caller.actor.kind,
            created_at=now,
            advice=advice,
        )
        if caller.can_review(GLOBAL_OWNER) or (
            caller.can_write(GLOBAL_OWNER) and request.change.op in self.auto_apply
        ):
            record.status = "applied"
            record.decided_by = caller.actor_id
            record.decided_at = now

            def finish(plan: Plan) -> list[Write]:
                record.touched, record.undo = plan.touched, plan.undo
                return [Create.of(cid, record)]

            await self._apply(caller, request.change, cid, finish)
        else:
            await self.store.apply(
                [
                    Create.of(cid, record),
                    Create.of(
                        RecordId("task", new_id()),
                        Task(
                            kind="review_ontology_change",
                            owner=GLOBAL_OWNER,
                            target=cid,
                            payload={"op": record.op, "by": caller.actor.name},
                            created_by=caller.actor_id,
                            created_at=now,
                        ),
                    ),
                ]
            )
        return ChangeResult(id=cid, status=record.status, op=record.op, advice=advice)

    async def _get(self, change: RecordId) -> OntologyChange:
        found = await self.store.get(change)
        if not isinstance(found, OntologyChange):
            raise errors.NotFoundError(f"ontology change {change} not found")
        return found

    async def _closing(self, change: RecordId) -> list[Write]:
        """Writes that close the review task of a change (done in the same batch as the decision,
        so a failure cannot leave an open task for a decided change)."""
        tasks = await self.store.select(
            "task", {"target": change, "kind": "review_ontology_change", "status": "pending"}
        )
        return [
            Update(t.id, {"status": "done", "updated_at": self.now()})
            for t in tasks
            if t.id is not None
        ]

    async def _unchanged(self, change: RecordId, seen: OntologyChange) -> None:
        """Called while holding the ontology lock: the change must still be as the caller read it
        (another reviewer or its proposer may have decided or amended it in between)."""
        now = await self._get(change)
        if now.status != seen.status or self._version(now) != self._version(seen):
            raise errors.ConflictError(
                f"{change} changed while this was being decided: read it again"
            )

    @staticmethod
    def _version(rec: OntologyChange) -> int:
        return rec.version or 1

    def _check_version(self, rec: OntologyChange, expected: int | None) -> None:
        if expected is not None and expected != self._version(rec):
            raise errors.ConflictError(
                f"{rec.id} is at version {self._version(rec)}, not {expected}: read it again"
            )

    def _waiting_or_raise(self, rec: OntologyChange) -> None:
        if rec.status not in ("pending", "changes_requested"):
            raise errors.ProposalStateError(f"change {rec.id} is already {rec.status}")

    async def approve(
        self, reviewer: Caller, change: RecordId, expect_version: int | None = None
    ) -> ChangeResult:
        if reviewer.actor.kind != "human":
            raise errors.InvalidRequestError("only a human can approve an ontology change")
        if not reviewer.can_write(GLOBAL_OWNER):
            raise errors.PermissionDeniedError("changing the ontology needs global write access")
        rec = await self._get(change)
        self._waiting_or_raise(rec)
        self._check_version(rec, expect_version)
        parsed = ChangeRequest.model_validate({"change": rec.change, "rationale": rec.rationale})
        # the ontology may have moved since the proposal
        await self.validate(parsed.change, reviewer, exclude=change)
        now = self.now()

        def finish(plan: Plan) -> list[Write]:
            return [
                Update(
                    change,
                    {
                        "status": "applied",
                        "decided_by": reviewer.actor_id,
                        "decided_at": now,
                        "touched": plan.touched,
                        "undo": plan.undo,
                    },
                ),
                *closing,
            ]

        closing = await self._closing(change)
        await self._apply(reviewer, parsed.change, change, finish, seen=rec)
        return ChangeResult(id=change, status="applied", op=rec.op, advice=rec.advice)

    async def reject(
        self,
        reviewer: Caller,
        change: RecordId,
        reason: str,
        expect_version: int | None = None,
    ) -> ChangeResult:
        if reviewer.actor.kind != "human":
            raise errors.InvalidRequestError("only a human can reject an ontology change")
        if not reviewer.can_write(GLOBAL_OWNER):
            raise errors.PermissionDeniedError("changing the ontology needs global write access")
        rec = await self._get(change)
        self._waiting_or_raise(rec)
        self._check_version(rec, expect_version)
        async with self.ontology.lock:
            await self._unchanged(change, rec)
            await self.store.apply(
                [
                    Update(
                        change,
                        {
                            "status": "rejected",
                            "decided_by": reviewer.actor_id,
                            "decided_at": self.now(),
                            "reason": reason,
                        },
                    ),
                    *await self._closing(change),
                ]
            )
        return ChangeResult(id=change, status="rejected", op=rec.op, reason=reason)

    async def pending(self, caller: Caller, limit: int = 50) -> list[OntologyChange]:
        where: dict[str, object] = {}
        if not caller.can_write(GLOBAL_OWNER):
            where["owner"] = caller.owner
        rows = await self.store.select(
            "ontology_change",
            where,
            where_in={"status": ["pending", "changes_requested"]},
            limit=limit,
            order_by="created_at",
        )
        return [r for r in rows if isinstance(r, OntologyChange)]

    async def history(
        self,
        caller: Caller,
        limit: int = 50,
        *,
        concept: RecordId | None = None,
        op: str | None = None,
    ) -> list[OntologyChange]:
        """Applied and decided changes, newest first (the investigation view); `concept`
        keeps those that touched it, `op` one kind."""
        where: dict[str, object] | None = (
            None if caller.can_write(GLOBAL_OWNER) else {"owner": caller.owner}
        )
        if op:
            where = {**(where or {}), "op": op}
        rows = await self.store.select(
            "ontology_change",
            where,
            limit=2000 if concept else limit,
            order_by="created_at",
            descending=True,
        )
        found = [r for r in rows if isinstance(r, OntologyChange)]
        if concept is not None:
            found = [r for r in found if concept in (r.touched or [])]
        return found[:limit]

    # ---- apply ------------------------------------------------------------------------

    async def _apply(
        self,
        caller: Caller,
        change: Change,
        change_id: RecordId,
        finish: Callable[[Plan], list[Write]],
        seen: OntologyChange | None = None,
    ) -> Plan:
        """Plan the change from the current ontology and commit it in one batch together with
        the change record (`finish` builds those writes from the plan), one writer at a time.
        `seen` is the held change a decision was based on: it is re-read under the lock."""
        async with self.ontology.lock:
            if seen is not None and seen.id is not None:
                await self._unchanged(seen.id, seen)
            plan = await self.planner.plan(change, caller, change_id)
            await self.store.apply([*plan.writes, *finish(plan)])
            self.ontology.invalidate()
        return plan

    def _can_amend(self, who: Caller, rec: OntologyChange) -> bool:
        return rec.proposed_by == who.actor_id or (
            who.actor.kind == "human" and who.can_write(GLOBAL_OWNER)
        )

    def _stamp(self, who: Caller) -> dict[str, str]:
        return {"by": str(who.actor_id), "at": self.now().isoformat()}

    async def amend(self, caller: Caller, change: RecordId, request: AmendChange) -> ChangeResult:
        """A new version of a waiting change (its proposer or a human reviewer), validated
        again; the older version is kept in its history."""
        rec = await self._get(change)
        self._waiting_or_raise(rec)
        if not self._can_amend(caller, rec):
            raise errors.PermissionDeniedError("only its proposer or a reviewer may amend this")
        if not request.why.strip():
            raise errors.InvalidRequestError("an amendment needs a `why`")
        self._check_version(rec, request.expect_version)
        advice = await self.validate(request.change, caller, exclude=change)
        version = self._version(rec)
        entry = {
            "version": version,
            "change": rec.change,
            "why": request.why,
            **self._stamp(caller),
        }
        async with self.ontology.lock:
            await self._unchanged(change, rec)
            await self.store.apply(
                [
                    Update(
                        change,
                        {
                            "change": request.change.model_dump(mode="json"),
                            "op": request.change.op,
                            "version": version + 1,
                            "history": [*(rec.history or []), entry],
                            "status": "pending",
                            "advice": advice,
                        },
                    )
                ]
            )
        return ChangeResult(
            id=change,
            status="pending",
            op=request.change.op,
            advice=advice,
            reason=f"version {version + 1}",
        )

    async def withdraw(
        self, caller: Caller, change: RecordId, request: WithdrawRequest
    ) -> ChangeResult:
        rec = await self._get(change)
        self._waiting_or_raise(rec)
        if not self._can_amend(caller, rec):
            raise errors.PermissionDeniedError("only its proposer or a reviewer may withdraw this")
        async with self.ontology.lock:
            await self._unchanged(change, rec)
            await self.store.apply(
                [
                    Update(
                        change,
                        {
                            "status": "withdrawn",
                            "decided_by": caller.actor_id,
                            "decided_at": self.now(),
                            "reason": request.reason,
                        },
                    ),
                    *await self._closing(change),
                ]
            )
        return ChangeResult(id=change, status="withdrawn", op=rec.op, reason=request.reason)

    async def request_changes(
        self, reviewer: Caller, change: RecordId, request: RequestChanges
    ) -> ChangeResult:
        """Send a waiting change back to its proposer with a comment."""
        if reviewer.actor.kind != "human" or not reviewer.can_write(GLOBAL_OWNER):
            raise errors.PermissionDeniedError("only a human reviewer can ask for changes")
        rec = await self._get(change)
        self._waiting_or_raise(rec)
        self._check_version(rec, request.expect_version)
        if not request.comment.strip():
            raise errors.InvalidRequestError("say what should change")
        comment = {"version": self._version(rec), "text": request.comment, **self._stamp(reviewer)}
        async with self.ontology.lock:
            await self._unchanged(change, rec)
            await self.store.apply(
                [
                    Update(
                        change,
                        {
                            "status": "changes_requested",
                            "comments": [*(rec.comments or []), comment],
                        },
                    )
                ]
            )
        return ChangeResult(
            id=change, status="changes_requested", op=rec.op, reason=request.comment
        )

    async def revert(self, reviewer: Caller, change: RecordId, why: str) -> ChangeResult:
        """Undo an applied change by applying its stored undo, as one new change. Refused when a
        later change touched the same concepts (revert those first)."""
        if reviewer.actor.kind != "human":
            raise errors.InvalidRequestError("only a human can revert an ontology change")
        if not reviewer.can_write(GLOBAL_OWNER):
            raise errors.PermissionDeniedError("changing the ontology needs global write access")
        if not why.strip():
            raise errors.InvalidRequestError("a revert needs a reason")
        rec = await self._get(change)
        if rec.status != "applied" or not rec.undo:
            raise errors.ProposalStateError(f"{change} cannot be reverted (it is {rec.status})")
        if rec.reverted_by is not None:
            raise errors.ProposalStateError(f"{change} was already reverted by {rec.reverted_by}")
        mine = set(rec.touched or [])
        everything = [
            c
            for c in await self.history(reviewer, 2000)
            if c.id is not None and c.id != change and c.status == "applied"
        ]
        newer = {c.id for c in everything if c.created_at > rec.created_at}
        later = [
            c
            for c in everything
            if c.id in newer
            and c.reverted_by is None  # an undone change no longer counts
            and c.reverts not in newer  # nor does a revert of a later change: they cancel out
            and mine & set(c.touched or [])
        ]
        if later:
            raise errors.ConflictError(
                "later changes touched the same concepts: revert them first ("
                + ", ".join(str(c.id) for c in later[:5])
                + ")"
            )
        undo = [ChangeRequest(change=u, rationale=why).change for u in rec.undo]  # type: ignore[arg-type]
        now = self.now()
        rid = RecordId("ontology_change", new_id())
        async with self.ontology.lock:
            touched = frozenset(mine)
            plans = [await self.planner.plan(u, reviewer, rid, touched) for u in undo]
            record = OntologyChange(
                owner=reviewer.owner,
                change={"op": "revert", "change": str(change)},
                op="revert",
                rationale=why,
                status="applied",
                proposed_by=reviewer.actor_id,
                proposer_kind=reviewer.actor.kind,
                created_at=now,
                decided_by=reviewer.actor_id,
                decided_at=now,
                touched=sorted({t for p in plans for t in p.touched}, key=str),
                undo=[u for p in plans for u in p.undo],
                reverts=change,
            )
            await self.store.apply(
                [
                    *(w for p in plans for w in p.writes),
                    Create.of(rid, record),
                    Update(change, {"reverted_by": rid}),
                ]
            )
            self.ontology.invalidate()
        return ChangeResult(id=rid, status="applied", op="revert")

    async def preview(self, caller: Caller, request: ChangeRequest) -> ChangePreview:
        """What the change would do now, without proposing or writing anything."""
        advice = await self.validate(request.change, caller)
        plan = await self.planner.plan(request.change, caller, None)
        memories = {str(c): await self.ontology.count_memories(caller, c) for c in plan.touched}
        return ChangePreview(
            op=request.change.op,
            advice=advice,
            effects=Effects(summary=plan.lines, touched=plan.touched, memories=memories),
        )
