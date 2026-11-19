# Benchmark suite

Measures recall quality on labelled scenarios, with the embedder (and later reranker and
proposing LLM) as a parameter of the run. Design: `plans/18-benchmark.md` (local). Modelled on
the labelled-case approach of `dsh-agentic-roleplay`: planted ground truth, precision and
recall, per-cell reporting, immutable result files.

```
uv run pytest tests/benchmark_test                      # hash embedder, asserts (regression guard)
BENCH_EMBED_URL=http://localhost:8081/v1 BENCH_EMBED_MODEL=qwen3-embedding-4b \
BENCH_EMBED_DIM=2560 BENCH_LABEL=qwen3-4b \
BENCH_EMBED_QUERY_PREFIX=$'Instruct: Given a task or message from a developer, retrieve the stored notes that apply\nQuery:' \
  uv run pytest tests/benchmark_test/test_recall.py     # real embedder: measures, does not assert
```

Several embedders (and recall settings) in one run: list them as **profiles** in a JSON file
(`profiles.example.json`; a `sweep` expands one profile into one per setting combination) and
compare the result files:

```
BENCH_PROFILES=my-profiles.json BENCH_LABEL=trial uv run pytest tests/benchmark_test/test_recall.py
uv run python tests/benchmark_test/compare.py tests/benchmark_test/results/recall-trial-*.json
```

Settings that change the pipeline are the production ones (`ONTYRA_RECALL__*`), e.g.
`ONTYRA_RECALL__VECTOR_FLOOR=0.6`; they are recorded in the result file.

## What is where

- `src/ontyra/calibration/data/worlds/commons/` (ships with the package): **one pool for a whole person's memory** (350 memories, 266 concepts):
  a directory with `world.json` plus one file per domain (`00-core`, `dev-quayside`,
  `dev-other`, `office-finance`, `office-admin`, nine `research-*` fields, `daily-personal`,
  `daily-home`, `misc-hobbies`). Everything lives in one store on purpose: leakage between
  fields is what is measured. Every kind (fact, directive, decision, event, procedure, task,
  lesson) appears in every domain group, and every planted **trap** (`trap` on a memory:
  superseded, retracted, expired, qualifier, anchor, overridden, contradicted, opposite,
  homonym, twin, stale) appears at least three times; `test_data.py` enforces both.
  Memories can use shorthands (`about`, `at`, `q`, `strength`, see `framework/models.py`).
- `src/ontyra/calibration/data/cases/recall/*.json` (291 cases): `include` (must come back), `exclude` (must not),
  `allow`/`allow_prefix` (arguable, never penalised). **Scoring policy:** anything else that
  comes back from the *same domain part* as an expected memory is *tolerated* (counted, not
  penalised: a neighbouring finance rule next to the right finance rule is not a leak);
  anything from another domain is a false positive (a leak); `strict` and `silent` cases
  tolerate nothing. Other expectations: `top`, `min_cautions`, `checks`, `caveats`,
  `verdict`, `then` (more requests in one session). `tags` group the report; `split` is
  `tune` or `holdout`.
- **Paraphrase cases** (tag `paraphrase`): the validator rejects a query when more than 20% of
  its content words appear in an expected memory, so they test meaning, not shared words.
- `data/baselines/hash.json`: the cases the built-in hash embedder fails, frozen by
  `PYTHONPATH=tests uv run python -m benchmark_test.baseline RESULT.json`. The suite asserts
  (hash embedder, default settings only) that nothing else fails and that no listed case
  starts passing; regenerate deliberately after reading the diff.
- `framework/`: `models` (data, shorthands), `validate`, `score`, `results`, `run`, `config`
  (profiles). `conftest.py`: fixtures. `compare.py` (table over result files), `cutoff.py` (offline search of the fusion cut-off settings and `abstain_cosine` on recorded lane scores: `PYTHONPATH=tests uv run python -m benchmark_test.cutoff RESULT...`), `separation.py`
  (threshold-free ranking quality of an embedder), `baseline.py`.
- `results/<suite>-<label>-<profile>.json` (git-ignored): configuration, git rev, per-tag and
  per-split cells, every case with what came back, ranks, scores and `why`. Never overwritten.

