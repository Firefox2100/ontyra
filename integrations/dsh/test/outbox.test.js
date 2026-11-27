import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createOutbox } from '../lib/outbox.js'

/** A memory-backed stand-in for the file the Host half uses. */
function memory() {
  let saved = []
  return {
    persist: {
      load: () => saved,
      save: (items) => {
        saved = items
      },
    },
    saved: () => saved,
  }
}

/** The error `createClient` throws when the daemon cannot be reached at all. */
function down() {
  const error = new Error('Ontyra is unreachable')
  error.status = 0
  return error
}

/** The error `createClient` throws when the daemon answered with a status. */
function refused(status) {
  const error = new Error(`Ontyra answered ${status}`)
  error.status = status
  return error
}

test('a queued write is replayed once and leaves the queue', async () => {
  const store = memory()
  const sent = []
  const outbox = createOutbox({
    persist: store.persist,
    send: async (item) => {
      sent.push(item)
    },
  })
  const queued = await outbox.enqueue('ontyra_remember', '/v1/proposals', { ops: [] })
  assert.equal(outbox.size(), 1)
  const result = await outbox.flush()
  assert.deepEqual(result, { sent: 1, kept: 0 })
  assert.equal(sent.length, 1)
  assert.equal(sent[0].key, queued.key)
  assert.equal(outbox.size(), 0)
  assert.deepEqual(store.saved(), [])
})

test('a daemon that is still down keeps the write, and stops the run', async () => {
  const store = memory()
  const outbox = createOutbox({
    persist: store.persist,
    send: async () => {
      throw down()
    },
  })
  await outbox.enqueue('ontyra_remember', '/v1/proposals', { ops: [] })
  await outbox.enqueue('ontyra_forget', '/v1/memories/forget', { uri: 'file://d/x' })
  const result = await outbox.flush()
  assert.deepEqual(result, { sent: 0, kept: 2 })
  assert.equal(outbox.size(), 2)
})

test('an answer, even a refusal, drops the write for good', async () => {
  let calls = 0
  const outbox = createOutbox({
    persist: memory().persist,
    send: async () => {
      calls += 1
      throw refused(422)
    },
  })
  await outbox.enqueue('ontyra_remember', '/v1/proposals', { ops: [] })
  const result = await outbox.flush()
  assert.deepEqual(result, { sent: 0, kept: 0 })
  assert.equal(calls, 1)
  assert.equal(outbox.size(), 0)
})

test('the queue has a ceiling, and it forgets the oldest', async () => {
  const outbox = createOutbox({ persist: memory().persist, send: async () => {}, limit: 2 })
  await outbox.enqueue('a', '/v1/proposals', { ops: [] })
  await outbox.enqueue('b', '/v1/proposals', { ops: [] })
  await outbox.enqueue('c', '/v1/proposals', { ops: [] })
  assert.deepEqual(
    outbox.pending().map((item) => item.tool),
    ['b', 'c'],
  )
})

test('a restarted Host finds what was queued before', async () => {
  const store = memory()
  const first = createOutbox({ persist: store.persist, send: async () => {} })
  const queued = await first.enqueue('ontyra_remember', '/v1/proposals', { ops: [] })
  const second = createOutbox({ persist: store.persist, send: async () => {} })
  assert.deepEqual(
    second.pending().map((item) => item.key),
    [queued.key],
  )
})

test('overlapping flushes share one replay and lose nothing', async () => {
  const store = memory()
  const sent = []
  const outbox = createOutbox({
    persist: store.persist,
    send: async (item) => {
      await new Promise((resolve) => setTimeout(resolve, 5))
      sent.push(item.path)
    },
  })
  await outbox.enqueue('ontyra_remember', '/a', {})
  await outbox.enqueue('ontyra_remember', '/b', {})
  await outbox.enqueue('ontyra_remember', '/c', {})
  const results = await Promise.all([outbox.flush(), outbox.flush(), outbox.flush()])
  assert.deepEqual(sent, ['/a', '/b', '/c'])
  assert.deepEqual(results[0], { sent: 3, kept: 0 })
  assert.equal(outbox.size(), 0)
  await outbox.enqueue('ontyra_remember', '/d', {})
  assert.deepEqual(await outbox.flush(), { sent: 1, kept: 0 }) // a finished replay does not stick
})
