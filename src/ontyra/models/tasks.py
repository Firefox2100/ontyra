"""Maintenance tasks: work the daemon finds but cannot judge itself, done by the host agent or a
human. A task carries a card (what to look at, the choices); its result is validated."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from ontyra.models.base import ActorRef, Model, OwnerRef, RecordId
from ontyra.models.core import TaskKind

# the kinds an agent may claim and finish (the review_* kinds of proposals and ontology changes
# are decided through their own endpoints, by a human)
MAINTENANCE_KINDS: tuple[TaskKind, ...] = (
    "consolidate",
    "review_contradiction",
    "review_stale_dependent",
    "review_feedback",
    "expire_check",
    "review_concept_drift",
)
Status = Literal["pending", "claimed", "done", "failed", "parked"]
Decision = Literal["merge", "supersede", "retract", "revise", "keep", "distinct", "defer"]


class TaskView(Model):
    id: RecordId
    kind: TaskKind
    owner: OwnerRef
    status: Status
    target: RecordId | None = None
    card: dict[str, Any] = {}
    result: dict[str, Any] = {}
    claimed_by: ActorRef | None = None
    created_at: datetime
    updated_at: datetime | None = None


class ResolveRequest(Model):
    """Ask the daemon to do what the card proposes (as a proposal from you): `merge` keeps the
    oldest memory of a duplicate cluster and supersedes the others; `supersede` lets the newer
    memory of an update replace the older; `retract` retracts the memory of a feedback or expiry
    task."""

    decision: Literal["merge", "supersede", "retract"]


class TaskResult(Model):
    """How a maintenance task was resolved. `merge` (a duplicate cluster), `supersede` (an
    update), `retract` and `revise` (a memory reported wrong or expiring) must cite the proposal
    that did it; `distinct` (two memories differ) and `keep` (the memory stands) are remembered and
    not raised again; `defer` parks the task for later."""

    decision: Decision
    proposal: RecordId | None = None
    note: str | None = None


class DetectReport(Model):
    created: list[TaskView] = []
    existing: int = 0  # findings that already have a task
    closed: int = 0  # tasks whose memories changed in the meantime
    reopened: int = 0  # tasks whose held proposal was rejected, back in the queue
    skipped: int = 0  # findings in memory the caller may not change
    model_errors: int = 0


class TaskSettings(Model):
    """Maintenance detection (`ONTYRA_TASKS__*`)."""

    expiry_warn_days: int = 14  # a memory that expires within this many days is checked
    detect_every_hours: float = 24.0  # the daemon runs detection this often (0: only on request)
    detect_first_after_minutes: float = 10.0  # ... and for the first time this long after start
    refine_cap: int = 200  # refinement tasks queued per split concept per pass (plans/23)
