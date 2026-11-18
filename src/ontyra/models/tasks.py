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
)
Status = Literal["pending", "claimed", "done", "failed", "parked"]
Decision = Literal["merge", "supersede", "distinct", "defer"]


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


class TaskResult(Model):
    """How a maintenance task was resolved. `merge` (a duplicate cluster) and `supersede` (an
    update) must cite the proposal that did it; `distinct` says the memories are different
    things and the finding is not raised again; `defer` parks the task for later."""

    decision: Decision
    proposal: RecordId | None = None
    note: str | None = None


class DetectReport(Model):
    created: list[TaskView] = []
    existing: int = 0  # findings that already have a task
    closed: int = 0  # tasks whose memories changed in the meantime
    skipped: int = 0  # findings in memory the caller may not change
    model_errors: int = 0
