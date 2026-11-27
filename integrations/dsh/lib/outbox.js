/**
 * A write that could not reach the daemon, kept until it can.
 *
 * The model is told the write is queued rather than lost, and the retry carries the same
 * `idempotency_key`, so a write that actually committed while the answer was lost is not stored
 * twice. Anything the daemon answered (even with an error) is dropped: a 4xx will not get better
 * by waiting, and replaying it forever would hide the real finding from the caller.
 *
 * @module
 */

/** How many writes are kept: enough to survive a short outage, small enough to stay invisible. */
export const LIMIT = 20

/**
 * @typedef {{ key: string, path: string, body: unknown, queuedAt: string, tool: string }} Pending
 * @typedef {{ load(): Pending[], save(items: Pending[]): void }} Persist
 * @typedef {{ status: number }} TransportFailure
 */

/**
 * Build an outbox over a persistence hook and a sender.
 * @param {object} options - the collaborators.
 * @param {Persist} options.persist - where the queue lives between calls (and restarts).
 * @param {(item: Pending) => Promise<unknown>} options.send - one replay.
 * @param {number} [options.limit] - how many pending writes to keep.
 * @returns {{ enqueue: (tool: string, path: string, body: unknown) => Promise<Pending>,
 *   flush: () => Promise<{ sent: number, kept: number }>, size: () => number,
 *   pending: () => Pending[] }} the outbox.
 */
export function createOutbox({ persist, send, limit = LIMIT }) {
  /** @type {Pending[] | undefined} */
  let items
  const all = () => {
    if (items === undefined) {
      try {
        items = persist.load()
      } catch {
        items = []
      }
      if (!Array.isArray(items)) items = []
    }
    return items
  }
  const keep = () => {
    try {
      persist.save(all())
    } catch {
      // A queue that cannot be written is still a queue in memory; losing it on restart is the
      // lesser failure compared with failing the model's tool call.
    }
  }
  /** @type {Promise<{ sent: number, kept: number }> | undefined} */
  let replaying
  const replay = async () => {
    const queue = all()
    let sent = 0
    while (queue.length > 0) {
      const item = queue[0]
      try {
        await send(item)
        sent += 1
      } catch (error) {
        const status = /** @type {TransportFailure} */ (error)?.status
        if (status === undefined || status === 0) break // still down: keep it and stop
      }
      queue.shift()
      keep()
    }
    return { sent, kept: queue.length }
  }
  return {
    /**
     * Keep a write for later.
     * @param {string} tool - the tool that attempted it, for the caller's message.
     * @param {string} path - the REST path.
     * @param {unknown} body - the request body.
     * @returns {Promise<Pending>} what was queued.
     */
    async enqueue(tool, path, body) {
      const queued = {
        key: globalThis.crypto.randomUUID(),
        path,
        body,
        tool,
        queuedAt: new Date().toISOString(),
      }
      const queue = all()
      queue.push(queued)
      while (queue.length > limit) queue.shift()
      keep()
      return queued
    },
    /**
     * Try every pending write once. A transport failure stops the run (the daemon is still down);
     * an answer, success or not, drops the item. Every tool call flushes first, so calls overlap:
     * they share one replay. Two replays would both send the head and both drop one item, which
     * discards the next write unsent.
     * @returns {Promise<{ sent: number, kept: number }>} what happened.
     */
    flush() {
      replaying ??= replay().finally(() => {
        replaying = undefined
      })
      return replaying
    },
    /** @returns {number} how many writes are waiting. */
    size() {
      return all().length
    },
    /** @returns {Pending[]} a copy of what is waiting. */
    pending() {
      return all().map((item) => ({ ...item }))
    },
  }
}
