---
title: Knowledge from sources
---

Memories are mostly what you tell the agent: instructions, preferences, decisions. Knowledge is what
a *source* says: a paper, a manual, a web page, a data file. Ontyra stores the conclusion and a
**reference** to where it came from. It does not store, read or require the source.

```json
{"op": "add", "kind": "fact",
 "fields": {"text": "Gene X is relevant to the Y pathway, which regulates Z", "about": ["gene_x"]},
 "evidence": [{"source": {"uri": "file:///papers/gene-x.pdf", "locator": "section 3, p. 4",
                          "quote": "X acts upstream of Y", "retrieved_at": "2026-11-23T10:00:00Z"}}]}
```

Only `uri` is required (`file:///path`, `https://...`, `doi:...`, `arxiv:...`; canonicalised, a
web page keeps its query). Add a `locator` (where in it), a short literal `quote` and, if you have
one, a `content_hash`: the quote stays with the memory even if the source is later edited or
deleted, and the hash lets a later check say whether it changed. Evidence is either words that
were said (an episode quote) or a source, not both; a memory can have several.

## What recall does with it

Recall returns the conclusion, with a pointer: `(source: file:///papers/gene-x.pdf section 3, p. 4)`.
To verify, an agent reads the source itself. Nothing in the daemon ever fetches it.

## Checking a source

When an agent or a tool re-reads a source it reports what it found (`POST /v1/sources/check`, MCP
`check_source`): `ok`, `changed` or `missing`. `changed` and `missing` open a review task for each
live memory that cites it (retract, revise, keep, defer; the task shows the quote that was
cited) and recall adds a caveat (`its source was reported changed`) until it is settled. The
daemon never polls, so a stale link costs nothing until someone looks. `GET /v1/sources?uri=` lists
the memories that cite a source and its last report.

## Vetting: auto-committed is not the same as reviewed

A memory a human wrote, or approved, is **vetted**. One that policy committed for an agent without
a human looking is **auto**: recall uses it and marks it `[unvetted]`. Set
`ONTYRA_RECALL__MIN_VETTING=vetted` to leave unvetted memories out of recall entirely. A human
works through them in `GET /v1/vetting/pending` and confirms each with `POST /v1/vetting/{id}/vet`. The console has a **Knowledge** page (`/ui/knowledge`) for the same: it lists what waits for vetting with its sources, lists sources reported changed or missing, and looks up a source to record a check.

This is what makes agent-extracted knowledge practical: a policy rule can commit an agent's fact
when it cites a source with a literal quote (so it can be audited), and hold the rest:

```bash
ONTYRA_POLICY__RULES='[
  {"actor_kinds":["human"],"decision":"auto_commit"},
  {"actor_kinds":["agent"],"kinds":["fact"],"sourced":true,"decision":"auto_commit"}
]'
```

`sourced: true` matches when every op cites a source with a `quote` (`false`: none may). The shipped
default still holds all agent writes for review.

## How recall treats cited knowledge

- **Its own section and budget.** A memory that cites a source (other than a directive, procedure,
  lesson or task) comes back under **Knowledge**, packed within `ONTYRA_RECALL__KNOWLEDGE_BUDGET_TOKENS`
  (default 600) on top of the main budget, so it neither crowds out directives nor is crowded out.
  `0` makes it share the main budget. The default is untuned: there is no documents benchmark yet.
- **Source tiers.** `ONTYRA_RECALL__SOURCE_TIERS` is your judgement of where sources come from, a JSON
  list of `{"match": "<uri prefix>", "tier": "<name>", "weight": 1.2}`. The first prefix that matches
  a cited source sets the tier, the best source of a memory counts, and the weight multiplies its
  score. The agent cannot claim a tier: it comes from the URI and your configuration.
- **Corroboration.** A memory citing several distinct sources gets `ONTYRA_RECALL__CORROBORATION_BOOST`
  (default 0.05) per extra source, for up to three, and recall says "N independent sources".
- **Age.** When the most recent reading of a source (`retrieved_at`) is older than
  `ONTYRA_RECALL__SOURCE_STALE_DAYS` (default 365, `0` off) recall adds a caveat.

## Bulk ingestion

Reading thousands of papers is a job for a separate client (an agent, or a tool using a local
model) that writes facts through the same proposal API as any actor. The daemon has no ingestion
pipeline; it stores what is proposed, with its sources.
