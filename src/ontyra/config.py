"""Typed settings. The single place environment variables are read.

Variables use the `ONTYRA_` prefix and `__` for nesting, e.g. `ONTYRA_DB__URL`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from ontyra.models import PolicyConfig
from ontyra.models.recall import RecallSettings


class DbSettings(BaseModel):
    """SurrealDB connection (`ONTYRA_DB__*`)."""

    url: str = "ws://localhost:8000/rpc"
    username: str = "root"
    password: SecretStr = SecretStr("root")
    namespace: str = "ontyra"
    database: str = "main"
    timeout: float = 30.0


class ServerSettings(BaseModel):
    """HTTP daemon (`ONTYRA_SERVER__*`). No authentication yet: keep it on localhost."""

    host: str = "127.0.0.1"
    port: int = 8080


class EmbeddingSettings(BaseModel):
    """Embedding adapter (`ONTYRA_EMBEDDING__*`).

    `hash` is a built-in, dependency-free lexical baseline. `langchain` instantiates any
    LangChain `Embeddings` class: e.g. CLASS_PATH=langchain_openai.OpenAIEmbeddings and
    KWARGS='{"model": "text-embedding-3-small", "base_url": "…"}' (secrets via the
    provider's own environment variables). `dimension` must match the model; the vector
    index is created with it, so changing it means re-embedding (pre-release: wipe).
    """

    provider: Literal["none", "hash", "langchain"] = "hash"
    dimension: int = 256
    name: str | None = None  # stored with each vector; defaults to kwargs["model"]
    class_path: str | None = None
    kwargs: dict[str, Any] = {}


class RerankSettings(BaseModel):
    """Optional reranker (`ONTYRA_RERANK__*`): a LangChain `BaseDocumentCompressor`."""

    provider: Literal["none", "langchain"] = "none"
    class_path: str | None = None
    kwargs: dict[str, Any] = {}
    top_n: int = 30  # candidates sent to the reranker


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ONTYRA_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db: DbSettings = DbSettings()
    server: ServerSettings = ServerSettings()
    # Commit policy; the safe default sends every proposal to review. For solo dev use
    # e.g. ONTYRA_POLICY__DEFAULT=auto_commit, or rules via ONTYRA_POLICY__RULES='[…]'.
    policy: PolicyConfig = PolicyConfig()
    # Trust per actor kind, overriding the built-in defaults (ONTYRA_ACTOR_TRUST__AGENT=0.9).
    actor_trust: dict[str, float] = {}
    embedding: EmbeddingSettings = EmbeddingSettings()
    rerank: RerankSettings = RerankSettings()
    recall: RecallSettings = RecallSettings()
    log_level: str = "INFO"


def load_settings(env_file: str | None = ".env") -> Settings:
    """Build settings from the environment (and an optional dotenv file)."""
    return Settings(_env_file=env_file)  # pyright: ignore[reportCallIssue]
