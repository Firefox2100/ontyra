# Benchmark suite (planned)

Purpose: evaluate different methods (retrieval lanes, fusion, ranking weights, packing,
qualifier handling, model tiers, …) against each other and tune parameters, on fixed,
reproducible scenarios. Design is pending (owner will specify). Constraints agreed so far:

- Not part of the default `pytest` run; run explicitly: `uv run pytest tests/benchmark_test`.
- Scenario data and ground truth are fixtures on disk, never generated from the system
  under test. Runs produce immutable result files (config + inputs + outputs + scores) so
  results can be re-graded and compared later without re-running.
- Methods and parameters are selected by configuration (the same env-var `Settings`
  mechanism as production), so a benchmark run is "a Settings object + a scenario set".
- Metrics to record per run: quality (task-specific), latency, token cost, and, once model
  tiers exist, results per executor profile.
- See plans/13-recall-v1.md (evaluation) and plans/17-evaluation-hardening.md.
