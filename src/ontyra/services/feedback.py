"""Feedback on recalled memories and explicit, user-started retraining of the reranker.

Feedback is retained, never folded into weights. A conflict resolver keeps the verdict of the
more trusted source (the newer one on a tie) for each (memory, query); expiry retires old events;
a retrain uses the events that still count. Retraining is separate from recording: it happens
only when a human asks for it and only if the configured reranker supports it.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from ontyra.models import RecordId, new_id
from ontyra.models.core import Memory, Task
from ontyra.models.feedback import (
    LABEL_RELEVANCE,
    Feedback,
    FeedbackRequest,
    FeedbackSummary,
    FeedbackView,
    RerankerRun,
    RerankerStatus,
    RerankExample,
    TrainOptions,
)
from ontyra.models.kinds import RevisionBase
from ontyra.models.search import RecallTrace
from ontyra.ports import Create, Store, Update
from ontyra.ports.adapters import PromotionGate, Reranker, TrainableReranker
from ontyra.services.context import Caller
from ontyra.services.errors import InvalidRequestError, NotFoundError
from ontyra.services.indexing import search_text_of

REPORTS = {"outdated", "wrong"}  # labels that ask someone to look at the memory itself


def normalise_query(query: str) -> str:
    return re.sub(r"\s+", " ", query.casefold()).strip()


def resolve(new: Feedback, existing: list[Feedback]) -> tuple[bool, list[Feedback]]:
    """Decide a new event against the active events for the same (memory, query).

    Returns `(new_wins, losers)`: the more trusted verdict wins (the newer on a tie); events it
    replaces are returned so the caller can mark them superseded.
    """
    losers: list[Feedback] = []
    for old in existing:
        if (new.weight, new.created_at) >= (old.weight, old.created_at):
            losers.append(old)
        else:
            return False, []
    return True, losers


def view(f: Feedback) -> FeedbackView:
    assert f.id is not None
    return FeedbackView(
        id=f.id,
        owner=f.owner,
        memory=f.memory,
        query=f.query,
        label=f.label,
        state=f.state,
        source=f.source,
        source_kind=f.source_kind,
        weight=f.weight,
        created_at=f.created_at,
        expires_at=f.expires_at,
        note=f.note,
    )


def feedback_card(
    *,
    memory: RecordId,
    revision: RecordId,
    kind: str,
    text: str,
    label: str,
    report: dict[str, Any],
) -> dict[str, Any]:
    """The task card for a memory reported `outdated` or `wrong`."""
    return {
        "finding": "feedback",
        "instructions": (
            "This memory was reported "
            f"{label}. Check it against what you know. If it is wrong or no longer "
            "true, retract it (`retract`); if it needs correcting, supersede or revise it "
            "and complete with `revise` and that proposal; if it is fine, complete with "
            "`keep` (not raised again for this revision). `defer` parks it."
        ),
        "memory": str(memory),
        "revision": str(revision),
        "kind": kind,
        "text": text,
        "reports": [report],
        "count": 1,
        "revisions": [str(revision)],
        "choices": ["retract", "revise", "keep", "defer"],
    }


class FeedbackService:
    def __init__(self, store: Store, now: Callable[[], datetime], ttl_days: int = 365) -> None:
        self.store = store
        self.now = now
        self.ttl_days = ttl_days

    async def _all(self, **where: Any) -> list[Feedback]:
        rows = await self.store.select("feedback", where or None, limit=100000)
        return [r for r in rows if isinstance(r, Feedback)]

    async def record(self, caller: Caller, request: FeedbackRequest) -> FeedbackView:
        memory = await self.store.get(request.memory)
        if not isinstance(memory, Memory) or memory.owner not in caller.visible_owners():
            raise NotFoundError(f"memory {request.memory} not found")
        revision = request.revision or memory.current
        found_revision = await self.store.get(revision) if revision is not None else None
        if not isinstance(found_revision, RevisionBase) or found_revision.memory != request.memory:
            raise NotFoundError("the feedback revision does not belong to that memory")
        if request.trace is not None:
            trace = await self.store.get(request.trace)
            if not isinstance(trace, RecallTrace) or trace.owner != caller.owner:
                raise NotFoundError(f"trace {request.trace} not found")
        now = self.now()
        days = request.expires_in_days if request.expires_in_days is not None else self.ttl_days
        event = Feedback(
            owner=caller.owner,
            memory=request.memory,
            revision=revision,
            query=request.query.strip(),
            label=request.label,
            relevance=LABEL_RELEVANCE[request.label],
            source=caller.actor_id,
            source_kind=caller.actor.kind,
            weight=caller.actor.trust,
            created_at=now,
            expires_at=now + timedelta(days=days) if days > 0 else None,
            session=request.session,
            trace=request.trace,
            note=request.note,
        )
        key = normalise_query(event.query)
        same = [
            f
            for f in await self._all(owner=caller.owner, memory=request.memory, state="active")
            if normalise_query(f.query) == key
        ]
        wins, losers = resolve(event, same)
        event_id = RecordId("feedback", new_id())
        if not wins:
            event.state = "superseded"
            event.superseded_by = same[0].id
        writes: list[Any] = [Create.of(event_id, event)]
        writes += [
            Update(f.id, {"state": "superseded", "superseded_by": event_id})
            for f in losers
            if f.id is not None
        ]
        if wins and request.label in REPORTS:
            writes += await self._report(caller, memory, revision, found_revision, event, now)
        await self.store.apply(writes)
        event.id = event_id
        return view(event)

    async def _report(
        self,
        caller: Caller,
        memory: Memory,
        revision: RecordId | None,
        found: RevisionBase,
        event: Feedback,
        now: datetime,
    ) -> list[Any]:
        """A memory reported `outdated` or `wrong` becomes (or adds to) one maintenance task per
        revision, for someone to retract, revise or confirm it. Judgement stays with them: the
        report alone never changes the memory."""
        assert revision is not None and memory.id is not None
        key = f"feedback:{revision}"
        report = {
            "label": event.label,
            "query": event.query,
            "by": caller.actor.name,
            "note": event.note,
        }
        existing = await self.store.select("task", {"dedupe_key": key}, limit=1)
        if existing:
            task = existing[0]
            if not isinstance(task, Task) or task.id is None or task.status in ("done", "failed"):
                return []  # decided already (a `keep` is not reopened by more reports)
            reports = [*task.payload.get("reports", []), report][-20:]
            return [
                Update(
                    task.id,
                    {"updated_at": now, "payload": {"reports": reports, "count": len(reports)}},
                )
            ]
        card = feedback_card(
            memory=memory.id,
            revision=revision,
            kind=memory.kind,
            text=found.text,
            label=event.label,
            report=report,
        )
        return [
            Create.of(
                RecordId("task", new_id()),
                Task(
                    kind="review_feedback",
                    owner=memory.owner,
                    target=memory.id,
                    dedupe_key=key,
                    payload=card,
                    created_by=caller.actor_id,
                    created_at=now,
                ),
            )
        ]

    async def retract(self, caller: Caller, feedback: RecordId) -> FeedbackView:
        found = await self.store.get(feedback)
        if not isinstance(found, Feedback):
            raise NotFoundError(f"feedback {feedback} not found")
        if found.owner != caller.owner:
            raise NotFoundError(f"feedback {feedback} not found")
        if found.source != caller.actor_id and caller.actor.kind != "human":
            raise InvalidRequestError("only the author or a human can retract feedback")
        await self.store.apply([Update(feedback, {"state": "retracted"})])
        found.state = "retracted"
        return view(found)

    async def expire(self) -> int:
        """Mark active events past their expiry as expired."""
        now = self.now()
        due = [
            f
            for f in await self._all(state="active")
            if f.expires_at is not None and f.expires_at <= now and f.id is not None
        ]
        if due:
            await self.store.apply([Update(f.id, {"state": "expired"}) for f in due if f.id])
        return len(due)

    async def list(
        self, caller: Caller, state: str | None = None, memory: RecordId | None = None
    ) -> list[FeedbackView]:
        where: dict[str, Any] = {"owner": caller.owner}
        if state:
            where["state"] = state
        if memory:
            where["memory"] = memory
        return [view(f) for f in await self._all(**where)]

    async def examples(self, owner: RecordId | None = None) -> list[RerankExample]:
        """Training pairs from the events that still count (query, text of the judged revision)."""
        await self.expire()
        active = await self._all(
            **({"state": "active", "owner": owner} if owner is not None else {"state": "active"})
        )
        revs = await self.store.get_many([f.revision for f in active if f.revision is not None])
        out: list[RerankExample] = []
        for f in active:
            rev = revs.get(f.revision) if f.revision is not None else None
            if isinstance(rev, RevisionBase):
                out.append(
                    RerankExample(
                        query=f.query,
                        text=search_text_of(rev),
                        relevance=f.relevance,
                        weight=f.weight,
                    )
                )
        return out

    async def summary(self, caller: Caller | None = None) -> FeedbackSummary:
        await self.expire()
        rows = await self._all(**({"owner": caller.owner} if caller else {}))
        by_label: dict[str, int] = {}
        for f in rows:
            if f.state == "active":
                by_label[f.label] = by_label.get(f.label, 0) + 1
        count = {
            s: sum(1 for f in rows if f.state == s)
            for s in ("active", "superseded", "expired", "retracted")
        }
        return FeedbackSummary(
            total=len(rows),
            active=count["active"],
            superseded=count["superseded"],
            expired=count["expired"],
            retracted=count["retracted"],
            by_label=by_label,
            trainable_examples=len(await self.examples(caller.owner if caller else None)),
            with_features=await self._with_features(rows),
        )

    async def _with_features(self, rows: list[Feedback]) -> int:
        """Active events whose trace holds the lane scores of the revision judged."""
        cited = [f for f in rows if f.state == "active" and f.trace and f.revision]
        traces = await self.store.get_many(list({f.trace for f in cited if f.trace}))
        count = 0
        for f in cited:
            trace = traces.get(f.trace) if f.trace else None
            if isinstance(trace, RecallTrace) and any(
                r["rev"] == str(f.revision) for r in (trace.scoring or {}).get("candidates", [])
            ):
                count += 1
        return count


class RerankTrainingService:
    def __init__(
        self,
        store: Store,
        feedback: FeedbackService,
        reranker: Reranker | None,
        now: Callable[[], datetime],
        min_examples: int = 50,
    ) -> None:
        self.store = store
        self.feedback = feedback
        self.reranker = reranker
        self.now = now
        self.min_examples = min_examples
        self.gate: PromotionGate | None = None  # set by whoever can judge a version (benchmark)

    def _trainable(self) -> TrainableReranker | None:
        return self.reranker if isinstance(self.reranker, TrainableReranker) else None

    async def status(self) -> RerankerStatus:
        trainable = self._trainable()
        runs = await self.store.select(
            "reranker_run", None, limit=20, order_by="created_at", descending=True
        )
        return RerankerStatus(
            reranker=getattr(self.reranker, "name", None) if self.reranker else None,
            trainable=trainable is not None,
            version=trainable.version if trainable else None,
            feedback=await self.feedback.summary(),
            min_examples=self.min_examples,
            runs=[r.model_dump(mode="json") for r in runs],
        )

    async def train(self, caller: Caller, options: TrainOptions | None = None) -> RerankerRun:
        opts = options or TrainOptions()
        trainable = self._trainable()
        if self.reranker is None:
            raise InvalidRequestError("no reranker is configured")
        if trainable is None:
            raise InvalidRequestError("the configured reranker is hosted and cannot be retrained")
        if caller.actor.kind != "human":
            raise InvalidRequestError("retraining is started by a human")
        examples = await self.feedback.examples()
        floor = opts.min_examples if opts.min_examples is not None else self.min_examples
        if len(examples) < floor:
            raise InvalidRequestError(
                f"{len(examples)} usable feedback pairs; at least {floor} are needed"
            )
        if len({e.relevance >= 0.5 for e in examples}) < 2:
            raise InvalidRequestError("feedback needs both relevant and not relevant examples")
        previous = trainable.version
        result = await trainable.train(examples, opts)
        activated, note, verdict = result.activated, result.note, None
        if activated and self.gate is not None:
            judged = await self.gate.check(trainable, result.version, previous)
            verdict = judged.model_dump(mode="json")
            if not judged.passed:
                await trainable.activate(previous)  # roll back: the new version stays on disk
                activated = False
                note = "; ".join(x for x in (note, "rejected by the promotion gate") if x)
        run = RerankerRun(
            reranker=trainable.name,
            version=result.version,
            examples=result.examples,
            metrics=result.metrics,
            activated=activated,
            started_by=caller.actor_id,
            created_at=self.now(),
            note=note,
            gate=verdict,
        )
        run_id = RecordId("reranker_run", new_id())
        await self.store.apply([Create.of(run_id, run)])
        run.id = run_id
        return run
