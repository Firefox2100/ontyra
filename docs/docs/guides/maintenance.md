---
title: Maintenance tasks
---

Some work needs judgment the daemon will not exercise itself: are these two memories the same, is
the newer one current? The daemon finds such cases deterministically ([Finding
duplicates](./duplicates.md)) and queues them as **tasks**. A host agent (or you) claims a task,
resolves it with an ordinary proposal, and completes it with a validated result. Nothing is ever
merged by the daemon.

## The flow

1. **Detect**: `POST /v1/tasks/detect` (MCP `detect_tasks`). Each finding is queued once, keyed by
   the revisions it concerns, so running it again adds nothing, and a decision that two memories are
   `distinct` is not asked again. A task whose memories were changed or retracted since is closed as
   obsolete.
2. **Look**: `GET /v1/tasks` (MCP `pending_tasks`; `kind` and `status` filter). Each task has a
   **card**: instructions, the memories involved, and the choices.
3. **Claim**: `POST /v1/tasks/{id}/claim` so two agents do not do the same work (`/release` gives
   it back).
4. **Resolve**: with a normal proposal, such as a `supersede` of the duplicates (reason `merged`)
   through `remember`. It goes through the usual validation and review policy, so an agent's merge
   waits for you like any other agent write.
   Or let the daemon do it: `POST /v1/tasks/{id}/resolve` (MCP `resolve_task`, or the **Merge**
   button in the console) submits the proposal the card describes, as you, and completes the task.
   A proposal that waits for review keeps the task *claimed* until it is decided; if it is
   rejected the next detection puts the task back in the queue.
5. **Complete**: `POST /v1/tasks/{id}/complete` with a decision:

| Decision | Meaning | Needs |
|---|---|---|
| `merge` | duplicates were consolidated (duplicate clusters) | the proposal that did it |
| `supersede` | the newer memory replaced the older (updates) | the proposal that did it |
| `retract` | the memory is no longer true (reported or expiring) | the proposal that did it |
| `revise` | the memory was corrected or extended | the proposal that did it |
| `distinct` | two memories differ in a way that matters; not raised again | nothing |
| `keep` | the memory stands; not raised again for that revision | nothing |
| `defer` | park it for later | nothing |

A decision the card does not offer, a missing proposal, or a proposal that is not yours or was
rejected is refused.

## Kinds

- `consolidate`: a cluster of duplicates.
- `review_contradiction`: the same statement with a changed value (an update).
- `review_feedback`: a memory someone reported `outdated` or `wrong`
  (`POST /v1/feedback`, or the MCP `feedback` tool). One task per revision collects every report; the
  report itself never changes the memory, a person or agent decides (`retract`, `revise` or `keep`).
- `expire_check`: a live memory whose `expires_at` has passed or falls within
  `ONTYRA_TASKS__EXPIRY_WARN_DAYS` (14) days, found by `detect`. Recall already ignores an expired
  memory; the task asks whether to extend it (`revise`), retract it, or let it lapse (`keep`).
- `review_stale_dependent`: a memory derived from one that was retracted (created by the
  retraction itself).

`review_proposal` and `review_ontology_change` tasks are created too; you decide those with
approve and reject, in the console or through their endpoints. They are listed only to someone who
could decide them, and cannot be claimed here.

## The console

`/ui/tasks` lists the open tasks with their memories side by side. **Look for new work** runs the
detection; **Merge into the oldest** (or **The newer replaces the older**, or **Retract it**) resolves
it as you; **They differ** or **It stands** records the decision; **Later** parks it. To revise a
memory, open it from the task, then complete the task with the proposal.

## Who sees what

Tasks belong to the owner of the memories they concern. You see your own and the global ones, and
change them under the same rules as memory: your own, or global with the `global_write`
permission. A scan never mixes owners.

Claiming reads the status and then writes it, which assumes one daemon, like the ontology.
