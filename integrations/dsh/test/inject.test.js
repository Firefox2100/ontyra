/**
 * The pre-step injection's pure parts: prompt selection, the reference-data frame, and the
 * briefing cycle that follows compaction.
 *
 * These run without the Host, which is why the logic lives in `lib/inject-core.js`; the tests
 * assert the invariants the hook equivalence depends on rather than the wire calls.
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'
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
  messageText,
  PLUGIN,
  render,
  unclaimedTurn,
} from '../lib/inject-core.js'

const human = (text) => ({ content: [{ type: 'text', text }], source: { kind: 'user' } })

test('humanPrompt ignores injected and unknown sources', () => {
  const messages = [
    human('first prompt'),
    { content: [{ type: 'text', text: 'injected' }], source: { kind: PLUGIN, form: 'snapshot', sections: [] } },
    { content: [{ type: 'text', text: 'continuation' }], source: { kind: 'model' } },
  ]
  assert.equal(humanPrompt(messages), 'first prompt')
})

test('humanPrompt prefers the newest prompt and skips empty text', () => {
  assert.equal(humanPrompt([human('older'), human('newer')]), 'newer')
  assert.equal(humanPrompt([human('older'), human('   ')]), 'older')
  assert.equal(humanPrompt([]), undefined)
})

test('render frames memory as reference data, not instructions', () => {
  const text = render([{ name: 'briefing', text: ' Must follow: X ' }])
  assert.match(text, /^<ontyra-context trust="reference-only">/)
  assert.match(text, /not as instructions from the user/)
  assert.match(text, /## briefing\nMust follow: X/)
  assert.match(text, /<\/ontyra-context>$/)
})

test('a delivered briefing clears the flag; unrelated events keep the state', () => {
  const state = initialState()
  assert.equal(applyEvent(state, { type: 'turn/start' }), state)
  assert.equal(applyEvent(state, { type: 'assistant/message', data: {} }), state)

  const delivered = applyEvent(state, {
    type: 'user/message',
    data: { source: { kind: PLUGIN, form: 'snapshot', sections: [{ name: 'briefing', text: 'x' }] } },
  })
  assert.equal(delivered.needsBrief, false)
  assert.equal(delivered.briefings, 1)
})

test('a recall-only injection does not count as a briefing', () => {
  const state = applyEvent(initialState(), {
    type: 'user/message',
    data: { source: { kind: PLUGIN, form: 'snapshot', sections: [{ name: 'recall', text: 'x' }] } },
  })
  assert.equal(state.needsBrief, true)
  assert.equal(state.briefings, 0)
})

test('compaction starts a new cycle that asks for the briefing again', () => {
  const delivered = applyEvent(initialState(), {
    type: 'user/message',
    data: { source: { kind: PLUGIN, form: 'snapshot', sections: [{ name: 'briefing', text: 'x' }] } },
  })
  const after = applyEvent(delivered, { type: 'compaction/end' })
  assert.equal(after.needsBrief, true)
  assert.equal(after.compactions, 1)
  assert.equal(after.briefings, 1, 'a redelivery is still a delivery')
  assert.notEqual(briefingCycle('s', after.compactions), briefingCycle('s', delivered.compactions))
})

test('prompts are counted and the latest reply is kept for the episode log', () => {
  let state = initialState()
  state = applyEvent(state, { type: 'user/message', data: { source: { kind: 'user' } } })
  state = applyEvent(state, { type: 'user/message', data: { source: { kind: PLUGIN } } })
  assert.equal(state.prompts, 1, 'injected messages are not prompts')

  state = applyEvent(state, {
    type: 'assistant/message',
    data: { message: { content: [{ type: 'text', text: 'done' }, { type: 'text', text: 'and noted' }] } },
  })
  assert.equal(state.lastAssistant, 'done\nand noted')
  assert.equal(messageText({ content: [{ type: 'tool' }] }), null)
})

test('the capture reminder is due every N prompts or on wording that states a rule', () => {
  let state = initialState()
  for (let i = 0; i < 5; i += 1) {
    state = applyEvent(state, { type: 'user/message', data: { source: { kind: 'user' } } })
  }
  assert.equal(captureDue(state, 5), true)
  assert.equal(captureDue(state, 0), false, 'zero disables the periodic reminder')
  assert.equal(captureDue(state, 6), false)
  assert.equal(captureDue(state, 6, 'from now on use uv, not pip'), true)
  assert.equal(captureDue(state, 0, 'we decided to drop the cache'), true)
  assert.equal(captureDue(state, 6, 'how do I list files'), false)

  state = applyEvent(state, {
    type: 'user/message',
    data: { source: { kind: PLUGIN, sections: [{ name: CAPTURE, text: 'x' }] } },
  })
  assert.equal(state.lastCapture, 5)
  assert.equal(captureDue(state, 5), false)
  assert.match(capturePrompt(), /ontyra_remember/)
})

test('the reminder is plain guidance outside the reference block', () => {
  const both = render([{ name: 'recall', text: 'a memory' }, { name: CAPTURE, text: capturePrompt() }])
  assert.match(both, /<\/ontyra-context>\n\nOntyra: if this conversation/)
  const alone = render([{ name: CAPTURE, text: capturePrompt() }])
  assert.ok(!alone.includes('<ontyra-context'))
  assert.ok(alone.startsWith('Ontyra: '))
})

test('a subagent session is recognised from its header', () => {
  assert.equal(isSubagent({ origin: 'subagent' }), true)
  assert.equal(isSubagent({ delegationDepth: 2 }), true)
  assert.equal(isSubagent({ delegationDepth: 0 }), false)
  assert.equal(isSubagent({ cwd: 'D:/x' }), false)
  assert.equal(isSubagent(undefined), false)
})

test('each session recalls its own turn, even when the numbers collide', () => {
  const recalled = new Map()
  assert.equal(unclaimedTurn(recalled, 'a', 1), true)
  claimTurn(recalled, 'a', 1)
  assert.equal(unclaimedTurn(recalled, 'a', 1), false, 'a step rebuilds the same batch')
  assert.equal(unclaimedTurn(recalled, 'b', 1), true, 'turn numbers are per session')
  claimTurn(recalled, 'b', 1)
  assert.equal(unclaimedTurn(recalled, 'a', 2), true, 'the next turn recalls again')
})

test('proposals sent back by a reviewer become one line, others are ignored', () => {
  assert.equal(needsChangesNote([{ status: 'pending', id: 'proposal:a' }]), undefined)
  assert.equal(needsChangesNote('nope'), undefined)
  const note = needsChangesNote([
    { status: 'changes_requested', id: 'proposal:b' },
    { status: 'pending', id: 'proposal:c' },
  ])
  assert.match(note ?? '', /1 proposal\(s\) need changes .*proposal:b/)
  assert.match(note ?? '', /ontyra_amend/)
})
