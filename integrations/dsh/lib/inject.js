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
 * @returns {Promise<{ text: string, delivered: string[] } | undefined>} the rendered text and the
 *   revisions it carries, or undefined when there is none.
 */
async function rendered(client, path, body, signal, timeoutMs) {
  try {
    const timeout = AbortSignal.timeout(Math.max(1, Math.trunc(Number(timeoutMs)) || 1500))
    const reply = await client.call({ method: 'POST', path, body }, AbortSignal.any([signal, timeout]))
    const delivered = Array.isArray(reply?.delivered) ? reply.delivered.map(String) : []
    if (reply?.silent === true || typeof reply?.rendered !== 'string') return undefined
    const text = reply.rendered.trim()
    if (text === '') return undefined
    // a recall's id goes with its memories, so a verdict on one can cite it
    return {
      text:
        path === '/v1/recall' && typeof reply.trace === 'string'
          ? `${text}\n(recall trace: ${reply.trace})`
          : text,
      delivered,
    }
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
 * The proposals still waiting, as `needsChangesNote` reads them.
 * @param {{ call(call: any, signal: AbortSignal): Promise<any> }} client - daemon client.
 * @param {AbortSignal} signal - the step's signal.
 * @param {number} timeoutMs - the configured budget for this call.
 * @returns {Promise<unknown>} the reply, or undefined when the daemon did not answer.
 */
async function waitingProposals(client, signal, timeoutMs) {
  try {
    const timeout = AbortSignal.timeout(Math.max(1, Math.trunc(Number(timeoutMs)) || 1500))
    return await client.call(
      { method: 'GET', path: '/v1/proposals?limit=50' },
      AbortSignal.any([signal, timeout]),
    )
  } catch {
    // The note is a courtesy: a slow or absent daemon costs nothing.
    return undefined
  }
}

/**
 * Register the pre-step listener for the lifetime of `ctx`.
 * @param {import('@deepseek-ai/cordis').Context} ctx - the plugin context.
 * @param {object} deps - the plugin's collaborators.
 * @param {() => any} deps.settingsNow - the live settings.
 * @param {{ call(call: any, signal: AbortSignal): Promise<any> }} deps.client - daemon client.
 * @param {(session: any) => string | undefined} deps.cwdOf - the session's working directory.
 * @param {((sessionId: string, parts: Array<{ name: string, text: string }>) => void)} [deps.remember]
 *   - keeps the last injected pack per session, for the settings card to show.
 * @returns {Promise<void>} when the projection is registered and the listeners are mounted.
 */
export async function registerInjection(ctx, { settingsNow, client, cwdOf, remember }) {
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
  /**
   * Revisions this session was already shown, echoed back as `seen`. The daemon already keeps a
   * ledger per session; this covers the case where the session id changes (fork, resume) and the
   * plugin has no durable record of what went into the transcript.
   * @type {Map<string, string[]>}
   */
  const seen = new Map()
  const seenFor = (sessionId) => seen.get(sessionId) ?? []
  const noteSeen = (sessionId, delivered) => {
    if (!Array.isArray(delivered) || delivered.length === 0) return
    const kept = [...seenFor(sessionId), ...delivered]
    // A cap, not a ledger: the daemon forgets what a session was shown anyway, and an unbounded
    // list would grow with every step of a long session.
    seen.set(sessionId, kept.slice(-200))
  }
  const forget = (sessionId) => {
    recalledTurn.delete(sessionId)
    attempted.delete(sessionId)
    seen.delete(sessionId)
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
    // The three deliveries are independent: a user who turns the briefing and the recall off
    // still asked for capture reminders, and a user who turns everything but episodes off still
    // wants the ledger kept.
    const delivering = settings.briefing === true || settings.recall === true || settings.capture !== false
    if (!delivering && settings.episodes !== true) return decision

    const session = agent.session
    const sessionId = String(session.id)
    // A subagent is a narrower reader: it gets the recall its own prompt needs, but not the
    // briefing (a child did not start a session the user opened), not the capture nudge (its
    // transcript belongs to the parent) and not an episode log of its own.
    const narrow = isSubagent(session.header) && settings.subagents !== true
    const state = projection.stateOf(session)
    const context = recallContext({ cwd: cwdOf(session), session: session.id })
    const budget = Number(settings.budgetTokens) || 1200
    /** @type {Array<{ name: string, text: string }>} */
    const parts = []

    const cycle = briefingCycle(sessionId, state.compactions)
    const cycles = attempted.get(sessionId) ?? new Set()
    attempted.set(sessionId, cycles)
    if (settings.briefing === true && !narrow && state.needsBrief === true && !cycles.has(cycle)) {
      cycles.add(cycle)
      // Both asks are about the same step and neither needs the other's answer, so a slow daemon
      // should cost one round trip, not two.
      const [briefing, waiting] = await Promise.all([
        rendered(client, '/v1/briefing', {
          context,
          query: null,
          redeliver: state.briefings > 0,
          budget_tokens: budget,
          profile: 'compact',
          seen: seenFor(sessionId),
        }, signal, settings.timeoutMs),
        waitingProposals(client, signal, settings.timeoutMs),
      ])
      if (briefing !== undefined) {
        parts.push({ name: 'briefing', text: briefing.text })
        noteSeen(sessionId, briefing.delivered)
      }
      const note = needsChangesNote(waiting)
      if (note !== undefined) parts.push({ name: 'proposals', text: note })
    }

    // A step's batch carries a human prompt only when one just arrived — the opening of a turn, or
    // a prompt queued while the previous turn ran. Later steps carry tool results instead.
    const query = unclaimedTurn(recalledTurn, sessionId, turn) ? humanPrompt(decision.messages) : undefined
    if (query !== undefined) claimTurn(recalledTurn, sessionId, turn)
    if (query !== undefined && !narrow) {
      // The episode log is history, not a precondition: the recall below must not wait on it.
      void episode(client, settings, session, 'user', query, signal)
    }
    if (settings.recall === true && query !== undefined) {
      const recall = await rendered(client, '/v1/recall', {
        context,
        query,
        redeliver: false,
        budget_tokens: budget,
        profile: 'compact',
        seen: seenFor(sessionId),
      }, signal, settings.timeoutMs)
      if (recall !== undefined) {
        parts.push({ name: 'recall', text: recall.text })
        noteSeen(sessionId, recall.delivered)
      }
    }

    if (!narrow && settings.capture !== false && query !== undefined && captureDue(state, settings.captureEvery, query)) {
      parts.push({ name: CAPTURE, text: capturePrompt() })
    }

    if (parts.length === 0) return decision
    const text = render(parts)
    // What the model was just handed, kept in memory for the settings card and the tab: a
    // debugging view ("what did it actually get?") that costs one map entry per session and is
    // never re-injected, so a stale copy can never masquerade as fresh context.
    remember?.(sessionId, parts)
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
      // The turn is closing: record the reply without making the close wait on the daemon.
      void episode(client, settings, session, 'assistant', state.lastAssistant, signal)
    }
  })
}
