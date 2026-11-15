"""Validated, resolved operations: what the planner turns into writes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from ontyra.models import Record, RecordId
from ontyra.models.core import Memory
from ontyra.models.proposals import Evidence, ReasonCode


@dataclass
class PreparedAdd:
    index: int
    memory: RecordId
    rev: RecordId
    owner: RecordId
    kind: str
    model: Record
    valid_from: datetime
    evidence: list[Evidence] = field(default_factory=lambda: [])
    sources: list[RecordId] = field(default_factory=lambda: [])  # revisions it derives from


@dataclass
class PreparedRevise:
    index: int
    memory: Memory
    old_rev: RecordId
    new_rev: RecordId
    model: Record
    reason_code: ReasonCode
    reason: str | None
    evidence: list[Evidence] = field(default_factory=lambda: [])


@dataclass
class PreparedSupersede:
    index: int
    new_memory: RecordId
    new_rev: RecordId
    new_guard: RecordId | None  # existing memory's current rev to guard (None if added here)
    target: Memory
    target_rev: RecordId
    reason_code: ReasonCode
    reason: str | None


@dataclass
class PreparedRetract:
    index: int
    memory: Memory
    rev: RecordId
    reason: str
    dependents: list[RecordId]


@dataclass
class PreparedLink:
    index: int
    relation: Literal["contradicts", "overrides", "refines", "promoted_to"]
    source_rev: RecordId
    target_rev: RecordId
    reason: str | None


@dataclass
class PreparedAnnotate:
    index: int
    target: RecordId
    kind: Literal["comment", "tag", "flag"]
    text: str


Prepared = (
    PreparedAdd
    | PreparedRevise
    | PreparedSupersede
    | PreparedRetract
    | PreparedLink
    | PreparedAnnotate
)
