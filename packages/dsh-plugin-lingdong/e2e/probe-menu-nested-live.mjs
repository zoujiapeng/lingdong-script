#!/usr/bin/env node
/**
 * Prove `invoke_menu` on a three-level native Win32 menu.
 *
 * Notepad offered two levels. This drives a window whose third level has no
 * accessibility node at all, so a driver that must click an addressable element
 * has nothing to click. The oracle is the window's own title, read by a process
 * that neither drove the menu nor built the window.
 *
 * Usage:  node packages/dsh-plugin-lingdong/e2e/probe-menu-nested-live.mjs
 */
import { spawn, spawnSync } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import os from 'node:os'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

import { resolveConfig } from '../src/config.mjs'
import { WinPilotSidecar } from '../src/sidecar.mjs'
import { createClient } from '../src/client.mjs'
import { actionBatchGraph } from '../src/tools.mjs'

const here = path.dirname(fileURLToPath(import.meta.url))
const BUILDER = path.join(here, 'probe-menu-nested.py')
const ORACLE = path.join(here, 'menu-oracle.py')

const failures = []
const check = (label, ok, detail) => {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}${detail ? ` — ${detail}` : ''}`)
  if (!ok) failures.push(label)
}

function oracle(args) {
  const result = spawnSync('python', [ORACLE, ...args], { encoding: 'utf8', shell: false })
  if (result.status !== 0) throw new Error(`oracle failed: ${result.stderr || result.stdout}`)
  return JSON.parse(String(result.stdout).trim().split('\n').pop())
}

const settings = resolveConfig({ port: Number(process.env.LINGDONG_NESTED_PORT || 8801) })
const sidecar = new WinPilotSidecar(settings, console)
const client = createClient(settings, sidecar)
let probe = null
let pid = 0
const workdir = mkdtempSync(path.join(os.tmpdir(), 'lingdong-nested-'))

try {
  await sidecar.ensure()

  // The builder prints the window it created and keeps it alive for this run.
  probe = spawn('python', [BUILDER, '--serve', '--workdir', workdir], { stdio: ['ignore', 'pipe', 'pipe'], shell: false })
  const first = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('probe window did not report within 60s')), 60_000)
    let buffered = ''
    probe.stdout.on('data', (chunk) => {
      buffered += String(chunk)
      const match = buffered.match(/PROBE_WINDOW hwnd=(\d+) pid=(\d+)/)
      if (match) { clearTimeout(timer); resolve({ hwnd: Number(match[1]), pid: Number(match[2]) }) }
    })
    probe.stderr.on('data', (chunk) => { process.stderr.write(`[probe] ${chunk}`) })
    probe.on('exit', (code) => { clearTimeout(timer); reject(new Error(`probe exited early (${code})`)) })
  })
  const { hwnd } = first
  pid = first.pid
  check('a native three-level menu window is up', Boolean(hwnd), `hwnd=${hwnd} pid=${pid}`)

  // Read both views of that one menu, so the gap is stated as a measurement.
  const win32 = oracle(['--hwnd', String(hwnd), '--dump'])
  const uia = oracle(['--hwnd', String(hwnd), '--uia'])
  const flat = []
  const flatten = (items, depth) => {
    for (const item of items) {
      if (item.separator) continue
      flat.push({ depth, text: item.text, id: item.id })
      flatten(item.children ?? [], depth + 1)
    }
  }
  flatten(win32.items ?? [], 1)
  check(
    'the Win32 menu exposes a third level',
    flat.some((item) => item.depth === 3),
    flat.map((item) => `${item.depth}:${item.text}`).join(' '),
  )
  check(
    'the accessibility tree exposes no submenu items for the same menu',
    (uia.levels ?? []).every((entry) => entry.level === 1 && entry.children === 0),
    `uia items: ${(uia.levels ?? []).map((entry) => entry.name).join(',') || 'none'}`,
  )

  // Drive it by path, naming the menu the way a person would.
  const invoke = async (pathSegments, label) => {
    const submitted = await client.post('/api/v2/arp/runs', {
      graph: actionBatchGraph({
        actions: [{ action: 'invoke_menu', hwnd, path: pathSegments }],
        name: label,
      }),
      inputs: {},
    }, { timeoutMs: 60_000 })
    const runId = submitted.run?.run_id ?? submitted.run_id
    const envelope = runId ? await client.waitForRun(runId, { timeoutMs: 60_000 }) : {}
    const run = envelope?.run ?? {}
    const event = (run.timeline ?? []).filter((entry) => entry.type === 'agent_batch_action').pop()
    let recorded = null
    try { recorded = event?.summary ? JSON.parse(event.summary) : null } catch { recorded = null }
    return { done: String(run.status ?? '') === 'done', data: recorded, error: String(recorded?.error ?? run.error ?? '') }
  }

  // 1) A third-level command, addressed by its labels.
  const ran = await invoke(['File', 'Export', 'As text'], 'nested export text')
  check(
    'the third-level command ran, confirmed by the window renaming itself',
    ran.done === true && oracle(['--hwnd', String(hwnd), '--state']).title === 'RAN:file-export-text',
    `title=${oracle(['--hwnd', String(hwnd), '--state']).title}`,
  )
  check(
    'the run resolved all three levels',
    ran.data?.resolved_path?.length === 3 && ran.data?.command === 102,
    JSON.stringify(ran.data?.resolved_path?.map((entry) => entry.text) ?? ran.error),
  )

  // 2) A sibling leaf, to show it is the path that selects, not position luck.
  const sibling = await invoke(['File', 'Export', 'As csv'], 'nested export csv')
  check(
    'a sibling third-level command resolves to its own id',
    sibling.done === true && sibling.data?.command === 103
      && oracle(['--hwnd', String(hwnd), '--state']).title === 'RAN:file-export-csv',
    `command=${sibling.data?.command} title=${oracle(['--hwnd', String(hwnd), '--state']).title}`,
  )

  // 3) The same commands addressed by index, for a locale this driver cannot read.
  const byIndex = await invoke([0, 1, 0], 'nested by index')
  check(
    'the same third-level command is reachable by index alone',
    byIndex.done === true && byIndex.data?.command === 102
      && oracle(['--hwnd', String(hwnd), '--state']).title === 'RAN:file-export-text',
    `path=[0,1,0] command=${byIndex.data?.command}`,
  )

  // 4) A refused path still names what was available at the level it failed on.
  const refused = await invoke(['File', 'Export', 'As pdf'], 'nested refuse')
  check(
    'a wrong leaf name is refused with that menu level contents',
    refused.done === false
      && (refused.data?.available?.length ?? 0) === 2
      && refused.data.available.every((entry) => typeof entry.text === 'string'),
    `available=${JSON.stringify(refused.data?.available?.map((entry) => entry.text) ?? refused.error)}`,
  )
} catch (error) {
  check('nested menu probe ran', false, error?.stack ?? String(error))
} finally {
  // Stopping the builder is enough: the probe window is bound to the builder's
  // job object, so Windows takes it down with it. That is verified below rather
  // than assumed, because a probe that leaks a window is worse than no probe.
  if (probe && probe.exitCode === null) { try { probe.kill() } catch { /* already gone */ } }
  await new Promise((resolve) => setTimeout(resolve, 1200))
  if (pid) {
    const listed = spawnSync('tasklist', ['/FI', `PID eq ${pid}`, '/NH'], { encoding: 'utf8', shell: false })
    if (String(listed.stdout ?? '').includes(String(pid))) {
      spawnSync('taskkill', ['/PID', String(pid), '/T', '/F'], { encoding: 'utf8', shell: false })
    }
  }
  check(
    'the probe left nothing behind',
    !spawnSync('tasklist', ['/FI', 'IMAGENAME eq DeepMenu.exe', '/NH'], { encoding: 'utf8', shell: false })
      .stdout?.includes('DeepMenu.exe'),
    'no probe window remains',
  )
  rmSync(workdir, { recursive: true, force: true })
  await sidecar.stop()
}

console.log(`\n${failures.length === 0 ? 'NESTED MENU PROBE PASSED' : `${failures.length} CHECK(S) FAILED: ${failures.join(', ')}`}`)
process.exitCode = failures.length === 0 ? 0 : 1
