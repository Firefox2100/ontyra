/**
 * The plugin's settings, resolved from what the Loader passes `apply`.
 *
 * Cordis hands `.volatile()` fields as accessors rather than values — that is how a live edit in
 * the settings card reaches a running plugin without a restart — so every read goes through
 * {@link fieldOf}. Defaults live here as plain values, mirroring the schema in `index.js`, so a
 * field the entry omits still means what the schema says it means.
 */

/** Defaults for every setting; keep in step with `Config` in `index.js`. */
export const DEFAULTS = Object.freeze({
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

/**
 * One setting's current value.
 * @param {unknown} value - an accessor (`{ get() }`) or a plain value.
 * @param {any} fallback - used when the field is absent or its accessor fails.
 * @returns {any} the value to act on.
 */
export function fieldOf(value, fallback) {
  if (value !== null && typeof value === 'object' && typeof (/** @type {any} */ (value).get) === 'function') {
    try {
      const current = /** @type {any} */ (value).get()
      return current === undefined ? fallback : current
    } catch {
      return fallback
    }
  }
  return value === undefined ? fallback : value
}

/**
 * Every setting, read now.
 * @param {any} rawConfig - what the Loader passed to `apply`.
 * @returns {typeof DEFAULTS} the resolved settings.
 */
export function settingsOf(rawConfig) {
  /** @type {Record<string, unknown>} */
  const settings = {}
  for (const [key, fallback] of Object.entries(DEFAULTS)) {
    settings[key] = fieldOf(rawConfig?.[key], fallback)
  }
  return /** @type {typeof DEFAULTS} */ (settings)
}
