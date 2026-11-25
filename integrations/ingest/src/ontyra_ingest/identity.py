"""Identity finders decide the URI a document is cited by. `file` (default) uses the file path;
`basic` prefers a DOI or arXiv id printed at the top of the document. More register through the
`ontyra_ingest.identity` entry-point group (name -> `IdentityFinder`)."""

from __future__ import annotations

import re
from collections.abc import Callable
from importlib.metadata import entry_points

from ontyra_ingest.models import ParsedDocument

IdentityFinder = Callable[[ParsedDocument], dict[str, str]]

_DOI = re.compile(r"\b(?:doi:\s*|https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/[^\s\"<>]+)", re.I)
_ARXIV = re.compile(r"\barXiv:\s*(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
_HEAD_CHARS = 3000


def _head(doc: ParsedDocument) -> str:
    text = ""
    for block in doc.blocks:
        text += block.text + "\n"
        if len(text) > _HEAD_CHARS:
            break
    return text[:_HEAD_CHARS]


def basic(doc: ParsedDocument) -> dict[str, str]:
    found = dict(doc.identifiers)
    head = _head(doc)
    if "doi" not in found and (m := _DOI.search(head)):
        found["doi"] = m.group(1).rstrip(".,;)")
    if "arxiv" not in found and (m := _ARXIV.search(head)):
        found["arxiv"] = m.group(1)
    return found


def none(doc: ParsedDocument) -> dict[str, str]:
    return dict(doc.identifiers)


def finders() -> dict[str, IdentityFinder]:
    found: dict[str, IdentityFinder] = {"file": none, "none": none, "basic": basic}
    for ep in entry_points(group="ontyra_ingest.identity"):
        found[ep.name] = ep.load()
    return found


def uri_for(identifiers: dict[str, str], fallback: str) -> str:
    if doi := identifiers.get("doi"):
        return f"doi:{doi.lower()}"
    if arxiv := identifiers.get("arxiv"):
        return f"arxiv:{arxiv}"
    if isbn := identifiers.get("isbn"):
        return f"isbn:{isbn}"
    return fallback
