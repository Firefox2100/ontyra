/**
 * The bundled skill's frontmatter, parsed without a YAML dependency.
 *
 * A skill file is `---`-delimited frontmatter followed by markdown; ours declares exactly `name`
 * and `description`, so a line-oriented reader is enough and the package stays dependency-free.
 */

/** The name a skill falls back to when its frontmatter omits one. */
const FALLBACK_NAME = 'ontyra-memory'

/**
 * Split a skill document into the parts the registry takes.
 * @param {string} source - the markdown file.
 * @returns {{ name: string, description: string, body: string }} the parsed skill.
 */
export function parseSkill(source) {
  const match = /^---\r?\n([\s\S]*?)\r?\n---\r?\n([\s\S]*)$/.exec(source)
  if (match === null) return { name: FALLBACK_NAME, description: '', body: source.trim() }
  const fields = {}
  for (const line of match[1].split(/\r?\n/)) {
    const at = line.indexOf(':')
    if (at > 0) fields[line.slice(0, at).trim()] = line.slice(at + 1).trim().replace(/^["']|["']$/g, '')
  }
  return {
    name: fields.name ?? FALLBACK_NAME,
    description: fields.description ?? '',
    body: match[2].trim(),
  }
}
