"""Feedback on recalled memories and explicit, user-started retraining of the reranker.

Feedback is retained, never folded into weights. A conflict resolver keeps the verdict of the
more trusted source (the newer one on a tie) for each (memory, query); expiry retires old events;
a retrain uses the events that still count. Retraining is separate from recording: it happens
only when a human asks for it and only if the configured reranker supports it.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from collections.abc import Callable
from contextlib import suppress
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
    GateRequest,
    GateVerdict,
    LayerState,
    LearningExclusion,
    LiveResult,
    RerankerRun,
    RerankerStatus,
    RerankExample,
    TrainingPolicy,
    TrainOptions,
)
from ontyra.models.kinds import RevisionBase
from ontyra.models.search import RecallTrace
from ontyra.ports import Create, Store, Update
from ontyra.ports.adapters import LiveReranker, PromotionGate, Reranker, TrainableReranker
from ontyra.services.context import Caller
from ontyra.services.errors import InvalidRequestError, NotFoundError
from ontyra.services.indexing import search_text_of
from ontyra.services.scorelayer import ScoreLayer, cap_shares

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
        veto_reason=f.veto_reason,
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


log = logging.getLogger(__name__)


class FeedbackService:
    def __init__(self, store: Store, now: Callable[[], datetime], ttl_days: int = 365) -> None:
        self.store = store
        self.now = now
        self.ttl_days = ttl_days
        self.on_change: Callable[[RecordId | None], None] | None = None  # the score layer's reset
        self.layer: ScoreLayer | None = None  # set by the composition root when it is enabled

    def changed(self, owner: RecordId | None) -> None:
        if self.on_change is not None:
            self.on_change(owner)

    async def active(self, owner: RecordId) -> list[Feedback]:
        """The owner's events that still count."""
        await self.expire()
        return await self._counted(await self.events(owner=owner, state="active"), training=False)

    async def exclusions(self, state: str = "active") -> list[LearningExclusion]:
        rows = await self.store.select("learning_exclusion", {"state": state}, limit=100000)
        return [r for r in rows if isinstance(r, LearningExclusion)]

    async def _counted(self, rows: list[Feedback], *, training: bool) -> list[Feedback]:
        """The events that may be learned from: not from a quarantined source or owner, and, for
        training, not from an owner who opted out (their own score layer may still use them)."""
        held = await self.exclusions()
        sources = {e.target for e in held if e.kind == "quarantine" and e.scope == "source"}
        owners = {e.target for e in held if e.kind == "quarantine" and e.scope == "owner"}
        if training:
            owners |= {e.target for e in held if e.kind == "opt_out"}
        return [f for f in rows if f.source not in sources and f.owner not in owners]

    async def layer_state(self, caller: Caller) -> LayerState:
        """What the score layer does for this caller's own feedback, and why."""
        if self.layer is None:
            return LayerState(enabled=False, status="off", reason="the score layer is off")
        return (await self.layer.state(caller.owner))[0]

    async def events(self, **where: Any) -> list[Feedback]:
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
            for f in await self.events(owner=caller.owner, memory=request.memory, state="active")
            if normalise_query(f.query) == key
        ]
        wins, losers = resolve(event, same)
        event_id = RecordId("feedback", new_id())
        twin = next(
            (
                f
                for f in await self.events(
                    owner=caller.owner, memory=request.memory, state="vetoed"
                )
                if normalise_query(f.query) == key and f.source == event.source
            ),
            None,
        )
        if twin is not None:  # a vetoed example stays out when the same source sends it again
            event.state, event.veto_reason = "vetoed", twin.veto_reason
            event.vetoed_by, event.vetoed_at = twin.vetoed_by, twin.vetoed_at
            wins, losers = False, []
        elif not wins:
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
        self.changed(caller.owner)
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
        report: dict[str, Any] = {"label": event.label}
        if memory.owner == caller.owner:  # someone else's report on shared memory stays private
            report |= {"query": event.query, "by": caller.actor.name, "note": event.note}
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
        self.changed(found.owner)
        found.state = "retracted"
        return view(found)

    async def expire(self) -> int:
        """Mark active events past their expiry as expired."""
        now = self.now()
        due = [
            f
            for f in await self.events(state="active")
            if f.expires_at is not None and f.expires_at <= now and f.id is not None
        ]
        if due:
            await self.store.apply([Update(f.id, {"state": "expired"}) for f in due if f.id])
            self.changed(None)
        return len(due)

    async def list(
        self, caller: Caller, state: str | None = None, memory: RecordId | None = None
    ) -> list[FeedbackView]:
        where: dict[str, Any] = {"owner": caller.owner}
        if state:
            where["state"] = state
        if memory:
            where["memory"] = memory
        return [view(f) for f in await self.events(**where)]

    async def examples(
        self, owner: RecordId | None = None, *, humans_only: bool = False
    ) -> list[RerankExample]:
        """Training pairs from the events that still count (query, text of the judged revision)."""
        await self.expire()
        where: dict[str, Any] = {"state": "active"}
        if owner is not None:
            where["owner"] = owner
        active = await self._counted(await self.events(**where), training=True)
        if humans_only:
            active = [f for f in active if f.source_kind == "human"]
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
                        source=str(f.source),
                    )
                )
        return out

    async def summary(self, caller: Caller | None = None) -> FeedbackSummary:
        await self.expire()
        rows = await self.events(**({"owner": caller.owner} if caller else {}))
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


