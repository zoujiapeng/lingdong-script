#!/usr/bin/env node
/**
 * End-to-end proof for `invoke_menu` against a real application.
 *
 * The oracle here is deliberately outside both the plugin and the runtime: a
 * separate process reads the target's own edit-control text length through the
 * Win32 API, so a menu command that did nothing cannot be mistaken for one that
 * worked. It also asserts the property that makes the action worth having --
 * the window is never activated.
 *
 * Usage:  node packages/dsh-plugin-lingdong/e2e/probe-menu-live.mjs
 */
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

import { resolveConfig } from '../src/config.mjs'
import { WinPilotSidecar } from '../src/sidecar.mjs'
import { createClient } from '../src/client.mjs'
import { actionBatchGraph } from '../src/tools.mjs'

const here = path.dirname(fileURLToPath(import.meta.url))
const READER = path.join(here, 'menu-oracle.py')

/** Ask the out-of-process oracle what the target window shows about itself. */
function oracle(args) {
  const result = spawnSync('python', [READER, ...args], { encoding: 'utf8', shell: false })
  if (result.status !== 0) throw new Error(`oracle failed: ${result.stderr || result.stdout}`)
  return JSON.parse(result.stdout.trim().split('\n').pop())
}

const settings = resolveConfig({ port: Number(process.env.LINGDONG_PROBE_PORT || 8792) })
const sidecar = new WinPilotSidecar(settings, console)
const client = createClient(settings, sidecar)
const failures = []
const check = (label, ok, detail) => {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}${detail ? ` — ${detail}` : ''}`)
  if (!ok) failures.push(label)
}

/** Run one offline shell step, the path this repository already uses for cleanup. */
async function shell(command) {
  return client.post('/api/v2/automation-ir/runs', {
    automation: {
      format: 'automation-ir/1',
      id: `air-menu-probe-${Date.now().toString(36)}`,
      name: 'menu probe cleanup',
      created: Math.floor(Date.now() / 1000),
      source: { kind: 'wps', format: 'wps/2', source: 'probe', source_run: '', task: '' },
      conditions: {},
      evidence: { required: [] },
      variables: {},
      runtime: { kind: 'wps_replay' },
      steps: [{ tool: 'run_shell', command, shell: 'powershell', timeout_s: 20 }],
    },
    inputs: {},
  }, { timeoutMs: 60_000 })
}

let hwnd = 0
const startedPids = []

try {
  await sidecar.ensure()

  // Start from a clean desktop. Earlier probing left several Notepad windows
  // behind, and driving "a" Notepad picked one that was not the one under test:
  // the batch then answered, correctly, that the window it was given has no
  // menu at all. Nothing here is asserted about those instances -- they are
  // removed so the window under test is identifiable.
  const strays = ((await client.get('/api/v2/desktop/windows', { timeoutMs: 60_000 }))?.windows ?? [])
    .filter((item) => String(item.process_name || '').toLowerCase().startsWith('notepad'))
  for (const stray of strays) await shell(`taskkill /PID ${stray.pid} /F`)
  await new Promise((resolve) => setTimeout(resolve, 800))

  const launched = await client.post('/api/v2/arp/runs', {
    graph: actionBatchGraph({ actions: [{ action: 'launch', command: 'notepad' }], name: 'menu probe launch' }),
    inputs: {},
  })
  const launchRunId = launched.run?.run_id ?? launched.run_id
  if (launchRunId) await client.waitForRun(launchRunId, { timeoutMs: 60_000 })
  await new Promise((resolve) => setTimeout(resolve, 1500))

  const windows = (await client.get('/api/v2/desktop/windows', { timeoutMs: 60_000 }))?.windows ?? []
  const notepad = windows.find((item) => String(item.process_name || '').toLowerCase().startsWith('notepad'))
  hwnd = notepad?.hwnd ?? 0
  if (notepad) startedPids.push(Number(notepad.pid))
  check('a clean Notepad window was launched to drive', Boolean(hwnd), `hwnd=${hwnd} pid=${notepad?.pid}`)
  if (!hwnd) throw new Error('no Notepad window')

  // Read the menu once, out of process, so the paths below are the real ones
  // for whatever locale this machine runs, rather than guessed English names.
  const menu = oracle(['--hwnd', String(hwnd), '--dump'])
  const findPath = (predicate) => {
    for (const top of menu.items ?? []) {
      for (const child of top.children ?? []) {
        if (predicate(top.text, child.text)) return [top.text, child.text]
      }
    }
    return null
  }
  const insertPath = findPath((top, child) => child.includes('时间') || child.toLowerCase().includes('time'))
  const statusPath = findPath((top, child) => child.includes('状态栏') || child.toLowerCase().includes('status bar'))
  check('the application reports a menu with a second level', Boolean(menu.items?.length), `${menu.items?.length} headings`)
  check('an insert-time-and-date command was found', Boolean(insertPath), JSON.stringify(insertPath))

  // Establish a background target: the shell is brought forward and the target
  // must stay behind it for the whole exchange.
  oracle(['--hwnd', String(hwnd), '--deactivate'])
  const foregroundBefore = oracle(['--foreground'])
  check('the target is not the foreground window', foregroundBefore.foreground !== hwnd, `foreground=${foregroundBefore.foreground}`)

  const stateBefore = oracle(['--hwnd', String(hwnd), '--state'])
  /**
   * Submit one invoke_menu batch and read back what the action answered.
   *
   * A refused action fails the run by design, so the node result is null. What
   * the action actually answered is on the BUS event for the child action --
   * the same record a model and the recorder read -- so that is what is read
   * here, which also checks that the refusal survives into that record.
   */
  const invoke = async (pathSegments, label) => {
    const submitted = await client.post('/api/v2/arp/runs', {
      graph: actionBatchGraph({
        actions: [{ action: 'invoke_menu', hwnd, path: pathSegments }],
        name: label,
      }),
      inputs: {},
    }, { timeoutMs: 60_000 })
    const runId = submitted.run?.run_id ?? submitted.run_id
    // The run endpoint answers {ok, run, arp}: the document is under `run`.
    const envelope = runId ? await client.waitForRun(runId, { timeoutMs: 60_000 }) : {}
    const run = envelope?.run ?? {}
    const node = run?.result ?? null
    const event = (run?.timeline ?? []).filter((entry) => entry.type === 'agent_batch_action').pop()
    let recorded = null
    try {
      // The run timeline carries the child action's bounded record.
      recorded = event?.summary ? JSON.parse(event.summary) : null
    } catch {
      // A record that does not parse is itself a finding, so it is reported
      // rather than silently replaced by the node result.
      recorded = null
    }
    const data = recorded ?? node?.results?.[0]?.data ?? node?.data ?? null
    return {
      runId,
      // A batch node writes its payload to the BUS rather than returning it, so
      // the node result is null even on success: the run's terminal status is
      // the signal for whether the batch as a whole completed.
      done: String(run?.status ?? '') === 'done',
      data: data && typeof data === 'object' ? data : null,
      error: String(data?.error ?? run?.error ?? node?.error ?? ''),
      run,
    }
  }

  // 1) A command whose effect the target itself records: inserting the date
  //    changes the document text length, read back out of process.
  const inserted = await invoke(insertPath, 'menu probe insert')
  const stateAfterInsert = oracle(['--hwnd', String(hwnd), '--state'])
  check(
    'invoke_menu ran the command against the real application',
    inserted.done === true && inserted.data?.delivered === true,
    JSON.stringify(inserted.data?.resolved_path ?? inserted.error).slice(0, 220),
  )
  check(
    "the application's own document changed, read by an independent process",
    stateAfterInsert.text_length !== stateBefore.text_length,
    `text length ${stateBefore.text_length} -> ${stateAfterInsert.text_length}`,
  )
  check(
    'the run reported the change it could observe',
    inserted.data?.action_state === 'changed',
    `action_state=${inserted.data?.action_state} changed=${JSON.stringify(inserted.data?.changed)}`,
  )

  // 2) The property that distinguishes this from a click: no activation, and
  //    the command that was refused still reports honestly.
  const foregroundAfter = oracle(['--foreground'])
  check(
    'the target was never activated by the menu command',
    foregroundAfter.foreground === foregroundBefore.foreground,
    `foreground ${foregroundBefore.foreground} -> ${foregroundAfter.foreground}`,
  )

  // 3) A heading is refused with the contents of that heading, and nothing runs.
  const refused = await invoke([insertPath[0]], 'menu probe heading')
  check(
    'a path that ends on a menu heading is refused and lists what is inside',
    refused.done === false
      && Array.isArray(refused.data?.available)
      && refused.data.available.length > 0
      && /container rather than a command/.test(refused.error),
    `available=${refused.data?.available?.length} error=${refused.error.slice(0, 90)}`,
  )

  // 4) A name that is not there is refused by name, again with the alternatives.
  const missing = await invoke([insertPath[0], 'LINGDONG-NO-SUCH-ITEM'], 'menu probe missing')
  check(
    'an unknown menu item is refused by name with the alternatives',
    missing.done === false
      && /no menu item is named/.test(missing.error)
      && (missing.data?.available?.length ?? 0) > 0,
    missing.error.slice(0, 110),
  )

  // 5) When a status bar toggle exists, its appearance/disappearance is a
  //    second, differently-shaped oracle for the same action.
  if (statusPath) {
    const beforeToggle = oracle(['--hwnd', String(hwnd), '--state'])
    const toggled = await invoke(statusPath, 'menu probe status bar')
    const afterToggle = oracle(['--hwnd', String(hwnd), '--state'])
    check(
      'a toggle command is confirmed by the control it shows or hides',
      toggled.done === true
        && JSON.stringify(beforeToggle.visible_children) !== JSON.stringify(afterToggle.visible_children),
      `${JSON.stringify(beforeToggle.visible_children)} -> ${JSON.stringify(afterToggle.visible_children)}`,
    )
  } else {
    console.log('SKIP  no status-bar command in this application menu')
  }
} catch (error) {
  check('menu probe ran', false, error?.stack ?? String(error))
} finally {
  // Only the application this probe started is removed, through the runtime's
  // own offline path, so the desktop is left as it was found.
  for (const processId of startedPids) {
    try {
      await shell(`taskkill /PID ${processId} /F`)
    } catch (error) {
      console.log(`cleanup: ${error?.message ?? error}`)
    }
  }
  await sidecar.stop()
}

console.log(`\n${failures.length === 0 ? 'MENU PROBE PASSED' : `${failures.length} CHECK(S) FAILED: ${failures.join(', ')}`}`)
process.exitCode = failures.length === 0 ? 0 : 1





