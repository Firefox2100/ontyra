/**
 * The settings card's browser half, exercised without a browser.
 *
 * `client.js` only registers a lazy-CJS factory, so a test can stand in for the module system,
 * hand it a tiny React-shaped shim with a real hook store, and render the card against a fake
 * context. That catches what a headless run otherwise leaves to the user's eyes: one control per
 * setting, one credential field, an inert card when the namespace is absent, and a Save that
 * writes only the staged values.
 */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { DEFAULTS } from '../lib/settings.js'

const here = dirname(fileURLToPath(import.meta.url))

/** One render pass's hook slots, and whether a setter asked for another pass. */
const runtime = { slots: [], index: 0, pending: false }

/** A class component base: `isReactComponent` marks it for the mini renderer below. */
class Component {
  constructor(props) {
    this.props = props
    this.state = {}
  }

  setState(next) {
    this.state = { ...this.state, ...(typeof next === 'function' ? next(this.state) : next) }
    runtime.pending = true
  }
}
Component.prototype.isReactComponent = true

/** A React shim: elements plus the five hooks the card uses, with state across passes. */
const React = {
  Component,
  createElement(type, props, ...children) {
    return { type, props: { ...(props ?? {}), children: children.flat() } }
  },
  useMemo: (factory) => factory(),
  useCallback: (fn) => fn,
  useEffect: () => {},
  useSyncExternalStore: (_subscribe, read) => read(),
  useState(initial) {
    const slot = runtime.index++
    if (!(slot in runtime.slots)) {
      runtime.slots[slot] = typeof initial === 'function' ? initial() : initial
    }
    return [runtime.slots[slot], (next) => {
      runtime.slots[slot] = typeof next === 'function' ? next(runtime.slots[slot]) : next
      runtime.pending = true
    }]
  },
}

/**
 * Render an element, resolving function and class components the way React would.
 * @param {any} node - an element, an array, or text.
 * @returns {any} the rendered element tree.
 */
function renderNode(node) {
  if (node === null || node === undefined || typeof node !== 'object') return node
  if (Array.isArray(node)) return node.map(renderNode)
  const type = node.type
  if (typeof type === 'function') {
    if (type.prototype && type.prototype.isReactComponent) {
      const instance = new type(node.props)
      try {
        return renderNode(instance.render())
      } catch (error) {
        instance.state = { ...instance.state, ...(type.getDerivedStateFromError?.(error) ?? {}) }
        return renderNode(instance.render())
      }
    }
    return renderNode(type(node.props))
  }
  return { type, props: { ...node.props, children: renderNode(node.props.children ?? []) } }
}

/**
 * Render a component until its state settles.
 * @param {() => any} component - the card under test.
 * @returns {any} its last tree.
 */
function render(component) {
  let tree
  let passes = 0
  do {
    runtime.pending = false
    runtime.index = 0
    tree = renderNode(component())
    passes += 1
  } while (runtime.pending && passes < 10)
  return tree
}

/**
 * Load the browser half through a stand-in module system.
 * @returns {any} the exports the factory produced.
 */
function loadClientHalf() {
  const source = readFileSync(join(here, '..', 'lib', 'client.js'), 'utf8')
  /** @type {any} */
  let exported
  const scope = /** @type {any} */ (globalThis)
  const previous = scope.window
  scope.window = {
    __ModuleLoader__: {
      load: (spec) => {
        assert.equal(spec.id, 'dsh-ontyra-memory')
        exported = spec.factory((specifier) => {
          assert.equal(specifier, 'react')
          return React
        })
      },
    },
  }
  try {
    // The file is an ESM script that only touches `window`, so evaluating it is a plain run.
    new Function(source)()
  } finally {
    scope.window = previous
  }
  runtime.slots = []
  return exported
}

/**
 * A client context whose services record what the card asks for.
 * @param {Record<string, unknown>} stored - the field values the fake describe mirror serves.
 * @param {{ served?: boolean, writable?: boolean, error?: string | null }} [options] - mirror shape.
 */
