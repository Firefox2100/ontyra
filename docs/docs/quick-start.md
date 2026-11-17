---
sidebar_position: 2
title: Quick start
---

This runs the daemon with an embedded database: no Docker, no model server. Retrieval quality is
basic (a built-in lexical embedder); see [Embeddings](./guides/embeddings.md) to upgrade.

## Requirements

[uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for you), `curl` and `jq`.

## 1. Start the daemon

```bash
git clone https://github.com/Firefox2100/ontyra.git ontyra && cd ontyra
uv sync --extra embedded --extra mcp

export ONTYRA_DB__URL="surrealkv://$HOME/.ontyra/ontyra.db"   # where the data lives
export ONTYRA_SERVER__PORT=8787                                # the port the plugins expect
export ONTYRA_MCP__ENABLED=true                                # the MCP endpoint at /mcp/
export ONTYRA_UI__ENABLED=true                                 # the operator console at /ui/
export ONTYRA_POLICY__DEFAULT=auto_commit                      # solo use: writes commit at once
uv run ontyra serve                                            # http://127.0.0.1:8787
```

Check it: `curl -s localhost:8787/health`. Interactive API docs are at `/docs`.

:::tip Review instead of auto-commit
`auto_commit` is convenient on your own machine. The default, `review`, holds every proposal until
a human approves it, and is what you want when agents write. See
[Review and policy](./guides/review.md).
:::

## 2. Store a memory

A write is a *proposal* made of operations. `X-Ontyra-Actor-Kind: human` marks you as the owner
(agents use the default, `agent`).

```bash
curl -s -X POST localhost:8787/v1/proposals \
  -H 'content-type: application/json' \
  -H 'X-Ontyra-Actor: me' -H 'X-Ontyra-Actor-Kind: human' \
  -d '{"ops": [{"op": "add", "kind": "directive", "fields": {
        "text": "Use uv and Python 3.12 for Python projects.",
        "strength": "must",
        "rule": "Use uv and Python 3.12",
        "scope": "universal"}}]}' | jq
```

`201` with `"status": "committed"` means it is stored.

## 3. Read it back

```bash
# What applies at the start of a session
curl -s -X POST localhost:8787/v1/briefing -H 'content-type: application/json' \
  -d '{"context": {"uris": [], "signals": []}}' | jq -r .rendered

# What is relevant to a message
curl -s -X POST localhost:8787/v1/recall -H 'content-type: application/json' \
  -d '{"query": "set up a new python project", "context": {"uris": [], "signals": []}}' | jq -r .rendered
```

Open `http://127.0.0.1:8787/ui/` to browse memories, try recall with a location, and review
proposals.

## 4. Connect an agent

Agents get the briefing and recall automatically through a plugin, and can write through MCP:

- [Claude Code](./agents/claude-code.md)
- [Codex](./agents/codex.md)
- [Anything else](./agents/other.md)
