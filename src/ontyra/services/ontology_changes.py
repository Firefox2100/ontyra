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
from ontyra.models.ontology import Concept, concept_slug
from ontyra.models.ontology_change import (
    AliasChange,
    Change,
    ChangeRequest,
    ChangeResult,
    DefineChange,
    DeprecateChange,
    MergeChange,
    OntologyChange,
    RelateChange,
)
from ontyra.ports import Create, Store, Update
from ontyra.services import errors
from ontyra.services.context import GLOBAL_OWNER, Caller
from ontyra.services.ontology import OntologyService

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

    # ---- validation -------------------------------------------------------------------

    async def _taken(self, term: str, onto_ids: dict[str, RecordId]) -> RecordId | None:
        return onto_ids.get(term.casefold().strip())

    async def validate(self, change: Change) -> list[str]:
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
                        concept_slug(name)
                        and RecordId("concept", concept_slug(name)) in onto.concepts
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
        return advice

    # ---- decide -----------------------------------------------------------------------

    async def propose(self, caller: Caller, request: ChangeRequest) -> ChangeResult:
        if not request.rationale.strip():
            raise errors.InvalidRequestError("a change needs a rationale")
        advice = await self.validate(request.change)
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
            await self._apply(caller, request.change)
            record.status = "applied"
            record.decided_by = caller.actor_id
            record.decided_at = now
            await self.store.apply([Create.of(cid, record)])
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

    async def _close_task(self, change: RecordId, reviewer: Caller) -> None:
        tasks = await self.store.select(
            "task", {"target": change, "kind": "review_ontology_change", "status": "pending"}
        )
        await self.store.apply(
            [
                Update(t.id, {"status": "done", "updated_at": self.now()})
                for t in tasks
                if t.id is not None
            ]
        )

    async def approve(self, reviewer: Caller, change: RecordId) -> ChangeResult:
        if reviewer.actor.kind != "human":
            raise errors.InvalidRequestError("only a human can approve an ontology change")
        if not reviewer.can_write(GLOBAL_OWNER):
            raise errors.PermissionDeniedError("changing the ontology needs global write access")
        rec = await self._get(change)
        if rec.status != "pending":
            raise errors.ProposalStateError(f"change {change} is already {rec.status}")
        parsed = ChangeRequest.model_validate({"change": rec.change, "rationale": rec.rationale})
        await self.validate(parsed.change)  # the ontology may have moved since the proposal
        await self._apply(reviewer, parsed.change)
        now = self.now()
        await self.store.apply(
            [
                Update(
                    change,
                    {"status": "applied", "decided_by": reviewer.actor_id, "decided_at": now},
                )
            ]
        )
        await self._close_task(change, reviewer)
        return ChangeResult(id=change, status="applied", op=rec.op, advice=rec.advice)

    async def reject(self, reviewer: Caller, change: RecordId, reason: str) -> ChangeResult:
        if reviewer.actor.kind != "human":
            raise errors.InvalidRequestError("only a human can reject an ontology change")
        if not reviewer.can_write(GLOBAL_OWNER):
            raise errors.PermissionDeniedError("changing the ontology needs global write access")
        rec = await self._get(change)
        if rec.status != "pending":
            raise errors.ProposalStateError(f"change {change} is already {rec.status}")
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
                )
            ]
        )
        await self._close_task(change, reviewer)
        return ChangeResult(id=change, status="rejected", op=rec.op, reason=reason)

    async def pending(self, caller: Caller, limit: int = 50) -> list[OntologyChange]:
        where: dict[str, object] = {"status": "pending"}
        if not caller.can_write(GLOBAL_OWNER):
            where["owner"] = caller.owner
        rows = await self.store.select("ontology_change", where, limit=limit, order_by="created_at")
        return [r for r in rows if isinstance(r, OntologyChange)]

    async def history(self, caller: Caller, limit: int = 50) -> list[OntologyChange]:
        where = None if caller.can_write(GLOBAL_OWNER) else {"owner": caller.owner}
        rows = await self.store.select(
            "ontology_change", where, limit=limit, order_by="created_at", descending=True
        )
        return [r for r in rows if isinstance(r, OntologyChange)]

    # ---- apply ------------------------------------------------------------------------

    async def _apply(self, caller: Caller, change: Change) -> None:
        match change:
            case DefineChange(concept=d):
                await self.ontology.define(caller, d)
            case RelateChange(relation=r):
                await self.ontology.relate(caller, r)
            case AliasChange(concept=term, alias=alias):
                await self._add_aliases(await self.ontology.resolve(term), [alias])
            case DeprecateChange(concept=term, replaced_by=other):
                await self.ontology.deprecate(
                    caller,
                    await self.ontology.resolve(term),
                    await self.ontology.resolve(other) if other else None,
                )
            case MergeChange(source=src, into=dst):
                a, b = await self.ontology.resolve(src), await self.ontology.resolve(dst)
                onto = await self.ontology.ontology()
                old: Concept = onto.concepts[a]
                await self._add_aliases(b, [old.label, *old.aliases])
                await self.ontology.deprecate(caller, a, b)

    async def _add_aliases(self, concept: RecordId, aliases: list[str]) -> None:
        onto = await self.ontology.ontology()
        c = onto.concepts[concept]
        merged = list(dict.fromkeys([*c.aliases, *(a.strip() for a in aliases if a.strip())]))
        merged = [a for a in merged if a.casefold() != c.label.casefold()]
        await self.store.apply([Update(concept, {"aliases": merged, "updated_at": self.now()})])
        self.ontology.invalidate()