function fakeContext(stored, options = {}) {
  const writes = []
  const unset = []
  const writable = options.writable ?? true
  const served = options.served ?? true
  const form = {
    getSnapshot: () => ({ status: 'ready', value: stored, base: {}, user: {}, revision: 1, writable }),
    subscribe: () => () => {},
    set: async (field, value) => {
      writes.push([field, value])
      return true
    },
    unset: async (field) => {
      unset.push(field)
      return true
    },
  }
  const mirror = {
    getSnapshot: () => ({
      status: served ? 'ready' : 'loading',
      view: served
        ? { namespaces: [{ ns: 'ontyra', value: stored, revision: 1 }], writable, hasDocument: false }
        : undefined,
      error: options.error ?? null,
    }),
    subscribe: () => () => {},
    ensure: async () => {},
  }
  /** @type {any[]} */
  const registered = []
  const ctx = {
    effect: (fn) => {
      const disposer = fn()
      return typeof disposer === 'function' ? disposer : () => {}
    },
    locale: { register: () => () => {}, bind: () => (key) => key },
    configForms: {
      get: () => form,
      describe: () => mirror,
      whileServed: (_namespaces, register) => register(),
    },
    slots: {
      inject: (_name, register) => register(),
      register: (options, component) => {
        registered.push({ options, component })
        return () => {}
      },
    },
    remote: {
      credentials: {
        describe: async () => ({ configured: false, writable: true }),
        set: async () => {},
        unset: async () => {},
      },
    },
  }
  return { ctx, form, writes, unset, registered, mirror }
}

/**
 * Flatten a rendered tree.
 * @param {any} node - an element, text, or array.
 * @returns {any[]} every element, depth first.
 */
function elements(node) {
  if (node === null || node === undefined || node === false) return []
  if (Array.isArray(node)) return node.flatMap(elements)
  if (typeof node !== 'object') return []
  return [node, ...elements(node.props?.children)]
}

/**
 * The text a node renders, whether the shim stored one child or several.
 * @param {any} node - an element.
 * @returns {string} its children joined.
 */
function labelOf(node) {
  const children = node?.props?.children
  if (Array.isArray(children)) return children.filter((child) => typeof child === 'string').join('')
  return typeof children === 'string' ? children : ''
}

/** Every field the card edits, keyed by the control id it renders. */
function controlIds(tree) {
  return elements(tree)
    .filter((node) => node.type === 'input' || node.type === 'select')
    .map((node) => node.props.id)
    .sort()
}

/**
 * The settings card among the registrations the client half makes.
 * @param {Array<{ options: any, component: any }>} registered - what the fake slot service recorded.
 * @returns {{ options: any, component: any }} the card registration.
 */
function cardOf(registered) {
  const card = registered.find((entry) => entry.options.name === 'settings.section')
  assert.ok(card, 'the settings card is registered')
  return card
}

test('the browser half registers the settings card and the Memory tab', () => {
  const exports = loadClientHalf()
  const { ctx, registered } = fakeContext({})
  // Every service the card touches must be declared: cordis throws on an undeclared read, and the
  // token buttons read `remote.credentials`.
  assert.deepEqual(exports.inject, ['slots', 'locale', 'configForms', 'remote', 'remote.credentials'])
  exports.apply(ctx)
  const card = cardOf(registered)
  assert.equal(card.options.id, 'dsh-ontyra-memory')
  assert.equal(card.options.locale, 'settings.ontyra')
  assert.equal(typeof card.component, 'function')
  const tab = registered.find((entry) => entry.options.name === 'conversation.view')
  assert.ok(tab, 'the Memory tab is registered on the conversation view row')
  assert.equal(tab.options.id, 'ontyra-memories')
  assert.equal(tab.options.order, 31)
  assert.equal(tab.options.locale, 'settings.ontyra')
  assert.equal(typeof tab.component, 'function')
  assert.equal(registered.length, 2)
})

