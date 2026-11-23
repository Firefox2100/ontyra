---
title: Learning from feedback
---

Feedback on recalled memories (`POST /v1/feedback`, MCP `feedback`) is kept as events. The **score
layer** uses the events of one owner to correct how recall ranks for that owner, without changing
any model: it works with a hosted reranker, a local one, or none. It is off by default.

```bash
ONTYRA_RECALL__ENGINE=fusion
ONTYRA_LEARNING__ENABLED=true
```

## What it does

Two corrections, both bounded, both recomputed from the feedback that still counts (nothing is
stored, so retracting or expiring an event changes the next result):

- **A prior per memory**: its verdicts, weighted by the trust of their source and halved every
  `HALF_LIFE_DAYS`, squashed and capped at `MAX_PRIOR`. A memory that keeps being judged
  irrelevant drifts down; one that keeps being applied drifts up.
- **A blend of the lane scores**: a logistic fit of "was it relevant?" over the scores the recall
  traces kept (lexical, dense, concept, reranker, decision model), starting from the plain
  ranking, kept within `MAX_SHIFT` of it and mixed in with a weight that grows with the amount of
  feedback up to `MAX_MIX`. No single source (actor) may weigh more than `SOURCE_CAP` of the fit.

Only your own feedback moves your own ranking; other owners' feedback never does.

## Where feedback comes from

Agents report it with the `feedback` tool (the memory, the query, and the `recall trace` shown with
the injected memories; the plugins' reminder asks for it when a memory clearly helped or misled,
without an extra turn). You can give it yourself in the console: the Recall page puts
helpful / irrelevant / outdated / wrong buttons under every memory, tied to that recall. A verdict
without a trace still counts for the memory's prior but gives the blend nothing to fit on
(`with_features` in the summary).

## The canary

A fixed share of the judged recalls (`CANARY_FRACTION`, chosen by a hash of the trace id) is never
trained on. After each refit the layer must rank them at least as well as the plain ranking, or it
stays off (`reverted`). Until there is enough feedback (`MIN_EXAMPLES` judged candidates and
`CANARY_MIN_GROUPS` canary recalls) it is `learning` and changes nothing.

Because the canary comes from the same feedback stream, it catches noise and drift, not a
consistent campaign of wrong verdicts. That is what example review is for, and a trusted
evaluation set is planned for retraining (plan 25).

## Reviewing the examples

Feedback is only as good as its verdicts. The console page **Feedback** (`/ui/feedback`), and
`GET /v1/feedback/outliers` (MCP `feedback_outliers`, full profile), rank your active examples by
how likely each is to be a mistake or poison:

- **disagrees**: the verdict is the opposite of what the ranking believed when it recalled the
  memory (the reranker's probability, or the final score without one);
- **flipped**: the same memory and query was judged both ways;
- **others disagree**: other owners judged the same shared memory the other way (an administrator's
  view, `everyone=true`);
- **burst**: one source sent a great many verdicts in a few minutes.

A **veto** (`POST /v1/feedback/{id}/veto`, with an optional reason; your own events, or anyone's for
an administrator) takes an example out of learning for good: the score layer is recomputed without
it, no training will use it, and the same source sending the same verdict again stays out too.
`unveto` puts it back. An **administrator can quarantine** a source (an actor) or a whole owner
(`POST /v1/feedback/exclusions`): nothing they sent counts anywhere until it is lifted.
**Opting out** (`POST /v1/feedback/opt-out`) is your own choice: your text and verdicts never enter
a training pool (training pools users' memory text into one model, so this matters on a shared
daemon), while your own score layer, which changes only your own ranking, keeps learning from
them. Reviewing needs a human credential.

## Retraining the reranker

With the reranker worker (`ONTYRA_RERANK__PROVIDER=worker`) a human can retrain it from feedback
(`POST /v1/reranker/train`; `GET /v1/reranker` shows the version, the feedback available and past
runs). It is always explicit, and always starts from the base model, so vetoed, expired and
deleted examples are really gone. The worker trains a LoRA adapter on the backbone and the
one-logit head; it serves "busy" meanwhile, and recall uses its plain ranking until it is back.

What goes in is a policy, not a surprise:

- `ONTYRA_RERANK__POOL`: `own` (default) trains from the caller's feedback only, `all` from
  everyone's, which is for one trust domain, not for strangers sharing a daemon. Vetoed,
  quarantined and opted-out examples never train.
- `ONTYRA_RERANK__TRAIN_SOURCES`: `all` (agents' verdicts count at their lower trust; where a human
  judged the same memory and query, the human's verdict stands) or `human`.
- `ONTYRA_RERANK__SOURCE_CAP`: no source may have more than this share of the training weight.
- `ONTYRA_RERANK__ANCHOR_RATIO`: clean, labelled pairs from the bundled benchmark worlds are mixed
  in per feedback pair, so a small or noisy pool cannot make the model forget what it knew.

A new version is **not activated by training**. The worker scores a trusted set (the benchmark's
held-out requests) with the old and the new version, and the daemon activates the new one only if
it is no worse than `ONTYRA_RERANK__GATE_TOLERANCE` on both mean reciprocal rank and pairwise
accuracy. A pool that was poisoned to fit itself passes validation on its own held-out pairs, but
not this. Every run is recorded (`reranker_run`, with the gate's numbers); a rejected version stays
on the worker's disk, and any version can be activated again.

## The live head edit

Between retrainings the worker's one-logit head can follow feedback in small steps
(`ONTYRA_RERANK__LIVE=true`; it needs `ONTYRA_RERANK__POOL=all`, because the head is shared by every
user: per-owner learning is the score layer above). It is built to be undone:

- **A replay, not an accumulation.** The served head is the version's own head plus an edit, and the
  edit is refitted from zero on the examples that count now, every time. A veto, a retraction or an
  exclusion therefore simply gives a fit without that example; there is nothing to subtract.
- **Bounded.** The edit may be at most `ONTYRA_RERANK__LIVE_MAX_DISTANCE` (default 25%) of the
  head's own norm, and is pulled towards zero. The backbone is never touched, so the judged texts'
  hidden states are computed once and refits take a fraction of a second.
- **Gated.** After each fit the trusted benchmark requests are scored with and without the edit;
  if it ranks them worse than `GATE_TOLERANCE` allows, it is not applied (`reverted`) and the head
  serves unedited.
- **Batched and explicit.** Feedback changes schedule one refit, at most every `LIVE_EVERY_SECONDS`
  (default 5 minutes), once `LIVE_MIN_EXAMPLES` usable pairs of both classes exist; below that the
  edit is removed. `POST /v1/reranker/live` refits now, `POST /v1/reranker/live/reset` drops the
  edit, `GET /v1/reranker` shows its distance and whether feedback has changed since.
- **Replaced by retraining.** A retraining starts from the base and its new version has no edit.

## Looking at it

`GET /v1/feedback/layer` says what the layer does for you: its status (`off`, `learning`, `active`,
`reverted`), why, how many examples and recalls it learned from, the canary's accuracy with and
without the layer, and the fitted coefficients. Recall traces keep `base` (the score before the
layer) next to `final` for each candidate. Settings: [Configuration](../reference/configuration.md).
