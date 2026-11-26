"""`ontyra-ingest`: read documents, extract knowledge with sources, propose it to the daemon."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

import typer

from ontyra_ingest import identity
from ontyra_ingest.chunking import chunk as make_chunks
from ontyra_ingest.config import Settings
from ontyra_ingest.daemon import ApiError, Daemon
from ontyra_ingest.llm import Llm
from ontyra_ingest.parsers import ParserError, choose, factories
from ontyra_ingest.pipeline import Pipeline, Report
from ontyra_ingest.profiles import all_profiles

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Bulk ingestion for Ontyra: documents in, sourced knowledge proposed to the daemon.",
)

SUFFIXES = {".txt", ".md", ".markdown", ".rst", ".html", ".htm", ".pdf"}


def _settings(**overrides: Any) -> Settings:
    return Settings(**{k: v for k, v in overrides.items() if v not in (None, [])})


def _files(paths: list[Path]) -> list[Path]:
    found: list[Path] = []
    for p in paths:
        if p.is_dir():
            found += sorted(f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in SUFFIXES)
        elif p.is_file():
            found.append(p)
        else:
            raise typer.BadParameter(f"{p} does not exist")
    return found


@app.command()
def run(
    paths: Annotated[list[Path], typer.Argument(help="Files or folders (searched recursively).")],
    profile: Annotated[
        list[str] | None, typer.Option("--profile", "-p", help="claims, datasets (repeatable).")
    ] = None,
    parser: Annotated[str | None, typer.Option(help="auto, builtin, mineru or a plugin.")] = None,
    mineru_url: Annotated[
        str | None, typer.Option(help="MinerU API, e.g. http://host:10010")
    ] = None,
    identity_finder: Annotated[
        str | None, typer.Option("--identity", help="file or basic (DOI/arXiv from the text).")
    ] = None,
    model: Annotated[str | None, typer.Option(help="Extraction model name.")] = None,
    llm_url: Annotated[str | None, typer.Option(help="OpenAI-compatible base URL.")] = None,
    topic: Annotated[
        str | None, typer.Option(help="What the documents are about (else the model names it).")
    ] = None,
    alias: Annotated[list[str] | None, typer.Option(help="Other names of the topic.")] = None,
    scope: Annotated[
        str | None,
        typer.Option(help="Scope of the detailed entries; unset: the daemon's (reference)."),
    ] = None,
    concepts: Annotated[
        str | None, typer.Option(help="existing: reuse concepts; create: define new subjects.")
    ] = None,
    url: Annotated[str | None, typer.Option(help="Ontyra daemon URL.")] = None,
    concurrency: Annotated[int | None, typer.Option(min=1)] = None,
    chunk_chars: Annotated[int | None, typer.Option(min=500)] = None,
    min_usefulness: Annotated[int | None, typer.Option(min=0, max=5)] = None,
    state_dir: Annotated[Path | None, typer.Option(help="Where progress is kept.")] = None,
    dry_run: Annotated[bool, typer.Option(help="Extract and check only; write nothing.")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="One JSON report per document.")] = False,
) -> None:
    """Ingest documents. Safe to repeat: finished work is not redone or resent."""
    settings = _settings(
        profiles=profile, parser=parser, mineru_url=mineru_url, identity=identity_finder,
        llm_model=model, llm_url=llm_url, concepts=concepts, topic=topic, aliases=alias,
        scope=scope, url=url, concurrency=concurrency,
        chunk_chars=chunk_chars, min_usefulness=min_usefulness, state_dir=state_dir,
    )  # fmt: skip
    files = _files(paths)
    if not files:
        typer.echo("nothing to ingest", err=True)
        raise typer.Exit(1)
    reports = asyncio.run(_run(settings, files, dry_run))
    for report in reports:
        typer.echo(json.dumps(asdict(report)) if as_json else report.line())
        for error in dict.fromkeys(report.errors):
            typer.echo(f"  ! {error}", err=True)
    if any(r.errors or r.failed for r in reports):
        raise typer.Exit(2)


async def _run(settings: Settings, files: list[Path], dry_run: bool) -> list[Report]:
    llm = Llm(settings)
    daemon = None if dry_run else Daemon(settings)
    try:
        if daemon:
            try:
                await daemon.health()
            except ApiError as exc:
                typer.echo(f"daemon: {exc}", err=True)
                raise typer.Exit(2) from exc
        pipeline = Pipeline(settings, llm, daemon)
        return [await pipeline.ingest(f, dry_run=dry_run) for f in files]
    finally:
        await llm.close()
        if daemon:
            await daemon.close()


@app.command()
def parse(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    parser: Annotated[str | None, typer.Option()] = None,
    mineru_url: Annotated[str | None, typer.Option()] = None,
    chunk_chars: Annotated[int, typer.Option(min=500)] = 3500,
) -> None:
    """Show how a file is read and cut into chunks (no model, no daemon)."""
    settings = _settings(parser=parser, mineru_url=mineru_url)
    try:
        parsed = asyncio.run(choose(settings, path).parse(path))
    except ParserError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    chunks = make_chunks(parsed.blocks, max_chars=chunk_chars, overlap=settings.overlap_chars)
    typer.echo(f"title: {parsed.title}\nblocks: {len(parsed.blocks)}\nchunks: {len(chunks)}")
    for c in chunks:
        typer.echo(f"\n--- chunk {c.index} [{c.locator}] {len(c.text)} chars\n{c.text[:300]}")


@app.command()
def plugins() -> None:
    """List what can be chosen: parsers, identity finders and profiles."""
    typer.echo("parsers:   " + ", ".join(sorted(factories())))
    typer.echo("identity:  " + ", ".join(sorted(identity.finders())))
    typer.echo("profiles:  " + ", ".join(all_profiles()))


def main() -> None:
    app()
