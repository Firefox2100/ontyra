"""Generate SurrealQL DDL from the model registry; apply it; detect and fix drift.

Reference implementation: spikes/schema_gen.
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


def _ident(path: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in path)


def _empty_value(surreal_type: str) -> str | None:
    """A neutral value of a required type, for rows written before the field existed."""
    if surreal_type.startswith("array<"):
        return "[]"
    if surreal_type.startswith("'"):  # a union of literals: the first
        return surreal_type.split(" | ")[0]
    return {
        "string": "''",
        "int": "0",
        "float": "0.0",
        "bool": "false",
        "datetime": "d'1970-01-01T00:00:00Z'",
        "object": "{}",
    }.get(surreal_type)


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

    @staticmethod
    def field_ddl(spec: TableSpec, f: FieldDef, migrate_with: str | None = None) -> str:
        """One field definition; `migrate_with` says what rows that already exist hold."""
        flex = " FLEXIBLE" if f.flexible else ""
        dflt = f" DEFAULT {f.default}" if f.default is not None else ""
        migrate = (
            f" MIGRATE EXISTING ROWS AS m_{_ident(f.path)} USING {migrate_with}"
            if migrate_with is not None
            else ""
        )
        ro = " READONLY" if spec.immutable else ""
        return (
            f"DEFINE FIELD OVERWRITE {f.path} ON {spec.name} TYPE {f.type}{flex}{dflt}{migrate}{ro}"
        )

    def plan_migrations(self, table_infos: dict[str, dict[str, Any]]) -> list[str]:
        """Definitions for fields a table in the database does not have yet, each saying what
        existing rows hold (the field's default, or an empty value of its type). A field whose
        type accepts no value that can be invented here is an error: write the migration by hand."""
        stmts: list[tuple[int, str, str]] = []
        for name, spec in self.registry.tables.items():
            info = table_infos.get(name)
            if info is None:
                continue
            have = set(info.get("fields", {}))
            fields, _ = self.collect_fields(spec.model)
            for f in fields:
                path = f.path.replace("[*]", ".*")
                if path in have or f.type.startswith("option<"):
                    continue
                value = f.default if f.default is not None else _empty_value(f.type)
                if value is None:
                    raise ValueError(f"cannot migrate {name}.{f.path}: no value for type {f.type}")
                stmts.append((f.path.count("[*]"), f.path, self.field_ddl(spec, f, value)))
        return [ddl for _, _, ddl in sorted(stmts)]

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
        stmts += [self.field_ddl(spec, f) for f in fields]
        for path, unique in indexes:
            idx = f"{spec.name}_{path.replace('.', '_').replace('[*]', '')}_idx"
            stmts.append(
                f"DEFINE INDEX OVERWRITE {idx} ON {spec.name} FIELDS {path}"
                f"{' UNIQUE' if unique else ''}"
            )
        if spec.prunable:  # was blocked before the table became prunable
            stmts.append(f"REMOVE EVENT IF EXISTS {spec.name}_no_delete ON TABLE {spec.name}")
        elif spec.immutable:
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


def search_ddl(embedding_dim: int | None) -> list[str]:
    """Index definitions the model registry cannot express: full-text and vector search."""
    stmts = [
        "DEFINE ANALYZER OVERWRITE ontyra_text TOKENIZERS class, punct "
        "FILTERS lowercase, ascii, snowball(english)",
        "DEFINE INDEX OVERWRITE search_doc_text_ft ON search_doc FIELDS text "
        "FULLTEXT ANALYZER ontyra_text BM25",
    ]
    if embedding_dim:
        stmts.append(
            "DEFINE INDEX OVERWRITE embedding_vector_hnsw ON embedding FIELDS vector "
            f"HNSW DIMENSION {int(embedding_dim)} DIST COSINE TYPE F32 EFC 150 M 12"
        )
    return stmts


class SchemaManager:
    """Applies the generated schema to a database.

    `embedding_dim` creates the vector index; changing it needs the `embedding` table to be
    emptied first (pre-release: wipe and re-embed).
    """

    def __init__(
        self, db: Database, reg: Registry | None = None, *, embedding_dim: int | None = None
    ) -> None:
        self.db = db
        self.generator = SchemaGenerator(reg)
        self.embedding_dim = embedding_dim

    async def apply(self) -> None:
        """Create/update every registered table (atomic, idempotent, keeps data). Fields the
        database lacks are added first, one by one, saying what existing rows hold; the rest of
        the definitions then apply in one transaction (an index cannot be defined in the
        transaction that migrates its rows)."""
        info = await self.db.info_db()
        tables = {t: await self.db.info_table(t) for t in info.get("tables", {})}
        for statement in self.generator.plan_migrations(tables):
            await self.db.run_atomic([statement])
        await self.db.run_atomic([*self.generator.ddl(), *search_ddl(self.embedding_dim)])

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
