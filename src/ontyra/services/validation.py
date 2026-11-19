"""Proposal validation: turns a request into resolved `Prepared` ops plus findings.

Read-only. Never writes. Errors block a proposal; warnings/info are advice for the
submitting agent (e.g. "this looks like an existing memory; supersede it?").
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from pydantic import ValidationError

from ontyra.models import Record, RecordId, new_id, registry
from ontyra.models.anchors import Anchor
from ontyra.models.core import Episode, Memory, Proposal, RevState
from ontyra.models.episodes import EpisodeSettings
from ontyra.models.kinds import DirectiveRev, RevisionBase, fields_guide
from ontyra.models.proposals import (
    AddMemory,
    Annotate,
    Evidence,
    Finding,
    LinkMemories,
    ProposalRequest,
    RetractMemory,
    ReviseMemory,
    SupersedeMemory,
)
from ontyra.ports import Store
from ontyra.services import errors
from ontyra.services.anchors import AnchorService
from ontyra.services.context import GLOBAL_OWNER, Caller
from ontyra.services.ontology import OntologyService
from ontyra.services.prepared import (
    Prepared,
    PreparedAdd,
    PreparedAnnotate,
    PreparedLink,
    PreparedRetract,
    PreparedRevise,
    PreparedSupersede,
)
from ontyra.services.qualifiers import qualifier_problems

RESERVED_FIELDS = {"id", "memory", "seq", "recorded_at", "proposed_by"}
SIMILAR_THRESHOLD = 0.5
DUPLICATE_THRESHOLD = 0.9


def rev_state_id(rev: RecordId) -> RecordId:
    """A revision's status record shares its id: `fact_rev:X` ↔ `rev_state:X`."""
    return RecordId("rev_state", rev.id)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.casefold()))


def similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


@dataclass
class Validation:
    owner: RecordId = GLOBAL_OWNER  # whose memory new memories join
    findings: list[Finding] = field(default_factory=lambda: [])
    prepared: list[Prepared] = field(default_factory=lambda: [])
    refs: dict[str, tuple[RecordId, RecordId]] = field(
        default_factory=lambda: {}
    )  # ref -> (memory, rev)

    @property
    def ok(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)


