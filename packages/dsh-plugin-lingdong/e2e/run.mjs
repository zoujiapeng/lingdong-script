#!/usr/bin/env node
/**
 * Paired end-to-end comparison: this plugin against the shipped Cua driver.
 *
 * Both arms run the same task, on the same machine, under the same model, in a
 * scratch profile created from the headless template. Nothing about the run is
 * the judge: every task has an oracle that reads the world afterwards - a file's
 * bytes, the clipboard, a window rectangle - and the agent never sees it. A run
 * that claims success and left nothing behind fails.
 *
 * Usage:
 *   node packages/dsh-plugin-lingdong/e2e/run.mjs                 # both arms, all tasks
 *   node packages/dsh-plugin-lingdong/e2e/run.mjs --arm lingdong  # one arm
 *   node packages/dsh-plugin-lingdong/e2e/run.mjs --task calculator_clipboard
 *   node packages/dsh-plugin-lingdong/e2e/run.mjs --list
 */
import { spawn, spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { randomBytes } from 'node:crypto'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const PLUGIN = path.resolve(HERE, '..')
const REPO = path.resolve(PLUGIN, '..', '..')
const WORK = path.join(REPO, 'output', 'e2e')
const TARGET_PS1 = path.join(HERE, 'target.ps1')

const ARMS = {
  lingdong: { profile: 'ld-e2e', patch: path.join(HERE, 'lingdong.patch.yml') },
  cua: { profile: 'cua-e2e', patch: path.join(HERE, 'cua.patch.yml') },
}

const RUN_TIMEOUT_MS = Number(process.env.LD_E2E_TIMEOUT_MS || 420_000)

function token(label) {
  return `LD-${label}-${randomBytes(3).toString('hex').toUpperCase()}`
}

function text(value) {
  return String(value ?? '')
}

/** Read a file the task was supposed to produce, or null. */
function produced(name) {
  const file = path.join(WORK, name)
  if (!existsSync(file)) return null
  try {
    return readFileSync(file, 'utf8')
  } catch {
    return null
  }
}

/** Compare a produced file against what the task asked for. */
function fileIs(name, expected) {
  const got = produced(name)
  if (got === null) return { pass: false, detail: `${name} was not created` }
  const normalized = got.replace(/\r\n/g, '\n').trim()
  return normalized === expected
    ? { pass: true, detail: `${normalized.length} bytes match` }
    : { pass: false, detail: `expected ${JSON.stringify(expected)} got ${JSON.stringify(normalized.slice(0, 80))}` }
}

/** Read the clipboard as plain text, through the runtime that owns the desktop. */
function clipboardText() {
  const script = `
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Clipboard]::GetText()
`
  const result = spawnSync('powershell', ['-NoProfile', '-NonInteractive', '-Command', script], {
    encoding: 'utf8',
    timeout: 20_000,
  })
  return text(result.stdout).replace(/\r\n/g, '\n').trim()
}

/**
 * The outer rectangle of a window by title fragment, read from Win32.
 *
 * The reader declares per-monitor DPI awareness first. Without it Windows
 * virtualizes the coordinates it reports and every measurement comes back
 * multiplied by the display scale - on a 125% display a window that really is
 * 640 px wide is reported as 512, and an oracle that believes it fails a
 * perfectly good action.
 */
function windowRect(fragment) {
  const script = `
$ErrorActionPreference='Stop'
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class W {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr p);
  [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr c);
  public delegate bool EnumProc(IntPtr h, IntPtr p);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
}
"@
[void][W]::SetProcessDpiAwarenessContext([IntPtr](-4))
$found = $null
$cb = [W+EnumProc]{
  param($h, $p)
  if (-not [W]::IsWindowVisible($h)) { return $true }
  $len = [W]::GetWindowTextLength($h)
  if ($len -le 0) { return $true }
  $sb = New-Object System.Text.StringBuilder ($len + 1)
  [void][W]::GetWindowText($h, $sb, $sb.Capacity)
  if ($sb.ToString() -like "*${fragment}*") {
    $r = New-Object W+RECT
    [void][W]::GetWindowRect($h, [ref]$r)
    $script:found = "$($r.L),$($r.T),$($r.R),$($r.B)"
    return $false
  }
  return $true
}
[void][W]::EnumWindows($cb, [IntPtr]::Zero)
$found
`
  const result = spawnSync('powershell', ['-NoProfile', '-NonInteractive', '-Command', script], {
    encoding: 'utf8',
    timeout: 30_000,
  })
  const parts = text(result.stdout).trim().split(',').map(Number)
  if (parts.length !== 4 || parts.some((value) => !Number.isFinite(value))) return null
  return { left: parts[0], top: parts[1], width: parts[2] - parts[0], height: parts[3] - parts[1] }
}

/** Kill any process whose name matches, ignoring failures. */
function killByName(names) {
  for (const name of names) {
    spawnSync('powershell', ['-NoProfile', '-Command', `Get-Process -Name ${name} -ErrorAction SilentlyContinue | Stop-Process -Force`], { timeout: 20_000 })
  }
}

/** Start the GUI target window and wait until it is on screen. */
async function startTarget(mode, value) {
  const child = spawn('powershell', ['-NoProfile', '-STA', '-File', TARGET_PS1, '-Token', value, '-Mode', mode], {
    stdio: 'ignore',
    windowsHide: false,
  })
  const deadline = Date.now() + 25_000
  while (Date.now() < deadline) {
    if (windowRect('LD Target')) return child
    await sleep(400)
  }
  child.kill()
  throw new Error('the GUI target window never appeared')
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/** One task: a goal a person could give, plus the oracle that judges it. */
function buildTasks() {
  return [
    {
      id: 'shell_control',
      // Not a GUI task. It exists so a harness difference shows up as a harness
      // difference instead of as a desktop failure.
      async setup() {
        const value = token('T6')
        return { value }
      },
      goal: ({ value }) => `Use a shell command to write exactly this text, and nothing else, to the file ${path.join(WORK, 't6.txt')}: ${value}`,
      async oracle({ value }) {
        return fileIs('t6.txt', value)
      },
    },
    {
      id: 'notepad_save',
      async setup() {
        const value = token('T1')
        return { value }
      },
      goal: ({ value }) => `Open Notepad, type exactly ${value} into it, and save the document to ${path.join(WORK, 't1.txt')}.`,
      async oracle({ value }) {
        return fileIs('t1.txt', value)
      },
      async teardown() {
        // The document was saved, so nothing is lost by closing it. Leaving it
        // open would put windows on someone's desktop and also change what the
        // next launch does: launching Notepad while one is already running can
        // adopt the existing window instead of creating a fresh one.
        killByName(['notepad'])
      },
    },
    {
      id: 'calculator_clipboard',
      async setup() {
        killByName(['CalculatorApp', 'Calculator'])
        await sleep(500)
        return {}
      },
      goal: () => 'Using the Windows Calculator application, work out 43 multiplied by 17, then copy the result to the clipboard and leave it there.',
      async oracle() {
        const got = clipboardText()
        return got === '731'
          ? { pass: true, detail: 'clipboard holds 731' }
          : { pass: false, detail: `clipboard holds ${JSON.stringify(got.slice(0, 40))}` }
      },
    },
    {
      id: 'read_target_text',
      async setup() {
        const value = token('T3')
        const child = await startTarget('text', value)
        return { value, child }
      },
      goal: () => `A window titled "LD Target" is open on the desktop. It displays a token. Find that token and write it, and nothing else, to ${path.join(WORK, 't3.txt')}.`,
      async oracle({ value }) {
        return fileIs('t3.txt', value)
      },
      async teardown({ child }) {
        // Only the process this task started: killing every powershell would
        // also kill whatever the run under test is doing at that moment.
        child?.kill()
      },
    },
    {
      id: 'read_target_pixels',
      async setup() {
        const value = token('T4')
        const child = await startTarget('pixels', value)
        return { value, child }
      },
      goal: () => `A window titled "LD Target" is open on the desktop. It shows a code drawn on the screen. Read that code and write it, and nothing else, to ${path.join(WORK, 't4.txt')}.`,
      async oracle({ value }) {
        return fileIs('t4.txt', value)
      },
      async teardown({ child }) {
        // Only the process this task started: killing every powershell would
        // also kill whatever the run under test is doing at that moment.
        child?.kill()
      },
    },
    {
      id: 'browser_confirm',
      // A page whose answer only exists if its confirmation dialog was really
      // accepted. A browser that silently dismisses dialogs gets a different
      // title, so this cannot pass by accident.
      async setup() {
        const value = token('T8')
        const source = readFileSync(path.join(HERE, 'confirm.html'), 'utf8')
        const page = path.join(WORK, 'confirm.html')
        writeFileSync(page, source.replace(/__TOKEN__/g, value), 'utf8')
        killByName(['chrome', 'msedge'])
        await sleep(800)
        return { value, url: `file:///${page.replace(/\\/g, '/')}` }
      },
      goal: ({ url }) => `Open this local page in a browser and ACCEPT the confirmation dialog it shows: ${url} . Then write the page's final title, and nothing else, to ${path.join(WORK, 't8.txt')}.`,
      async oracle({ value }) {
        return fileIs('t8.txt', value)
      },
      async teardown() {
        killByName(['chrome', 'msedge'])
      },
    },
    {
      id: 'window_frame',
      async setup() {
        const value = token('T5')
        const child = await startTarget('text', value)
        return { value, child }
      },
      goal: () => `Move and resize the window titled "LD Target" so that its outer frame is exactly x=200, y=150, width=640, height=480. Do not leave it any other size.`,
      async oracle() {
        const rect = windowRect('LD Target')
        if (!rect) return { pass: false, detail: 'the target window is gone' }
        const ok = Math.abs(rect.left - 200) <= 2 && Math.abs(rect.top - 150) <= 2
          && Math.abs(rect.width - 640) <= 2 && Math.abs(rect.height - 480) <= 2
        return ok
          ? { pass: true, detail: `frame is ${rect.left},${rect.top} ${rect.width}x${rect.height}` }
          : { pass: false, detail: `frame is ${rect.left},${rect.top} ${rect.width}x${rect.height}` }
      },
      async teardown({ child }) {
        // Only the process this task started: killing every powershell would
        // also kill whatever the run under test is doing at that moment.
        child?.kill()
      },
    },
  ]
}

/** Run one arm on one task and judge it by the oracle alone. */
async function runOne(armName, task) {
  const arm = ARMS[armName]
  const context = await task.setup()
  const goal = task.goal(context)
  const started = Date.now()
  let stdout = ''
  let stderr = ''
  let timedOut = false

  await new Promise((resolve) => {
    const child = spawn('dsh', ['--profile', arm.profile, '--patch', arm.patch, goal], {
      cwd: REPO,
      shell: true,
      windowsHide: true,
    })
    const timer = setTimeout(() => {
      timedOut = true
      child.kill()
    }, RUN_TIMEOUT_MS)
    child.stdout.on('data', (chunk) => { stdout += chunk })
    child.stderr.on('data', (chunk) => { stderr += chunk })
    child.on('close', () => { clearTimeout(timer); resolve() })
    child.on('error', () => { clearTimeout(timer); resolve() })
  })

  const elapsed = Date.now() - started
  let verdict
  try {
    verdict = await task.oracle(context)
  } catch (error) {
    verdict = { pass: false, detail: `oracle failed: ${error.message}` }
  }
  await task.teardown?.(context)
  await sleep(600)

  return {
    arm: armName,
    task: task.id,
    pass: verdict.pass,
    detail: verdict.detail,
    elapsed_ms: elapsed,
    timed_out: timedOut,
    said: stdout.trim().split('\n').filter(Boolean).slice(-1)[0]?.slice(0, 200) ?? '',
    // What makes a failure diagnosable without re-running it: a claim is not
    // evidence, but which tools were called and what they answered is.
    transcript: stdout.trim().slice(-6000),
    stderr: stderr.trim().slice(-1500),
  }
}

async function main() {
  const argv = process.argv.slice(2)
  const only = argv.includes('--task') ? argv[argv.indexOf('--task') + 1] : null
  const armArg = argv.includes('--arm') ? argv[argv.indexOf('--arm') + 1] : null
  const all = buildTasks()
  if (argv.includes('--list')) {
    for (const task of all) console.log(task.id)
    return
  }
  const tasks = only ? all.filter((task) => task.id === only) : all
  const arms = armArg ? [armArg] : Object.keys(ARMS)

  mkdirSync(WORK, { recursive: true })
  const results = []
  for (const task of tasks) {
    for (const arm of arms) {
      // Every attempt starts from nothing: an artifact left by the previous arm
      // must never be able to pass the next one's oracle.
      for (const leftover of ['t1.txt', 't3.txt', 't4.txt', 't5.txt', 't6.txt', 't8.txt']) {
        rmSync(path.join(WORK, leftover), { force: true })
      }
      spawnSync('powershell', ['-NoProfile', '-Command', 'Set-Clipboard -Value ""'], { timeout: 15_000 })
      process.stdout.write(`… ${task.id} / ${arm} `)
      const result = await runOne(arm, task)
      results.push(result)
      console.log(`${result.pass ? 'PASS' : 'FAIL'}  (${Math.round(result.elapsed_ms / 1000)}s) ${result.detail}`)
    }
  }

  const report = path.join(WORK, `report-${Date.now()}.json`)
  writeFileSync(report, JSON.stringify(results, null, 2), 'utf8')
  console.log('\n=== summary ===')
  for (const arm of arms) {
    const rows = results.filter((row) => row.arm === arm)
    const passed = rows.filter((row) => row.pass).length
    console.log(`${arm.padEnd(9)} ${passed}/${rows.length}`)
  }
  console.log(`report: ${report}`)
}

await main()
