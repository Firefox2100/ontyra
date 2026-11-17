"""The vocabulary of proposals: operations, findings and results (transport-visible models)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field

from ontyra.models.base import EpisodeRef, MemoryRef, Model, RecordId
from ontyra.models.kinds import KindName
from ontyra.models.policy import Decision

ReasonCode = Literal[
    "corrected", "outdated", "refined", "merged", "resolved_contradiction", "source_retracted"
]


class Evidence(Model):
    """A source episode; if `quote` is given it must literally occur in that episode."""

    episode: EpisodeRef
    quote: str | None = None


class AddMemory(Model):
    """Create a new memory (item + first revision)."""

    op: Literal["add"] = "add"
    ref: str | None = None  # alias other ops in this proposal can point at
    kind: KindName
    fields: dict[str, Any]  # `text` plus the kind's own fields (see models.kinds)
    valid_from: datetime | None = None
    evidence: list[Evidence] = []
    derived_from: list[str | RecordId] = []  # memories (or refs) this was derived from


class ReviseMemory(Model):
    """New revision of an existing memory; `fields` overlay the current revision."""

    op: Literal["revise"] = "revise"
    memory: MemoryRef
    fields: dict[str, Any]
    reason_code: ReasonCode = "refined"
    reason: str | None = None
    evidence: list[Evidence] = []


class SupersedeMemory(Model):
    """`new` (a `ref` from this proposal, or an existing memory id) replaces `target`."""

    op: Literal["supersede"] = "supersede"
    new: str | RecordId
    target: MemoryRef
    reason_code: ReasonCode
    reason: str | None = None


class RetractMemory(Model):
    """Declare a memory wrong/polluted. Dependents derived from it are flagged stale."""

    op: Literal["retract"] = "retract"
    memory: MemoryRef
    reason: str


class LinkMemories(Model):
    op: Literal["link"] = "link"
    relation: Literal["contradicts", "overrides", "refines", "promoted_to"]
    source: str | RecordId  # `ref` or memory id
    target: str | RecordId
    reason: str | None = None


class Annotate(Model):
    op: Literal["annotate"] = "annotate"
    target: RecordId
    kind: Literal["comment", "tag", "flag"] = "comment"
    text: str


Op = Annotated[
    AddMemory | ReviseMemory | SupersedeMemory | RetractMemory | LinkMemories | Annotate,
    Field(discriminator="op"),
]


class ProposalRequest(Model):
    ops: Annotated[list[Op], Field(min_length=1)]
    # Where new memories go: the caller's own memory (default) or the shared global memory,
    # which needs the `global_write` permission. Owners are never named: they come from the
    # authenticated account. With multi-user mode off there is only the global memory.
    space: Literal["own", "global"] = "own"
    idempotency_key: str | None = None
    note: str | None = None


class Finding(Model):
    """One validation observation. `error` blocks the proposal; others are advice."""

    severity: Literal["error", "warning", "info"]
    code: str
    message: str
    op: int | None = None  # index into the request's ops
    data: dict[str, Any] = {}


class ProposalResult(Model):
    proposal: RecordId
    status: Literal["pending", "committed", "rejected"]
    decision: Decision
    reason: str | None = None
    findings: list[Finding] = []
    created: dict[str, RecordId] = {}  # op `ref` (or "op<N>") -> new memory id
    duplicate_of_idempotency_key: bool = False
