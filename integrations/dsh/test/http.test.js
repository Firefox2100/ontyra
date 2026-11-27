/**
 * The plugin's HTTP face: the browser half fetches its own route on the GUI origin, and this half
 * talks to the daemon. These tests drive the routes without a socket, so they pin the contract a
 * client half can rely on: which paths answer, what a wrong method gets, and that only JSON leaves.
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { PREFIX, registerRoutes } from '../lib/http.js'

/**
 * Mount the routes on a fake context and hand back both halves of the seam.
 *
 * One `resource` answers reads (the Host serves those itself, `GET`/`HEAD` only); two exact
 * `handler` routes answer the writes, because a resource never sees another method.
 * @param {object} [settings] - what `settingsNow()` should answer.
 * @param {{ call: (call: any, signal?: any, timeoutMs?: number) => Promise<any> }} [client] - the daemon client.
 * @returns {{
 *   routes: Array<any>,
 *   path: string,
 *   kind: string,
 *   request: (path: string, init?: RequestInit) => Promise<Response>,
 *   write: (path: string, body: any, headers?: Record<string, string>) => Promise<{ status: number, body: any }>,
 * }} the mounted surface.
 */
function mount(settings = { baseUrl: 'http://127.0.0.1:8787', toolTimeoutMs: 20000 }, client = { call: async () => [] }, extra = {}) {
  const routes = []
  const ctx = {
    inject(names, callback) {
      callback({ effect: (fn) => fn(), webServer: { register: (route) => (routes.push(route), () => {}) } })
    },
    get: (name) => (name === 'webServer' ? { register: () => () => {} } : undefined),
  }
  registerRoutes(ctx, { settingsNow: () => settings, client, ...extra })
  const resource = routes.find((route) => route.resource !== undefined)
  assert.ok(resource, 'the plugin registers its read route when the web server is present')
  assert.equal(resource.kind, 'prefix')
  assert.equal(resource.path, PREFIX)
  return {
    routes,
    path: resource.path,
    kind: resource.kind,
    request: (path, init) => resource.resource(new Request(`http://127.0.0.1:63508${path}`, init)),
    write: (path, body, headers = {}) => {
      const route = routes.find((entry) => entry.path === `${PREFIX}${path}`)
      assert.ok(route?.handler, `the plugin registers a handler for ${path}`)
      const text = typeof body === 'string' ? body : JSON.stringify(body)
      const req = {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'x-ontyra-client': 'tab', ...headers },
        async *[Symbol.asyncIterator]() {
          yield Buffer.from(text)
        },
      }
      let status = 0
      let answer = ''
      const res = {
        writeHead: (code) => {
          status = code
        },
        end: (chunk) => {
          answer = chunk
        },
      }
      return Promise.resolve(route.handler(req, res)).then(() => ({
        status,
        body: answer === '' ? null : JSON.parse(answer),
      }))
    },
  }
}

test('the read route is a prefix under the plugin path', () => {
  const route = mount()
  assert.equal(route.path, PREFIX)
  assert.equal(route.kind, 'prefix')
  assert.equal(PREFIX, '/ontyra')
})

test('each write is an exact handler route, because a resource only answers GET and HEAD', () => {
  const route = mount()
  const writes = route.routes
    .filter((entry) => entry.handler !== undefined)
    .map((entry) => `${entry.kind} ${entry.path}`)
  assert.deepEqual(writes, ['exact /ontyra/correct', 'exact /ontyra/forget', 'exact /ontyra/outbox/retry'])
})

test('ping answers what the client half needs to render a connection line', async () => {
  const res = await mount({ baseUrl: 'http://100.86.189.45:8787/' }).request('/ontyra/ping', {
    headers: { 'x-ontyra-client': 'tab' },
  })
  assert.equal(res.status, 200)
  assert.match(res.headers.get('content-type'), /application\/json/)
  assert.equal(res.headers.get('cache-control'), 'no-store')
  const body = await res.json()
  assert.deepEqual(body, {
    ok: true,
    plugin: 'dsh-ontyra-memory',
    baseUrl: 'http://100.86.189.45:8787/',
    sawClientHeader: 'tab',
    path: '/ontyra/ping',
    // The ping is a real probe: the settings card must not say "connected" on a saved URL alone.
    daemon: { ok: true },
  })
})

test('a daemon that does not answer is said so, in the ping', async () => {
  const client = {
    call: async () => {
      throw Object.assign(new Error('Ontyra is unreachable at http://127.0.0.1:8787/'), { status: 0 })
    },
  }
  const res = await mount(undefined, client).request('/ontyra/ping')
  const body = await res.json()
  assert.equal(body.ok, true, 'the route itself answered')
  assert.equal(body.daemon.ok, false)
  assert.match(body.daemon.error, /unreachable/)
})

test('the last pack is a mirror of memory, never a source', async () => {
  const recent = new Map([['s1', { at: 5, parts: [{ name: 'briefing', text: 'x' }] }]])
  const mounted = mount(undefined, undefined, { recent })
  const listed = await mounted.request('/ontyra/last', { headers: { 'x-ontyra-client': 'tab' } })
  const body = await listed.json()
  assert.equal(body.ok, true)
  assert.deepEqual(body.sessions, [{ session: 's1', at: 5, parts: 1 }])
  const one = await (await mounted.request('/ontyra/last?session=s1')).json()
  assert.deepEqual(one.parts, [{ name: 'briefing', text: 'x' }])
})

test('an unknown path under the prefix is a 404, not a fallthrough', async () => {
  const res = await mount().request('/ontyra/nope')
  assert.equal(res.status, 404)
  assert.match((await res.json()).error, /no route/)
})

