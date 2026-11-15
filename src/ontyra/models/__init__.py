"""Pydantic domain models and the table/kind registry. No I/O and no framework imports."""

from ontyra.models import core as core  # importing registers tables and kinds
from ontyra.models import kinds as kinds
from ontyra.models.base import Indexed, LinksTo, Model, Record, RecordId, Ref
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
    "Indexed",
    "KindSpec",
    "LifecyclePolicy",
    "LinksTo",
    "Model",
    "Record",
    "RecordId",
    "Ref",
    "Registry",
    "TableSpec",
    "registry",
]
