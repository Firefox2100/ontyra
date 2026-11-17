---
name: ontyra-memory
description: How to read and write Ontyra long-term memory (recall, remember, concepts, ontology changes). Use when the user states a lasting preference, rule or decision, when recall returns memories, or when asked to remember, forget, or correct something.
---

# Using Ontyra memory

Ontyra is a local memory daemon (REST + MCP, default `http://127.0.0.1:8787`). Memories reach
you in two ways: injected context (a briefing at session start, recall on each prompt, when a
plugin or hook is installed) and the `ontyra` MCP tools (`recall`, `check`, `briefing`, `ask`,
`get`, `remember`, `concepts`, `propose_ontology_change`). If you have no MCP tools, the same
operations are REST calls under `/v1` (`POST /v1/recall`, `/v1/check`, `/v1/proposals`, …; send
headers `X-Ontyra-Actor: <your name>` and `X-Ontyra-Harness: <harness>`; the schema is at
`/openapi.json`). If the daemon is unreachable, carry on without memory and tell the user once. Treat injected memories as advice about this user and
project. **`Must follow` items are directives: obey them**, and tell the user if one conflicts
with what they just asked.

## When to write (`remember`)

Write only what is durable and non-obvious: a preference or rule ("never force-push main"), a
decision with its reason, a fact about the environment, a lesson from a failure. Do not write
anything transient, anything already in the code or git history, or anything you already got
from recall. One memory per statement.

Call `remember` with a proposal: `{"proposal": {"ops": [{"op": "add", "kind": ..., "fields": {...}}]}}`.

| kind | fields |
|---|---|
| `directive` | `text`, `rule`, `strength` (`must`/`should`/`prefer`), `polarity` (`do`/`avoid`) |
| `fact` | `text`, `subject`, `predicate`, `object` |
| `decision` | `text`, `chosen`, `rationale`, `decided_at`, `alternatives` |
| `lesson`, `procedure`, `event`, `task` | `text` plus the kind's own fields |

Say **where it applies** or it will not be found by context: `scope: "universal"` for a standing
preference that holds everywhere; otherwise `anchors` (`{"uri": "file://host/path", "match":
"subtree"}`), `about` (concept ids) and `qualifiers`. A directive with none of these gets an
`unscoped_directive` warning: fix it. To change an existing memory use `revise` or `supersede`
(read the findings: a duplicate warning usually means supersede). Quote user words as `evidence`
when you can. The result says `committed` or `pending`; pending means a human must approve it.
Never claim a memory is saved until the status says `committed`.

## Concepts and the ontology

`about` and qualifiers use concept ids. Look a term up with `concepts` (label or alias) before
using or inventing it. If the concept does not exist, or two names mean one thing, propose a
change with `propose_ontology_change`: `define`, `add_alias`, `relate`,
`merge` (duplicates) or `deprecate`, with a one-line `rationale`. Read its `advice` for similar
concepts, and prefer an alias or a merge over a new concept. Changes wait for the human.

## Before side effects

Call `check` with your intent before destructive or hard-to-undo actions (deleting, pushing,
deploying, migrating). It returns every applicable `must` directive plus prerequisites.

## Review

A human approves held proposals and ontology changes (`/v1/proposals`, `/v1/ontology/changes`).
Never try to approve your own: the tools refuse, and it defeats the point of review.

## Feedback

If a recalled memory was wrong or outdated, say so and propose a `revise`/`supersede`/`retract`
rather than silently ignoring it.
