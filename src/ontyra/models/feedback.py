"""Feedback on recalled memories, and the reranker runs trained from it (plans/21).

Feedback is kept, not folded into weights: every event is stored with its source and time, a
conflict resolver decides which events still count, and expiry retires old ones. A trainable
reranker is retrained from the surviving events when the user chooses to.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from ontyra.models.base import ActorRef, Indexed, MemoryRef, Model, OwnerRef, Record, RecordId
from ontyra.models.registry import registry

FeedbackLabel = Literal["applied", "helpful", "missed", "irrelevant", "outdated", "wrong"]
# What each label says about (query, memory): relevant (1) or not relevant (0).
LABEL_RELEVANCE: dict[str, float] = {
    "applied": 1.0,
    "helpful": 1.0,
    "missed": 1.0,  # "this should have come back for this query"
    "irrelevant": 0.0,
    "outdated": 0.0,
    "wrong": 0.0,
}
FeedbackState = Literal["active", "superseded", "expired", "retracted"]


@registry.table("feedback")
class Feedback(Record):
    """One judgement of one memory for one query (kept until it expires or is superseded)."""

    owner: Annotated[OwnerRef, Indexed()]
    memory: Annotated[MemoryRef, Indexed()]
    revision: RecordId | None = None  # the revision judged (its text is what a reranker sees)
    query: str
    label: FeedbackLabel
    relevance: float  # derived from the label: 1 relevant, 0 not
    source: ActorRef
    source_kind: str  # `human`, `agent`, ... (trust is configured per kind)
    weight: float  # trust of the source at the time
    created_at: datetime
    expires_at: datetime | None = None
    state: Annotated[FeedbackState, Indexed()] = "active"
    superseded_by: RecordId | None = None
    session: str | None = None
    trace: RecordId | None = None
    note: str | None = None


@registry.table("reranker_run")
class RerankerRun(Record):
    """One training run of a reranker: what went in, how it did, whether it was activated."""

    reranker: str
    version: str
    examples: int
    metrics: dict[str, Any] = {}
    activated: bool
    started_by: ActorRef
    created_at: datetime
    note: str | None = None
    gate: dict[str, Any] | None = None  # the promotion gate's verdict, when one is configured


# ---- service-facing models -----------------------------------------------------------------


class FeedbackRequest(Model):
    memory: MemoryRef
    query: str
    label: FeedbackLabel
    revision: RecordId | None = None  # default: the memory's current revision
    session: str | None = None
    trace: RecordId | None = None
    note: str | None = None
    expires_in_days: int | None = None  # default: the configured time to live


class FeedbackView(Model):
    id: RecordId
    owner: RecordId
    memory: RecordId
    query: str
    label: FeedbackLabel
    state: FeedbackState
    source: RecordId
    source_kind: str
    weight: float
    created_at: datetime
    expires_at: datetime | None
    note: str | None = None


class FeedbackSummary(Model):
    total: int
    active: int
    superseded: int
    expired: int
    retracted: int
    by_label: dict[str, int]
    trainable_examples: int  # active events that map to a training pair


class RerankExample(Model):
    """A labelled (query, text) pair for training a reranker."""

    query: str
    text: str
    relevance: float  # 1 relevant, 0 not
    weight: float = 1.0


class TrainOptions(Model):
    epochs: int = 4  # at most; the best epoch on held-out data is kept
    learning_rate: float = 2e-5
    batch_size: int = 16
    validation_fraction: float = 0.15
    seed: int = 13
    min_examples: int | None = None  # default: the configured minimum
    activate: bool = True  # make the result the active version if it passes the gate


class GateVerdict(Model):
    """A promotion gate's decision on a freshly trained version."""

    passed: bool
    detail: dict[str, Any] = {}


class TrainResult(Model):
    version: str
    examples: int
    metrics: dict[str, Any] = {}
    activated: bool = False
    note: str | None = None


class RerankerStatus(Model):
    reranker: str | None
    trainable: bool
    version: str | None
    feedback: FeedbackSummary
    min_examples: int
    runs: list[dict[str, Any]] = []
