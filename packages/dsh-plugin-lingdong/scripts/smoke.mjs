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
  check('tool count', tools.length === 8, `${tools.length}`)
  const names = new Set()
  for (const tool of tools) {
    check(`tool name ${tool.name}`, TOOL_NAME.test(tool.name))
    check(`tool unique ${tool.name}`, !names.has(tool.name))
    names.add(tool.name)
    check(`tool schema ${tool.name}`, tool.inputSchema?.type === 'object')
    check(`tool handler ${tool.name}`, typeof tool.handler === 'function')
    check(`tool description ${tool.name}`, typeof tool.description === 'string' && tool.description.length > 20)
  }

  // 2) The generated graph must pass the runtime's own canonical validator.
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

  // 4) A named window must be turned into a leading focus action by the plugin
  //    itself, because the backend graph contract does not require a target.
  const targeted = withTargetWindow([{ action: 'move_mouse', x: 10, y: 10 }], 'SmokeTarget')
  check(
    'named window prepends focus',
    targeted.length === 2 && targeted[0].action === 'focus_window' && targeted[0].title === 'SmokeTarget',
    JSON.stringify(targeted),
  )
  const untouched = withTargetWindow([{ action: 'move_mouse', x: 10, y: 10 }], undefined)
  check('no window leaves the batch unchanged', untouched.length === 1 && untouched[0].action === 'move_mouse')
} catch (error) {
  check('smoke run', false, error?.stack ?? String(error))
} finally {
  await sidecar.stop()
}

console.log(`\n${failures.length === 0 ? 'ALL CHECKS PASSED' : `${failures.length} CHECK(S) FAILED: ${failures.join(', ')}`}`)
process.exitCode = failures.length === 0 ? 0 : 1
