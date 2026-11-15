"""Connection abstraction over the SurrealDB Python SDK.

The SDK's capabilities differ by connection mode (see AGENTS.md): this module exposes those
differences as `Capabilities` and offers only operations that behave the same everywhere
(`query`, `run_atomic`, introspection). Nothing outside `ontyra.storage` imports the SDK.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
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


def capabilities_for(url: str) -> Capabilities:
    scheme = urlparse(url).scheme or url.split(":", 1)[0]
    if scheme in ("ws", "wss"):
        return Capabilities("ws", True, True, True, True, False)
    if scheme in ("http", "https"):
        return Capabilities("http", False, False, True, True, False)
    if scheme in _EMBEDDED_SCHEMES:
        return Capabilities("embedded", False, False, False, False, True)
    raise ValueError(f"unsupported database url scheme: {url!r}")


def _map_error(exc: Exception) -> errors.StorageError:
    msg = str(exc)
    low = msg.lower()
    if isinstance(exc, sdk_errors.ConnectionUnavailableError):
        return errors.StorageUnavailableError(msg)
    if "readonly" in low or "is immutable" in low:
        return errors.ImmutableViolationError(msg)
    if "already contains" in low:
        return errors.UniqueViolationError(msg)
    if isinstance(exc, sdk_errors.ValidationError) or "no such field" in low:
        return errors.SchemaViolationError(msg)
    if "failed transaction" in low or ("transaction" in low and "cancel" in low):
        return errors.TransactionAbortedError(msg)
    return errors.StorageError(f"{type(exc).__name__}: {msg}")


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
        """Run SurrealQL; returns one result per statement. Values are SDK-encoded."""
        try:
            params = encode(vars) if vars else None
            return await (self._conn.query(sql, params) if params else self._conn.query(sql))
        except Exception as exc:
            raise _map_error(exc) from exc

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
