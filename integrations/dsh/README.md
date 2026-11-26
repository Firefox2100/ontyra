# Ontyra memory for DeepSeek Harness

The DSH half of Ontyra: the same memory daemon the Claude Code and Codex plugins talk to, wired
into the harness as a plugin instead of a set of shell hooks.

What the model gets:

- a **briefing** injected at the start of a session and again after context compaction — what
  applies at this location, at this time;
- **recall** injected once per turn, on the first prompt that turn carries, when something clears
  the relevance bar;
- the **memory tools** (`ontyra_recall`, `ontyra_check`, `ontyra_briefing`, `ontyra_ask`,
  `ontyra_get`, `ontyra_remember`, `ontyra_feedback`, `ontyra_consult`, `ontyra_check_source`,
  `ontyra_pending_proposals`, `ontyra_amend`, and with the `full` profile `ontyra_concepts`,
  `ontyra_propose_ontology_change`) as native harness tools, not MCP tools;
- **episodes** (on by default; prompt and answer sent to the daemon as session history) and, also on
  by default, a capture nudge that asks the model to record what it learned.

It never changes what the daemon stores: every write is still a proposal that the daemon's policy
accepts, holds or rejects.

## Install

The daemon has to be reachable first; see the repository README for `uv run ontyra serve`. Then, in
DSH:

```
plugin_manager install_bundle target=<repository>/integrations/dsh
```

The bundle inserts one Loader row (`ontyra`) whose settings namespace is `ontyra`. After an edit to
this package, reload it with `set_bundle enabled=false` followed by `enabled=true` — the Host does
not watch the manifest of an installed bundle.

The plugin row's title and description come from `locale/<language>.json`; its icon comes from
`package.json`'s `icon` field — a path relative to this directory, SVG/PNG/JPEG/WebP, at most
256 KiB, and it must stay inside the package. Ship the file and set the field in the same change:
a declared icon that is missing or too large leaves the row readable but icon-less, with the
failure reported as an error next to the plugin.

## Settings

Settings → **Ontyra memory**. Every field is a stored preference, editable at runtime; a change
applies on the next step, or on the next tool call. Nothing needs the bundle reloaded.

