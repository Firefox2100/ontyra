"""Build adapters from settings. Provider classes are imported lazily from `class_path`, so
LangChain integrations (`langchain_openai`, `langchain_ollama`, …) stay optional."""

from __future__ import annotations

import importlib
from typing import Any

from ontyra.adapters.embeddings import HashEmbedder, LangChainEmbedder
from ontyra.adapters.rerank import LangChainReranker
from ontyra.config import EmbeddingSettings, RerankSettings
from ontyra.ports.adapters import Embedder, Reranker


def _instantiate(class_path: str | None, kwargs: dict[str, Any]) -> Any:
    if not class_path or "." not in class_path:
        raise ValueError(
            "class_path must be a dotted path such as 'langchain_openai.OpenAIEmbeddings'"
        )
    module, _, name = class_path.rpartition(".")
    try:
        cls = getattr(importlib.import_module(module), name)
    except ImportError as exc:
        raise ValueError(f"cannot import {class_path}: {exc} (install the integration)") from exc
    return cls(**kwargs)


def build_embedder(settings: EmbeddingSettings) -> Embedder | None:
    if settings.provider == "none":
        return None
    if settings.provider == "hash":
        return HashEmbedder(settings.dimension)
    emb = _instantiate(settings.class_path, settings.kwargs)
    name = settings.name or str(settings.kwargs.get("model") or settings.class_path)
    return LangChainEmbedder(emb, name=name, dimension=settings.dimension)


def build_reranker(settings: RerankSettings) -> Reranker | None:
    if settings.provider == "none":
        return None
    return LangChainReranker(_instantiate(settings.class_path, settings.kwargs))
