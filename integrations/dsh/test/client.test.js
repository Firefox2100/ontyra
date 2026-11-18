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

test('the browser half registers one card in the bundle slot', () => {
  const exports = loadClientHalf()
  const { ctx, registered } = fakeContext({})
  // Every service the card touches must be declared: cordis throws on an undeclared read, and the
  // token buttons read `remote.credentials`.
  assert.deepEqual(exports.inject, ['slots', 'locale', 'configForms', 'remote', 'remote.credentials'])
  exports.apply(ctx)
  assert.equal(registered.length, 1)
  assert.equal(registered[0].options.name, 'settings.section')
  assert.equal(registered[0].options.id, 'dsh-ontyra-memory')
  assert.equal(registered[0].options.locale, 'settings.ontyra')
  assert.equal(typeof registered[0].component, 'function')
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
    episodes: false,
    capture: false,
    captureEvery: 10,
    subagents: false,
  })
  exports.apply(ctx)
  const tree = render(registered[0].component)
  const inputs = elements(tree).filter((node) => node.type === 'input')
  const byType = (type) => inputs.filter((node) => node.props.type === type)
  assert.equal(byType('checkbox').length, 6, 'boolean settings')
  assert.equal(byType('number').length, 3, 'numeric settings')
  assert.equal(byType('text').length, 3, 'text settings')
  assert.equal(byType('password').length, 1, 'the bearer token')
  assert.equal(controlIds(tree).length, 14)
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
  const tree = render(registered[0].component)
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
  const tree = render(registered[0].component)
  assert.equal(elements(tree).filter((node) => node.type === 'input').length, 0)
  assert.match(elements(tree).map((node) => labelOf(node)).join(' '), /loading.*not connected/)
})

test('saving writes only the staged field', async () => {
  const exports = loadClientHalf()
  const { ctx, registered, writes } = fakeContext({ baseUrl: 'http://127.0.0.1:8787', captureEvery: 10 })
  exports.apply(ctx)
  const Card = registered[0].component
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
  const Card = registered[0].component
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