class ProposalValidator:
    def __init__(
        self,
        store: Store,
        now: Callable[[], datetime],
        ontology: OntologyService,
        anchors: AnchorService,
        episodes: EpisodeSettings | None = None,
    ) -> None:
        self.episodes = episodes or EpisodeSettings()
        self.store = store
        self.now = now
        self.ontology = ontology
        self.anchors = anchors

    async def validate(
        self, caller: Caller, request: ProposalRequest, *, exclude: RecordId | None = None
    ) -> Validation:
        """Check a proposal. `exclude` is a pending proposal being amended: it is not compared
        with itself."""
        v = Validation(owner=GLOBAL_OWNER if request.space == "global" else caller.owner)
        ctx = _Ctx(self, caller, v)
        if v.owner != caller.owner and not caller.can_write(v.owner):
            ctx.error(None, "permission_denied", "writing to the global memory needs permission")
        # Pass 1: allocate ids for adds so later ops can reference them by alias.
        adds: dict[int, tuple[RecordId, RecordId]] = {}
        for i, op in enumerate(request.ops):
            if isinstance(op, AddMemory):
                mem, rev = new_id(), new_id()
                spec = registry.kinds[op.kind]
                adds[i] = (RecordId("memory", mem), RecordId(spec.table, rev))
                key = op.ref or f"op{i}"
                if key in v.refs:
                    ctx.error(i, "duplicate_ref", f"ref '{key}' is used more than once")
                v.refs[key] = adds[i]
        # Pass 2: validate each op.
        for i, op in enumerate(request.ops):
            if isinstance(op, AddMemory):
                await ctx.add(i, op, *adds[i])
            elif isinstance(op, ReviseMemory):
                await ctx.revise(i, op)
            elif isinstance(op, SupersedeMemory):
                await ctx.supersede(i, op)
            elif isinstance(op, RetractMemory):
                await ctx.retract(i, op)
            elif isinstance(op, LinkMemories):
                await ctx.link(i, op)
            else:
                await ctx.annotate(i, op)
        await self._pending_findings(ctx, request, exclude)
        return v

    async def _visible_pending(self, caller: Caller, exclude: RecordId | None) -> list[Proposal]:
        """Proposals still waiting that the caller may see (its own, and the global ones when it
        may write the global memory): what a new write should know about before duplicating."""
        statuses = {"status": ["pending", "changes_requested"]}
        if caller.can_write(GLOBAL_OWNER):
            rows = await self.store.select(
                "proposal",
                where_in=statuses,
                where_any={"owner": caller.owner, "space": "global"},
                limit=200,
            )
        else:
            rows = await self.store.select(
                "proposal", {"owner": caller.owner}, where_in=statuses, limit=200
            )
        return [r for r in rows if isinstance(r, Proposal) and r.id != exclude]

    async def _pending_findings(
        self, ctx: _Ctx, request: ProposalRequest, exclude: RecordId | None
    ) -> None:
        """Advice about other waiting proposals (never an error, never used by recall)."""
        waiting = await self._visible_pending(ctx.caller, exclude)
        if not waiting:
            return
        for i, op in enumerate(request.ops):
            hits = 0
            for p in waiting:
                for n, other in enumerate(p.ops):
                    if hits >= 5:
                        break
                    kind = other.get("op")
                    if isinstance(op, AddMemory) and kind == "add" and other.get("kind") == op.kind:
                        other_fields: dict[str, Any] = other.get("fields") or {}
                        text = str(other_fields.get("text", ""))
                        mine = str(op.fields.get("text", ""))
                        score = similarity(mine, text)
                        if mine and score >= SIMILAR_THRESHOLD:
                            hits += 1
                            ctx.warn(
                                i,
                                "pending_similar",
                                f"{p.id} (waiting for review) op {n} says nearly the same: "
                                f"{text[:100]!r}",
                                proposal=str(p.id),
                                op_number=n,
                                similarity=round(score, 2),
                                hint="amend that proposal instead of adding another",
                            )
                    target = (
                        op.memory
                        if isinstance(op, ReviseMemory | RetractMemory)
                        else op.target
                        if isinstance(op, SupersedeMemory)
                        else None
                    )
                    if target is not None and kind in ("revise", "retract", "supersede"):
                        other_target = other.get("memory") or other.get("target")
                        if other_target is not None and str(other_target) == str(target):
                            hits += 1
                            ctx.warn(
                                i,
                                "pending_conflict",
                                f"{p.id} (waiting for review) op {n} also changes {target}",
                                proposal=str(p.id),
                                op_number=n,
                                hint="amend that proposal, or wait for it to be decided",
                            )


