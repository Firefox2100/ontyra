"""External sources a memory cites: a pointer, never a copy.

A source is anything that can be named: a file, a web page, a paper, a recording. The daemon does
not read it, store it or require that it exists; it keeps the reference with the memory (write-once,
like the memory's text) and later, what clients report about it (`SourceCheck`).
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from ontyra.models.base import ActorRef, Indexed, Model, Record, RecordId
from ontyra.models.registry import registry

Medium = Literal["file", "web", "pdf", "code", "media", "data", "other"]
CheckState = Literal["ok", "changed", "missing"]


class SourceRef(Model):
    """Where a statement comes from. Only `uri` is required; the rest makes a later check, or a
    reader without access to the source, much more useful. Give a short `quote` copied literally
    from the source: it stays with the memory even if the source is edited or deleted."""

    uri: str = Field(
        min_length=3,
        max_length=2000,
        description="file:///path, https://…, doi:10.…, arxiv:…; canonicalised by the daemon.",
    )
    medium: Medium = "other"
    locator: str | None = Field(
        default=None, max_length=200, description="Where in it: page, section, lines, timestamp."
    )
    quote: str | None = Field(
        default=None, max_length=600, description="Words from the source, copied literally."
    )
    title: str | None = Field(default=None, max_length=300)
    content_hash: str | None = Field(
        default=None,
        max_length=200,
        description="Hash of the source as read (any scheme, e.g. sha256:…): lets a later check "
        "say whether it changed.",
    )
    retrieved_at: datetime | None = Field(default=None, description="When it was read.")


def source_check_id(uri: str) -> RecordId:
    """The check record of a canonical URI has an id derived from it, so it can be read in batch."""
    return RecordId("source_check", hashlib.sha1(uri.encode()).hexdigest()[:24])


@registry.table("source_check")
class SourceCheck(Record):
    """What a client last reported about a source (the daemon never looks). Mutable status, kept
    apart from the memories that cite the source; one row per canonical URI."""

    uri: Annotated[str, Indexed(unique=True)]
    state: CheckState
    checked_at: datetime
    checked_by: ActorRef
    content_hash: str | None = None  # as observed by the check
    note: str | None = None


class SourceCheckRequest(Model):
    """Report what you found when you went back to a source: `ok` (as cited), `changed` (the
    cited statement may no longer hold) or `missing`. `changed` and `missing` open a review task
    for each memory that cites it."""

    uri: str = Field(min_length=3, max_length=2000)
    state: CheckState
    content_hash: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=1000)


class SourceCheckResult(Model):
    uri: str  # canonical
    state: CheckState
    memories: int  # live memories that cite it
    tasks: int  # review tasks opened (changed or missing)
