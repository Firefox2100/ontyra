"""Shared (non-kind) tables and edges."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from ontyra.models.base import (
    ActorRef,
    Indexed,
    MemoryRef,
    OwnerRef,
    Record,
    RecordId,
    RevRef,
    UserRef,
)
from ontyra.models.kinds import KindName
from ontyra.models.registry import REV_WILDCARD, registry


@registry.table("actor")
class Actor(Record):
    kind: Literal["human", "agent", "tool", "service", "model"]
    name: Annotated[str, Indexed(unique=True)]
    harness: str | None = None
    trust: float = 0.5
    meta: dict[str, Any] = {}


@registry.table("owner")
class Owner(Record):
    kind: Literal["global", "user", "agent"]
    name: str


Permission = Literal["global_write", "admin"]


@registry.table("user")
class User(Record):
    """An account (multi-user mode). It owns its memories through `owner`; `global_write` lets
    it change the shared global memory and ontology, `admin` lets it manage accounts."""

    name: Annotated[str, Indexed(unique=True)]
    owner: OwnerRef
    permissions: list[Permission] = []
    disabled: bool = False
    created_at: datetime


@registry.table("credential")
class Credential(Record):
    """One way a user authenticates: themselves, their Codex, their Claude Code... Only the
    SHA-256 of the token is stored. `kind` is the actor kind the credential acts as: a `human`
    credential may review and (by policy) commit at once; an `agent` one is held for review."""

    user: UserRef
    name: str
    kind: Literal["human", "agent"]
    harness: str | None = None
    token_hash: Annotated[str, Indexed(unique=True)]
    token_hint: str
    created_at: datetime
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None


@registry.table("episode", immutable=True)
class Episode(Record):
    occurred_at: datetime
    actor: ActorRef
    owner: OwnerRef
    session: str
    role: str
    content: str
    meta: dict[str, Any] = {}


@registry.table("memory")
class Memory(Record):
    """Item identity plus the mutable `current` pointer to a revision."""

    kind: KindName
    owner: OwnerRef
    current: RevRef | None = None
    status: Literal["active", "superseded", "retracted", "expired"] = "active"
    created_at: datetime


@registry.table("rev_state")
class RevState(Record):
    """Mutable status/validity of one (immutable) revision."""

    rev: Annotated[RevRef, Indexed(unique=True)]
    state: Literal["proposed", "tentative", "committed", "rejected"]
    lifecycle: Literal["active", "superseded", "retracted", "expired"] = "active"
    valid_from: datetime
    valid_to: datetime | None = None
    retracted_at: datetime | None = None
    retract_reason: str | None = None
    stale: bool = False  # a source it was derived from was retracted
    stale_reason: str | None = None


# ---- edges between revisions (immutable, reasoned) -----------------------

_REV = (REV_WILDCARD,)


@registry.table("supersedes", immutable=True, edge=(_REV, _REV))
class Supersedes(Record):
    """`new_rev -> old_rev`: the edge is the explanation of what replaced what, and why."""

    reason_code: Literal[
        "corrected", "outdated", "refined", "merged", "resolved_contradiction", "source_retracted"
    ]
    reason: str | None = None
    by: ActorRef
    at: datetime


@registry.table("contradicts", immutable=True, edge=(_REV, _REV))
class Contradicts(Record):
    reason: str | None = None
    by: ActorRef
    at: datetime


@registry.table("overrides", immutable=True, edge=(_REV, _REV))
class Overrides(Record):
    """Narrower-anchor item wins over a broader one without invalidating it."""

    reason: str | None = None
    by: ActorRef
    at: datetime


@registry.table("refines", immutable=True, edge=(_REV, _REV))
class Refines(Record):
    reason: str | None = None
    by: ActorRef
    at: datetime


@registry.table("derived_from", immutable=True, edge=(_REV, ("episode", REV_WILDCARD)))
class DerivedFrom(Record):
    """Provenance; drives cascade (dependents become stale when a source is retracted)."""

    at: datetime


@registry.table(
    "promoted_to", immutable=True, edge=(("lesson_rev",), ("directive_rev", "procedure_rev"))
)
class PromotedTo(Record):
    by: ActorRef
    at: datetime


# ---- workflow and side data ----------------------------------------------


@registry.table("proposal")
class Proposal(Record):
    """An atomic batch of operations submitted by one actor (see plans/01, plans/10)."""

    actor: ActorRef
    owner: OwnerRef  # the submitter's own memory
    space: Literal["own", "global"] = "own"  # where its additions go
    permissions: list[str] = []  # the submitter's permissions when it was made
    status: Literal["pending", "committed", "rejected"]
    decision: Literal["auto_commit", "review", "reject"]  # policy outcome at submission
    decision_reason: str | None = None
    submitted_at: datetime
    decided_at: datetime | None = None
    decided_by: ActorRef | None = None
    idempotency_key: Annotated[str | None, Indexed()] = None
    note: str | None = None
    ops: list[dict[str, Any]]
    findings: list[dict[str, Any]] = []
    result: dict[str, Any] = {}


@registry.table("annotation")
class Annotation(Record):
    """Freely mutable commentary on any record (comments, tags, flags)."""

    target: RecordId
    kind: Literal["comment", "tag", "flag"]
    text: str
    by: ActorRef
    at: datetime


@registry.table("usage")
class Usage(Record):
    """Recall statistics for one memory (feeds relevance, never touches revisions)."""

    memory: Annotated[MemoryRef, Indexed(unique=True)]
    recalls: int = 0
    last_recalled_at: datetime | None = None
    helpful: int = 0
    unhelpful: int = 0


TaskKind = Literal[
    "review_proposal",
    "review_stale_dependent",
    "review_contradiction",
    "review_ontology_change",
    "review_concept_drift",
    "consolidate",
    "summarize",
    "refresh_index",
    "expire_check",
    "resolve_mention",
]


@registry.table("task")
class Task(Record):
    """Work that needs judgment: queued by the service, performed by a human or agent."""

    kind: TaskKind
    status: Literal["pending", "claimed", "done", "failed", "parked"] = "pending"
    target: RecordId | None = None
    payload: dict[str, Any] = {}
    result: dict[str, Any] = {}
    min_tier: int | None = None
    created_by: ActorRef
    claimed_by: ActorRef | None = None
    created_at: datetime
    updated_at: datetime | None = None
