---
title: Claude Code
---

The Claude Code plugin adds three things to every session:

- a **briefing** at session start (and after resume or compaction): the rules that apply where
  you are working;
- **recall on each prompt**: memories relevant to your message, injected as context;
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

By default the agent writes only when you ask it to ("remember that we use uv"). To have it
reflect every few prompts and propose what is durable, start Claude Code with
`ONTYRA_CAPTURE=1` (`ONTYRA_CAPTURE_EVERY`, default 10 prompts). Nothing is stored without your
approval unless you chose `auto_commit`; see [Review and policy](../guides/review.md).

## Settings

| Variable | Default | Effect |
|---|---|---|
| `ONTYRA_URL` | `http://127.0.0.1:8787` | where the daemon is |
| `ONTYRA_CAPTURE` | off | periodic "anything worth remembering?" prompt |
| `ONTYRA_CAPTURE_EVERY` | `10` | prompts between capture prompts |
| `ONTYRA_LOG_EPISODES` | `1` | log prompts and replies as episodes (what proposals cite as evidence) |
| `ONTYRA_BUDGET_TOKENS` | daemon default | size limit for injected context |
