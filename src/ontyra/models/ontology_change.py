"""Ontology change proposals: how concepts are added, merged and retired by agents.

A change is a typed request plus a rationale. A human's change applies at once; an agent's waits
for review unless its op is listed in `ONTYRA_ONTOLOGY__AUTO_APPLY`. Applied changes are kept
(with who decided and why), so the ontology's history is auditable.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field

from ontyra.models.base import ActorRef, Indexed, Model, OwnerRef, Record, RecordId
from ontyra.models.ontology import ConceptDefinition, RelationRequest
from ontyra.models.registry import registry

ChangeStatus = Literal["pending", "changes_requested", "applied", "rejected", "withdrawn"]


class DefineChange(Model):
    op: Literal["define"] = "define"
    concept: ConceptDefinition


class RelateChange(Model):
    op: Literal["relate"] = "relate"
    relation: RelationRequest


class AliasChange(Model):
    op: Literal["add_alias"] = "add_alias"
    concept: str  # id, label or alias
    alias: str


class DeprecateChange(Model):
    op: Literal["deprecate"] = "deprecate"
    concept: str
    replaced_by: str | None = None
    reason: str | None = None


class MergeChange(Model):
    op: Literal["merge"] = "merge"
    source: str  # becomes an alias of `into` and is deprecated, replaced by it
    into: str


class EditChange(Model):
    """Say the same thing better: a clearer label, a tighter definition, other names. The meaning
    must not change (otherwise define a new concept and deprecate this one); the id never does.
    The previous label stays as an alias."""

    op: Literal["edit"] = "edit"
    concept: str
    label: str | None = None
    definition: str | None = None  # an empty string clears it
    add_aliases: list[str] = []
    remove_aliases: list[str] = []


class UnrelateChange(Model):
    """End one relation (the edge is kept as history)."""

    op: Literal["unrelate"] = "unrelate"
    relation: RelationRequest


class ReparentChange(Model):
    """Replace one broader concept by another, atomically."""

    op: Literal["reparent"] = "reparent"
    concept: str
    relation: Literal["is_a", "part_of"] = "is_a"
    from_parent: str
    to_parent: str


class SplitChange(Model):
    """One concept turned out to be several: the source is deprecated (kept as a tombstone) and
    points to what it became. `into` are existing concepts (ids, labels) or new ones to define."""

    op: Literal["split"] = "split"
    source: str
    into: Annotated[list[ConceptDefinition | str], Field(min_length=2)]


class RestoreChange(Model):
    """Put a concept back to the fields it had at an earlier version (also how an edit, a
    deprecation or a merge is undone). It makes a new version; nothing is rewritten."""

    op: Literal["restore"] = "restore"
    concept: str
    version: int


Change = Annotated[
    DefineChange
    | RelateChange
    | AliasChange
    | DeprecateChange
    | MergeChange
    | EditChange
    | UnrelateChange
    | ReparentChange
    | SplitChange
    | RestoreChange,
    Field(discriminator="op"),
]


@registry.table("ontology_change")
class OntologyChange(Record):
    owner: Annotated[OwnerRef, Indexed()]
    change: dict[str, Any]  # a `Change`, as data
    op: Annotated[str, Indexed()]
    rationale: str
    status: Annotated[ChangeStatus, Indexed()] = "pending"
    proposed_by: ActorRef
    proposer_kind: str
    created_at: datetime
    decided_by: ActorRef | None = None
    decided_at: datetime | None = None
    reason: str | None = None
    advice: list[str] = []
    # The concepts it touched, and the changes that undo it (applied in order, as one
    # batch, by a revert). Nullable so changes stored before them still validate.
    touched: list[RecordId] | None = None
    undo: list[dict[str, Any]] | None = None
    # review workflow; nullable so changes stored before it still validate
    version: int | None = None
    history: list[dict[str, Any]] | None = None  # older versions: {version, change, by, at, why}
    comments: list[dict[str, Any]] | None = None  # {version, text, by, at}
    reverts: RecordId | None = None  # a revert: the change it undid
    reverted_by: RecordId | None = None  # the revert that undid this one


class ChangeRequest(Model):
    change: Change
    rationale: str = Field(description="Why: what you saw that makes this change right")


class AmendChange(Model):
    """Replace the change of a waiting ontology change: a new version, validated again."""

    change: Change
    why: str = Field(description="What you changed and why (kept with the older version)")
    expect_version: int | None = None


class Effects(Model):
    """What a change does (or would do), for a preview and for a reviewer."""

    summary: list[str] = []  # one line per effect
    touched: list[RecordId] = []
    memories: dict[str, int] = {}  # concept -> live memories directly about it


class ChangeResult(Model):
    id: RecordId
    status: ChangeStatus
    op: str
    advice: list[str] = []  # similar concepts, duplicates: read before approving
    reason: str | None = None
    effects: Effects | None = None


class ChangePreview(Model):
    """What a change would do, without proposing it."""

    op: str
    advice: list[str] = []
    effects: Effects


class LiteChangeRequest(ChangeRequest):
    """The ontology change of the `lite` MCP profile: the common operations only. A weak
    tool-caller is unlikely to need `split`, `reparent`, `unrelate` or `restore`."""

    change: Annotated[  # type: ignore[assignment]
        DefineChange | RelateChange | AliasChange | DeprecateChange | MergeChange | EditChange,
        Field(discriminator="op"),
    ]
