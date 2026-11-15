"""Shared (non-kind) tables and edges."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from ontyra.models.base import Indexed, Record, Ref
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


@registry.table("episode", immutable=True)
class Episode(Record):
    occurred_at: datetime
    actor: Ref("actor")  # type: ignore[valid-type]
    owner: Ref("owner")  # type: ignore[valid-type]
    session: str
    role: str
    content: str
    meta: dict[str, Any] = {}


@registry.table("memory")
class Memory(Record):
    """Item identity plus the mutable `current` pointer to a revision."""

    kind: KindName
    owner: Ref("owner")  # type: ignore[valid-type]
    current: Ref(REV_WILDCARD) | None = None  # type: ignore[valid-type]
    created_at: datetime


@registry.table("rev_state")
class RevState(Record):
    """Mutable status/validity of one (immutable) revision."""

    rev: Annotated[Ref(REV_WILDCARD), Indexed(unique=True)]  # type: ignore[valid-type]
    state: Literal["proposed", "tentative", "committed", "rejected"]
    lifecycle: Literal["active", "superseded", "retracted", "expired"] = "active"
    valid_from: datetime
    valid_to: datetime | None = None
    retracted_at: datetime | None = None
    retract_reason: str | None = None


# ---- edges between revisions (immutable, reasoned) -----------------------

_REV = (REV_WILDCARD,)


@registry.table("supersedes", immutable=True, edge=(_REV, _REV))
class Supersedes(Record):
    """`new_rev -> old_rev`: the edge is the explanation of what replaced what, and why."""

    reason_code: Literal[
        "corrected", "outdated", "refined", "merged", "resolved_contradiction", "source_retracted"
    ]
    reason: str | None = None
    by: Ref("actor")  # type: ignore[valid-type]
    at: datetime


@registry.table("contradicts", immutable=True, edge=(_REV, _REV))
class Contradicts(Record):
    reason: str | None = None
    by: Ref("actor")  # type: ignore[valid-type]
    at: datetime


@registry.table("overrides", immutable=True, edge=(_REV, _REV))
class Overrides(Record):
    """Narrower-anchor item wins over a broader one without invalidating it."""

    reason: str | None = None
    by: Ref("actor")  # type: ignore[valid-type]
    at: datetime


@registry.table("refines", immutable=True, edge=(_REV, _REV))
class Refines(Record):
    reason: str | None = None
    by: Ref("actor")  # type: ignore[valid-type]
    at: datetime


@registry.table("derived_from", immutable=True, edge=(_REV, ("episode", REV_WILDCARD)))
class DerivedFrom(Record):
    """Provenance; drives cascade (dependents become stale when a source is retracted)."""

    at: datetime


@registry.table(
    "promoted_to", immutable=True, edge=(("lesson_rev",), ("directive_rev", "procedure_rev"))
)
class PromotedTo(Record):
    by: Ref("actor")  # type: ignore[valid-type]
    at: datetime
