---
title: Authentication and accounts
---

Ontyra has three ways to run, from simplest to most controlled. Whichever you pick, **the owner
of a memory is never a request parameter**: it is the global memory when accounts are off, and
the authenticated account when they are on.

| Mode | Settings | Who can call | Owner |
|---|---|---|---|
| Open (default) | nothing | anyone who can reach the port | the global memory |
| Single token | `ONTYRA_AUTH__TOKEN` | requests carrying that token | the global memory |
| Multi-user | `ONTYRA_AUTH__MULTI_USER=true` + `ONTYRA_AUTH__ADMIN_TOKEN` | requests carrying an account's credential | the account |

A token is sent as `Authorization: Bearer <token>`, on REST, on MCP and (as a sign-in) in the
console. `/health` and the API docs stay open.

:::caution Still not for the internet
Tokens travel in clear text over plain HTTP. Keep the daemon on loopback, or put it behind a
reverse proxy that terminates TLS.
:::

## Open and single-token mode

With no token anyone who can reach the port is trusted, so keep it on loopback. Callers still
label themselves with `X-Ontyra-Actor`, `X-Ontyra-Actor-Kind` and `X-Ontyra-Harness`, which the
daemon uses to apply the review policy (a `human` may auto-commit, an `agent` waits). These
labels are not authenticated.

To require a token (at least 16 characters):

```bash
export ONTYRA_AUTH__TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
```

Give clients the same value as `ONTYRA_TOKEN` (the Claude Code and Codex plugins send it when
it is set). There is exactly one token, and it opens everything.

## Multi-user mode

Each person has an **account**, which owns a private memory. An account holds any number of
**credentials**, one per thing that acts for them: themselves in the console, their Codex, their
Claude Code, a script. Each credential has its own token, its own name and a kind:

- `human`: the person. May review proposals, and commits at once if your policy lets humans.
- `agent`: an agent acting for the person. Its writes follow the policy for agents (by default,
  held for the person to approve).

What an account may see and do:

- It reads **its own memories and the global memory**, never anyone else's.
- It writes its own memory freely (policy permitting). Writing the **global memory** (and
  changing the shared ontology and resources) needs the `global_write` permission. To put a memory
  there, send `"space": "global"` on the proposal; without permission it is rejected.
- Proposals are reviewed by a human credential of the same account; global proposals by a human
  with `global_write`. An agent credential never reviews.
- `admin` manages accounts and credentials (and retrains the reranker). Only a human credential
  of an admin can use it.

Start the daemon with a bootstrap token for the built-in `admin` account:

```bash
export ONTYRA_AUTH__MULTI_USER=true
export ONTYRA_AUTH__ADMIN_TOKEN="a-long-random-secret"
uv run ontyra serve
```

Then create accounts and credentials over the API (`ADMIN` is that token). The token of a new
credential appears in the response **once**; only a hash is stored.

```bash
A="Authorization: Bearer $ADMIN"
curl -s -X POST localhost:8787/v1/admin/users -H "$A" -H 'content-type: application/json' \
  -d '{"name": "alice", "permissions": []}'
curl -s -X POST localhost:8787/v1/admin/users/alice/credentials -H "$A" \
  -H 'content-type: application/json' -d '{"name": "me", "kind": "human"}'
curl -s -X POST localhost:8787/v1/admin/users/alice/credentials -H "$A" \
  -H 'content-type: application/json' -d '{"name": "codex", "kind": "agent", "harness": "codex"}'
```

Other admin calls: `GET /v1/admin/users`, `PATCH /v1/admin/users/{name}` (permissions, `disabled`),
`GET /v1/admin/credentials`, `POST /v1/admin/credentials/{id}/revoke`. A revoked credential or a
disabled account is refused immediately. Change the admin token by changing the setting.

Give each client its own credential token as `ONTYRA_TOKEN`: the human one for the console, the
agent ones for the plugins.

In multi-user mode the `X-Ontyra-*` headers are ignored. The caller's name is `account.credential`
(for example `alice.codex`).

## The console

With any token required, `/ui/login` asks for a token and starts a cookie session (HttpOnly,
same-site strict, `ONTYRA_AUTH__UI_SESSION_HOURS`, default 12; a restart signs everyone out).
Only a human credential (or the single token) can sign in.

## MCP tools and permission prompts

Every MCP tool carries annotations that clients such as Claude Code and Codex use to decide
whether to ask first:

| Tool | Read-only | Destructive | Notes |
|---|---|---|---|
| `recall`, `check`, `briefing`, `ask`, `get`, `concepts`, `search`, `overview`, `neighbors`, `list_memories`, `pending_proposals`, `pending_ontology_changes` | yes | no | `recall` and `briefing` also note what they delivered in the session |
| `remember`, `propose_ontology_change` | no | no | records a proposal; memories change only if the policy commits it, and history is kept |
| `reject`, `reject_ontology_change` | no | no | closes a held item, commits nothing |
| `approve`, `approve_ontology_change` | no | yes | commits a held item, which can supersede or retract memories or change the shared ontology |

None of them reach outside the daemon (`openWorldHint` is false), and each has a plain-language
title.
