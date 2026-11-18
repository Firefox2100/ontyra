"""Build adapters from settings. Provider classes are imported lazily from `class_path`, so
LangChain integrations (`langchain_openai`, `langchain_ollama`, …) stay optional."""

from __future__ import annotations

import importlib
from typing import Any

from ontyra.adapters.decider import LangChainDecider, SystemOneDecider
from ontyra.adapters.embeddings import HashEmbedder, LangChainEmbedder
from ontyra.adapters.rerank import CrossEncoderReranker, HttpReranker, LangChainReranker
from ontyra.config import DecisionSettings, EmbeddingSettings, RerankSettings
from ontyra.ports.adapters import Decider, Embedder, Reranker


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
    return LangChainEmbedder(
        emb,
        name=name,
        dimension=settings.dimension,
        query_prefix=settings.query_prefix,
        document_prefix=settings.document_prefix,
    )


def build_reranker(settings: RerankSettings) -> Reranker | None:
    if settings.provider == "none":
        return None
    if settings.provider == "http":
        if not settings.url or not settings.model:
            raise ValueError("rerank provider http needs ONTYRA_RERANK__URL and __MODEL")
        return HttpReranker(settings.url, settings.model, api_key=settings.api_key)
    if settings.provider == "cross_encoder":
        return CrossEncoderReranker(
            settings.model or "cross-encoder/ms-marco-MiniLM-L6-v2",
            model_dir=settings.model_dir,
            device=settings.device,
        )
    return LangChainReranker(_instantiate(settings.class_path, settings.kwargs))


def build_decider(settings: DecisionSettings) -> Decider | None:
    if settings.provider == "none":
        return None
    if settings.provider == "systemone":
        if not settings.url or not settings.model:
            raise ValueError("decision provider systemone needs ONTYRA_DECISION__URL and __MODEL")
        return SystemOneDecider(
            settings.url,
            settings.model,
            api_key=settings.api_key,
            format=settings.format,
            memory_chars=settings.memory_chars,
            compare_chars=settings.compare_chars,
            timeout=settings.timeout,
        )
    model = _instantiate(settings.class_path, settings.kwargs)
    name = settings.name or str(settings.kwargs.get("model") or settings.class_path)
    return LangChainDecider(
        model,
        name=name,
        format=settings.format,
        memory_chars=settings.memory_chars,
        compare_chars=settings.compare_chars,
    )
