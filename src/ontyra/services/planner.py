"""Pure translation of validated ops into backend-neutral writes (no I/O)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from ontyra.models import Decision, RecordId, new_id
from ontyra.models.core import Annotation, Memory, Proposal, RevState, Task
from ontyra.models.kinds import RevisionBase
from ontyra.models.proposals import Finding
from ontyra.ports import Create, Delete, ExpectCurrent, Relate, Update, Write
from ontyra.services.indexing import doc_for, doc_id, embedding_id
from ontyra.services.prepared import (
    Prepared,
    PreparedAdd,
    PreparedLink,
    PreparedRetract,
    PreparedRevise,
    PreparedSupersede,
)
from ontyra.services.validation import rev_state_id


def _unindex(rev: RecordId) -> list[Write]:
    """Remove a revision from the derived index (it is no longer the live one)."""
    return [Delete(doc_id(rev)), Delete(embedding_id(rev))]


def _evidence_edges(rev: RecordId, evidence: Sequence[Any], now: datetime) -> list[Write]:
    return [Relate("derived_from", rev, ev.episode, {"at": now}) for ev in evidence]


def memory_writes(prepared: Sequence[Prepared], actor: RecordId, now: datetime) -> list[Write]:
    """Writes that apply the ops. Order matters inside the batch (guards first)."""
    guards: list[Write] = []
    body: list[Write] = []
    # adds first: later ops (supersede/link) may point at the revisions they create
    ordered = sorted(prepared, key=lambda op: 0 if isinstance(op, PreparedAdd) else 1)
    for p in ordered:
        if isinstance(p, PreparedAdd):
            assert p.model.id is None and isinstance(p.model, RevisionBase)
            body += [
                Create.of(doc_id(p.rev), doc_for(p.model, p.rev, p.kind, p.owner, now)),
                Create.of(p.rev, p.model),
                Create.of(
                    rev_state_id(p.rev),
                    RevState(rev=p.rev, state="committed", valid_from=p.valid_from),
                ),
                Create.of(
                    p.memory,
                    Memory(
                        kind=p.kind,  # type: ignore[arg-type]
                        owner=p.owner,
                        current=p.rev,
                        created_at=now,
                    ),
                ),
                *_evidence_edges(p.rev, p.evidence, now),
                *(Relate("derived_from", p.rev, src, {"at": now}) for src in p.sources),
            ]
        elif isinstance(p, PreparedRevise):
            assert p.memory.id is not None
            guards.append(ExpectCurrent(p.memory.id, p.old_rev))
            assert isinstance(p.model, RevisionBase)
            body += [
                *_unindex(p.old_rev),
                Create.of(
                    doc_id(p.new_rev),
                    doc_for(p.model, p.new_rev, p.memory.kind, p.memory.owner, now),
                ),
                Create.of(p.new_rev, p.model),
                Create.of(
                    rev_state_id(p.new_rev),
                    RevState(rev=p.new_rev, state="committed", valid_from=now),
                ),
                Update(rev_state_id(p.old_rev), {"lifecycle": "superseded", "valid_to": now}),
                Update(p.memory.id, {"current": p.new_rev}),
                Relate(
                    "supersedes",
                    p.new_rev,
                    p.old_rev,
                    _edge(p.reason_code, p.reason, actor, now),
                ),
                *_evidence_edges(p.new_rev, p.evidence, now),
            ]
        elif isinstance(p, PreparedSupersede):
            assert p.target.id is not None
            guards.append(ExpectCurrent(p.target.id, p.target_rev))
            if p.new_guard is not None:
                guards.append(ExpectCurrent(p.new_memory, p.new_guard))
            body += [
                *_unindex(p.target_rev),
                Update(rev_state_id(p.target_rev), {"lifecycle": "superseded", "valid_to": now}),
                Update(p.target.id, {"status": "superseded"}),
                Relate(
                    "supersedes",
                    p.new_rev,
                    p.target_rev,
                    _edge(p.reason_code, p.reason, actor, now),
                ),
            ]
        elif isinstance(p, PreparedRetract):
            assert p.memory.id is not None
            guards.append(ExpectCurrent(p.memory.id, p.rev))
            body += [
                *_unindex(p.rev),
                Update(
                    rev_state_id(p.rev),
                    {"lifecycle": "retracted", "retracted_at": now, "retract_reason": p.reason},
                ),
                Update(p.memory.id, {"status": "retracted"}),
            ]
            for dep in p.dependents:
                body += [
                    Update(
                        rev_state_id(dep),
                        {"stale": True, "stale_reason": f"{p.rev} was retracted: {p.reason}"},
                    ),
                    Create.of(
                        RecordId("task", new_id()),
                        Task(
                            kind="review_stale_dependent",
                            owner=p.memory.owner,
                            target=dep,
                            payload={"source": str(p.rev), "reason": p.reason},
                            created_by=actor,
                            created_at=now,
                        ),
                    ),
                ]
        elif isinstance(p, PreparedLink):
            body.append(Relate(p.relation, p.source_rev, p.target_rev, _link_edge(p, actor, now)))
        else:
            body.append(
                Create.of(
                    RecordId("annotation", new_id()),
                    Annotation(target=p.target, kind=p.kind, text=p.text, by=actor, at=now),
                )
            )
    return [*guards, *body]


def _edge(code: Any, reason: str | None, actor: RecordId, now: datetime) -> dict[str, Any]:
    edge: dict[str, Any] = {"reason_code": code, "by": actor, "at": now}
    if reason:
        edge["reason"] = reason
    return edge


def _link_edge(p: PreparedLink, actor: RecordId, now: datetime) -> dict[str, Any]:
    edge: dict[str, Any] = {"by": actor, "at": now}
    if p.reason and p.relation != "promoted_to":
        edge["reason"] = p.reason
    return edge


def proposal_write(
    *,
    proposal: RecordId,
    actor: RecordId,
    owner: RecordId,
    space: str,
    permissions: Sequence[str],
    status: str,
    decision: Decision,
    reason: str | None,
    ops: list[dict[str, Any]],
    findings: Sequence[Finding],
    now: datetime,
    key: str | None,
    note: str | None,
    result: dict[str, Any],
    decided_by: RecordId | None = None,
) -> Write:
    return Create.of(
        proposal,
        Proposal(
            actor=actor,
            owner=owner,
            space=space,  # type: ignore[arg-type]
            permissions=sorted(permissions),
            status=status,  # type: ignore[arg-type]
            decision=decision,
            decision_reason=reason,
            submitted_at=now,
            decided_at=None if status == "pending" else now,
            decided_by=decided_by,
            idempotency_key=key,
            note=note,
            ops=ops,
            findings=[f.model_dump(mode="json") for f in findings],
            result=result,
        ),
    )
