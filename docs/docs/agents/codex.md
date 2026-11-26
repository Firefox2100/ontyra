---
title: Codex
---

The Codex plugin does the same as the [Claude Code](./claude-code.md) one: a briefing at session
start, recall on each prompt, and the memory tools over MCP, plus the skill.

## Requirements

A running daemon with MCP enabled ([Quick start](../quick-start.md)), `curl` and `jq`, Codex CLI.

## Install

```bash
git clone https://github.com/Firefox2100/ontyra.git ontyra && cd ontyra
codex plugin marketplace add .
codex plugin add ontyra@ontyra
```

Start a new Codex session, then:

1. Run `/hooks` and **trust** the three Ontyra hooks. Codex skips hooks you have not reviewed,
   and asks again if the script changes.
2. Run `/mcp`: `ontyra` should be listed.

## Check that it works

Ask "what do you remember about this project?". Or send a prompt and look at the daemon: with a
stored rule, `curl -s localhost:8787/v1/briefing -X POST -H 'content-type: application/json' -d '{"context":{"uris":[],"signals":[]}}'`.
Anything the agent proposes appears in the operator console under Review.

## A daemon that requires a token

Export `ONTYRA_TOKEN` before starting Codex (an agent credential, see
[Authentication](../guides/authentication.md)). The hooks send it, and the MCP server entry reads
it through `bearer_token_env_var`. If it is unset, Codex connects without credentials.

## Another daemon address

Edit `url` in the plugin's `.mcp.json` and export `ONTYRA_URL` for the hooks.

## Remove

```bash
codex plugin remove ontyra@ontyra
codex plugin marketplace remove ontyra
```
