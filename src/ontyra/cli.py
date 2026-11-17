"""Process entry point: start the daemon and administer the schema.

Not a memory interface: memory operations go through the transports only.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

import uvicorn

from ontyra import __version__
from ontyra.adapters import build_embedder
from ontyra.app import create_rest_app
from ontyra.config import Settings, load_settings
from ontyra.storage import Database, SchemaManager


async def _db_command(settings: Settings, command: str, *, yes: bool) -> int:
    db = await Database.connect(
        settings.db.url,
        namespace=settings.db.namespace,
        database=settings.db.database,
        username=settings.db.username,
        password=settings.db.password.get_secret_value(),
        timeout=settings.db.timeout,
    )
    try:
        embedder = build_embedder(settings.embedding)
        manager = SchemaManager(db, embedding_dim=embedder.dimension if embedder else None)
        target = f"{settings.db.url} ns={settings.db.namespace} db={settings.db.database}"
        if command == "apply":
            await manager.apply()
            print(f"schema applied to {target}")
        elif command == "sync":
            removed = await manager.sync()
            print(f"schema applied to {target}; removed {len(removed)} stale item(s)")
            for stmt in removed:
                print(f"  {stmt}")
        elif command == "wipe":
            if not yes:
                print(f"refusing to wipe {target} without --yes", file=sys.stderr)
                return 2
            await manager.wipe()
            print(f"wiped every Ontyra table in {target}")
        elif command == "status":
            info = await db.info_db()
            drift = await manager.drift()
            print(f"{target}\n  server {await db.server_version()}  mode {db.capabilities.mode}")
            print(f"  tables: {len(info.get('tables', {}))}  pending schema changes: {len(drift)}")
            for stmt in drift:
                print(f"  {stmt}")
        return 0
    finally:
        await db.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ontyra", description="Ontyra memory daemon")
    parser.add_argument("--version", action="version", version=f"ontyra {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="start the REST daemon")
    serve.add_argument("--host", help="bind address (default: ONTYRA_SERVER__HOST)")
    serve.add_argument("--port", type=int, help="port (default: ONTYRA_SERVER__PORT)")
    db = sub.add_parser("db", help="schema administration")
    db.add_argument("action", choices=["apply", "sync", "wipe", "status"])
    db.add_argument("--yes", action="store_true", help="confirm destructive actions (wipe)")
    args = parser.parse_args(argv)

    settings = load_settings()
    if args.command == "serve":
        host = args.host or settings.server.host
        port = args.port or settings.server.port
        settings = settings.for_bind(host, port)
        uvicorn.run(
            create_rest_app(settings), host=host, port=port, log_level=settings.log_level.lower()
        )
        return 0
    return asyncio.run(_db_command(settings, args.action, yes=args.yes))


if __name__ == "__main__":
    raise SystemExit(main())
