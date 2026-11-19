# Decision-model benchmark

Measures the optional **decision model** (kev-9b through the System One API) on every question
Ontyra puts to it, and on questions it may be asked later. The daemon never depends on it: the
model is advice, a failed call falls back to the deterministic result, and nothing it says is
fed back into training. This suite says what that advice is worth.

```
BENCH_LIVE=1 BENCH_PROFILES=profiles.json BENCH_LABEL=trial uv run pytest tests/decision_test
uv run pytest tests/decision_test            # offline only: data, client, scorers (a scripted model)
```

Environment: `BENCH_LIVE=1`; the model `BENCH_DECISION_URL` (default `http://127.0.0.1:8083/v1`),
`BENCH_DECISION_MODEL` (`kev-9b`), `BENCH_DECISION_CONCURRENCY` (4), `BENCH_DECISION_JUDGE_CASES`
(100 requests sampled, 0 = all), `BENCH_DECISION_JUDGE_CANDIDATES` (8), `BENCH_DECISION_GATE_THRESHOLD`
(0.5). The embedder (it builds the judge task's candidates and the gate task's recall) is the
first profile of the benchmark suite (`BENCH_PROFILES` / `BENCH_EMBED_*`, see
`tests/benchmark_test/README.md`): use the production embedder and the fusion engine for
meaningful numbers (the hash embedder is only a smoke test). Results go to
`results/<task>-<label>.json` (git-ignored, never overwritten): per-variant summaries and every
row (the probabilities, the item, what went wrong).

## Tasks

| task | file | question | where Ontyra asks it |
|---|---|---|---|
| `same` | `test_same.py` | do two memories state the same thing? | duplicate scan (`ConsolidationService`) |
| `judge` | `test_judge.py` | which of the retrieved candidates matter for this situation? | fusion recall (`FusionRecallService._decide`) |
| `gate` | `test_gate.py` | does this message need the earlier conversation, or is it chit-chat? | not built: a candidate for hooks/daemon |

* **same** (152 pairs, `src/ontyra/calibration/data/pairs.json`, labelled `dup` / `update` / `distinct`; paraphrases,
  light edits, and close pairs of the invented world). Variants: the production `noul` question
  (one order, and `-min` over both orders as the scan does), and a three-way `choice3`
  (same / update / distinct). Reports AUC against the rest, against updates and against distinct
  pairs, precision and recall at the scan's `confirm` threshold, how many updates or distinct
  pairs it would merge, the best possible cut-off and seconds per pair.
* **judge**: the candidates of real recall requests (the top applicable memories by fused score,
  sampled from the recall cases, labelled by the cases), asked with the production adapter. Reports
  top-1 and AUC of the pipeline's own order, the model alone and the 50/50 blend recall uses; and
  the AUC of "none of these" on requests that must stay silent. Variants: `choice`,
  `choice-shuffled` (candidate order permuted: position bias), `choice-200` (longer memory text),
  `noul`.
* **gate**: `data/gate.json` (48 hand-written messages with history: `refers_back`, `standalone`
  after a topic switch or at the start, `filler`) plus every turn of the benchmark dialogues
  (78). Classification variants (`choice3`, `choice2`, `noul`, with one or two earlier turns)
  against the `is_thin` regex of the `adaptive` strategy and "always standalone"; then
  **end to end**: the gate chooses the recall query of the dialogue runner (`refers_back` widens
  it with the previous user message, or searches both: `gate-users2@t`, `gate-union@t` for
  thresholds t) and is compared with `last`, `users2`, `adaptive`, `union` and `oracle` on
  the same turns.

Adding a task: a module in `framework/` (data model, the variants as callables, a pure scorer),
offline tests with a scripted model in `test_framework.py`, a live test that writes a result file.

## Layout

`framework/`: `client` (a thin System One client that times every request; production adapters
are measured as they are), `metrics` (AUC, thresholds, latency), `same`, `judge`, `gate`,
`results`, `config`. `data/`: `gate.json` (the statement pairs ship with the package, see `ontyra calibrate`) (all invented; nothing from a real store).
`test_data.py` checks the data (balance, uniqueness, realms), `test_framework.py` the client and
scorers.
