"""`Store` implementation on SurrealDB."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from ontyra.models import Record, RecordId, registry
from ontyra.models.core import Episode, Memory
from ontyra.models.search import SearchDoc
from ontyra.ports.storage import Hit
from ontyra.ports.writes import Create, Delete, ExpectCurrent, Relate, Update, Upsert, Write
from ontyra.storage.codec import decode
from ontyra.storage.database import Database

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _ident(name: str) -> str:
    if not _IDENT.fullmatch(name):
        raise ValueError(f"unsafe identifier: {name!r}")
    return name


def _model(table: str) -> type[Record]:
    return registry.tables[table].model


def _parse(row: dict[str, Any]) -> Record:
    row = decode(row)
    table = RecordId.parse(row["id"]).table
    return _model(table).model_validate(row)


def compile_writes(writes: Sequence[Write]) -> tuple[list[str], dict[str, Any]]:
    """Translate backend-neutral writes into SurrealQL statements plus parameters."""
    stmts: list[str] = []
    params: dict[str, Any] = {}
    for i, w in enumerate(writes):
        if isinstance(w, Create):
            params[f"i{i}"], params[f"c{i}"] = w.id, w.content
            stmts.append(f"CREATE $i{i} CONTENT $c{i}")
        elif isinstance(w, Update):
            params[f"i{i}"], params[f"c{i}"] = w.id, w.patch
            stmts.append(f"UPDATE $i{i} MERGE $c{i}")
        elif isinstance(w, Upsert):
            params[f"i{i}"], params[f"c{i}"] = w.id, w.content
            stmts.append(f"UPSERT $i{i} CONTENT $c{i}")
        elif isinstance(w, Delete):
            params[f"i{i}"] = w.id
            stmts.append(f"DELETE $i{i}")
        elif isinstance(w, Relate):
            spec = registry.tables.get(w.edge)
            if spec is None or spec.edge is None:
                raise ValueError(f"not an edge table: {w.edge!r}")
            params[f"s{i}"], params[f"t{i}"], params[f"c{i}"] = w.source, w.target, w.content
            stmts.append(f"RELATE $s{i}->{_ident(w.edge)}->$t{i} CONTENT $c{i}")
        else:  # ExpectCurrent
            assert isinstance(w, ExpectCurrent)
            params[f"m{i}"], params[f"e{i}"] = w.memory, w.revision
            stmts.append(
                f"LET $g{i} = (SELECT VALUE current FROM ONLY $m{i}); "
                f"IF $g{i} != $e{i} {{ THROW 'ontyra:conflict {w.memory}' }}"
            )
    return stmts, params


class SurrealStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, id: RecordId) -> Record | None:
        rows = (await self.db.query("SELECT * FROM ONLY $id", {"id": id}))[0]
        return _parse(rows) if rows else None

    async def select(
        self,
        table: str,
        where: Mapping[str, Any] | None = None,
        *,
        where_in: Mapping[str, Sequence[Any]] | None = None,
        where_any: Mapping[str, Any] | None = None,
        limit: int = 100,
        order_by: str | None = None,
        descending: bool = False,
    ) -> list[Record]:
        _ident(table)
        params: dict[str, Any] = {"limit": limit}
        clauses: list[str] = []
        for n, (key, value) in enumerate((where or {}).items()):
            params[f"w{n}"] = value
            clauses.append(f"{_ident(key)} = $w{n}")
        for n, (key, values) in enumerate((where_in or {}).items()):
            params[f"wi{n}"] = list(values)
            clauses.append(f"{_ident(key)} IN $wi{n}")
        any_clauses: list[str] = []
        for n, (key, value) in enumerate((where_any or {}).items()):
            params[f"wa{n}"] = value
            any_clauses.append(f"{_ident(key)} = $wa{n}")
        if any_clauses:
            clauses.append("(" + " OR ".join(any_clauses) + ")")
        sql = f"SELECT * FROM {table}"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        if order_by:
            sql += f" ORDER BY {_ident(order_by)} {'DESC' if descending else 'ASC'}"
        sql += " LIMIT $limit"
        rows = (await self.db.query(sql, params))[0]
        return [_model(table).model_validate(decode(r)) for r in rows]

    async def revisions(self, memory: RecordId) -> list[Record]:
        mem = await self.get(memory)
        if not isinstance(mem, Memory):
            return []
        table = registry.kinds[mem.kind].table
        return await self.select(table, {"memory": memory}, limit=10_000, order_by="seq")

    async def active_memories(
        self, kind: str, owners: Sequence[RecordId], limit: int = 500
    ) -> list[tuple[Memory, Record]]:
        rows = (
            await self.db.query(
                "SELECT * FROM memory WHERE kind = $k AND status = 'active' "
                "AND owner IN $owners LIMIT $limit",
                {"k": kind, "owners": list(owners), "limit": limit},
            )
        )[0]
        memories = [Memory.model_validate(decode(r)) for r in rows]
        pointers: list[tuple[Memory, RecordId]] = []
        for m in memories:
            if m.current is not None:
                pointers.append((m, m.current))
        if not pointers:
            return []
        revs = {
            RecordId.parse(r["id"]): _parse(r)
            for r in (await self.db.query("SELECT * FROM $ids", {"ids": [c for _, c in pointers]}))[
                0
            ]
        }
        return [(m, revs[c]) for m, c in pointers if c in revs]

    async def dependents(self, rev: RecordId) -> list[RecordId]:
        rows = (
            await self.db.query("SELECT VALUE in FROM derived_from WHERE out = $r", {"r": rev})
        )[0]
        return [RecordId.parse(r) for r in rows]

    async def get_many(self, ids: Sequence[RecordId]) -> dict[RecordId, Record]:
        if not ids:
            return {}
        rows = (await self.db.query("SELECT * FROM $ids", {"ids": list(dict.fromkeys(ids))}))[0]
        return {RecordId.parse(r["id"]): _parse(r) for r in rows}

    async def search_text(
        self, query: str, owners: Sequence[RecordId], *, kinds: Sequence[str] = (), limit: int = 50
    ) -> list[Hit]:
        if not query.strip():
            return []
        kind_clause = " AND kind IN $kinds" if kinds else ""
        rows = (
            await self.db.query(
                "SELECT rev, search::score(0) AS score FROM search_doc "
                f"WHERE text @0,OR@ $q AND owner IN $owners{kind_clause} "
                "ORDER BY score DESC LIMIT $limit",
                {"q": query, "owners": list(owners), "kinds": list(kinds), "limit": limit},
            )
        )[0]
        return [Hit(RecordId.parse(r["rev"]), float(r["score"])) for r in rows]

    async def search_vector(
        self,
        vector: Sequence[float],
        owners: Sequence[RecordId],
        *,
        kinds: Sequence[str] = (),
        limit: int = 50,
    ) -> list[Hit]:
        k, ef = int(limit), max(int(limit) * 3, 100)
        kind_clause = " AND kind IN $kinds" if kinds else ""
        rows = (
            await self.db.query(
                "SELECT rev, vector::distance::knn() AS dist FROM embedding "
                f"WHERE vector <|{k},{ef}|> $v AND owner IN $owners{kind_clause} "
                "ORDER BY dist LIMIT $limit",
                {"v": list(vector), "owners": list(owners), "kinds": list(kinds), "limit": k},
            )
        )[0]
        return [Hit(RecordId.parse(r["rev"]), 1.0 - float(r["dist"])) for r in rows]

    async def docs(
        self,
        owners: Sequence[RecordId],
        *,
        kinds: Sequence[str] = (),
        anchors: Sequence[str] | None = None,
        unanchored: bool = False,
        about: Sequence[RecordId] | None = None,
        expires_before: datetime | None = None,
        limit: int = 500,
    ) -> list[SearchDoc]:
        params: dict[str, Any] = {"owners": list(owners), "limit": limit}
        selectors: list[str] = []
        if anchors:
            selectors.append("anchors CONTAINSANY $anchors")
            params["anchors"] = list(anchors)
        if unanchored:
            selectors.append("array::len(anchors) = 0")
        if about:
            selectors.append("about CONTAINSANY $about")
            params["about"] = list(about)
        sql = "SELECT * FROM search_doc WHERE owner IN $owners"
        if kinds:
            sql += " AND kind IN $kinds"
            params["kinds"] = list(kinds)
        if expires_before is not None:
            sql += " AND expires_at != NONE AND expires_at < $expires_before"
            params["expires_before"] = expires_before
        if selectors:
            sql += " AND (" + " OR ".join(selectors) + ")"
        elif anchors is not None or about is not None:
            return []  # selectors were requested but all were empty: nothing can match
        sql += " ORDER BY created_at DESC LIMIT $limit"
        rows = (await self.db.query(sql, params))[0]
        return [SearchDoc.model_validate(decode(r)) for r in rows]

    async def memory_edges(self, edge: str, memories: Sequence[RecordId]) -> list[dict[str, Any]]:
        spec = registry.tables.get(edge)
        if spec is None or spec.edge is None:
            raise ValueError(f"not an edge table: {edge!r}")
        if not memories:
            return []
        rows = (
            await self.db.query(
                f"SELECT *, in.memory AS in_memory, out.memory AS out_memory FROM {_ident(edge)} "
                "WHERE in.memory IN $m OR out.memory IN $m LIMIT 5000",
                {"m": list(memories)},
            )
        )[0]
        return [decode(r) for r in rows]

    async def recent_episodes(
        self, owners: Sequence[RecordId], since: datetime, limit: int
    ) -> list[Episode]:
        rows = (
            await self.db.query(
                "SELECT * FROM episode WHERE owner IN $owners AND occurred_at >= $since "
                "ORDER BY occurred_at DESC LIMIT $limit",
                {"owners": list(owners), "since": since, "limit": limit},
            )
        )[0]
        return [Episode.model_validate(decode(r)) for r in rows]

    async def uncited_episodes(self, before: datetime, limit: int) -> list[RecordId]:
        rows = (
            await self.db.query(
                "SELECT id FROM episode WHERE occurred_at < $before "
                "AND count(<-derived_from) = 0 LIMIT $limit",
                {"before": before, "limit": limit},
            )
        )[0]
        return [decode(r)["id"] for r in rows]

    async def revision_states_at(self, at: datetime, limit: int) -> list[RecordId]:
        rows = (
            await self.db.query(
                "SELECT id FROM rev_state WHERE state = 'committed' AND valid_from <= $at "
                "AND (valid_to = NONE OR valid_to > $at) "
                "AND (retracted_at = NONE OR retracted_at > $at) LIMIT $limit",
                {"at": at, "limit": limit},
            )
        )[0]
        return [decode(r)["id"] for r in rows]

    async def uncited_traces(self, before: datetime, limit: int) -> list[RecordId]:
        rows = (
            await self.db.query(
                "SELECT id FROM recall_trace WHERE created_at < $before "
                "AND id NOT IN (SELECT VALUE trace FROM feedback WHERE trace != NONE) "
                "LIMIT $limit",
                {"before": before, "limit": limit},
            )
        )[0]
        return [decode(r)["id"] for r in rows]

    async def unembedded(self, model: str, limit: int = 64) -> list[SearchDoc]:
        rows = (
            await self.db.query(
                "SELECT * FROM search_doc WHERE rev NOT IN "
                "(SELECT VALUE rev FROM embedding WHERE model = $m) LIMIT $limit",
                {"m": model, "limit": limit},
            )
        )[0]
        return [SearchDoc.model_validate(decode(r)) for r in rows]

    async def edges(
        self,
        edge: str,
        *,
        source: RecordId | None = None,
        target: RecordId | None = None,
        sources: Sequence[RecordId] | None = None,
        targets: Sequence[RecordId] | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        spec = registry.tables.get(edge)
        if spec is None or spec.edge is None:
            raise ValueError(f"not an edge table: {edge!r}")
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": limit}
        if source is not None:
            clauses.append("in = $s")
            params["s"] = source
        if target is not None:
            clauses.append("out = $t")
            params["t"] = target
        if sources is not None:
            clauses.append("in IN $ss")
            params["ss"] = list(sources)
        if targets is not None:
            clauses.append("out IN $ts")
            params["ts"] = list(targets)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = (await self.db.query(f"SELECT * FROM {_ident(edge)}{where} LIMIT $limit", params))[0]
        return [decode(r) for r in rows]

    async def apply(self, writes: Sequence[Write]) -> None:
        if not writes:
            return
        stmts, params = compile_writes(writes)
        await self.db.run_atomic(stmts, params)
