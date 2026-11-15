"""Request and response vocabulary of recall and exploration (plans/03)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from ontyra.models.anchors import Scalar
from ontyra.models.base import Model, RecordId
from ontyra.models.kinds import KindName


class RecallSettings(Model):
    """Recall tuning (`ONTYRA_RECALL__*`). Defaults are hand-set starting points; the benchmark
    suite is where they get tuned."""

    machine: str = "localhost"  # host id for `file:` URIs without one
    candidates: int = 40  # per lane
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
    default_budget_tokens: int = 1200
    briefing_budget_tokens: int = 2000


Section = Literal["must_follow", "applicable", "context", "how_to", "open_tasks"]


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
    query: str | None = None  # the user message / task text
    hints: list[str] = []  # keywords the host agent already inferred
    kinds: list[KindName] = []  # restrict to these kinds (default: all)
    budget_tokens: int | None = None
    profile: Literal["compact", "rich"] = "rich"
    redeliver: bool = False  # ignore the ledger (e.g. after context compaction)


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
        return [*self.must_follow, *self.applicable, *self.context, *self.how_to, *self.open_tasks]


class CheckResult(Model):
    verdict: Literal["clear", "caution"]
    summary: str
    pack: RecallPack


# ---- exploration ------------------------------------------------------------------------


class SearchRequest(Model):
    query: str
    context: RecallContext = RecallContext()
    kinds: list[KindName] = []
    concept: str | None = None  # filter: about this concept (or anything it expands to)
    limit: int = 20


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
        "prerequisites_of",
        "explain",
    ]
    memory: RecordId | None = None
    topic: str | None = None
    since: datetime | None = None
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
