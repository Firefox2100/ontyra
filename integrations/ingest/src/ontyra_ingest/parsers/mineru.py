"""MinerU 4 over its Tasks API (`/v1/tasks`): layout-aware PDF parsing."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import zipfile
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


class _Rows(HTMLParser):
    """Table rows as a grid: a cell that spans rows or columns is repeated into the cells it covers,
    so every row has a value under every header (a row after a `rowspan` has fewer `<td>`s, and
    read by position its values would land under the wrong headers)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._cell: list[str] | None = None
        self._span = (1, 1)  # rows, columns of the open cell
        self._carry: dict[int, tuple[int, str]] = {}  # column -> (rows still to cover, value)

    def _fill(self) -> None:
        """Put the values carried down from earlier rows at the current position."""
        row = self.rows[-1]
        while (col := len(row)) in self._carry:
            left, value = self._carry[col]
            row.append(value)
            if left <= 1:
                del self._carry[col]
            else:
                self._carry[col] = (left - 1, value)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.rows.append([])
            self._fill()
        elif tag == "sup" and self._cell is not None:
            self._cell.append("^")  # a footnote mark: "PSRAM^2", not a model called "PSRAM2"
        elif tag in ("td", "th"):
            self._cell = []
            wanted = {k: v for k, v in attrs}
            self._span = (_span(wanted.get("rowspan")), _span(wanted.get("colspan")))

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None and self.rows:
            value = re.sub(r"\s+", " ", "".join(self._cell)).strip()
            rows, cols = self._span
            row = self.rows[-1]
            for _ in range(cols):
                if rows > 1:
                    self._carry[len(row)] = (rows - 1, value)
                row.append(value)
                self._fill()
            self._cell = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _span(value: str | None) -> int:
    try:
        return max(1, min(int(value or 1), 100))
    except ValueError:
        return 1


_RULE = re.compile(r"^:?-{2,}:?$")
_LOOSE_TAG = re.compile(r"</?(?:sup|sub)>")


def _mark(match: re.Match[str]) -> str:
    return "^" if match.group(0) == "<sup>" else ""


