"""Concept management and the cached reasoner snapshot (plans/11).

Prototype scope: concepts are created and related directly by a caller (curator); versioned
concept revisions and ontology-change proposals come later. The snapshot cache assumes a
single daemon process writes the ontology (it is invalidated by this service's own writes).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from ontyra.models import RecordId
from ontyra.models.ontology import (
    Concept,
    ConceptDefinition,
    ConceptView,
    Related,
    RelationName,
    RelationRequest,
    RelationView,
    concept_slug,
)
from ontyra.ports import Create, Relate, Store, Update
from ontyra.services import errors
from ontyra.services.context import Caller
from ontyra.services.reasoner import Ontology

MAX_CONCEPTS = 20_000


class OntologyService:
    def __init__(self, store: Store, now: Callable[[], datetime]) -> None:
        self.store = store
        self.now = now
        self._snapshot: Ontology | None = None

    # ---- snapshot -------------------------------------------------------------

    async def ontology(self) -> Ontology:
        """The current reasoner snapshot (rebuilt after any ontology write)."""
        if self._snapshot is None:
            rows = await self.store.select("concept", limit=MAX_CONCEPTS)
            concepts = {c.id: c for c in rows if isinstance(c, Concept) and c.id is not None}
            edges = await self.store.edges("related", limit=MAX_CONCEPTS * 10)
            self._snapshot = Ontology(concepts, [(e["in"], e["relation"], e["out"]) for e in edges])
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
        """Concepts whose label or alias matches `term` (the agent never guesses ids)."""
        onto = await self.ontology()
        ids = onto.find(term)
        if not ids:
            slug = RecordId("concept", concept_slug(term)) if term.strip() else None
            ids = [slug] if slug is not None and slug in onto.concepts else []
        # a deprecated concept is not listed next to the one that replaced it
        ids = [
            i
            for i in ids
            if not (onto.concepts[i].status == "deprecated" and onto.concepts[i].replaced_by in ids)
        ]
        return [await self._view(caller, onto.concepts[i], onto) for i in ids]

    async def list(
        self, caller: Caller, *, scheme: str | None = None, limit: int = 200
    ) -> list[ConceptView]:
        onto = await self.ontology()
        concepts = sorted(onto.concepts.values(), key=lambda c: c.label.casefold())
        picked = [c for c in concepts if scheme is None or c.scheme == scheme][:limit]
        return [await self._view(caller, c, onto) for c in picked]

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

    async def define(self, caller: Caller, request: ConceptDefinition) -> ConceptView:
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
        )
        writes = [Create.of(cid, concept)]
        writes += [
            Relate("related", cid, parent, {"relation": "is_a", "by": caller.actor_id, "at": now})
            for parent in parents
        ]
        await self.store.apply(writes)
        self.invalidate()
        return await self.get(caller, cid)

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
