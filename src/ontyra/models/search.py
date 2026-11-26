"""Derived read models: the search index, embeddings, the delivery ledger and recall traces.

Everything here is rebuildable from revisions (except the ledger and traces, which record
what was delivered and why).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from ontyra.models.base import (
    ActorRef,
    ConceptRef,
    Indexed,
    MemoryRef,
    OwnerRef,
    Record,
    RevRef,
)
from ontyra.models.kinds import KindName, Scope
from ontyra.models.registry import registry


@registry.table("search_doc")
class SearchDoc(Record):
    """One row per *live* revision (current, committed, active); id equals the revision's id.

    Superseded and retracted revisions are removed from the index in the same atomic batch,
    so a lane can never return a stale item.
    """

    rev: Annotated[RevRef, Indexed(unique=True)]
    memory: Annotated[MemoryRef, Indexed()]
    kind: KindName
    owner: Annotated[OwnerRef, Indexed()]
    text: str  # full-text indexed (see storage.schema EXTRA_DDL)
    anchors: list[str] = []  # canonical anchor URIs; empty = unanchored
    sources: list[str] = []  # canonical URIs of the sources it cites
    about: list[ConceptRef] = []
    scope: Scope = "contextual"  # the revision's scope; reference rows stay out of recall lanes
    expires_at: datetime | None = None  # the revision's expiry (expiry checks use it)
    created_at: datetime


@registry.table("embedding")
class Embedding(Record):
    """Vector of a revision's text under one model. The HNSW index dimension is configured."""

    rev: Annotated[RevRef, Indexed(unique=True)]
    owner: OwnerRef
    kind: KindName
    scope: Scope = "contextual"
    model: str
    vector: list[float]
    created_at: datetime


@registry.table("delivery")
class Delivery(Record):
    """Session recall ledger: what was already delivered to a session."""

    owner: Annotated[OwnerRef, Indexed()]
    session: Annotated[str, Indexed()]
    memory: MemoryRef
    rev: RevRef
    delivered_at: datetime
    notified: bool = False  # a correction notice was already sent


@registry.table("recall_trace", immutable=True, prunable=True)
class RecallTrace(Record):
    """Why each item of a recall pack was (or was not) delivered, for debugging and evaluation."""

    owner: Annotated[OwnerRef, Indexed()]
    actor: ActorRef
    path: str
    session: str | None = None
    request: dict[str, Any]
    items: list[dict[str, Any]]
    dropped: list[dict[str, Any]] = []
    # What the engine saw: the best candidates' lane scores (the features calibration and
    # reranker training learn from), and whether the request stayed silent.
    scoring: dict[str, Any] | None = None  # None: recorded before scores were kept, or turned off
    created_at: datetime
