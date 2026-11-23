"""Path A: exploration verbs (search with facets, get with expansions, neighbors, structural
questions, overview). Composed from the same recall machinery, so visibility and qualifier
rules are identical everywhere (plans/03).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import datetime
from typing import Any

from ontyra.models import RecordId
from ontyra.models.core import Episode, Memory, RevState
from ontyra.models.kinds import RevisionBase
from ontyra.models.recall import (
    AskRequest,
    AskResult,
    Overview,
    RecallContext,
    SearchHit,
    SearchRequest,
    SearchResult,
)
from ontyra.models.registry import registry
from ontyra.models.search import RecallTrace
from ontyra.models.sources import SourceCheck, source_check_id
from ontyra.ports import Store
from ontyra.services import errors
from ontyra.services.context import Caller
from ontyra.services.memory import MemoryService
from ontyra.services.ontology import OntologyService
from ontyra.services.recall import Candidate, RecallService, assess
from ontyra.services.text import coverage
from ontyra.services.validation import rev_state_id
from ontyra.services.views import MemoryDetail, Neighbors

VERBS = [
    "recall(query, context) - what is relevant to this message here",
    "briefing(context) - everything that applies at this location/environment",
    "search(query, filters) - find memories; facets show how to narrow",
    "get(memory, expand=history|links|evidence|applies) - one memory in depth",
    "neighbors(memory|concept) - what is connected",
    "ask(question) - applies_here, supersession_chain, absence, conflicts, changes_since, "
    "as_of, prerequisites_of, explain",
    "check(intent) - before an action with side effects",
    "concepts(term) - resolve a term to ontology concepts",
]


class ExplorationService:
    def __init__(
        self,
        store: Store,
        memory: MemoryService,
        ontology: OntologyService,
        recall: RecallService,
        now: Callable[[], datetime],
    ) -> None:
        self.store = store
        self.memory = memory
        self.ontology = ontology
        self.recall = recall
        self.now = now

    # ---- search ------------------------------------------------------------------------

    async def search(self, caller: Caller, request: SearchRequest) -> SearchResult:
        ctx = await self.recall.resolve_context(request.context)
        cands = await self.recall.query_candidates(caller, request.query, ctx, request.kinds)
        if request.concept:
            cid = await self.ontology.resolve(request.concept)
            allowed = set(ctx.ontology.expand(cid))
            docs = await self.store.get_many([_doc(r) for r in cands])
            cands = {
                r: c
                for r, c in cands.items()
                if (d := docs.get(_doc(r))) is not None and allowed & set(getattr(d, "about", []))
            }
        visible = await self.recall.hydrate(cands, ctx, only_applicable=False, query=request.query)
        ranked = await self.recall.rank(visible, query=request.query, ranked_by_text=True)
        facets: dict[str, Counter[str]] = {
            "kind": Counter(),
            "anchor": Counter(),
            "concept": Counter(),
            "applies": Counter(),
        }
        hits: list[SearchHit] = []
        for c in ranked:
            assert c.doc is not None and c.revision is not None and c.assessment is not None
            applies = _applies(c)
            facets["kind"][c.doc.kind] += 1
            facets["applies"][applies] += 1
            for a in c.revision.anchors or []:
                facets["anchor"][a.uri] += 1
            if not c.revision.anchors:
                facets["anchor"]["(everywhere)"] += 1
            for cid in c.revision.about:
                label = (
                    ctx.ontology.concepts[cid].label if cid in ctx.ontology.concepts else str(cid)
                )
                facets["concept"][label] += 1
            if len(hits) < request.limit:
                hits.append(
                    SearchHit(
                        memory=c.revision.memory,
                        revision=c.rev,
                        kind=c.doc.kind,
                        text=c.revision.text,
                        score=round(c.score, 4),
                        why=[*c.why, *(f"{k} {v:.2f}" for k, v in sorted(c.lanes.items()))],
                        applies=applies,  # type: ignore[arg-type]
                    )
                )
        out = SearchResult(
            query=request.query,
            hits=hits,
            facets={k: dict(v.most_common(10)) for k, v in facets.items() if v},
        )
        if len(ranked) > len(hits):
            out.affordances.append(f"{len(ranked) - len(hits)} more matches: narrow by a facet")
        if hits:
            out.affordances.append("get(<memory id>, expand=...) for detail")
        return out

    # ---- get ---------------------------------------------------------------------------

    async def get(
        self, caller: Caller, memory: RecordId, expand: list[str], context: RecallContext
    ) -> MemoryDetail:
        view = await self.memory.get_memory(caller, memory)
        detail = MemoryDetail(memory=view)
        if "history" in expand:
            detail.history = await self.memory.history(caller, memory)
        if "links" in expand:
            detail.links = await self._links(memory)
        if "evidence" in expand:
            detail.evidence = await self._evidence(view.revision, caller)
        if "applies" in expand:
            detail.applies = await self._applies(view.revision, context)
        return detail

    async def _links(self, memory: RecordId) -> list[dict[str, Any]]:
        """Links of a memory across all its revisions (supersession edges are per revision)."""
        out: list[dict[str, Any]] = []
        for edge in ("supersedes", "contradicts", "overrides", "refines", "promoted_to"):
            for e in await self.store.memory_edges(edge, [memory]):
                outgoing = e["in_memory"] == memory
                other = e["out"] if outgoing else e["in"]
                out.append(
                    {
                        "relation": edge,
                        "direction": "out" if outgoing else "in",
                        "other": str(other),
                        "other_memory": str(e["out_memory"] if outgoing else e["in_memory"]),
                        "reason": e.get("reason"),
                    }
                )
        return out

    async def _evidence(self, rev: RecordId, caller: Caller) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        visible = set(caller.visible_owners())
        for e in await self.store.edges("derived_from", source=rev):
            src = await self.store.get(e["out"])
            item: dict[str, Any] = {"source": str(e["out"])}
            if isinstance(src, Episode):
                if src.owner not in visible:  # a global memory may cite someone's private prompt
                    out.append(item)
                    continue
                item |= {"role": src.role, "session": src.session, "excerpt": src.content[:300]}
            elif isinstance(src, RevisionBase):
                item |= {"text": src.text}
            out.append(item)
        revision = await self.store.get(rev)
        if isinstance(revision, RevisionBase) and revision.sources:
            checks = await self.store.get_many([source_check_id(s.uri) for s in revision.sources])
            for s in revision.sources:  # cited, not stored: the daemon never reads them
                found = checks.get(source_check_id(s.uri))
                out.append(
                    {"source": s.uri}
                    | s.model_dump(mode="json", exclude={"uri"}, exclude_none=True)
                    | (
                        {"checked": found.state, "checked_at": found.checked_at.isoformat()}
                        if isinstance(found, SourceCheck)
                        else {}
                    )
                )
        return out

    async def _applies(self, rev_id: RecordId, context: RecallContext) -> dict[str, Any]:
        rev = await self.store.get(rev_id)
        if not isinstance(rev, RevisionBase):
            raise errors.NotFoundError(f"{rev_id} does not exist")
        ctx = await self.recall.resolve_context(context)
        a = assess(rev, ctx)
        return {
            "applies": a.include,
            "anchor": a.anchor,
            "distance": a.distance,
            "caveats": a.caveats,
            "checks": a.outcome.checks,
            "reasons": a.reasons,
            "flags": a.flags,
            "anchors": [x.uri for x in rev.anchors],
            "qualifiers": [q.model_dump(mode="json") for q in rev.qualifiers],
            "location_given": ctx.has_location,
        }

    # ---- neighbors ------------------------------------------------------------------------

    async def neighbors(self, caller: Caller, target: RecordId) -> Neighbors:
        onto = await self.ontology.ontology()
        out = Neighbors(center=target)
        if target.table == "concept":
            view = await self.ontology.get(caller, target)
            out.concepts = [r.model_dump(mode="json") for r in view.relations]
            docs = await self.store.docs(caller.visible_owners(), about=[target], limit=50)
            out.memories = [
                {"memory": str(d.memory), "kind": d.kind, "text": d.text[:120]} for d in docs
            ]
            return out
        view = await self.memory.get_memory(caller, target)
        rev = await self.store.get(view.revision)
        if not isinstance(rev, RevisionBase):
            return out
        out.concepts = [
            {"concept": str(c), "label": onto.concepts[c].label if c in onto.concepts else str(c)}
            for c in rev.about
        ]
        out.links = await self._links(target)
        if rev.about:
            related = await self.store.docs(
                caller.visible_owners(), about=list(rev.about), limit=50
            )
            out.memories = [
                {
                    "memory": str(d.memory),
                    "kind": d.kind,
                    "text": d.text[:120],
                    "shared_concepts": [
                        onto.concepts[c].label
                        for c in d.about
                        if c in rev.about and c in onto.concepts
                    ],
                }
                for d in related
                if d.memory != target
            ]
        return out

    # ---- ask ------------------------------------------------------------------------------

    async def ask(self, caller: Caller, request: AskRequest) -> AskResult:
        q = request.question
        if q == "applies_here":
            mem = self._need_memory(request)
            view = await self.memory.get_memory(caller, mem)
            ans = await self._applies(view.revision, request.context)
            return AskResult(
                question=q,
                answer=ans,
                summary=(
                    "applies here" + (f" (anchor {ans['anchor']})" if ans["anchor"] else "")
                    if ans["applies"]
                    else "does not apply here: " + "; ".join(ans["reasons"])
                ),
            )
        if q == "supersession_chain":
            hist = await self.memory.history(caller, self._need_memory(request))
            return AskResult(
                question=q,
                answer=hist.model_dump(mode="json"),
                summary=f"{len(hist.revisions)} revision(s); current status {hist.status}",
            )
        if q == "absence":
            topic = request.topic or ""
            res = await self.search(
                caller, SearchRequest(query=topic, context=request.context, limit=25)
            )
            ctx = await self.recall.resolve_context(request.context)
            envelope = {
                "topic": topic,
                "searched": "committed, active, valid-now memories visible to the caller",
                "anchors": sorted(ctx.chain.distance),
                "observed": [
                    ctx.ontology.concepts[o.concept].label
                    for o in ctx.observations
                    if o.concept in ctx.ontology.concepts
                ],
                "as_of": self.now().isoformat(),
            }
            related = [h for h in res.hits if h.applies != "no"][:5]
            return AskResult(
                question=q,
                answer={
                    "found": [h.model_dump(mode="json") for h in related],
                    "envelope": envelope,
                },
                summary=(
                    f"{len(related)} possibly related memory(ies)"
                    if related
                    else f"no live memory about '{topic}' under these anchors and qualifiers"
                ),
            )
        if q == "conflicts":
            return await self._conflicts(caller, request)
        if q == "changes_since":
            return await self._changes(caller, request)
        if q == "as_of":
            return await self._as_of(caller, request)
        if q == "prerequisites_of":
            mem = self._need_memory(request)
            view = await self.memory.get_memory(caller, mem)
            rev = await self.store.get(view.revision)
            ctx = await self.recall.resolve_context(request.context)
            a = assess(rev, ctx) if isinstance(rev, RevisionBase) else None
            pre = [
                q2.model_dump(mode="json")
                for q2 in getattr(rev, "qualifiers", [])
                if q2.role == "prerequisite"
            ]
            pre_text = list(getattr(rev, "preconditions", []))
            return AskResult(
                question=q,
                answer={
                    "prerequisites": pre,
                    "preconditions": pre_text,
                    "checks": a.outcome.checks if a else [],
                },
                summary=f"{len(pre) + len(pre_text)} prerequisite(s)",
            )
        if q == "explain":
            return await self._explain(caller, request)
        raise errors.InvalidRequestError(f"unknown question {q}")

    @staticmethod
    def _need_memory(request: AskRequest) -> RecordId:
        if request.memory is None:
            raise errors.InvalidRequestError(f"{request.question} needs a memory id")
        return request.memory

    async def _conflicts(self, caller: Caller, request: AskRequest) -> AskResult:
        ctx = await self.recall.resolve_context(request.context)
        docs = await self.store.docs(
            caller.visible_owners(), anchors=list(ctx.chain.distance), unanchored=True, limit=5000
        )
        revs = [d.rev for d in docs]
        pairs = [
            {"a": str(e["in"]), "b": str(e["out"]), "reason": e.get("reason")}
            for e in await self.store.edges("contradicts", sources=revs, targets=revs)
        ]
        return AskResult(
            question="conflicts",
            answer={"contradictions": pairs},
            summary=f"{len(pairs)} unresolved contradiction(s) among applicable memories",
        )

    async def _as_of(self, caller: Caller, request: AskRequest) -> AskResult:
        """The memories as they stood at `at`: each memory's revision that was in force then
        (committed, valid, not yet superseded, retracted or expired), optionally narrowed to a
        `topic` by word overlap. It reads the revision history, not the search index."""
        if request.at is None:
            raise errors.InvalidRequestError("as_of needs `at`")
        at = request.at
        owners = set(caller.visible_owners())
        ids = await self.store.revision_states_at(at, limit=2000)
        # a revision's status record shares its id part (`fact_rev:X` <-> `rev_state:X`)
        tables = [spec.table for spec in registry.kinds.values()]
        revs = await self.store.get_many([RecordId(t, i.id) for i in ids for t in tables])
        memories = await self.store.get_many(
            list({r.memory for r in revs.values() if isinstance(r, RevisionBase)})
        )
        rows: list[dict[str, Any]] = []
        for rid, rev in revs.items():
            if not isinstance(rev, RevisionBase):
                continue
            memory = memories.get(rev.memory)
            if not isinstance(memory, Memory) or memory.owner not in owners:
                continue
            expires = getattr(rev, "expires_at", None)
            if expires is not None and expires <= at:
                continue
            rows.append(
                {
                    "memory": str(memory.id),
                    "revision": str(rid),
                    "kind": memory.kind,
                    "seq": rev.seq,
                    "text": rev.text,
                    "current_now": memory.current == rid and memory.status == "active",
                }
            )
        if request.topic:
            scored = [(coverage(request.topic, r["text"]), r) for r in rows]
            rows = [r for c, r in sorted(scored, key=lambda x: -x[0]) if c > 0]
        rows = rows[:50]
        changed = sum(not r["current_now"] for r in rows)
        return AskResult(
            question="as_of",
            answer={"as_of": at.isoformat(), "memories": rows},
            summary=(
                f"{len(rows)} memory(ies) in force at {at.isoformat()}"
                + (
                    f"; {changed} of them have since been revised, superseded or retracted"
                    if changed
                    else ""
                )
            ),
        )

    async def _changes(self, caller: Caller, request: AskRequest) -> AskResult:
        if request.since is None:
            raise errors.InvalidRequestError("changes_since needs `since`")
        since = request.since
        docs = await self.store.docs(caller.visible_owners(), limit=5000)
        added = [
            {"memory": str(d.memory), "revision": str(d.rev), "text": d.text[:120]}
            for d in docs
            if d.created_at >= since
        ]
        gone: list[dict[str, Any]] = []
        for status in ("superseded", "retracted"):
            for m in await self.store.select(
                "memory",
                {"status": status},
                where_in={"owner": caller.visible_owners()},
                limit=2000,
            ):
                if not isinstance(m, Memory) or m.current is None:
                    continue
                st = await self.store.get(rev_state_id(m.current))
                when = (
                    st.retracted_at
                    if isinstance(st, RevState) and status == "retracted"
                    else (st.valid_to if isinstance(st, RevState) else None)
                )
                if when is not None and when >= since:
                    gone.append({"memory": str(m.id), "status": status, "at": when.isoformat()})
        return AskResult(
            question="changes_since",
            answer={"added_or_revised": added, "superseded_or_retracted": gone},
            summary=(
                f"{len(added)} added/revised, {len(gone)} superseded/retracted "
                f"since {since.isoformat()}"
            ),
        )

    async def _explain(self, caller: Caller, request: AskRequest) -> AskResult:
        if request.trace is None:
            raise errors.InvalidRequestError("explain needs a recall `trace` id")
        trace = await self.store.get(request.trace)
        if not isinstance(trace, RecallTrace) or trace.owner != caller.owner:
            raise errors.NotFoundError(f"{request.trace} does not exist")
        items = trace.items
        if request.memory is not None:
            items = [i for i in items if i.get("memory") == str(request.memory)]
        dropped = trace.dropped
        return AskResult(
            question="explain",
            answer={
                "path": trace.path,
                "delivered": items,
                "dropped": dropped,
                "request": trace.request,
            },
            summary=f"{len(items)} delivered, {len(dropped)} dropped",
        )

    # ---- overview ---------------------------------------------------------------------------

    async def overview(self, caller: Caller, context: RecallContext) -> Overview:
        ctx = await self.recall.resolve_context(context)
        docs = await self.store.docs(caller.visible_owners(), limit=10_000)
        counts = await self.store.doc_counts(caller.visible_owners())  # exact; `docs` is a sample
        concepts = Counter(
            ctx.ontology.concepts[c].label
            for d in docs
            for c in d.about
            if c in ctx.ontology.concepts
        )
        recent = [f"{d.kind}: {d.text[:80]}" for d in docs[:5]]
        return Overview(
            counts=dict(counts),
            concepts=[f"{label} ({n})" for label, n in concepts.most_common(10)],
            anchor_chain=sorted(ctx.chain.distance, key=lambda u: ctx.chain.distance[u]),
            qualifiers_observed=[
                ctx.ontology.concepts[o.concept].label
                + (f"={o.value}" if o.value is not None else "")
                for o in ctx.observations
                if o.concept in ctx.ontology.concepts
            ],
            recent=recent,
            verbs=VERBS,
        )


def _doc(rev: RecordId) -> RecordId:
    return RecordId("search_doc", rev.id)


def _applies(c: Candidate) -> str:
    a = c.assessment
    assert a is not None
    if not a.include:
        return "no"
    if (a.flags and "location_unknown" in a.flags) or a.outcome.demote:
        return "unknown"
    return "yes"
