"""Typed settings. The single place environment variables are read.

Variables use the `ONTYRA_` prefix and `__` for nesting, e.g. `ONTYRA_DB__URL`.
"""

from __future__ import annotations

import ipaddress
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ontyra.models import PolicyConfig
from ontyra.models.consolidation import ConsolidationSettings
from ontyra.models.episodes import EpisodeSettings
from ontyra.models.feedback import LearningSettings
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
    max_body_bytes: Annotated[int, Field(ge=0)] = (
        2_097_152  # request bodies above this get 413; 0 = off
    )


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

    `http`: a hosted rerank API (`{url}/rerank`; cannot be retrained). `worker`: our reranker
    worker (`ontyra worker`, same API plus admin routes; the only retrainable reranker).
    `langchain`: any LangChain `BaseDocumentCompressor` (not retrainable).

    Retraining (`worker` only; explicit, never automatic): `pool` says whose feedback trains
    (`own`: the caller's, `all`: everyone's, for one trust domain), `train_sources` whether agents'
    verdicts count (`human` only, or `all`, at their lower trust), `source_cap` the largest share
    of the weight one source may have, `anchor_ratio` how many clean benchmark pairs are mixed in
    per feedback pair, and `gate_tolerance` how much a new version may lose on the trusted
    benchmark requests and still be activated. `live` turns on the live head edit (worker only,
    and only with `pool="all"`, since the head is shared): the head is refitted from the
    feedback, at most every `live_every_seconds`, from `live_min_examples` usable pairs, and may
    move at most `live_max_distance` of its own norm.
    """

    provider: Literal["none", "langchain", "http", "worker"] = "none"
    class_path: str | None = None  # langchain
    kwargs: dict[str, Any] = {}  # langchain
    url: str | None = None  # http, worker
    model: str | None = None  # http model name; the worker's name in traces
    api_key: str | None = None  # http, worker
    timeout: float = 10.0  # http, worker: seconds; a slower answer counts as no answer
    train_timeout: float = 3600.0  # worker: seconds a retraining or an activation may take
    top_n: int = 30  # candidates sent to the reranker (legacy engine)
    min_examples: int = 50  # feedback pairs needed before a retrain is allowed
    feedback_ttl_days: int = 365  # feedback expires after this long unless given its own
    pool: Literal["own", "all"] = "own"
    train_sources: Literal["human", "all"] = "all"
    source_cap: float = 0.5
    anchor_ratio: float = 1.0
    gate_tolerance: float = 0.01
    live: bool = False  # a live head edit from feedback (worker; needs pool="all")
    live_min_examples: int = 20
    live_every_seconds: float = 300.0
    live_max_distance: float = 0.25


class WorkerSettings(BaseModel):
    """The reranker worker (`ONTYRA_WORKER__*`), a separate process: `ontyra worker`.

    It owns the GPU and serves a standard `/rerank` API (Jina/Cohere shape), so the daemon
    reaches it like any hosted reranker (`ONTYRA_RERANK__PROVIDER=http`, `ONTYRA_RERANK__URL`).
    Needs the `worker` extra (torch, transformers, safetensors).

    `model` is a Hugging Face id or a local directory of safetensors weights: a Qwen3-style
    reranker (a causal LM scoring "yes"/"no") is converted exactly to a one-logit head; a
    sequence-classification model with one label or a cross-encoder is used as it is.
    `device` and `dtype` default to `auto`. `serve` and `train` say what the operator wants;
    `GET /status` reports what the machine can actually do.
    """

    host: str = "127.0.0.1"
    port: int = 8788
    token: SecretStr | None = None  # when set, requests need `Authorization: Bearer <token>`
    model: str = "Qwen/Qwen3-Reranker-0.6B"
    model_dir: str = "worker"  # versions, manifests and the active pointer live here
    architecture: Literal["auto", "qwen3", "sequence"] = "auto"
    device: str = "auto"  # auto | cpu | cuda | cuda:N | mps | xpu
    dtype: Literal["auto", "fp32", "fp16", "bf16"] = "auto"
    attention: str = "sdpa"
    serve: Literal["auto", "on", "off"] = "auto"
    train: Literal["auto", "on", "off"] = "auto"
    live_edit: bool = True  # allow head-only updates from feedback while serving
    max_length: int = 1024  # tokens per query + document pair
    batch_size: int = 16  # pairs per forward pass (halves on out-of-memory)
    batch_wait_ms: int = 5  # how long to wait for more requests to share a batch
    queue_size: int = 64  # waiting requests; more are refused with 503
    load_retries: int = 2  # tries again when the device has no room for the weights
    load_retry_seconds: float = 3.0  # wait between them
    offload_after_seconds: float = 300.0  # idle: weights move to host memory (0 = never)
    free_after_seconds: float = 1800.0  # idle: weights are dropped altogether (0 = never)
    instruction: str = "Given a situation or request, find the stored memory that applies to it"
    prompt_prefix: str | None = None  # Qwen3 template; None uses the model's own
    prompt_suffix: str | None = None
    yes_token: str = "yes"
    no_token: str = "no"


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
    """Operator console (`ONTYRA_UI__*`) served at `/ui` by the daemon.

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


class BackupSettings(BaseModel):
    """Scheduled exports of the whole database (`ONTYRA_BACKUP__*`). Off until `dir` is set.

    The daemon writes `ontyra-YYYYmmdd-HHMMSS.surql` (schema and every record, readable by its
    owner only) into `dir` every `every_hours` and keeps the newest `keep`. Needs a SurrealDB
    server, not an embedded database. The files hold all memories: keep them as private as the
    database. Restore with `ontyra db import`.
    """

    dir: str | None = None
    every_hours: float = Field(default=24.0, ge=0.0, allow_inf_nan=False)  # 0: off
    keep: int = Field(default=14, ge=1)


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
    worker: WorkerSettings = WorkerSettings()
    learning: LearningSettings = LearningSettings()
    decision: DecisionSettings = DecisionSettings()
    consolidation: ConsolidationSettings = ConsolidationSettings()
    tasks: TaskSettings = TaskSettings()
    episodes: EpisodeSettings = EpisodeSettings()
    recall: RecallSettings = RecallSettings()
    ontology: OntologySettings = OntologySettings()
    mcp: McpSettings = McpSettings()
    ui: UiSettings = UiSettings()
    auth: AuthSettings = AuthSettings()
    backup: BackupSettings = BackupSettings()
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
