"""External model adapters implementing `ontyra.ports.adapters`. The only place that imports
LangChain (lazily, so it stays an optional extra)."""

from ontyra.adapters.embeddings import HashEmbedder, LangChainEmbedder
from ontyra.adapters.factory import build_embedder, build_reranker
from ontyra.adapters.rerank import LangChainReranker

__all__ = [
    "HashEmbedder",
    "LangChainEmbedder",
    "LangChainReranker",
    "build_embedder",
    "build_reranker",
]
