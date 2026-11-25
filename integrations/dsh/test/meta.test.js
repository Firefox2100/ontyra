/**
 * The plugin's manifest metadata: what the Plugins page shows before anything is configured.
 *
 * DSH reads the title and description from `locale/<language>.json` next to the package, through
 * the `./locale/*.json` export, and falls back to the package name when `locale/en.json` is
 * missing. A missing or empty entry here is not a crash — it is a plugin row with no words on it —
 * so the tests pin every field the page needs.
 */

import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const manifest = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'))
const localeDir = join(root, 'locale')
const LANGUAGES = ['de', 'en', 'es', 'fr', 'ja', 'pt', 'ru', 'zh']

/** Read and parse one locale file. */
function dictionary(language) {
  return JSON.parse(readFileSync(join(localeDir, `${language}.json`), 'utf8'))
}

test('the manifest carries the fields the Plugins page reads', () => {
  assert.equal(manifest.dsh.manifestVersion, 1)
  assert.ok(manifest.description.length > 0, 'the description is the fallback for the row')
  assert.equal(manifest.engines.dsh, '>=0.2.2-rc.1 <0.2.3', 'those are the only releases checked')
  assert.deepEqual(
    manifest.exports['./locale/*.json'],
    './locale/*.json',
    'the reader resolves every dictionary through the exports map',
  )
  assert.ok(manifest.files.includes('locale'), 'the dictionaries ship with the package')
  assert.ok(manifest.files.includes('assets'), 'the icon ships with the package')
  assert.ok(manifest.keywords.includes('dsh-plugin'), 'the gallery can find it')
})

test('the package declares no icon it does not ship', () => {
  if (manifest.icon === undefined) return
  const icon = join(root, manifest.icon)
  const size = statSync(icon).size
  assert.ok(size > 0 && size <= 256 * 1024, 'icons stay under the 256 KiB limit')
  assert.match(manifest.icon, /\.(svg|png|jpe?g|webp)$/i)
})

test('every language DSH ships has a dictionary', () => {
  const files = readdirSync(localeDir).filter((name) => name.endsWith('.json')).sort()
  assert.deepEqual(files, LANGUAGES.map((language) => `${language}.json`))
})

test('each dictionary names the plugin and says what it needs', () => {
  const english = dictionary('en')
  for (const language of LANGUAGES) {
    const meta = dictionary(language).meta
    assert.ok(meta, `${language} has a meta block`)
    assert.ok(typeof meta.title === 'string' && meta.title.trim().length > 0, `${language} title`)
    assert.ok(
      typeof meta.description === 'string' && meta.description.trim().length > 0,
      `${language} description`,
    )
    assert.ok(meta.title !== manifest.name, `${language} is more than the package name`)
    if (language !== 'en') {
      assert.notEqual(meta.title, english.meta.title, `${language} title is translated`)
      assert.notEqual(meta.description, english.meta.description, `${language} text is translated`)
    }
  }
})
