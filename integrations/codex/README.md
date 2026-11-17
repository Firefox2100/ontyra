# Ontyra for Codex

A Codex plugin: memory without the agent having to ask. Same behaviour as the Claude Code plugin,
packaged for Codex (`.codex-plugin/plugin.json`).

- **Hooks** (`hooks/ontyra-hook.sh`, curl + jq over REST): a briefing at session start (also after
  resume, clear and compaction) and recall on every prompt, injected as developer context. Fails
  open when the daemon is unreachable. Prompts and the assistant's last message are logged as
  episodes (`ONTYRA_LOG_EPISODES=0` to stop). Opt-in capture nudge: `ONTYRA_CAPTURE=1`.
- **MCP** (`.mcp.json`): the daemon's streamable-HTTP endpoint, caller identified as `codex`.
- **Skill** (`skills/ontyra-memory`): when and how to `remember`, scope, concepts, ontology changes.

## Requirements

A running Ontyra daemon with MCP on (`ONTYRA_MCP__ENABLED=true`), by default at
`http://127.0.0.1:8787`; `curl` and `jq` on the PATH. No authentication yet: loopback only.
Another address: edit `url` in `.mcp.json` and export `ONTYRA_URL` for the hooks.

## Install

```bash
git clone <this repository> && cd ontyra
codex plugin marketplace add .          # reads .agents/plugins/marketplace.json
codex plugin add ontyra@ontyra
```

Start a new Codex session, then run `/hooks` and **trust** the three Ontyra hooks (Codex skips
hooks it has not been shown; an update to the script asks again). `/mcp` should list `ontyra`.

Remove: `codex plugin remove ontyra@ontyra`, `codex plugin marketplace remove ontyra`.

## Check it works

1. In a session, ask "what do you remember about this project?": the agent should call
   `mcp__ontyra__recall` or answer from the injected briefing.
2. `curl -s localhost:8787/v1/proposals` after asking it to remember something: the proposal is
   there, pending (agents are held for review; approve in the operator console `/ui/review`).
3. Hook by hand:
   `echo '{"hook_event_name":"UserPromptSubmit","prompt":"hi","cwd":"/tmp","session_id":"s"}' | ONTYRA_HARNESS=codex hooks/ontyra-hook.sh`

`hooks/ontyra-hook.sh` and `skills/ontyra-memory/SKILL.md` are copies of the Claude plugin's and
the shared skill (a unit test checks they are identical); the harness is chosen by `ONTYRA_HARNESS`
in `hooks/hooks.json`.

Verified against Codex CLI 0.164 documentation; not yet exercised in a live Codex session.
