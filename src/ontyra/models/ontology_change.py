"""Ontology change proposals: how concepts are added, merged and retired by agents (plans/11).

A change is a typed request plus a rationale. A human's change applies at once; an agent's waits
for review unless its op is listed in `ONTYRA_ONTOLOGY__AUTO_APPLY`. Applied changes are kept
(with who decided and why), so the ontology's history is auditable.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field

from ontyra.models.base import ActorRef, Indexed, Model, Record, RecordId
from ontyra.models.ontology import ConceptDefinition, RelationRequest
from ontyra.models.registry import registry

ChangeStatus = Literal["pending", "applied", "rejected"]


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


class MergeChange(Model):
    op: Literal["merge"] = "merge"
    source: str  # becomes an alias of `into` and is deprecated, replaced by it
    into: str


Change = Annotated[
    DefineChange | RelateChange | AliasChange | DeprecateChange | MergeChange,
    Field(discriminator="op"),
]


@registry.table("ontology_change")
class OntologyChange(Record):
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


class ChangeRequest(Model):
    change: Change
    rationale: str = Field(description="Why: what you saw that makes this change right")


class ChangeResult(Model):
    id: RecordId
    status: ChangeStatus
    op: str
    advice: list[str] = []  # similar concepts, duplicates: read before approving
    reason: str | None = None
