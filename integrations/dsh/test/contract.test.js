/**
 * The tool surface has to stay the daemon's: same tools, same descriptions, same required
 * arguments as `integrations/contract/mcp-tools-lite.json`. This is the JS half of the contract
 * check that `scripts/export_contract.py --check` runs on the Python side.
 */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

import { contextArg, TOOLS, toolVisible, toolsFor } from '../lib/tools.js'

const contract = JSON.parse(
  readFileSync(new URL('../../contract/mcp-tools-lite.json', import.meta.url), 'utf8'),
)

/** The lite profile the plugin mirrors: everything but the ontology-write tools. */
const LITE = toolsFor('lite').map((tool) => tool.name)

test('lite registers the daemon’s lite tools, prefixed', () => {
  assert.deepEqual(LITE, [
    'ontyra_recall',
    'ontyra_check',
    'ontyra_briefing',
    'ontyra_ask',
    'ontyra_get',
    'ontyra_remember',
    'ontyra_feedback',
    'ontyra_consult',
    'ontyra_check_source',
    'ontyra_pending_proposals',
    'ontyra_amend',
  ])
})

test('full adds the ontology tools', () => {
  assert.deepEqual(toolsFor('full').map((tool) => tool.name), [...LITE, 'ontyra_concepts', 'ontyra_propose_ontology_change'])
})

test('the live settings decide each step’s tool surface', () => {
  // The plugin registers every tool once; these predicates are what a settings change reaches.
  assert.equal(toolVisible({ tools: true, toolsProfile: 'lite' }, 'ontyra_recall'), true)
  assert.equal(toolVisible({ tools: true, toolsProfile: 'lite' }, 'ontyra_concepts'), false)
  assert.equal(toolVisible({ tools: true, toolsProfile: 'full' }, 'ontyra_concepts'), true)
  assert.equal(toolVisible({ tools: false, toolsProfile: 'full' }, 'ontyra_recall'), false)
  assert.equal(toolVisible({ tools: true }, 'ontyra_concepts'), false)
})

test('every tool’s description is the daemon’s own', () => {
  for (const tool of TOOLS) {
    const remote = contract.find((entry) => `ontyra_${entry.name}` === tool.name)
    assert.ok(remote, `${tool.name} is not in the MCP contract`)
    assert.equal(tool.description, remote.description, `${tool.name} description drifted`)
  }
})

test('required parameters match the daemon’s required arguments', () => {
  for (const tool of TOOLS) {
    const remote = contract.find((entry) => `ontyra_${entry.name}` === tool.name)
    const schema = remote.input_schema
    const nested = Object.values(schema.properties ?? {})
    // The MCP tools take one request model (or two scalars); the native tools take it flattened.
    const required = nested.length === 1 && nested[0].type === 'object'
      ? nested[0].required ?? []
      : schema.required ?? []
    const ours = Object.entries(tool.parameters)
      .filter(([, spec]) => /** @type {{ required?: boolean }} */ (spec).required === true)
      .map(([key]) => key)
    assert.deepEqual(ours.sort(), [...required].sort(), `${tool.name} required arguments drifted`)
  }
})

test('no tool invents an argument the daemon does not accept', () => {
  for (const tool of TOOLS) {
    const remote = contract.find((entry) => `ontyra_${entry.name}` === tool.name)
    const schema = remote.input_schema
    const properties = schema.properties ?? {}
    const keys = Object.keys(properties)
    // One request model, or the daemon's flat scalars; the native tools take either flattened.
    const inner = keys.length === 1 && properties[keys[0]].type === 'object'
      ? properties[keys[0]].properties ?? {}
      : properties
    const accepted = new Set(Object.keys(inner))
    for (const declared of Object.keys(tool.parameters)) {
      assert.ok(accepted.has(declared), `${tool.name} declares "${declared}", which the daemon does not take`)
    }
  }
})

test('the context argument is accepted as an object or a JSON string', () => {
  assert.deepEqual(contextArg({ uris: ['file:///D:/x'] }), { uris: ['file:///D:/x'] })
  assert.deepEqual(contextArg('{"uris":["file:///D:/x"]}'), { uris: ['file:///D:/x'] })
  assert.equal(contextArg(undefined), undefined)
  assert.equal(contextArg(null), undefined)
  assert.equal(contextArg('not json'), undefined)
  assert.equal(contextArg('42'), undefined)
})

test('bare signal labels become the objects the daemon validates', () => {
  assert.deepEqual(contextArg({ signals: ["windows", { concept: "python", value: "3.12" }] }), {
    signals: [{ concept: "windows" }, { concept: "python", value: "3.12" }],
  })
  assert.deepEqual(contextArg(JSON.stringify({ uris: [], signals: ["git"] })), { uris: [], signals: [{ concept: "git" }] })
})

/**
 * The routes the daemon actually serves under `/v1` (`src/ontyra/transports/rest/routers/`).
 * A tool that calls anything else answers 404, which only a live daemon would reveal.
 */
