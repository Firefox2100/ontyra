/**
 * The bundled skill: its frontmatter parses, and it names the tools this plugin actually ships.
 *
 * A skill that drifts from the tool surface is worse than no skill: the model reads a name that
 * does not exist and calls it. So this checks both the parser and the cross-reference.
 */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { parseSkill } from '../lib/skill.js'
import { LOCAL_ONLY, toolsFor } from '../lib/tools.js'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const source = readFileSync(join(root, 'skills', 'ontyra-memory', 'SKILL.md'), 'utf8')

test('the skill has a name and a description', () => {
  const skill = parseSkill(source)
  assert.equal(skill.name, 'ontyra-memory')
  assert.match(skill.description, /ontyra/i)
  assert.ok(skill.body.startsWith('#'), 'the body keeps its heading')
  assert.ok(!skill.body.includes('name: ontyra-memory'), 'the frontmatter is stripped')
})

test('frontmatter is parsed line by line', () => {
  const skill = parseSkill('---\nname: demo\ndescription: "Quoted: description"\n---\n\nBody text\n')
  assert.deepEqual(skill, { name: 'demo', description: 'Quoted: description', body: 'Body text' })
})

test('a file without frontmatter keeps its text and falls back to the default name', () => {
  const skill = parseSkill('# Just markdown\n')
  assert.deepEqual(skill, { name: 'ontyra-memory', description: '', body: '# Just markdown' })
})

test('every tool the skill names is one the plugin registers', () => {
  const registered = new Set([
    ...toolsFor('lite'),
    ...toolsFor('full'),
    ...[...LOCAL_ONLY].map((name) => ({ name })),
  ].map((tool) => tool.name))
  const named = new Set(source.match(/ontyra_[a-z_]+/g) ?? [])
  assert.ok(named.size > 0, 'the skill names tools')
  for (const name of named) assert.ok(registered.has(name), `${name} is not a registered tool`)
})
