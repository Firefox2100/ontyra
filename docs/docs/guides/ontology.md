---
title: The ontology
---

The ontology is the vocabulary memories are about: **concepts** (`python`, `ubuntu`, `ci pipeline`)
with aliases and typed relations (`is_a`, `part_of`, `same_as`, `alternative_to`, `depends_on`,
`disjoint_with`, ...). Memories point at concept ids (`about`, qualifiers); recall expands a
mention through the relations, so a memory about `ubuntu` answers a question about `linux`.
Nothing here is advice: advice lives in memories.

## Changing it

Agents **propose** changes (`propose_ontology_change`, `POST /v1/ontology/changes`); a human's own
change applies at once, an agent's waits for review. Every change is applied as one atomic batch.

| change | what it does |
|---|---|
| `define` | a new concept (id = slug of its label, never reused) |
| `relate` / `unrelate` | add a relation / end one (the edge is kept as history) |
| `edit` | a clearer label, a tighter definition, other aliases; the old label becomes an alias |
| `reparent` | replace one broader concept by another, atomically, with a loop check |
| `merge` | `source` becomes an alias of `into` and is deprecated |
| `split` | one concept turned out to be several: new concepts are created (or existing ones named) |
| `deprecate` | retire a concept, with or without a successor, and a reason |
| `restore` | put a concept back to an earlier version (also how a revert works) |

**Edits must not change what a concept means.** If the meaning changes, define a new concept and
deprecate the old one. The daemon cannot check this; a reviewer decides, and a rewritten definition
that shares little with the old one gets an advice line.

**Preview first**: `POST /v1/ontology/changes/preview` (MCP `preview_ontology_change`, full profile; the
console always previews) lists what a change would do: the concepts touched, how many live memories
are about them, and advice. Nothing is written.

## Versions, tombstones and what stays visible

- Each edit writes a new immutable **version** of the concept. Versions never appear in lookups,
  recall, search or lists; only the investigation tools show them: `GET /v1/concepts/ID/history`,
  `GET /v1/ontology/changes?pending=false&concept=ID` (MCP `concept_history`, `ontology_history`,
  full profile) and the console's concept page.
- A **deprecated concept is never deleted**: it stays as a tombstone with `replaced_by` (one
  successor) or `split_into` (several). Ids are not reused.
- *Using* a deprecated concept is steered: lookups and lists hide it (a split concept stands for its
  successors); a new memory that names it is moved to the successor with a warning, or refused when
  it was split (choose a successor) or has none.
- *Representing* existing data is unaffected: memories keep the ids they were written with and are
  still found (recall expands a replaced or split concept to its successors). The derived search
  index is tidied by the scheduled maintenance; a split also queues optional refinement tasks
  (`review_concept_drift`, capped by `ONTYRA_TASKS__REFINE_CAP`) asking which successor a memory is
  really about. Revisions are never rewritten.

## Revert

`POST /v1/ontology/changes/ID/revert` (human, global write; MCP `revert_ontology_change`) applies the
change's stored undo as one new change. It is refused while a later change touched the same
concepts: revert those first. A revert can itself be reverted.

## Health check

`GET /v1/ontology/changes/check` (MCP `check_ontology`, console **Concepts**, then **Health check**) is a
read-only report: loops in `is_a`/`part_of`, links to missing concepts, replacement loops, names used
by two concepts, deprecated concepts that live memories still use, unused concepts. Each finding names
the change that most likely caused it. Nothing is repaired automatically.

## Review of waiting changes

A waiting ontology change can be amended (a new version), withdrawn by its proposer, or sent back by a
reviewer with a comment (`changes_requested`); approval can name the version the reviewer saw. A
second proposal that defines a similar concept, or touches the same one, gets a `pending_similar` or
`pending_conflict` advice line. See [Review and policy](./review.md).

## In the console

**Concepts** lists concepts (tick *show deprecated* for tombstones) and links to a concept page with
its relations, memories, versions and the changes that touched it. Edit forms always show a preview
before applying. **Change history** lists applied changes with a revert button; the **Review** page
shows what each waiting change would do.