def _pipe_rows(text: str) -> list[list[str]]:
    """Rows of a markdown table (`| a | b |`); MinerU writes simple tables this way, merged cells
    it writes as HTML. The `---` rule under the header is not a row."""
    rows: list[list[str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        parts = line.strip("|").split("|")
        cells = [re.sub(r"\s+", " ", _LOOSE_TAG.sub(_mark, c)).strip() for c in parts]
        if not all(_RULE.match(c) for c in cells if c):
            rows.append(cells)
    return rows


def table_lines(content: str) -> list[str]:
    """One self-describing line per table row, `Header: value; Header: value`, so a row can be
    read, quoted and chunked on its own. A table without a usable header keeps plain cells."""
    if "<table" in content or "<tr" in content:
        parser = _Rows()
        parser.feed(content)
        rows = [r for r in parser.rows if any(r)]
    else:
        rows = [r for r in _pipe_rows(content) if any(r)]
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


_NUMBERED = re.compile(r"^(\d+(?:\.\d+)*)\.?\s")
_SKIPPED = frozenset({"header", "footer", "page_number", "aside_text", "index"})
_LOOSE = re.compile(r"</?(?:sup|sub)>")


def _text(value: Any) -> str:
    return plain_math(_LOOSE.sub("", str(value or "")).strip())


def _notes(items: Any) -> str:
    """Captions or footnotes: a list of `{bbox, content}`."""
    rows = cast(list[Any], items or [])
    parts = [_text(cast(dict[str, Any], r).get("content")) for r in rows if isinstance(r, dict)]
    return " ".join(p for p in parts if p)


def _level(text: str) -> int:
    """A section title's depth from its number ("4.5.3" is deeper than "4"); MinerU gives every
    title the same level. A label such as "Note:" opens no section."""
    if _LABEL.match(text):
        return 0
    numbered = _NUMBERED.match(text)
    return 2 + numbered.group(1).count(".") if numbered else 2


def blocks_from(pages: list[dict[str, Any]]) -> list[Block]:
    """Blocks from MinerU's structured content (`pages[].blocks[]`). Running headers and footers,
    page numbers, margin text and the table of contents are not content and are left out."""
    blocks: list[Block] = []
    for page in pages:
        idx = page.get("page_idx")
        page_no = idx + 1 if isinstance(idx, int) else None
        for item in cast(list[dict[str, Any]], page.get("blocks") or []):
            kind = item.get("type")
            if kind in _SKIPPED:
                continue
            if kind == "table":
                caption = " ".join(
                    p for p in (_notes(item.get("captions")), _notes(item.get("footnotes"))) if p
                )
                if caption:
                    blocks.append(Block(text=caption, page=page_no))
                for line in table_lines(str(item.get("content") or "")):
                    blocks.append(Block(text=line, page=page_no))
            elif kind in ("image", "chart"):
                if caption := _notes(item.get("captions")):
                    blocks.append(Block(text=caption, page=page_no))
            elif kind == "list":
                for line in str(item.get("content") or "").splitlines():
                    if entry := _text(line.lstrip("-*• ")):
                        blocks.append(Block(text=entry, page=page_no))
            elif text := _text(item.get("content")):
                if kind == "doc_title":
                    level = 1
                elif kind == "paragraph_title":
                    level = _level(text)
                else:
                    level = 0
                blocks.append(Block(text=text, level=level, page=page_no))
    return blocks


class MinerUParser:
    name = "mineru"
    suffixes = frozenset({".pdf"})
    reading = "2"  # our reading of MinerU's output; 2: markdown tables are read, not dropped

    def __init__(self, url: str, tier: str | None, timeout: float) -> None:
        self._url = url.rstrip("/")
        self._tier = tier
        self._timeout = timeout
        self.version = f"{self.reading}:{tier or 'auto'}"

    async def parse(self, path: Path) -> ParsedDocument:
        data = await asyncio.to_thread(path.read_bytes)
        manifest: dict[str, Any] = {
            "files": [
                {
                    "file_name": path.name,
                    "bytes": len(data),
                    "sha256sum": hashlib.sha256(data).hexdigest(),
                }
            ],
            "output_formats": ["default"],
        }
        if self._tier:
            manifest["tier"] = self._tier
        async with httpx.AsyncClient(timeout=60.0) as client:
            created = await client.post(f"{self._url}/v1/tasks", json=manifest)
            created.raise_for_status()
            task = cast(dict[str, Any], created.json())
            entry = cast(dict[str, Any], task["files"][0])
            if upload := cast(dict[str, Any] | None, entry.get("upload")):
                put = await client.put(
                    str(upload["url"]),
                    content=data,
                    headers=cast(dict[str, str], upload.get("headers") or {}),
                    timeout=self._timeout,
                )
                put.raise_for_status()
            detail = await self._wait(client, str(task["task_id"]))
            result = cast(dict[str, Any], detail["files"][0])
            if result.get("status") != "completed":
                error = cast(dict[str, Any], result.get("error") or {})
                why = error.get("message") or result.get("status")
                raise RuntimeError(f"MinerU could not parse {path.name}: {why}")
            archive = await client.get(str(result["result_url"]), timeout=self._timeout)
            archive.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(archive.content)) as zf:
            structured = json.loads(zf.read("structured_content.json"))
        blocks = blocks_from(cast(list[dict[str, Any]], structured.get("pages") or []))
        title = next((b.text for b in blocks if b.level == 1), None)
        return ParsedDocument(blocks=blocks, title=title)

    async def _wait(self, client: httpx.AsyncClient, task_id: str) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout
        while True:
            response = await client.get(f"{self._url}/v1/tasks/{task_id}")
            response.raise_for_status()
            detail = cast(dict[str, Any], response.json())
            if detail.get("status") in ("completed", "failed", "partial", "canceled"):
                return detail
            if loop.time() > deadline:
                raise TimeoutError(f"MinerU did not finish task {task_id} in {self._timeout:.0f}s")
            await asyncio.sleep(2)
