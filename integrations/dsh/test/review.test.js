/**
 * The approval card: the one tool the daemon does not have, because it needs a person in front of
 * the screen. These tests pin the questions it asks and the calls it makes, without a UI.
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  APPROVE,
  REJECT,
  REQUEST_CHANGES,
  applyDecision,
  bounded,
  commentOf,
  decisionOf,
  describeProposal,
  firstSentence,
  questionsFor,
  reviewPending,
} from '../lib/review.js'

const PENDING = [
  {
    proposal: 'proposal:01a',
    status: 'pending',
    note: 'the user said so',
    ops: [
      { op: 'add', kind: 'lesson', fields: { text: 'Redact before storing. Then check again.' } },
      { op: 'retract', memory: 'memory:01b', reason: 'wrong' },
    ],
  },
  { proposal: 'proposal:01b', ops: [{ op: 'revise', memory: 'memory:01c', fields: { text: 'x' } }] },
]

test('a proposal is described by its ops and the reason it was sent', () => {
  const text = describeProposal(PENDING[0])
  assert.match(text, /add lesson: Redact before storing\./)
  assert.match(text, /retract memory:01b: wrong/)
  assert.match(text, /Why: the user said so/)
})

test('a long op list is cut, and says how much was left out', () => {
  const many = { ops: Array.from({ length: 9 }, () => ({ op: 'retract', memory: 'memory:x', reason: 'r' })) }
  const text = describeProposal(many)
  assert.equal(text.split('\n').filter((line) => line.startsWith('•')).length, 7)
  assert.match(text, /…and 3 more/)
})

test('the first sentence is what a card shows, not the whole memory', () => {
  assert.equal(firstSentence('One thing. Another thing.'), 'One thing.')
  assert.equal(firstSentence('   spaced   out  '), 'spaced out')
  assert.ok(firstSentence('x'.repeat(400)).length <= 160)
})

test('every waiting proposal becomes one question with three answers', () => {
  const questions = questionsFor(PENDING)
  assert.equal(questions.length, 2)
  assert.equal(questions[0].id, 'ontyra-proposal-0')
  assert.match(questions[0].question, /\(2 ops\)/)
  assert.match(questions[1].question, /\(1 op\)/)
  assert.deepEqual(questions[0].options.map((option) => option.label), [APPROVE, REJECT, REQUEST_CHANGES])
  assert.match(questions[0].detail, /add lesson/)
})

test('an answer is one of the three labels, and nothing else counts', () => {
  assert.equal(decisionOf([{ selected: [APPROVE] }]), 'approve')
  assert.equal(decisionOf([{ selected: [REJECT] }]), 'reject')
  assert.equal(decisionOf([{ selected: [REQUEST_CHANGES] }]), 'changes')
  assert.equal(decisionOf([{ selected: [] }]), undefined)
  assert.equal(decisionOf(undefined), undefined)
})

test('the free-text box is a comment when it has words in it', () => {
  assert.equal(commentOf([{ selected: [REJECT], custom: ' too vague ' }]), 'too vague')
  assert.equal(commentOf([{ selected: [REJECT], custom: '   ' }]), undefined)
  assert.equal(commentOf(undefined), undefined)
})

test('approving posts to the approve route under the bare id', async () => {
  const seen = []
  const client = { call: async (call) => (seen.push(call), { status: 'committed' }) }
  const outcome = await applyDecision(client, 'proposal:01a', 'approve', 'looks right')
  assert.equal(outcome.status, 'committed')
  assert.equal(seen[0].method, 'POST')
  assert.equal(seen[0].path, '/v1/proposals/01a/approve')
  assert.deepEqual(seen[0].body, { reason: 'looks right' })
})

test('rejecting without a comment still gives the daemon its reason', async () => {
  const seen = []
  const client = { call: async (call) => (seen.push(call), { status: 'rejected' }) }
  await applyDecision(client, '01b', 'reject', undefined)
  assert.equal(seen[0].path, '/v1/proposals/01b/reject')
  assert.match(seen[0].body.reason, /rejected from the DSH card/)
})

test('requesting changes posts the comment the daemon requires', async () => {
  const seen = []
  const client = { call: async (call) => (seen.push(call), { status: 'changes_requested' }) }
  await applyDecision(client, '01c', 'changes', 'say less')
  assert.equal(seen[0].path, '/v1/proposals/01c/request-changes')
  assert.deepEqual(seen[0].body, { comment: 'say less' })
})

test('a refused decision is reported, not thrown at the person', async () => {
  const client = { call: async () => { throw new Error('proposal already decided') } }
  const outcome = await applyDecision(client, '01d', 'approve', undefined)
  assert.match(outcome.error, /already decided/)
  assert.equal(outcome.decision, 'approve')
})

test('a review round asks one question per proposal and applies each answer', async () => {
  const calls = []
  const client = {
    call: async (call) => {
      calls.push(call)
      if (call.method === 'GET') return PENDING
      return { status: 'committed' }
    },
  }
  const asked = []
  const result = await reviewPending({
    client,
    ask: async (request) => {
      asked.push(request)
      return { answers: [{ id: 'ontyra-proposal-0', selected: [APPROVE] }, { id: 'ontyra-proposal-1', selected: [REJECT], custom: 'no' }] }
    },
    agent: { id: 'session:1' },
  })
  assert.equal(result.asked, 2)
  assert.equal(asked.length, 1)
  assert.equal(asked[0].questions.length, 2)
  assert.deepEqual(result.outcomes.map((outcome) => outcome.decision), ['approve', 'reject'])
  assert.equal(calls.filter((call) => call.method === 'POST').length, 2)
  assert.match(calls[0].path, /status=pending/)
})

test('nothing waiting means nothing asked', async () => {
  let asked = 0
  const result = await reviewPending({
    client: { call: async () => [] },
    ask: async () => {
      asked += 1
      return { answers: [] }
    },
    agent: {},
  })
  assert.deepEqual(result, { asked: 0, outcomes: [] })
  assert.equal(asked, 0)
})

test('a question left unanswered is named, not applied', async () => {
  const posted = []
  const client = {
    call: async (call) => {
      if (call.method === 'GET') return [PENDING[0]]
      posted.push(call)
      return { status: 'committed' }
    },
  }
  const result = await reviewPending({ client, ask: async () => ({ answers: [] }), agent: {} })
  assert.deepEqual(result.outcomes, [{ proposal: 'proposal:01a', decision: 'unanswered' }])
  assert.equal(posted.length, 0)
})

test('the limit keeps a stray number from asking about the whole queue', () => {
  assert.equal(bounded(9999, 20), 50)
  assert.equal(bounded(0, 20), 20)
  assert.equal(bounded('x', 20), 20)
  assert.equal(bounded(3, 20), 3)
})

test('a card names the ends of a link and the target of a note', () => {
  const text = describeProposal({
    ops: [
      { op: 'link', relation: 'contradicts', source: 'memory:a', target: 'memory:b' },
      { op: 'annotate', target: 'memory:c', kind: 'comment', text: 'Check this first.' },
    ],
  })
  assert.match(text, /link memory:a contradicts memory:b/)
  assert.match(text, /annotate memory:c: Check this first\./)
  assert.doesNotMatch(text, /undefined/)
})
