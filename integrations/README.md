# Integrations

Everything that connects a harness to the daemon. These live in this repository so a change to
the API and the code that uses it land together, but they are **separate artifacts** and never
import `ontyra` (a unit test enforces it): the only coupling is the contract.

| Folder | What | Language |
|---|---|---|
| `contract/` | OpenAPI + MCP tool schemas, generated: `uv run python scripts/export_contract.py` (CI fails if stale) | JSON |
| `claude-code/` | Claude Code plugin: hooks over REST, MCP config | shell, JSON |
| `codex/` | Codex plugin: hooks over REST, MCP config, skill (marketplace at `.agents/plugins/`) | shell, JSON |
| `dsh/` | DeepSeek Harness plugin | TypeScript |
| `ingest/` | `ontyra-ingest`: bulk-ingestion CLI (documents → sourced facts over REST) | Python (typer) |

Rules: talk to the daemon over REST or MCP only; fail open (a dead daemon must never block the
harness); keep prompts and task cards in the daemon (served as MCP prompts/resources), not
copied here. Each integration versions and releases on its own; releases are tagged with a prefix
(`daemon-v…`, `claude-code-v…`).