test('the card renders one control per setting plus the token field', () => {
  const exports = loadClientHalf()
  const { ctx, registered } = fakeContext({
    baseUrl: 'http://127.0.0.1:8787',
    tokenRef: 'ONTYRA_TOKEN',
    harness: 'dsh',
    briefing: true,
    recall: true,
    budgetTokens: 1200,
    timeoutMs: 1500,
    tools: true,
    toolsProfile: 'lite',
    episodes: true,
    capture: false,
    captureEvery: 10,
    subagents: false,
  })
  exports.apply(ctx)
  const tree = render(cardOf(registered).component)
  const inputs = elements(tree).filter((node) => node.type === 'input')
  const byType = (type) => inputs.filter((node) => node.props.type === type)
  assert.equal(byType('checkbox').length, 6, 'boolean settings')
  assert.equal(byType('number').length, 4, 'numeric settings')
  assert.equal(byType('text').length, 3, 'text settings')
  assert.equal(byType('password').length, 1, 'the bearer token')
  assert.equal(controlIds(tree).length, 15)
  const baseUrl = inputs.find((node) => node.props.id === 'ontyra-setting-baseUrl')
  assert.equal(baseUrl.props.value, 'http://127.0.0.1:8787', 'stored values reach the controls')
  const selects = elements(tree).filter((node) => node.type === 'select')
  assert.equal(selects.length, 1, 'the tool profile is a dropdown')
  assert.equal(selects[0].props.id, 'ontyra-setting-toolsProfile')
  assert.equal(selects[0].props.value, 'lite', 'the dropdown shows the stored profile')
  const options = elements(selects[0]).filter((node) => node.type === 'option')
  assert.deepEqual(options.map((node) => node.props.value), ['lite', 'full'])
  const buttons = elements(tree).filter((node) => node.type === 'button')
  assert.ok(buttons.some((node) => labelOf(node) === 'save'), 'the form has a save button')
  assert.ok(buttons.some((node) => labelOf(node) === 'saveToken'), 'the token has its own save')
})

test('the card carries every language the harness ships', () => {
  const exports = loadClientHalf()
  const languages = Object.keys(exports.dictionaries).sort()
  assert.deepEqual(languages, ['de', 'en', 'es', 'fr', 'ja', 'pt', 'ru', 'zh'])
  const reference = Object.keys(exports.dictionaries.en).sort()
  for (const language of languages) {
    assert.deepEqual(
      Object.keys(exports.dictionaries[language]).sort(),
      reference,
      `${language} must translate exactly the English keys`,
    )
  }
})

test('the card is inert when the namespace is not served', () => {
  const exports = loadClientHalf()
  const { ctx, registered } = fakeContext({}, { served: true })
  ctx.configForms.describe = () => ({
    getSnapshot: () => ({
      status: 'ready',
      view: { namespaces: [{ ns: 'other-plugin', value: {}, revision: 1 }], writable: true, hasDocument: false },
      error: null,
    }),
    subscribe: () => () => {},
    ensure: async () => {},
  })
  exports.apply(ctx)
  const tree = render(cardOf(registered).component)
  assert.equal(elements(tree).filter((node) => node.type === 'input').length, 0)
  assert.equal(elements(tree).filter((node) => node.type === 'button').length, 0)
  const text = elements(tree).map((node) => labelOf(node)).join(' ')
  assert.match(text, /unavailable/)
  assert.match(text, /other-plugin/, 'the message names the served namespaces')
})

test('the card renders while the settings mirror has no answer yet', () => {
  const exports = loadClientHalf()
  const { ctx, registered } = fakeContext({}, { served: false, error: 'not connected' })
  exports.apply(ctx)
  const tree = render(cardOf(registered).component)
  assert.equal(elements(tree).filter((node) => node.type === 'input').length, 0)
  assert.match(elements(tree).map((node) => labelOf(node)).join(' '), /loading.*not connected/)
})

test('saving writes only the staged field', async () => {
  const exports = loadClientHalf()
  const { ctx, registered, writes } = fakeContext({ baseUrl: 'http://127.0.0.1:8787', captureEvery: 10 })
  exports.apply(ctx)
  const Card = cardOf(registered).component
  const number = elements(render(Card)).find((node) => node.props.id === 'ontyra-setting-captureEvery')
  number.props.onChange({ target: { value: '25' } })
  const save = elements(render(Card)).find((node) => node.type === 'button' && labelOf(node) === 'save')
  await save.props.onClick()
  assert.deepEqual(writes, [['captureEvery', 25]])
})

