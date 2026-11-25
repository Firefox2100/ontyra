"""Section-aware chunking: a chunk never crosses a top-level section boundary, carries the heading
chain, and starts with a short overlap from the chunk before it in the same section."""

from __future__ import annotations

import re

from ontyra_ingest.models import Block, Chunk

_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _pieces(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    out: list[str] = []
    current = ""
    for sentence in _SENTENCE.split(text):
        while len(sentence) > limit:
            if current:
                out.append(current)
                current = ""
            out.append(sentence[:limit])
            sentence = sentence[limit:]
        if current and len(current) + 1 + len(sentence) > limit:
            out.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        out.append(current)
    return out


def chunk(blocks: list[Block], *, max_chars: int, overlap: int) -> list[Chunk]:
    """Pack consecutive blocks of a section into chunks of at most about `max_chars`."""
    chunks: list[Chunk] = []
    path: list[tuple[int, str]] = []
    body: list[str] = []
    page: int | None = None
    section: list[str] = []
    tail = ""

    def flush() -> None:
        nonlocal tail
        text = "\n\n".join(body).strip()
        body.clear()
        if not text:
            return
        full = f"{tail}\n\n{text}".strip() if tail else text
        chunks.append(Chunk(index=len(chunks), text=full, section=list(section), page=page))
        tail = text[-overlap:] if overlap > 0 else ""

    for block in blocks:
        if block.level > 0:
            flush()
            tail = ""
            while path and path[-1][0] >= block.level:
                path.pop()
            path.append((block.level, block.text))
            section = [title for _, title in path]
            page = block.page
            continue
        for piece in _pieces(block.text, max_chars):
            size = sum(len(b) + 2 for b in body)
            if body and size + len(piece) > max_chars:
                flush()
            if not body:
                page = block.page if block.page is not None else page
            body.append(piece)
    flush()
    return chunks
