"""Memory kinds: one immutable revision table per kind."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from ontyra.models.anchors import Anchor, Qualifier
from ontyra.models.base import (
    ActorRef,
    ConceptRef,
    EpisodeRef,
    MemoryRef,
    Model,
    Record,
    SubjectRef,
)
from ontyra.models.registry import LifecyclePolicy, registry
from ontyra.models.sources import SourceRef

KindName = Literal["fact", "directive", "decision", "event", "procedure", "task", "lesson"]
Scope = Literal["contextual", "universal", "topical", "reference"]
# What a memory that cites an external source is by default: reference material, looked up on
# demand and never delivered unasked. Rules, procedures and lessons keep the ordinary default.
REFERENCE_KINDS: tuple[KindName, ...] = ("fact", "decision", "event")


class RevisionBase(Record):
    """Fields common to every kind's revision. All content is write-once."""

    memory: MemoryRef
    seq: int
    text: str
    recorded_at: datetime
    proposed_by: ActorRef
    confidence: float = 1.0
    expires_at: datetime | None = None
    about: list[ConceptRef] = []  # concepts this is about (ontology links)
    anchors: list[Anchor] = []  # where it applies; empty = everywhere the owner is active
    qualifiers: list[Qualifier] = []  # under which circumstances (with roles)
    sources: list[SourceRef] = []  # external sources it cites (write-once)
    # `universal`: holds whatever the context (a standing preference, a global prohibition) and
    # is always part of a briefing or check. `contextual` (default): holds where its anchors,
    # qualifiers or subject (`about`) tie it to the context; with none of those it is unscoped
    # (flagged when written) and reaches an agent only through recall of its text. `topical`:
    # reaches an agent only when the context ties to it (an anchor, or an `about` concept the
    # work, the place or the message is about), whatever its text matches: knowledge of one
    # module or project that should not surface elsewhere. `reference`: never delivered by recall,
    # briefing or check; found only by looking it up (`consult`, `search`): documentation and
    # notes taken from sources, which is what a sourced fact is unless it says otherwise.
    scope: Scope = "contextual"


@registry.kind("fact", LifecyclePolicy(supersedes_by_edge=True))
class FactRev(RevisionBase):
    subject: SubjectRef
    predicate: str
    object: str | SubjectRef


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
    evidence: list[EpisodeRef] = []


_BOOKKEEPING = {"memory", "seq", "recorded_at", "proposed_by"}
_HINTS = {"subject": "concept id, e.g. concept:python", "object": "text or a concept id"}
_WHEN = {
    "fact": "a standing statement about the world or the setup",
    "directive": "a rule for how you should behave; say where it applies: `scope: universal`, or "
    "anchors / about",
    "decision": "a choice that was made, with what it was chosen over",
    "event": "something that happened at a particular time",
    "procedure": "ordered steps to follow: ONE memory for the whole procedure",
    "task": "something to do later (a reminder)",
    "lesson": "what a failure taught, to apply next time",
}
_SKIPPED = {"evidence"}  # a lesson's own list; evidence goes on the op, not in `fields`


def _type_hint(annotation: object) -> str:
    """A short, model-readable type: `must|should|prefer`, `list of {text}`, `ISO datetime`."""
    from typing import get_args, get_origin

    if get_origin(annotation) is Literal:
        return "|".join(str(a) for a in get_args(annotation))
    args = [a for a in get_args(annotation) if a is not type(None)]
    if get_origin(annotation) is list and args:
        inner = args[0]
        fields = getattr(inner, "model_fields", None)
        if fields:
            return "list of {" + ", ".join(fields) + "}"
        return f"list of {_type_hint(inner)}"
    if len(args) == 1 and args[0] is not annotation:
        return _type_hint(args[0])
    if annotation is datetime:
        return "ISO datetime"
    return getattr(annotation, "__name__", str(annotation))


def fields_guide(kind: str | None = None) -> str:
    """What `fields` takes for each kind (or one), derived from the revision models so the
    text shown to agents cannot drift. Required fields first; optional ones in brackets."""
    lines: list[str] = []
    for name, spec in registry.kinds.items():
        if kind is not None and name != kind:
            continue
        required: list[str] = []
        optional: list[str] = []
        for field, info in spec.model.model_fields.items():
            if field in _BOOKKEEPING | _SKIPPED:
                continue
            if field in RevisionBase.model_fields and field != "text":
                continue
            hint = _HINTS.get(field) or _type_hint(info.annotation)
            label = field if hint in ("str", "float") else f"{field} ({hint})"
            (required if info.is_required() else optional).append(label)
        when = f" ({_WHEN[name]})" if name in _WHEN else ""
        lines.append(
            f"{name}{when}: "
            + ", ".join(required)
            + (f" [{', '.join(optional)}]" if optional else "")
        )
    return "\n".join(lines)
