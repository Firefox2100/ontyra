"""Settings of the model-driven suites, all from `BENCH_*` environment variables.

The suites call a chat model through LangChain (`langchain-openai`, any OpenAI-compatible
server). Nothing here runs unless `BENCH_LIVE=1`; the data and scorers are tested offline.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class EvalSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BENCH_", extra="ignore", env_file=None)

    live: bool = False
    label: str | None = None  # prefix of the result files; default: a timestamp
    only: str | None = None  # substring filter on case ids
    repeat: int = 1  # samples per case (only useful with a temperature above 0)
    results_dir: str | None = None

    arms: str = "none,injected,tools,both"  # which scenario arms to run (comma separated)

    llm_url: str = "http://127.0.0.1:8081/v1"
    llm_model: str = "qwen3.6-35b-a3b-uncensored-1m"
    llm_api_key: str = "none"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 6000  # headroom for a model that reasons (BENCH_LLM_THINKING=1)
    llm_timeout: float = 300.0
    llm_thinking: bool = False  # Qwen3 style reasoning: off by default, it is slow and noisy
