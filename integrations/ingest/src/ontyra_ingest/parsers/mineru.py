"""MinerU over its HTTP API (`POST /file_parse`): layout-aware PDF parsing."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, cast

import httpx

from ontyra_ingest.models import Block, ParsedDocument


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


def blocks_from(items: list[dict[str, Any]]) -> list[Block]:
    blocks: list[Block] = []
    for item in items:
        kind = item.get("type")
        page = item.get("page_idx")
        page_no = page + 1 if isinstance(page, int) else None
        if kind == "text":
            text = str(item.get("text") or "").strip()
            if text:
                blocks.append(
                    Block(text=text, level=int(item.get("text_level") or 0), page=page_no)
                )
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
