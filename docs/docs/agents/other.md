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

- `lite` (default): `recall`, `check`, `briefing`, `ask`, `consult`, `get`, `remember`, `feedback`,
  `check_source`, `concepts`, `propose_ontology_change`, `pending_proposals` and `amend`.
- `full`: adds search and browsing, duplicate detection, maintenance tasks, feedback review,
  ontology history, and the human review tools. The generated
  `integrations/contract/mcp-tools-full.json` file is the exact list for the current source tree.

Give the agent the `ontyra-memory` skill (`integrations/skills/ontyra-memory/SKILL.md`
in the repository; `integrations/skills/install.sh` copies it to `~/.agents/skills`) so it knows when to
recall, how to scope what it writes, and to check before side effects.

## REST

The REST API also exposes `consult`, source checks, ontology management, maintenance tasks,
feedback review, reranker administration and account administration. Run the daemon and open
`/docs` for the interactive reference, or `/openapi.json` for the schema. The repository keeps a
generated copy at `integrations/contract/openapi.json` and tests it for drift.

Write status codes: `201` committed, `202` held for review, `422` rejected (the body lists why).
