"""Concepts, their relations, and resources (the graph memories are anchored in)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal

from ontyra.models.base import ActorRef, ConceptRef, Indexed, Model, Record, RecordId
from ontyra.models.registry import registry

ValueType = Literal["bool", "version", "number", "enum", "string"]
VersionScheme = Literal["semver", "pep440", "dotted", "distro"]
Dimension = Literal["memory", "duration"]
Scheme = Literal["domain", "qualifier"]

# Relation types the reasoner gives meaning to (see services/reasoner.py).
RelationName = Literal[
    "is_a",
    "part_of",
    "instance_of",
    "same_as",
    "alternative_to",
    "replaced_by",
    "depends_on",
    "disjoint_with",
    "split_into",
]


def try_slug(label: str) -> str | None:
    """`concept_slug`, or None for a name with no letter or digit in it."""
    try:
        return concept_slug(label)
    except ValueError:
        return None


def concept_slug(label: str) -> str:
    """Stable, readable id for a concept label: `Ubuntu 24.04` → `ubuntu_24_04`."""
    slug = re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_")
    if not slug:
        raise ValueError(f"cannot derive a concept id from {label!r}")
    return slug


@registry.table("concept")
class Concept(Record):
    """What kind of thing exists (never advice). The id is the slug of the label."""

    label: str
    scheme: Scheme = "domain"  # `qualifier` concepts are browsed separately
    aliases: list[str] = []
    definition: str | None = None
    value_type: ValueType = "bool"
    version_scheme: VersionScheme | None = None
    dimension: Dimension | None = None
    values: list[str] = []  # allowed values of an enum concept
    status: Literal["active", "deprecated"] = "active"
    replaced_by: ConceptRef | None = None
    created_at: datetime
    updated_at: datetime | None = None
    # plans/23. Nullable so concepts stored before versions still validate: `version` is the
    # sequence number of the latest `concept_rev` (None: none written yet), `reason` why it was
    # deprecated.
    version: int | None = None
    reason: str | None = None


@registry.table("concept_rev", immutable=True)
class ConceptRev(Record):
    """One version of a concept's editable fields. History only: ordinary reads use `concept`
    (the current state); versions surface only through the investigation tools."""

    concept: Annotated[ConceptRef, Indexed()]
    seq: int
    label: str
    aliases: list[str] = []
    definition: str | None = None
    status: Literal["active", "deprecated"] = "active"
    replaced_by: ConceptRef | None = None
    reason: str | None = None
    change: RecordId | None = None  # the ontology change that made it (None: before versions)
    by: ActorRef | None = None
    at: datetime


@registry.table("related", edge=(("concept",), ("concept",)))
class Related(Record):
    """`concept -> related -> concept`, typed by `relation`."""

    relation: RelationName
    by: ActorRef
    at: datetime
    # An ended edge is history: the reasoner and every read ignore it (plans/23).
    ended_at: datetime | None = None
    ended_by: ActorRef | None = None
    ended_by_change: RecordId | None = None


ResourceKind = Literal[
    "host", "account", "project", "dir", "file", "repo", "url", "harness", "other"
]


@registry.table("resource")
class Resource(Record):
    """A registered anchor. Containment is mostly syntactic (path segments); this record adds
    explicit containment (`parents`) and equivalence (`same_as`) on top (plans/01)."""

    uri: Annotated[str, Indexed(unique=True)]
    kind: ResourceKind = "other"
    label: str | None = None
    parents: list[str] = []
    same_as: list[str] = []
    instance_of: list[ConceptRef] = []
    created_at: datetime


# ---- service-facing request/response models --------------------------------------------


class ConceptDefinition(Model):
    """Request: create a concept. The id is derived from the label."""

    label: str
    scheme: Scheme = "domain"
    aliases: list[str] = []
    definition: str | None = None
    value_type: ValueType = "bool"
    version_scheme: VersionScheme | None = None
    dimension: Dimension | None = None
    values: list[str] = []
    is_a: list[str] = []  # labels/ids of broader concepts (convenience for `related`)


class RelationRequest(Model):
    source: str  # concept id (`concept:x`, `x`) or label/alias
    relation: RelationName
    target: str


class RelationView(Model):
    direction: Literal["out", "in"]
    relation: str
    other: RecordId
    label: str


class ConceptView(Model):
    id: RecordId
    label: str
    scheme: str
    aliases: list[str]
    definition: str | None
    value_type: str
    version_scheme: str | None
    dimension: str | None
    status: str
    replaced_by: RecordId | None
    successors: list[RecordId] = []  # a split concept: what it became
    deprecation_reason: str | None = None
    relations: list[RelationView] = []
    memories: int = 0  # live memories about this concept (directly)


class ResourceRequest(Model):
    uri: str
    kind: ResourceKind = "other"
    label: str | None = None
    parents: list[str] = []
    same_as: list[str] = []
    instance_of: list[str] = []  # concept ids or labels


class ConceptVersion(Model):
    """One entry of a concept's history (investigation tools only)."""

    seq: int
    label: str
    aliases: list[str]
    definition: str | None
    status: str
    replaced_by: RecordId | None
    reason: str | None
    change: RecordId | None
    by: RecordId | None
    at: datetime


class EdgeEvent(Model):
    """A relation of the concept as it was added and, when it was, ended."""

    relation: str
    direction: Literal["out", "in"]
    other: RecordId
    added_at: datetime
    ended_at: datetime | None
    ended_by_change: RecordId | None


class ConceptHistory(Model):
    concept: RecordId
    current: int | None  # latest version number (None: never edited since versions began)
    versions: list[ConceptVersion]
    relations: list[EdgeEvent]
    changes: list[RecordId]  # ontology changes that touched it, newest first


class OntologyFinding(Model):
    severity: Literal["error", "warning", "info"]
    code: str
    message: str
    concepts: list[RecordId] = []
    change: RecordId | None = None  # the change that most likely caused it


class OntologyCheck(Model):
    """A read-only report on the ontology's health (nothing is fixed automatically)."""

    concepts: int
    live_edges: int
    findings: list[OntologyFinding]
