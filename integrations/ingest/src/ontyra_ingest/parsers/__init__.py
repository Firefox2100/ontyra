"""Parsers turn a file into blocks. Built-ins: `builtin`, `mineru`. More register through the
`ontyra_ingest.parsers` entry-point group (name -> factory taking the settings)."""

from __future__ import annotations

from collections.abc import Callable
from importlib.metadata import entry_points
from pathlib import Path
from typing import Protocol

from ontyra_ingest.config import Settings
from ontyra_ingest.models import ParsedDocument


class Parser(Protocol):
    name: str
    suffixes: frozenset[str]

    async def parse(self, path: Path) -> ParsedDocument: ...


ParserFactory = Callable[[Settings], Parser]


class ParserError(Exception):
    pass


def _builtin(settings: Settings) -> Parser:
    from ontyra_ingest.parsers.builtin import BuiltinParser

    return BuiltinParser()


def _mineru(settings: Settings) -> Parser:
    from ontyra_ingest.parsers.mineru import MinerUParser

    if not settings.mineru_url:
        raise ParserError("the mineru parser needs ONTYRA_INGEST_MINERU_URL (or --mineru-url)")
    return MinerUParser(settings.mineru_url, settings.mineru_backend, settings.mineru_timeout)


def factories() -> dict[str, ParserFactory]:
    found: dict[str, ParserFactory] = {"builtin": _builtin, "mineru": _mineru}
    for ep in entry_points(group="ontyra_ingest.parsers"):
        found[ep.name] = ep.load()
    return found


def choose(settings: Settings, path: Path) -> Parser:
    """The parser named by the settings; `auto` takes MinerU for a PDF when it is configured and
    the built-in parser otherwise."""
    available = factories()
    name = settings.parser
    if name == "auto":
        name = "mineru" if path.suffix.lower() == ".pdf" and settings.mineru_url else "builtin"
    if name not in available:
        raise ParserError(f"unknown parser {name!r}; available: {', '.join(sorted(available))}")
    parser = available[name](settings)
    if path.suffix.lower() not in parser.suffixes:
        raise ParserError(f"parser {parser.name!r} does not read {path.suffix or 'this'} files")
    return parser
