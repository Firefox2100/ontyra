# Ontyra for Claude Code

A Claude Code plugin: memory without the agent having to ask.

- **Hooks** (`hooks/ontyra-hook.sh`, curl + jq over the REST API): a briefing at session
  start (redelivered after a compaction or /clear; a resumed session only gets what is new) and recall on every prompt, injected as context.
  Fails open when the daemon is unreachable. Location is the working directory and the git
  root (`file://host/path`); the daemon derives containment.
- **Episodes**: every prompt and the assistant's last message (Stop hook) are logged to
  `/v1/episodes`, the lossless log proposals cite as evidence (`ONTYRA_LOG_EPISODES=0` to stop).
- **Capture prompt** (opt-in, `ONTYRA_CAPTURE=1`): at most once per `ONTYRA_CAPTURE_EVERY`
  prompts (default 10) the Stop hook keeps the agent going once, asking it to `remember` anything
  durable it learned. Never loops (`stop_hook_active`). Proposals follow the daemon's policy
  (default: held for review).
- **MCP** (`.mcp.json`): the `lite` tool profile (`recall`, `check`, `briefing`, `ask`, `get`,
  `remember`) over streamable HTTP, so the agent can query and propose memories itself.

Needs the daemon running with `ONTYRA_MCP__ENABLED=true` (`uv run ontyra serve`), `curl` and
`jq`. `ONTYRA_URL` overrides `http://127.0.0.1:8787`. Authentication is optional: set `ONTYRA_TOKEN` to the daemon's token when it requires one
(single-user token or an account's credential; see the docs, Authentication).

Try it without installing: `claude --plugin-dir integrations/claude-code`.
Test the hook by hand: `echo '{"hook_event_name":"UserPromptSubmit","prompt":"hi","cwd":"/tmp","session_id":"s"}' | hooks/ontyra-hook.sh`.

## Skill

`skills/ontyra-memory/SKILL.md` teaches the agent when and how to `remember`, scope what it
writes, look up concepts and propose ontology changes, and use `check`. For ontology tools and
review (`propose_ontology_change`, `approve_ontology_change`…) use the `full` profile: change the
`url` in `.mcp.json` to `…/mcp/` with the daemon started with `ONTYRA_MCP__PROFILE=full`.

## Using it with the deployed daemon (`~/Software/ontyra`, port 8787)

Start (or check) the daemon: `cd ~/Software/ontyra && docker compose up -d`.

One session, nothing installed: `ONTYRA_CAPTURE=1 claude --plugin-dir ~/workspace/ontyra/integrations/claude-code`
(`--continue` or `--resume <id>` re-opens an earlier conversation with the plugin loaded).

Permanently: in Claude Code run
`/plugin marketplace add ~/workspace/ontyra/integrations` then `/plugin install ontyra@ontyra`
(then `/reload-plugins`). Capture is opt-in per session via `ONTYRA_CAPTURE=1` in the environment
Claude Code starts in (add it to your shell profile to make it permanent).

Review what the agent proposes: `curl -s localhost:8787/v1/proposals` and
`curl -s localhost:8787/v1/ontology/changes`; approve with
`curl -XPOST localhost:8787/v1/proposals/<id>/approve -H 'X-Ontyra-Actor: me' -H 'X-Ontyra-Actor-Kind: human'`
(the plugin's own identity is `agent`, so it cannot approve for itself).
