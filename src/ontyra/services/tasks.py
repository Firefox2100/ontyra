"""The maintenance queue: findings the daemon cannot judge become tasks that a host agent (or a
human) claims, resolves through ordinary proposals, and completes with a validated result.

Detection is deterministic (`ConsolidationService.scan`) and idempotent: a finding is keyed by the
revisions it concerns, so it is queued once, a decision that two memories are `distinct` is not
asked again, and a change to any member makes it a new finding. A task whose memories are no
longer live is closed as obsolete. The daemon never merges anything itself.

Single-daemon assumption: a claim reads the status, then writes it (as the ontology does).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any

from ontyra.models import RecordId, new_id
from ontyra.models.consolidation import DuplicateCluster, ScanRequest, UpdateCandidate
from ontyra.models.core import Proposal, Task, TaskKind
from ontyra.models.kinds import RevisionBase
from ontyra.models.ontology import Concept
from ontyra.models.proposals import ProposalRequest, RetractMemory, SupersedeMemory
from ontyra.models.search import SearchDoc
from ontyra.models.tasks import (
    MAINTENANCE_KINDS,
    DetectReport,
    TaskResult,
    TaskView,
)
from ontyra.ports import Create, Store, Update
from ontyra.services import errors
from ontyra.services.consolidation import ConsolidationService
from ontyra.services.context import GLOBAL_OWNER, Caller
from ontyra.services.indexing import doc_id
from ontyra.services.memory import MemoryService
from ontyra.services.ontology import OntologyService

CHOICES: dict[TaskKind, list[str]] = {
    "consolidate": ["merge", "distinct", "defer"],
    "review_contradiction": ["supersede", "distinct", "defer"],
    "review_stale_dependent": ["keep", "retract", "revise", "defer"],
    "review_feedback": ["retract", "revise", "keep", "defer"],
    "expire_check": ["retract", "revise", "keep", "defer"],
    "review_concept_drift": ["revise", "keep", "retract", "defer"],
}
NEEDS_PROPOSAL = {"merge", "supersede", "retract", "revise"}


def view(task: Task) -> TaskView:
    assert task.id is not None
    return TaskView(
        id=task.id,
        kind=task.kind,
        owner=task.owner,
        status=task.status,
        target=task.target,
        card=task.payload,
        result=task.result,
        claimed_by=task.claimed_by,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def duplicate_card(cluster: DuplicateCluster) -> dict[str, Any]:
    return {
        "finding": "duplicates",
        "instructions": (
            "These memories say the same thing. If they do, keep one and supersede the others: "
            "submit a `supersede` proposal for each (the kept memory as `new`, reason `merged`), "
            "then complete this task with decision `merge` and that proposal. If they differ in a "
            "way that "
            "matters, complete with `distinct` (it is not raised again). `defer` parks it."
        ),
        "keep": str(cluster.keep),
        "members": [m.model_dump(mode="json") for m in cluster.members],
        "pairs": [p.model_dump(mode="json") for p in cluster.pairs],
        "revisions": [str(m.revision) for m in cluster.members],
        "choices": CHOICES["consolidate"],
    }


def update_card(update: UpdateCandidate) -> dict[str, Any]:
    return {
        "finding": "update",
        "instructions": (
            "The same statement with a different value. If the newer one is current, submit a "
            "`supersede` proposal (the newer memory as `new`, the older as `target`, reason "
            "`outdated`) and complete this task with decision `supersede` and that proposal. If "
            "both hold (for example for different places), complete with `distinct`. `defer` "
            "parks it."
        ),
        "older": update.older.model_dump(mode="json"),
        "newer": update.newer.model_dump(mode="json"),
        "cosine": update.cosine,
        "revisions": [str(update.older.revision), str(update.newer.revision)],
        "choices": CHOICES["review_contradiction"],
    }


def expiry_card(
    *,
    memory: RecordId,
    kind: str,
    text: str,
    revision: RecordId,
    expires_at: datetime,
    expired: bool,
) -> dict[str, Any]:
    return {
        "finding": "expiry",
        "state": "expired" if expired else "expiring",
        "expires_at": expires_at.isoformat(),
        "memory": str(memory),
        "kind": kind,
        "text": text,
        "instructions": (
            "This memory "
            + ("has expired and is no longer recalled" if expired else "expires soon")
            + ". If it still holds, extend it (a `revise` proposal with a new "
            "`expires_at`, or without one for no expiry) and complete with `revise`. If it "
            "is no longer true, retract it (`retract`). If it should simply lapse, "
            "complete with `keep` (not raised again). `defer` parks it."
        ),
        "revisions": [str(revision)],
        "choices": CHOICES["expire_check"],
    }


def refinement_card(
    *, doc: SearchDoc, text: str, concept: Concept, successors: list[tuple[RecordId, str]]
) -> dict[str, Any]:
    names = ", ".join(f"{label} ({cid})" for cid, label in successors)
    return {
        "finding": "concept_split",
        "memory": str(doc.memory),
        "kind": doc.kind,
        "text": text,
        "concept": {"id": str(doc.about[0]) if doc.about else None, "label": concept.label},
        "successors": [{"id": str(cid), "label": label} for cid, label in successors],
        "instructions": (
            f"This memory is about {concept.label}, which was split into {names}. It is still "
            "found for any of them, so nothing is lost; if it is really about one, `revise` it "
            "(a proposal that changes `about` to that concept) and complete with `revise` and "
            "that proposal. If it holds for all of them, complete with `keep` (not raised "
            "again). `retract` if it is no longer true; `defer` parks it."
        ),
        "revisions": [str(doc.rev)],
        "choices": CHOICES["review_concept_drift"],
    }


class TaskService:
    def __init__(
        self,
        store: Store,
        now: Callable[[], datetime],
        consolidation: ConsolidationService,
        memory: MemoryService,
        expiry_warn_days: int = 14,
        ontology: OntologyService | None = None,
        refine_cap: int = 200,
    ) -> None:
        self.ontology = ontology
        self.refine_cap = refine_cap
        self.store = store
        self.now = now
        self.consolidation = consolidation
        self.memory = memory
        self.expiry_warn_days = expiry_warn_days

    # -- reading ---------------------------------------------------------------------------

    async def list(
        self,
        caller: Caller,
        *,
        kinds: Sequence[TaskKind] = (),
        statuses: Sequence[str] = ("pending", "claimed"),
        limit: int = 50,
    ) -> list[TaskView]:
        where_in: dict[str, Sequence[Any]] = {"owner": caller.visible_owners()}
        if kinds:
            where_in["kind"] = list(kinds)
        if statuses:
            where_in["status"] = list(statuses)
        rows = await self.store.select(
            "task", where_in=where_in, limit=limit, order_by="created_at", descending=True
        )
        return [view(t) for t in rows if isinstance(t, Task) and self._may_see(caller, t)]

    async def get(self, caller: Caller, task: RecordId) -> TaskView:
        return view(await self._visible(caller, task))

    # -- detecting -------------------------------------------------------------------------

    async def detect(self, caller: Caller) -> DetectReport:
        """Scan for duplicates and updates, queue what is new, close what no longer applies."""
        report = DetectReport()
        scan = await self.consolidation.scan(caller, ScanRequest())
        report.model_errors = scan.model_errors
        now = self.now()
        writes: list[Create] = []
        seen: set[str] = set()
        findings: list[tuple[TaskKind, str, RecordId | None, dict[str, Any], Any]] = []
        for cluster in scan.duplicates:
            card = duplicate_card(cluster)
            key = "consolidate:" + "|".join(sorted(card["revisions"]))
            findings.append(("consolidate", key, cluster.keep, card, cluster.members[0].owner))
        for update in scan.updates:
            card = update_card(update)
            key = "update:" + "|".join(sorted(card["revisions"]))
            findings.append(
                ("review_contradiction", key, update.newer.memory, card, update.newer.owner)
            )
        findings += await self._expiry_findings(caller, now)
        findings += await self._refinement_findings(caller)
        for kind, key, target, card, owner in findings:
            if key in seen:
                continue
            seen.add(key)
            if not caller.can_write(owner):
                report.skipped += 1
                continue
            if await self.store.select("task", {"dedupe_key": key}, limit=1):
                report.existing += 1
                continue
            task = Task(
                kind=kind,
                owner=owner,
                target=target,
                dedupe_key=key,
                payload=card,
                created_by=caller.actor_id,
                created_at=now,
            )
            tid = RecordId("task", new_id())
            writes.append(Create.of(tid, task))
            report.created.append(view(task.model_copy(update={"id": tid})))
        if writes:
            await self.store.apply(writes)
        report.closed = await self._close_obsolete(caller, now)
        report.reopened = await self._reopen_rejected(caller, now)
        return report

    async def _refinement_findings(
        self, caller: Caller
    ) -> list[tuple[TaskKind, str, RecordId | None, dict[str, Any], Any]]:
        """Live memories still about a concept that was split: which successor is it about?
        Optional precision work (recall already finds them), capped per split concept."""
        if self.ontology is None:
            return []
        onto = await self.ontology.ontology()
        out: list[tuple[TaskKind, str, RecordId | None, dict[str, Any], Any]] = []
        for cid, c in onto.concepts.items():
            targets = onto.successors(cid)
            if c.status != "deprecated" or not targets:
                continue
            docs = await self.store.docs(
                caller.visible_owners(), about=[cid], limit=self.refine_cap
            )
            texts = {
                r: rec.text
                for r, rec in (await self.store.get_many([d.rev for d in docs])).items()
                if isinstance(rec, RevisionBase)
            }
            succ = [(t, onto.concepts[t].label) for t in targets if t in onto.concepts]
            for doc in docs:
                card = refinement_card(
                    doc=doc.model_copy(update={"about": [cid]}),
                    text=texts.get(doc.rev, doc.text),
                    concept=c,
                    successors=succ,
                )
                out.append(
                    ("review_concept_drift", f"refine:{doc.rev}:{cid}", doc.memory, card, doc.owner)
                )
        return out

    async def _expiry_findings(
        self, caller: Caller, now: datetime
    ) -> list[tuple[TaskKind, str, RecordId | None, dict[str, Any], Any]]:
        """Live memories that have expired or will within the warning window."""
        horizon = now + timedelta(days=self.expiry_warn_days)
        docs = await self.store.docs(caller.visible_owners(), expires_before=horizon, limit=500)
        texts = {
            r: rec.text
            for r, rec in (await self.store.get_many([d.rev for d in docs])).items()
            if isinstance(rec, RevisionBase)
        }
        out: list[tuple[TaskKind, str, RecordId | None, dict[str, Any], Any]] = []
        for doc in docs:
            assert doc.expires_at is not None
            expired = doc.expires_at <= now
            card = expiry_card(
                memory=doc.memory,
                kind=doc.kind,
                text=texts.get(doc.rev, doc.text),
                revision=doc.rev,
                expires_at=doc.expires_at,
                expired=expired,
            )
            out.append(("expire_check", f"expire:{doc.rev}", doc.memory, card, doc.owner))
        return out

    async def _close_obsolete(self, caller: Caller, now: datetime) -> int:
        """Close open tasks of the caller's visible memory when a revision they name is gone."""
        open_tasks = await self.store.select(
            "task",
            where_in={
                "owner": caller.visible_owners(),
                "kind": list(MAINTENANCE_KINDS),
                "status": ["pending", "claimed", "parked"],
            },
            limit=1000,
        )
        closing: list[Update] = []
        for task in open_tasks:
            if not isinstance(task, Task) or task.id is None or not caller.can_write(task.owner):
                continue
            revisions = [RecordId.parse(r) for r in task.payload.get("revisions", [])]
            if not revisions:
                continue
            live = await self.store.get_many([doc_id(r) for r in revisions])
            if all(isinstance(d, SearchDoc) for d in live.values()) and len(live) == len(revisions):
                continue
            outcome: dict[str, Any] = {"outcome": "obsolete"}
            if task.result.get("awaiting_review"):  # an update merges into the old result
                outcome["awaiting_review"] = False
            closing.append(
                Update(task.id, {"status": "done", "updated_at": now, "result": outcome})
            )
        if closing:
            await self.store.apply(closing)
        return len(closing)

    async def _reopen_rejected(self, caller: Caller, now: datetime) -> int:
        """A task waiting on a held proposal returns to the queue when that proposal is rejected."""
        waiting = await self.store.select(
            "task",
            where_in={
                "owner": caller.visible_owners(),
                "kind": list(MAINTENANCE_KINDS),
                "status": ["claimed"],
            },
            limit=1000,
        )
        reopen: list[Update] = []
        for task in waiting:
            cited = task.result.get("proposal") if isinstance(task, Task) else None
            if not isinstance(task, Task) or task.id is None or not cited:
                continue
            proposal = await self.store.get(RecordId.parse(cited))
            if isinstance(proposal, Proposal) and proposal.status == "rejected":
                reopen.append(
                    Update(
                        task.id,
                        {
                            "status": "pending",
                            "claimed_by": None,
                            "updated_at": now,
                            "result": {
                                "outcome": "proposal_rejected",
                                "proposal": cited,
                                "awaiting_review": False,
                            },
                        },
                    )
                )
        if reopen:
            await self.store.apply(reopen)
        return len(reopen)

    # -- working ---------------------------------------------------------------------------

    async def claim(self, caller: Caller, task_id: RecordId) -> TaskView:
        task = await self._workable(caller, task_id)
        if task.status != "pending":
            raise errors.ConflictError(f"{task_id} is {task.status}, not pending")
        await self.store.apply(
            [
                Update(
                    task_id,
                    {"status": "claimed", "claimed_by": caller.actor_id, "updated_at": self.now()},
                )
            ]
        )
        return await self.get(caller, task_id)

    async def release(self, caller: Caller, task_id: RecordId) -> TaskView:
        task = await self._workable(caller, task_id)
        if task.status != "claimed":
            raise errors.ConflictError(f"{task_id} is {task.status}, not claimed")
        await self.store.apply(
            [Update(task_id, {"status": "pending", "claimed_by": None, "updated_at": self.now()})]
        )
        return await self.get(caller, task_id)

    async def complete(self, caller: Caller, task_id: RecordId, result: TaskResult) -> TaskView:
        task = await self._workable(caller, task_id)
        if task.status not in ("claimed", "parked", "pending"):
            raise errors.ConflictError(f"{task_id} is already {task.status}")
        if task.status == "claimed" and task.claimed_by not in (None, caller.actor_id):
            raise errors.ConflictError(f"{task_id} is claimed by another actor")
        allowed = CHOICES.get(task.kind, [])
        if result.decision not in allowed:
            raise errors.InvalidRequestError(f"decision must be one of {allowed}")
        if result.decision in NEEDS_PROPOSAL:
            await self._check_proposal(caller, result)
        elif result.proposal is not None:
            raise errors.InvalidRequestError(f"decision {result.decision!r} takes no proposal")
        awaiting = (
            result.proposal is not None
            and (await self._proposal(caller, result.proposal)).status == "pending"
        )
        status = "parked" if result.decision == "defer" else "claimed" if awaiting else "done"
        body = result.model_dump(mode="json", exclude_none=True)
        if (
            awaiting
        ):  # a held proposal may still be rejected: the finding stays open until it is not
            body["awaiting_review"] = True
        await self.store.apply(
            [
                Update(
                    task_id,
                    {
                        "status": status,
                        "result": body,
                        "claimed_by": caller.actor_id,
                        "updated_at": self.now(),
                    },
                )
            ]
        )
        return await self.get(caller, task_id)

    async def resolve(self, caller: Caller, task_id: RecordId, decision: str) -> TaskView:
        """Do what a `merge` or `supersede` decision means, as an ordinary proposal from the
        caller (so the review policy applies to it), then complete the task citing it."""
        task = await self._workable(caller, task_id)
        card = task.payload
        if task.kind == "consolidate" and decision == "merge":
            keep = RecordId.parse(card["keep"])
            ops = [
                SupersedeMemory(new=keep, target=RecordId.parse(m["memory"]), reason_code="merged")
                for m in card["members"]
                if RecordId.parse(m["memory"]) != keep
            ]
        elif task.kind == "review_contradiction" and decision == "supersede":
            ops = [
                SupersedeMemory(
                    new=RecordId.parse(card["newer"]["memory"]),
                    target=RecordId.parse(card["older"]["memory"]),
                    reason_code="outdated",
                )
            ]
        elif decision == "retract" and task.kind in (
            "expire_check",
            "review_feedback",
            "review_stale_dependent",
            "review_concept_drift",
        ):
            ops = [
                RetractMemory(
                    memory=task.target if task.target else RecordId.parse(card["memory"]),
                    reason=f"retracted from task {task_id}",
                )
            ]
        else:
            raise errors.InvalidRequestError(
                f"a {task.kind} task cannot be resolved with {decision!r}; complete it instead"
            )
        space = "global" if task.owner == GLOBAL_OWNER and caller.owner != GLOBAL_OWNER else "own"
        done = await self.memory.submit(
            caller,
            ProposalRequest(ops=ops, space=space, note=f"resolves {task_id}"),  # type: ignore[arg-type]
        )
        if done.status == "rejected":
            why = "; ".join(
                f"{f.code}: {f.message}" for f in done.findings if f.severity == "error"
            )
            raise errors.InvalidRequestError(f"the proposal was refused: {why}")
        return await self.complete(
            caller,
            task_id,
            TaskResult(decision=decision, proposal=done.proposal),  # type: ignore[arg-type]
        )

    async def _proposal(self, caller: Caller, proposal_id: RecordId) -> Proposal:
        proposal = await self.store.get(proposal_id)
        if not isinstance(proposal, Proposal) or proposal.owner not in caller.visible_owners():
            raise errors.NotFoundError(f"proposal {proposal_id} does not exist")
        return proposal

    async def _check_proposal(self, caller: Caller, result: TaskResult) -> None:
        if result.proposal is None:
            raise errors.InvalidRequestError(
                f"decision {result.decision!r} must cite the proposal that did it"
            )
        proposal = await self._proposal(caller, result.proposal)
        if proposal.status not in ("committed", "pending"):
            raise errors.InvalidRequestError(
                f"proposal {result.proposal} is {proposal.status}; cite one that is committed "
                "or waiting for review"
            )

    # -- access ----------------------------------------------------------------------------

    @staticmethod
    def _may_see(caller: Caller, task: Task) -> bool:
        if task.owner not in caller.visible_owners():
            return False
        if task.kind in ("review_proposal", "review_ontology_change"):
            return caller.can_review(task.owner)  # only those who could decide it
        return True

    async def _visible(self, caller: Caller, task_id: RecordId) -> Task:
        found = await self.store.get(task_id) if task_id.table == "task" else None
        if not isinstance(found, Task) or not self._may_see(caller, found):
            raise errors.NotFoundError(f"task {task_id} not found")
        return found

    async def _workable(self, caller: Caller, task_id: RecordId) -> Task:
        task = await self._visible(caller, task_id)
        if task.kind not in MAINTENANCE_KINDS:
            raise errors.InvalidRequestError(
                f"a {task.kind} task is decided through its own endpoint, not here"
            )
        if not caller.can_write(task.owner):
            raise errors.PermissionDeniedError("you may not change this memory")
        return task
