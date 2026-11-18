"""Typed settings. The single place environment variables are read.

Variables use the `ONTYRA_` prefix and `__` for nesting, e.g. `ONTYRA_DB__URL`.
"""

from __future__ import annotations

import ipaddress
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ontyra.models import PolicyConfig
from ontyra.models.consolidation import ConsolidationSettings
from ontyra.models.episodes import EpisodeSettings
from ontyra.models.recall import RecallSettings
from ontyra.models.tasks import TaskSettings


class DbSettings(BaseModel):
    """SurrealDB connection (`ONTYRA_DB__*`)."""

    url: str = "ws://localhost:8000/rpc"
    username: str = "root"
    password: SecretStr = SecretStr("root")
    namespace: str = "ontyra"
    database: str = "main"
    timeout: float = 30.0


class ServerSettings(BaseModel):
    """HTTP daemon (`ONTYRA_SERVER__*`). Public binding needs the documented auth/proxy setup."""

    host: str = "127.0.0.1"
    port: int = 8080
    deployment: Literal["local", "public"] = "local"
    allow_open_non_loopback: bool = False


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
    # Instruction-tuned embedders (Qwen3-Embedding, e5, bge…) expect a prefix on queries and/or
    # documents. Applied by the `langchain` provider; the stored text is not changed.
    query_prefix: str = ""
    document_prefix: str = ""


class RerankSettings(BaseModel):
    """Optional reranker (`ONTYRA_RERANK__*`).

    `http`: a hosted rerank API (`{url}/rerank`; cannot be retrained). `cross_encoder`: an
    in-process sentence-transformers cross-encoder that can be fine-tuned from feedback (needs
    the `rerank-local` extra); `model` is its base model, `model_dir` where versions are kept.
    `langchain`: any LangChain `BaseDocumentCompressor` (not retrainable).
    """

    provider: Literal["none", "langchain", "http", "cross_encoder"] = "none"
    class_path: str | None = None  # langchain
    kwargs: dict[str, Any] = {}  # langchain
    url: str | None = None  # http
    model: str | None = None  # http model name, or the cross-encoder's base model
    api_key: str | None = None  # http
    model_dir: str | None = None  # cross_encoder: where trained versions are saved
    device: str | None = None  # cross_encoder: cpu (default), cuda, ... (set it explicitly)
    top_n: int = 30  # candidates sent to the reranker (legacy engine)
    min_examples: int = 50  # feedback pairs needed before a retrain is allowed
    feedback_ttl_days: int = 365  # feedback expires after this long unless given its own


class DecisionSettings(BaseModel):
    """Optional decision model (`ONTYRA_DECISION__*`); needs `ONTYRA_RECALL__ENGINE=fusion`.

    A decision model (System One API) answers in one pass, with probabilities, which of the top
    candidates matter for the situation; recall blends that into its ranking. When `provider` is
    `none` recall is the deterministic fusion ranking (plus the reranker, if configured), and
    a decision model that fails or times out is skipped for that request.

    `systemone`: `POST {url}/systemone` (TypeSafe's Jev API, LocalAI, llama.cpp's server…).
    `langchain`: any `langchain_core.decisions.BaseDecisionModel` class (`class_path`, `kwargs`).
    `format`: `choice` (one question over the candidates plus "none"; best in the benchmark) or
    `noul` (one yes/no question per candidate). A server limits how large a request may be (a
    `choice` over many long memories can be refused): lower `memory_chars` or
    `ONTYRA_RECALL__DECISION_CANDIDATES` to fit, or raise the limit on the server.
    """

    provider: Literal["none", "systemone", "langchain"] = "none"
    url: str | None = None  # systemone, e.g. http://localhost:8083/v1
    model: str | None = None  # systemone
    api_key: str | None = None  # systemone
    class_path: str | None = None  # langchain
    kwargs: dict[str, Any] = {}  # langchain
    name: str | None = None  # reported in traces; defaults to the model or class path
    format: Literal["choice", "noul"] = "choice"
    memory_chars: int = 100  # each candidate is cut to this many characters in the request
    compare_chars: int = 300  # each memory of a duplicate check is cut to this many characters
    timeout: float = 30.0  # seconds; a slower answer counts as no answer


class OntologySettings(BaseModel):
    """Ontology change proposals (`ONTYRA_ONTOLOGY__*`).

    A human's change applies at once; an agent's waits for review unless its op is listed in
    `auto_apply` (ops: define, relate, add_alias, deprecate, merge), e.g. `["add_alias"]`.
    """

    auto_apply: list[str] = []


