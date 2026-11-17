---
title: Review and policy
---

## Policy

Every proposal passes the **policy**, configured by environment variables:

- `ONTYRA_POLICY__DEFAULT`: `review` (default), `auto_commit` or `reject`.
- `ONTYRA_POLICY__RULES`: a JSON list of rules, first match wins. A rule applies only if every
  operation in the proposal fits it.

Solo use on your own machine: `ONTYRA_POLICY__DEFAULT=auto_commit`.

Agents plus a human owner (what the Docker setup uses): hold agent writes, commit your own.

```bash
ONTYRA_POLICY__DEFAULT=review
ONTYRA_POLICY__RULES='[{"actor_kinds":["human"],"decision":"auto_commit"}]'
```

Who counts as a human comes from the `X-Ontyra-Actor-Kind` header, which is only a label unless you
turn on accounts: with [multi-user mode](./authentication.md) it comes from the credential. Without
it anyone who can reach the port can claim it, so keep the daemon on loopback.

## The operator console

With `ONTYRA_UI__ENABLED=true`, open `http://127.0.0.1:8787/ui/`:

- **Overview**: counts of waiting proposals and ontology changes, and active memories per kind.
- **Review**: each held proposal with its operations, findings and raw JSON, and approve and reject
  buttons. The same for ontology changes.
- **Memories**: browse by kind and status; open one for its fields, revision history, links and
  evidence.
- **Concepts**: look up a concept with its aliases and relations.
- **Recall playground**: try a recall or briefing with a query, locations and signals, and see why
  each memory was returned.

The console acts as a human named `ONTYRA_UI__ACTOR` (default `owner`) and has no login unless a
token is required (then `/ui/login`). It
refuses form posts that come from another website, but it is meant for loopback only.

## Over the API

```bash
curl -s localhost:8787/v1/proposals                      # pending proposals
curl -s -X POST localhost:8787/v1/proposals/ID/approve \
  -H 'X-Ontyra-Actor: me' -H 'X-Ontyra-Actor-Kind: human'
curl -s localhost:8787/v1/ontology/changes               # pending ontology changes
```

`ID` is the part after `proposal:`. Approval re-validates against the current data, so a proposal
that has gone stale is rejected rather than applied.
