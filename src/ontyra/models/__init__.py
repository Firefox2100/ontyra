"""Pydantic domain models and the table/kind registry. No I/O and no framework imports."""

from ontyra.models import core as core  # importing registers tables and kinds
from ontyra.models import feedback as feedback
from ontyra.models import kinds as kinds
from ontyra.models import ontology as ontology
from ontyra.models import search as search
from ontyra.models.base import Indexed, LinksTo, Model, Record, RecordId, Ref
from ontyra.models.ids import new_id
from ontyra.models.policy import Decision, PolicyConfig, PolicyRule
from ontyra.models.registry import (
    REV_WILDCARD,
    KindSpec,
    LifecyclePolicy,
    Registry,
    TableSpec,
    registry,
)

__all__ = [
    "REV_WILDCARD",
    "Decision",
    "Indexed",
    "KindSpec",
    "LifecyclePolicy",
    "LinksTo",
    "Model",
    "PolicyConfig",
    "PolicyRule",
    "Record",
    "RecordId",
    "Ref",
    "Registry",
    "TableSpec",
    "new_id",
    "registry",
]
