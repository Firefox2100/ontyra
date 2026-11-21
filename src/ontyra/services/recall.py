"""Recall: the shared pipeline behind recall (path B), briefing (C) and check (E)
(plans/03). Deterministic; no model is called except the optional embedder/reranker ports.

Stages: 1 resolve context → 2 candidates (lanes) → 3 visibility → 4 qualifier roles →
5 rank → 6 precedence/conflicts → 7 collapse + ledger → 8 pack → 9 render + trace.
"""

from __future__ import annotations

import hashlib
import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from ontyra.models import RecordId, new_id
from ontyra.models.core import Memory, RevState, Usage
from ontyra.models.kinds import DirectiveRev, EventRev, RevisionBase, TaskRev
from ontyra.models.recall import (
    Caution,
    CheckResult,
    Notice,
    RecallContext,
    RecallItem,
    RecallPack,
    RecallRequest,
    RecallSettings,
)
from ontyra.models.search import Delivery, RecallTrace, SearchDoc
from ontyra.ports import Create, Delete, Embedder, Hit, Reranker, Store, Update, Upsert
from ontyra.services.anchors import AnchorService, Chain
from ontyra.services.context import Caller
from ontyra.services.indexing import doc_id
from ontyra.services.ontology import OntologyService
from ontyra.services.qualifiers import Observation, QualifierOutcome, apply_roles
from ontyra.services.reasoner import Ontology
from ontyra.services.recall_pack import build_pack, estimate_tokens, render_pack
from ontyra.services.text import coverage
from ontyra.services.validation import rev_state_id, similarity

DUPLICATE_TEXT = 0.9
RRF_K = 60
_BOOKKEEPING = {
    "id",
    "memory",
    "seq",
    "text",
    "recorded_at",
    "proposed_by",
    "confidence",
    "about",
    "anchors",
    "qualifiers",
}


# ---- stage 1: resolved context ---------------------------------------------------------


log = logging.getLogger(__name__)


@dataclass
class Resolved:
    chain: Chain
    observations: list[Observation]
    ontology: Ontology
    has_location: bool
    unresolved: list[str] = field(default_factory=lambda: [])


@dataclass
class Assessment:
    """Whether and how a revision applies in a resolved context (stages 3-4)."""

    include: bool
    anchor: str | None = None
    distance: int | None = None
    outcome: QualifierOutcome = field(default_factory=QualifierOutcome)
    flags: list[str] = field(default_factory=lambda: [])
    caveats: list[str] = field(default_factory=lambda: [])
    reasons: list[str] = field(default_factory=lambda: [])


@dataclass
class Candidate:
    rev: RecordId
    lanes: dict[str, float] = field(default_factory=lambda: {})  # lane → score in [0, 1]
    ranks: dict[str, int] = field(default_factory=lambda: {})
    why: list[str] = field(default_factory=lambda: [])
    doc: SearchDoc | None = None
    revision: RevisionBase | None = None
    state: RevState | None = None
    assessment: Assessment | None = None
    relevance: float = 0.0
    score: float = 0.0


def assess(rev: RevisionBase, ctx: Resolved) -> Assessment:
    """Anchors (where) and qualifiers (when) of one revision against the context."""
    a = Assessment(include=True)
    if rev.anchors:
        matches = [(ctx.chain.match(x), x.uri) for x in rev.anchors]
        found = [(d, uri) for d, uri in matches if d is not None]
        if found:
            a.distance, a.anchor = min(found)
        elif ctx.has_location:
            a.include = False
            a.reasons.append("anchored elsewhere: " + ", ".join(x.uri for x in rev.anchors))
            return a
        else:
            a.flags.append("location_unknown")
            a.caveats.append("applies at " + ", ".join(x.uri for x in rev.anchors))
    a.outcome = apply_roles(rev.qualifiers, ctx.observations, ctx.ontology)
    a.caveats += a.outcome.caveats
    a.reasons += a.outcome.reasons
    if not a.outcome.include:
        a.include = False
    if a.outcome.triggered:
        a.flags.append("triggered")
    return a


