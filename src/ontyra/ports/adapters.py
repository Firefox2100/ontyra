"""Ports for external model adapters. Implementations live in `ontyra.adapters`."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class Embedder(Protocol):
    """Turns text into vectors. `name` identifies model + settings (stored with each vector)."""

    @property
    def name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class Reranker(Protocol):
    """Scores candidate texts against a query (higher is more relevant), in input order."""

    async def rerank(self, query: str, texts: Sequence[str]) -> list[float]: ...
