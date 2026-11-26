#!/usr/bin/env node
/**
 * Check that this bundle is installed in a running DeepSeek Harness and that its daemon answers.
 *
 * Everything here is read-only: it asks the daemon for health and an empty recall, and it asks the
 * Host for the client module graph to confirm the browser half is being served. Use it when the
 * Ontyra section is missing from Settings or when a tool answers `Ontyra is unreachable`.
 *
 *   node integrations/dsh/scripts/doctor.mjs
 *   node integrations/dsh/scripts/doctor.mjs --gui http://127.0.0.1:49200 --daemon http://127.0.0.1:8787
 *   DSH_GUI_URL=http://127.0.0.1:49200 node integrations/dsh/scripts/doctor.mjs
 *   node integrations/dsh/scripts/doctor.mjs --daemon http://100.86.189.45:8787 --token "$ONTYRA_TOKEN"
 *
 * The GUI port changes on every launch, so it has no default: the browser-half checks are skipped
 * unless `--gui` or `DSH_GUI_URL` names one.
 *
 * Exits 0 when every check passes, 1 otherwise.
 */

import { setTimeout as delay } from 'node:timers/promises'

const PACKAGE = 'dsh-ontyra-memory'

/** Parse `--flag value` pairs; unknown flags are ignored so the script stays usable while DSH moves. */
function options(argv) {
  const parsed = new Map()
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index]
    if (!token.startsWith('--')) continue
    const value = argv[index + 1]
    if (value !== undefined && !value.startsWith('--')) {
      parsed.set(token.slice(2), value)
      index += 1
    } else {
      parsed.set(token.slice(2), 'true')
    }
  }
  const gui = parsed.get('gui') ?? process.env.DSH_GUI_URL
  const token = parsed.get('token') ?? process.env.ONTYRA_TOKEN
  return {
    gui: gui === undefined || gui === 'true' ? undefined : noTrailingSlash(gui),
    daemon: noTrailingSlash(parsed.get('daemon') ?? process.env.ONTYRA_URL ?? 'http://127.0.0.1:8787'),
    token: token === undefined || token === 'true' ? undefined : token,
    timeout: Number(parsed.get('timeout') ?? 5000),
  }
}

/** @param {string} url - a base URL. @returns {string} the same URL without a trailing slash. */
function noTrailingSlash(url) {
  return url.replace(/\/+$/, '')
}

/**
 * Fetch with a deadline; the caller decides what a failure means.
 * @param {string} url - absolute URL.
 * @param {{ timeout: number, method?: string, headers?: Record<string, string>, body?: string }} plan - request shape.
 * @returns {Promise<Response>} the response, once headers are in.
 */
async function request(url, plan) {
  return fetch(url, {
    method: plan.method ?? 'GET',
    headers: plan.headers,
    body: plan.body,
    signal: AbortSignal.timeout(plan.timeout),
  })
}

/**
 * Read the first data-bearing Server-Sent-Events frame from a stream that never ends.
 *
 * The stream opens with a comment frame (`:connected`), so frames without a `data:` line are
 * skipped rather than parsed.
 * @param {string} url - the event stream.
 * @param {number} timeout - milliseconds to wait for the first frame.
 * @returns {Promise<string>} that frame's data text.
 */
async function firstFrame(url, timeout) {
  const abort = new AbortController()
  const deadline = setTimeout(() => abort.abort(), timeout)
  try {
    const response = await fetch(url, { headers: { accept: 'text/event-stream' }, signal: abort.signal })
    if (!response.ok) throw new Error(`GET ${url} answered ${response.status}`)
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    for (;;) {
      const { value, done } = await reader.read()
      if (done) throw new Error(`GET ${url} closed before sending a frame`)
      buffer += decoder.decode(value, { stream: true })
      for (;;) {
        const boundary = buffer.indexOf('\n\n')
        if (boundary < 0) break
        const frame = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        const data = frame
          .split('\n')
          .filter((line) => line.startsWith('data:'))
          .map((line) => line.slice(5).trimStart())
          .join('\n')
        if (data !== '') {
          abort.abort()
          return data
        }
      }
    }
  } finally {
    clearTimeout(deadline)
    await delay(0)
  }
}

/** Print one check's outcome and remember whether it failed. */
function report(results) {
  return (ok, label, detail) => {
    results.push(ok)
    console.log(`${ok ? 'ok  ' : 'FAIL'}  ${label}${detail === '' ? '' : `  —  ${detail}`}`)
  }
}

async function main() {
  const { gui, daemon, token, timeout } = options(process.argv.slice(2))
  const results = []
  const say = report(results)
  const authorization = token === undefined ? {} : { authorization: `Bearer ${token}` }

  try {
    const health = await request(`${daemon}/health`, { timeout, headers: authorization })
    const body = await health.json().catch(() => ({}))
    say(health.ok, 'daemon health', health.ok ? `${daemon} ${body.version ?? ''}`.trim() : `answered ${health.status}`)
  } catch (error) {
    say(false, 'daemon health', `${daemon}: ${error.message}`)
  }

  try {
    const recall = await request(`${daemon}/v1/recall`, {
      timeout,
      method: 'POST',
      headers: { ...authorization, 'content-type': 'application/json', accept: 'application/json' },
      body: JSON.stringify({ query: 'doctor probe', context: { uris: ['file:///doctor'], signals: [] } }),
    })
    const hint = recall.status === 401 ? ' — the daemon wants a token: pass --token or set ONTYRA_TOKEN' : ''
    say(recall.ok, 'daemon recall', `answered ${recall.status}${hint}`)
  } catch (error) {
    say(false, 'daemon recall', error.message)
  }

  let entry
  if (gui === undefined) {
    console.log('skip  Host module graph  —  pass --gui <url> or set DSH_GUI_URL to check the browser half')
  } else {
    try {
      const payload = await firstFrame(`${gui}/plugins/events`, timeout)
      const graph = JSON.parse(payload)
      const entries = graph.graph?.entries ?? []
      entry = entries.find((candidate) => candidate.id === PACKAGE)
      say(
        entry !== undefined,
        'Host module graph',
        entry === undefined
          ? `${PACKAGE} is not in ${entries.length} entries; the bundle is not installed or not enabled`
          : `${PACKAGE} at rev ${entry.rev}`,
      )
    } catch (error) {
      say(false, 'Host module graph', `${gui}/plugins/events: ${error.message}`)
    }
  }

  if (entry !== undefined) {
    try {
      const artifact = await request(`${gui}/${entry.url}`, { timeout })
      const text = artifact.ok ? await artifact.text() : ''
      const hasSection = text.includes('settings.section')
      say(
        artifact.ok && hasSection,
        'browser half',
        artifact.ok ? `${text.length} bytes, ${hasSection ? 'registers the settings section' : 'missing the section registration'}` : `answered ${artifact.status}`,
      )
    } catch (error) {
      say(false, 'browser half', error.message)
    }
  }

  const failed = results.filter((ok) => !ok).length
  console.log(failed === 0 ? '\nevery check passed' : `\n${failed} check(s) failed`)
  process.exitCode = failed === 0 ? 0 : 1
}

await main()