class _Ctx:
    """Per-validation state and the per-op checks."""

    def __init__(self, owner: ProposalValidator, caller: Caller, v: Validation) -> None:
        self.o, self.caller, self.v = owner, caller, v
        self.touched: set[RecordId] = set()  # existing memories already mutated by an op

    # ---- finding helpers -------------------------------------------------

    def error(self, i: int | None, code: str, message: str, **data: Any) -> None:
        self.v.findings.append(
            Finding(severity="error", code=code, message=message, op=i, data=data)
        )

    def warn(self, i: int | None, code: str, message: str, **data: Any) -> None:
        self.v.findings.append(
            Finding(severity="warning", code=code, message=message, op=i, data=data)
        )

    def info(self, i: int | None, code: str, message: str, **data: Any) -> None:
        self.v.findings.append(
            Finding(severity="info", code=code, message=message, op=i, data=data)
        )

    def _schema_errors(self, i: int, exc: ValidationError, kind: str | None = None) -> None:
        for e in exc.errors():
            where = ".".join(str(p) for p in e["loc"]) or "(root)"
            self.error(i, "invalid_field", f"{where}: {e['msg']}", field=where)
        if kind is not None:  # what the kind takes, so a retry can be right
            self.v.findings.append(
                Finding(
                    severity="info",
                    code="fields_hint",
                    message=f"`fields` for kind `{kind}` (optional in brackets): "
                    + fields_guide(kind),
                    op=i,
                )
            )

    # ---- shared lookups ----------------------------------------------------

    async def _memory(self, i: int, rid: RecordId, *, mutate: bool) -> tuple[Memory, Record] | None:
        mem = await self.o.store.get(rid)
        if not isinstance(mem, Memory):
            self.error(
                i, "unknown_memory", f"{rid} does not exist (to store something new use op `add`)"
            )
            return None
        if mem.owner not in self.caller.visible_owners():
            self.error(
                i, "unknown_memory", f"{rid} does not exist (to store something new use op `add`)"
            )  # do not leak other owners
            return None
        if mutate and not self.caller.can_write(mem.owner):
            self.error(i, "permission_denied", f"{rid} belongs to {mem.owner}", memory=str(rid))
            return None
        if mem.status != "active":
            self.error(i, "not_active", f"{rid} is {mem.status}", memory=str(rid))
            return None
        if mem.current is None:
            self.error(i, "no_revision", f"{rid} has no current revision")
            return None
        rev = await self.o.store.get(mem.current)
        if rev is None:
            self.error(i, "no_revision", f"{rid} current revision is missing")
            return None
        if mutate:
            if rid in self.touched:
                self.error(i, "duplicate_target", f"{rid} is targeted by more than one op")
                return None
            self.touched.add(rid)
        return mem, rev

    async def _resolve(
        self, i: int, token: str | RecordId
    ) -> tuple[RecordId, RecordId, bool] | None:
        """(memory, rev, is_new_in_this_proposal) for a ref alias or an existing memory id."""
        if isinstance(token, str) and token in self.v.refs:
            mem, rev = self.v.refs[token]
            return mem, rev, True
        try:
            rid = RecordId.parse(token)
        except ValueError:
            self.error(
                i, "unknown_ref", f"'{token}' is neither a ref in this proposal nor a memory id"
            )
            return None
        found = await self._memory(i, rid, mutate=False)
        if found is None:
            return None
        mem, rev = found
        assert mem.id is not None and rev.id is not None
        return mem.id, rev.id, False

    async def _check_evidence(self, i: int, evidence: Sequence[Evidence]) -> list[Evidence]:
        """The evidence with every episode resolved (a quote alone is looked up in the log).
        Problems are recorded as errors; the proposal is then rejected anyway."""
        resolved: list[Evidence] = []
        for ev in evidence:
            if ev.episode is not None:
                ep = await self.o.store.get(ev.episode)
                if not isinstance(ep, Episode) or ep.owner not in (self.v.owner, self.caller.owner):
                    self.error(
                        i, "unknown_episode", f"evidence episode {ev.episode} does not exist"
                    )
                    continue
                if ev.quote is not None and _norm(ev.quote) not in _norm(ep.content):
                    self.error(
                        i,
                        "grounding_failed",
                        f"quote not found in {ev.episode}",
                        quote=ev.quote[:120],
                    )
                    continue
                resolved.append(ev)
            else:
                assert ev.quote is not None
                found = await self._episode_for(ev.quote)
                if found is None:
                    self.error(
                        i,
                        "grounding_failed",
                        "the quote does not occur in your recent episodes; copy the words as "
                        "they were said, or leave the evidence out",
                        quote=ev.quote[:120],
                    )
                    continue
                ev.episode = found  # the stored proposal names the episode; review may come late
                resolved.append(ev)
        return resolved

    async def _episode_for(self, quote: str) -> RecordId | None:
        """The newest recent episode containing the quote; what a person said beats what was
        said back to them."""
        cfg = self.o.episodes
        owners = list(dict.fromkeys([self.v.owner, self.caller.owner]))
        since = self.o.now() - timedelta(hours=cfg.match_window_hours)
        wanted = _norm(quote)
        found: Episode | None = None
        for ep in await self.o.store.recent_episodes(owners, since, cfg.match_limit):
            if wanted not in _norm(ep.content):
                continue
            if ep.role == "user":
                return ep.id
            found = found or ep
        return found.id if found is not None else None

    def _bookkeeping(self, memory: RecordId, seq: int) -> dict[str, Any]:
        return {
            "memory": memory,
            "seq": seq,
            "recorded_at": self.o.now(),
            "proposed_by": self.caller.actor_id,
        }

    # ---- ops -----------------------------------------------------------------

    def _unscoped(self, i: int, model: Any, memory: RecordId | None = None) -> None:
        """A directive tied to nothing is neither universal nor contextual: say so when written."""
        if (
            getattr(model, "scope", "contextual") == "contextual"
            and isinstance(model, DirectiveRev)
            and not (model.anchors or model.qualifiers or model.about)
        ):
            self.warn(
                i,
                "unscoped_directive",
                "this directive has no anchors, qualifiers or `about` and is not "
                "`scope: universal`: it will not appear in briefings or checks. Say where or "
                "when it applies, or set `scope: universal` if it holds everywhere"
                + (
                    f". It is saved as {memory} if the proposal commits: fix it with `revise` on "
                    "that id, do not add it a second time"
                    if memory
                    else ""
                ),
            )

    async def add(self, i: int, op: AddMemory, mem: RecordId, rev: RecordId) -> None:
        reserved = RESERVED_FIELDS & op.fields.keys()
        if reserved:
            self.error(i, "reserved_field", f"fields set by the service: {sorted(reserved)}")
            return
        spec = registry.kinds[op.kind]
        try:
            model = spec.model.model_validate({**op.fields, **self._bookkeeping(mem, 1)})
        except ValidationError as exc:
            self._schema_errors(i, exc, op.kind)
            return
        links = await self._links(i, model)
        if links is None:
            return
        model = links
        evidence = await self._check_evidence(i, op.evidence)
        sources: list[RecordId] = []
        for token in op.derived_from:
            src = await self._resolve(i, token)
            if src is not None:
                source_memory = await self.o.store.get(src[0])
                if not isinstance(source_memory, Memory) or source_memory.owner != self.v.owner:
                    self.error(i, "owner_mismatch", "derived memories must have the same owner")
                    continue
                sources.append(src[1])
        self._unscoped(i, model, mem)
        await self._similar(i, op.kind, model)
        self.v.prepared.append(
            PreparedAdd(
                i,
                mem,
                rev,
                self.v.owner,
                op.kind,
                model,
                op.valid_from or self.o.now(),
                evidence,
                sources,
            )
        )

    async def _links(self, i: int, model: Record) -> Record | None:
        """Check ontology links and qualifier expressions; canonicalise anchor URIs.
        Returns the (possibly rewritten) model, or `None` after recording errors."""
        if not isinstance(model, RevisionBase):
            return model
        onto = await self.o.ontology.ontology()
        bad = False
        about: list[RecordId] = []
        for cid in model.about:
            if cid not in onto.concepts:
                self.error(i, "unknown_concept", f"{cid} does not exist", concept=str(cid))
                bad = True
                continue
            live, note = await self.o.ontology.use(cid)
            if live is None:
                self.error(
                    i, "deprecated_concept", note or f"{cid} is deprecated", concept=str(cid)
                )
                bad = True
                continue
            if live != cid:
                self.warn(i, "deprecated_concept", f"{note}: used instead", concept=str(cid))
            about.append(live)
        model.about = list(dict.fromkeys(about))
        for q in model.qualifiers:
            qc = onto.concepts.get(q.concept)
            if qc is None:
                self.error(
                    i, "unknown_concept", f"{q.concept} does not exist", concept=str(q.concept)
                )
                bad = True
                continue
            for problem in qualifier_problems(q, qc):
                self.error(i, "bad_qualifier", problem, concept=str(q.concept))
                bad = True
        anchors: list[Anchor] = []
        for a in model.anchors:
            try:
                anchors.append(Anchor(uri=self.o.anchors.canonical(a.uri), match=a.match))
            except errors.InvalidRequestError as exc:
                self.error(i, "bad_anchor", str(exc), uri=a.uri)
                bad = True
        if bad:
            return None
        unique = {(a.uri, a.match): a for a in anchors}
        model.anchors = list(unique.values())
        return model

    async def _similar(
        self, i: int, kind: str, model: Record, *, exclude: RecordId | None = None
    ) -> None:
        if not isinstance(model, RevisionBase):
            return
        existing = await self.o.store.active_memories(kind, self.caller.visible_owners())
        scored: list[tuple[float, Memory, RevisionBase]] = []
        for mem, rev in existing:
            if not isinstance(rev, RevisionBase) or mem.id == exclude:
                continue
            score = similarity(model.text, rev.text)
            if score >= SIMILAR_THRESHOLD:
                scored.append((score, mem, rev))
        for score, mem, rev in sorted(scored, key=lambda t: -t[0])[:5]:
            dup = score >= DUPLICATE_THRESHOLD
            (self.warn if dup else self.info)(
                i,
                "possible_duplicate" if dup else "similar_memory",
                f"{mem.id} looks {'like a duplicate' if dup else 'related'}: {rev.text[:100]!r}",
                memory=str(mem.id),
                similarity=round(score, 2),
                hint="supersede, revise or link it with a reason, or ignore if distinct",
            )

    async def revise(self, i: int, op: ReviseMemory) -> None:
        found = await self._memory(i, op.memory, mutate=True)
        if found is None:
            return
        mem, old = found
        if not isinstance(old, RevisionBase) or old.id is None:
            self.error(i, "no_revision", f"{op.memory} has no usable current revision")
            return
        reserved = RESERVED_FIELDS & op.fields.keys()
        if reserved:
            self.error(i, "reserved_field", f"fields set by the service: {sorted(reserved)}")
            return
        base = old.model_dump(mode="python", exclude={"id"})
        merged = {**base, **op.fields, **self._bookkeeping(old.memory, old.seq + 1)}
        if {k: v for k, v in merged.items() if k not in RESERVED_FIELDS} == {
            k: v for k, v in base.items() if k not in RESERVED_FIELDS
        }:
            self.error(i, "no_change", "the revision would be identical to the current one")
            return
        spec = registry.kinds[mem.kind]
        try:
            model = spec.model.model_validate(merged)
        except ValidationError as exc:
            self._schema_errors(i, exc, mem.kind)
            return
        links = await self._links(i, model)
        if links is None:
            return
        model = links
        evidence = await self._check_evidence(i, op.evidence)
        new_rev = RecordId(spec.table, new_id())
        self.v.prepared.append(
            PreparedRevise(i, mem, old.id, new_rev, model, op.reason_code, op.reason, evidence)
        )

    async def supersede(self, i: int, op: SupersedeMemory) -> None:
        target = await self._memory(i, op.target, mutate=True)
        new = await self._resolve(i, op.new)
        if target is None or new is None:
            return
        t_mem, t_rev = target
        new_mem, new_rev, is_new = new
        if new_mem == op.target:
            self.error(i, "self_supersede", "a memory cannot supersede itself")
            return
        if not is_new:
            new_row = await self.o.store.get(new_mem)
            if isinstance(new_row, Memory) and new_row.owner != t_mem.owner:
                self.error(i, "owner_mismatch", "both memories must have the same owner")
                return
        assert t_rev.id is not None
        self.v.prepared.append(
            PreparedSupersede(
                i,
                new_mem,
                new_rev,
                None if is_new else new_rev,
                t_mem,
                t_rev.id,
                op.reason_code,
                op.reason,
            )
        )

    async def retract(self, i: int, op: RetractMemory) -> None:
        found = await self._memory(i, op.memory, mutate=True)
        if found is None:
            return
        mem, rev = found
        assert rev.id is not None
        deps = await self.o.store.dependents(rev.id)
        if deps:
            self.warn(
                i,
                "has_dependents",
                f"{len(deps)} revision(s) were derived from this memory and will be flagged stale",
                dependents=[str(d) for d in deps],
            )
        self.v.prepared.append(PreparedRetract(i, mem, rev.id, op.reason, deps))

    async def link(self, i: int, op: LinkMemories) -> None:
        src = await self._resolve(i, op.source)
        dst = await self._resolve(i, op.target)
        if src is None or dst is None:
            return
        if src[0] == dst[0]:
            self.error(i, "self_link", "cannot link a memory to itself")
            return
        owners: list[RecordId | None] = []
        for memory, _, is_new in (src, dst):
            if is_new:  # created in this very proposal: it will have the proposal's owner
                owners.append(self.v.owner)
                continue
            row = await self.o.store.get(memory)
            owners.append(row.owner if isinstance(row, Memory) else None)
        if owners[0] is None or owners[0] != owners[1]:
            self.error(i, "owner_mismatch", "linked memories must have the same owner")
            return
        if op.relation == "promoted_to" and not (
            src[1].table == "lesson_rev" and dst[1].table in ("directive_rev", "procedure_rev")
        ):
            self.error(i, "bad_link", "promoted_to goes from a lesson to a directive or procedure")
            return
        self.v.prepared.append(PreparedLink(i, op.relation, src[1], dst[1], op.reason))

    async def annotate(self, i: int, op: Annotate) -> None:
        target = await self.o.store.get(op.target) if op.target.table in registry.tables else None
        owner = getattr(target, "owner", None)
        memory = getattr(target, "memory", None)
        if owner is None and isinstance(memory, RecordId):
            parent = await self.o.store.get(memory)
            owner = parent.owner if isinstance(parent, Memory) else None
        if owner is None and target is not None:
            owner = GLOBAL_OWNER
        if target is None or not isinstance(owner, RecordId) or not self.caller.can_write(owner):
            self.error(
                i,
                "unknown_target",
                f"{op.target} does not exist: annotate needs the id of an existing memory; "
                "to store something new use op `add`",
            )
            return
        self.v.prepared.append(PreparedAnnotate(i, op.target, op.kind, op.text))


def state_is_visible(state: RevState | None) -> bool:
    """Default visibility of a revision: committed, active, not retracted."""
    return state is not None and state.state == "committed" and state.lifecycle == "active"
