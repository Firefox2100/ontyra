---
slug: /
sidebar_position: 1
title: What is Ontyra
---

Ontyra is long-term memory for agents, run as a small daemon that agent harnesses (Claude Code,
Codex, your own) talk to over REST or MCP.

:::caution Experimental
Ontyra is an experiment. The schema may change between versions, there is no authentication yet
(run it on loopback only), and the retrieval settings have not been tuned on a large corpus.
:::

## What it does

- **Remembers things in types**, not as a pile of notes: facts, directives (rules), decisions,
  events, procedures, tasks and lessons, each with its own fields.
- **Knows where a memory applies.** A rule can be tied to a folder, a repository, a host or a
  condition ("only when running Python 3.12"), so it reaches the agent only in that context.
- **Briefs and recalls.** When a session starts, the agent is told the rules that apply here. On
  each prompt, memories relevant to the message are retrieved and injected. When nothing clears
  the bar, nothing is injected.
- **Stays under your control.** Agents only *propose* memories. A proposal is validated and either
  committed or held until you approve it (in the operator console or over the API).
- **Keeps history.** Revisions are immutable; a corrected memory supersedes the old one, and you
  can see what changed and why.

The daemon makes no chat-model calls. Judgement (what is worth remembering, how to phrase it)
is the agent's; the daemon stores, validates, indexes and serves. Embeddings, reranking and an
optional decision model are external services you point it at.

## Where to go next

1. [Quick start](./quick-start.md): run the daemon and store your first memory in five minutes.
2. Connect an agent: [Claude Code](./agents/claude-code.md), [Codex](./agents/codex.md), or any
   [MCP or REST client](./agents/other.md).
3. [Concepts](./concepts/overview.md): what the pieces are and how recall decides.
4. [Deploy it](./guides/deployment.md) with Docker and a real embedding model.
