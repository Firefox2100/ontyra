/**
 * Ontyra long-term memory for DeepSeek Harness.
 *
 * The Claude Code and Codex plugins reach the daemon through a shell hook; DeepSeek Harness has
 * no such bridge, so this plugin is the equivalent: it registers the daemon's tool surface
 * natively (no MCP server to configure), and — from M2 on — injects the session briefing and
 * per-prompt recall through `agent/pre-step`. Nothing about it is specific to the daemon's own
 * deployment: it speaks REST to `baseUrl`, with a credential reference rather than a secret in
 * the Cordis patch.
 *
 * Every setting is `.volatile()`: the settings card edits the live profile entry, and the plugin
 * reads `config` at call time, so a change applies to the next call without a restart.
 */

import z from '@deepseek-ai/schemastery'
import { defineTool } from '@deepseek-ai/dsh-tools'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { recallContext } from './context.js'
import { registerInjection } from './inject.js'
import { createClient } from './ontyra.js'
import { DEFAULTS, settingsOf } from './settings.js'
import { parseSkill } from './skill.js'
import { TOOLS, toolVisible } from './tools.js'

/** The Loader entry id; also the settings namespace the plugin's card edits. */
export const name = 'ontyra'

/**
 * The one service this plugin cannot work without. Injection rides on `sessionProjections`,
 * `credentials` and `workingDirectory`, but each of those is reached through `ctx.inject` or a
 * guarded read, so a deployment that composes fewer services still gets the tools.
 */
export const inject = ['tools']

/** The plugin's settings; the namespace is the Loader entry id (`ontyra`). */
const Config = z.object({
  baseUrl: z.string().default('http://127.0.0.1:8787').volatile(),
  tokenRef: z.string().role('credential-ref').default('ONTYRA_TOKEN').volatile(),
  harness: z.string().default('dsh').volatile(),
  briefing: z.boolean().default(true).volatile(),
  recall: z.boolean().default(true).volatile(),
  budgetTokens: z.natural().default(1200).volatile(),
  // Two budgets: a briefing or recall holds up a step, so it is cut short; a tool call is the
  // model waiting for an answer, and a daemon on another host needs room for its decider.
  timeoutMs: z.natural().default(1500).volatile(),
  toolTimeoutMs: z.natural().default(20000).volatile(),
  tools: z.boolean().default(true).volatile(),
  toolsProfile: z.string().default('lite').volatile(),
  episodes: z.boolean().default(true).volatile(),
  capture: z.boolean().default(true).volatile(),
  captureEvery: z.natural().default(5).volatile(),
  subagents: z.boolean().default(false).volatile(),
})

export { Config }

/**
 * Register the daemon's tool surface.
 *
 * Every tool is registered once, and its `when` condition decides step by step whether it is
 * offered: that is what makes the `tools` switch and the `toolsProfile` picker take effect on
 * the next step instead of on the next reload. The registry takes its own timeout at
 * registration, so that one is a generous backstop; `toolTimeoutMs` is read live and bounds each
 * call, which is what lets a daemon on another host work without a reload.
 * @param {import('@deepseek-ai/cordis').Context} ctx - the plugin context.
 * @param {() => typeof DEFAULTS} settingsNow - the live settings.
 * @param {{ call(call: any, signal: AbortSignal): Promise<unknown> }} client - daemon client.
 * @param {(session: any) => string | undefined} cwdOf - the session's working directory.
 */
function registerTools(ctx, settingsNow, client, cwdOf) {
  // A backstop only: the live budget below cuts every call first, and it is the settings value,
  // not this number, that a deployment tunes.
  const backstopMs = 120_000
  for (const tool of TOOLS) {
    const when = () => {
      try {
        return toolVisible(settingsNow(), tool.name)
      } catch {
        // A settings read that failed must not look like "this tool was removed".
        return true
      }
    }
    ctx.effect(() => ctx.tools.register(defineTool({
      name: tool.name,
      description: tool.description,
      parameters: tool.parameters,
      timeoutMs: backstopMs,
      output: {
        schema: { type: 'json' },
        render: (_args, value) => [{ type: 'text', text: tool.render(value) }],
      },
      execute: async (args, exec) => {
        const session = exec.agent?.session
        const context = recallContext({ cwd: cwdOf(session), session: session?.id })
        return await client.call(
          tool.request(args, context),
          exec.signal,
          settingsNow().toolTimeoutMs,
        )
      },
    }), { when }), `ontyra: tool ${tool.name}`)
  }
}

/** Where the bundled skill lives, next to `lib/`. */
const SKILL_PATH = join(dirname(fileURLToPath(import.meta.url)), '..', 'skills', 'ontyra-memory', 'SKILL.md')

/**
 * Publish the bundled skill, when the deployment has a skill registry.
 * @param {import('@deepseek-ai/cordis').Context} ctx - the plugin context.
 */
function registerSkill(ctx) {
  ctx.inject(['skills'], (skillCtx) => {
    let skill
    try {
      skill = parseSkill(readFileSync(SKILL_PATH, 'utf8'))
    } catch {
      return // No skill file: the tools and injection still work without it.
    }
    skillCtx.effect(() => skillCtx.skills.register({
      name: skill.name,
      description: skill.description,
      content: skill.body,
    }), 'ontyra: skill')
  })
}

/**
 * Mount the plugin.
 * @param {import('@deepseek-ai/cordis').Context} ctx - the plugin context.
 * @param {any} rawConfig - the entry's config: plain values and volatile accessors.
 * @returns {Promise<void>} when the plugin's registrations are in place.
 */
export async function apply(ctx, rawConfig) {
  const settingsNow = () => settingsOf(rawConfig)
  /** @type {() => Promise<{ value: string } | undefined>} */
  let resolveToken = async () => undefined
  ctx.inject(['credentials'], (credentialCtx) => {
    resolveToken = async () => {
      try {
        return await credentialCtx.credentials.resolve(settingsNow().tokenRef)
      } catch {
        return undefined
      }
    }
  })

  /** @type {{ get(session: any): string } | undefined} */
  let workingDirectory
  ctx.inject(['workingDirectory'], (directoryCtx) => {
    workingDirectory = directoryCtx.workingDirectory
  })

  const cwdOf = (session) => {
    if (workingDirectory !== undefined) {
      try {
        return workingDirectory.get(session)
      } catch {
        // The directory service can refuse a session it does not own; the header still can.
      }
    }
    return session?.header?.cwd
  }

  const client = createClient({ settings: settingsNow, resolveToken: () => resolveToken() })
  registerSkill(ctx)
  registerTools(ctx, settingsNow, client, cwdOf)
  const wanted = settingsNow()
  if (wanted.briefing || wanted.recall || wanted.capture !== false || wanted.episodes) {
    // Injection rides on services the deployment may not compose; losing it must not cost the
    // tools, so a failure here is logged and the rest of the plugin stays mounted.
    try {
      await registerInjection(ctx, { settingsNow, client, cwdOf })
    } catch (error) {
      ctx.logger?.warn?.(error)
    }
  }
}
