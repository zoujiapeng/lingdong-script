#!/usr/bin/env node
/**
 * Offline smoke test for the Lingdong plugin.
 *
 * Starts (or attaches to) the repository's own WinPilot runtime, checks that the
 * declared tool surface is well formed, and submits the generated action-batch
 * graph to the runtime's real ARP validator. Nothing here touches the desktop:
 * validation is a pure contract check.
 *
 * Usage:  node packages/dsh-plugin-lingdong/scripts/smoke.mjs
 */
import { resolveConfig } from '../src/config.mjs'
import { WinPilotSidecar } from '../src/sidecar.mjs'
import { createClient } from '../src/client.mjs'
import { buildTools, actionBatchGraph, withTargetWindow } from '../src/tools.mjs'

const TOOL_NAME = /^[A-Za-z0-9_-]{1,64}$/
const failures = []
/** Declared tool names, shared with the activation check further down. */
const names = new Set()

function check(label, ok, detail) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}${detail ? ` — ${detail}` : ''}`)
  if (!ok) failures.push(label)
}

const settings = resolveConfig({ port: Number(process.env.LINGDONG_SMOKE_PORT || 8790) })
console.log(`repoRoot : ${settings.repoRoot}`)
console.log(`python   : ${settings.python}`)
console.log(`baseUrl  : http://${settings.host}:${settings.port}`)

const sidecar = new WinPilotSidecar(settings, console)
const client = createClient(settings, sidecar)

try {
  await sidecar.ensure()
  check('runtime reachable', true, sidecar.baseUrl)

  const ping = await client.get('/api/ping', { timeoutMs: 15_000 })
  check('GET /api/ping', Boolean(ping), JSON.stringify(ping).slice(0, 120))

  // 1) Tool surface is well formed for the DeepSeek function-name contract.
  const tools = buildTools({ client, sidecar, settings })
  check('tool count', tools.length === 13, `${tools.length}`)
  for (const tool of tools) {
    check(`tool name ${tool.name}`, TOOL_NAME.test(tool.name))
    check(`tool unique ${tool.name}`, !names.has(tool.name))
    names.add(tool.name)
    check(`tool schema ${tool.name}`, tool.inputSchema?.type === 'object')
    check(`tool handler ${tool.name}`, typeof tool.handler === 'function')
    check(`tool description ${tool.name}`, typeof tool.description === 'string' && tool.description.length > 20)
  }

  // 2) The observation surface answers and is bounded.
  const screen = await client.get('/api/v2/desktop/screen', { timeoutMs: 30_000 })
  check(
    'GET /api/v2/desktop/screen',
    screen?.ok === true && screen.virtual?.width > 0 && screen.virtual?.height > 0,
    JSON.stringify(screen?.virtual ?? screen),
  )
  const cursor = await client.get('/api/v2/desktop/cursor', { timeoutMs: 30_000 })
  check('GET /api/v2/desktop/cursor', cursor?.ok === true, JSON.stringify(cursor))
  const windows = await client.get('/api/v2/desktop/windows', { timeoutMs: 60_000 })
  check('GET /api/v2/desktop/windows', Boolean(windows) && typeof windows === 'object', `keys=${Object.keys(windows ?? {}).join('|')}`)
  const apps = await client.get('/api/v2/desktop/apps?max_results=5', { timeoutMs: 180_000 })
  check('GET /api/v2/desktop/apps', apps?.ok === true, `count=${apps?.count ?? '?'}`)

  // 3) The generated graph must pass the runtime's own canonical validator.
  const observeGraph = actionBatchGraph({
    actions: [{ action: 'screenshot', managed_artifact: true, scope: 'screen', purpose: 'user_output', sendable: true }],
    name: 'smoke observe',
  })
  const observeValidation = await client.post('/api/v2/arp/validate', observeGraph)
  check('validate observe graph', observeValidation?.valid === true, JSON.stringify(observeValidation?.errors ?? []))

  // 3) A spatial batch must be accepted when the target window is established first.
  const spatialGraph = actionBatchGraph({
    actions: [
      { action: 'focus_window', title: 'SmokeTarget' },
      { action: 'move_mouse', x: 10, y: 10 },
    ],
    name: 'smoke spatial',
  })
  const spatialValidation = await client.post('/api/v2/arp/validate', spatialGraph)
  check('validate spatial graph', spatialValidation?.valid === true, JSON.stringify(spatialValidation?.errors ?? []))

  // 5) A named window must be turned into a leading focus action by the plugin
  //    itself, because the backend graph contract does not require a target.
  const targeted = withTargetWindow([{ action: 'move_mouse', x: 10, y: 10 }], 'SmokeTarget')
  check(
    'named window prepends focus',
    targeted.length === 2 && targeted[0].action === 'focus_window' && targeted[0].title === 'SmokeTarget',
    JSON.stringify(targeted),
  )
  const untouched = withTargetWindow([{ action: 'move_mouse', x: 10, y: 10 }], undefined)
  check('no window leaves the batch unchanged', untouched.length === 1 && untouched[0].action === 'move_mouse')

  // 6) The plugin's own activation path: mount it against a host context and
  //    assert what it publishes. This runs the code DSH runs in apply(), so a
  //    broken registration fails here rather than at the next harness restart.
  await checkActivation()
} catch (error) {
  check('smoke run', false, error?.stack ?? String(error))
} finally {
  await sidecar.stop()
}

