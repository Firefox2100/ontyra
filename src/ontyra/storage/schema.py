"""Generate SurrealQL DDL from the model registry; apply it; detect and fix drift.

Reference implementation: spikes/schema_gen (spike 2, plans/10).
"""

from __future__ import annotations

import types
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from ontyra.models import Indexed, LinksTo, RecordId, Registry, TableSpec
from ontyra.models import registry as default_registry
from ontyra.storage.database import Database

_SCALARS: dict[Any, str] = {
    str: "string",
    int: "int",
    float: "float",
    bool: "bool",
    datetime: "datetime",
}


@dataclass(frozen=True)
class FieldDef:
    path: str
    type: str
    flexible: bool = False
    default: str | None = None  # SurrealQL literal


def _lit(v: Any) -> str:
    return "'" + v.replace("\\", "\\\\").replace("'", "\\'") + "'" if isinstance(v, str) else str(v)


def _default_literal(default: Any) -> str | None:
    if default is PydanticUndefined or default is None:
        return None
    if isinstance(default, bool):
        return "true" if default else "false"
    if isinstance(default, (int, float)):
        return repr(default)
    if isinstance(default, str):
        return _lit(default)
    if isinstance(default, list) and not default:
        return "[]"
    if isinstance(default, dict) and not default:
        return "{}"
    return None


