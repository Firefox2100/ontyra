"""Registry of stored tables and memory kinds (the single source for DDL generation)."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TypeVar

from ontyra.models.base import Record

REV_WILDCARD = "*_rev"

R = TypeVar("R", bound=Record)


@dataclass(frozen=True)
class LifecyclePolicy:
    """Persistence semantics of a memory kind."""

    supersedes_by_edge: bool = True
    relevance_decay: bool = False
    evidence_gated: bool = False


@dataclass(frozen=True)
class TableSpec:
    name: str
    model: type[Record]
    immutable: bool = False  # all fields READONLY and DELETE blocked
    prunable: bool = False  # immutable, but DELETE stays allowed (retention sweeps)
    edge: tuple[tuple[str, ...], tuple[str, ...]] | None = None  # (IN tables, OUT tables)


@dataclass(frozen=True)
class KindSpec:
    name: str
    model: type[Record]
    policy: LifecyclePolicy

    @property
    def table(self) -> str:
        return f"{self.name}_rev"


class Registry:
    def __init__(self) -> None:
        self.tables: dict[str, TableSpec] = {}
        self.kinds: dict[str, KindSpec] = {}

    def table(
        self,
        name: str,
        *,
        immutable: bool = False,
        prunable: bool = False,
        edge: tuple[tuple[str, ...], tuple[str, ...]] | None = None,
    ) -> Callable[[type[R]], type[R]]:
        def deco(cls: type[R]) -> type[R]:
            if name in self.tables:
                raise ValueError(f"table already registered: {name}")
            self.tables[name] = TableSpec(name, cls, immutable, prunable, edge)
            return cls

        return deco

    def kind(
        self, name: str, policy: LifecyclePolicy | None = None
    ) -> Callable[[type[R]], type[R]]:
        """Register a memory kind; creates the immutable `<kind>_rev` table."""

        def deco(cls: type[R]) -> type[R]:
            spec = KindSpec(name, cls, policy or LifecyclePolicy())
            if name in self.kinds or spec.table in self.tables:
                raise ValueError(f"kind already registered: {name}")
            self.kinds[name] = spec
            self.tables[spec.table] = TableSpec(spec.table, cls, immutable=True)
            return cls

        return deco

    def rev_tables(self) -> tuple[str, ...]:
        return tuple(k.table for k in self.kinds.values())

    def expand(self, tables: Iterable[str]) -> tuple[str, ...]:
        """Replace the `*_rev` wildcard by every registered revision table."""
        out: list[str] = []
        for t in tables:
            out.extend(self.rev_tables() if t == REV_WILDCARD else (t,))
        return tuple(dict.fromkeys(out))


registry = Registry()
"""The default registry, populated by importing `ontyra.models` (kinds and core tables)."""
