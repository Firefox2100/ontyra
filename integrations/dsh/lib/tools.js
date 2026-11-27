/**
 * The Ontyra tool surface, as data: what each tool is called, what it tells the model, and the
 * one REST call it makes. Descriptions are the daemon's own (mcp-tools-*.json), so a harness
 * reading these tools learns the same rules as one talking to the MCP endpoint.
 *
 * Nothing here touches the network: `request()` returns the call to make, and the Host plugin
 * performs it. That keeps the shapes testable with `node --test` alone.
 */

import { qualifyOps } from './context.js'

/**
 * Where the agent is working, as `/v1/recall` wants it: the URIs that anchor it, the concepts
 * the environment shows, and the session the daemon keeps its delivery ledger under.
 * @typedef {{ uris: string[], signals: Array<{ concept: string, value?: string }>, session: string | null }} RecallContext
 */

/**
 * One REST call produced for a tool invocation.
 * @typedef {{ method: 'GET' | 'POST', path: string, body?: unknown }} Call
 */

/**
 * The bare id the daemon's URL paths want: a model copies `memory:abc` or `proposal:abc` from
 * what it was shown, and the path holds only `abc`. Any table prefix is stripped, not just the
 * one the tool happened to expect — a wrong prefix is more likely a copy slip than a mistake
 * worth passing through into a doubled `proposal:proposal:abc`.
 * @param {unknown} value - the id as the model gave it.
 * @returns {string} the id with no `table:` prefix.
 */
function idOf(value) {
  const text = String(value ?? '').trim()
  const cut = text.indexOf(':')
  return cut === -1 ? text : text.slice(cut + 1)
}

/**
 * A record reference as the daemon's request bodies want it: `table:id`, named by this tool
 * rather than by whatever prefix the model typed. A body field means one table (`memory`,
 * `recall_trace`), so the tool's table always wins: a recalled trace id pasted as `trace:abc`,
 * or as `abc`, becomes `recall_trace:abc`, which is the only id the daemon can look up.
 * @param {unknown} value - the id as the model gave it.
 * @param {string} table - the table this field holds.
 * @returns {string} `table:id`.
 */
function recordRef(value, table) {
  return `${table}:${idOf(value)}`
}

/**
 * Merge the plugin's own context with whatever the model passed: the model's parts win, because
 * only it knows what it is looking at right now.
 * @param {Record<string, unknown>} args - validated tool arguments.
 * @param {RecallContext} context - the environment the plugin observed.
 * @returns {Record<string, unknown>} the `context` body field.
 */
function withContext(args, context) {
  const given = contextArg(args.context)
  if (given === undefined) return { ...context }
  return { ...context, ...given }
}

/**
 * Read the `context` argument, which models sometimes send as a JSON string, and whose `signals`
 * the daemon wants as `{concept}` objects even though a bare label reads naturally.
 * @param {unknown} value - the argument as received.
 * @returns {Record<string, unknown> | undefined} the object, or undefined when none was given.
 */
export function contextArg(value) {
  if (value === undefined || value === null) return undefined
  let given = value
  if (typeof given === 'string') {
    try {
      given = JSON.parse(given)
    } catch {
      return undefined
    }
  }
  if (given === null || typeof given !== 'object') return undefined
  const context = { .../** @type {Record<string, unknown>} */ (given) }
  if (Array.isArray(context.signals)) {
    context.signals = context.signals
      .map((signal) => (typeof signal === 'string' ? { concept: signal } : signal))
      .filter((signal) => signal !== null && typeof signal === 'object')
  }
  return context
}

/** A text renderer that prefers the daemon's ready-to-read pack, and falls back to its JSON. */
function renderedText(value) {
  const rendered = /** @type {{ rendered?: unknown }} */ (value)?.rendered
  if (typeof rendered === 'string' && rendered.length > 0) return rendered
  return JSON.stringify(value, null, 2)
}

