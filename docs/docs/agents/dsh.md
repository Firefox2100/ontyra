---
title: DSH
---

The DSH plugin connects DeepSeek Harness to Ontyra through REST. It injects a briefing at session
start and after compaction, recalls memory before each step, records episodes, and registers native
`ontyra_*` tools. If the daemon is unavailable, the plugin leaves the turn alone.

## Requirements

Start an Ontyra daemon first. The default plugin address is `http://127.0.0.1:8787`.

## Install

Install the bundle from a checkout through DSH's plugin manager:

```text
plugin_manager install_bundle target=/path/to/ontyra/integrations/dsh
```

The bundle adds one Loader entry named `ontyra`. Restart the profile after installation or after a
change to `package.json`. Disabling and re-enabling the bundle reloads ordinary Host-side changes.

Open **Settings -> Ontyra memory** to configure it. The useful settings are:

| Setting | Default | Purpose |
|---|---|---|
| `baseUrl` | `http://127.0.0.1:8787` | Ontyra REST endpoint |
| `tokenRef` | `ONTYRA_TOKEN` | DSH credential containing the bearer token |
| `briefing` | `true` | Inject a session briefing |
| `recall` | `true` | Recall before the first prompt of each step |
| `toolsProfile` | `lite` | Use `full` to add ontology tools |
| `episodes` | `true` | Record prompts and answers for quote evidence |
| `capture` | `true` | Prompt the model to record durable conclusions |
| `subagents` | `false` | Also inject and record in subagent sessions |

The token is stored in DSH's credential store, not in the Cordis patch. A missing token is valid
for an open loopback daemon.

## What the model receives

Injected memory is a user-role message with the `ontyra` source, wrapped in
`<ontyra-context trust="reference-only">`. The wrapper marks stored memory as reference data rather
than a fresh user instruction. Injection is append-only so DSH can retain its prompt cache.

The lite tool profile includes recall, checks, briefings, structured questions, reads, proposals,
feedback, pending proposals and amendments. The full profile adds concept and ontology-change
tools. Every write still passes through Ontyra's validation and review policy.

## Check the installation

From the repository root, run:

```bash
node integrations/dsh/scripts/doctor.mjs
```

The doctor checks daemon health, an empty recall, DSH's loaded module graph and the browser-side
settings module. Use `--gui` and `--daemon` when either service runs at a different address.

For package-level tests:

```bash
cd integrations/dsh
node --test test/*.test.js
```

The plugin's own [README](https://github.com/Firefox2100/ontyra/tree/main/integrations/dsh) has the
complete settings, troubleshooting notes and package layout.
