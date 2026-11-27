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
 * This machine's name as a URI host.
 *
 * `file:` URIs without a host are canonicalised by the daemon with *its own* machine name, which
 * makes equal paths on two devices collide in one shared daemon. Sending the name here keeps them
 * apart. Host names may contain characters a URI host cannot (`_`, spaces), so they are folded.
 * @returns {string} lowercase host name, never empty.
 */
export function hostOf() {
  const cleaned = hostname()
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9.-]+/g, '-')
    .replace(/^-+|-+$/g, '')
  return cleaned === '' ? 'localhost' : cleaned
}

/**
 * A `file:` URI for a host path, qualified with this machine's name.
 *
 * The daemon canonicalises the result with that host, so the path (and its ancestors) become
 * anchors scoped to this device: a memory anchored here does not apply to the same-looking path on
 * another machine (a Windows workspace and a Linux one, say).
 * @param {string} path - absolute path, POSIX or Windows.
 * @returns {string} `file://<host>/…` with forward slashes.
 */
export function fileUri(path) {
  const posix = path.replace(/\\/g, '/').replace(/\/+$/, '')
  const rooted = posix.startsWith('/') ? posix : `/${posix}`
  return `file://${hostOf()}${rooted}`
}

/**
 * Rewrite a `file:` URI so it names this machine.
 *
 * A path anchor only means something relative to one machine, and this process is the only side
 * that knows which: so every `file:` URI is re-hosted, whatever host it arrived with. A model that
 * typed `file:///D:/repo`, or copied a Tailscale device name out of some other memory, therefore
 * cannot anchor a memory to a machine that never wrote it. Other schemes are left alone.
 * @param {unknown} uri - the URI as the model wrote it.
 * @returns {unknown} the qualified URI, or the input when it needs none.
 */
export function qualifyFileUri(uri) {
  if (typeof uri !== 'string') return uri
  // `file://D:/repo`: the drive letter was read as the host; it belongs to the path.
  const drive = /^file:\/\/([a-z]):(\/.*)?$/i.exec(uri)
  if (drive !== null) return `file://${hostOf()}/${drive[1]}:${drive[2] ?? '/'}`
  const match = /^file:\/\/([^/?#]*)([^?#]*)/i.exec(uri)
  if (match === null) return uri
  const path = match[2]
  const rooted = path.startsWith('/') ? path : `/${path}`
  return `file://${hostOf()}${rooted}`
}

/**
 * Qualify the anchors of the operations a model asked to send.
 *
 * Only `add` and `revise` carry anchors. The ops are cloned so the caller's argument is untouched.
 * @param {unknown} ops - the `ops` argument of `ontyra_remember`.
 * @returns {unknown} the ops with qualified anchor URIs.
 */
export function qualifyOps(ops) {
  if (!Array.isArray(ops)) return ops
  return ops.map((op) => {
    if (op === null || typeof op !== 'object') return op
    const fields = /** @type {{ fields?: unknown }} */ (op).fields
    if (fields === null || typeof fields !== 'object') return op
    const anchors = /** @type {{ anchors?: unknown }} */ (fields).anchors
    if (!Array.isArray(anchors)) return op
    const qualified = anchors.map((anchor) =>
      anchor !== null && typeof anchor === 'object'
        ? { .../** @type {Record<string, unknown>} */ (anchor), uri: qualifyFileUri(/** @type {{ uri?: unknown }} */ (anchor).uri) }
        : anchor,
    )
    return { ...op, fields: { ...fields, anchors: qualified } }
  })
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
 * The context a recall or briefing is asked for: where the agent works, what it works on, which
 * session it is, and which working copy — two harnesses in one repository are one reader.
 * @param {{ cwd?: string, session?: string | null, workspace?: string | null }} where - the
 *   session's directory and id.
 * @returns {Record<string, unknown>} the `RecallContext` body field.
 */
export function recallContext({ cwd, session, workspace }) {
  const shared = workspace ?? null
  if (cwd === undefined || cwd === '') {
    return {
      uris: [],
      signals: [{ concept: platformConcept() }],
      session: session ?? null,
      workspace: shared,
    }
  }
  return {
    uris: [fileUri(cwd)],
    signals: projectSignals(cwd),
    session: session ?? null,
    workspace: shared ?? fileUri(cwd),
  }
}

/** The machine name, for the `X-Ontyra-*` headers' provenance only. */
export function machineName() {
  return hostOf()
}