/** Default renderer: the JSON reply, which for these tools is already the answer. */
function jsonText(value) {
  return JSON.stringify(value, null, 2)
}

/**
 * Every tool the plugin can register, in the daemon's profile order.
 * @type {Array<{ name: string, title: string, description: string, parameters: Record<string, unknown>, request(args: Record<string, any>, context: RecallContext): Call, render(value: unknown): string }>}
 */
export const TOOLS = [
  {
    name: 'ontyra_recall',
    title: 'Recall memories',
    description:
      'Memories relevant to a message in a context. Empty (`silent`) when nothing clears the '
      + 'relevance bar. `rendered` is ready to read. Call before starting a task unless relevant '
      + 'memory is already in your context. When the message depends on earlier turns, make `query` '
      + 'standalone: name the subject instead of "it" or "that".',
    parameters: {
      query: { type: 'string', description: 'The message, question or task to recall for' },
      context: { type: 'json', description: 'Where the agent is working; omit to use the observed environment' },
      budget_tokens: { type: 'integer', description: 'Token budget for the rendered pack' },
      redeliver: { type: 'boolean', description: 'Ask again for what this session already received' },
    },
    request: (args, context) => ({
      method: 'POST',
      path: '/v1/recall',
      body: {
        context: withContext(args, context),
        query: args.query ?? null,
        redeliver: args.redeliver ?? false,
        budget_tokens: args.budget_tokens ?? null,
        profile: 'compact',
      },
    }),
    render: renderedText,
  },
  {
    name: 'ontyra_check',
    title: 'Check before acting',
    description:
      'Before an action with side effects (delete, push, deploy, migrate): the directives, '
      + 'prerequisites and lessons that apply to this intent, plus every applicable `must` directive. '
      + 'Not needed before writing memory.',
    parameters: {
      intent: { type: 'string', required: true, description: 'What you are about to do' },
      context: { type: 'json', description: 'Where the agent is working; omit to use the observed environment' },
    },
    request: (args, context) => ({
      method: 'POST',
      path: '/v1/check',
      body: { intent: args.intent, context: withContext(args, context) },
    }),
    render: renderedText,
  },
  {
    name: 'ontyra_briefing',
    title: 'Session briefing',
    description:
      'Everything that applies at this location, without a question: use at session start, on a '
      + 'project change, or after context compaction (`redeliver`).',
    parameters: {
      context: { type: 'json', description: 'Where the agent is working; omit to use the observed environment' },
      budget_tokens: { type: 'integer', description: 'Token budget for the rendered pack' },
      redeliver: { type: 'boolean', description: 'Ask again for what this session already received' },
    },
    request: (args, context) => ({
      method: 'POST',
      path: '/v1/briefing',
      body: {
        context: withContext(args, context),
        query: null,
        redeliver: args.redeliver ?? false,
        budget_tokens: args.budget_tokens ?? null,
        profile: 'compact',
      },
    }),
    render: renderedText,
  },
  {
    name: 'ontyra_ask',
    title: 'Ask a structural question',
    description: 'Structural questions answered exactly (what applies here, history…).',
    parameters: {
      question: {
        type: 'string',
        required: true,
        enum: ['applies_here', 'supersession_chain', 'absence', 'conflicts', 'changes_since', 'as_of', 'prerequisites_of', 'explain'],
        description:
          'Which structural question: applies_here / supersession_chain / prerequisites_of need '
          + '`memory`; absence needs `topic`; changes_since needs `since`; as_of needs `at` (what was in force then; `topic` narrows); explain needs `trace`',
      },
      memory: { type: 'string', description: 'Memory id (`memory:abc` or `abc`)' },
      topic: { type: 'string', description: 'Concept label, anchor or kind; used by absence' },
      since: { type: 'string', description: 'ISO instant; used by changes_since' },
      at: { type: 'string', description: 'ISO instant; used by as_of' },
      trace: { type: 'string', description: 'Recall trace id, `recall_trace:abc` or `abc`; used by explain' },
      context: { type: 'json', description: 'Where the agent is working; omit to use the observed environment' },
    },
    request: (args, context) => ({
      method: 'POST',
      path: '/v1/ask',
      body: {
        question: args.question,
        memory: args.memory === undefined ? null : recordRef(args.memory, 'memory'),
        topic: args.topic ?? null,
        since: args.since ?? null,
        at: args.at ?? null,
        trace: args.trace === undefined ? null : recordRef(args.trace, 'recall_trace'),
        context: withContext(args, context),
      },
    }),
    render: jsonText,
  },
  {
    name: 'ontyra_get',
    title: 'Get a memory',
    description:
      'One memory by id (`memory:abc` or `abc`), optionally with `expand`: history, links, '
      + 'evidence, applies.',
    parameters: {
      memory: { type: 'string', required: true, description: 'Memory id, with or without the `memory:` prefix' },
      expand: {
        type: 'array',
        items: { type: 'string', enum: ['history', 'links', 'evidence', 'applies'] },
        description: 'What to expand the memory with',
      },
    },
    request: (args) => {
      const expand = /** @type {string[] | undefined} */ (args.expand) ?? []
      const query = expand.map((part) => `expand=${encodeURIComponent(part)}`).join('&')
      return {
        method: 'GET',
        path: `/v1/memories/${encodeURIComponent(idOf(args.memory))}/detail${query ? `?${query}` : ''}`,
      }
    },
    render: jsonText,
  },
  {
    name: 'ontyra_remember',
    title: 'Remember (propose a memory change)',
    description:
      'Store, change or retire a memory by sending a proposal of ops. To store something new use op '
      + '`add` (a `kind` and its `fields`; the schema lists them). `revise`, `supersede` and '
      + '`retract` (and `link`, `annotate` in the full profile) act on memories that already exist: '
      + 'use ids from recall or an earlier result, never an invented one. Cite what the user said as '
      + '`evidence: [{"quote": ...}]`. The proposal is validated, then committed or held for review: '
      + 'say it is saved only when `status` is `committed` (`pending` means a human must approve). '
      + 'Read the findings: a duplicate warning usually means supersede; an unscoped warning means '
      + '`revise` that memory, not add it again. Side effects: a proposal is recorded; if the policy '
      + 'commits it, memories are added or superseded or retracted (history is kept). Memories go to '
      + 'your own space; `space: global` writes to the shared memory and needs permission.',
    parameters: {
      ops: {
        type: 'json',
        required: true,
        description:
          'The operations. `add` stores something NEW (`kind` and `fields`); `revise`, `supersede` and '
          + '`retract` act on memories that already exist, by id from recall or an earlier result. For `add`, `fields` takes `text` (the whole statement, in one sentence) '
          + 'plus the kind\'s own fields, required first, optional in brackets:\n'
          + 'fact (a standing statement about the world or the setup): text, subject (concept id, e.g. concept:python), predicate, object (text or a concept id)\n'
          + 'directive (a rule for how you should behave; say where it applies: `scope: universal`, or anchors / about): text, strength (must|should|prefer), rule [polarity (do|avoid), rationale]\n'
          + 'decision (a choice that was made, with what it was chosen over): text, chosen, rationale, decided_at (ISO datetime) [alternatives (list of str)]\n'
          + 'event (something that happened at a particular time): text, occurred_at (ISO datetime) [participants (list of str), outcome]\n'
          + 'procedure (ordered steps to follow: ONE memory for the whole procedure): text, trigger, steps (list of {text, optional}) [preconditions (list of str)]\n'
          + 'task (something to do later (a reminder)): text [due (ISO datetime), state (open|done|cancelled)]\n'
          + 'lesson (what a failure taught, to apply next time): text, situation, insight [do_instead]\n'
          + 'Every kind also takes: scope (contextual|universal), anchors, about (concept ids), '
          + 'qualifiers, expires_at, confidence. Do not repeat `kind` inside `fields`. Cite what the '
          + 'user said as `evidence: [{"quote": ...}]` on the op.',
      },
      note: { type: 'string', description: 'Why this proposal, in one line' },
      space: { type: 'string', description: '`own` (default) or `global`' },
      idempotency_key: { type: 'string', description: 'Retry the same proposal safely with the same key' },
      redaction: {
        type: 'string',
        description:
          '`auto` (default) masks values that look like credentials before storing and says so '
          + 'in the findings; `off` stores a false positive as sent (a test fixture, a '
          + 'documented example) — never a real key',
      },
    },
    request: (args) => ({
      method: 'POST',
      path: '/v1/proposals',
      body: {
        ops: qualifyOps(args.ops),
        note: args.note ?? null,
        space: args.space ?? 'own',
        idempotency_key: args.idempotency_key ?? null,
        redaction: args.redaction === 'off' ? 'off' : 'auto',
      },
    }),
    render: jsonText,
  },
  {
    name: 'ontyra_feedback',
    title: 'Give feedback on a memory',
    description:
      'Say that a recalled memory was `helpful`, `applied`, `irrelevant`, `missed` (it should have '
      + 'come back), `outdated` or `wrong`. Give the `query` it came back for and, when shown, the '
      + 'recall `trace`. Side effect: the verdict is stored. `outdated` and `wrong` also queue a '
      + 'maintenance task for a human or agent to retract, revise or confirm the memory; the memory '
      + 'itself is not changed.',
    parameters: {
      memory: { type: 'string', required: true, description: 'Memory id, with or without the `memory:` prefix' },
      query: { type: 'string', required: true, description: 'The query the memory came back (or should have) for' },
      label: {
        type: 'string',
        required: true,
        enum: ['applied', 'helpful', 'missed', 'irrelevant', 'outdated', 'wrong'],
        description: 'The verdict',
      },
      trace: { type: 'string', description: 'The recall trace shown with the injected memories, `recall_trace:abc` or `abc`' },
      note: { type: 'string', description: 'Why, in a few words' },
    },
    request: (args) => ({
      method: 'POST',
      path: '/v1/feedback',
      body: {
        memory: recordRef(args.memory, 'memory'),
        query: args.query,
        label: args.label,
        trace: args.trace ? recordRef(args.trace, 'recall_trace') : null,
        note: args.note ?? null,
      },
    }),
    render: jsonText,
  },
  {
    name: 'ontyra_consult',
    title: 'Consult reference material',
    description:
      'Look something up in documentation and notes taken from sources (datasheets, manuals, '
      + 'papers). Recall never delivers these by itself: when a memory says there is material on a '
      + 'topic and you need details, call this with a specific `query` and the `topic`. Every hit '
      + 'names its source and quote.',
    parameters: {
      query: { type: 'string', required: true, description: 'What to look up, specific' },
      topic: { type: 'string', description: 'A concept (label, alias or id); else what the context ties to' },
      limit: { type: 'integer', description: 'Most hits to return (default 8)' },
      context: { type: 'json', description: 'Where the agent is working; omit to use the observed environment' },
    },
    request: (args, context) => ({
      method: 'POST',
      path: '/v1/consult',
      body: {
        query: args.query,
        topic: args.topic ?? null,
        context: withContext(args, context),
        limit: args.limit ?? 8,
      },
    }),
    render: jsonText,
  },
  {
    name: 'ontyra_correct',
    title: 'Correct a memory',
    description:
      'The one call to fix a memory: `fields` overlay its current revision, so send only what '
      + 'changes. Use this instead of a hand-built `revise` when you know the id and the '
      + 'correction. The result is an ordinary proposal — read its `status` and findings.',
    parameters: {
      memory: {
        type: 'string',
        required: true,
        description: 'The memory to fix (`memory:abc` or `abc`)',
      },
      fields: {
        type: 'json',
        required: true,
        description: 'Only what changes: `text`, and any of the kind\'s own fields',
      },
      reason: { type: 'string', description: 'Why it is wrong, kept with the revision' },
    },
    request: (args) => ({
      method: 'POST',
      path: `/v1/memories/${encodeURIComponent(idOf(args.memory))}/correct`,
      body: {
        fields: typeof args.fields === 'string' ? JSON.parse(args.fields) : args.fields ?? {},
        reason: args.reason ?? null,
      },
    }),
    render: jsonText,
  },
  {
    name: 'ontyra_forget',
    title: 'Forget memories',
    description:
      'Retire memories: give `memory` for one, or `uri` for everything anchored under that path '
      + '(the project or files it applies to). Nothing is deleted — each target is retracted '
      + 'with your reason, so the history stays. Read `status`: `committed` means gone from '
      + 'recall now, `pending` means a human must approve.',
    parameters: {
      memory: { type: 'string', description: 'The one memory to retire (`memory:abc` or `abc`)' },
      uri: {
        type: 'string',
        description: 'Retire everything anchored under this path (subtree)',
      },
      reason: { type: 'string', required: true, description: 'Why it should go; kept with each retraction' },
      limit: { type: 'integer', description: 'Most memories to retire at once (default 50)' },
    },
    request: (args) => ({
      method: 'POST',
      path: '/v1/memories/forget',
      body: {
        memory: args.memory ? recordRef(args.memory, 'memory') : null,
        uri: args.uri ?? null,
        reason: args.reason,
        limit: args.limit ?? 50,
      },
    }),
    render: jsonText,
  },
  {
    name: 'ontyra_check_source',
    title: 'Report on a cited source',
    description:
      'After reading a source that a memory cites (a file, page, paper), say what you found: '
      + '`ok` (as cited), `changed` or `missing`. The daemon never reads sources itself. Side '
      + 'effect: the report is stored; `changed` and `missing` open a review task for each memory '
      + 'that cites it. The memories themselves are not changed.',
    parameters: {
      uri: { type: 'string', required: true, description: 'The source as cited (file path URI, URL, doi:…)' },
      state: {
        type: 'string',
        required: true,
        enum: ['ok', 'changed', 'missing'],
        description: 'What you found',
      },
      content_hash: { type: 'string', description: 'Hash of the source as you read it' },
      note: { type: 'string', description: 'What changed, in a few words' },
    },
    request: (args) => ({
      method: 'POST',
      path: '/v1/sources/check',
      body: {
        uri: args.uri,
        state: args.state,
        content_hash: args.content_hash ?? null,
        note: args.note ?? null,
      },
    }),
    render: jsonText,
  },
  {
    name: 'ontyra_pending_proposals',
    title: 'Pending proposals',
    description: "Your proposals (and, with permission, the global ones) that are still waiting: `pending`, or `changes_requested` with the reviewer's `comments`. Check it before proposing something that may already be waiting, and when the user says a proposal needs changes. Each has a `version`, its ops and findings.",
    parameters: {
      limit: { type: 'number', description: 'How many to list (default 50)' },
    },
    request: (args) => ({
      method: 'GET',
      path: `/v1/proposals?limit=${encodeURIComponent(String(args.limit ?? 50))}`,
    }),
    render: jsonText,
    // DSH-only: a replayable presentation hint, so a UI can show the waiting proposals as cards
    // instead of parsing the text. Other tools have no such seam, so this one carries it alone.
    meta: (value) => ({
      card: 'ontyra-proposals',
      proposals: (Array.isArray(value) ? value : []).slice(0, 20).map((proposal) => ({
        id: String(proposal?.id ?? ''),
        status: String(proposal?.status ?? ''),
        note: String(proposal?.note ?? ''),
        ops: Array.isArray(proposal?.ops) ? proposal.ops.length : 0,
        version: proposal?.version ?? null,
      })),
    }),
  },
  {
    name: 'ontyra_amend',
    title: 'Amend a waiting proposal',
    description: "Replace the ops of a proposal that is still waiting (`pending` or `changes_requested`) with a corrected list and say why; it becomes a new version and goes back to review. Use this instead of sending a second proposal when a `pending_similar` finding names it, or when a reviewer asked for changes. Pass `expect_version` (from `pending_proposals`).",
    parameters: {
      proposal: { type: 'string', required: true, description: 'Proposal id, with or without the `proposal:` prefix' },
      request: {
        type: 'json',
        required: true,
        description:
          'The amendment: `ops` (the whole corrected list, it replaces the old one), `why` (what you '
          + 'changed and why) and, from ontyra_pending_proposals, `expect_version`',
      },
    },
    request: (args) => ({
      method: 'POST',
      path: `/v1/proposals/${encodeURIComponent(idOf(args.proposal))}/amend`,
      body: args.request,
    }),
    render: jsonText,
  },
  {
    name: 'ontyra_concepts',
    title: 'Look up concepts',
    description:
      'Find concepts by label or alias (`python`, `ssh`) to get the ids that `about`, anchors and '
      + 'qualifiers refer to, with their relations. Empty: no such concept; propose one with '
      + '`propose_ontology_change` rather than guessing an id.',
    parameters: {
      term: { type: 'string', required: true, description: 'Label or alias to resolve' },
    },
    request: (args) => ({
      method: 'GET',
      path: `/v1/concepts?term=${encodeURIComponent(String(args.term))}`,
    }),
    render: jsonText,
  },
  {
    name: 'ontyra_propose_ontology_change',
    title: 'Propose an ontology change',
    description: "Propose an ontology change: `define` a concept, `relate` two, `add_alias`, `merge` duplicates, `deprecate`, or `edit` a concept's label, definition or aliases (say the same thing better; if the meaning changes, define a new concept and deprecate the old one instead). A concept never changes id; a deprecated one stays and points to its successor. Held for review unless a human makes it. Check `concepts` first; read `advice` for similar existing concepts and for an edit that looks like a different meaning.",
    parameters: {
      change: { type: 'json', required: true, description: 'One change: define, relate, add_alias, merge, deprecate or edit' },
      rationale: { type: 'string', required: true, description: 'Why: what you saw that makes this change right' },
    },
    request: (args) => ({
      method: 'POST',
      path: '/v1/ontology/changes',
      body: { change: args.change, rationale: args.rationale },
    }),
    render: jsonText,
  },
]

