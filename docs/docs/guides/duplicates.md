---
title: Finding duplicates
---

Memories pile up. An agent that did not look first can store a rule that is already there, worded
differently, and recall then spends your token budget on both. `POST /v1/consolidation/scan` (the
`find_duplicates` MCP tool in the `full` profile) reports them. It only reads: nothing is merged,
superseded or retracted. You resolve a cluster with an ordinary proposal; [maintenance
tasks](./maintenance.md) queue the clusters and record what was decided.

```bash
curl -s localhost:8787/v1/consolidation/scan -H 'content-type: application/json' -d '{}'
```

## What it reports

- `duplicates`: clusters of memories of the same kind that say the same thing, oldest first.
  `keep` is the oldest; supersede or retract the others (or merge their wording into it) with a
  `supersede` or `retract` proposal.
- `updates`: a pair that is the same statement with a changed value (PostgreSQL 14 and 16, a
  300 EUR limit and a 500 EUR one). That is not a duplicate: the newer one probably supersedes the
  older, and merging would lose the change. The two are told apart by the numbers, versions and
  dates they contain.
- `scanned`, `unembedded` (memories without a vector, skipped), `rejected` and `model_errors`
  (see below).

A scan looks at your own memories and the global ones, never pairs a memory with one of another
owner, and compares only memories of the same kind that apply in a common place (anchors that
contain one another, or none).

## How a pair is judged

1. **Candidates** are the nearest neighbours of each memory in the embedding index with a cosine of
   at least `ONTYRA_CONSOLIDATION__CANDIDATE_COSINE` (0.70).
2. **Without a decision model**, a candidate is a duplicate when its cosine reaches
   `ONTYRA_CONSOLIDATION__DUPLICATE_COSINE` (0.85).
3. **With a decision model** ([System One](../reference/configuration.md), the same
   `ONTYRA_DECISION__*` settings as recall), it is asked in both orders whether the two say the same
   thing; a candidate is a duplicate when both answers reach `ONTYRA_CONSOLIDATION__CONFIRM` (0.85).
   A pair it cannot answer is judged by the cosine rule and counted in `model_errors`; one it
   rejects is counted in `rejected`.

The model is advice, never the last word, and its answers are not fed back into any trained model.
The cosines suit `qwen3-embedding-8b`; recalibrate them for another embedder.

## How well it works

Measured on the benchmark world (`tests/benchmark_test`: 47 planted duplicates among 429
memories, 11% restated by hand as light edits or full paraphrases) with `qwen3-embedding-8b`:

| Judged by | Duplicates found | Precision |
|---|---|---|
| cosine ≥ 0.90 | 40 of 47 | 1.00 |
| cosine ≥ 0.85 (the default) | 45 of 47 | 1.00 |
| cosine ≥ 0.80 | 47 of 47 | 0.86 (8 false pairs: `molarity`/`molality`, `pay in 30`/`pay in 45 days`, …) |
| `kev-9b` confirming candidates ≥ 0.70 | 45 of 47 | 1.00 (it rejected 45 candidate pairs) |

So a well-chosen cosine threshold does as well as the decision model on this world, and the model's
advantage is that the result does not depend on that choice: a threshold that is a little too low
costs precision, a little too high costs recall, and the model avoids the trade. The thresholds
were tuned on this one world with one embedder, so check them on yours.

Reproduce: `BENCH_PROFILES=profiles.json uv run pytest tests/benchmark_test/test_consolidation.py`
(a profile sets the embedder, an optional `decision` model and `consolidation` thresholds).
