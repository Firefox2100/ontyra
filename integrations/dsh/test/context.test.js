/**
 * Anchors name the machine.
 *
 * A shared daemon that fills in `file:` hosts itself cannot tell two devices apart, so the plugin
 * says which one it is: this machine's name goes into every path anchor, and into anchors the model
 * writes by hand. These cases pin the rewriting, including the Windows two-slash form where the
 * drive letter looks like a host.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { fileUri, hostOf, qualifyFileUri, qualifyOps, recallContext } from '../lib/context.js'

const HOST = hostOf()

test('the host is a URI-safe name, never empty', () => {
  assert.match(HOST, /^[a-z0-9.-]+$/)
})

test('a Windows path becomes a file URI on this host', () => {
  assert.equal(fileUri('D:\\GitHub\\ontyra'), `file://${HOST}/D:/GitHub/ontyra`)
})

test('a POSIX path becomes a file URI on this host', () => {
  assert.equal(fileUri('/home/me/repo/'), `file://${HOST}/home/me/repo`)
})

test('a trailing separator does not leave a trailing slash', () => {
  assert.ok(!fileUri('/tmp/').endsWith('/'))
})

test('a host-less URI gets this machine', () => {
  assert.equal(qualifyFileUri('file:///D:/repo'), `file://${HOST}/D:/repo`)
})

test('the two-slash Windows form keeps its drive letter', () => {
  // `file://D:/repo` parses as host `d`: the drive letter must go back into the path.
  assert.equal(qualifyFileUri('file://D:/repo'), `file://${HOST}/D:/repo`)
})

test('localhost is replaced: it is what the daemon would have written', () => {
  assert.equal(qualifyFileUri('file://localhost/D:/repo'), `file://${HOST}/D:/repo`)
})

test('another machine name is replaced too: this process is the one that knows', () => {
  // A model that copied `ai-pc` out of a Tailscale device name, or out of some other memory,
  // would otherwise anchor the memory to a machine that never wrote it — silently dead.
  assert.equal(qualifyFileUri('file://otherbox/D:/repo'), `file://${HOST}/D:/repo`)
  assert.equal(qualifyFileUri('file://ai-pc/home/x/workspace'), `file://${HOST}/home/x/workspace`)
})

test('non-file URIs and non-strings pass through untouched', () => {
  assert.equal(qualifyFileUri('https://example.com/a'), 'https://example.com/a')
  assert.equal(qualifyFileUri('doi:10.1000/xyz'), 'doi:10.1000/xyz')
  assert.equal(qualifyFileUri(undefined), undefined)
})

test('the recall context carries the qualified cwd', () => {
  const context = recallContext({ cwd: 'D:\\GitHub\\ontyra', session: 's1' })
  assert.deepEqual(context.uris, [`file://${HOST}/D:/GitHub/ontyra`])
  assert.equal(context.session, 's1')
})

test('anchors a model wrote are qualified, and the argument is not mutated', () => {
  const ops = [
    { op: 'add', kind: 'lesson', fields: { text: 'x', anchors: [{ uri: 'file:///D:/repo', match: 'subtree' }] } },
    { op: 'revise', memory: 'memory:abc', fields: { anchors: [{ uri: 'file://localhost/D:/repo' }] } },
    { op: 'add', kind: 'fact', fields: { text: 'no anchors here' } },
    { op: 'retract', memory: 'memory:abc' },
  ]
  const out = qualifyOps(ops)
  assert.equal(out[0].fields.anchors[0].uri, `file://${HOST}/D:/repo`)
  assert.equal(out[1].fields.anchors[0].uri, `file://${HOST}/D:/repo`)
  assert.equal(ops[0].fields.anchors[0].uri, 'file:///D:/repo')
  assert.equal(out[2].fields, ops[2].fields)
  assert.equal(out[3], ops[3])
})
