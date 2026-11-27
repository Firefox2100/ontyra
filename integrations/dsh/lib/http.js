/**
 * The plugin's own HTTP face on the GUI origin.
 *
 * A client half runs in the browser and cannot reach the daemon directly: the daemon may be on
 * another machine, its token never leaves the credential store, and a cross-origin request would
 * have to reveal both. DSH already runs an HTTP server for the GUI, and a plugin may claim a path
 * on it (`ctx.webServer.register`), so the browser half fetches this route and the Host half does
 * the talking — same process, same credentials, one place that knows the settings.
 *
 * The route answers JSON only. Reads are `GET`; the two writes the Memory tab offers (`correct`,
 * `forget`) are `POST` and require the `x-ontyra-client` header, which a cross-site form cannot
 * send without a preflight this Host never approves — the tab is the only caller.
 */

/** Prefix every route of this plugin lives under. */
export const PREFIX = '/ontyra'

/**
 * Mount the plugin's HTTP routes.
 * @param {import('@deepseek-ai/cordis').Context} ctx - the plugin context.
 * @param {{ settingsNow: () => any }} deps - live settings, read per request.
 * @returns {void}
 */
export function registerRoutes(ctx, deps) {
  ctx.inject(['webServer'], (webCtx) => {
    webCtx.effect(() => {
      const disposers = [
        webCtx.webServer.register({
          kind: 'prefix',
          path: PREFIX,
          resource: (request) => answer(request, { ...deps, ctx }),
        }),
        // A `resource` answers `GET`/`HEAD` only — the Host refuses every other method with 405
        // before matching — so the two writes are exact `handler` routes that see the raw request.
        ...WRITES.map((path) =>
          webCtx.webServer.register({
            kind: 'exact',
            path: `${PREFIX}${path}`,
            handler: (req, res) => serveWrite(path, req, res, deps),
          }),
        ),
        // Replaying the outbox is a write too, and it needs no body: a person clicking "retry" is
        // the whole request.
        webCtx.webServer.register({
          kind: 'exact',
          path: `${PREFIX}/outbox/retry`,
          handler: (req, res) => serveRetry(req, res, deps),
        }),
      ]
      return () => {
        for (const dispose of disposers) dispose()
      }
    }, 'ontyra: http routes')
  })
}
/**
 * Answer one request under {@link PREFIX}.
 * @param {Request} request - the incoming request.
 * @param {{ settingsNow: () => any }} deps - live settings.
 * @returns {Promise<Response>} the response.
 */
async function answer(request, deps) {
  const url = new URL(request.url)
  const path = url.pathname.slice(PREFIX.length) || '/'
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    return json({ error: 'this route only reads' }, 405)
  }
  if (path === '/ping') {
    const settings = deps.settingsNow()
    return json({
      ok: true,
      plugin: 'dsh-ontyra-memory',
      baseUrl: settings.baseUrl,
      sawClientHeader: request.headers.get('x-ontyra-client'),
      path: url.pathname,
      // A real test, not an echo: the settings card says "connected" only when the daemon answers.
      daemon: await health(deps),
    })
  }
  if (path === '/services') {
    // Which seams this deployment actually composes: the plugin degrades per seam, so the client
    // half and a debugging user can see what is present before blaming a missing feature.
    return json(Object.fromEntries(SERVICES.map((name) => [name, present(deps.ctx, name)])))
  }
  if (path === '/memories') {
    return memories(url, deps)
  }
  if (path.startsWith('/memories/') && path.length > '/memories/'.length) {
    return oneMemory(path.slice('/memories/'.length), deps)
  }
  if (path === '/outbox') {
    return outboxState(deps)
  }
  if (path === '/last') {
    return lastPack(url, deps)
  }
  return json({ error: `no route for ${url.pathname}` }, 404)
}

/**
 * Ask the daemon whether it is there, on a short budget: the card is a status line, not a job.
 * @param {{ client: any, settingsNow: () => any }} deps - the daemon client and live settings.
 * @returns {Promise<{ ok: boolean, version?: string, error?: string, status?: number }>} the probe.
 */
async function health(deps) {
  try {
    const body = await deps.client.call({ method: 'GET', path: '/health' }, undefined, 5000)
    return { ok: true, version: body?.version }
  } catch (error) {
    return { ok: false, error: error?.message ?? String(error), status: error?.status ?? 0 }
  }
}

/**
 * One memory with what it rests on, for the row the tab expands.
 * @param {string} id - the memory id, with or without its prefix.
 * @param {{ client: any }} deps - the daemon client.
 * @returns {Promise<Response>} `{ ok, memory }` or `{ ok: false, error }`.
 */
async function oneMemory(id, deps) {
  const bare = decodeURIComponent(id).replace(/^memory:/, '')
  const query = new URLSearchParams({ expand: 'evidence' })
  try {
    const body = await deps.client.call({ method: 'GET', path: `/v1/memories/${bare}/detail?${query.toString()}` })
    return json({ ok: true, memory: body })
  } catch (error) {
    return json({ ok: false, error: error?.message ?? String(error), status: error?.status ?? 0 })
  }
}

