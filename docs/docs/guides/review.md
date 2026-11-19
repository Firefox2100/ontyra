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

## Changing what is waiting

A proposal that is held for review is not frozen:

- **Amend**: its submitter (or a human reviewer) replaces its operations with a corrected list and
  says why. That is a new **version**; the older versions stay in the proposal's `history`, and
  approval names the version the reviewer saw, so an approval of a stale version is refused.
- **Withdraw**: its submitter or a reviewer takes it back (final).
- **Request changes**: a reviewer sends it back with a comment (and optional comments on single
  operations). It leaves the approve queue (`changes_requested`) until it is amended; the submitter
  sees the comments when it lists its pending proposals.
- **Per-operation decisions**: a reviewer may approve only some operations (the ones they need are
  included, and the rest are rejected with a reason) or reject some (and the operations that depend
  on them) while the rest keeps waiting as a new version. Operations depend on each other when one
  names the `ref` of another, for example a `supersede` of a memory added in the same proposal.
- **Waiting proposals are visible to new writes**: when an agent proposes something that nearly
  duplicates a waiting proposal, or changes a memory another waiting proposal also changes, the
  result carries a `pending_similar` or `pending_conflict` finding naming that proposal ("amend it
  instead"). Waiting proposals never affect recall.

```bash
curl -s -X POST localhost:8787/v1/proposals/ID/amend -H 'Content-Type: application/json' \
  -d '{"ops": [...], "why": "named the repo", "expect_version": 1}'
curl -s -X POST localhost:8787/v1/proposals/ID/request-changes -H 'Content-Type: application/json' \
  -H 'X-Ontyra-Actor-Kind: human' -d '{"comment": "too vague", "op_comments": {"0": "which repo?"}}'
curl -s -X POST localhost:8787/v1/proposals/ID/approve -H 'Content-Type: application/json' \
  -H 'X-Ontyra-Actor-Kind: human' -d '{"ops": [0, 2], "reason": "the second is a style opinion"}'
```

Over MCP, agents have `pending_proposals` and `amend` (both profiles) and `withdraw` (full);
reviewing tools (`approve`, `reject`, `request_changes`) are in the full profile and need a human
credential. In the console, a waiting proposal has per-operation checkboxes (approve or reject the
selected ones; what they need or what needs them is included), a version history with a diff between
versions, a request-changes form and, on the review queue, a **bulk edit** that gives the same scope,
expiry, anchor or concepts to the add and revise operations of several proposals as a new version each.
Waiting ontology changes can be amended, withdrawn or sent back the same way (see
[The ontology](./ontology.md)).

## Over the API

```bash
curl -s localhost:8787/v1/proposals                      # pending proposals
curl -s -X POST localhost:8787/v1/proposals/ID/approve \
  -H 'X-Ontyra-Actor: me' -H 'X-Ontyra-Actor-Kind: human'
curl -s localhost:8787/v1/ontology/changes               # pending ontology changes
```

`ID` is the part after `proposal:`. Approval re-validates against the current data, so a proposal
that has gone stale is rejected rather than applied.