test('the memories route proxies the daemon and keeps only what the tab renders', async () => {
  const seen = []
  const client = {
    call: async (call) => {
      seen.push(call)
      return [
        {
          memory: 'memory:01a',
          kind: 'lesson',
          text: 'redact before storing',
          status: 'active',
          revision: 'memory:01a',
          confidence: 0.9,
          content: {
            scope: 'contextual',
            anchors: [{ uri: 'file://legion5/D:/repo', match: 'subtree' }],
            secret: 'not for the browser',
          },
        },
      ]
    },
  }
  const res = await mount(undefined, client).request('/ontyra/memories?limit=20&kind=lesson')
  assert.equal(res.status, 200)
  assert.equal(seen[0].method, 'GET')
  assert.match(seen[0].path, /^\/v1\/memories\?status=active&limit=20&kind=lesson$/)
  const body = await res.json()
  assert.equal(body.ok, true)
  assert.deepEqual(body.items, [
    {
      id: 'memory:01a',
      kind: 'lesson',
      text: 'redact before storing',
      status: 'active',
      revision: 'memory:01a',
      scope: 'contextual',
      confidence: 0.9,
      anchors: [{ uri: 'file://legion5/D:/repo', match: 'subtree' }],
    },
  ])
})

test('a daemon that is down is a state the tab renders, not a failed route', async () => {
  const client = {
    call: async () => {
      throw Object.assign(new Error('Ontyra is unreachable at http://127.0.0.1:8787/'), { status: 0 })
    },
  }
  const res = await mount(undefined, client).request('/ontyra/memories')
  assert.equal(res.status, 200)
  const body = await res.json()
  assert.equal(body.ok, false)
  assert.equal(body.status, 0)
  assert.match(body.error, /unreachable/)
})

test('the limit is clamped so one panel cannot ask for the whole library', async () => {
  let asked = ''
  const client = { call: async (call) => ((asked = call.path), []) }
  await mount(undefined, client).request('/ontyra/memories?limit=9999')
  assert.match(asked, /limit=500/)
})

test('the services route reports the seams this deployment composes', async () => {
  const res = await mount().request('/ontyra/services')
  assert.equal(res.status, 200)
  const body = await res.json()
  assert.deepEqual(Object.keys(body).sort(), [
    'approval',
    'credentials',
    'sessionProjections',
    'sessions',
    'skills',
    'userQuestions',
    'webServer',
    'workingDirectory',
  ])
  assert.equal(body.webServer, true, 'the route itself proves the web server is there')
})

test('a write without the tab header is refused, so a stray page cannot edit memory', async () => {
  const answer = await mount().write('/correct', { memory: 'memory:01a', fields: { text: 'x' } }, { 'x-ontyra-client': '' })
  assert.equal(answer.status, 400)
  assert.match(answer.body.error, /identify itself/)
})

test('correct proxies one overlay to the daemon and reports the proposal status', async () => {
  const seen = []
  const client = {
    call: async (call) => {
      seen.push(call)
      return { proposal: 'proposal:01a', status: 'committed', decision: 'auto_commit' }
    },
  }
  const answer = await mount(undefined, client).write('/correct', {
    memory: 'memory:01a',
    fields: { text: 'shorter' },
    reason: 'typo',
  })
  assert.equal(answer.status, 200)
  assert.equal(seen[0].method, 'POST')
  assert.equal(seen[0].path, '/v1/memories/01a/correct')
  assert.deepEqual(seen[0].body, { fields: { text: 'shorter' }, reason: 'typo' })
  assert.equal(answer.body.ok, true)
  assert.equal(answer.body.result.status, 'committed')
})

test('forget proxies a uri sweep and names what a pending proposal means', async () => {
  const seen = []
  const client = {
    call: async (call) => {
      seen.push(call)
      return { proposals: ['proposal:01b'], forgotten: ['memory:02b'], status: 'pending' }
    },
  }
  const answer = await mount(undefined, client).write('/forget', { uri: 'file://legion5/D:/repo', reason: 'moved on' })
  assert.equal(answer.status, 200)
  assert.equal(seen[0].path, '/v1/memories/forget')
  assert.deepEqual(seen[0].body, { uri: 'file://legion5/D:/repo', reason: 'moved on' })
  assert.equal(answer.body.result.status, 'pending')
  assert.deepEqual(answer.body.result.forgotten, ['memory:02b'])
})

test('a write that is missing its subject says what it needs', async () => {
  const empty = await mount().write('/correct', { fields: { text: 'x' } })
  assert.equal(empty.status, 400)
  assert.match(empty.body.error, /memory id/)
  const noFields = await mount().write('/correct', { memory: 'memory:01a', fields: {} })
  assert.equal(noFields.status, 400)
  assert.match(noFields.body.error, /fields that change/)
  const noTarget = await mount().write('/forget', { reason: 'x' })
  assert.equal(noTarget.status, 400)
  assert.match(noTarget.body.error, /memory or a uri/)
  const broken = await mount().write('/forget', 'not json')
  assert.equal(broken.status, 400)
  assert.match(broken.body.error, /JSON body/)
  const wrongType = await mount().write('/forget', { uri: 'file:///x' }, { 'content-type': 'text/plain' })
  assert.equal(wrongType.status, 400)
  assert.match(wrongType.body.error, /JSON body/)
})

test('a refused write keeps the daemon status so the tab can explain it', async () => {
  const client = {
    call: async () => {
      throw Object.assign(new Error('proposal already decided'), { status: 409 })
    },
  }
  const answer = await mount(undefined, client).write('/correct', { memory: 'memory:01a', fields: { text: 'x' } })
  assert.equal(answer.status, 400)
  assert.equal(answer.body.status, 409)
  assert.match(answer.body.error, /already decided/)
})
