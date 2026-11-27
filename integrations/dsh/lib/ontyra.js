/**
 * The daemon client: one HTTP call per tool invocation, bounded by the configured timeout and
 * the caller's cancellation, failing with the daemon's own message.
 *
 * The plugin never caches a reply and never retries: memory is optional context, so a slow or
 * absent daemon must cost a call, not a session.
 */

import { hostOf } from './context.js'

/** A failed daemon call, carrying what the model should read. */
export class OntyraError extends Error {
  /**
   * @param {string} message - what went wrong, as the daemon said it.
   * @param {number} status - HTTP status, or 0 for a transport failure.
   */
  constructor(message, status) {
    super(message)
    this.name = 'OntyraError'
    this.status = status
  }
}

/**
 * The daemon's error text from a JSON error body: FastAPI sends a string, or a list of
 * validation entries.
 * @param {unknown} body - parsed reply body, if any.
 * @param {string} fallback - text to use when the body carries nothing useful.
 * @returns {string} one line for the model.
 */
export function errorText(body, fallback) {
  const detail = /** @type {{ detail?: unknown }} */ (body)?.detail
  if (typeof detail === 'string' && detail.length > 0) return detail
  if (Array.isArray(detail)) {
    const parts = detail
      .map((entry) => /** @type {{ msg?: unknown, loc?: unknown }} */ (entry))
      .map((entry) => (typeof entry?.msg === 'string' ? entry.msg : null))
      .filter((part) => part !== null)
    if (parts.length > 0) return parts.join('; ')
  }
  return fallback
}

/**
 * Join a base URL and an absolute path without doubling or dropping the slash.
 * @param {string} baseUrl - configured daemon address.
 * @param {string} path - path starting with `/`.
 * @returns {string} the URL to fetch.
 */
export function joinUrl(baseUrl, path) {
  return `${baseUrl.replace(/\/+$/, '')}${path}`
}

/**
 * Build the client one plugin instance uses. Settings are read per call, so a settings edit takes
 * effect on the next tool call without a restart.
 * @param {{
 *   settings: () => { baseUrl: string, harness: string, timeoutMs: number },
 *   resolveToken: () => Promise<{ value: string } | undefined>,
 * }} options - configuration and credential accessors.
 * @returns {{ call(call: { method: string, path: string, body?: unknown }, signal?: AbortSignal, timeoutMs?: number): Promise<unknown> }} the client.
 */
export function createClient({ settings, resolveToken }) {
  return {
    async call(call, signal, timeoutMs) {
      const config = settings()
      const token = await resolveToken()
      /** @type {Record<string, string>} */
      const headers = {
        accept: 'application/json',
        'x-ontyra-actor': config.harness,
        'x-ontyra-actor-kind': 'agent',
        'x-ontyra-harness': config.harness,
        // Which machine this is: the daemon records every path anchor of a proposal against it,
        // so a host that was typed or copied into an anchor cannot survive.
        'x-ontyra-machine': hostOf(),
      }
      if (call.body !== undefined) headers['content-type'] = 'application/json'
      if (token !== undefined && token.value.length > 0) {
        headers.authorization = `Bearer ${token.value}`
      }
      // Injection must not stall a step and a tool call is worth waiting for, so the caller may
      // name its own budget; without one, the settings' `timeoutMs` stands.
      const budget = timeoutMs === undefined ? config.timeoutMs : timeoutMs
      const timeout = AbortSignal.timeout(Math.max(1, Number(budget) || 1))
      // A caller without a signal of its own (the tab's read routes) still gets the budget: an
      // absent signal is not an aborted one, so give the combinable list a live member.
      const caller = signal ?? new AbortController().signal
      /** @type {Response} */
      let response
      try {
        response = await fetch(joinUrl(config.baseUrl, call.path), {
          method: call.method,
          headers,
          body: call.body === undefined ? undefined : JSON.stringify(call.body),
          signal: AbortSignal.any([caller, timeout]),
        })
      } catch (cause) {
        const reason = cause instanceof Error ? cause.message : String(cause)
        throw new OntyraError(`Ontyra is unreachable at ${config.baseUrl}: ${reason}`, 0)
      }
      const text = await response.text()
      /** @type {unknown} */
      let body
      try {
        body = text.length > 0 ? JSON.parse(text) : null
      } catch {
        body = null
      }
      if (!response.ok) {
        throw new OntyraError(
          `Ontyra answered ${response.status}: ${errorText(body, text.slice(0, 400) || response.statusText)}`,
          response.status,
        )
      }
      return body
    },
  }
}