def in_scope(rev: RevisionBase, assessment: Assessment, ctx: Resolved, topics: str = "") -> bool:
    """Is there positive evidence that this memory belongs in a briefing or check here?

    `scope: universal` always does. Otherwise it needs a tie to the context: an anchor that
    matched, a qualifier that was satisfied (not merely unknown), or a subject (`about`) that
    the context touches, directly or through the ontology. The context is its signals, the
    concepts of the resources in its anchor chain and, for a check, the concepts mentioned in
    the intent (`topics`). A directive with no tie at all is unscoped and is reached by recall
    of its text only; other kinds without a subject keep the older, permissive behaviour.
    """
    if rev.scope == "universal":
        return True
    if assessment.distance is not None or assessment.outcome.matched:
        return True
    if not rev.about:
        return not isinstance(rev, DirectiveRev)
    onto = ctx.ontology
    seen = [o.concept for o in ctx.observations]
    seen += [m.concept for m in onto.mentions(topics)] if topics else []
    return any(
        onto.entails(concept, about) or onto.entails(about, concept)
        for about in rev.about
        for concept in seen
    )


def _firm(c: Candidate) -> bool:
    """A candidate applies for certain: no unknown necessary qualifier, no unknown location."""
    a = c.assessment
    return a is None or (not a.outcome.demote and "location_unknown" not in a.flags)


def _detail(rev: RevisionBase) -> dict[str, Any]:
    data = rev.model_dump(mode="json", exclude=_BOOKKEEPING)
    return {k: v for k, v in data.items() if v not in (None, [], "", {})}