/**
 * The last context the model was handed in a session: what the plugin actually injected.
 *
 * It is a read-only mirror of memory the Host half already holds. It exists because "the agent
 * seems to know something odd" is otherwise unanswerable — the transcript shows the injected
 * block, but not which memory or why.
 * @param {URL} url - the request URL, whose `session` narrows the answer.
 * @param {{ recent?: Map<string, any> }} deps - the remembered packs.
 * @returns {Response} `{ ok, sessions: [...] }` or `{ ok, session, at, parts }`.
 */
function lastPack(url, deps) {
  const sessions = deps.recent ?? new Map()
  const wanted = url.searchParams.get('session')
  if (wanted === null) {
    const list = [...sessions.entries()]
      .map(([session, entry]) => ({ session, at: entry.at, parts: entry.parts?.length ?? 0 }))
      .sort((left, right) => right.at - left.at)
      .slice(0, 20)
    return json({ ok: true, sessions: list })
  }
  const entry = sessions.get(wanted)
  if (entry === undefined) return json({ ok: false, error: 'this session has not been injected yet' })
  return json({ ok: true, session: wanted, at: entry.at, parts: entry.parts })
}

/**
 * What is waiting to be sent, so the tab can say "two writes are not stored yet".
 * @param {{ outbox?: any }} deps - the write queue, when the deployment has one.
 * @returns {Response} `{ ok, count, items }`.
 */
function outboxState(deps) {
  const pending = typeof deps.outbox?.pending === 'function' ? deps.outbox.pending() : []
  return json({
    ok: true,
    count: pending.length,
    items: pending.map((item) => ({ tool: item.tool, path: item.path, at: item.at ?? item.enqueuedAt })),
  })
}

/**
 * Replay the queue on demand: a person who knows the daemon is back should not have to wait for
 * the next tool call.
 * @param {import('node:http').IncomingMessage} req - the raw request.
 * @param {import('node:http').ServerResponse} res - the raw response.
 * @param {{ outbox?: any }} deps - the write queue.
 * @returns {Promise<void>} resolves once the response is written.
 */
async function serveRetry(req, res, deps) {
  if (!req.headers['x-ontyra-client']) {
    return send(res, 400, { ok: false, error: 'the Memory tab must identify itself' })
  }
  if (typeof deps.outbox?.flush !== 'function') {
    return send(res, 200, { ok: true, sent: 0, left: 0 })
  }
  const before = deps.outbox.pending().length
  let error
  try {
    await deps.outbox.flush()
  } catch (failure) {
    error = failure?.message ?? String(failure)
  }
  const left = deps.outbox.pending().length
  return send(res, 200, { ok: error === undefined, sent: before - left, left, error })
}

/**
 * List memories through the daemon for the tab in the browser.
 *
 * The tab is a reader here: it never writes the delivery ledger, so browsing the library cannot
 * make recall think a memory was already handed to the agent.
 * @param {URL} url - the request URL, whose query narrows the list.
 * @param {{ client: any }} deps - the daemon client.
 * @returns {Promise<Response>} `{ ok, items }` or `{ ok: false, error }`.
 */
async function memories(url, deps) {
  const limit = Math.min(Math.max(Number(url.searchParams.get('limit')) || 100, 1), 500)
  const query = new URLSearchParams({ status: 'active', limit: String(limit) })
  const kind = url.searchParams.get('kind')
  if (kind !== null && kind !== '') query.set('kind', kind)
  try {
    const body = await deps.client.call({ method: 'GET', path: `/v1/memories?${query.toString()}` })
    const items = Array.isArray(body) ? body : []
    return json({ ok: true, items: items.map(slim) })
  } catch (error) {
    // A daemon that is down is a state the tab renders, not a broken route: answer 200 with the
    // reason so the panel can say what is wrong instead of showing an empty library.
    return json({ ok: false, error: error?.message ?? String(error), status: error?.status ?? 0 })
  }
}

/**
 * Keep only what the tab shows, so the payload stays small for a large library.
 * @param {any} entry - one `MemoryView` from `GET /v1/memories`.
 * @returns {object} the fields the panel renders.
 */
function slim(entry) {
  const content = entry?.content ?? {}
  const anchors = Array.isArray(content.anchors) ? content.anchors : []
  return {
    id: String(entry?.memory ?? ''),
    kind: entry?.kind,
    text: entry?.text,
    status: entry?.status,
    revision: entry?.revision === undefined ? undefined : String(entry.revision),
    scope: content.scope,
    confidence: entry?.confidence,
    anchors: anchors.map((anchor) => ({ uri: anchor?.uri, match: anchor?.match })),
  }
}

/** Services this plan wants to use, in the order the docs name them. */
const SERVICES = ['webServer', 'userQuestions', 'approval', 'credentials', 'skills', 'workingDirectory', 'sessionProjections', 'sessions']

