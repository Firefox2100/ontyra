"""Typed settings. The single place environment variables are read.

Variables use the `ONTYRA_` prefix and `__` for nesting, e.g. `ONTYRA_DB__URL`.
"""

from __future__ import annotations

from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DbSettings(BaseModel):
    """SurrealDB connection (`ONTYRA_DB__*`)."""

    url: str = "ws://localhost:8000/rpc"
    username: str = "root"
    password: SecretStr = SecretStr("root")
    namespace: str = "ontyra"
    database: str = "main"
    timeout: float = 30.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ONTYRA_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db: DbSettings = DbSettings()
    log_level: str = "INFO"


def load_settings(env_file: str | None = ".env") -> Settings:
    """Build settings from the environment (and an optional dotenv file)."""
    return Settings(_env_file=env_file)  # pyright: ignore[reportCallIssue]