/** Tools only the `full` profile exposes (they reach the shared ontology). */
export const FULL_ONLY = new Set(['ontyra_concepts', 'ontyra_propose_ontology_change'])

/**
 * The tools only this harness has, because they exist to use a seam the others do not: an approval
 * needs a person, and only DSH can put a card in front of one. They have no daemon counterpart, so
 * they stay out of `TOOLS` — the contract mirror and its tests describe the daemon's surface.
 */
export const LOCAL_ONLY = new Set(['ontyra_review'])

/**
 * Whether this step should offer a tool, given the live settings. The plugin registers every tool
 * once and asks this per step: registering a subset would freeze the `tools` switch and the
 * profile picker until the next reload, and the settings card promises otherwise.
 * @param {{ tools?: boolean, toolsProfile?: string }} settings - the live settings.
 * @param {string} name - the tool's registered name.
 * @returns {boolean} whether the tool participates in the step.
 */
export function toolVisible(settings, name) {
  if (settings.tools !== true) return false
  return settings.toolsProfile === 'full' || !FULL_ONLY.has(name)
}

/**
 * The tools one profile registers.
 * @param {'lite' | 'full'} profile - configured tool profile.
 * @returns {typeof TOOLS} the tools to register.
 */
export function toolsFor(profile) {
  return profile === 'full' ? TOOLS : TOOLS.filter((tool) => !FULL_ONLY.has(tool.name))
}