class RecallService:
    def __init__(
        self,
        store: Store,
        ontology: OntologyService,
        anchors: AnchorService,
        settings: RecallSettings,
        now: Callable[[], datetime],
        *,
        embedder: Embedder | None = None,
        reranker: Reranker | None = None,
        rerank_top_n: int = 30,
    ) -> None:
        self.store = store
        self.ontology = ontology
        self.anchors = anchors
        self.cfg = settings
        self.now = now
        self.embedder = embedder
        self.reranker = reranker
        self.rerank_top_n = rerank_top_n
        self._last_prune: datetime | None = None

    def calibration_note(self) -> str | None:
        """A sentence for the operator when the fusion thresholds were tuned for another embedder
        (or none), else None. The thresholds are set by `ontyra calibrate` (plans/22)."""
        if self.embedder is None or self.cfg.engine != "fusion":
            return None
        name = self.embedder.name
        if self.cfg.calibrated_for == name or name.startswith("hash-"):
            return None
        if self.cfg.calibrated_for is None:
            return (
                f"Recall thresholds are not marked as calibrated for {name}:"
                " run `ontyra calibrate`."
            )
        return (
            f"Recall thresholds were calibrated for {self.cfg.calibrated_for}, but {name} is"
            " running: run `ontyra calibrate`."
        )

    # ---- stage 1 ---------------------------------------------------------------------

    async def resolve_context(self, ctx: RecallContext) -> Resolved:
        onto = await self.ontology.ontology()
        chain = await self.anchors.resolve(ctx.uris)
        observations: list[Observation] = [Observation(concept=c) for c in chain.concepts]
        unresolved: list[str] = []
        for s in ctx.signals:
            try:
                cid = await self.ontology.resolve(s.concept)
            except Exception:  # unknown or ambiguous label: keep it as an unresolved signal
                unresolved.append(s.concept)
                continue
            observations.append(Observation(concept=cid, value=s.value, unit=s.unit))
        return Resolved(chain, observations, onto, bool(ctx.uris), unresolved)

    # ---- stage 2: candidate lanes ----------------------------------------------------

    def _vector_score(self, cosine: float) -> float:
        floor = self.cfg.vector_floor
        return max(0.0, (cosine - floor) / (1.0 - floor))

    def _add(
        self,
        cands: dict[RecordId, Candidate],
        hits: Sequence[Hit],
        lane: str,
        norm: Callable[[float], float],
    ) -> None:
        for rank, hit in enumerate(hits, start=1):
            score = norm(hit.score)
            if score <= 0:
                continue
            c = cands.setdefault(hit.rev, Candidate(hit.rev))
            c.lanes[lane] = max(c.lanes.get(lane, 0.0), score)
            c.ranks[lane] = min(c.ranks.get(lane, rank), rank)

    async def concept_lane(
        self,
        text: str,
        ctx: Resolved,
        owners: Sequence[RecordId],
        kinds: Sequence[str],
        limit: int,
    ) -> list[tuple[float, SearchDoc, str]]:
        """Concept lane: mentions, ontology expansion, memories about those concepts
        (best weight first, as `(weight, doc, why)`)."""
        weights: dict[RecordId, tuple[float, str]] = {}
        for m in ctx.ontology.mentions(text):
            for cid, (w, why) in ctx.ontology.expand(m.concept).items():
                if cid not in weights or weights[cid][0] < w:
                    weights[cid] = (
                        w,
                        f"about {ctx.ontology.concepts[cid].label} ({why})"
                        if cid in ctx.ontology.concepts
                        else why,
                    )
        if not weights:
            return []
        # Best weight first, tier by tier, so a flood of memories about a weakly related concept
        # (fetched newest first) can never push out one about the concept that was mentioned.
        ranked: list[tuple[float, SearchDoc, str]] = []
        seen: set[RecordId] = set()
        for tier in sorted({w for w, _ in weights.values()}, reverse=True):
            about = [c for c, (w, _) in weights.items() if w == tier]
            for d in await self.store.docs(owners, kinds=kinds, about=about, limit=limit):
                if d.rev in seen:
                    continue
                seen.add(d.rev)
                best = max((weights[c] for c in d.about if c in weights), key=lambda t: t[0])
                ranked.append((best[0], d, best[1]))
            if len(ranked) >= limit:
                break
        ranked.sort(key=lambda t: -t[0])
        return ranked[:limit]

    async def query_candidates(
        self, caller: Caller, text: str, ctx: Resolved, kinds: Sequence[str]
    ) -> dict[RecordId, Candidate]:
        owners = caller.visible_owners()
        n = self.cfg.candidates
        cands: dict[RecordId, Candidate] = {}
        # BM25 magnitudes depend on corpus statistics, so the lane keeps only the *relative*
        # score here; `hydrate` scales it by how much of the query the text actually covers.
        lexical = await self.store.search_text(text, owners, kinds=kinds, limit=n)
        top = max((h.score for h in lexical), default=0.0)
        self._add(cands, lexical, "lexical", lambda s: s / top if top > 0 else 0.0)
        if self.embedder is not None:
            vec = await self.embedder.embed_query(text)
            self._add(
                cands,
                await self.store.search_vector(vec, owners, kinds=kinds, limit=n),
                "vector",
                self._vector_score,
            )
        for rank, (w, d, why) in enumerate(
            await self.concept_lane(text, ctx, owners, kinds, n * 2), start=1
        ):
            c = cands.setdefault(d.rev, Candidate(d.rev))
            c.doc = d
            c.lanes["concept"] = max(c.lanes.get("concept", 0.0), w)
            c.ranks["concept"] = min(c.ranks.get("concept", rank), rank)
            if why not in c.why:
                c.why.append(why)
        return cands

    # ---- stages 3-4: hydrate, visibility, qualifiers ----------------------------------------

    async def hydrate(
        self,
        cands: dict[RecordId, Candidate],
        ctx: Resolved,
        *,
        only_applicable: bool = True,
        query: str | None = None,
    ) -> list[Candidate]:
        revs = await self.store.get_many(list(cands))
        states = await self.store.get_many([rev_state_id(r) for r in cands])
        docs = await self.store.get_many([doc_id(r) for r in cands])
        now = self.now()
        out: list[Candidate] = []
        for rid, c in cands.items():
            rev, state = revs.get(rid), states.get(rev_state_id(rid))
            if not isinstance(rev, RevisionBase) or not isinstance(state, RevState):
                continue
            if state.state != "committed" or state.lifecycle != "active":
                continue
            if state.valid_from > now or (state.valid_to is not None and state.valid_to <= now):
                continue
            if rev.expires_at is not None and rev.expires_at <= now:
                continue
            d = c.doc or docs.get(doc_id(rid))
            c.doc = d if isinstance(d, SearchDoc) else None
            if query and c.doc is not None and "lexical" in c.lanes:
                c.lanes["lexical"] *= coverage(query, c.doc.text)
            c.revision, c.state = rev, state
            c.assessment = assess(rev, ctx)
            if c.assessment.include or not only_applicable:
                out.append(c)
        return out

    # ---- stage 5: ranking -------------------------------------------------------------------

    def _usage_factor(self, usage: Usage | None) -> float:
        return 1.0 + (0.05 * math.log1p(usage.recalls) if usage else 0.0)

    async def rank(
        self, cands: list[Candidate], *, query: str | None, ranked_by_text: bool
    ) -> list[Candidate]:
        mems = [c.revision.memory for c in cands if c.revision is not None]
        usages = await self.store.get_many([RecordId("usage", m.id) for m in mems])
        lanes_used = {lane for c in cands for lane in c.lanes} or {"none"}
        norm = len(lanes_used) / (RRF_K + 1)
        now = self.now()
        for c in cands:
            assert c.revision is not None and c.assessment is not None and c.doc is not None
            weights = self.cfg.lane_weights
            c.relevance = min(
                1.0, max((weights.get(lane, 1.0) * s for lane, s in c.lanes.items()), default=0.0)
            )
            rrf = sum(1.0 / (RRF_K + r) for r in c.ranks.values()) / norm if c.ranks else 0.0
            base = 0.6 * c.relevance + 0.4 * min(1.0, rrf) if ranked_by_text else 0.5
            a = c.assessment
            mult = self.cfg.kind_weights.get(c.doc.kind, 1.0) * c.revision.confidence
            if a.distance is not None:
                mult *= 1.0 + self.cfg.anchor_bonus / (1 + a.distance)
            if a.outcome.boost:
                mult *= self.cfg.sufficient_boost
            if a.outcome.demote:
                mult *= self.cfg.demote_factor
            mult *= 1.0 + 0.05 * min(a.outcome.matched, 4)
            if isinstance(c.revision, EventRev):
                age = max(0.0, (now - c.revision.occurred_at).total_seconds() / 86400)
                mult *= max(0.2, 0.5 ** (age / self.cfg.event_half_life_days))
            usage = usages.get(RecordId("usage", c.revision.memory.id))
            mult *= self._usage_factor(usage if isinstance(usage, Usage) else None)
            c.score = base * mult
        if self.reranker is not None and query and ranked_by_text and cands:
            await self._rerank(cands, query)
        return sorted(cands, key=lambda c: (-c.score, str(c.rev)))

    async def _rerank(self, cands: list[Candidate], query: str) -> None:
        assert self.reranker is not None
        top = sorted(cands, key=lambda c: -c.score)[: self.rerank_top_n]
        scores = await self.reranker.rerank(query, [c.doc.text for c in top if c.doc])
        for c, s in zip(top, scores, strict=False):
            sig = s if 0.0 <= s <= 1.0 else 1.0 / (1.0 + math.exp(-s))
            c.relevance = 0.5 * c.relevance + 0.5 * sig
            c.score = c.score * (0.5 + sig)
            c.why.append(f"reranked {sig:.2f}")

    # ---- stage 6: precedence and conflicts ---------------------------------------------------

    async def precedence(
        self, ranked: list[Candidate]
    ) -> tuple[list[Candidate], list[Caution], list[dict[str, Any]]]:
        mems = [c.revision.memory for c in ranked if c.revision is not None]
        by_memory = {c.revision.memory: c for c in ranked if c.revision is not None}
        dropped: list[dict[str, Any]] = []
        cautions: list[Caution] = []
        overridden: dict[RecordId, RecordId] = {}  # candidate rev -> memory that overrides it
        for e in await self.store.memory_edges("overrides", mems):
            if e["in_memory"] in by_memory and e["out_memory"] in by_memory:
                if not _firm(by_memory[e["in_memory"]]):
                    continue  # an overrider whose own conditions are unknown does not win yet
                overridden[by_memory[e["out_memory"]].rev] = e["in_memory"]
        seen: set[frozenset[RecordId]] = set()
        for e in await self.store.memory_edges("contradicts", mems):
            a_mem, b_mem = e["in_memory"], e["out_memory"]
            pair = frozenset((a_mem, b_mem))
            if a_mem in by_memory and b_mem in by_memory and pair not in seen:
                seen.add(pair)
                cautions.append(
                    Caution(
                        memory=a_mem,
                        message=f"contradiction unresolved between {a_mem} and "
                        f"{b_mem}: {e.get('reason') or 'both are shown'}",
                    )
                )
        kept: list[Candidate] = []
        for c in ranked:
            if c.rev in overridden:
                dropped.append({"rev": str(c.rev), "reason": f"overridden by {overridden[c.rev]}"})
            else:
                kept.append(c)
        return kept, cautions, dropped

    # ---- stage 7: collapse and ledger ---------------------------------------------------------

    def collapse(self, ranked: list[Candidate]) -> tuple[list[Candidate], list[dict[str, Any]]]:
        kept: list[Candidate] = []
        dropped: list[dict[str, Any]] = []
        for c in ranked:  # already best first
            assert c.revision is not None
            twin = next(
                (
                    k
                    for k in kept
                    if k.revision is not None
                    and k.revision.text != ""
                    and similarity(k.revision.text, c.revision.text) >= DUPLICATE_TEXT
                ),
                None,
            )
            if twin is None:
                kept.append(c)
            else:
                dropped.append({"rev": str(c.rev), "reason": f"near-duplicate of {twin.rev}"})
        return kept, dropped

    async def _ledger(
        self, caller: Caller, session: str | None, kept: list[Candidate], *, redeliver: bool
    ) -> tuple[list[Candidate], list[Notice], list[dict[str, Any]]]:
        if not session:
            return kept, [], []
        rows = await self.store.select(
            "delivery", {"owner": caller.owner, "session": session}, limit=5000
        )
        deliveries = [d for d in rows if isinstance(d, Delivery)]
        delivered_revs = {d.rev for d in deliveries}
        notices = await self._notices(deliveries)
        dropped: list[dict[str, Any]] = []
        if not redeliver:
            fresh: list[Candidate] = []
            for c in kept:
                if c.rev in delivered_revs:
                    dropped.append({"rev": str(c.rev), "reason": "already delivered this session"})
                else:
                    fresh.append(c)
            kept = fresh
        return kept, notices, dropped

    async def _notices(self, deliveries: list[Delivery]) -> list[Notice]:
        pending = [d for d in deliveries if not d.notified]
        if not pending:
            return []
        mems = await self.store.get_many(list({d.memory for d in pending}))
        notices: list[Notice] = []
        writes: list[Update] = []
        for d in pending:
            m = mems.get(d.memory)
            if not isinstance(m, Memory) or d.id is None:
                continue
            if m.current != d.rev or m.status != "active":
                if m.status == "retracted":
                    notices.append(
                        Notice(
                            kind="retracted",
                            memory=d.memory,
                            message=f"{d.memory} (delivered earlier) was retracted: do not use it",
                        )
                    )
                else:
                    notices.append(
                        Notice(
                            kind="superseded",
                            memory=d.memory,
                            replaced_by=m.current if m.current != d.rev else None,
                            message=f"{d.memory} (delivered earlier) has changed: re-check it",
                        )
                    )
                writes.append(Update(d.id, {"notified": True}))
        if writes:
            await self.store.apply(writes)
        return notices

    # ---- item building, delivery bookkeeping -----------------------------------------------------

    def _item(self, c: Candidate, *, with_lane_why: bool) -> RecallItem:
        rev, a = c.revision, c.assessment
        assert rev is not None and a is not None and c.doc is not None and c.state is not None
        why = list(c.why)
        if with_lane_why:
            why += [f"{lane} {score:.2f}" for lane, score in sorted(c.lanes.items())]
        if a.anchor:
            why.append(f"anchored at {a.anchor}" + (f" (+{a.distance})" if a.distance else ""))
        flags = list(a.flags)
        if c.state.stale:
            flags.append("stale")
        if rev.confidence < 0.6:
            flags.append("low_confidence")
        return RecallItem(
            memory=rev.memory,
            revision=c.rev,
            kind=c.doc.kind,
            text=rev.text,
            strength=rev.strength if isinstance(rev, DirectiveRev) else None,
            polarity=rev.polarity if isinstance(rev, DirectiveRev) else None,
            score=round(c.score, 4),
            relevance=round(c.relevance, 4),
            anchor=a.anchor,
            anchor_distance=a.distance,
            why=why,
            caveats=list(a.caveats),
            checks=list(a.outcome.checks),
            flags=flags,
            confidence=rev.confidence,
            valid_from=c.state.valid_from,
            detail={**_detail(rev), "seq": rev.seq},
        )

    async def _record(
        self,
        caller: Caller,
        pack: RecallPack,
        request: RecallRequest,
        path: str,
        dropped: list[dict[str, Any]],
        ctx: Resolved,
        scoring: dict[str, Any] | None = None,
    ) -> None:
        now = self.now()
        items = pack.items()
        if scoring and scoring.get("candidates"):
            shown = {str(i.revision) for i in items}
            for row in scoring["candidates"]:
                row["shown"] = row["rev"] in shown
        trace_id = RecordId("recall_trace", new_id())
        writes: list[Any] = [
            Create.of(
                trace_id,
                RecallTrace(
                    owner=caller.owner,
                    actor=caller.actor_id,
                    path=path,
                    session=request.context.session,
                    request=request.model_dump(mode="json"),
                    items=[i.model_dump(mode="json", exclude={"detail"}) for i in items],
                    dropped=dropped,
                    scoring=scoring or None,
                    created_at=now,
                ),
            )
        ]
        usages = await self.store.get_many([RecordId("usage", i.memory.id) for i in items])
        for i in items:
            uid = RecordId("usage", i.memory.id)
            prev = usages.get(uid)
            recalls = (prev.recalls if isinstance(prev, Usage) else 0) + 1
            writes.append(
                Upsert.of(
                    uid,
                    Usage(
                        memory=i.memory,
                        recalls=recalls,
                        last_recalled_at=now,
                        helpful=prev.helpful if isinstance(prev, Usage) else 0,
                        unhelpful=prev.unhelpful if isinstance(prev, Usage) else 0,
                    ),
                )
            )
            if request.context.session:
                key = f"{caller.owner}|{request.context.session}|{i.revision}"
                writes.append(
                    Upsert.of(
                        RecordId("delivery", hashlib.sha256(key.encode()).hexdigest()),
                        Delivery(
                            owner=caller.owner,
                            session=request.context.session,
                            memory=i.memory,
                            rev=i.revision,
                            delivered_at=now,
                        ),
                    )
                )
        await self.store.apply(writes)
        pack.trace = trace_id
        await self._prune_traces()

    async def _prune_traces(self) -> None:
        """Delete traces older than the retention that no feedback cites; at most once an hour.
        A failure costs nothing."""
        days = self.cfg.trace_retention_days
        now = self.now()
        if days <= 0 or (self._last_prune and now - self._last_prune < timedelta(hours=1)):
            return
        self._last_prune = now
        try:
            stale = await self.store.uncited_traces(now - timedelta(days=days), limit=1000)
            if stale:
                await self.store.apply([Delete(rid) for rid in stale])
        except Exception:
            log.warning("recall trace retention sweep failed", exc_info=True)

    def _affordances(self, pack: RecallPack) -> list[str]:
        out: list[str] = []
        for m in pack.more:
            out.append(f"{m.count} more {m.section.replace('_', ' ')} did not fit: {m.hint}")
        revised = [i for i in pack.items() if i.detail.get("seq", 1) > 1]
        if revised:
            out.append(f"{len(revised)} item(s) have earlier revisions: ask supersession_chain")
        checks = sorted({c for i in pack.items() for c in i.checks})
        if checks:
            out.append("verify before acting: " + "; ".join(checks))
        if pack.items():
            out.append("get(<memory id>, expand=...) shows evidence, links and history")
        return out

    # ---- public operations -----------------------------------------------------------------

    async def recall(self, caller: Caller, request: RecallRequest) -> RecallPack:
        """Path B: what is relevant to this message, in this context. Silent when nothing
        clears the relevance threshold."""
        ctx = await self.resolve_context(request.context)
        text = " ".join([request.query or "", *request.hints]).strip()
        dropped: list[dict[str, Any]] = []
        if not text:
            return await self._finish(caller, request, "recall", [], [], ctx, dropped)
        cands = await self.query_candidates(caller, text, ctx, request.kinds)
        visible = await self.hydrate(cands, ctx, query=text)
        ranked = await self.rank(visible, query=text, ranked_by_text=True)
        relevant: list[Candidate] = []
        for c in ranked:
            if c.relevance >= self.cfg.relevance_threshold or (
                c.assessment is not None and c.assessment.outcome.triggered
            ):
                relevant.append(c)
            else:
                dropped.append(
                    {"rev": str(c.rev), "reason": f"below threshold ({c.relevance:.2f})"}
                )
        relevant += await self.triggered_extras(caller, request, relevant)
        return await self._finish(caller, request, "recall", relevant, [], ctx, dropped)

    async def triggered_extras(
        self, caller: Caller, request: RecallRequest, have: list[Candidate]
    ) -> list[Candidate]:
        """The trigger lane of input-driven recall: memories whose `trigger` qualifier is
        satisfied by the conditions in `request.context` are delivered whatever the message says
        (a briefing already does this). Needs signals: a trigger is a condition on observed
        state."""
        if not request.context.signals:
            return []
        _, keep = await self.briefing_candidates(caller, request)
        known = {c.rev for c in have}
        extra = [
            c
            for c in keep
            if c.assessment is not None and c.assessment.outcome.triggered and c.rev not in known
        ]
        return await self.rank(extra, query=None, ranked_by_text=False) if extra else []

    async def briefing_candidates(
        self, caller: Caller, request: RecallRequest, topics: str = ""
    ) -> tuple[Resolved, list[Candidate]]:
        """The memories a briefing would carry before ranking and packing (see `in_scope`)."""
        ctx = await self.resolve_context(request.context)
        owners = caller.visible_owners()
        docs = await self.store.docs(
            owners,
            kinds=request.kinds,
            anchors=list(ctx.chain.distance),
            unanchored=True,
            limit=5000,
        )
        cands = {d.rev: Candidate(d.rev, doc=d) for d in docs}
        visible = await self.hydrate(cands, ctx)
        keep: list[Candidate] = []
        for c in visible:
            assert c.doc is not None and c.revision is not None and c.assessment is not None
            a = c.assessment
            anchored_here = a.distance is not None
            if not in_scope(c.revision, a, ctx, topics):
                continue
            if (
                c.doc.kind == "directive"
                or a.outcome.triggered
                or (
                    anchored_here
                    and not (isinstance(c.revision, TaskRev) and c.revision.state != "open")
                )
                or (
                    isinstance(c.revision, TaskRev)
                    and c.revision.state == "open"
                    and (c.revision.due is not None and c.revision.due <= self.now())
                )
            ):
                keep.append(c)
        return ctx, keep

    async def briefing(
        self, caller: Caller, request: RecallRequest, topics: str = ""
    ) -> RecallPack:
        """Path C: what applies here, with no question. Complete for directives that are in
        scope: unanchored ones that are about something else are left out."""
        ctx, keep = await self.briefing_candidates(caller, request, topics)
        ranked = await self.rank(keep, query=None, ranked_by_text=False)
        return await self._finish(caller, request, "briefing", ranked, [], ctx, [])

    async def check(self, caller: Caller, intent: str, context: RecallContext) -> CheckResult:
        """Path E: about to do something with side effects. Applicable directives,
        prerequisites and lessons for this intent, plus every applicable `must`."""
        request = RecallRequest(
            context=context,
            query=intent,
            kinds=["directive", "lesson", "procedure", "decision"],
            profile="compact",
            redeliver=True,  # an explicit check always answers in full, whatever the ledger says
            gate=False,  # an action is never a pleasantry
        )
        pack = await self.recall(caller, request)
        briefing = await self.briefing(
            caller,
            RecallRequest(context=context, kinds=["directive"], profile="compact", redeliver=True),
            topics=intent,
        )
        relevant = pack.items()  # what the intent itself matched, before the standing musts
        has_checks = any(i.checks for i in relevant)
        known = {x.memory for x in relevant}
        musts = [i for i in briefing.must_follow if i.memory not in known]
        pack.must_follow = [*pack.must_follow, *musts]
        pack.path = "check"
        pack.silent = not pack.items()
        pack.rendered = render_pack(pack, request.profile)  # the musts added above are in it
        pack.tokens = estimate_tokens(pack.rendered) if pack.rendered else 0
        pack.affordances = self._affordances(pack)
        # the verdict reflects what matches this intent; standing musts are listed, not judged
        verdict: Literal["clear", "caution"] = "caution" if relevant or has_checks else "clear"
        summary = (
            f"{len(relevant)} item(s) match this action; review them before acting"
            if relevant
            else "nothing in memory is specific to this action here"
            + (f" ({len(musts)} standing must-follow rule(s) listed)" if musts else "")
        )
        return CheckResult(verdict=verdict, summary=summary, pack=pack)

    async def _finish(
        self,
        caller: Caller,
        request: RecallRequest,
        path: Literal["recall", "briefing", "check"],
        ranked: list[Candidate],
        extra_cautions: list[Caution],
        ctx: Resolved,
        dropped: list[dict[str, Any]],
        scoring: dict[str, Any] | None = None,
    ) -> RecallPack:
        kept, cautions, d1 = await self.precedence(ranked)
        kept, d2 = self.collapse(kept)
        kept, notices, d3 = await self._ledger(
            caller, request.context.session, kept, redeliver=request.redeliver
        )
        budget = request.budget_tokens or (
            self.cfg.briefing_budget_tokens
            if path == "briefing"
            else self.cfg.default_budget_tokens
        )
        items = [self._item(c, with_lane_why=path != "briefing") for c in kept]
        shown = {i.memory for i in items}
        cautions = [c for c in cautions if c.memory in shown or c.memory is None]
        cautions += extra_cautions
        cautions += [
            Caution(
                memory=i.memory,
                message=f"{i.memory} is stale: a source it was derived from was retracted",
            )
            for i in items
            if "stale" in i.flags
        ]
        pack = build_pack(
            path, items, budget_tokens=budget, profile=request.profile, cautions=cautions
        )
        pack.notices = notices
        pack.unresolved = ctx.unresolved
        pack.silent = not pack.items() and not notices
        pack.affordances = self._affordances(pack)
        pack.rendered = render_pack(pack, request.profile)
        pack.tokens = estimate_tokens(pack.rendered) if pack.rendered else 0
        await self._record(caller, pack, request, path, [*dropped, *d1, *d2, *d3], ctx, scoring)
        return pack


__all__ = ["RecallService", "assess", "estimate_tokens"]