| Setting | Default | Meaning |
|---|---|---|
| `baseUrl` | `http://127.0.0.1:8787` | Where the Ontyra REST API listens. |
| `tokenRef` | `ONTYRA_TOKEN` | Name of the credential that holds the bearer token. |
| `harness` | `dsh` | Sent as `X-Ontyra-Harness`; also the actor name. |
| `briefing` | `true` | Inject the briefing at session start and after compaction. |
| `recall` | `true` | Inject recall once per turn, on its first prompt. |
| `budgetTokens` | `1200` | Token budget per rendered pack. |
| `timeoutMs` | `1500` | How long a briefing or recall may hold up a step before it is skipped. |
| `toolTimeoutMs` | `20000` | How long a tool call may wait. A daemon on another host needs this: a recall there takes about two seconds, which the injection budget would cut off. |
| `tools` | `true` | Register the memory tools. |
| `toolsProfile` | `lite` | Dropdown: `lite`, or `full` to add the ontology tools. |
| `episodes` | `true` | Record prompts and answers as session episodes (what a memory's `quote` evidence is matched against). |
| `capture` | `true` | Remind the model to record what was settled (rule-like prompts, and every few prompts); a line in the injected context, no extra turn. |
| `captureEvery` | `5` | Prompts between periodic capture reminders. |
| `subagents` | `false` | Also inject and record inside subagent sessions. |

The token itself is **not** a setting: the card writes it to the harness credential store under
`tokenRef`, so it never lands in the Cordis patch, and the daemon treats a missing token as
"unauthenticated", which is the right default for a loopback daemon.

The card speaks every language the harness ships — English, Chinese, Japanese, Spanish, French,
German, Russian and Portuguese — and a missing key falls back to the English string rather than
showing a raw key. The plugin row itself is localized the same way, from `locale/<language>.json`
(`meta.title`, `meta.description`); those files resolve through the `./locale/*.json` export, which
is what the harness reads them by.

## A daemon on another machine

Point `baseUrl` at it (for example `http://100.86.189.45:8787`) and put its bearer token in the
harness credential store under `tokenRef`; a daemon that needs authentication answers `401` without
one, which the tools report as an error rather than retrying. Two things differ from loopback:

- **latency.** A briefing is cheap, but a recall or a check runs the decider and takes about two
  seconds over the network. `timeoutMs` (1500, the injection budget) would cut that off, which is
  why tool calls have their own `toolTimeoutMs` (20000). Leave it generous; a call that hits the
  budget is reported as `Ontyra is unreachable at …: The operation was aborted due to timeout`.
- **concepts.** Signals are matched against the daemon's own ontology, so a remote daemon that
  never learned `windows` or `python` reports them under `unresolved` and simply uses less context.
  That is the intended answer, not a failure.

## Anchors name the machine

A path anchor means a path on **one** machine, and the daemon cannot tell which one it is: it fills
the host of a host-less `file:` URI with its *own* name, so on a shared daemon every device would
otherwise anchor `D:/work` to the same place — and a Windows workspace and a Linux one are not the
same place.

So the plugin says who it is: every call carries `X-Ontyra-Machine: <hostname, folded>`, and the
daemon records every path anchor of a proposal against it (`services/anchors.py: qualify`). A host
that was typed by hand, or copied out of another machine's memory, is replaced rather than stored;
anchors of other schemes (`https:`, `doi:`, `git:`) are untouched. The plugin also re-hosts what it
sends (`lib/context.js: hostOf`, `qualifyFileUri`), so an older daemon behaves the same way.

Memories written before that change carry the host-less form and stop matching; re-anchoring them
was a one-off data fix, not part of this package — one `revise` per memory through
`POST /v1/proposals` with the corrected `anchors` (a memory that already names a host is left
alone).

## Behaviour

Injection is a durable user-role message carrying a `ontyra` message source, wrapped in
`<ontyra-context trust="reference-only">`. The wrapper is deliberate: memory entries are reference
data about the project, not instructions from the user, and a poisoned or stale entry must not be
able to redirect the session. The message is append-only, so the harness's prompt cache survives,
and it is re-sent only after compaction.

Everything the plugin sends is best-effort: an unreachable daemon, a timeout or a rejected request
logs nothing into the transcript and never blocks a turn. The capture
reminder is a line in the context injected before a step, so it costs no extra turn.

## Layout

```
package.json          bundle metadata: cordis patch, client half, engines, keywords
cordis.patch.yml      the one Loader row this bundle inserts
locale/               the plugin row's title and description, one file per language
lib/index.js          Host half: Config, tool registration, skill, wiring
lib/tools.js          the tool surface, mapped onto REST calls
lib/ontyra.js         REST client: headers, timeouts, error text
lib/context.js        cwd, machine-qualified anchors and project signals for a call
lib/settings.js       defaults, and reading volatile fields as plain values
lib/inject.js         pre-step injection (with the capture reminder) and turn-stopping episodes
lib/inject-core.js    the pure parts of the above (renderer, projection, counters)
lib/skill.js          the bundled skill's frontmatter
lib/client.js         browser half: the settings card, in eight languages
skills/ontyra-memory/ the skill the Host registers (when it has a skill registry)
scripts/doctor.mjs    read-only check: daemon alive, Host serves the browser half
test/                 node --test suites for the contract, injection, skill, card and metadata
```

## Checking an install

`node integrations/dsh/scripts/doctor.mjs --gui http://127.0.0.1:<port>` answers "is this actually
working here?" without opening the UI. It asks the daemon for `/health` and an empty recall, reads
the Host's client module graph from `/plugins/events`, and fetches the browser half the Host reports.
The daemon defaults to `http://127.0.0.1:8787` (`--daemon` or `ONTYRA_URL`); the GUI port changes on
every start, so `--gui` or `DSH_GUI_URL` is required for the two Host checks, which are skipped
otherwise. A daemon that requires authentication needs `--token` (or `ONTYRA_TOKEN`), otherwise the
recall check reports `answered 401` with that hint. It exits non-zero if any check that ran failed.

A passing run looks like:

```
ok    daemon health  —  http://127.0.0.1:8787 0.1.dev11+g946959e7a
ok    daemon recall  —  answered 200
ok    Host module graph  —  dsh-ontyra-memory at rev <hash>
ok    browser half  —  <n> bytes, registers the settings section
```

If the first two pass but the module graph does not name `dsh-ontyra-memory`, the bundle is installed
but not enabled, or the profile was not restarted after `package.json` last changed.

## Tests

```
node --test integrations/dsh/test/*.test.js
```

The contract suite compares every tool description against
`integrations/contract/mcp-tools-lite.json`, so a change to the daemon's tool surface fails here
until this package is updated to match. The skill suite checks that every tool the skill names is
one this plugin registers. The metadata suite checks the eight `locale/` files and the manifest
fields the Plugins page reads. The context suite pins which host a path anchor gets, including the
Windows two-slash form where the drive letter looks like a host. The remaining suites cover the
injection rules and the settings card, and need no browser and no daemon.

## Troubleshooting

- **No Ontyra section in Settings.** The bundle has to be installed *and* enabled; after an install
  or a change to `package.json` the profile must be restarted, because the Host does not watch an
  installed bundle's manifest. Disabling and re-enabling the bundle reloads the Host half only.
- **The section is there but empty.** The card reports its own state: `loading` means the settings
  mirror has not answered yet, `unavailable` means the Host does not serve the `ontyra` namespace
  (the Loader row is missing or disabled, and the message names the namespaces that *are* served),
  and a red message with `Ontyra settings failed to render` is a bug worth reporting with the
  console text. Nothing at all inside the section means the browser half was never loaded — check
  that `package.json` declares `dsh.client` and `exports["./client"]`, then reload the window, and
  restart the profile only if that does not help (the browser half is resolved from the manifest
  when the client module graph is built).
- **The card shows no inputs although the section renders.** It renders from the `settings.describe`
  mirror, not from the shared `ConfigForm`: that form stays `loading` for good when the Host's
  serialized schema cannot be rehydrated or the resolved value fails it, which used to leave the
  section blank. Reads take the mirror's redacted JSON; writes still go through the form.
- **Tools are missing.** `tools` is off in the settings, or the plugin failed to apply — the Host
  log names the entry. Tools are registered once and filtered per step, so a `tools` or
  `toolsProfile` change shows up on the next step without a reload; a failed apply needs one.
- **Tools return `Ontyra is unreachable at …`.** Nothing is listening on `baseUrl`; start the daemon
  or fix the URL in the settings card. Injection stays silent by design — the session simply has no
  memory context.
- **`ontyra_propose_ontology_change` refuses the follow-up.** Ontology changes are held for review and
  only a human may approve or reject them; the tool proposes, a person decides.
- **Is the browser half loaded at all?** Ask the running Host instead of guessing: read one frame from
  `GET /plugins/events` (SSE) and look for an entry whose `id` is `dsh-ontyra-memory`, then fetch its
  `url` (`plugins/??dsh-ontyra-memory/client.js&rev=…`) — a 200 with JavaScript means the Host has the
  artifact, so only the open page is stale.
