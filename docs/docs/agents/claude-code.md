---
title: Claude Code
---

The Claude Code plugin adds three things to every session:

- a **briefing** at session start (and after resume or compaction): the rules that apply where
  you are working;
- recall on each prompt: memories relevant to your message, injected as context;
- the **memory tools** (`recall`, `check`, `briefing`, `ask`, `get`, `remember`, `concepts`,
  `propose_ontology_change`) and a skill that tells the agent when to use them.

If the daemon is down or slow the session carries on without memory (the hooks fail open).

## Requirements

A running daemon with MCP enabled ([Quick start](../quick-start.md)), `curl` and `jq`.

## Install

From a clone of this repository, inside Claude Code:

```text
/plugin marketplace add /path/to/ontyra/integrations
/plugin install ontyra@ontyra
/reload-plugins
```

Or try it for one session without installing:

```bash
claude --plugin-dir /path/to/ontyra/integrations/claude-code
```

Another daemon address: `export ONTYRA_URL=http://host:port` before starting Claude Code.

## Check that it works

Start a session in a project and ask "what do you remember about this project?". The agent calls
`recall` or answers from the injected briefing. Plugin tools appear as
`mcp__plugin_ontyra_ontyra__*`.

## Letting the agent propose memories

The agent proposes what is durable without being asked. When a prompt reads like a rule,
preference, correction or decision, and every few prompts (`ONTYRA_CAPTURE_EVERY`, default 5),
a one-line reminder is added to the context already sent with the prompt. It costs no extra turn
or model call: the agent writes a proposal in the same turn, or does nothing. The same reminder asks
for a `feedback` verdict (helpful, wrong, outdated, irrelevant) on a recalled memory that clearly
helped or misled; the recall trace id shown with the injected memories lets the daemon learn from
it (see [Learning from feedback](../guides/feedback-learning.md)). Turn it off with
`ONTYRA_CAPTURE=0`. Nothing is stored without your approval unless you chose `auto_commit`; see
[Review and policy](../guides/review.md).

## Settings

| Variable | Default | Effect |
|---|---|---|
| `ONTYRA_URL` | `http://127.0.0.1:8787` | where the daemon is |
| `ONTYRA_TOKEN` | unset | the token to send, when the daemon requires one |
| `ONTYRA_CAPTURE` | `1` | reminder in the injected context to record what was settled (`0` turns it off) |
| `ONTYRA_CAPTURE_EVERY` | `5` | prompts between periodic reminders (rule-like prompts trigger it at once) |
| `ONTYRA_LOG_EPISODES` | `1` | log prompts and replies as episodes (what a memory's `quote` evidence is matched against; see [Evidence](../guides/evidence.md)) |
| `ONTYRA_BUDGET_TOKENS` | daemon default | size limit for injected context |
| `ONTYRA_PROFILE` | `compact` | `compact` renders each memory as one line with its id (a directive as its short rule; `get` gives the full text and rationale); `rich` adds the full text, anchors and scores |
