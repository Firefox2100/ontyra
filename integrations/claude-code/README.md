# Ontyra for Claude Code

A Claude Code plugin: memory without the agent having to ask.

- **Hooks** (`hooks/ontyra-hook.sh`, curl + jq over the REST API): a briefing at session
  start (redelivered after a compaction or /clear; a resumed session only gets what is new) and recall on every prompt, injected as context.
  Fails open when the daemon is unreachable. Location is the working directory and the git
  root, anchored as `file://<machine>/<path>` with the machine folding the same way the DeepSeek
  Harness plugin does (lowercased hostname): a memory written under one harness is therefore
  found by the others on that machine, and not on another one.
- **Episodes**: every prompt and the assistant's last message (Stop hook) are logged to
  `/v1/episodes`, the log that a memory's `quote` evidence is matched against; episodes no memory cites expire after
  14 days (`ONTYRA_LOG_EPISODES=0` to stop).
- **Capture reminder** (on; `ONTYRA_CAPTURE=0` turns it off): a one-line reminder to `remember` what
  was settled is added to the context already injected with a prompt, when the prompt reads like a
  rule, preference, correction or decision, and every `ONTYRA_CAPTURE_EVERY` prompts (default 5).
  It also asks for a `feedback` verdict on a recalled memory that clearly helped or misled (the recall
  trace id is added to the injected memories so the verdict can cite it). It costs no extra turn or
  model call: the agent acts in the same turn, or does nothing.
  Proposals follow the daemon's policy (default: held for review).
- **MCP** (`.mcp.json`): the `lite` tool profile (`recall`, `check`, `briefing`, `ask`, `get`,
  `remember`, `consult`, `check_source`, `feedback`, `pending_proposals`, `amend`) over streamable
  HTTP, so the agent can query and propose memories itself.

Needs the daemon running with `ONTYRA_MCP__ENABLED=true` (`uv run ontyra serve`), `curl` and
`jq` (the hook says so on stderr and continues without memory if `jq` is missing). `ONTYRA_URL` overrides `http://127.0.0.1:8787`. Authentication is optional: set `ONTYRA_TOKEN` to the daemon's token when it requires one
(single-user token or an account's credential; see the docs, Authentication).

A daemon on another machine works the same way: point `ONTYRA_URL` (hooks) and `url` in
`.mcp.json` (MCP) at it, and export `ONTYRA_TOKEN`. Recall there costs the round trip — a couple of
seconds on a LAN or VPN — which the hook's 8 s budget absorbs.

Try it without installing: `claude --plugin-dir integrations/claude-code`.
Test the hook by hand: `echo '{"hook_event_name":"UserPromptSubmit","prompt":"hi","cwd":"/tmp","session_id":"s"}' | hooks/ontyra-hook.sh`.

## Skill

`skills/ontyra-memory/SKILL.md` teaches the agent when and how to `remember`, scope what it
writes, look up concepts and propose ontology changes, and use `check` and `consult`. For ontology
tools and review (`propose_ontology_change`, `approve_ontology_change`…) use the `full` profile:
change the `url` in `.mcp.json` to `…/mcp/` with the daemon started with
`ONTYRA_MCP__PROFILE=full`.

## Using it with the deployed daemon (`~/Software/ontyra`, port 8787)

Start (or check) the daemon: `cd ~/Software/ontyra && docker compose up -d`.

One session, nothing installed: `claude --plugin-dir ~/workspace/ontyra/integrations/claude-code`
(`--continue` or `--resume <id>` re-opens an earlier conversation with the plugin loaded).

Permanently: in Claude Code run
`/plugin marketplace add ~/workspace/ontyra/integrations` then `/plugin install ontyra@ontyra`
(then `/reload-plugins`). Capture is on by default; `ONTYRA_CAPTURE=0` in the environment Claude
Code starts in turns it off for good.

Review what the agent proposes: `curl -s localhost:8787/v1/proposals` and
`curl -s localhost:8787/v1/ontology/changes`; approve with
`curl -XPOST localhost:8787/v1/proposals/<id>/approve -H 'X-Ontyra-Actor: me' -H 'X-Ontyra-Actor-Kind: human'`
(the plugin's own identity is `agent`, so it cannot approve for itself).
