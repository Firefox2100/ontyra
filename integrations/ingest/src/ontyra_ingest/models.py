"""Data that moves through the pipeline."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Block(BaseModel):
    """One piece of a parsed document. `level` > 0 is a heading (1 = top)."""

    text: str
    level: int = 0
    page: int | None = None


class ParsedDocument(BaseModel):
    blocks: list[Block]
    title: str | None = None
    # What the parser itself knows about the document's identity, e.g. {"doi": "10.1/x"}.
    identifiers: dict[str, str] = Field(default_factory=dict)


class Chunk(BaseModel):
    index: int
    text: str
    section: list[str] = Field(default_factory=list)  # heading chain, outermost first
    page: int | None = None

    @property
    def locator(self) -> str:
        parts: list[str] = []
        if self.page is not None:
            parts.append(f"p. {self.page}")
        if self.section:
            parts.append(" > ".join(self.section))
        return ", ".join(parts)[:200]


class Candidate(BaseModel):
    """One statement the model proposes, before it is checked against the text."""

    kind: str = "fact"
    text: str
    subject: str
    predicate: str
    object: str
    quote: str
    parents: list[str] = Field(default_factory=list)  # concept labels the subject is a kind of
    extra: dict[str, str] = Field(default_factory=dict)


class Grounded(BaseModel):
    candidate: Candidate
    quote: str  # the words as they occur in the chunk
    chunk: int
    locator: str


class Rejected(BaseModel):
    candidate: Candidate
    chunk: int
    reason: str
