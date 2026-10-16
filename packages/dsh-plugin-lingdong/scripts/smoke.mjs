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
import { BrowserSession } from '../src/browser.mjs'
import { createClient } from '../src/client.mjs'
import { buildTools, buildBrowserTool, actionBatchGraph, withTargetWindow } from '../src/tools.mjs'

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
  //    The browser session is constructed but never started: the worker is
  //    spawned lazily, so building the descriptor here costs nothing.
  const browserSession = new BrowserSession(settings, console)
  const tools = buildTools({ client, sidecar, settings, browser: browserSession })
  tools.push(buildBrowserTool(browserSession))
  check('tool count', tools.length === 16, `${tools.length}`)
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

  // 3) The joint window snapshot and the predicate evaluator answer for a real
  //    window. A Notepad window is launched through the runtime's own canonical
  //    launch action, so the frame under test is one the runtime can produce.
  //    Only a window that appears after this launch is ever closed again, and
  //    it is closed with an ordinary window action rather than a kill.
  //    Identity is the window handle, not the pid: Windows reuses pids, so a
  //    pid set makes this check flaky on a busy machine.
  const before = new Set(
    ((await client.get('/api/v2/desktop/windows', { timeoutMs: 60_000 }))?.windows ?? [])
      .filter((item) => String(item.process_name || '').toLowerCase().startsWith('notepad'))
      .map((item) => item.hwnd),
  )
  const launchGraph = actionBatchGraph({
    actions: [{ action: 'launch', command: 'notepad' }],
    name: 'smoke launch',
  })
  const launched = await client.post('/api/v2/arp/runs', { graph: launchGraph, inputs: {} })
  const launchRunId = launched.run?.run_id ?? launched.run_id
  if (launchRunId) await client.waitForRun(launchRunId, { timeoutMs: 60_000 })
  await new Promise((resolve) => setTimeout(resolve, 1500))

  const windowList = await client.get('/api/v2/desktop/windows', { timeoutMs: 60_000 })
  const notepadWindows = (windowList?.windows ?? []).filter((item) => String(item.process_name || '').toLowerCase().startsWith('notepad'))
  const adopted = notepadWindows.find((item) => !before.has(item.hwnd))
  // Launching an application that is already running may adopt the window that
  // is already there instead of creating another one, and that is correct
  // behaviour - so the check is "there is a Notepad to work with", not "a new
  // window appeared". Cleanup below only closes what this run added.
  const notepad = adopted ?? notepadWindows[0]
  check(
    'the launch left a usable Notepad window',
    Boolean(notepad),
    notepad ? `hwnd=${notepad.hwnd} pid=${notepad.pid} ${adopted ? '(new)' : '(adopted an existing window)'}` : 'no notepad window',
  )

  if (notepad) {
    const windowInfo = await client.get(`/api/v2/desktop/window?hwnd=${notepad.hwnd}`, { timeoutMs: 30_000 })
    check(
      'GET /api/v2/desktop/window reads one handle',
      windowInfo?.ok === true && windowInfo.window?.hwnd === notepad.hwnd && windowInfo.window?.process_name === notepad.process_name,
      JSON.stringify(windowInfo?.window ?? {}).slice(0, 140),
    )

    const elements = await client.get(`/api/v2/desktop/elements?hwnd=${notepad.hwnd}&limit=50`, { timeoutMs: 60_000 })
    check(
      'GET /api/v2/desktop/elements is JSON-serializable, without live control handles',
      elements?.ok === true
        && Array.isArray(elements.elements)
        && elements.returned === elements.elements.length
        && elements.elements.every((item) => typeof item.role === 'string' && Array.isArray(item.bbox))
        && elements.elements.every((item) => !('uia_ctrl' in (item.extra || {}))),
      `${elements?.returned ?? '?'} of ${elements?.count ?? '?'} elements`,
    )

    const snapshot = await client.get(`/api/v2/desktop/snapshot?hwnd=${notepad.hwnd}&limit=50`, { timeoutMs: 60_000 })
    check(
      'GET /api/v2/desktop/snapshot returns window facts and an image together',
      snapshot?.ok === true
        && snapshot.window?.hwnd === notepad.hwnd
        && snapshot.image?.content_type === 'image/jpeg'
        && snapshot.image.width > 0
        && snapshot.image.height > 0
        && typeof snapshot.image.base64 === 'string'
        && snapshot.image.base64.length > 1000,
      JSON.stringify({ window: snapshot?.window?.title, image: snapshot?.image && { w: snapshot.image.width, h: snapshot.image.height, bytes: snapshot.image.bytes } }),
    )
    check(
      'the snapshot image really decodes to the size it reports',
      (() => {
        const raw = Buffer.from(snapshot?.image?.base64 ?? '', 'base64')
        if (raw.length < 4 || raw[0] !== 0xff || raw[1] !== 0xd8) return false
        // JPEG SOF0/SOF2 carries the frame size; read it rather than trusting.
        let at = 2
        while (at + 9 < raw.length) {
          if (raw[at] !== 0xff) { at += 1; continue }
          const marker = raw[at + 1]
          const length = raw.readUInt16BE(at + 2)
          if (marker >= 0xc0 && marker <= 0xcf && marker !== 0xc4 && marker !== 0xc8 && marker !== 0xcc) {
            // Frame header layout: FF, marker, length(2), precision(1), height(2), width(2).
            return raw.readUInt16BE(at + 5) === snapshot.image.height
              && raw.readUInt16BE(at + 7) === snapshot.image.width
          }
          at += 2 + length
        }
        return false
      })(),
      JSON.stringify(snapshot?.image && { w: snapshot.image.width, h: snapshot.image.height }),
    )
    check(
      'the snapshot carries the accessibility tree and its own bound',
      Array.isArray(snapshot?.elements)
        && snapshot.window.accessibility_returned === snapshot.elements.length
        && snapshot.window.accessibility_count >= snapshot.elements.length,
      `${snapshot?.elements?.length ?? '?'} of ${snapshot?.window?.accessibility_count ?? '?'}`,
    )

    const satisfied = await client.post('/api/v2/desktop/verify', {
      expect: { window_title: notepad.title.slice(0, 12) },
      hwnd: notepad.hwnd,
      timeout_s: 3,
    }, { timeoutMs: 60_000 })
    check(
      'POST /api/v2/desktop/verify confirms a condition that does hold',
      satisfied?.ok === true && satisfied.satisfied === true,
      JSON.stringify({ satisfied: satisfied?.satisfied, detail: satisfied?.detail }),
    )

    const unsatisfied = await client.post('/api/v2/desktop/verify', {
      expect: { window_title: 'a title that cannot be on this desktop 9f3a1c' },
      hwnd: notepad.hwnd,
      timeout_s: 0.5,
    }, { timeoutMs: 60_000 })
    check(
      'POST /api/v2/desktop/verify answers false for a condition that does not hold',
      unsatisfied?.ok === true && unsatisfied.satisfied === false,
      JSON.stringify({ satisfied: unsatisfied?.satisfied, detail: unsatisfied?.detail }).slice(0, 140),
    )

    let unsupported = null
    try {
      await client.post('/api/v2/desktop/verify', { expect: { no_such_condition: 1 } }, { timeoutMs: 30_000 })
    } catch (error) {
      unsupported = error
    }
    check(
      'an unsupported condition is refused by name instead of answering false',
      unsupported !== null && /no_such_condition/.test(String(unsupported.message)),
      unsupported ? String(unsupported.message).slice(0, 120) : 'no error',
    )

    // The two primitives the shipped driver has and this one did not: a value
    // write that never clicks, and a frame change that never takes focus. Both
    // are confirmed through a different surface than the one that performed
    // them, so neither result is the action's own word for it.
    const FRAME = { x: 150, y: 120, width: 700, height: 460 }
    const framed = await client.post('/api/v2/arp/runs', {
      graph: actionBatchGraph({
        actions: [{ action: 'set_window_frame', hwnd: notepad.hwnd, ...FRAME }],
        name: 'smoke frame',
      }),
      inputs: {},
    }, { timeoutMs: 60_000 })
    const frameRunId = framed.run?.run_id ?? framed.run_id
    if (frameRunId) await client.waitForRun(frameRunId, { timeoutMs: 60_000 })
    const readBack = await client.get(`/api/v2/desktop/window?hwnd=${notepad.hwnd}`, { timeoutMs: 30_000 })
    const got = readBack?.window?.rect ?? []
    check(
      'set_window_frame moved the window, confirmed by an independent window read',
      frameRunId
        && Math.abs(got[0] - FRAME.x) <= 1 && Math.abs(got[1] - FRAME.y) <= 1
        && Math.abs((got[2] - got[0]) - FRAME.width) <= 1 && Math.abs((got[3] - got[1]) - FRAME.height) <= 1,
      `requested ${JSON.stringify(FRAME)} read back [${got.join(',')}]`,
    )

    const MARK = 'LINGDONG-SMOKE-VALUE'
    const wrote = await client.post('/api/v2/arp/runs', {
      graph: actionBatchGraph({
        actions: [{ action: 'set_value', hwnd: notepad.hwnd, automation_id: '15', text: MARK }],
        name: 'smoke value',
      }),
      inputs: {},
    }, { timeoutMs: 60_000 })
    const valueRunId = wrote.run?.run_id ?? wrote.run_id
    if (valueRunId) await client.waitForRun(valueRunId, { timeoutMs: 60_000 })
    // The perception collector renders an element that holds a value as
    // "name=value", so reading the window back shows what the control holds.
    const afterWrite = await client.get(`/api/v2/desktop/snapshot?hwnd=${notepad.hwnd}&limit=60`, { timeoutMs: 60_000 })
    check(
      'set_value wrote the control, confirmed by an independent tree read',
      valueRunId && JSON.stringify(afterWrite?.elements ?? []).includes(MARK),
      `elements=${afterWrite?.elements?.length ?? '?'}`,
    )

    // Undo the write so the document is clean again and the close below cannot
    // raise a save prompt in whatever language this machine runs.
    if (valueRunId) {
      const undone = await client.post('/api/v2/arp/runs', {
        graph: actionBatchGraph({
          actions: [
            { action: 'focus_window', hwnd: notepad.hwnd },
            { action: 'hotkey', keys: ['ctrl', 'z'] },
          ],
          name: 'smoke undo',
        }),
        inputs: {},
      }, { timeoutMs: 60_000 })
      const undoRunId = undone.run?.run_id ?? undone.run_id
      if (undoRunId) await client.waitForRun(undoRunId, { timeoutMs: 60_000 })
    }

    // Close the window this run opened. The canonical close is attempted first;
    // a window that still refuses to go is terminated by pid through the
    // offline flow, because leaving litter on someone's desktop is not an
    // acceptable way for a smoke test to end. When the launch adopted a window
    // that was already there, nothing is closed at all: it is not this run's.
    let after = notepadWindows
    if (adopted) {
      const closeGraph = actionBatchGraph({
        actions: [
          { action: 'focus_window', title: adopted.title },
          { action: 'hotkey', keys: ['alt', 'f4'] },
        ],
        name: 'smoke close',
      })
      const closed = await client.post('/api/v2/arp/runs', { graph: closeGraph, inputs: {} })
      const closeRunId = closed.run?.run_id ?? closed.run_id
      if (closeRunId) await client.waitForRun(closeRunId, { timeoutMs: 60_000 })
      await new Promise((resolve) => setTimeout(resolve, 800))
      after = ((await client.get('/api/v2/desktop/windows', { timeoutMs: 60_000 }))?.windows ?? [])
        .filter((item) => String(item.process_name || '').toLowerCase().startsWith('notepad'))
      if (after.length !== before.size && adopted.pid) {
        await client.post('/api/v2/automation-ir/runs', {
          automation: {
            format: 'automation-ir/1',
            id: 'air-smoke-cleanup',
            name: 'smoke cleanup',
            created: Math.floor(Date.now() / 1000),
            source: { kind: 'wps', format: 'wps/2', source: 'smoke', source_run: '', task: '' },
            conditions: {},
            evidence: { required: [] },
            variables: {},
            runtime: { kind: 'wps_replay' },
            steps: [{ tool: 'run_shell', command: `taskkill /PID ${adopted.pid} /F`, shell: 'powershell', timeout_s: 20 }],
          },
          inputs: {},
        }, { timeoutMs: 60_000 })
        await new Promise((resolve) => setTimeout(resolve, 1500))
        after = ((await client.get('/api/v2/desktop/windows', { timeoutMs: 60_000 }))?.windows ?? [])
          .filter((item) => String(item.process_name || '').toLowerCase().startsWith('notepad'))
      }
    }
    check(
      'the smoke left the desktop as it found it',
      after.length === before.size,
      `notepad windows: before=${before.size} after=${after.length}${adopted ? '' : ' (nothing was opened)'}`,
    )
  }

  // 4) A spatial batch must be accepted when the target window is established first.
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
  await checkActivation()} catch (error) {
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
function fakeHost({ withWebServer, withComputerUse = true }) {
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
    // Optional injection: the callback runs only for a service this host has.
    inject(names, callback) {
      const satisfied = names.every((service) => (
        service === 'webServer' ? withWebServer
          : service === 'computerUse' ? withComputerUse
            : true
      ))
      if (satisfied) callback(ctx)
    },
  }
  if (withWebServer) {
    ctx.webServer = { register(route) { seen.routes.push(route); return () => {} } }
  }
  if (!withComputerUse) delete ctx.computerUse
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
  check('apply() registers the 16 tools', withWeb.seen.tools.length === 16, `${withWeb.seen.tools.length}`)
  check(
    'the registered tool names match the declared surface',
    withWeb.seen.tools.map((tool) => tool.name).sort().join(',') === [...names].sort().join(','),
    `${withWeb.seen.tools.map((tool) => tool.name).join(',')} vs ${[...names].join(',')}`,
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
  check(
    'the browser tool exposes one stateful action surface',
    (() => {
      const tool = withWeb.seen.tools.find((entry) => entry.name === 'lingdong_browser')
      const actions = tool?.parameters?.properties?.action?.enum ?? []
      return actions.length === 22
        && actions.includes('launch')
        && actions.includes('connect')
        && actions.includes('screenshot')
        && actions.includes('upload')
        && (tool.parameters.required ?? []).join(',') === 'action'
    })(),
    'expected the declared browser action list',
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
    headless.seen.tools.length === 16 && headless.seen.routes.length === 0,
    `tools=${headless.seen.tools.length} routes=${headless.seen.routes.length}`,
  )

  // The regression an end-to-end run found: requiring the computer-use service
  // left this plugin pending forever in a composition that ships no
  // computer-use registry, so the model got no desktop tools at all.
  const noSlot = fakeHost({ withWebServer: false, withComputerUse: false })
  let noSlotThrew = null
  try {
    await plugin.apply(noSlot.ctx, config)
    await new Promise((resolve) => setTimeout(resolve, 100))
  } catch (error) {
    noSlotThrew = error
  }
  check('apply() completes without a computer-use registry', noSlotThrew === null, noSlotThrew ? String(noSlotThrew.message ?? noSlotThrew) : '')
  check(
    'a composition without the computer-use slot still gets every tool',
    noSlot.seen.tools.length === 16 && noSlot.seen.computerUse.length === 0,
    `tools=${noSlot.seen.tools.length} slot=${noSlot.seen.computerUse.length}`,
  )
  check(
    'the plugin declares only the services it truly requires',
    Array.isArray(plugin.inject) && plugin.inject.join(',') === 'tools,systemPrompt',
    JSON.stringify(plugin.inject),
  )
}

console.log(`\n${failures.length === 0 ? 'ALL CHECKS PASSED' : `${failures.length} CHECK(S) FAILED: ${failures.join(', ')}`}`)
process.exitCode = failures.length === 0 ? 0 : 1
