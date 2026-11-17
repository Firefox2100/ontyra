"""Base building blocks for all pydantic models: record ids, typed links, record base class.

This module is the model layer's only vocabulary for database identity. It deliberately
does not import the SurrealDB SDK: the storage layer converts between `RecordId` and the
SDK's own record id type.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, cast

from pydantic import AfterValidator, BaseModel, ConfigDict
from pydantic_core import core_schema


class RecordId:
    """An immutable `table:id` record identifier."""

    __slots__ = ("_id", "_table")

    def __init__(self, table: str, id: str | int) -> None:
        if not table or ":" in table:
            raise ValueError(f"invalid table name: {table!r}")
        self._table = table
        self._id = id

    @property
    def table(self) -> str:
        return self._table

    @property
    def id(self) -> str | int:
        return self._id

    @classmethod
    def parse(cls, value: Any) -> RecordId:
        """Accept `RecordId`, `"table:id"`, `{"table":..,"id":..}` or an SDK-like record id."""
        if isinstance(value, RecordId):
            return value
        if isinstance(value, str):
            table, sep, ident = value.partition(":")
            if not sep or not ident:
                raise ValueError(f"not a record id: {value!r}")
            return cls(table, ident.strip("`⟨⟩"))
        if isinstance(value, dict):
            mapping = cast("dict[str, Any]", value)
            if "table" in mapping and "id" in mapping:
                return cls(str(mapping["table"]), mapping["id"])
            raise ValueError(f"not a record id: {value!r}")
        # SDK-style record id (duck-typed so the model layer needs no SDK import)
        sdk: Any = value
        table: Any = getattr(sdk, "table_name", None)
        ident: Any = getattr(sdk, "id", None)
        if isinstance(table, str) and isinstance(ident, (str, int)):
            return cls(table, ident)
        raise ValueError(f"not a record id: {value!r}")

    def __str__(self) -> str:
        return f"{self._table}:{self._id}"

    def __repr__(self) -> str:
        return f"RecordId({self._table!r}, {self._id!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, RecordId) and (self._table, self._id) == (other._table, other._id)

    def __hash__(self) -> int:
        return hash((self._table, self._id))

    @classmethod
    def __get_pydantic_json_schema__(cls, schema: Any, handler: Any) -> dict[str, Any]:
        return {
            "type": "string",
            "examples": ["memory:0194f3a1b2c3d4e5f6a7"],
            "title": "RecordId",
            "description": "A record id as `table:id`.",
        }

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> core_schema.CoreSchema:
        return core_schema.no_info_plain_validator_function(
            cls.parse,
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda v: str(v), when_used="json"
            ),
        )


@dataclass(frozen=True)
class LinksTo:
    """Marks a `RecordId` field as a typed link: `record<a|b>`.

    A table name of the form `"*_rev"` stands for "every registered memory-kind revision
    table"; it is expanded by the registry (DDL generation and validation).
    """

    tables: tuple[str, ...]


@dataclass(frozen=True)
class Indexed:
    """Marks a field to get a (optionally unique) single-field index."""

    unique: bool = False


def check_link(tables: tuple[str, ...]) -> Callable[[RecordId], RecordId]:
    """Validator: the record must live in one of `tables` (`*_rev` expands via the registry)."""

    def _check(value: RecordId) -> RecordId:
        from ontyra.models.registry import registry

        allowed = registry.expand(tables)
        if allowed and value.table not in allowed:
            raise ValueError(f"{value} must link to one of {sorted(allowed)}")
        return value

    return _check


def Ref(*tables: str) -> Any:
    """Ad-hoc typed link (type checkers see `Any`); prefer the typed aliases below."""
    return Annotated[RecordId, LinksTo(tuple(tables)), AfterValidator(check_link(tables))]


# Typed link aliases: valid type expressions, so type checkers see plain `RecordId`.
ActorRef = Annotated[RecordId, LinksTo(("actor",)), AfterValidator(check_link(("actor",)))]
OwnerRef = Annotated[RecordId, LinksTo(("owner",)), AfterValidator(check_link(("owner",)))]
UserRef = Annotated[RecordId, LinksTo(("user",)), AfterValidator(check_link(("user",)))]
MemoryRef = Annotated[RecordId, LinksTo(("memory",)), AfterValidator(check_link(("memory",)))]
ConceptRef = Annotated[RecordId, LinksTo(("concept",)), AfterValidator(check_link(("concept",)))]
EpisodeRef = Annotated[RecordId, LinksTo(("episode",)), AfterValidator(check_link(("episode",)))]
RevRef = Annotated[RecordId, LinksTo(("*_rev",)), AfterValidator(check_link(("*_rev",)))]
SubjectRef = Annotated[
    RecordId,
    LinksTo(("concept", "resource")),
    AfterValidator(check_link(("concept", "resource"))),
]


class Model(BaseModel):
    """Base for every pydantic model: strict about unknown fields."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


class Record(Model):
    """Base for stored records. `id` is assigned by the database or the service."""

    id: RecordId | None = None
