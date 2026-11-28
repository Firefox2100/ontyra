"""Settings: environment (`ONTYRA_INGEST_*`, plus `ONTYRA_URL`/`ONTYRA_TOKEN` shared with the
plugins), overridden by command-line options."""

from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def default_state_dir() -> Path:
    return Path.home() / ".local" / "state" / "ontyra-ingest"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ONTYRA_INGEST_", extra="ignore", populate_by_name=True
    )

    # The daemon.
    url: str = Field(
        default="http://127.0.0.1:8787",
        validation_alias=AliasChoices("ONTYRA_URL", "ONTYRA_INGEST_URL"),
    )
    token: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("ONTYRA_TOKEN", "ONTYRA_INGEST_TOKEN")
    )
    actor: str = "ontyra-ingest"
    space: str = "own"

    # The extraction model: any OpenAI-compatible chat endpoint.
    llm_url: str = "http://127.0.0.1:8081/v1"
    llm_model: str = "qwen3.6-35b-a3b-uncensored-1m"
    llm_key: SecretStr | None = None
    llm_timeout: float = 600.0
    llm_extra: dict[str, object] = Field(default_factory=dict)  # merged into every request body
    llm_retries: int = 2

    # Parsing.
    parser: str = "auto"
    mineru_url: str | None = None
    mineru_tier: str | None = None  # flash | basic | standard | advanced; unset: the service picks
    mineru_timeout: float = 900.0
    identity: str = "none"

    # Chunking.
    chunk_chars: int = 3500
    overlap_chars: int = 300

    # What to extract.
    profiles: list[str] = Field(default_factory=lambda: ["claims", "datasets"])
    # Sections that hold no knowledge: a chunk whose heading chain matches any of these (a
    # case-insensitive regex on a heading) is not sent to the model.
    skip_sections: list[str] = Field(
        default_factory=lambda: [
            r"^(table of )?contents$",
            r"^list of (tables|figures)$",
            r"^revision history$",
            r"^(disclaimer|copyright)\b",
            r"^(related )?(documentation|resources)\b",
            r"^(references|bibliography|acknowledge?ments?)$",
        ]
    )
    topic: str | None = None  # the subject of every document of the run; else the model names it
    aliases: list[str] = Field(default_factory=list)
    # Scope of the detailed entries. Unset, the daemon decides (a sourced fact is reference
    # material: looked up, never delivered); `contextual` makes them recallable by text.
    scope: str | None = None
    rework_rounds: int = 1
    min_usefulness: int = 3  # datasets profile: lowest 0-5 score that is kept
    concepts: str = "existing"  # existing | create
    batch_size: int = 10
    concurrency: int = 1

    state_dir: Path = Field(default_factory=default_state_dir)
