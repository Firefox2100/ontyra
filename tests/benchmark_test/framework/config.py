"""What a benchmark run is configured with.

A run is a list of **profiles**: an embedder (and optionally a reranker) plus recall-setting
overrides. The embedder is part of what is evaluated, so none is fixed here; the default is the
built-in hash embedder, which needs nothing. Profiles come from `BENCH_PROFILES` (a JSON file,
see `profiles.example.json`) or, when that is unset, from the single-profile `BENCH_EMBED_*`
variables.
"""

from __future__ import annotations

import itertools
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ontyra.config import DecisionSettings, EmbeddingSettings, RerankSettings
from ontyra.models.recall import RecallSettings


class Profile(BaseModel):
    """One embedder/reranker/recall-settings combination to evaluate."""

    model_config = ConfigDict(extra="forbid")

    name: str
    # embedder: `hash` (no `model`) or any OpenAI-compatible endpoint (LocalAI, vLLM, Ollama…)
    url: str | None = None
    model: str | None = None
    dim: int = 0
    api_key: str = "none"
    query_prefix: str = ""
    document_prefix: str = ""
    class_path: str = "langchain_openai.OpenAIEmbeddings"
    kwargs: dict[str, Any] = Field(default_factory=dict)
    # reranker: any `ONTYRA_RERANK__*` setting (provider http | cross_encoder | langchain, ...)
    reranker: dict[str, Any] = Field(default_factory=dict)
    # decision model: any `ONTYRA_DECISION__*` setting (provider systemone | langchain, url, ...)
    decision: dict[str, Any] = Field(default_factory=dict)
    rerank_class_path: str | None = None  # shortcut for a LangChain compressor
    rerank_kwargs: dict[str, Any] = Field(default_factory=dict)
    # overrides of `ONTYRA_RECALL__*` for this profile (e.g. {"vector_floor": 0.5})
    recall: dict[str, Any] = Field(default_factory=dict)
    # a sweep expands this profile into one per combination: {"vector_floor": [0.3, 0.5]}
    sweep: dict[str, list[Any]] = Field(default_factory=dict)

    def embedding(self) -> EmbeddingSettings:
        if not self.url and not self.model:
            return EmbeddingSettings(provider="hash", dimension=self.dim or 256)
        if not self.model or not self.dim:
            raise ValueError("an embedder profile needs `model` and `dim` (BENCH_EMBED_MODEL/DIM)")
        kwargs: dict[str, Any] = {"model": self.model, **self.kwargs}
        if self.url:
            kwargs.setdefault("base_url", self.url)
            kwargs.setdefault("api_key", self.api_key)
            kwargs.setdefault("check_embedding_ctx_length", False)  # servers take plain strings
        return EmbeddingSettings(
            provider="langchain",
            class_path=self.class_path,
            kwargs=kwargs,
            dimension=self.dim,
            name=self.model,
            query_prefix=self.query_prefix,
            document_prefix=self.document_prefix,
        )

    def rerank(self) -> RerankSettings:
        if self.reranker:
            return RerankSettings.model_validate(self.reranker)
        if not self.rerank_class_path:
            return RerankSettings()
        return RerankSettings(
            provider="langchain", class_path=self.rerank_class_path, kwargs=self.rerank_kwargs
        )

    def decision_settings(self) -> DecisionSettings:
        return DecisionSettings.model_validate(self.decision)

    def recall_settings(self) -> RecallSettings:
        """The environment's recall settings (`ONTYRA_RECALL__*`) with this profile's overrides."""
        from ontyra.config import Settings

        base = Settings(_env_file=None).recall  # type: ignore[call-arg]
        return RecallSettings.model_validate({**base.model_dump(), **self.recall})

    @property
    def baseline(self) -> bool:
        """The hash embedder with default settings: the regression guard asserts only here."""
        return (
            self.embedding().provider == "hash" and not self.rerank_class_path and not self.recall
        )

    def expand(self) -> list[Profile]:
        if not self.sweep:
            return [self]
        keys = sorted(self.sweep)
        out: list[Profile] = []
        for values in itertools.product(*(self.sweep[k] for k in keys)):
            tag = ".".join(f"{k}-{v}" for k, v in zip(keys, values, strict=True))
            out.append(
                self.model_copy(
                    update={
                        "name": f"{self.name}.{tag}",
                        "recall": {**self.recall, **dict(zip(keys, values, strict=True))},
                        "sweep": {},
                    }
                )
            )
        return out


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text)


class BenchSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BENCH_", extra="ignore", env_file=None)

    live: bool = False  # model-driven suites run only when set
    label: str | None = None  # prefix of the result files; default: a timestamp
    repeat: int = 1
    only: str | None = None  # substring filter on case ids
    assert_results: bool | None = Field(  # default: assert only for the baseline profile
        default=None, validation_alias="BENCH_ASSERT"
    )
    results_dir: str | None = None
    profiles: str | None = None  # path of a JSON list of profiles

    # the single-profile shortcut
    embed_url: str | None = None
    embed_model: str | None = None
    embed_dim: int = 0
    embed_api_key: str = "none"
    embed_query_prefix: str = ""
    embed_document_prefix: str = ""
    embed_class_path: str = "langchain_openai.OpenAIEmbeddings"
    embed_kwargs: dict[str, Any] = Field(default_factory=dict)
    rerank_class_path: str | None = None
    rerank_kwargs: dict[str, Any] = Field(default_factory=dict)

    def profile_list(self) -> list[Profile]:
        if self.profiles:
            raw = json.loads(Path(self.profiles).read_text(encoding="utf-8"))
            return [p for item in raw for p in Profile.model_validate(item).expand()]
        return [
            Profile(
                name=slug(self.embed_model or "hash"),
                url=self.embed_url,
                model=self.embed_model,
                dim=self.embed_dim,
                api_key=self.embed_api_key,
                query_prefix=self.embed_query_prefix,
                document_prefix=self.embed_document_prefix,
                class_path=self.embed_class_path,
                kwargs=self.embed_kwargs,
                rerank_class_path=self.rerank_class_path,
                rerank_kwargs=self.rerank_kwargs,
            )
        ]

    def run_label(self, profile: Profile, stamp: str) -> str:
        return slug(f"{self.label or stamp}-{profile.name}")
