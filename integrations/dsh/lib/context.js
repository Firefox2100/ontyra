/**
 * What the plugin tells the daemon about where the agent is working: the same anchors and
 * signals `integrations/claude-code/hooks/ontyra-hook.sh` reports, computed from the Host's own
 * working directory instead of a shell.
 *
 * Over-reporting is safe: a concept the ontology does not know comes back in `unresolved`.
 */

import { existsSync } from 'node:fs'
import { hostname, platform } from 'node:os'
import { join } from 'node:path'

/**
 * A `file:` URI for a host path. The daemon canonicalises it with its machine name as the host,
 * so the path (and its ancestors) become the anchors a memory can be scoped to.
 * @param {string} path - absolute path, POSIX or Windows.
 * @returns {string} `file:///…` with forward slashes.
 */
export function fileUri(path) {
  const posix = path.replace(/\\/g, '/').replace(/\/+$/, '')
  const rooted = posix.startsWith('/') ? posix : `/${posix}`
  return `file://${rooted}`
}

/** The OS concept the ontology knows for this platform. */
function platformConcept() {
  if (platform() === 'win32') return 'windows'
  if (platform() === 'darwin') return 'macos'
  return 'linux'
}

/** Project files that show what a directory is written in, in the hook's order. */
const PROJECT_FILES = [
  [['pyproject.toml', 'requirements.txt'], 'python'],
  [['package.json'], 'nodejs'],
  [['Cargo.toml'], 'rust'],
  [['go.mod'], 'go'],
  [['Dockerfile', 'compose.yaml', 'compose.yml'], 'docker'],
]

/**
 * The concepts a directory shows.
 * @param {string} dir - directory to look in.
 * @returns {Array<{ concept: string }>} signals, most specific first.
 */
export function projectSignals(dir) {
  /** @type {Array<{ concept: string }>} */
  const signals = [{ concept: platformConcept() }]
  for (const [files, concept] of PROJECT_FILES) {
    if (files.some((file) => existsSync(join(dir, file)))) signals.push({ concept })
  }
  if (existsSync(join(dir, '.git'))) signals.push({ concept: 'git' })
  return signals
}

/**
 * The `context` body field for a recall, check or briefing call.
 * @param {{ cwd?: string, session?: string | null }} where - the Host's working directory and session.
 * @returns {{ uris: string[], signals: Array<{ concept: string }>, session: string | null }} the context.
 */
export function recallContext({ cwd, session }) {
  if (cwd === undefined || cwd === '') {
    return { uris: [], signals: [{ concept: platformConcept() }], session: session ?? null }
  }
  return {
    uris: [fileUri(cwd)],
    signals: projectSignals(cwd),
    session: session ?? null,
  }
}

/** The machine name, for the `X-Ontyra-*` headers' provenance only. */
export function machineName() {
  return hostname()
}