test('a rejected number leaves the field staged and says why', async () => {
  const exports = loadClientHalf()
  const { ctx, registered, writes } = fakeContext({ captureEvery: 10 })
  exports.apply(ctx)
  const Card = cardOf(registered).component
  const number = elements(render(Card)).find((node) => node.props.id === 'ontyra-setting-captureEvery')
  number.props.onChange({ target: { value: '2.5' } })
  const save = elements(render(Card)).find((node) => node.type === 'button' && labelOf(node) === 'save')
  await save.props.onClick()
  assert.deepEqual(writes, [])
  assert.ok(elements(render(Card)).some((node) => labelOf(node) === 'invalidNumber'))
})

test('the card edits exactly the fields the host declares', () => {
  const source = readFileSync(join(here, '..', 'lib', 'client.js'), 'utf8')
  const edited = [...source.matchAll(/\{\s*key: '([A-Za-z]+)'/g)].map((match) => match[1])
  assert.deepEqual([...edited].sort(), [...Object.keys(DEFAULTS)].sort())
})

test('the Memory tab reads its own route and renders what came back', async () => {
  const exports = loadClientHalf()
  const t = (key) => key
  const asked = []
  const request = async (url) => {
    asked.push(url)
    return {
      json: async () => ({
        ok: true,
        items: [{ id: 'memory:01a', kind: 'directive', text: 'Never force-push to main', status: 'active', anchors: [] }],
      }),
    }
  }
  const state = await exports.fetchMemories(t, request)
  assert.equal(asked[0], '/ontyra/memories?limit=200')
  assert.equal(state.status, 'ready')
  assert.equal(state.items.length, 1)
  assert.equal(state.items[0].kind, 'directive')
})

test('a failing route becomes an error the panel can show', async () => {
  const exports = loadClientHalf()
  const t = (key) => key
  const refused = await exports.fetchMemories(t, async () => ({ json: async () => ({ ok: false, error: 'Ontyra is unreachable' }) }))
  assert.equal(refused.status, 'error')
  assert.match(refused.error, /unreachable/)
  const broken = await exports.fetchMemories(t, async () => {
    throw new Error('connection refused')
  })
  assert.equal(broken.status, 'error')
  assert.match(broken.error, /connection refused/)
})

test('the Memory tab renders its heading and a loading line before data arrives', () => {
  const exports = loadClientHalf()
  const { ctx, registered } = fakeContext({})
  exports.apply(ctx)
  const tab = registered.find((entry) => entry.options.name === 'conversation.view')
  const tree = render(tab.component)
  const buttons = elements(tree).filter((node) => node.type === 'button')
  assert.equal(buttons.length, 1, 'a refresh button')
  assert.match([].concat(buttons[0].props.children).join(''), /refresh/)
})

test('an edit in the tab is a correct proposal carrying only the new text', async () => {
  const exports = loadClientHalf()
  const t = (key) => ({ editReason: 'edited in the Memory tab' })[key] ?? key
  const seen = []
  const request = async (url, init) => {
    seen.push({ url, init })
    return { json: async () => ({ ok: true, result: { status: 'pending' } }) }
  }
  const result = await exports.correctMemory(t, { memory: 'memory:01a', text: 'shorter rule' }, request)
  assert.equal(seen[0].url, '/ontyra/correct')
  assert.equal(seen[0].init.method, 'POST')
  assert.equal(seen[0].init.headers['x-ontyra-client'], 'tab')
  assert.deepEqual(JSON.parse(seen[0].init.body), {
    memory: 'memory:01a',
    fields: { text: 'shorter rule' },
    reason: 'edited in the Memory tab',
  })
  assert.equal(result.ok, true)
  assert.equal(result.pending, true, 'a review policy still has to approve it')
})

test('a refusal from the route reaches the tab as the reason it failed', async () => {
  const exports = loadClientHalf()
  const t = (key) => key
  const refused = await exports.correctMemory(t, { memory: 'memory:01a', text: 'x' }, async () => ({
    json: async () => ({ ok: false, error: 'proposal already decided' }),
  }))
  assert.equal(refused.ok, false)
  assert.match(refused.error, /already decided/)
  const broken = await exports.forgetMemory(t, { memory: 'memory:01a' }, async () => {
    throw new Error('connection refused')
  })
  assert.equal(broken.ok, false)
  assert.match(broken.error, /connection refused/)
})

test('forget in the tab sends the memory it is retiring', async () => {
  const exports = loadClientHalf()
  const t = (key) => ({ forgotReason: 'forgotten from the Memory tab' })[key] ?? key
  const seen = []
  const result = await exports.forgetMemory(t, { memory: 'memory:01b' }, async (url, init) => {
    seen.push({ url, init })
    return { json: async () => ({ ok: true, result: { status: 'committed' } }) }
  })
  assert.equal(seen[0].url, '/ontyra/forget')
  assert.deepEqual(JSON.parse(seen[0].init.body), { memory: 'memory:01b', reason: 'forgotten from the Memory tab' })
  assert.equal(result.ok, true)
  assert.equal(result.pending, false)
})

test('the connection probe reports what the host half actually saw', async () => {
  const exports = loadClientHalf()
  const t = (key) => key
  const healthy = await exports.testConnection(t, async () => ({ json: async () => ({ ok: true, daemon: { ok: true, version: '0.1.dev1' } }) }))
  assert.deepEqual(healthy, { ok: true, version: '0.1.dev1', error: '' })
  const down = await exports.testConnection(t, async () => ({ json: async () => ({ ok: true, daemon: { ok: false, error: 'unreachable', status: 0 } }) }))
  assert.equal(down.ok, false)
  assert.match(down.error, /unreachable/)
  const broken = await exports.testConnection(t, async () => { throw new Error('offline') })
  assert.equal(broken.ok, false)
  assert.match(broken.error, /offline/)
})

test('the queue is read and replayed through the host half', async () => {
  const exports = loadClientHalf()
  const t = (key) => ({ unreachable: 'not reachable' })[key] ?? key
  const seen = []
  const queue = await exports.fetchOutbox(async (url, init) => {
    seen.push({ url, method: init?.method ?? 'GET' })
    return { json: async () => ({ ok: true, count: 2, items: [{ tool: 'ontyra_remember' }] }) }
  })
  assert.equal(seen[0].url, '/ontyra/outbox')
  assert.equal(queue.count, 2)
  const sent = await exports.retryOutbox(t, async (url, init) => {
    seen.push({ url, method: init?.method })
    return { json: async () => ({ ok: true, sent: 1, left: 1 }) }
  })
  assert.equal(seen[1].method, 'POST')
  assert.equal(sent.sent, 1)
  assert.equal(sent.left, 1)
  const refused = await exports.retryOutbox(t, async () => ({ json: async () => ({ ok: false, error: 'still down' }) }))
  assert.equal(refused.ok, false)
  assert.match(refused.error, /still down/)
})

test('a row detail is fetched by id and a refusal stays a refusal', async () => {
  const exports = loadClientHalf()
  const seen = []
  const ok = await exports.fetchDetail('memory:01a', async (url) => {
    seen.push(url)
    return { json: async () => ({ ok: true, memory: { memory: 'memory:01a', evidence: [] } }) }
  })
  assert.equal(seen[0], '/ontyra/memories/memory%3A01a')
  assert.equal(ok.ok, true)
  assert.equal(ok.memory.memory, 'memory:01a')
  const refused = await exports.fetchDetail('memory:01b', async () => ({ json: async () => ({ ok: false, error: 'gone' }) }))
  assert.equal(refused.ok, false)
  assert.match(refused.error, /gone/)
})

test('the settings card offers a connection test beside the saved target', () => {
  const exports = loadClientHalf()
  const { ctx, registered } = fakeContext({
    baseUrl: 'http://127.0.0.1:8787',
    tokenRef: 'ONTYRA_TOKEN',
    harness: 'dsh',
    briefing: true,
    recall: true,
    budgetTokens: 1200,
    timeoutMs: 1500,
    toolTimeoutMs: 20000,
    tools: true,
    toolsProfile: 'lite',
    episodes: true,
    capture: false,
    captureEvery: 10,
    subagents: false,
  })
  exports.apply(ctx)
  const tree = render(cardOf(registered).component)
  const buttons = elements(tree).filter((node) => node.type === 'button')
  assert.ok(buttons.some((node) => labelOf(node) === 'testConnection'), 'the card can probe the daemon')
  assert.ok(buttons.some((node) => labelOf(node) === 'save'), 'and still saves settings')
})