"""External model adapters implementing `ontyra.ports.adapters`. The only place that imports
LangChain (lazily, so it stays an optional extra)."""

from ontyra.adapters.decider import LangChainDecider, SystemOneDecider
from ontyra.adapters.embeddings import HashEmbedder, LangChainEmbedder
from ontyra.adapters.factory import build_decider, build_embedder, build_reranker
from ontyra.adapters.rerank import HttpReranker, LangChainReranker, WorkerReranker

__all__ = [
    "HashEmbedder",
    "HttpReranker",
    "LangChainDecider",
    "LangChainEmbedder",
    "LangChainReranker",
    "SystemOneDecider",
    "WorkerReranker",
    "build_decider",
    "build_embedder",
    "build_reranker",
]
