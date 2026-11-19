---
title: Calibrating thresholds
---

Several settings are numbers whose right value depends on the model that produces the scores: the
cosine below which recall stays silent, how close to the best result a candidate must be, the
cosines of the duplicate scan. A different embedder needs different values. `ontyra calibrate`
suggests them.

```bash
ontyra calibrate            # lists what it will ask of the embedder, then stops
ontyra calibrate --yes --out calibration.json
ontyra calibrate --from calibration.json      # read a saved run again: no model calls
```

It runs a labelled benchmark that ships with Ontyra (an invented person's memory, a few hundred
requests with the right answers, and pairs of statements labelled duplicate, update or different)
through **your configured embedder** in a scratch store, then searches the settings offline.
It prints the number of embedding requests first and will not call a non-`hash` embedder without
`--yes`, because a hosted model may bill for it (about a thousand short texts; a local model takes
some minutes). The deployed database is never touched: the scratch store is in memory, or a
throwaway database of the same SurrealDB server in its own namespace.

The output is a block of environment variables with its evidence:

- the pass rate of your **current** settings and of the **suggested** ones, on the part of the
  benchmark used for the search (`tune`) and on a part held back (`holdout`);
- how well the model separates relevant from irrelevant memories at all; a model that does not is
  told so rather than given a threshold;
- whether the suggestion is the middle of a plateau of equally good settings or a knife edge.

Apply the values in the daemon's environment and restart it. They fix the model's **score scale**
(where relevant and irrelevant cosines sit), not your domain: the benchmark world is invented.

## The decision model and the reranker

When a decision model (`ONTYRA_DECISION__*`) or a reranker (`ONTYRA_RERANK__*`) is configured, the
same run also asks it about the benchmark's requests and pairs, and reports:

- `decision_weight` and `decision_margin` for the decision model, `rerank_weight` for the reranker
  (with the margin to use alongside it): the pass rate **without** the model, with your current
  values, and with the suggested ones. If the model does not improve on the plain ranking the
  output says so;
- `confirm` for the duplicate scan: the lowest score at which at most 5% of what the model accepts
  is not a real duplicate.

The cost is listed first (a decision request per benchmark request plus two per pair) and `--yes`
is required, because these models may be billed too. The run also prints
`ONTYRA_RECALL__CALIBRATED_FOR=<embedder>`; with it set, the console's overview tells you when the
embedder later changes and the thresholds no longer fit.

## Checking your own store

```bash
ontyra calibrate --own            # needs ONTYRA_RECALL__ENGINE=fusion
```

The benchmark fixes the scale of the model; your data can sit elsewhere. This read-only check
queries your configured database with the first words of about sixty of your own memories
(*on-topic*: the memory itself should come first) and with the benchmark's unrelated requests
(*off-topic*), and reports where your `abstain_cosine` falls between the two distributions. It
needs no labels and only the embedder, and writes nothing: no trace, no usage. On-topic probes are
easier than real requests, so use it to spot a threshold that is clearly wrong for your data, not
to fine-tune one.

## What recall records

With the fusion engine each recall trace keeps the lane scores of the best candidates (cosine,
BM25, lexical, dense, fused, reranker and decision-model probabilities, and whether each was
shown), `ONTYRA_RECALL__TRACE_CANDIDATES` of them (0 turns it off). Feedback that cites the trace
then has the features it was given for: `GET /v1/feedback/summary` counts them as `with_features`.
Traces older than `ONTYRA_RECALL__TRACE_RETENTION_DAYS` (default 180) are deleted unless a
feedback event cites them. Tuning the thresholds from this feedback is planned, not built.