/** The two writes the Memory tab may make, each an exact handler route. */
const WRITES = ['/correct', '/forget']

/** How much of a request body the two writes accept. */
const BODY_LIMIT = 64 * 1024

/**
 * Answer one write from the tab: check the caller, read the body, proxy it, answer JSON.
 *
 * The `x-ontyra-client` header is the caller check: a cross-site form can post without a preflight
 * only without it, and anything that does send it is a page on this origin. `content-type` keeps
 * the body the JSON the tab promised to send.
 * @param {string} path - `/correct` or `/forget`.
 * @param {import('node:http').IncomingMessage} req - the raw request.
 * @param {import('node:http').ServerResponse} res - the raw response.
 * @param {{ client: any, settingsNow: () => any }} deps - the daemon client and live settings.
 * @returns {Promise<void>} resolves once the response is written.
 */
async function serveWrite(path, req, res, deps) {
  if (!req.headers['x-ontyra-client']) {
    return send(res, 400, { ok: false, error: 'the Memory tab must identify itself' })
  }
  if (!String(req.headers['content-type'] ?? '').startsWith('application/json')) {
    return send(res, 400, { ok: false, error: 'expected a JSON body' })
  }
  let body
  try {
    body = JSON.parse(await readBody(req))
  } catch {
    return send(res, 400, { ok: false, error: 'expected a JSON body' })
  }
  const { status, payload } = await applyWrite(path, body, deps)
  return send(res, status, payload)
}

/**
 * Read a request body, refusing anything larger than a correction needs.
 * @param {import('node:http').IncomingMessage} req - the raw request.
 * @returns {Promise<string>} the body text.
 */
async function readBody(req) {
  const chunks = []
  let size = 0
  for await (const chunk of req) {
    size += chunk.length
    if (size > BODY_LIMIT) throw new Error('body too large')
    chunks.push(chunk)
  }
  return Buffer.concat(chunks).toString('utf8')
}

/**
 * Proxy one write to the daemon and say what happened.
 *
 * Both writes are ordinary proposals on the daemon side, so the answer carries `status`: `pending`
 * means a human still has to approve it, and the tab says so rather than pretending it is done.
 * @param {string} path - `/correct` or `/forget`.
 * @param {any} body - the parsed JSON payload.
 * @param {{ client: any, settingsNow: () => any }} deps - the daemon client and live settings.
 * @returns {Promise<{ status: number, payload: any }>} the HTTP status and body to send.
 */
async function applyWrite(path, body, deps) {
  const settings = deps.settingsNow()
  try {
    if (path === '/correct') {
      const memory = String(body?.memory ?? '').replace(/^memory:/, '')
      if (!memory) return { status: 400, payload: { ok: false, error: 'correct needs a memory id' } }
      const fields = body?.fields
      if (!fields || typeof fields !== 'object' || Array.isArray(fields) || Object.keys(fields).length === 0) {
        return { status: 400, payload: { ok: false, error: 'correct needs the fields that change' } }
      }
      const result = await deps.client.call(
        {
          method: 'POST',
          path: `/v1/memories/${encodeURIComponent(memory)}/correct`,
          body: { fields, reason: String(body?.reason ?? 'edited in the Memory tab') },
        },
        undefined,
        settings.toolTimeoutMs,
      )
      return { status: 200, payload: { ok: true, result } }
    }
    const forget = { reason: String(body?.reason ?? 'forgotten from the Memory tab') }
    if (body?.memory) forget.memory = String(body.memory).replace(/^memory:/, '')
    else if (body?.uri) forget.uri = String(body.uri)
    else return { status: 400, payload: { ok: false, error: 'forget needs a memory or a uri' } }
    const result = await deps.client.call(
      { method: 'POST', path: '/v1/memories/forget', body: forget },
      undefined,
      settings.toolTimeoutMs,
    )
    return { status: 200, payload: { ok: true, result } }
  } catch (error) {
    const status = Number(error?.status ?? 0)
    return {
      status: status >= 400 && status < 500 ? 400 : 502,
      payload: { ok: false, error: error?.message ?? String(error), status },
    }
  }
}

/**
 * Write a JSON answer on a raw response.
 * @param {import('node:http').ServerResponse} res - the raw response.
 * @param {number} status - the status code.
 * @param {unknown} payload - the body.
 * @returns {void}
 */
function send(res, status, payload) {
  const text = JSON.stringify(payload)
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
    'content-length': Buffer.byteLength(text),
  })
  res.end(text)
}

/**
 * Whether a service is registered on the context.
 * @param {any} ctx - the plugin context.
 * @param {string} name - the service name.
 * @returns {boolean} whether it resolved.
 */
function present(ctx, name) {
  try {
    return typeof ctx?.get === 'function' ? ctx.get(name) !== undefined : false
  } catch {
    return false
  }
}

/**
 * Serialise a small JSON body.
 * @param {unknown} body - the payload.
 * @param {number} [status] - the status code.
 * @returns {Response} the response.
 */
function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' },
  })
}
