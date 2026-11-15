"""Convert between Ontyra values (`RecordId`, pydantic models) and SDK values."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel
from surrealdb import RecordID

from ontyra.models import Record, RecordId


def encode(value: Any) -> Any:
    """Recursively convert `RecordId` to the SDK's record id, models to dicts."""
    if isinstance(value, RecordId):
        return RecordID(value.table, value.id)
    if isinstance(value, BaseModel):
        return encode(to_content(value))
    if isinstance(value, Mapping):
        return {k: encode(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, (list, tuple, set)):
        return [encode(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    return value


def to_content(model: BaseModel) -> dict[str, Any]:
    """The `CONTENT` payload for a record: no `id`, no `None` fields (NONE in SurrealDB)."""
    exclude = {"id"} if isinstance(model, Record) else None
    return model.model_dump(mode="python", exclude=exclude, exclude_none=True)


def record_id(value: RecordId) -> RecordID:
    return RecordID(value.table, value.id)


def decode(value: Any) -> Any:
    """Recursively convert the SDK's record ids into `RecordId`."""
    if isinstance(value, RecordID):
        return RecordId.parse(value)
    if isinstance(value, Mapping):
        return {k: decode(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, (list, tuple)):
        return [decode(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    return value
