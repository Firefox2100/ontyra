"""Connection abstraction over the SurrealDB Python SDK.

The SDK's capabilities differ by connection mode (see AGENTS.md): this module exposes those
differences as `Capabilities` and offers only operations that behave the same everywhere
(`query`, `run_atomic`, introspection). Nothing outside `ontyra.storage` imports the SDK.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlparse

from surrealdb import AsyncSurreal
from surrealdb import errors as sdk_errors

from ontyra.storage import errors
from ontyra.storage.codec import encode

_EMBEDDED_SCHEMES = {"mem", "memory", "file", "surrealkv", "rocksdb"}
_SERVER_SCHEMES = {"ws", "wss", "http", "https"}


@dataclass(frozen=True)
class Capabilities:
    """What this connection mode can do (verified by spikes/surrealdb_probe.py)."""

    mode: str  # "ws" | "http" | "embedded"
    live_queries: bool
    sdk_transactions: bool  # SDK transaction/session objects (we never rely on them)
    export_import: bool
    requires_auth: bool
    exclusive_access: bool  # only one connection may open the data path


def normalize_url(url: str) -> str:
    """Turn the backslashes of a Windows path into forward slashes in an embedded URL.

    `f"surrealkv://{Path.home()}/ontyra.db"` produces `surrealkv://C:\\Users\\me\\...` on Windows,
    and `urlparse` reads `C:` as a host and the rest as a port, so the SDK rejects the URL before
    the engine ever sees the path. The engine resolves the path itself and accepts forward
    slashes on Windows, so the rewrite is enough. Server URLs are left untouched: their authority
    and port are real.
    """

    scheme, separator, rest = url.partition("://")
    if not separator or scheme not in _EMBEDDED_SCHEMES or "\\" not in rest:
        return url
    return f"{scheme}://{rest.replace('\\', '/')}"


def capabilities_for(url: str) -> Capabilities:
    scheme = urlparse(url).scheme or url.split(":", 1)[0]
    if scheme in ("ws", "wss"):
        return Capabilities("ws", True, True, True, True, False)
    if scheme in ("http", "https"):
        return Capabilities("http", False, False, True, True, False)
    if scheme in _EMBEDDED_SCHEMES:
        return Capabilities("embedded", False, False, False, False, True)
    raise ValueError(f"unsupported database url scheme: {url!r}")


def _classify(kind: str, message: str) -> errors.StorageError:
    """Map a SurrealDB error (kind + message) to a storage error."""
    low = message.lower()
    if "ontyra:conflict" in low or "write conflict" in low or "can be retried" in low:
        return errors.ConflictError(message)  # our guard, or the engine's optimistic commit
    if "readonly" in low or "is immutable" in low:
        return errors.ImmutableViolationError(message)
    if "already contains" in low:
        return errors.UniqueViolationError(message)
    if kind.lower() == "validation" or "no such field" in low:
        return errors.SchemaViolationError(message)
    if "failed transaction" in low or "cancelled transaction" in low:
        return errors.TransactionAbortedError(message)
    return errors.StorageError(f"{kind or 'error'}: {message}")


def _map_error(exc: Exception) -> errors.StorageError:
    if isinstance(exc, sdk_errors.ConnectionUnavailableError):
        return errors.StorageUnavailableError(str(exc))
    return _classify(str(getattr(exc, "kind", type(exc).__name__)), str(exc))


_NOT_ROOT_CAUSE = {"NotExecuted", "Cancelled"}


def _detail_kind(statement: dict[str, Any]) -> str | None:
    details: Any = statement.get("details")
    if not isinstance(details, dict):
        return None
    kind: Any = cast("dict[str, Any]", details).get("kind")
    return None if kind is None else str(kind)


class Database:
    """An open, authenticated, namespace/database-selected connection."""

    def __init__(self, conn: Any, url: str, capabilities: Capabilities) -> None:
        self._conn = conn
        self.url = url
        self.capabilities = capabilities

    @classmethod
    async def connect(
        cls,
        url: str,
        *,
        namespace: str,
        database: str,
        username: str | None = None,
        password: str | None = None,
        timeout: float = 30.0,  # noqa: ASYNC109 - SDK request timeout, not asyncio's
    ) -> Database:
        url = normalize_url(url)
        caps = capabilities_for(url)
        try:
            conn: Any = (
                AsyncSurreal(url, timeout=timeout) if caps.mode != "embedded" else AsyncSurreal(url)
            )
            if caps.requires_auth:
                if username is None or password is None:
                    raise errors.StorageError("username and password are required for server mode")
                await conn.signin({"username": username, "password": password})
            await conn.use(namespace, database)
        except errors.StorageError:
            raise
        except Exception as exc:
            raise _map_error(exc) from exc
        return cls(conn, url, caps)

    async def close(self) -> None:
        await self._conn.close()

    async def server_version(self) -> str:
        return str(await self._conn.version())

    async def query(self, sql: str, vars: dict[str, Any] | None = None) -> list[Any]:
        """Run SurrealQL; returns one result per statement. Values are SDK-encoded.

        On failure the *root cause* is raised: inside a transaction every statement reports
        "not executed", and the SDK would surface the first of those instead of the real error.
        """
        try:
            params = encode(vars) if vars else None
            raw = await (self._conn.query_raw(sql, params) if params else self._conn.query_raw(sql))
        except Exception as exc:
            raise _map_error(exc) from exc
        statements = cast("list[dict[str, Any]]", raw["result"] if isinstance(raw, dict) else raw)
        failed = [st for st in statements if st.get("status") != "OK"]
        if failed:
            root = next((st for st in failed if _detail_kind(st) not in _NOT_ROOT_CAUSE), failed[0])
            raise _classify(str(root.get("kind", "")), str(root.get("result")))
        return [st["result"] for st in statements]

    async def run_atomic(
        self, statements: Sequence[str], vars: dict[str, Any] | None = None
    ) -> list[Any]:
        """Run statements as one transaction (`BEGIN … COMMIT` in a single query).

        Portable across every connection mode. Any failing statement (including `THROW`)
        rolls the whole batch back and raises `TransactionAbortedError` or the more
        specific storage error for the cause.
        """
        body = "; ".join(s.strip().rstrip(";") for s in statements if s.strip())
        return await self.query(f"BEGIN; {body}; COMMIT;", vars)

    async def info_db(self) -> dict[str, Any]:
        return (await self.query("INFO FOR DB"))[0]

    async def info_table(self, table: str) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
            raise ValueError(f"unsafe table name: {table!r}")
        return (await self.query(f"INFO FOR TABLE {table}"))[0]
