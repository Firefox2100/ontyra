/**
 * The hook equivalence for DeepSeek Harness: what `SessionStart`, `UserPromptSubmit` and
 * `Stop` do in the Claude Code and Codex plugins, expressed as a pre-step listener.
 *
 * A step's request is assembled from the prompt that opened it plus any pending input, so the
 * briefing belongs to the first step of a turn and the recall to the step that carries a fresh
 * human prompt. Injected text is a durable `user` message attributed to this plugin
 * (`source.kind === 'ontyra'`), which keeps the request append-only and lets the projection
 * observe that a briefing was actually delivered rather than assume it was.
 *
 * Delivery is best effort by design: an unreachable daemon, a timeout or an empty result leaves
 * the step exactly as it was. Memory is context, never a precondition.
 */

import { createUserMessage } from '@deepseek-ai/dsh-llm'
import { recallContext } from './context.js'
import {
  applyEvent,
  briefingCycle,
  CAPTURE,
  captureDue,
  capturePrompt,
  claimTurn,
  humanPrompt,
  initialState,
  isSubagent,
  needsChangesNote,
  PLUGIN,
  PROJECTION_KEY,
  render,
  unclaimedTurn,
} from './inject-core.js'

/**
 * Structural stand-in for the projection's Zod schema: the registry only ever calls `parse`,
 * and a per-session counter of this shape needs no validation.
 */
const stateSchema = { parse: (value) => value }

/**
 * Ask the daemon for rendered text.
 * @param {{ call(call: any, signal: AbortSignal): Promise<any> }} client - daemon client.
 * @param {string} path - REST path below `baseUrl`.
 * @param {Record<string, unknown>} body - request body.
 * @param {AbortSignal} signal - the step's signal.
 * @param {number} timeoutMs - the configured budget for this call.
 * @returns {Promise<string | undefined>} the rendered text, or undefined when there is none.
 */
async function rendered(client, path, body, signal, timeoutMs) {
  try {
    const timeout = AbortSignal.timeout(Math.max(1, Math.trunc(Number(timeoutMs)) || 1500))
    const reply = await client.call({ method: 'POST', path, body }, AbortSignal.any([signal, timeout]))
    if (reply?.silent === true || typeof reply?.rendered !== 'string') return undefined
    const text = reply.rendered.trim()
    if (text === '') return undefined
    // a recall's id goes with its memories, so a verdict on one can cite it
    return path === '/v1/recall' && typeof reply.trace === 'string'
      ? `${text}\n(recall trace: ${reply.trace})`
      : text
  } catch {
    return undefined
  }
}

/**
 * Track, per session and per compaction cycle, which briefing was delivered.
 * @param {import('@deepseek-ai/cordis').Context} ctx - the plugin context.
 * @returns {Promise<{ stateOf(session: any): { compactions: number, briefings: number, needsBrief: boolean } }>}
 *   the registered projection's handle.
 */
function trackBriefing(ctx) {
  return ctx.sessionProjections.register({
    key: PROJECTION_KEY,
    stateVersion: 2,
    stateSchema,
    init: initialState,
    apply: applyEvent,
  })
}

/**
 * Append one episode for the session ledger, if the daemon is being told about them.
 * @param {{ call(call: any, signal: AbortSignal): Promise<any> }} client - daemon client.
 * @param {any} settings - the live settings.
 * @param {any} session - the session the episode belongs to.
 * @param {'user' | 'assistant'} role - who spoke.
 * @param {string} content - what they said.
 * @param {AbortSignal} signal - the step's or turn's signal.
 * @returns {Promise<void>} when the episode is recorded or accepted as lost.
 */
async function episode(client, settings, session, role, content, signal) {
  if (settings.episodes !== true || content === '') return
  try {
    await client.call({
      method: 'POST',
      path: '/v1/episodes',
      body: {
        session: String(session.id),
        role,
        content,
        meta: { harness: settings.harness },
      },
    }, AbortSignal.any([signal, AbortSignal.timeout(Math.max(1, Number(settings.timeoutMs) || 1500))]))
  } catch {
    // The episode log is history, not a precondition: a daemon that is down costs one line.
  }
}

/**
 * Register the pre-step listener for the lifetime of `ctx`.
 * @param {import('@deepseek-ai/cordis').Context} ctx - the plugin context.
 * @param {object} deps - the plugin's collaborators.
 * @param {() => any} deps.settingsNow - the live settings.
 * @param {{ call(call: any, signal: AbortSignal): Promise<any> }} deps.client - daemon client.
 * @param {(session: any) => string | undefined} deps.cwdOf - the session's working directory.
 * @returns {Promise<void>} when the projection is registered and the listeners are mounted.
 */
