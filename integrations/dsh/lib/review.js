/**
 * Approvals are where the harness can do better than a chat message: the daemon already keeps
 * proposals waiting, and DSH can ask the person who is here, in place, with the proposal in front
 * of them. This module turns pending proposals into one question each, then applies the answers.
 *
 * The three answers map to the daemon's own endpoints, so approving from a card is the same act as
 * approving from the web console: the proposal's version fence, the ops that were committed and
 * the audit trail are the daemon's, not ours.
 */
/** Keep one card from asking about the whole queue. */
export function bounded(value, fallback, max = 50) {
  const number = Number(value)
  if (!Number.isFinite(number) || number <= 0) return fallback
  return Math.min(Math.trunc(number), max)
}

/** The labels are answers, so they are matched literally. They are also the option labels. */
export const APPROVE = 'Approve'
export const REJECT = 'Reject'
export const REQUEST_CHANGES = 'Request changes'

/** What each answer posts, and with which body key. */
const ENDPOINT = {
  approve: { path: 'approve', extra: 'reason', body: {} },
  reject: { path: 'reject', extra: 'reason', body: {} },
  changes: { path: 'request-changes', extra: 'comment', body: {} },
}

/** One line of an op, short enough for a card and specific enough to judge. */
function opLine(op) {
  switch (op.op) {
    case 'add':
      return `add ${op.kind}: ${firstSentence(op.fields?.text)}`
    case 'revise':
      return `revise ${op.memory}: ${Object.keys(op.fields ?? {}).join(', ') || 'no fields'}`
    case 'supersede':
      return `supersede ${op.target} with ${op.new ?? 'a new revision'}`
    case 'retract':
      return `retract ${op.memory}: ${op.reason ?? ''}`.trim()
    case 'link':
      return `link ${op.source} ${op.relation ?? '→'} ${op.target}`
    case 'annotate':
      return `annotate ${op.target}: ${firstSentence(op.text)}`
    default:
      return String(op.op ?? 'op')
  }
}

/** The first sentence of a text, cut so a card stays readable. */
export function firstSentence(text, limit = 160) {
  const clean = String(text ?? '').replace(/\s+/g, ' ').trim()
  const end = clean.search(/[.!?](\s|$)/)
  const sentence = end === -1 ? clean : clean.slice(0, end + 1)
  return sentence.length <= limit ? sentence : `${sentence.slice(0, limit - 1).trimEnd()}…`
}

/** What a reviewer needs to decide: who asked, what the ops are, and why. */
export function describeProposal(proposal, limit = 6) {
  const ops = Array.isArray(proposal?.ops) ? proposal.ops : []
  const lines = ops.slice(0, limit).map((op) => `• ${opLine(op)}`)
  if (ops.length > limit) lines.push(`• …and ${ops.length - limit} more`)
  const why = proposal?.note ?? proposal?.reason
  return [...lines, '', why ? `Why: ${why}` : ''].filter((line) => line !== '').join('\n')
}

/**
 * One question per pending proposal.
 * @param {Array<any>} proposals - the daemon's pending proposals.
 * @returns {Array<{ id: string, question: string, detail: string, header: string, options: Array<{ label: string }> }>}
 */
export function questionsFor(proposals) {
  return (Array.isArray(proposals) ? proposals : []).map((proposal, index) => {
    const ops = Array.isArray(proposal?.ops) ? proposal.ops.length : 0
    return {
      id: `ontyra-proposal-${index}`,
      header: 'Ontyra memory',
      question: `Commit this memory change${ops === 1 ? '' : `s`} (${ops} op${ops === 1 ? '' : 's'})?`,
      detail: describeProposal(proposal),
      options: [{ label: APPROVE }, { label: REJECT }, { label: REQUEST_CHANGES }],
    }
  })
}

/**
 * Which decision an answer carries. Accepts one answer item or the batch it came in, so a caller
 * holding either shape needs no adapter.
 * @param {{ selected?: string[] } | Array<{ selected?: string[] }> | undefined} answers
 *   - the answer for one question.
 * @returns {'approve' | 'reject' | 'changes' | undefined} the decision, or undefined when unanswered.
 */
