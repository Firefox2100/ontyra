"""Feedback on recalled memories, and the reranker runs trained from it.

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
FeedbackState = Literal["active", "superseded", "expired", "retracted", "vetoed"]


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
    veto_reason: str | None = None  # set when the event was vetoed (state `vetoed`)
    vetoed_by: ActorRef | None = None
    vetoed_at: datetime | None = None


@registry.table("learning_exclusion")
class LearningExclusion(Record):
    """Keeps feedback out of learning, durably.

    `quarantine` (an admin's call, for a source or a whole owner): the events do not count
    anywhere, the owner's own score layer included. `opt_out` (a user's own choice): the owner's
    text and verdicts never enter training; their own score layer still learns from them.
    """

    kind: Annotated[Literal["quarantine", "opt_out"], Indexed()]
    scope: Literal["source", "owner"]
    target: Annotated[RecordId, Indexed()]  # an actor (source) or an owner
    reason: str = ""
    by: ActorRef
    created_at: datetime
    state: Annotated[Literal["active", "lifted"], Indexed()] = "active"


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
    kind: Literal["retrain", "live"] = "retrain"


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
    veto_reason: str | None = None


class FeedbackSummary(Model):
    total: int
    active: int
    superseded: int
    expired: int
    retracted: int
    by_label: dict[str, int]
    trainable_examples: int  # active events that map to a training pair
    with_features: int = 0  # active events whose recall trace kept the memory's lane scores


class RerankExample(Model):
    """A labelled (query, text) pair for training a reranker."""

    query: str
    text: str
    relevance: float  # 1 relevant, 0 not
    weight: float = 1.0
    source: str = ""  # who judged it (feedback weight is capped per source)


class TrainingPolicy(Model):
    """What may train a reranker and how a new version is judged (from `ONTYRA_RERANK__*`)."""

    min_examples: int = 50
    pool: Literal["own", "all"] = "own"  # whose feedback trains: the caller's, or everyone's
    train_sources: Literal["human", "all"] = "all"  # whether agents' verdicts count
    source_cap: float = 0.5  # largest share of the weight one source may have
    anchor_ratio: float = 1.0  # clean benchmark pairs mixed in per feedback pair
    gate_tolerance: float = 0.01  # what a new version may lose on the trusted requests
    live: bool = False  # a live head edit from feedback (needs `pool="all"`: the head is shared)
    live_min_examples: int = 20  # usable pairs of both classes before a live edit is fitted
    live_every_seconds: float = 300.0  # feedback changes are batched into one refit this often
    live_max_distance: float = 0.25  # the edit's norm, at most this share of the head's norm


class GateCandidate(Model):
    text: str
    relevant: bool


class GateRequest(Model):
    """One request of the trusted set a retrained version must not do worse on."""

    query: str
    candidates: list[GateCandidate]


class TrainOptions(Model):
    epochs: int = 4  # at most; the best epoch on held-out data is kept
    learning_rate: float = 1e-4  # LoRA; the head learns faster
    batch_size: int = 8
    lora_rank: int = 8
    head_only: bool = False  # train the head alone (no LoRA)
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


class LiveResult(Model):
    """What a live head edit did: refitted from the version's own head and the examples that count,
    served unless it ranked the trusted requests worse (`reverted`)."""

    applied: bool
    reverted: bool = False
    distance: float = 0.0  # the edit's norm relative to the head's
    examples: int = 0
    metrics: dict[str, Any] = {}
    note: str | None = None


class RerankerStatus(Model):
    reranker: str | None
    trainable: bool
    version: str | None
    feedback: FeedbackSummary
    min_examples: int
    runs: list[dict[str, Any]] = []
    live: dict[str, Any] = {}  # the live head edit: enabled, distance, stale


# ---- reviewing examples --------------------------------------------------


class VetoRequest(Model):
    reason: str = ""


class OutlierView(Model):
    """One judged example, with why it deserves a look (the more reasons, the higher it ranks)."""

    feedback: FeedbackView
    text: str  # the judged revision's text
    model_score: float | None = None  # the reranker's probability, else the final score, at recall
    disagreement: float | None = None  # |verdict - model_score|, 0 agrees, 1 opposite
    reasons: list[str] = []  # disagrees | flipped | others_disagree | burst
    rank: float = 0.0


class ExclusionRequest(Model):
    kind: Literal["quarantine", "opt_out"] = "quarantine"
    scope: Literal["source", "owner"] = "source"
    target: RecordId | None = None  # default for an opt-out: the caller's own owner
    reason: str = ""


class ExclusionView(Model):
    id: RecordId
    kind: Literal["quarantine", "opt_out"]
    scope: Literal["source", "owner"]
    target: RecordId
    reason: str
    by: RecordId
    created_at: datetime
    state: Literal["active", "lifted"]


class OptOutRequest(Model):
    enabled: bool = True


# ---- the score layer ----------------------------------------------------


class LearningSettings(Model):
    """The score layer (`ONTYRA_LEARNING__*`): what an owner's own feedback may change in how
    recall ranks, without touching any model weights.

    Two corrections, both bounded and both recomputed from the feedback log (never stored): a
    *prior* per memory (decayed net verdicts, capped at `max_prior`) and an *online blend* (a
    logistic fit over the lane scores of the recalls that were judged, kept within `max_shift`
    of the plain ranking, and mixed in with a weight that grows with the evidence up to
    `max_mix`). A fixed share of the judged recalls (`canary_fraction`) is never trained on:
    after each refit the layer must rank them at least as well as the plain ranking, or it stays
    off. Needs the fusion engine; `enabled` is off by default.
    """

    enabled: bool = False
    min_examples: int = 30  # judged candidates (outside the canary) before the blend is fitted
    half_life_days: float = 90.0  # a verdict's weight halves every this many days
    max_prior: float = 0.1  # largest score change one memory's history may cause
    prior_scale: float = 2.0  # decayed net verdicts at which the prior reaches ~76% of its cap
    max_mix: float = 0.5  # share of the fitted score in the final one, at most
    mix_half: int = 100  # judged candidates at which the mix is half of `max_mix`
    max_shift: float = 1.0  # largest change of any fitted coefficient from the plain ranking
    l2: float = 0.5  # pull of the fit towards the plain ranking
    source_cap: float = 0.5  # largest share of the fit one source (actor) may have
    canary_fraction: float = 0.25  # share of the judged recalls held out as the canary
    canary_min_groups: int = 5  # recalls in the canary needed before the layer may turn on
    canary_tolerance: float = 0.0  # the layer may rank the canary this much worse and stay on
    cache_seconds: float = 300.0  # how long a computed state is reused (feedback resets it)


class LayerState(Model):
    """What the score layer is doing for one owner, and why."""

    enabled: bool
    status: Literal["off", "learning", "active", "reverted"]
    reason: str = ""
    examples: int = 0  # judged candidates the blend was fitted on
    groups: int = 0  # recalls they came from
    canary_groups: int = 0
    canary_base: float | None = None  # pairwise accuracy of the plain ranking on the canary
    canary_layer: float | None = None  # ... and with the layer
    mix: float = 0.0
    coefficients: dict[str, float] = {}
    priors: int = 0  # memories with a prior
