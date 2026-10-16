/**
 * Lingdong computer-use provider for the DeepSeek Harness.
 *
 * This plugin adds capability and changes nothing that DSH already ships:
 * it claims the exclusive computer-use provider slot, registers model-facing
 * tools, and manages its own desktop runtime process. Disabling the plugin
 * removes every trace of it.
 * @module @lingdong/dsh-plugin-lingdong
 */
import { ComputerUseProviderName } from '@deepseek-ai/dsh-computer-use/brand'
import { createMcpToolDefinition } from '@deepseek-ai/dsh-mcp-client'
import { Config, resolveConfig } from './config.mjs'
import { WinPilotSidecar } from './sidecar.mjs'
import { createClient } from './client.mjs'
import { buildTools, GUIDANCE } from './tools.mjs'

/** Cordis plugin identity. */
export const name = 'lingdong'

/** Services required before the provider can publish anything. */
export const inject = ['computerUse', 'tools', 'systemPrompt']

export { Config }

/** Provider name claimed in the exclusive computer-use slot. */
export const PROVIDER_NAME = 'lingdong'

/**
 * Mount the provider and its tool surface.
 * @param ctx - context carrying the computer-use registry, tools and system prompt.
 * @param config - declared plugin configuration.
 * @returns after tool registration and prompt wiring complete.
 */
export async function apply(ctx, config = {}) {
  const settings = resolveConfig(config)
  const log = ctx.logger ?? console
  const sidecar = new WinPilotSidecar(settings, log)
  const client = createClient(settings, sidecar)

  ctx.effect(function* () {
    if (settings.registerComputerUse) {
      try {
        yield ctx.computerUse.register(ComputerUseProviderName(PROVIDER_NAME))
      } catch (error) {
        // The slot is exclusive by design. A taken slot must not brick the
        // harness, so the tools still mount and the condition is reported.
        log.warn?.(
          `[lingdong] computer-use provider slot is occupied (${error?.message ?? error}); ` +
          'the desktop tools are still registered. Disable the other provider to replace it.',
        )
      }
    }
    yield () => sidecar.stop()
    const child = ctx.plugin({
      name: 'lingdong-tools',
      inject: ['tools', 'systemPrompt'],
      apply: (inner) => mountTools(inner, client, sidecar, settings),
    })
    yield child.dispose
  }, 'lingdong.runtime')

  // Warm the runtime without blocking activation: a missing interpreter or a
  // slow first import must not prevent DSH from starting.
  sidecar.ensure().then(
    () => log.info?.(`[lingdong] desktop runtime ready at ${sidecar.baseUrl}`),
    (error) => log.warn?.(`[lingdong] desktop runtime unavailable: ${error?.message ?? error}`),
  )
}

/**
 * Register every tool and the computer-use guidance section.
 * @param inner - child context carrying the tools and system-prompt registries.
 * @param client - runtime REST client.
 * @param sidecar - runtime lifecycle owner.
 * @param settings - resolved plugin settings.
 */
function mountTools(inner, client, sidecar, settings) {
  const registered = []
  for (const tool of buildTools({ client, sidecar, settings })) {
    inner.tools.register(createMcpToolDefinition(inner, {
      name: tool.name,
      rawName: tool.name,
      description: tool.description,
      inputSchema: tool.inputSchema,
      async call(args, execution) {
        if (execution?.signal?.aborted) throw new Error('lingdong tool call was cancelled')
        return tool.handler(args, execution)
      },
    }))
    registered.push(tool.name)
  }
  inner.systemPrompt.section({
    name: 'computer-use:lingdong',
    order: inner.systemPrompt.getSectionOrder('TOOL_COMPUTER_USE'),
    text: GUIDANCE,
  })
  inner.logger?.info?.(`[lingdong] registered ${registered.length} desktop tools`)
}
