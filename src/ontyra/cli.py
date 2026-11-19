"""Process entry point: start the daemon and administer the schema.

Not a memory interface: memory operations go through the transports only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import uvicorn

from ontyra import __version__
from ontyra.adapters import build_embedder
from ontyra.app import create_rest_app
from ontyra.config import Settings, load_settings
from ontyra.storage import Database, SchemaManager


async def _backup_command(
    settings: Settings, command: str, path: str | None, *, keep: int | None, yes: bool
) -> int:
    """`db export` (a SurrealQL file of the whole database) and `db import` (into an empty one)."""
    from datetime import UTC, datetime

    from ontyra.storage import backup

    cfg = settings.db
    creds = {
        "namespace": cfg.namespace,
        "database": cfg.database,
        "username": cfg.username,
        "password": cfg.password.get_secret_value(),
    }
    target = f"{cfg.url} ns={cfg.namespace} db={cfg.database}"
    if command == "export":
        file = Path(path or "backups")
        if file.suffix != ".surql":
            file = file / backup.default_name(datetime.now(UTC))
        text = await backup.export_sql(cfg.url, timeout=600, **creds)
        await asyncio.to_thread(backup.write_private, file, text)
        print(f"exported {target} to {file} ({len(text) // 1024} KiB)")
        if keep is not None:
            for old in await asyncio.to_thread(backup.prune, file.parent, keep):
                print(f"  removed older export {old}")
        return 0
    if not path:
        print("db import needs the file to import", file=sys.stderr)
        return 2
    db = await Database.connect(
        cfg.url,
        namespace=cfg.namespace,
        database=cfg.database,
        username=cfg.username,
        password=cfg.password.get_secret_value(),
        timeout=cfg.timeout,
    )
    try:
        try:
            rows: list[Any] = (await db.query("SELECT count() FROM memory GROUP ALL"))[0]
        except Exception:  # no schema yet: an empty database
            rows = []
        if rows and rows[0].get("count"):
            print(
                f"{target} already holds memories: import only into an empty database",
                file=sys.stderr,
            )
            return 2
    finally:
        await db.close()
    if not yes:
        print(f"refusing to import {path} into {target} without --yes", file=sys.stderr)
        return 2
    text = await asyncio.to_thread(Path(path).read_text, "utf-8")
    await backup.import_sql(cfg.url, text, timeout=600, **creds)
    print(f"imported {path} into {target}")
    return 0


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


async def _own_command(settings: Settings, *, yes: bool) -> int:
    from ontyra.calibration import own

    if settings.recall.engine != "fusion":
        print("The check needs ONTYRA_RECALL__ENGINE=fusion.", file=sys.stderr)
        return 2
    print(
        "The check reads your configured database (nothing is written) and embeds about 60 of its "
        f"memories' first words plus the benchmark's off-topic queries with "
        f"{settings.embedding.provider} {settings.embedding.name or ''}."
    )
    if settings.embedding.provider != "hash" and not yes:
        print("Re-run with --yes to call the embedder (it may be billed).", file=sys.stderr)
        return 2
    print("\n" + own.render(await own.probe(settings, progress=print)))
    return 0


def _confined_path(raw: str) -> Path:
    """Resolve a calibration path and refuse to leave the directory the command was started in.

    An agent may invoke this CLI, so a path taken from its arguments must not become a way to
    read or overwrite files the operator never named (Sonar S8707). The recording is an artifact
    of the run, written next to it; `--from` reads it back.
    """
    base = os.path.realpath(os.getcwd())
    resolved = os.path.realpath(raw)
    if resolved != base and not resolved.startswith(base + os.sep):
        raise SystemExit(f"refusing {raw!r}: it is outside {base}")
    return Path(resolved)


async def _calibrate_command(
    settings: Settings, saved: dict[str, Any] | None, out: Path | None, *, yes: bool
) -> int:
    from ontyra.calibration import runner

    if saved is not None:
        recording = saved["recording"]
    else:
        need = runner.estimate(settings)
        print(
            f"Calibration will embed up to {need['embedding_texts_at_most']} texts "
            f"({need['memories']} memories, {need['recall_requests']} requests, "
            f"{need['pair_texts']} statement pairs) with {settings.embedding.provider} "
            f"{settings.embedding.name or ''}."
        )
        if need["rerank_requests"]:
            print(f"It will also make {need['rerank_requests']} rerank requests.")
        if need["decision_requests"]:
            print(f"It will also make {need['decision_requests']} decision-model requests.")
        paid = (
            settings.embedding.provider != "hash"
            or need["rerank_requests"]
            or need["decision_requests"]
        )
        if paid and not yes:
            print("Re-run with --yes to call the models (they may be billed).", file=sys.stderr)
            return 2
        recording = await runner.record(settings, progress=print)
        print(
            f"{recording['embedding_requests']} embedding requests "
            f"({recording['embedding_texts']} texts) in {recording['seconds']} s"
        )
    report = runner.analyse(recording)
    print("\n" + runner.render(report))
    if out:
        await asyncio.to_thread(runner.save, out, recording, report)
        print(f"\nsaved to {out} (re-read with --from, no model calls)")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ontyra", description="Ontyra memory daemon")
    parser.add_argument("--version", action="version", version=f"ontyra {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="start the REST daemon")
    serve.add_argument("--host", help="bind address (default: ONTYRA_SERVER__HOST)")
    serve.add_argument("--port", type=int, help="port (default: ONTYRA_SERVER__PORT)")
    db = sub.add_parser("db", help="schema administration")
    db.add_argument("action", choices=["apply", "sync", "wipe", "status", "export", "import"])
    db.add_argument(
        "path", nargs="?", help="export: file or directory (default ./backups); import: file"
    )
    db.add_argument(
        "--keep", type=int, help="export: keep only this many newest exports in the directory"
    )
    db.add_argument("--yes", action="store_true", help="confirm destructive actions (wipe)")
    cal = sub.add_parser(
        "calibrate",
        help="suggest recall and duplicate-scan thresholds for the configured embedder",
        description="Runs the bundled labelled benchmark through the configured embedder (this "
        "calls it, once; embedding requests are listed first) and prints suggested settings. "
        "Nothing is changed; the deployed database is not touched.",
    )
    cal.add_argument("--yes", action="store_true", help="run, calling the configured embedder")
    cal.add_argument("--out", help="write the recording and report here (JSON)")
    cal.add_argument(
        "--own",
        action="store_true",
        help="instead: check the abstention threshold on your own store (read-only)",
    )
    cal.add_argument("--from", dest="source", help="analyse a saved recording: no model calls")
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
    if args.command == "calibrate" and args.own:
        return asyncio.run(_own_command(settings, yes=args.yes))
    if args.command == "calibrate":
        saved = json.loads(_confined_path(args.source).read_text("utf-8")) if args.source else None
        out = _confined_path(args.out) if args.out else None
        return asyncio.run(_calibrate_command(settings, saved, out, yes=args.yes))
    if args.action in ("export", "import"):
        return asyncio.run(
            _backup_command(settings, args.action, args.path, keep=args.keep, yes=args.yes)
        )
    return asyncio.run(_db_command(settings, args.action, yes=args.yes))


if __name__ == "__main__":
    raise SystemExit(main())