const ROUTES = {
  ontyra_recall: ['POST', '/v1/recall'],
  ontyra_check: ['POST', '/v1/check'],
  ontyra_briefing: ['POST', '/v1/briefing'],
  ontyra_ask: ['POST', '/v1/ask'],
  ontyra_get: ['GET', /^\/v1\/memories\/[^/]+\/detail(\?.*)?$/],
  ontyra_remember: ['POST', '/v1/proposals'],
  ontyra_feedback: ['POST', '/v1/feedback'],
  ontyra_consult: ['POST', '/v1/consult'],
  ontyra_check_source: ['POST', '/v1/sources/check'],
  ontyra_pending_proposals: ['GET', /^\/v1\/proposals\?limit=\d+$/],
  ontyra_amend: ['POST', /^\/v1\/proposals\/[^/]+\/amend$/],
  ontyra_concepts: ['GET', /^\/v1\/concepts\?term=[^&]+$/],
  ontyra_propose_ontology_change: ['POST', '/v1/ontology/changes'],
}

test('every tool calls a route the daemon serves', () => {
  const args = {
    ontyra_recall: { query: 'x' },
    ontyra_check: { intent: 'x' },
    ontyra_briefing: {},
    ontyra_ask: { question: 'conflicts' },
    ontyra_get: { memory: 'abc' },
    ontyra_remember: { ops: [] },
    ontyra_feedback: { memory: 'abc', query: 'x', label: 'helpful', trace: 'def' },
    ontyra_consult: { query: 'x', topic: 'esp32' },
    ontyra_check_source: { uri: 'file:///a', state: 'changed' },
    ontyra_pending_proposals: {},
    ontyra_amend: { proposal: 'abc', request: { ops: [], why: 'x' } },
    ontyra_concepts: { term: 'python' },
    ontyra_propose_ontology_change: { change: {}, rationale: 'x' },
  }
  for (const tool of TOOLS) {
    const [method, path] = ROUTES[tool.name]
    const call = tool.request(args[tool.name], {})
    assert.equal(call.method, method, `${tool.name} method drifted`)
    if (typeof path === 'string') assert.equal(call.path, path, `${tool.name} path drifted`)
    else assert.match(call.path, path, `${tool.name} path drifted`)
  }
})

test('feedback names the memory and the recall trace as records', () => {
  const feedback = TOOLS.find((tool) => tool.name === 'ontyra_feedback')
  const body = feedback.request({ memory: 'abc', query: 'q', label: 'wrong', trace: 'def' }, {}).body
  assert.equal(body.memory, 'memory:abc')
  assert.equal(body.trace, 'recall_trace:def')
  assert.equal(feedback.request({ memory: 'memory:abc', query: 'q', label: 'wrong', trace: 'recall_trace:x' }, {}).body.trace, 'recall_trace:x')
})

test('ask sends record ids in the bodies and bare ids in the paths', () => {
  const ask = TOOLS.find((tool) => tool.name === 'ontyra_ask')
  const body = /** @type {{ body: { memory: string, trace: string } }} */ (
    ask.request({ question: 'applies_here', memory: 'abc', trace: 'def' }, {})
  ).body
  assert.equal(body.memory, 'memory:abc')
  assert.equal(body.trace, 'recall_trace:def')
  const given = /** @type {{ body: { memory: string } }} */ (
    ask.request({ question: 'applies_here', memory: 'memory:abc' }, {})
  ).body
  assert.equal(given.memory, 'memory:abc')

  const get = TOOLS.find((tool) => tool.name === 'ontyra_get')
  const call = get.request({ memory: 'memory:abc' }, {})
  assert.equal(call.path, '/v1/memories/abc/detail')
})

test('a table prefix the model copied is replaced, never doubled', () => {
  const ask = TOOLS.find((tool) => tool.name === 'ontyra_ask')
  const trace = (value) => /** @type {{ body: { trace: string } }} */ (
    ask.request({ question: 'explain', trace: value }, {})
  ).body.trace
  // `ontyra_recall` shows the trace as `recall_trace:abc`; `ontyra_feedback` calls it `trace`, so
  // both forms, and the wrong one, have to end up naming the table the daemon can look up.
  assert.equal(trace('recall_trace:01a'), 'recall_trace:01a')
  assert.equal(trace('trace:01a'), 'recall_trace:01a')
  assert.equal(trace('01a'), 'recall_trace:01a')

  const get = TOOLS.find((tool) => tool.name === 'ontyra_get')
  assert.equal(get.request({ memory: 'memory:01a' }, {}).path, '/v1/memories/01a/detail')

  const amend = TOOLS.find((tool) => tool.name === 'ontyra_amend')
  const call = amend.request({ proposal: 'proposal:01a', request: { ops: [], why: 'x' } }, {})
  assert.equal(call.path, '/v1/proposals/01a/amend')
})

test('the remember ops list the daemon’s per-kind fields', () => {
  const remote = contract.find((entry) => entry.name === 'remember')
  let guide = ''
  const walk = (node) => {
    if (typeof node === 'string' && node.includes('directive (') && node.includes('fact (')) guide = node
    else if (node && typeof node === 'object') Object.values(node).forEach(walk)
  }
  walk(remote.inputSchema ?? remote.input_schema)
  const lines = guide.split('\n').filter((line) => /^(fact|directive|decision|event|procedure|task|lesson) \(/.test(line))
  assert.equal(lines.length, 7)
  const ops = TOOLS.find((tool) => tool.name === 'ontyra_remember').parameters.ops.description
  for (const line of lines) assert.ok(ops.includes(line), `ops description lacks: ${line}`)
})
