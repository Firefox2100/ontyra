"""Concept management and the cached reasoner snapshot (plans/11).

Prototype scope: concepts are created and related directly by a caller (curator); versioned
concept revisions and ontology-change proposals come later. The snapshot cache assumes a
single daemon process writes the ontology (it is invalidated by this service's own writes).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime

from ontyra.models import RecordId, new_id
from ontyra.models.ontology import (
    Concept,
    ConceptDefinition,
    ConceptHistory,
    ConceptRev,
    ConceptVersion,
    ConceptView,
    EdgeEvent,
    Related,
    RelationName,
    RelationRequest,
    RelationView,
    concept_slug,
)
from ontyra.ports import Create, Relate, Store, Update, Write
from ontyra.services import errors
from ontyra.services.context import Caller
from ontyra.services.reasoner import Ontology

MAX_CONCEPTS = 20_000


class OntologyService:
    def __init__(self, store: Store, now: Callable[[], datetime]) -> None:
        self.store = store
        self.now = now
        self._snapshot: Ontology | None = None
        # One writer at a time: a change plans from the snapshot and then commits one batch, so
        # two changes must not interleave (the daemon is one process; plans/23).
        self.lock = asyncio.Lock()

    # ---- snapshot -------------------------------------------------------------

    async def ontology(self) -> Ontology:
        """The current reasoner snapshot (rebuilt after any ontology write)."""
        if self._snapshot is None:
            rows = await self.store.select("concept", limit=MAX_CONCEPTS)
            concepts = {c.id: c for c in rows if isinstance(c, Concept) and c.id is not None}
            edges = await self.store.edges("related", limit=MAX_CONCEPTS * 10)
            self._snapshot = Ontology(
                concepts,
                [(e["in"], e["relation"], e["out"]) for e in edges if not e.get("ended_at")],
            )
        return self._snapshot

    def invalidate(self) -> None:
        self._snapshot = None

    # ---- lookup ----------------------------------------------------------------

    async def resolve(self, token: str | RecordId) -> RecordId:
        """A concept id from an id (`concept:x`), a slug or a label/alias. Raises if unknown."""
        onto = await self.ontology()
        if isinstance(token, RecordId):
            if token in onto.concepts:
                return token
            raise errors.NotFoundError(f"unknown concept {token}")
        text = token.strip()
        if text.startswith("concept:"):
            cid = RecordId("concept", text.partition(":")[2])
            if cid in onto.concepts:
                return cid
        slug = RecordId("concept", concept_slug(text)) if text else None
        if slug is not None and slug in onto.concepts:
            return slug
        found = onto.find(text)
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            raise errors.InvalidRequestError(
                f"'{token}' is ambiguous: {', '.join(str(f) for f in found)}"
            )
        raise errors.NotFoundError(f"unknown concept '{token}'")

    async def get(self, caller: Caller, concept: RecordId) -> ConceptView:
        onto = await self.ontology()
        c = onto.concepts.get(concept)
        if c is None:
            raise errors.NotFoundError(f"unknown concept {concept}")
        return await self._view(caller, c, onto)

    async def lookup(self, caller: Caller, term: str) -> list[ConceptView]:
        """Concepts whose label or alias matches `term` (the agent never guesses ids). A
        deprecated concept is not offered: a split one stands for what it became."""
        onto = await self.ontology()
        ids = onto.find(term)
        if not ids:
            slug = RecordId("concept", concept_slug(term)) if term.strip() else None
            ids = [slug] if slug is not None and slug in onto.concepts else []
        offered: list[RecordId] = []
        for i in ids:
            how, targets = onto.live(i)
            offered += targets if how in ("redirect", "split") else [i] if how == "ok" else []
        offered = list(dict.fromkeys(offered))
        return [await self._view(caller, onto.concepts[i], onto) for i in offered]

    async def list(
        self,
        caller: Caller,
        *,
        scheme: str | None = None,
        limit: int = 200,
        include_deprecated: bool = False,
    ) -> list[ConceptView]:
        onto = await self.ontology()
        concepts = sorted(onto.concepts.values(), key=lambda c: c.label.casefold())
        picked = [
            c
            for c in concepts
            if (scheme is None or c.scheme == scheme)
            and (include_deprecated or c.status == "active")
        ][:limit]
        return [await self._view(caller, c, onto) for c in picked]

    async def use(self, token: RecordId) -> tuple[RecordId | None, str | None]:
        """A concept as a *writer* may use it: the live concept (a one-to-one replacement is
        followed) and a note saying so, or `None` and the reason it cannot be used."""
        onto = await self.ontology()
        if token not in onto.concepts:
            return None, f"{token} does not exist"
        how, targets = onto.live(token)
        if how == "ok":
            return token, None
        if how == "redirect":
            return targets[0], f"{token} is deprecated; {targets[0]} replaces it"
        if how == "split":
            return None, (f"{token} was split: choose one of {', '.join(str(t) for t in targets)}")
        return None, f"{token} is deprecated and nothing replaces it"

    async def _view(self, caller: Caller, c: Concept, onto: Ontology) -> ConceptView:
        assert c.id is not None
        about = await self.store.docs(caller.visible_owners(), about=[c.id], limit=1000)
        return ConceptView(
            id=c.id,
            label=c.label,
            scheme=c.scheme,
            aliases=c.aliases,
            definition=c.definition,
            value_type=c.value_type,
            version_scheme=c.version_scheme,
            dimension=c.dimension,
            status=c.status,
            replaced_by=c.replaced_by,
            successors=onto.successors(c.id),
            deprecation_reason=c.reason,
            relations=[
                RelationView(
                    direction=d,  # type: ignore[arg-type]
                    relation=rel,
                    other=other,
                    label=onto.concepts[other].label if other in onto.concepts else str(other),
                )
                for d, rel, other in onto.neighbours(c.id)
            ],
            memories=len(about),
        )

    # ---- writes -----------------------------------------------------------------

    async def define_writes(
        self, caller: Caller, request: ConceptDefinition, *, change: RecordId | None = None
    ) -> tuple[RecordId, list[Write]]:
        """The writes that create a concept: the concept, its first version and its parents."""
        label = request.label.strip()
        if not label:
            raise errors.InvalidRequestError("a concept needs a label")
        try:
            cid = RecordId("concept", concept_slug(label))
        except ValueError as exc:
            raise errors.InvalidRequestError(str(exc)) from exc
        onto = await self.ontology()
        if cid in onto.concepts:
            raise errors.ConflictError(f"concept {cid} already exists")
        if request.value_type == "version" and request.version_scheme is None:
            raise errors.InvalidRequestError("a version concept needs a version_scheme")
        if request.value_type == "enum" and not request.values:
            raise errors.InvalidRequestError("an enum concept needs its values")
        parents = [await self.resolve(p) for p in request.is_a]
        now = self.now()
        concept = Concept(
            label=label,
            scheme=request.scheme,
            aliases=[a for a in dict.fromkeys(request.aliases) if a.strip()],
            definition=request.definition,
            value_type=request.value_type,
            version_scheme=request.version_scheme,
            dimension=request.dimension,
            values=request.values,
            created_at=now,
            version=1,
        )
        writes: list[Write] = [
            Create.of(cid, concept),
            self.version_write(cid, concept, 1, caller, change),
        ]
        writes += [
            Relate("related", cid, parent, {"relation": "is_a", "by": caller.actor_id, "at": now})
            for parent in parents
        ]
        return cid, writes

    def version_write(
        self, cid: RecordId, c: Concept, seq: int, caller: Caller | None, change: RecordId | None
    ) -> Write:
        """The immutable version row for the concept as `c` now is."""
        return Create.of(
            RecordId("concept_rev", new_id()),
            ConceptRev(
                concept=cid,
                seq=seq,
                label=c.label,
                aliases=list(c.aliases),
                definition=c.definition,
                status=c.status,
                replaced_by=c.replaced_by,
                reason=c.reason,
                change=change,
                by=caller.actor_id if caller else None,
                at=self.now(),
            ),
        )

    async def define(self, caller: Caller, request: ConceptDefinition) -> ConceptView:
        async with self.lock:
            cid, writes = await self.define_writes(caller, request)
            await self.store.apply(writes)
            self.invalidate()
        return await self.get(caller, cid)

    async def edge_ids(self, source: RecordId, relation: str, target: RecordId) -> list[RecordId]:
        """The live edges `source -relation-> target` (ended ones are history)."""
        rows = await self.store.edges("related", source=source, target=target)
        return [r["id"] for r in rows if r.get("relation") == relation and not r.get("ended_at")]

    async def canonicalise_index(self, caller: Caller, limit: int = 1000) -> int:
        """The opportunistic update (plans/23): index rows (`search_doc.about`, derived data) that
        still carry a concept replaced one-to-one are rewritten to the live concept. Recall is
        correct without it (the reasoner follows replacements); this keeps the index tidy and
        the lane fast. Memory revisions are never touched. Returns the rows changed."""
        onto = await self.ontology()
        moved = {
            cid: targets[0]
            for cid in onto.concepts
            for how, targets in [onto.live(cid)]
            if how == "redirect"
        }
        if not moved:
            return 0
        docs = await self.store.docs(caller.visible_owners(), about=list(moved), limit=limit)
        writes: list[Write] = []
        for d in docs:
            if d.id is None:
                continue
            about = list(dict.fromkeys(moved.get(c, c) for c in d.about))
            if about != d.about:
                writes.append(Update(d.id, {"about": about}))
        await self.store.apply(writes)
        return len(writes)

    # ---- history (investigation) -------------------------------------------------

    async def history(self, concept: RecordId) -> ConceptHistory:
        """Versions of a concept, the relations it has had, and the changes that touched it.
        Ordinary reads never show these."""
        onto = await self.ontology()
        c = onto.concepts.get(concept)
        if c is None:
            raise errors.NotFoundError(f"unknown concept {concept}")
        revs = sorted(
            (
                r
                for r in await self.store.select("concept_rev", {"concept": concept}, limit=1000)
                if isinstance(r, ConceptRev)
            ),
            key=lambda r: r.seq,
        )
        out = await self.store.edges("related", source=concept)
        inn = await self.store.edges("related", target=concept)
        events = [
            EdgeEvent(
                relation=e["relation"],
                direction="out",
                other=e["out"],
                added_at=e["at"],
                ended_at=e.get("ended_at"),
                ended_by_change=e.get("ended_by_change"),
            )
            for e in out
        ] + [
            EdgeEvent(
                relation=e["relation"],
                direction="in",
                other=e["in"],
                added_at=e["at"],
                ended_at=e.get("ended_at"),
                ended_by_change=e.get("ended_by_change"),
            )
            for e in inn
        ]
        changes = [
            ch.id
            for ch in await self.store.select(
                "ontology_change", limit=2000, order_by="created_at", descending=True
            )
            if ch.id is not None and concept in (getattr(ch, "touched", None) or [])
        ]
        return ConceptHistory(
            concept=concept,
            current=c.version,
            versions=[
                ConceptVersion(
                    seq=r.seq,
                    label=r.label,
                    aliases=r.aliases,
                    definition=r.definition,
                    status=r.status,
                    replaced_by=r.replaced_by,
                    reason=r.reason,
                    change=r.change,
                    by=r.by,
                    at=r.at,
                )
                for r in revs
            ],
            relations=sorted(events, key=lambda e: e.added_at),
            changes=changes,
        )

    async def relate(self, caller: Caller, request: RelationRequest) -> ConceptView:
        src = await self.resolve(request.source)
        dst = await self.resolve(request.target)
        if src == dst:
            raise errors.InvalidRequestError("a concept cannot be related to itself")
        onto = await self.ontology()
        rel: RelationName = request.relation
        existing = {(d, r, o) for d, r, o in onto.neighbours(src)}
        if ("out", rel, dst) in existing:
            return await self.get(caller, src)
        if rel in ("is_a", "part_of", "instance_of") and src in onto.ancestors(dst, (rel,)):
            raise errors.ConflictError(f"{dst} is already a kind of {src}: that would be a cycle")
        await self.store.apply(
            [
                Relate(
                    "related",
                    src,
                    dst,
                    Related(relation=rel, by=caller.actor_id, at=self.now()).model_dump(
                        mode="python", exclude={"id"}
                    ),
                )
            ]
        )
        self.invalidate()
        return await self.get(caller, src)

    async def deprecate(
        self, caller: Caller, concept: RecordId, replaced_by: RecordId | None = None
    ) -> ConceptView:
        onto = await self.ontology()
        if concept not in onto.concepts:
            raise errors.NotFoundError(f"unknown concept {concept}")
        if replaced_by is not None and replaced_by not in onto.concepts:
            raise errors.NotFoundError(f"unknown concept {replaced_by}")
        patch: dict[str, object] = {"status": "deprecated", "updated_at": self.now()}
        if replaced_by is not None:
            patch["replaced_by"] = replaced_by
        await self.store.apply([Update(concept, patch)])
        self.invalidate()
        return await self.get(caller, concept)

    async def count_memories(self, caller: Caller, concept: RecordId) -> int:
        return len(await self.store.docs(caller.visible_owners(), about=[concept], limit=10_000))
