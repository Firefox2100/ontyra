"""MinerU over its HTTP API (`POST /file_parse`): layout-aware PDF parsing."""

from __future__ import annotations

import asyncio
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, cast

import httpx

from ontyra_ingest.models import Block, ParsedDocument

_TEX = {
    "times": "×", "dagger": "†", "circ": "°", "pm": "±", "mu": "µ", "Omega": "Ω", "sim": "~",
    "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "approx": "≈", "rightarrow": "→",
}  # fmt: skip
_COMMAND = re.compile(r"\\([A-Za-z]+)")
_LABEL = re.compile(r"^(notes?|warnings?|cautions?|tips?|important|example)s?\\s*:?$|.*:$", re.I)
_MATH = re.compile(r"\$([^$]*)\$")


def _plain(tex: str) -> str:
    """A formula MinerU wrote as LaTeX, read as plain text: `\\mathsf { V } _ { I H }` -> `V_IH`."""
    tex = _COMMAND.sub(lambda m: _TEX.get(m.group(1), ""), tex)  # wrappers and unknowns vanish
    return re.sub(r"\s+", "", tex.replace("{", "").replace("}", ""))


def plain_math(text: str) -> str:
    return _MATH.sub(lambda m: _plain(m.group(1)), text) if "$" in text else text


def content_list(payload: Any) -> list[dict[str, Any]]:
    """The content list in whichever envelope the service answers with."""
    if isinstance(payload, list):
        return cast(list[dict[str, Any]], payload)
    if not isinstance(payload, dict):
        raise ValueError("MinerU answered with neither a list nor an object")
    body = cast(dict[str, Any], payload)
    candidates: list[Any] = [body.get("content_list")]
    results = body.get("results")
    if isinstance(results, dict):
        for r in cast(dict[str, Any], results).values():
            if isinstance(r, dict):
                candidates.append(cast(dict[str, Any], r).get("content_list"))
    for value in candidates:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                continue
        if isinstance(value, list):
            return cast(list[dict[str, Any]], value)
    raise ValueError("MinerU's answer has no content_list")


class _Rows(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.rows.append([])
        elif tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None and self.rows:
            self.rows[-1].append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def table_lines(html: str) -> list[str]:
    """One self-describing line per table row, `Header: value; Header: value`, so a row can be
    read, quoted and chunked on its own. A table without a usable header keeps plain cells."""
    parser = _Rows()
    parser.feed(html)
    rows = [r for r in parser.rows if any(r)]
    if len(rows) < 2:
        return ["; ".join(c for r in rows for c in r if c)] if rows else []
    header, body = rows[0], rows[1:]
    lines: list[str] = []
    for row in body:
        cells = [
            f"{header[i]}: {v}" if i < len(header) and header[i] else v
            for i, v in enumerate(row)
            if v
        ]
        if cells:
            lines.append("; ".join(plain_math(c) for c in cells))
    return lines


def blocks_from(items: list[dict[str, Any]]) -> list[Block]:
    blocks: list[Block] = []
    for item in items:
        kind = item.get("type")
        page = item.get("page_idx")
        page_no = page + 1 if isinstance(page, int) else None
        if kind == "text":
            text = plain_math(str(item.get("text") or "").strip())
            if text:
                level = int(item.get("text_level") or 0)
                if level and _LABEL.match(text):
                    level = (
                        0  # "Note:", "Warning" introduce the text below, they do not open a section
                    )
                if text:
                    blocks.append(Block(text=text, level=level, page=page_no))
        elif kind == "list":
            entries: list[Any] = item.get("list_items") or []
            for entry in entries:
                if str(entry).strip():
                    blocks.append(Block(text=str(entry).strip(), page=page_no))
        elif kind == "table":
            caption: list[Any] = item.get("table_caption") or []
            footnote: list[Any] = item.get("table_footnote") or []
            text = " ".join(str(p).strip() for p in [*caption, *footnote] if str(p).strip())
            if text:
                blocks.append(Block(text=text, page=page_no))
            for line in table_lines(str(item.get("table_body") or "")):
                blocks.append(Block(text=line, page=page_no))
    return blocks


class MinerUParser:
    name = "mineru"
    suffixes = frozenset({".pdf"})

    def __init__(self, url: str, backend: str | None, timeout: float) -> None:
        self._url = url.rstrip("/")
        self._backend = backend
        self._timeout = timeout

    async def parse(self, path: Path) -> ParsedDocument:
        data: dict[str, Any] = {
            "return_content_list": True,
            "return_md": False,
            "return_images": False,
            "parse_method": "auto",
            "lang_list": "en",
        }
        if self._backend:
            data["backend"] = self._backend
        async with httpx.AsyncClient(base_url=self._url, timeout=self._timeout) as client:
            response = await client.post(
                "/file_parse",
                files={
                    "files": (
                        path.name,
                        await asyncio.to_thread(path.read_bytes),
                        "application/pdf",
                    )
                },
                data=data,
            )
            response.raise_for_status()
        blocks = blocks_from(content_list(response.json()))
        title = next((b.text for b in blocks if b.level == 1), None)
        return ParsedDocument(blocks=blocks, title=title)
