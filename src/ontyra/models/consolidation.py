"""Duplicate and update candidates found by a consolidation scan (a report, never a change)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from ontyra.models.base import MemoryRef, Model, RevRef
from ontyra.models.kinds import KindName


class ScanRequest(Model):
    kinds: list[KindName] = []  # empty: every kind
    updates: bool = True  # also report restatements with a changed value


class Member(Model):
    memory: MemoryRef
    revision: RevRef
    kind: KindName
    text: str
    created_at: datetime


class PairScore(Model):
    a: MemoryRef
    b: MemoryRef
    cosine: float
    # the decision model's probability that the two say the same thing (the lower of its two
    # answers, A,B and B,A); None when it was not asked or could not answer
    same: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    judged_by: Literal["model", "cosine"]


class DuplicateCluster(Model):
    """Memories that say the same thing. `keep` is the oldest; the rest can be superseded by it
    (or the other way round) with an ordinary proposal; a human or agent decides."""

    members: list[Member]  # oldest first
    pairs: list[PairScore]
    keep: MemoryRef


class UpdateCandidate(Model):
    """The same statement with a changed value (a version, an amount, a date): `newer` probably
    supersedes `older`. Not a duplicate: merging would lose the update."""

    older: Member
    newer: Member
    cosine: float


class ScanReport(Model):
    scanned: int  # live memories looked at
    unembedded: int  # of those, without a vector under the current model (skipped)
    duplicates: list[DuplicateCluster] = []
    updates: list[UpdateCandidate] = []
    rejected: int = 0  # candidate pairs the decision model said are different things
    decision_model: str | None = None  # None: judged by cosine alone
    model_errors: int = 0  # pairs the model could not answer (judged by cosine instead)
    note: str | None = None


class ConsolidationSettings(BaseModel):
    """Duplicate detection (`ONTYRA_CONSOLIDATION__*`; the scan is `POST /v1/consolidation/scan`).

    Candidates are live memories of the same kind whose embeddings have a cosine of at least
    `candidate_cosine`. With a decision model configured (`ONTYRA_DECISION__*`) it is asked, in
    both orders, whether the two say the same thing, and a pair is a duplicate when both answers
    reach `confirm`; a pair it cannot judge falls back to the cosine rule. Without one, a pair
    is a duplicate when its cosine reaches `duplicate_cosine`. Calibrate the cosines per
    embedder (the defaults suit qwen3-embedding-8b). Nothing is merged: the scan only reports.
    """

    candidate_cosine: float = 0.70
    duplicate_cosine: float = 0.85
    confirm: float = 0.85
    neighbours: int = 8  # nearest memories examined per memory
    max_memories: int = 2000  # a scan looks at this many live memories at most