export async function registerInjection(ctx, { settingsNow, client, cwdOf }) {
  const projection = await trackBriefing(ctx)
  /**
   * Turn numbers are per session, so both dedupe sets are keyed by session id: a plugin-global
   * "last turn" would let a second session's turn be skipped, and a global cycle set would grow
   * without bound.
   * @type {Map<string, number>} the turn whose human prompt was already recalled
   */
  const recalledTurn = new Map()
  /** @type {Map<string, Set<string>>} briefing cycles already asked for, empty answer included */
  const attempted = new Map()
  const forget = (sessionId) => {
    recalledTurn.delete(sessionId)
    attempted.delete(sessionId)
  }
  // Sessions come and go; the two maps only hold what an in-flight session needs.
  ctx.on('agent/disposed', (payload) => {
    const id = payload?.agent?.session?.id ?? payload?.session?.id
    if (id !== undefined) forget(String(id))
  })

  ctx.on('agent/pre-step', async ({ agent, turn, signal }, next) => {
    const decision = await next()
    if (decision.kind === 'reject' || signal.aborted) return decision

    const settings = settingsNow()
    if (settings.briefing !== true && settings.recall !== true) return decision

    const session = agent.session
    const sessionId = String(session.id)
    if (isSubagent(session.header) && settings.subagents !== true) return decision
    const state = projection.stateOf(session)
    const context = recallContext({ cwd: cwdOf(session), session: session.id })
    const budget = Number(settings.budgetTokens) || 1200
    /** @type {Array<{ name: string, text: string }>} */
    const parts = []

    const cycle = briefingCycle(sessionId, state.compactions)
    const cycles = attempted.get(sessionId) ?? new Set()
    attempted.set(sessionId, cycles)
    if (settings.briefing === true && state.needsBrief === true && !cycles.has(cycle)) {
      cycles.add(cycle)
      const briefing = await rendered(client, '/v1/briefing', {
        context,
        query: null,
        redeliver: state.briefings > 0,
        budget_tokens: budget,
        profile: 'compact',
      }, signal, settings.timeoutMs)
      if (briefing !== undefined) parts.push({ name: 'briefing', text: briefing })
      try {
        const timeout = AbortSignal.timeout(Math.max(1, Math.trunc(Number(settings.timeoutMs)) || 1500))
        const waiting = await client.call(
          { method: 'GET', path: '/v1/proposals?limit=50' },
          AbortSignal.any([signal, timeout]),
        )
        const note = needsChangesNote(waiting)
        if (note !== undefined) parts.push({ name: 'proposals', text: note })
      } catch {
        // The note is a courtesy: a slow or absent daemon costs nothing.
      }
    }

    // A step's batch carries a human prompt only when one just arrived — the opening of a turn, or
    // a prompt queued while the previous turn ran. Later steps carry tool results instead.
    const query = unclaimedTurn(recalledTurn, sessionId, turn) ? humanPrompt(decision.messages) : undefined
    if (query !== undefined) claimTurn(recalledTurn, sessionId, turn)
    if (query !== undefined) {
      await episode(client, settings, session, 'user', query, signal)
    }
    if (settings.recall === true && query !== undefined) {
      const recall = await rendered(client, '/v1/recall', {
        context,
        query,
        redeliver: false,
        budget_tokens: budget,
        profile: 'compact',
      }, signal, settings.timeoutMs)
      if (recall !== undefined) parts.push({ name: 'recall', text: recall })
    }

    if (settings.capture !== false && query !== undefined && captureDue(state, settings.captureEvery, query)) {
      parts.push({ name: CAPTURE, text: capturePrompt() })
    }

    if (parts.length === 0) return decision
    const text = render(parts)
    return {
      ...decision,
      messages: [
        ...decision.messages,
        createUserMessage({
          content: [{ type: 'text', text }],
          source: {
            kind: PLUGIN,
            form: 'snapshot',
            sections: parts.map(({ name, text: part }) => ({ name, text: part })),
          },
        }),
      ],
    }
  })

  // What the `Stop` hook does: keep the turn's reply, and keep the turn's reply as an episode.
  ctx.on('agent/turn-stopping', async ({ agent, signal }) => {
    const settings = settingsNow()
    if (settings.episodes !== true) return
    const session = agent.session
    // Gate here too: a subagent's transcript belongs to its parent, so recording it would flood
    // the ledger with work the parent already reports.
    if (isSubagent(session.header) && settings.subagents !== true) return
    const state = projection.stateOf(session)
    if (state.lastAssistant !== null) {
      await episode(client, settings, session, 'assistant', state.lastAssistant, signal)
    }
  })
}
