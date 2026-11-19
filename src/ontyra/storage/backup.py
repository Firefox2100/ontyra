"""Export and import of a whole database as SurrealQL text, over the server's HTTP endpoints.

Server mode only (`ws://`, `http://`): an embedded engine has no export, and the daemon that owns
it is the only process that can open it. The export is the server's own (`/export`: schema,
analyzers, every record), so it can be imported with `/import` into an empty database of the same
or a newer server. Files are written readable by the owner only; they hold all memories.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from ontyra.storage import errors


def http_base(url: str) -> str:
    """The server's HTTP root for a `ws://host:port/rpc` (or `http://`) connection URL."""
    parts = urlparse(url)
    if parts.scheme not in ("ws", "wss", "http", "https"):
        raise errors.StorageError(
            "export and import need a SurrealDB server (ws:// or http://); an embedded database "
            "is owned by the daemon process"
        )
    scheme = {"ws": "http", "wss": "https"}.get(parts.scheme, parts.scheme)
    return urlunparse((scheme, parts.netloc, "", "", "", ""))


def _headers(namespace: str, database: str) -> dict[str, str]:
    return {"surreal-ns": namespace, "surreal-db": database, "Accept": "application/octet-stream"}


async def export_sql(
    url: str,
    *,
    namespace: str,
    database: str,
    username: str,
    password: str,
    timeout: float = 600,  # noqa: ASYNC109 - HTTP timeout
) -> str:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                f"{http_base(url)}/export",
                headers=_headers(namespace, database),
                auth=(username, password),
            )
    except httpx.HTTPError as exc:
        raise errors.StorageError(f"export failed: {exc!r}") from exc
    if response.status_code >= 400:
        raise errors.StorageError(f"export refused: HTTP {response.status_code}")
    return response.text


async def import_sql(
    url: str,
    text: str,
    *,
    namespace: str,
    database: str,
    username: str,
    password: str,
    timeout: float = 600,  # noqa: ASYNC109 - HTTP timeout
) -> None:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{http_base(url)}/import",
                headers={
                    **_headers(namespace, database),
                    "Accept": "application/json",
                    "Content-Type": "text/plain",
                },
                auth=(username, password),
                content=text.encode("utf-8"),
            )
    except httpx.HTTPError as exc:
        raise errors.StorageError(f"import failed: {exc!r}") from exc
    if response.status_code >= 400:
        raise errors.StorageError(f"import refused: HTTP {response.status_code}")
    results: list[Any] = response.json()
    failed: list[Any] = [r for r in results if isinstance(r, dict) and r.get("status") == "ERR"]  # type: ignore[reportUnknownMemberType]
    if failed:
        raise errors.StorageError(f"import had {len(failed)} failing statement(s): {failed[0]}")


def write_private(path: Path, text: str) -> None:
    """Write `text` to `path`, readable by the owner only (created that way, not chmod-ed after)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)


def default_name(now: datetime) -> str:
    return f"ontyra-{now:%Y%m%d-%H%M%S}.surql"


def prune(directory: Path, keep: int) -> list[Path]:
    """Delete the oldest `ontyra-*.surql` exports in `directory` beyond the newest `keep`."""
    found = sorted(directory.glob("ontyra-*.surql"))
    stale = found[: max(0, len(found) - keep)]
    for path in stale:
        path.unlink()
    return stale
