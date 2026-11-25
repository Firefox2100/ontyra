"""Plans for ontology changes: what a change writes, as one batch, and how to undo it.

A *mutation* (edit, alias, relation changes) keeps the concept and writes a new version of it;
relations are ended, never deleted. A *structural* change (merge, split, deprecate) leaves the old
concept in place as a tombstone pointing at what replaced it. Every plan is computed from the
current snapshot and applied by the caller in a single `Store.apply` under the ontology lock, so a
change is all or nothing. The undo of a plan is a list of changes (a revert applies them).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ontyra.models import RecordId
from ontyra.models.ontology import Concept, ConceptDefinition, ConceptRev, RelationRequest
from ontyra.models.ontology_change import (
    AliasChange,
    Change,
    DefineChange,
    DeprecateChange,
    EditChange,
    MergeChange,
    RelateChange,
    ReparentChange,
    RestoreChange,
    SplitChange,
    UnrelateChange,
)
from ontyra.ports import Relate, Update, Write
from ontyra.services import errors
from ontyra.services.context import Caller
from ontyra.services.ontology import OntologyService
from ontyra.services.reasoner import Ontology

DRIFT = (
    0.3  # a rewritten definition sharing less than this with the old one looks like a new meaning
)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.casefold()))


def _overlap(a: str, b: str) -> float:
    x, y = _tokens(a), _tokens(b)
    return len(x & y) / len(x | y) if x and y else 1.0


@dataclass
class Plan:
    writes: list[Write] = field(default_factory=lambda: [])
    touched: list[RecordId] = field(default_factory=lambda: [])
    undo: list[dict[str, Any]] = field(default_factory=lambda: [])
    lines: list[str] = field(default_factory=lambda: [])  # what it does, one line each
    advice: list[str] = field(default_factory=lambda: [])


class OntologyPlanner:
    def __init__(self, ontology: OntologyService, now: Callable[[], datetime]) -> None:
        self.o = ontology
        self.now = now

    # ---- entry ------------------------------------------------------------------------

    async def plan(
        self,
        change: Change,
        caller: Caller,
        change_id: RecordId | None,
        batch: frozenset[RecordId] = frozenset(),
    ) -> Plan:
        """The writes for `change`; raises when it cannot apply. Nothing is written. `batch` are
        the concepts a revert restores together: a name one of them holds is not a collision."""
        match change:
            case DefineChange(concept=d):
                return await self._define(caller, change_id, d)
            case RelateChange(relation=r):
                return await self._relate(caller, r)
            case UnrelateChange(relation=r):
                return await self._unrelate(caller, change_id, r)
            case AliasChange(concept=term, alias=alias):
                return await self._edit(
                    caller, change_id, EditChange(concept=term, add_aliases=[alias])
                )
            case EditChange():
                return await self._edit(caller, change_id, change)
            case DeprecateChange(concept=term, replaced_by=other, reason=reason):
                return await self._deprecate(caller, change_id, term, other, reason)
            case MergeChange(source=src, into=dst):
                return await self._merge(caller, change_id, src, dst)
            case ReparentChange():
                return await self._reparent(caller, change_id, change)
            case SplitChange():
                return await self._split(caller, change_id, change)
            case RestoreChange(concept=term, version=version):
                return await self._restore(caller, change_id, term, version, batch)

    # ---- helpers ----------------------------------------------------------------------

    @staticmethod
    def _names(onto: Ontology, *, except_for: RecordId | None = None) -> dict[str, RecordId]:
        """Every label and alias in use, deprecated concepts included (ids are never reused)."""
        out: dict[str, RecordId] = {}
        for cid, c in onto.concepts.items():
            if cid == except_for:
                continue
            for name in (c.label, *c.aliases):
                out.setdefault(name.casefold().strip(), cid)
        return out

    def _bump(
        self, caller: Caller, change_id: RecordId | None, cid: RecordId, c: Concept, **patch: Any
    ) -> tuple[list[Write], int]:
        """Writes that move `c` to the patched fields as a new version (writing the version it
        had before first, for a concept from before versions); and the version it had."""
        before = c.version or 1
        writes: list[Write] = []
        if c.version is None:
            baseline = self.o.version_write(cid, c, 1, None, None)
            writes.append(baseline)
        after = before + 1
        new = c.model_copy(update=patch)
        writes.append(
            Update(cid, {**patch, "version": after, "updated_at": self.now()}),
        )
        writes.append(self.o.version_write(cid, new, after, caller, change_id))
        return writes, before

    async def _need(self, term: str) -> tuple[RecordId, Concept, Ontology]:
        cid = await self.o.resolve(term)
        onto = await self.o.ontology()
        return cid, onto.concepts[cid], onto

    @staticmethod
    def _restore_of(cid: RecordId, version: int) -> dict[str, Any]:
        return RestoreChange(concept=str(cid), version=version).model_dump(mode="json")

    # ---- define / relate ----------------------------------------------------------------

    async def _define(
        self, caller: Caller, change_id: RecordId | None, d: ConceptDefinition
    ) -> Plan:
        cid, writes = await self.o.define_writes(caller, d, change=change_id)
        parents = [await self.o.resolve(p) for p in d.is_a]
        undo = DeprecateChange(concept=str(cid), reason="the definition was reverted")
        return Plan(
            writes,
            [cid, *parents],
            [undo.model_dump(mode="json")],
            [f"define {cid}" + (f" under {', '.join(str(p) for p in parents)}" if parents else "")],
        )

    async def _relate(self, caller: Caller, r: RelationRequest) -> Plan:
        src, dst = await self.o.resolve(r.source), await self.o.resolve(r.target)
        if src == dst:
            raise errors.InvalidRequestError("a concept cannot be related to itself")
        if r.relation == "split_into":
            raise errors.InvalidRequestError("use `split` to say what a concept became")
        onto = await self.o.ontology()
        for cid in (src, dst):
            if onto.concepts[cid].status == "deprecated":
                raise errors.InvalidRequestError(f"{cid} is deprecated: relate its successor")
        if ("out", r.relation, dst) in set(onto.neighbours(src)):
            return Plan(lines=[f"{src} -{r.relation}-> {dst} already exists"])
        if r.relation in ("is_a", "part_of", "instance_of") and src in onto.ancestors(
            dst, (r.relation,)
        ):
            raise errors.ConflictError(f"{dst} is already a kind of {src}: that would be a cycle")
        edge = {"relation": r.relation, "by": caller.actor_id, "at": self.now()}
        undo = UnrelateChange(
            relation=RelationRequest(source=str(src), relation=r.relation, target=str(dst))
        )
        return Plan(
            [Relate("related", src, dst, edge)],
            [src, dst],
            [undo.model_dump(mode="json")],
            [f"relate {src} -{r.relation}-> {dst}"],
        )

    async def _unrelate(
        self, caller: Caller, change_id: RecordId | None, r: RelationRequest
    ) -> Plan:
        src, dst = await self.o.resolve(r.source), await self.o.resolve(r.target)
        ids = await self.o.edge_ids(src, r.relation, dst)
        if not ids:
            raise errors.InvalidRequestError(f"{src} -{r.relation}-> {dst} does not exist")
        ended = {"ended_at": self.now(), "ended_by": caller.actor_id, "ended_by_change": change_id}
        redo = RelateChange(
            relation=RelationRequest(source=str(src), relation=r.relation, target=str(dst))
        )
        return Plan(
            [Update(i, ended) for i in ids],
            [src, dst],
            [redo.model_dump(mode="json")],
            [f"end {src} -{r.relation}-> {dst} (kept as history)"],
        )

    async def _reparent(
        self, caller: Caller, change_id: RecordId | None, ch: ReparentChange
    ) -> Plan:
        cid, _, onto = await self._need(ch.concept)
        old, new = await self.o.resolve(ch.from_parent), await self.o.resolve(ch.to_parent)
        if new in (cid, old):
            raise errors.InvalidRequestError("the new parent must be another concept")
        ids = await self.o.edge_ids(cid, ch.relation, old)
        if not ids:
            raise errors.InvalidRequestError(f"{cid} -{ch.relation}-> {old} does not exist")
        if onto.concepts[new].status == "deprecated":
            raise errors.InvalidRequestError(f"{new} is deprecated: use its successor")
        if ("out", ch.relation, new) in set(onto.neighbours(cid)):
            raise errors.InvalidRequestError(f"{cid} -{ch.relation}-> {new} already exists")
        if cid in onto.ancestors(new, (ch.relation,)):
            raise errors.ConflictError(f"{new} is already a kind of {cid}: that would be a cycle")
        writes: list[Write] = [
            *(
                Update(
                    i,
                    {
                        "ended_at": self.now(),
                        "ended_by": caller.actor_id,
                        "ended_by_change": change_id,
                    },
                )
                for i in ids
            ),
            Relate(
                "related",
                cid,
                new,
                {"relation": ch.relation, "by": caller.actor_id, "at": self.now()},
            ),
        ]
        undo = ReparentChange(
            concept=str(cid), relation=ch.relation, from_parent=str(new), to_parent=str(old)
        )
        return Plan(
            writes,
            [cid, old, new],
            [undo.model_dump(mode="json")],
            [f"move {cid} from under {old} to under {new} ({ch.relation})"],
        )

    # ---- edit ---------------------------------------------------------------------------

    async def _edit(self, caller: Caller, change_id: RecordId | None, ch: EditChange) -> Plan:
        cid, c, onto = await self._need(ch.concept)
        if c.status == "deprecated":
            raise errors.InvalidRequestError(f"{cid} is deprecated: restore it before editing it")
        names = self._names(onto, except_for=cid)

        def free(name: str) -> None:
            hit = names.get(name.casefold().strip())
            if hit is not None:
                raise errors.InvalidRequestError(f"{name!r} already names {hit}")

        label, aliases = c.label, list(c.aliases)
        new_label = (ch.label or "").strip()
        if new_label and new_label != c.label:
            free(new_label)
            aliases = [a for a in aliases if a.casefold() != new_label.casefold()]
            if c.label.casefold() != new_label.casefold():
                aliases.append(c.label)
            label = new_label
        for alias in (a.strip() for a in ch.add_aliases):
            if not alias or alias.casefold() == label.casefold():
                continue
            if alias.casefold() not in {a.casefold() for a in aliases}:
                free(alias)
                aliases.append(alias)
        for alias in ch.remove_aliases:
            keep = [a for a in aliases if a.casefold() != alias.casefold().strip()]
            if len(keep) == len(aliases):
                raise errors.InvalidRequestError(f"{alias!r} is not an alias of {cid}")
            aliases = keep
        advice: list[str] = []
        definition = c.definition
        if ch.definition is not None:
            definition = ch.definition.strip() or None
            if c.definition and definition and _overlap(c.definition, definition) < DRIFT:
                advice.append(
                    "the new definition shares little with the old one: if the meaning changed, "
                    "define a new concept and deprecate this one instead of editing it"
                )
        patch: dict[str, Any] = {}
        if label != c.label:
            patch["label"] = label
        if aliases != c.aliases:
            patch["aliases"] = aliases
        if definition != c.definition:
            patch["definition"] = definition
        if not patch:
            raise errors.InvalidRequestError("the edit changes nothing")
        writes, before = self._bump(caller, change_id, cid, c, **patch)
        return Plan(
            writes,
            [cid],
            [self._restore_of(cid, before)],
            [f"edit {cid}: " + ", ".join(sorted(patch))],
            advice,
        )

    # ---- structural ---------------------------------------------------------------------

    async def _deprecate(
        self,
        caller: Caller,
        change_id: RecordId | None,
        term: str,
        other: str | None,
        reason: str | None,
    ) -> Plan:
        cid, c, onto = await self._need(term)
        if c.status == "deprecated":
            raise errors.InvalidRequestError(f"{cid} is already deprecated")
        successor: RecordId | None = None
        if other:
            successor = await self.o.resolve(other)
            if successor == cid or onto.resolve(successor) == cid:
                raise errors.InvalidRequestError("a concept cannot replace itself")
            if onto.concepts[successor].status == "deprecated":
                raise errors.InvalidRequestError(f"{successor} is deprecated itself")
        writes, before = self._bump(
            caller,
            change_id,
            cid,
            c,
            status="deprecated",
            replaced_by=successor,
            reason=(reason or "").strip() or None,
        )
        line = f"deprecate {cid}" + (
            f", replaced by {successor}" if successor else ", no successor"
        )
        return Plan(
            writes,
            [cid, *([successor] if successor else [])],
            [self._restore_of(cid, before)],
            [line],
        )

    async def _merge(self, caller: Caller, change_id: RecordId | None, src: str, dst: str) -> Plan:
        a, ca, _ = await self._need(src)
        b, cb, _ = await self._need(dst)
        if a == b:
            raise errors.InvalidRequestError("cannot merge a concept into itself")
        if ca.status == "deprecated":
            raise errors.InvalidRequestError(f"{a} is already deprecated")
        if cb.status == "deprecated":
            raise errors.InvalidRequestError(f"{b} is deprecated: merge into its successor")
        moved = [n for n in (ca.label, *ca.aliases) if n.casefold() != cb.label.casefold()]
        merged: list[str] = []
        for name in [*cb.aliases, *moved]:
            if name.casefold() not in {m.casefold() for m in merged}:
                merged.append(name)
        w_into, before_b = self._bump(caller, change_id, b, cb, aliases=merged)
        w_src, before_a = self._bump(
            caller, change_id, a, ca, status="deprecated", replaced_by=b, reason="merged"
        )
        return Plan(
            [*w_into, *w_src],
            [a, b],
            [self._restore_of(a, before_a), self._restore_of(b, before_b)],
            [f"merge {a} into {b} (its names become aliases; {a} stays as a deprecated tombstone)"],
        )

    async def _split(self, caller: Caller, change_id: RecordId | None, ch: SplitChange) -> Plan:
        sid, sc, onto = await self._need(ch.source)
        if sc.status == "deprecated":
            raise errors.InvalidRequestError(f"{sid} is already deprecated")
        parents = [
            o
            for d, rel, o in onto.neighbours(sid)
            if d == "out" and rel == "is_a" and onto.concepts[o].status == "active"
        ]
        writes: list[Write] = []
        targets: list[RecordId] = []
        created: list[RecordId] = []
        for item in ch.into:
            if isinstance(item, ConceptDefinition):
                d = (
                    item
                    if item.is_a
                    else item.model_copy(update={"is_a": [str(p) for p in parents]})
                )
                tid, w = await self.o.define_writes(caller, d, change=change_id)
                writes += w
                created.append(tid)
            else:
                tid = await self.o.resolve(item)
                live, note = await self.o.use(tid)
                if live is None:
                    raise errors.InvalidRequestError(note or f"{tid} cannot be used")
                tid = live
            if tid == sid:
                raise errors.InvalidRequestError("a concept cannot be split into itself")
            targets.append(tid)
        if len(set(targets)) < 2:
            raise errors.InvalidRequestError("a split needs at least two different results")
        edge = {"relation": "split_into", "by": caller.actor_id, "at": self.now()}
        writes += [Relate("related", sid, t, edge) for t in targets]
        bump, before = self._bump(
            caller, change_id, sid, sc, status="deprecated", replaced_by=None, reason="split"
        )
        undo: list[dict[str, Any]] = [
            UnrelateChange(
                relation=RelationRequest(source=str(sid), relation="split_into", target=str(t))
            ).model_dump(mode="json")
            for t in targets
        ]
        undo += [
            DeprecateChange(concept=str(t), reason="the split was reverted").model_dump(mode="json")
            for t in created
        ]
        undo.append(self._restore_of(sid, before))
        return Plan(
            [*writes, *bump],
            [sid, *targets],
            undo,
            [
                f"split {sid} into {', '.join(str(t) for t in targets)}"
                + (f" ({len(created)} new)" if created else "")
                + f"; {sid} stays as a deprecated tombstone"
            ],
        )

    async def _restore(
        self,
        caller: Caller,
        change_id: RecordId | None,
        term: str,
        version: int,
        batch: frozenset[RecordId],
    ) -> Plan:
        cid, c, onto = await self._need(term)
        found = [
            r
            for r in await self.o.store.select(
                "concept_rev", {"concept": cid, "seq": version}, limit=1
            )
            if isinstance(r, ConceptRev)
        ]
        if not found:
            raise errors.NotFoundError(f"{cid} has no version {version}")
        rev = found[0]
        names = self._names(onto, except_for=cid)
        for name in (rev.label, *rev.aliases):
            hit = names.get(name.casefold().strip())
            if hit is not None and hit not in batch:
                raise errors.InvalidRequestError(f"{name!r} now names {hit}: cannot restore it")
        if rev.replaced_by is not None and rev.replaced_by not in onto.concepts:
            raise errors.NotFoundError(f"{rev.replaced_by} no longer exists")
        patch: dict[str, Any] = {
            "label": rev.label,
            "aliases": list(rev.aliases),
            "definition": rev.definition,
            "status": rev.status,
            "replaced_by": rev.replaced_by,
            "reason": rev.reason,
        }
        if all(getattr(c, k) == v for k, v in patch.items()):
            raise errors.InvalidRequestError(f"{cid} already is as it was in version {version}")
        writes, before = self._bump(caller, change_id, cid, c, **patch)
        return Plan(
            writes,
            [cid],
            [self._restore_of(cid, before)],
            [f"restore {cid} to version {version}"],
        )
