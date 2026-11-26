/**
 * The client's deadlines: injection is cut short, a tool call gets its own budget.
 *
 * A daemon on another host needs about two seconds for a recall. The step it holds up cannot wait
 * that long, so the settings carry two numbers and the caller picks one per call; these cases pin
 * that the third argument really replaces the configured value.
 */

import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import test from 'node:test'

import { hostOf } from '../lib/context.js'
import { createClient, OntyraError } from '../lib/ontyra.js'

/** A daemon that answers after `delayMs`, so a deadline can be observed rather than guessed. */
async function slowDaemon(delayMs) {
  const server = createServer((_request, response) => {
    setTimeout(() => {
      response.writeHead(200, { 'content-type': 'application/json' })
      response.end('{"ok":true}')
    }, delayMs)
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const { port } = server.address()
  return {
    url: `http://127.0.0.1:${port}`,
    async close() {
      await new Promise((resolve) => server.close(resolve))
    },
  }
}

function clientFor(url, timeoutMs) {
  return createClient({
    settings: () => ({ baseUrl: url, harness: 'dsh', timeoutMs }),
    resolveToken: async () => undefined,
  })
}

test('a call without its own budget uses the configured timeout', async () => {
  const daemon = await slowDaemon(400)
  try {
    const client = clientFor(daemon.url, 50)
    await assert.rejects(
      () => client.call({ method: 'GET', path: '/health' }, AbortSignal.timeout(5000)),
      (error) => error instanceof OntyraError && error.status === 0 && /unreachable/.test(error.message),
    )
  } finally {
    await daemon.close()
  }
})

test('a call may name a longer budget than the configured one', async () => {
  const daemon = await slowDaemon(300)
  try {
    const client = clientFor(daemon.url, 50)
    const answer = await client.call({ method: 'GET', path: '/health' }, AbortSignal.timeout(5000), 4000)
    assert.deepEqual(answer, { ok: true })
  } finally {
    await daemon.close()
  }
})

test('a budget of zero still leaves the call a hair of time rather than failing to construct', async () => {
  const daemon = await slowDaemon(10)
  try {
    const client = clientFor(daemon.url, 4000)
    // `AbortSignal.timeout(0)` is already aborted, so the client clamps to 1 ms; the server's 10 ms
    // delay makes the outcome deterministic without depending on scheduler luck.
    await assert.rejects(() => client.call({ method: 'GET', path: '/health' }, AbortSignal.timeout(5000), 0))
  } finally {
    await daemon.close()
  }
})

test('every call declares which machine it runs on', async () => {
  /** @type {import('node:http').IncomingHttpHeaders | undefined} */
  let seen
  const server = createServer((request, response) => {
    seen = request.headers
    response.writeHead(200, { 'content-type': 'application/json' })
    response.end('{"ok":true}')
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const { port } = server.address()
  try {
    const client = clientFor(`http://127.0.0.1:${port}`, 4000)
    await client.call({ method: 'GET', path: '/health' }, AbortSignal.timeout(5000))
    // The daemon records path anchors against this header, so a host typed into an anchor cannot
    // survive; a call without it would fall back to the daemon's own name.
    assert.equal(seen?.['x-ontyra-machine'], hostOf())
  } finally {
    await new Promise((resolve) => server.close(resolve))
  }
})
