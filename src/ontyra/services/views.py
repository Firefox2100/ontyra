"""Read models returned by the memory service."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ontyra.models import Model, RecordId


class MemoryView(Model):
    memory: RecordId
    kind: str
    owner: RecordId
    status: str
    revision: RecordId
    seq: int
    text: str
    content: dict[str, Any]  # the kind's own fields (JSON-compatible)
    state: str
    lifecycle: str
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    stale: bool = False
    confidence: float = 1.0


class SupersessionReason(Model):
    """Why a revision was replaced (the `supersedes` edge's own fields)."""

    reason_code: str
    reason: str | None = None
    by: RecordId | None = None
    at: datetime | None = None


class RevisionView(Model):
    revision: RecordId
    seq: int
    text: str
    state: str
    lifecycle: str
    recorded_at: datetime
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    superseded_by: list[RecordId] = []
    reasons: list[SupersessionReason] = []


class History(Model):
    memory: RecordId
    kind: str
    status: str
    revisions: list[RevisionView]


class MemoryDetail(Model):
    """`get` with optional expansions (history, links, evidence, applicability)."""

    memory: MemoryView
    history: History | None = None
    links: list[dict[str, Any]] | None = None
    evidence: list[dict[str, Any]] | None = None
    applies: dict[str, Any] | None = None


class Neighbors(Model):
    center: RecordId
    concepts: list[dict[str, Any]] = []
    memories: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