Anchors = Callable[[], tuple[list[RerankExample], list[GateRequest]]]


def metric_gate(metrics: dict[str, Any], tolerance: float) -> GateVerdict | None:
    """The verdict from the trusted requests scored before and after (None when none were)."""
    before, after = metrics.get("gate_before"), metrics.get("gate_after")
    if not before or not after:
        return None
    detail = {"before": before, "after": after, "tolerance": tolerance}
    passed = all(after[k] >= before[k] - tolerance for k in ("mrr", "pairwise"))
    return GateVerdict(passed=passed, detail=detail)


class RerankTrainingService:
    def __init__(
        self,
        store: Store,
        feedback: FeedbackService,
        reranker: Reranker | None,
        now: Callable[[], datetime],
        policy: TrainingPolicy | None = None,
        anchors: Anchors | None = None,
    ) -> None:
        self.store = store
        self.feedback = feedback
        self.reranker = reranker
        self.now = now
        self.cfg = policy or TrainingPolicy()
        self.min_examples = self.cfg.min_examples
        self.anchors = anchors  # clean benchmark pairs and the trusted gate requests
        self.gate: PromotionGate | None = None  # an extra check, run with the candidate active
        self._stale = False  # feedback changed since the live edit was last fitted
        self._timer: asyncio.TimerHandle | None = None
        self._task: asyncio.Task[None] | None = None

    def _trainable(self) -> TrainableReranker | None:
        return self.reranker if isinstance(self.reranker, TrainableReranker) else None

    async def status(self) -> RerankerStatus:
        trainable = self._trainable()
        if trainable is not None:
            with suppress(Exception):  # an unreachable worker shows as an unknown version
                await trainable.refresh()
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
            live=await self._live_status(),
        )

    async def _live_status(self) -> dict[str, Any]:
        info: dict[str, Any] = {"configured": self.cfg.live, "stale": self._stale}
        if isinstance(self.reranker, LiveReranker):
            with suppress(Exception):
                info |= await self.reranker.live_status()
        return info

    async def _examples(self, owner: RecordId | None) -> list[RerankExample]:
        """The feedback that may train: the pool's (`owner` when the pool is `own`), from the
        sources allowed, no source above its share of the weight."""
        rows = await self.feedback.examples(
            owner if self.cfg.pool == "own" else None,
            humans_only=self.cfg.train_sources == "human",
        )
        weights = cap_shares(
            [e.source for e in rows], [e.weight for e in rows], self.cfg.source_cap
        )
        return [e.model_copy(update={"weight": w}) for e, w in zip(rows, weights, strict=True)]

    async def train(self, caller: Caller, options: TrainOptions | None = None) -> RerankerRun:
        opts = options or TrainOptions()
        trainable = self._trainable()
        if self.reranker is None:
            raise InvalidRequestError("no reranker is configured")
        if trainable is None:
            raise InvalidRequestError("the configured reranker is hosted and cannot be retrained")
        if caller.actor.kind != "human":
            raise InvalidRequestError("retraining is started by a human")
        examples = await self._examples(caller.owner)
        floor = opts.min_examples if opts.min_examples is not None else self.min_examples
        if len(examples) < floor:
            raise InvalidRequestError(
                f"{len(examples)} usable feedback pairs; at least {floor} are needed"
            )
        if len({e.relevance >= 0.5 for e in examples}) < 2:
            raise InvalidRequestError("feedback needs both relevant and not relevant examples")
        anchors, gate = self.anchors() if self.anchors else ([], [])
        count = min(len(anchors), int(self.cfg.anchor_ratio * len(examples)))
        mixed = examples + random.Random(opts.seed).sample(anchors, count)
        await trainable.refresh()
        previous = trainable.version
        try:
            result = await trainable.train(mixed, opts, gate)
        except ValueError as error:  # the worker refused (busy, cannot train here)
            raise InvalidRequestError(str(error)) from error
        verdict = metric_gate(result.metrics, self.cfg.gate_tolerance)
        activated, note = False, result.note
        if verdict is not None and not verdict.passed:
            note = "; ".join(x for x in (note, "rejected by the promotion gate") if x)
        elif opts.activate:
            await trainable.activate(result.version)
            activated = True
            if self.gate is not None:
                extra = await self.gate.check(trainable, result.version, previous)
                if not extra.passed:
                    await trainable.activate(previous)  # roll back: the new version stays
                    activated, verdict = False, extra
                    note = "; ".join(x for x in (note, "rejected by the promotion gate") if x)
        run = RerankerRun(
            reranker=trainable.name,
            version=result.version,
            examples=len(examples),
            metrics=result.metrics | {"anchors": len(mixed) - len(examples)},
            activated=activated,
            started_by=caller.actor_id,
            created_at=self.now(),
            note=note,
            gate=verdict.model_dump(mode="json") if verdict else None,
        )
        run_id = RecordId("reranker_run", new_id())
        await self.store.apply([Create.of(run_id, run)])
        run.id = run_id
        return run

    # -- the live tier: a bounded edit of the shared head, replayed from the version's own head ----

    def _live(self) -> LiveReranker:
        if not self.cfg.live:
            raise InvalidRequestError("live edits are off (ONTYRA_RERANK__LIVE)")
        if self.cfg.pool != "all":
            raise InvalidRequestError(
                "the head is shared, so a live edit needs ONTYRA_RERANK__POOL=all "
                "(per-owner learning is the score layer)"
            )
        if not isinstance(self.reranker, LiveReranker):
            raise InvalidRequestError("the configured reranker has no live edit (use the worker)")
        return self.reranker

    def mark_stale(self) -> None:
        """Feedback changed (a verdict, a veto, an exclusion): the live edit is out of date, and
        with live edits on it is refitted from scratch, batched, soon."""
        self._stale = True
        if not self.cfg.live or self._timer is not None or (self._task and not self._task.done()):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # no event loop (a synchronous caller): the next explicit call refits
            return
        self._timer = loop.call_later(self.cfg.live_every_seconds, self._fire)

    def _fire(self) -> None:
        self._timer = None
        self._task = asyncio.get_running_loop().create_task(self._refit())

    async def _refit(self) -> None:
        try:
            await self.live_update(None)
        except Exception as error:  # the worker may be busy, down or without enough examples
            log.warning("live head edit not refitted: %s", error)

    def close(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._task is not None:
            self._task.cancel()

    async def live_update(self, caller: Caller | None = None) -> LiveResult:
        """Refit the live head edit from the snapshot and the examples that count now (a replay),
        and serve it unless it ranks the trusted benchmark requests worse. `caller` is None for the
        scheduled refit; a person asking must be a human."""
        live = self._live()
        if caller is not None and caller.actor.kind != "human":
            raise InvalidRequestError("a live edit is started by a human or by the schedule")
        examples = await self._examples(None)
        anchors, gate = self.anchors() if self.anchors else ([], [])
        usable = len(examples) >= self.cfg.live_min_examples and (
            len({e.relevance >= 0.5 for e in examples}) == 2
        )
        if not usable:  # too little evidence: the head serves unedited
            await live.live_reset()
            self._stale = False
            note = (
                f"{len(examples)} usable pairs of both classes needed: {self.cfg.live_min_examples}"
            )
            return LiveResult(applied=False, examples=len(examples), note=note)
        count = min(len(anchors), int(self.cfg.anchor_ratio * len(examples)))
        mixed = examples + random.Random(13).sample(anchors, count)
        result = await live.live_update(
            mixed,
            gate,
            max_distance=self.cfg.live_max_distance,
            tolerance=self.cfg.gate_tolerance,
        )
        self._stale = False
        result.examples = len(examples)
        if result.reverted:
            result.note = "the edit ranked the trusted requests worse, so it was not applied"
        info = await live.live_status()
        run = RerankerRun(
            reranker=getattr(live, "name", "reranker"),
            version=str(info.get("version", live.version)),
            examples=len(examples),
            metrics=result.metrics | {"distance": result.distance, "anchors": count},
            activated=result.applied,
            started_by=caller.actor_id if caller else RecordId("actor", "live-schedule"),
            created_at=self.now(),
            note=result.note,
            kind="live",
        )
        await self.store.apply([Create.of(RecordId("reranker_run", new_id()), run)])
        return result

    async def live_reset(self, caller: Caller) -> LiveResult:
        """Drop the live edit: the version's own head serves again."""
        live = self._live()
        if caller.actor.kind != "human":
            raise InvalidRequestError("only a human resets the live edit")
        await live.live_reset()
        self._stale = False
        return LiveResult(applied=False, note="the live edit was removed")
