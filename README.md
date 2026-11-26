# ontyra

English | [简体中文](README.zh-cn.md)

[![Quality gate status](https://sonarcloud.io/api/project_badges/measure?project=Firefox2100_ontyra&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=Firefox2100_ontyra) [![Coverage](https://sonarcloud.io/api/project_badges/measure?project=Firefox2100_ontyra&metric=coverage)](https://sonarcloud.io/summary/new_code?id=Firefox2100_ontyra) [![Reliability Rating](https://sonarcloud.io/api/project_badges/measure?project=Firefox2100_ontyra&metric=reliability_rating)](https://sonarcloud.io/summary/new_code?id=Firefox2100_ontyra) [![Security Rating](https://sonarcloud.io/api/project_badges/measure?project=Firefox2100_ontyra&metric=security_rating)](https://sonarcloud.io/summary/new_code?id=Firefox2100_ontyra)

Experimental long-term memory system for agents: typed, layered memory in a daemon that agent
harnesses (Claude Code, Codex, DSH, any MCP client) talk to over REST or MCP.

Status: experiment. Authentication is optional (a single token, or per-user accounts); with none, run it on loopback only.

## Try it

```bash
uv sync --extra embedded --extra mcp
export ONTYRA_DB__URL="surrealkv://$HOME/.ontyra/ontyra.db" ONTYRA_SERVER__PORT=8787 \
  ONTYRA_MCP__ENABLED=true ONTYRA_UI__ENABLED=true ONTYRA_POLICY__DEFAULT=auto_commit
uv run ontyra serve        # http://127.0.0.1:8787  (console at /ui/, API docs at /docs)
```

Then connect an agent: `integrations/claude-code` (Claude Code plugin), `integrations/codex`
(Codex plugin) or `integrations/dsh` (DeepSeek Harness plugin). To load documents as sourced
knowledge, use the ingestion client in `integrations/ingest`. Full guides, in English and Chinese,
are in `docs/` (`cd docs && pnpm install && pnpm start`).
