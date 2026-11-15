"""Reranker implementations."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


class LangChainReranker:
    """Wraps a LangChain `BaseDocumentCompressor` that scores documents (cross-encoders,
    hosted rerank APIs). The compressor returns documents with a `relevance_score` in their
    metadata; documents it drops get score 0."""

    def __init__(self, compressor: Any) -> None:
        self._compressor = compressor

    async def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        from langchain_core.documents import Document

        docs = [Document(page_content=t, metadata={"i": i}) for i, t in enumerate(texts)]
        kept = await self._compressor.acompress_documents(docs, query)
        scores = [0.0] * len(texts)
        for doc in kept:
            scores[int(doc.metadata["i"])] = float(doc.metadata.get("relevance_score", 1.0))
        return scores