export function decisionOf(answers) {
  const item = Array.isArray(answers) ? answers[0] : answers
  const selected = Array.isArray(item?.selected) ? item.selected : []
  if (selected.includes(APPROVE)) return 'approve'
  if (selected.includes(REJECT)) return 'reject'
  if (selected.includes(REQUEST_CHANGES)) return 'changes'
  return undefined
}

/** A comment from the "other" box, if the person typed one. */
export function commentOf(answers) {
  const item = Array.isArray(answers) ? answers[0] : answers
  const custom = item?.custom
  return typeof custom === 'string' && custom.trim() !== '' ? custom.trim() : undefined
}

/**
 * Apply one answer to one proposal.
 * @param {{ call: (call: any, signal?: any, timeoutMs?: number) => Promise<any> }} client - the daemon client.
 * @param {string} proposalId - the proposal id, with any prefix.
 * @param {'approve' | 'reject' | 'changes'} decision - the answer.
 * @param {string | undefined} comment - what the person said, when they said anything.
 * @param {{ signal?: AbortSignal, timeoutMs?: number }} [options] - the tool's frame.
 * @returns {Promise<{ proposal: string, decision: string, status?: string, error?: string }>} the outcome.
 */
export async function applyDecision(client, proposalId, decision, comment, options = {}) {
  const endpoint = ENDPOINT[decision]
  if (endpoint === undefined) return { proposal: proposalId, decision, error: 'unknown decision' }
  const id = String(proposalId).replace(/^proposal:/, '')
  // `reject` needs a reason; `request-changes` needs a comment. The card may not have one, so say
  // what the reviewer chose in the words the daemon already understands.
  const body = { ...endpoint.body }
  if (endpoint.extra === 'reason') body.reason = comment ?? (decision === 'reject' ? 'rejected from the DSH card' : undefined)
  if (endpoint.extra === 'comment') body.comment = comment ?? 'changes requested from the DSH card'
  try {
    const result = await client.call(
      { method: 'POST', path: `/v1/proposals/${id}/${endpoint.path}`, body },
      options.signal,
      options.timeoutMs,
    )
    return { proposal: id, decision, status: result?.status }
  } catch (error) {
    return { proposal: id, decision, error: error instanceof Error ? error.message : String(error) }
  }
}

/**
 * Ask for a decision on every pending proposal and apply the answers.
 * @param {{
 *   client: { call: (call: any, signal?: any, timeoutMs?: number) => Promise<any> },
 *   ask: (request: { questions: any[], agent: any, signal?: any }) => Promise<{ answers: any[] }>,
 *   agent: any,
 *   signal?: AbortSignal,
 *   timeoutMs?: number,
 * }} options - the plumbing: the daemon client, the question channel, the live agent.
 * @returns {Promise<{ asked: number, outcomes: Array<{ proposal: string, decision: string, status?: string, error?: string }> }>}
 */
export async function reviewPending({ client, ask, agent, signal, timeoutMs }) {
  const pending = await client.call(
    { method: 'GET', path: `/v1/proposals?status=pending&limit=${bounded(20, 20)}` },
    signal,
    timeoutMs,
  )
  const proposals = Array.isArray(pending) ? pending : []
  if (proposals.length === 0) return { asked: 0, outcomes: [] }
  const questions = questionsFor(proposals)
  const answer = await ask({ questions, agent, signal })
  const answers = Array.isArray(answer?.answers) ? answer.answers : []
  const outcomes = []
  for (const [index, proposal] of proposals.entries()) {
    const decision = decisionOf(answers[index])
    if (decision === undefined) {
      outcomes.push({ proposal: String(proposal?.proposal ?? proposal?.id ?? ''), decision: 'unanswered' })
      continue
    }
    outcomes.push(
      await applyDecision(client, proposal?.proposal ?? proposal?.id, decision, commentOf(answers[index]), {
        signal,
        timeoutMs,
      }),
    )
  }
  return { asked: questions.length, outcomes }
}
