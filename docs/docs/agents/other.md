---
title: Other clients (MCP and REST)
---

Any client that speaks MCP over streamable HTTP, or HTTP, can use Ontyra.

## MCP

Endpoint: `http://127.0.0.1:8787/mcp/` (enable with `ONTYRA_MCP__ENABLED=true`).

Authenticate with `Authorization: Bearer <token>` when the daemon requires one ([Authentication](../guides/authentication.md)). Without accounts the caller labels itself with headers (labels, not credentials; ignored when accounts are on):

| Header | Meaning |
|---|---|
| `X-Ontyra-Actor` | a name, e.g. `my-agent` |
| `X-Ontyra-Actor-Kind` | `agent` (default) or `human` |
| `X-Ontyra-Harness` | the host program |

Two tool profiles, chosen with `ONTYRA_MCP__PROFILE`:

- `lite` (default): `recall`, `check`, `briefing`, `ask`, `get`, `remember`, `concepts`,
  `propose_ontology_change`.
- `full`: adds `search`, `overview`, `neighbors`, `list_memories`, and the review tools
  (`pending_proposals`, `approve`, `reject`, and the ontology equivalents).

Give the agent the `ontyra-memory` skill (`integrations/skills/ontyra-memory/SKILL.md`
in the repository; `integrations/skills/install.sh` copies it to `~/.agents/skills`) so it knows when to
recall, how to scope what it writes, and to check before side effects.

## REST

The same operations: `POST /v1/recall`, `/v1/briefing`, `/v1/check`, `/v1/search`, `/v1/ask`,
`/v1/proposals`, `/v1/episodes`, `/v1/feedback`, and `GET /v1/memories`. Run the daemon and open
`/docs` for the interactive reference, or `/openapi.json` for the schema (a generated copy is kept
in `integrations/contract/`).

Write status codes: `201` committed, `202` held for review, `422` rejected (the body lists why).
