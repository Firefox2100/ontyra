"""Settings of the decision-model benchmark, from `BENCH_*` / `BENCH_DECISION_*` variables.

Nothing live runs unless `BENCH_LIVE=1`. The decision model defaults to the owner's decisions
LocalAI (`kev-9b` on :8083). The embedder that builds the candidates of the judge task and the
recall requests of the gate task is the benchmark suite's profile (`BENCH_PROFILES` or
`BENCH_EMBED_*`, see `tests/benchmark_test/README.md`); the hash embedder when none is set.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DecisionBenchSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BENCH_DECISION_", extra="ignore", env_file=None)

    live: bool = Field(default=False, validation_alias="BENCH_LIVE")
    label: str | None = Field(default=None, validation_alias="BENCH_LABEL")
    only: str | None = Field(default=None, validation_alias="BENCH_ONLY")  # substring of ids
    results_dir: str | None = Field(default=None, validation_alias="BENCH_RESULTS_DIR")

    url: str = "http://127.0.0.1:8083/v1"
    model: str = "kev-9b"
    api_key: str | None = None
    timeout: float = 60.0
    concurrency: int = 4  # requests in flight at once (the server queues the rest)
    judge_cases: int = 100  # recall cases sampled for the judge task (every n-th, 0 = all)
    judge_candidates: int = 8  # candidates sent per request, as `decision_candidates` in recall
    gate_threshold: float = 0.5  # p(refers back) at which the gate widens the query
