/**
 * The Ontyra tool surface, as data: what each tool is called, what it tells the model, and the
 * one REST call it makes. Descriptions are the daemon's own (mcp-tools-*.json), so a harness
 * reading these tools learns the same rules as one talking to the MCP endpoint.
 *
 * Nothing here touches the network: `request()` returns the call to make, and the Host plugin
 * performs it. That keeps the shapes testable with `node --test` alone.
 */

/**
 * Where the agent is working, as `/v1/recall` wants it: the URIs that anchor it, the concepts
 * the environment shows, and the session the daemon keeps its delivery ledger under.
 * @typedef {{ uris: string[], signals: Array<{ concept: string, value?: string }>, session: string | null }} RecallContext
 */

/**
 * One REST call produced for a tool invocation.
 * @typedef {{ method: 'GET' | 'POST', path: string, body?: unknown }} Call
 */

/** Strip the `memory:` table prefix a model may copy from an id it was shown. */
function idOf(value) {
  return String(value ?? '').replace(/^memory:/, '')
}

/**
 * A record reference as the daemon's request bodies want it: `table:id`, with the table supplied
 * when the model gave only the id. URL paths instead take the bare id (see `idOf`).
 * @param {unknown} value - the id as the model gave it.
 * @param {string} table - the table to name when the value carries none.
 * @returns {string} `table:id`.
 */
function recordRef(value, table) {
  const text = String(value ?? '')
  return text.includes(':') ? text : `${table}:${text}`
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
      + 'relevance bar. `rendered` is ready to read. Call before starting a task.',
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
      'Before an action with side effects: the directives, prerequisites and lessons that apply '
      + 'to this intent, plus every applicable `must` directive.',
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
        enum: ['applies_here', 'supersession_chain', 'absence', 'conflicts', 'changes_since', 'prerequisites_of', 'explain'],
        description:
          'Which structural question: applies_here / supersession_chain / prerequisites_of need '
          + '`memory`; absence needs `topic`; changes_since needs `since`; explain needs `trace`',
      },
      memory: { type: 'string', description: 'Memory id (`memory:abc` or `abc`)' },
      topic: { type: 'string', description: 'Concept label, anchor or kind; used by absence' },
      since: { type: 'string', description: 'ISO instant; used by changes_since' },
      trace: { type: 'string', description: 'Recall trace id; used by explain' },
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
        trace: args.trace === undefined ? null : recordRef(args.trace, 'trace'),
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
      'Write to memory. Send a proposal of ops (`add`, `revise`, `supersede`, `retract`, `link`, '
      + '`annotate`); it is validated, then committed or held for review. Side effects: a proposal '
      + 'is recorded; if the policy commits it, memories are added or superseded or retracted '
      + '(history is kept). Read the findings: warnings may point at a duplicate you should '
      + 'supersede. Memories go to your own space; `space: global` writes to the shared memory and '
      + 'needs permission.',
    parameters: {
      ops: {
        type: 'json',
        required: true,
        description:
          'The operations: add, revise, supersede, retract, link, annotate. Every `fields` object '
          + 'needs `text` (one-line summary) plus its kind\'s own fields',
      },
      note: { type: 'string', description: 'Why this proposal, in one line' },
      space: { type: 'string', description: '`own` (default) or `global`' },
      idempotency_key: { type: 'string', description: 'Retry the same proposal safely with the same key' },
    },
    request: (args) => ({
      method: 'POST',
      path: '/v1/proposals',
      body: {
        ops: args.ops,
        note: args.note ?? null,
        space: args.space ?? 'own',
        idempotency_key: args.idempotency_key ?? null,
      },
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
    description:
      'Propose an ontology change: `define` a concept, `relate` two, `add_alias`, `merge` '
      + 'duplicates, or `deprecate`. Held for review unless a human makes it. Check `concepts` '
      + 'first; read `advice` for similar existing concepts.',
    parameters: {
      change: { type: 'json', required: true, description: 'One change: define, relate, add_alias, merge or deprecate' },
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
 * The tools one profile registers.
 * @param {'lite' | 'full'} profile - configured tool profile.
 * @returns {typeof TOOLS} the tools to register.
 */
export function toolsFor(profile) {
  return profile === 'full' ? TOOLS : TOOLS.filter((tool) => !FULL_ONLY.has(tool.name))
}