class SchemaGenerator:
    def __init__(self, reg: Registry | None = None) -> None:
        self.registry = reg or default_registry

    # ---- type mapping -------------------------------------------------

    def surreal_type(self, tp: Any, meta: tuple[Any, ...] = ()) -> tuple[str, bool]:
        """Return (SurrealQL type expression, is_flexible_object)."""
        origin = get_origin(tp)
        if origin is Annotated:
            base, *ann = get_args(tp)
            return self.surreal_type(base, meta + tuple(ann))
        if tp is Any:
            return "any", False
        if tp is RecordId:
            links = next((m for m in meta if isinstance(m, LinksTo)), None)
            if links is None:
                return "record", False
            return f"record<{'|'.join(self.registry.expand(links.tables))}>", False
        if origin in (Union, types.UnionType):
            members = list(get_args(tp))
            optional = type(None) in members
            parts: list[str] = []
            flex = False
            for m in members:
                if m is type(None):
                    continue
                t, f = self.surreal_type(m, meta)
                parts.append(t)
                flex = flex or f
            inner = " | ".join(parts)
            return (f"option<{inner}>" if optional else inner), flex
        if origin is Literal:
            return " | ".join(_lit(v) for v in get_args(tp)), False
        if origin in (list, set, tuple):
            t, f = self.surreal_type(get_args(tp)[0], meta)
            return f"array<{t}>", f
        if origin is dict or tp is dict:
            return "object", True
        if isinstance(tp, type) and issubclass(tp, BaseModel):
            return "object", False
        if tp in _SCALARS:
            return _SCALARS[tp], False
        raise TypeError(f"no SurrealDB mapping for {tp!r}")

    def _model_in(self, tp: Any) -> type[BaseModel] | None:
        origin = get_origin(tp)
        if origin is Annotated:
            return self._model_in(get_args(tp)[0])
        if origin in (Union, types.UnionType, list, set, tuple):
            for a in get_args(tp):
                m = self._model_in(a)
                if m:
                    return m
            return None
        return tp if isinstance(tp, type) and issubclass(tp, BaseModel) else None

    def _is_array(self, tp: Any) -> bool:
        origin = get_origin(tp)
        if origin is Annotated:
            return self._is_array(get_args(tp)[0])
        if origin in (Union, types.UnionType):
            return any(self._is_array(a) for a in get_args(tp) if a is not type(None))
        return origin in (list, set, tuple)

    # ---- fields -------------------------------------------------------

    def collect_fields(
        self, model: type[BaseModel], prefix: str = ""
    ) -> tuple[list[FieldDef], list[tuple[str, bool]]]:
        """Return (field definitions, [(path, unique)] indexes) for a model."""
        out: list[FieldDef] = []
        indexes: list[tuple[str, bool]] = []
        for name, info in model.model_fields.items():
            if name == "id":
                continue
            path = f"{prefix}{name}"
            t, flex = self.surreal_type(info.annotation, tuple(info.metadata))
            out.append(FieldDef(path, t, flex, _default_literal(info.default)))
            for m in info.metadata:
                if isinstance(m, Indexed):
                    indexes.append((path, m.unique))
            sub = self._model_in(info.annotation)
            if sub is not None:
                child = f"{path}[*]." if self._is_array(info.annotation) else f"{path}."
                f2, i2 = self.collect_fields(sub, child)
                out += f2
                indexes += i2
        return out, indexes

    # ---- statements ---------------------------------------------------

    def table_ddl(self, spec: TableSpec) -> list[str]:
        if spec.edge:
            ins = "|".join(self.registry.expand(spec.edge[0]))
            outs = "|".join(self.registry.expand(spec.edge[1]))
            stmts = [
                f"DEFINE TABLE OVERWRITE {spec.name} TYPE RELATION IN {ins} OUT {outs} "
                "ENFORCED SCHEMAFULL"
            ]
        else:
            stmts = [f"DEFINE TABLE OVERWRITE {spec.name} SCHEMAFULL"]
        fields, indexes = self.collect_fields(spec.model)
        for f in fields:
            flex = " FLEXIBLE" if f.flexible else ""
            dflt = f" DEFAULT {f.default}" if f.default is not None else ""
            ro = " READONLY" if spec.immutable else ""
            stmts.append(
                f"DEFINE FIELD OVERWRITE {f.path} ON {spec.name} TYPE {f.type}{flex}{dflt}{ro}"
            )
        for path, unique in indexes:
            idx = f"{spec.name}_{path.replace('.', '_').replace('[*]', '')}_idx"
            stmts.append(
                f"DEFINE INDEX OVERWRITE {idx} ON {spec.name} FIELDS {path}"
                f"{' UNIQUE' if unique else ''}"
            )
        if spec.immutable:
            stmts.append(
                f"DEFINE EVENT OVERWRITE {spec.name}_no_delete ON TABLE {spec.name} "
                f'WHEN $event = "DELETE" THEN {{ THROW "{spec.name} is immutable" }}'
            )
        return stmts

    def ddl(self) -> list[str]:
        stmts: list[str] = []
        for spec in self.registry.tables.values():
            stmts += self.table_ddl(spec)
        return stmts

    def expected_field_names(self, spec: TableSpec) -> set[str]:
        names: set[str] = {"in", "out"} if spec.edge else set()
        fields, _ = self.collect_fields(spec.model)
        for f in fields:
            p = f.path.replace("[*]", ".*")
            names.add(p)
            if f.type.startswith(("array<", "option<array<")):
                names.add(p + ".*")
        return names

    def plan_drift(
        self, db_info: dict[str, Any], table_infos: dict[str, dict[str, Any]]
    ) -> list[str]:
        """REMOVE statements for tables/fields present in the DB but not in the registry."""
        stmts: list[str] = []
        for t in db_info.get("tables", {}):
            if t not in self.registry.tables:
                stmts.append(f"REMOVE TABLE {t}")
        for t, spec in self.registry.tables.items():
            info = table_infos.get(t)
            if not info:
                continue
            want = self.expected_field_names(spec)
            for f in info.get("fields", {}):
                if f in want or (f.endswith(".*") and f[:-2] in want):
                    continue
                stmts.append(f"REMOVE FIELD `{f}` ON {t}")
        return stmts


class SchemaManager:
    """Applies the generated schema to a database."""

    def __init__(self, db: Database, reg: Registry | None = None) -> None:
        self.db = db
        self.generator = SchemaGenerator(reg)

    async def apply(self) -> None:
        """Create/update every registered table (atomic, idempotent, keeps data)."""
        await self.db.run_atomic(self.generator.ddl())

    async def drift(self) -> list[str]:
        info = await self.db.info_db()
        table_infos = {t: await self.db.info_table(t) for t in info.get("tables", {})}
        return self.generator.plan_drift(info, table_infos)

    async def sync(self, *, prune: bool = True) -> list[str]:
        """Apply, then (optionally) remove stale tables/fields. Returns the removals."""
        await self.apply()
        removals = await self.drift() if prune else []
        if removals:
            await self.db.run_atomic(removals)
        return removals

    async def wipe(self) -> None:
        """Dev only: remove every registered table (data included)."""
        await self.db.run_atomic(
            [f"REMOVE TABLE IF EXISTS {t}" for t in self.generator.registry.tables]
        )
