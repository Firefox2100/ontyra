"""The basic parser: plain text, Markdown, HTML and (text-layer) PDF, with no external service."""

from __future__ import annotations

import asyncio
import re
from html.parser import HTMLParser
from pathlib import Path

from ontyra_ingest.models import Block, ParsedDocument

_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_SKIP = {"script", "style", "noscript", "template", "head"}
_BLOCK_TAGS = {"p", "div", "li", "tr", "br", "section", "article", "pre", "blockquote", "table"}


class _Html(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[Block] = []
        self.title: str | None = None
        self._buf: list[str] = []
        self._skip = 0
        self._level = 0
        self._in_title = False

    def flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        self._buf = []
        if text:
            self.blocks.append(Block(text=text, level=self._level))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif re.fullmatch(r"h[1-6]", tag):
            self.flush()
            self._level = int(tag[1])
        elif tag in _BLOCK_TAGS:
            self.flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False
        elif re.fullmatch(r"h[1-6]", tag):
            self.flush()
            self._level = 0
        elif tag in _BLOCK_TAGS:
            self.flush()

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title = (self.title or "") + data.strip()
        elif not self._skip:
            self._buf.append(data)


def _text_blocks(text: str, *, markdown: bool) -> list[Block]:
    blocks: list[Block] = []
    para: list[str] = []

    def flush() -> None:
        body = " ".join(line.strip() for line in para).strip()
        para.clear()
        if body:
            blocks.append(Block(text=body))

    for line in text.splitlines():
        heading = _HEADING.match(line) if markdown else None
        if heading:
            flush()
            blocks.append(Block(text=heading.group(2), level=len(heading.group(1))))
        elif not line.strip():
            flush()
        else:
            para.append(line)
    flush()
    return blocks


def _pdf_blocks(path: Path) -> list[Block]:
    from pypdf import PdfReader

    blocks: list[Block] = []
    for number, page in enumerate(PdfReader(str(path)).pages, start=1):
        for chunk in re.split(r"\n\s*\n", page.extract_text() or ""):
            body = re.sub(r"\s+", " ", chunk).strip()
            if body:
                blocks.append(Block(text=body, page=number))
    return blocks


class BuiltinParser:
    name = "builtin"
    version = "1"
    suffixes = frozenset({".txt", ".md", ".markdown", ".rst", ".html", ".htm", ".pdf"})

    async def parse(self, path: Path) -> ParsedDocument:
        return await asyncio.to_thread(self._parse, path)

    def _parse(self, path: Path) -> ParsedDocument:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return ParsedDocument(blocks=_pdf_blocks(path))
        raw = path.read_text(encoding="utf-8", errors="replace")
        if suffix in {".html", ".htm"}:
            parser = _Html()
            parser.feed(raw)
            parser.flush()
            return ParsedDocument(blocks=parser.blocks, title=parser.title)
        blocks = _text_blocks(raw, markdown=suffix in {".md", ".markdown"})
        title = next((b.text for b in blocks if b.level == 1), None)
        return ParsedDocument(blocks=blocks, title=title)
