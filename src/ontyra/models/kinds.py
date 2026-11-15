"""Memory kinds: one immutable revision table per kind (see plans/01)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from ontyra.models.base import Model, Record, Ref
from ontyra.models.registry import LifecyclePolicy, registry

KindName = Literal["fact", "directive", "decision", "event", "procedure", "task", "lesson"]


class RevisionBase(Record):
    """Fields common to every kind's revision. All content is write-once."""

    memory: Ref("memory")  # type: ignore[valid-type]
    seq: int
    text: str
    recorded_at: datetime
    proposed_by: Ref("actor")  # type: ignore[valid-type]
    confidence: float = 1.0
    expires_at: datetime | None = None


@registry.kind("fact", LifecyclePolicy(supersedes_by_edge=True))
class FactRev(RevisionBase):
    subject: Ref("concept", "resource")  # type: ignore[valid-type]
    predicate: str
    object: str | Ref("concept", "resource")  # type: ignore[valid-type]


@registry.kind("directive", LifecyclePolicy(evidence_gated=True))
class DirectiveRev(RevisionBase):
    strength: Literal["must", "should", "prefer"]
    polarity: Literal["do", "avoid"] = "do"
    rule: str
    rationale: str | None = None


@registry.kind("decision")
class DecisionRev(RevisionBase):
    chosen: str
    alternatives: list[str] = []
    rationale: str
    decided_at: datetime


@registry.kind("event", LifecyclePolicy(relevance_decay=True))
class EventRev(RevisionBase):
    occurred_at: datetime
    participants: list[str] = []
    outcome: str | None = None


class Step(Model):
    text: str
    optional: bool = False


@registry.kind("procedure", LifecyclePolicy(evidence_gated=True))
class ProcedureRev(RevisionBase):
    trigger: str
    steps: list[Step]
    preconditions: list[str] = []


@registry.kind("task")
class TaskRev(RevisionBase):
    due: datetime | None = None
    state: Literal["open", "done", "cancelled"] = "open"


@registry.kind("lesson", LifecyclePolicy(evidence_gated=True))
class LessonRev(RevisionBase):
    situation: str
    insight: str
    do_instead: str | None = None
    evidence: list[Ref("episode")] = []  # type: ignore[valid-type]