class McpSettings(BaseModel):
    """MCP server (`ONTYRA_MCP__*`), served by the daemon at `path` (streamable HTTP).

    Needs the `mcp` extra. `lite` is a handful of tools for weak tool-callers; `full` adds
    search, browsing and review. Authentication and caller identity are shared with REST.
    """

    enabled: bool = False
    profile: Literal["lite", "full"] = "lite"
    path: str = "/mcp"


class UiSettings(BaseModel):
    """Operator console (`ONTYRA_UI__*`) served at `/ui` by the daemon (plans/19).

    In open mode it acts as the configured human `actor`; authenticated modes use a human
    credential. State-changing requests require an allowed browser origin.
    """

    enabled: bool = False
    actor: str = "owner"


class AuthSettings(BaseModel):
    """Authentication. Off by default (loopback use).

    Single-user mode (`multi_user` false): everything belongs to the global owner. Set `token`
    to require `Authorization: Bearer <token>` on every request; leave it unset for none.
    Multi-user mode: every request needs a credential issued to a user (see `/v1/admin`);
    `admin_token` is the bootstrap credential of the built-in `admin` account.
    """

    multi_user: bool = False
    token: SecretStr | None = None
    admin_token: SecretStr | None = None
    single_user_kind: Literal["human", "agent"] = "human"
    ui_session_hours: int = 12
    ui_cookie_secure: bool | None = None
    ui_allowed_origins: list[str] = []

    @field_validator("token", "admin_token", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        return None if value == "" else value  # an empty variable in a compose file

    @field_validator("ui_allowed_origins")
    @classmethod
    def _origins(cls, values: list[str]) -> list[str]:
        out: list[str] = []
        for value in values:
            parsed = urlparse(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username is not None
                or parsed.path not in {"", "/"}
                or parsed.params
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(f"auth.ui_allowed_origins contains an invalid origin: {value!r}")
            out.append(f"{parsed.scheme.lower()}://{parsed.netloc.lower()}")
        return out

    @model_validator(mode="after")
    def _check(self) -> AuthSettings:
        if self.multi_user and self.admin_token is None:
            raise ValueError("multi-user mode needs ONTYRA_AUTH__ADMIN_TOKEN (the bootstrap admin)")
        for name, secret in (("token", self.token), ("admin_token", self.admin_token)):
            if secret is not None and len(secret.get_secret_value()) < 32:
                raise ValueError(f"auth.{name} must be at least 32 characters")
        return self


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
    decision: DecisionSettings = DecisionSettings()
    consolidation: ConsolidationSettings = ConsolidationSettings()
    tasks: TaskSettings = TaskSettings()
    episodes: EpisodeSettings = EpisodeSettings()
    recall: RecallSettings = RecallSettings()
    ontology: OntologySettings = OntologySettings()
    mcp: McpSettings = McpSettings()
    ui: UiSettings = UiSettings()
    auth: AuthSettings = AuthSettings()
    log_level: str = "INFO"

    @model_validator(mode="after")
    def _deployment_security(self) -> Settings:
        try:
            loopback = ipaddress.ip_address(self.server.host).is_loopback
        except ValueError:
            loopback = self.server.host.casefold() == "localhost"
        auth_required = self.auth.multi_user or self.auth.token is not None
        if (
            self.server.deployment == "local"
            and not loopback
            and not auth_required
            and not self.server.allow_open_non_loopback
        ):
            raise ValueError(
                "an open non-loopback listener needs ONTYRA_SERVER__ALLOW_OPEN_NON_LOOPBACK=true"
            )
        if self.server.deployment == "public":
            if not self.auth.multi_user:
                raise ValueError("public deployment requires multi-user authentication")
            if self.ui.enabled and not self.auth.ui_allowed_origins:
                raise ValueError("a public UI requires ONTYRA_AUTH__UI_ALLOWED_ORIGINS")
            if self.ui.enabled and self.auth.ui_cookie_secure is False:
                raise ValueError("a public UI cannot disable secure session cookies")
        return self

    @property
    def ui_cookie_secure(self) -> bool:
        if self.auth.ui_cookie_secure is not None:
            return self.auth.ui_cookie_secure
        return self.server.deployment == "public"

    def for_bind(self, host: str, port: int) -> Settings:
        """Revalidate command-line bind overrides against the deployment security policy."""
        values = self.model_dump(mode="python")
        values["server"] = {**values["server"], "host": host, "port": port}
        return Settings.model_validate(values)


def load_settings(env_file: str | None = ".env") -> Settings:
    """Build settings from the environment (and an optional dotenv file)."""
    return Settings(_env_file=env_file)  # pyright: ignore[reportCallIssue]
