"""Request and response vocabulary of recall and exploration (plans/03)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field

from ontyra.models.anchors import Scalar
from ontyra.models.base import Model, RecordId
from ontyra.models.kinds import KindName
from ontyra.models.sources import SourceRef


class SourceTier(Model):
    """How far a kind of source is trusted: URIs starting with `match` (canonical form, e.g.
    `https://arxiv.org/` or `file://host/docs/`) are of this tier, scored by `weight`."""

    match: str = Field(min_length=3)
    tier: str = Field(min_length=1, max_length=40)
    weight: float = Field(default=1.0, ge=0.0, le=2.0)


class RecallSettings(Model):
    """Recall tuning (`ONTYRA_RECALL__*`). Defaults are hand-set starting points; the benchmark
    suite is where they get tuned."""

    engine: Literal["legacy", "fusion"] = "legacy"  # which recall engine serves `recall`
    machine: str = "localhost"  # host id for `file:` URIs without one
    candidates: int = 40  # per lane (legacy engine)
    # --- fusion engine (plans/21) ---
    overfetch: int = 150  # per lane, before applicability filtering
    fusion_alpha: float = 0.2  # weight of the lexical score; 1 - alpha goes to the dense score
    fusion_baseline: Literal["mean", "min"] = "mean"  # dense score = (cos - base) / (top - base)
    fusion_margin: float = 0.1  # keep candidates within this of the best fused score
    calibrated_for: str | None = None  # the embedder the thresholds were tuned for (calibrate)
    abstain_cosine: float = 0.0  # stay silent when the best cosine is below this (per embedder)
    concept_boost: float = 0.0  # added to the fused score, times the concept lane weight
    rerank_candidates: int = 30  # how many top candidates the reranker sees
    rerank_weight: float = 0.5  # share of the reranker probability in the final score
    # which vetting levels recall may use: `auto` (default: what policy committed without a human
    # looking is used, marked "unvetted") or `vetted` (only what a human wrote or approved)
    min_vetting: Literal["auto", "vetted"] = "auto"
    # --- decision model (optional; ONTYRA_DECISION__* selects it, plans/21) ---
    decision_candidates: int = 8  # top candidates the decision model is asked about
    decision_weight: float = 0.5  # share of its probability in the final score
    decision_margin: float = 0.15  # keep candidates within this of the best blended score
    trace_candidates: int = 30  # candidates whose lane scores a recall trace keeps (0: none)
    trace_retention_days: int = 180  # traces older than this are deleted unless feedback cites them
    # Skip the lookup for a pure pleasantry (needs the decision model): off until enabled.
    filler_gate: bool = False
    # p(filler) at or above this skips recall (no real request was skipped at 0.7 in the benchmark)
    filler_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    filler_max_words: int = Field(default=8, ge=1)  # only messages this short are asked about
    relevance_threshold: float = 0.25  # input-driven recall stays silent below this
    vector_floor: float = 0.3  # cosine at or below this counts as no similarity
    lane_weights: dict[str, float] = {"lexical": 1.0, "vector": 1.0, "concept": 0.9}
    kind_weights: dict[str, float] = {
        "directive": 1.2,
        "fact": 1.0,
        "decision": 1.0,
        "lesson": 1.0,
        "procedure": 0.9,
        "task": 0.8,
        "event": 0.6,
    }
    anchor_bonus: float = 0.25  # anchored items: 1 + bonus / (1 + distance)
    sufficient_boost: float = 1.15
    demote_factor: float = 0.8
    event_half_life_days: float = 30.0
    # --- knowledge from sources ---
    # tokens for memories that cite a source, on top of the main budget (they do not crowd out
    # directives); 0 gives them no room of their own
    knowledge_budget_tokens: int = 600
    # deployment's judgement of sources, first prefix match wins: `weight` multiplies the score
    source_tiers: list[SourceTier] = []
    corroboration_boost: float = 0.05  # per extra distinct source (up to 3)
    source_stale_days: int = 365  # a caveat when the source was last read longer ago (0: never)
    default_budget_tokens: int = 1200
    briefing_budget_tokens: int = 2000


Section = Literal["must_follow", "applicable", "context", "knowledge", "how_to", "open_tasks"]


class Signal(Model):
    """An observation about the environment, by concept id or label ("python", 3.12)."""

    concept: str
    value: Scalar | None = None
    unit: str | None = None


class RecallContext(Model):
    """Where and in what circumstances the agent is working. All parts are optional."""

    uris: list[str] = []  # cwd, repo root, files touched, `user@host`, harness id…
    signals: list[Signal] = []  # os, shell, language and version, tools present…
    session: str | None = None  # enables the no-repeat ledger and correction notices


class RecallRequest(Model):
    context: RecallContext = RecallContext()
    query: Annotated[str | None, Field(max_length=20_000)] = None  # the user message / task text
    hints: Annotated[
        list[Annotated[str, Field(max_length=200)]], Field(max_length=50)
    ] = []  # keywords the host agent already inferred
    kinds: list[KindName] = []  # restrict to these kinds (default: all)
    budget_tokens: Annotated[int | None, Field(ge=1, le=100_000)] = None
    profile: Literal["compact", "rich"] = "rich"
    redeliver: bool = False  # ignore the ledger (e.g. after context compaction)
    decide: bool = True  # False: skip the decision model for this request even if configured
    gate: bool = True  # False: look up even a pleasantry (skips the filler gate)


class RecallItem(Model):
    memory: RecordId
    revision: RecordId
    kind: KindName
    text: str
    strength: str | None = None  # directives: must / should / prefer
    polarity: str | None = None  # directives: do / avoid
    score: float
    relevance: float
    anchor: str | None = None  # the anchor that matched, if any
    anchor_distance: int | None = None
    why: list[str] = []
    caveats: list[str] = []  # "only if linux", "verify prerequisite: conda"
    checks: list[str] = []  # prerequisites the agent should verify
    flags: list[str] = []  # stale, low_confidence, location_unknown, triggered…
    confidence: float = 1.0
    valid_from: datetime | None = None
    detail: dict[str, Any] = {}  # the kind's own fields worth showing (rule, steps, due…)
    sources: list[SourceRef] = []  # external sources it cites (read them yourself to check)
    vetting: Literal["vetted", "auto"] = "vetted"  # `auto`: committed without a human looking


class More(Model):
    section: str
    count: int
    hint: str


class Notice(Model):
    kind: Literal["superseded", "retracted"]
    memory: RecordId
    message: str
    replaced_by: RecordId | None = None


class Caution(Model):
    memory: RecordId | None = None
    message: str


class RecallPack(Model):
    path: Literal["recall", "briefing", "check"]
    must_follow: list[RecallItem] = []
    applicable: list[RecallItem] = []
    context: list[RecallItem] = []
    knowledge: list[RecallItem] = []  # what cites a source (own token budget)
    how_to: list[RecallItem] = []
    open_tasks: list[RecallItem] = []
    cautions: list[Caution] = []
    more: list[More] = []
    notices: list[Notice] = []
    unresolved: list[str] = []  # context signals no concept could be found for
    affordances: list[str] = []
    silent: bool = True  # nothing cleared the threshold: inject nothing
    tokens: int = 0
    rendered: str = ""
    trace: RecordId | None = None

    def items(self) -> list[RecallItem]:
        return [
            *self.must_follow,
            *self.applicable,
            *self.context,
            *self.knowledge,
            *self.how_to,
            *self.open_tasks,
        ]


class CheckResult(Model):
    verdict: Literal["clear", "caution"]
    summary: str
    pack: RecallPack


# ---- exploration ------------------------------------------------------------------------


class SearchRequest(Model):
    query: Annotated[str, Field(max_length=2000)]
    context: RecallContext = RecallContext()
    kinds: list[KindName] = []
    concept: str | None = None  # filter: about this concept (or anything it expands to)
    limit: Annotated[int, Field(ge=1, le=200)] = 20


class SearchHit(Model):
    memory: RecordId
    revision: RecordId
    kind: KindName
    text: str
    score: float
    why: list[str] = []
    applies: Literal["yes", "no", "unknown"] = "unknown"


class SearchResult(Model):
    query: str
    hits: list[SearchHit]
    facets: dict[str, dict[str, int]]  # facet name → value → count
    affordances: list[str] = []


class AskRequest(Model):
    question: Literal[
        "applies_here",
        "supersession_chain",
        "absence",
        "conflicts",
        "changes_since",
        "as_of",
        "prerequisites_of",
        "explain",
    ]
    memory: RecordId | None = None
    topic: Annotated[str | None, Field(max_length=500)] = None
    since: datetime | None = None
    at: datetime | None = None  # as_of: the moment to look at
    trace: RecordId | None = None
    context: RecallContext = RecallContext()


class AskResult(Model):
    question: str
    answer: dict[str, Any]
    summary: str
    affordances: list[str] = []


class Overview(Model):
    counts: dict[str, int]
    concepts: list[str]
    anchor_chain: list[str]
    qualifiers_observed: list[str]
    recent: list[str]
    verbs: list[str]