Test files: `test_data.py` (data is consistent and covers the matrix), `test_framework.py`
(scorer, results, config), `test_recall.py` (the suite, ~1 minute). Add a world part or case by
editing JSON only; run `test_data.py` first.

## Scores, not just pass/fail

Every request is also **probed**: the lanes run with the candidate limit raised to the whole pool
and the result file records, for *every* memory, raw BM25, raw cosine, concept weight, query
coverage, whether it applies in the context, and the pipeline's relevance and final score (a
gzipped sidecar `*.rows.jsonl.gz`; the main file holds per-request metrics and a `retrieval`
summary per tag/split). From these, threshold-free metrics are computed per lane (`bm25`,
`lexical` = relative BM25 x coverage, `cosine`, `concept`, `relevance`, `score`): recall@1/3/5/10,
MRR, nDCG@10 and AUC over the memories that apply. They say how well a lane *orders* memories
before any cut-off, which is what a hybrid (lexical + embedding, then rerank) design optimises.

```
uv run python tests/benchmark_test/compare.py --lanes [overall|tag:paraphrase|split:holdout] RESULT...
PYTHONPATH=tests uv run python -m benchmark_test.explain CASE_ID RESULT...   # one case, every lane's score and rank
PYTHONPATH=tests uv run python -m benchmark_test.fusion RESULT...            # lanes, RRF and convex combinations, tune vs holdout
```

Lane scores do not depend on the embedder's `vector_floor`, so one run per model is enough to
compare models and fusion strategies offline; floor sweeps only matter for the pass/fail view.

## Environment (see also `framework/config.py`)

`BENCH_PROFILES`, `BENCH_LABEL`, `BENCH_ONLY` (case id substring), `BENCH_RESULTS_DIR`, `BENCH_ASSERT`
(default: only for the hash embedder), `BENCH_EMBED_URL|MODEL|DIM|API_KEY|QUERY_PREFIX|
DOCUMENT_PREFIX|CLASS_PATH|KWARGS`, `BENCH_RERANK_CLASS_PATH|KWARGS`. The OpenAI-compatible
embedder needs `langchain-openai` (a dev dependency).

Rule: do not tune a threshold from unlabelled data; add a labelled case first, tune on
`tune`, confirm on `holdout`.

## Decision model in a profile

A profile can enable the decision model like any other setting: `"decision": {"provider":
"systemone", "url": "http://localhost:8083/v1", "model": "kev-9b"}` (any `ONTYRA_DECISION__*`
field) with `recall` overrides `decision_candidates`, `decision_weight`, `decision_margin` and
`"engine": "fusion"`. The same profile without `decision` is the deterministic baseline.


## Dialogues (`test_dialogue.py`)

Recall cases are single requests. Real prompts are often not ("yes, do it", "and what went wrong
the last time we bumped it?"), so the dialogue suite plays scripted conversations
(`data/cases/dialogue/*.json`, 31 dialogues, 80 turns across dev, office, research, daily life and
hobbies, every memory kind, and the shapes anaphora, ellipsis, confirm, correction, topic switch,
pasted logs, multi-topic, session ledger and redelivery) and scores one recall per user turn.

A turn is marked `needs_history` when its message cannot be understood alone; it then carries a
hand-written `standalone` query, and the validator rejects a history-dependent message that names
its own answer. The suite is a **measurement, not a regression gate**: it asserts nothing. It plays
every dialogue once per *strategy* for building the request from the history
(`framework/dialogue.py`): `last` (today's behaviour), `users2`/`users3` (the last 2 or 3 user
messages joined), `window` (the last two exchanges including the assistant's replies), `hints`
(keywords of the previous exchange as hints), `adaptive` (history only when the message is thin or
points back), `union` (results of `last` and `users2` merged) and `oracle` (the standalone query:
the upper bound). `BENCH_HISTORY` picks the strategies. Reports split the turns into `standalone`
and `needs_history` so a strategy that helps one and hurts the other shows. The evaluation suite
adds the `llm` strategy (the model writes the query).

```
BENCH_PROFILES=prod.json BENCH_LABEL=dlg uv run pytest tests/benchmark_test/test_dialogue.py -s
```
