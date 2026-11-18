---
title: Evidence and episodes
---

A memory can say where it came from. The source is an **episode**: one raw message of a
conversation, stored word for word and never changed. The Claude Code, Codex and DSH plugins log
every prompt and reply to `POST /v1/episodes` for you (`ONTYRA_LOG_EPISODES=0`, or the `episodes`
setting in DSH, turns that off).

## Citing what was said

An agent that writes a memory cites the words behind it with a **quote**. It needs no id:

```json
{"op": "add", "kind": "directive", "fields": {"...": "..."},
 "evidence": [{"quote": "from now on, always use pytest"}]}
```

The daemon looks for the quote in the caller's recent episodes, newest first, ignoring case and
spacing. What a person said wins over what was said back to them. The proposal then stores the
episode it found, so a review days later still points at the same message. A quote that occurs
nowhere is a `grounding_failed` error and the proposal is rejected: a memory cannot claim a
sentence nobody said. An explicit `episode` id is still accepted, and the quote must then occur
in that episode.

## How long episodes are kept

The harness keeps its own transcript; the daemon's log exists to check quotes. An episode that no
memory cites is deleted after `ONTYRA_EPISODES__RETENTION_DAYS` (default 14; 0 keeps everything).
The sweep runs at most once per `ONTYRA_EPISODES__PRUNE_INTERVAL_MINUTES`, when an episode is
written. A cited episode is kept for as long as the memory it supports exists.

| Setting | Default | Meaning |
|---|---|---|
| `ONTYRA_EPISODES__MATCH_WINDOW_HOURS` | `72` | how far back a quote is looked up |
| `ONTYRA_EPISODES__MATCH_LIMIT` | `500` | most recent episodes examined |
| `ONTYRA_EPISODES__RETENTION_DAYS` | `14` | age at which an uncited episode is deleted |
| `ONTYRA_EPISODES__PRUNE_INTERVAL_MINUTES` | `60` | minimum time between sweeps |
