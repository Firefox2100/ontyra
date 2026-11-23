"""Reviewing the examples learning uses (plans/25, phase 2): which ones look wrong, vetoing them,
and keeping a source, an owner or oneself out of learning, durably.

Nothing here edits a model. A veto changes an event's state and an exclusion is a row; the score
layer and any retraining read both, so the effect is a replay without the example. Admins can
review everyone's feedback; anyone else only their own, and only with a human credential.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from ontyra.models import RecordId, new_id
from ontyra.models.feedback import (
    ExclusionRequest,
    ExclusionView,
    Feedback,
    FeedbackView,
    LearningExclusion,
    OutlierView,
)
from ontyra.models.kinds import RevisionBase
from ontyra.models.search import RecallTrace
from ontyra.ports import Create, Store, Update
from ontyra.services.context import Caller
from ontyra.services.errors import InvalidRequestError, NotFoundError, PermissionDeniedError
from ontyra.services.feedback import FeedbackService, normalise_query, view
from ontyra.services.indexing import search_text_of

DISAGREES = 0.6  # |verdict - model score| from which an example is flagged
BURST_EVENTS = 20  # events from one source within BURST_WINDOW count as a burst
BURST_WINDOW = timedelta(minutes=10)
REASON_WEIGHT = 0.3  # what each reason beyond the disagreement adds to the rank


def rank_outliers(
    events: list[Feedback],
    scores: dict[RecordId, float],
    history: dict[tuple[RecordId, RecordId, str], list[Feedback]],
    others: dict[RecordId, list[Feedback]],
) -> list[tuple[Feedback, float | None, float | None, list[str], float]]:
    """(event, model score, disagreement, reasons, rank) for each event, best candidates first.

    `scores` maps an event id to the model's score for it at recall time; `history` holds every
    event of the same (owner, memory, query) in any state; `others` the other owners' active
    events on each memory (admins only).
    """
    by_source: dict[RecordId, list[datetime]] = defaultdict(list)
    for e in events:
        by_source[e.source].append(e.created_at)
    out: list[tuple[Feedback, float | None, float | None, list[str], float]] = []
    for e in events:
        assert e.id is not None
        reasons: list[str] = []
        score = scores.get(e.id)
        gap = None if score is None else abs(e.relevance - score)
        if gap is not None and gap >= DISAGREES:
            reasons.append("disagrees")
        past = history.get((e.owner, e.memory, normalise_query(e.query)), [])
        if any((p.relevance >= 0.5) != (e.relevance >= 0.5) for p in past if p.id != e.id):
            reasons.append("flipped")
        if any((o.relevance >= 0.5) != (e.relevance >= 0.5) for o in others.get(e.memory, [])):
            reasons.append("others_disagree")
        near = [t for t in by_source[e.source] if abs(t - e.created_at) <= BURST_WINDOW / 2]
        if len(near) >= BURST_EVENTS:
            reasons.append("burst")
        rank = (gap or 0.0) + REASON_WEIGHT * len([r for r in reasons if r != "disagrees"])
        out.append((e, score, gap, reasons, round(rank, 4)))
    out.sort(key=lambda row: (-row[4], row[0].created_at))
    return out


class FeedbackReviewService:
    def __init__(
        self, store: Store, feedback: FeedbackService, now: Callable[[], datetime]
    ) -> None:
        self.store = store
        self.feedback = feedback
        self.now = now

    @staticmethod
    def _human(caller: Caller) -> None:
        if caller.actor.kind != "human":
            raise PermissionDeniedError("reviewing feedback needs a human credential")

    @staticmethod
    def _admin(caller: Caller) -> bool:
        return caller.actor.kind == "human" and caller.has("admin")

    async def outliers(
        self, caller: Caller, *, limit: int = 50, everyone: bool = False
    ) -> list[OutlierView]:
        """The owner's active examples that most deserve a look (everyone's for an admin)."""
        if everyone and not self._admin(caller):
            raise PermissionDeniedError("reviewing everyone's feedback needs an administrator")
        await self.feedback.expire()
        where: dict[str, Any] = {"state": "active"}
        if not everyone:
            where["owner"] = caller.owner
        events = [e for e in await self.feedback.events(**where) if e.id is not None]
        traces = await self.store.get_many(list({e.trace for e in events if e.trace}))
        scores: dict[RecordId, float] = {}
        for e in events:
            trace = traces.get(e.trace) if e.trace else None
            if not isinstance(trace, RecallTrace) or e.revision is None or e.id is None:
                continue
            for row in (trace.scoring or {}).get("candidates", []):
                if row.get("rev") == str(e.revision):
                    value = row.get("rerank")
                    value = row.get("final") if value is None else value
                    if value is not None:
                        scores[e.id] = min(1.0, max(0.0, float(value)))
                    break
        history: dict[tuple[RecordId, RecordId, str], list[Feedback]] = defaultdict(list)
        for e in await self.feedback.events(**({} if everyone else {"owner": caller.owner})):
            if e.state in ("active", "superseded"):
                history[(e.owner, e.memory, normalise_query(e.query))].append(e)
        others: dict[RecordId, list[Feedback]] = defaultdict(list)
        if everyone:
            for e in events:
                others[e.memory].extend(
                    x for x in events if x.memory == e.memory and x.owner != e.owner
                )
        ranked = rank_outliers(events, scores, history, others)[:limit]
        revisions = await self.store.get_many([e.revision for e, *_ in ranked if e.revision])
        views: list[OutlierView] = []
        for e, score, gap, reasons, rank in ranked:
            rev = revisions.get(e.revision) if e.revision else None
            views.append(
                OutlierView(
                    feedback=view(e),
                    text=search_text_of(rev) if isinstance(rev, RevisionBase) else "",
                    model_score=None if score is None else round(score, 4),
                    disagreement=None if gap is None else round(gap, 4),
                    reasons=reasons,
                    rank=rank,
                )
            )
        return views

    async def _event(self, caller: Caller, feedback: RecordId) -> Feedback:
        self._human(caller)
        found = await self.store.get(feedback)
        if not isinstance(found, Feedback) or (
            found.owner != caller.owner and not self._admin(caller)
        ):
            raise NotFoundError(f"feedback {feedback} not found")
        return found

    async def veto(self, caller: Caller, feedback: RecordId, reason: str = "") -> FeedbackView:
        """Take an example out of learning for good; the layer is replayed without it."""
        found = await self._event(caller, feedback)
        if found.state == "vetoed":
            return view(found)
        now = self.now()
        await self.store.apply(
            [
                Update(
                    feedback,
                    {
                        "state": "vetoed",
                        "veto_reason": reason.strip() or None,
                        "vetoed_by": caller.actor_id,
                        "vetoed_at": now,
                    },
                )
            ]
        )
        self.feedback.changed(found.owner)
        found.state, found.veto_reason = "vetoed", reason.strip() or None
        return view(found)

    async def unveto(self, caller: Caller, feedback: RecordId) -> FeedbackView:
        found = await self._event(caller, feedback)
        if found.state != "vetoed":
            raise InvalidRequestError("this example is not vetoed")
        await self.store.apply(
            [
                Update(
                    feedback,
                    {"state": "active", "veto_reason": None, "vetoed_by": None, "vetoed_at": None},
                )
            ]
        )
        self.feedback.changed(found.owner)
        found.state, found.veto_reason = "active", None
        return view(found)

    # -- exclusions ---------------------------------------------------------------------------

    @staticmethod
    def _exclusion_view(e: LearningExclusion) -> ExclusionView:
        assert e.id is not None
        return ExclusionView(
            id=e.id,
            kind=e.kind,
            scope=e.scope,
            target=e.target,
            reason=e.reason,
            by=e.by,
            created_at=e.created_at,
            state=e.state,
        )

    async def exclusions(self, caller: Caller) -> list[ExclusionView]:
        """Active exclusions: an admin sees all, anyone else those that concern their own owner."""
        self._human(caller)
        rows = await self.feedback.exclusions()
        if not self._admin(caller):
            rows = [e for e in rows if e.target == caller.owner or e.by == caller.actor_id]
        return [self._exclusion_view(e) for e in rows]

    async def exclude(self, caller: Caller, request: ExclusionRequest) -> ExclusionView:
        """Quarantine a source or an owner (admin), or opt an owner out of training (that owner)."""
        self._human(caller)
        if request.kind == "opt_out":
            if request.scope != "owner":
                raise InvalidRequestError("an opt-out concerns an owner")
            target = request.target or caller.owner
            if target != caller.owner and not self._admin(caller):
                raise PermissionDeniedError("you can only opt yourself out")
        else:
            if not self._admin(caller):
                raise PermissionDeniedError("quarantine needs an administrator")
            if request.target is None:
                raise InvalidRequestError("quarantine needs a target (an actor or an owner)")
            target = request.target
            if target.table != ("actor" if request.scope == "source" else "owner"):
                raise InvalidRequestError(f"a {request.scope} target must be an {request.scope}")
        for existing in await self.feedback.exclusions():
            if (existing.kind, existing.scope, existing.target) == (
                request.kind,
                request.scope,
                target,
            ):
                return self._exclusion_view(existing)
        row = LearningExclusion(
            kind=request.kind,
            scope=request.scope,
            target=target,
            reason=request.reason.strip(),
            by=caller.actor_id,
            created_at=self.now(),
        )
        row_id = RecordId("learning_exclusion", new_id())
        await self.store.apply([Create.of(row_id, row)])
        self.feedback.changed(None)
        row.id = row_id
        return self._exclusion_view(row)

    async def lift(self, caller: Caller, exclusion: RecordId) -> ExclusionView:
        self._human(caller)
        found = await self.store.get(exclusion)
        if not isinstance(found, LearningExclusion):
            raise NotFoundError(f"exclusion {exclusion} not found")
        own_opt_out = found.kind == "opt_out" and found.target == caller.owner
        if not (self._admin(caller) or own_opt_out):
            raise PermissionDeniedError("only an administrator can lift this")
        await self.store.apply([Update(exclusion, {"state": "lifted"})])
        self.feedback.changed(None)
        found.state = "lifted"
        return self._exclusion_view(found)

    async def opt_out(self, caller: Caller, enabled: bool) -> ExclusionView | None:
        """Keep (or stop keeping) your own feedback and text out of training."""
        self._human(caller)
        if enabled:
            return await self.exclude(
                caller, ExclusionRequest(kind="opt_out", scope="owner", reason="opted out")
            )
        for e in await self.feedback.exclusions():
            if e.kind == "opt_out" and e.target == caller.owner and e.id is not None:
                return await self.lift(caller, e.id)
        return None
