# ontyra

Experimental long-term memory system for agents: typed, layered memory in a daemon that agent
harnesses (Claude Code, Codex, any MCP client) talk to over REST or MCP.

Status: experiment. Authentication is optional (a single token, or per-user accounts); with none, run it on loopback only.

## Try it

```bash
uv sync --extra embedded --extra mcp
export ONTYRA_DB__URL="surrealkv://$HOME/.ontyra/ontyra.db" ONTYRA_SERVER__PORT=8787 \
  ONTYRA_MCP__ENABLED=true ONTYRA_UI__ENABLED=true ONTYRA_POLICY__DEFAULT=auto_commit
uv run ontyra serve        # http://127.0.0.1:8787  (console at /ui/, API docs at /docs)
```

Then connect an agent: `integrations/claude-code` (Claude Code plugin) or `integrations/codex`
(Codex plugin). Full guides are in `docs/` (`cd docs && pnpm install && pnpm start`).