/**
 * Drive one Cordis effect: run it, keep what it yields, and return a disposer.
 * @param fn - the effect body, a generator or a plain function.
 * @returns a function releasing everything the body yielded.
 */
function runEffect(fn) {
  const value = fn()
  if (value && typeof value.next === 'function') {
    const yielded = []
    let step = value.next()
    while (!step.done) {
      yielded.push(step.value)
      step = value.next()
    }
    return () => {
      for (let i = yielded.length - 1; i >= 0; i -= 1) {
        if (typeof yielded[i] === 'function') yielded[i]()
      }
    }
  }
  return typeof value === 'function' ? value : () => {}
}

/** A host context carrying exactly the services this plugin injects. */
function fakeHost({ withWebServer }) {
  const seen = { tools: [], sections: [], routes: [], plugins: [], computerUse: [] }
  const ctx = {
    logger: { info() {}, warn() {}, error() {} },
    effect(fn) { runEffect(fn); return () => {} },
    plugin(definition) {
      seen.plugins.push(definition.name)
      // Cordis applies a child plugin on its own fiber; this stands in for it.
      if (typeof definition.apply === 'function') definition.apply(ctx)
      return { dispose() {} }
    },
    computerUse: { register(name) { seen.computerUse.push(String(name)); return () => {} } },
    tools: { register(tool) { seen.tools.push(tool) } },
    systemPrompt: { section(section) { seen.sections.push(section) }, getSectionOrder() { return 100 } },
    inject(names, callback) {
      if (withWebServer && names.indexOf('webServer') !== -1) callback(ctx)
    },
  }
  if (withWebServer) {
    ctx.webServer = { register(route) { seen.routes.push(route); return () => {} } }
  }
  return { ctx, seen }
}

/**
 * Assert the plugin mounts correctly in both compositions.
 * @returns after both mounts are checked.
 */
async function checkActivation() {
  let plugin = null
  try {
    plugin = await import('../src/index.mjs')
  } catch (error) {
    check(
      'plugin module imports (run scripts/link-peers.mjs if this fails)',
      false,
      String(error?.message ?? error),
    )
    return
  }
  check('plugin module imports', true, `name=${plugin.name}`)

  // Nothing listens on this port, so the runtime warm-up must degrade to a
  // warning instead of rejecting activation.
  const config = { port: 8799, autoStart: false, repoRoot: settings.repoRoot }

  const withWeb = fakeHost({ withWebServer: true })
  let threw = null
  try {
    await plugin.apply(withWeb.ctx, config)
    await new Promise((resolve) => setTimeout(resolve, 100))
  } catch (error) {
    threw = error
  }
  check('apply() completes with a web server present', threw === null, threw ? String(threw.message ?? threw) : '')
  check(
    'apply() claims the exclusive computer-use slot',
    withWeb.seen.computerUse.join(',') === 'lingdong',
    withWeb.seen.computerUse.join(','),
  )
  check(
    'apply() mounts the tool child plugin',
    withWeb.seen.plugins.join(',') === 'lingdong-tools',
    withWeb.seen.plugins.join(','),
  )
  check('apply() registers the 13 tools', withWeb.seen.tools.length === 13, `${withWeb.seen.tools.length}`)
  check(
    'the registered tool names match the declared surface',
    withWeb.seen.tools.map((tool) => tool.name).sort().join(',') === [...names].sort().join(','),
    withWeb.seen.tools.map((tool) => tool.name).join(','),
  )
  check(
    'apply() adds exactly one system-prompt section',
    withWeb.seen.sections.length === 1 && withWeb.seen.sections[0].name === 'computer-use:lingdong',
    withWeb.seen.sections.map((section) => section.name).join(','),
  )
  check(
    'apply() registers the flow bridge on the web server',
    withWeb.seen.routes.length === 1
      && withWeb.seen.routes[0].kind === 'prefix'
      && withWeb.seen.routes[0].path === '/lingdong/api'
      && typeof withWeb.seen.routes[0].handler === 'function',
    JSON.stringify(withWeb.seen.routes.map((route) => `${route.kind} ${route.path}`)),
  )

  const headless = fakeHost({ withWebServer: false })
  let headlessThrew = null
  try {
    await plugin.apply(headless.ctx, config)
    await new Promise((resolve) => setTimeout(resolve, 100))
  } catch (error) {
    headlessThrew = error
  }
  check('apply() completes without a web server', headlessThrew === null, headlessThrew ? String(headlessThrew.message ?? headlessThrew) : '')
  check(
    'a headless composition still gets the full tool surface',
    headless.seen.tools.length === 13 && headless.seen.routes.length === 0,
    `tools=${headless.seen.tools.length} routes=${headless.seen.routes.length}`,
  )
}

console.log(`\n${failures.length === 0 ? 'ALL CHECKS PASSED' : `${failures.length} CHECK(S) FAILED: ${failures.join(', ')}`}`)
process.exitCode = failures.length === 0 ? 0 : 1
