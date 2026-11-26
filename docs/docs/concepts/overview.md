---
title: Concepts
---

## Memory kinds

Every memory has a kind with its own fields, so rules are not mixed up with observations.

| Kind | What it holds |
|---|---|
| `fact` | a statement about a subject (`subject`, `predicate`, `object`) |
| `directive` | a rule: `strength` (`must`, `should`, `prefer`), `polarity` (`do`, `avoid`), `rule`, `rationale` |
| `decision` | what was chosen, the alternatives, and why |
| `event` | something that happened, with time and outcome; its relevance decays |
| `procedure` | steps to follow when a trigger occurs |
| `task` | something to do, with a due date and state |
| `lesson` | a situation, the insight and what to do instead |

All kinds also carry `text` (the readable statement), `confidence`, optional expiry, and where and
when they apply (below).

## Proposals and review

Agents never write memories directly. They send a **proposal**: a list of operations (`add`,
`revise`, `supersede`, `retract`, `link`, `annotate`). The daemon validates the whole proposal
(fields fit the kind, referenced memories exist, a quoted source really contains the quote),
then a **policy** decides: commit, hold for review, or reject. Held proposals wait for a human in
the operator console or over the API. See [Review and policy](../guides/review.md).

Revisions are immutable. Correcting a memory creates a new revision; replacing one supersedes it.
Facts are superseded, not decayed. History stays available.

## Where a memory applies

- Anchors tie a memory to a place: a URI such as `file://host/path/to/repo`, `ssh://user@host`,
  or a repository. A path covers everything beneath it (`subtree`) or only itself (`exact`).
- Scope: `universal` memories hold everywhere and are always part of a briefing. `contextual`
  ones (the default) reach the agent only with evidence they apply here: a matching anchor, a
  satisfied qualifier, or a concept the current situation touches. A rule that is neither universal
  nor tied to anything is *unscoped*, and the daemon warns when you write one. `topical` memories
  (knowledge of one module or project) reach the agent *only* through a tie to what the work is
  about, however well their text matches: an anchor, a concept of a [registered
  resource](../guides/ontology.md) the work happens in, or a concept (or alias) the message names.
  A topical memory must say what it is `about`. `reference` memories are never delivered by recall,
  briefing or check at all: they are documentation and notes taken from sources, looked up with
  `consult` (or `search`). A memory that cites a source is `reference` unless it says otherwise;
  a short topical overview memory ("there are notes on X: look them up") tells the agent they exist.
- Qualifiers are conditions ("only when python is 3.12 or newer") and can be necessary,
  sufficient, a prerequisite, an exclusion, or a trigger.

## Ontology

Concepts (`python`, `docker`, `localai`) and relations between them (`is_a`, `part_of`, `same_as`,
`replaced_by`, `depends_on`, `disjoint_with`) let a memory about one thing be found from a related
one, and give conditions a vocabulary. Agents propose ontology changes (define, relate, add an
alias, edit, split, merge, deprecate); a human approves them unless the operation is configured
for automatic approval. See [The ontology](../guides/ontology.md).

## Recall

- Briefing: at session start, the complete set of rules that apply here, plus facts and
  decisions anchored here and anything due.
- Recall: on each message, memories relevant to the text and the context. Lexical and
  embedding search find candidates; applicability filters them; scores decide; the result is
  trimmed to a token budget. If the best match is not close enough, nothing is returned.
- Check: before an action with side effects, what must be known first.
- A **session ledger** stops the same memory being delivered twice in a session, and sends a
  one-time correction if a delivered memory was later revised or retracted.

## Optional model services

The daemon calls only these external services, all optional: an **embedding** model, a
**reranker**, and a **decision model** (a small model that answers which of the top candidates
matters for the situation, with probabilities and no generated text). They never change what is
stored; an unreachable one is skipped.
