#!/usr/bin/env node
/**
 * Does a DPI-unaware shell child move a window to scaled coordinates?
 *
 * The agent arm fails `window_frame` the same way every round: it asks for an
 * outer frame of x=200 y=150 640x480 and the window ends up at 250,188 800x600,
 * which is exactly 1.25x on all four numbers - this machine's display scale.
 * The agent reaches the window through `run_shell`, and that child process never
 * declares DPI awareness, so the hypothesis is that Windows virtualizes the
 * coordinates it passes to `MoveWindow`.
 *
 * This probe tests only that mechanism, with no model, no runtime and no
 * sidecar: one window, one handle, two shells that differ only in whether they
 * declare DPI awareness.
 *
 *   node packages/dsh-plugin-lingdong/e2e/probe-shell-dpi.mjs
 */
import { spawn, spawnSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const REQUEST = { x: 200, y: 150, width: 640, height: 480 }
const SCALE = 1.25

/** The declaration both shells receive; `aware` is the only difference. */
const PRELUDE = `
Add-Type @"
using System; using System.Text; using System.Runtime.InteropServices;
public class W {
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr h, int x, int y, int w, int hh, bool repaint);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr c);
  [DllImport("user32.dll")] public static extern IntPtr GetProcessDpiAwarenessContext();
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
}
"@
`

/**
 * Run one PowerShell with a fixed DPI declaration.
 *
 * -2/-4 are the `DPI_AWARENESS_CONTEXT` values for per-monitor; `aware: false`
 * leaves the default (unaware) in place, which is what a plain spawned
 * powershell.exe gets. `compatLayer` exercises the environment-variable route
 * instead of an in-process P/Invoke, because that route costs nothing per call.
 */
function ps(body, { aware, compatLayer }) {
  const script = `${PRELUDE}${aware ? '[void][W]::SetProcessDpiAwarenessContext([IntPtr](-4))' : '# default: DPI unaware'}\n${body}`
  const out = spawnSync('powershell', ['-NoProfile', '-NonInteractive', '-Command', script], {
    encoding: 'utf8',
    timeout: 30_000,
    windowsHide: true,
    env: compatLayer ? { ...process.env, __COMPAT_LAYER: compatLayer } : process.env,
  })
  return String(out.stdout ?? '').trim()
}

function findTarget() {
  const listing = ps(`
$found = @()
Get-Process | Where-Object { $_.MainWindowHandle -ne 0 } | ForEach-Object {
  $sb = New-Object System.Text.StringBuilder 256
  [void][W]::GetWindowText($_.MainWindowHandle, $sb, 256)
  $t = $sb.ToString()
  if ($t -like '*LD Target*') { $found += "$($_.MainWindowHandle)|$t" }
}
$found -join "\`n"
`, { aware: true })
  const first = listing.split('\n').map((line) => line.trim()).filter(Boolean)[0]
  if (!first) return null
  const [hwnd, ...rest] = first.split('|')
  return { hwnd: Number(hwnd), title: rest.join('|') }
}

function rectOf(hwnd) {
  const raw = ps(`
$h = [IntPtr]${hwnd}
$r = New-Object W+RECT
[void][W]::GetWindowRect($h, [ref]$r)
"$($r.L),$($r.T),$($r.R),$($r.B),$([W]::GetDpiForWindow($h))"
`, { aware: true })
  const parts = raw.split(',').map(Number)
  if (parts.length !== 5 || parts.some((value) => !Number.isFinite(value))) return null
  return {
    x: parts[0], y: parts[1], width: parts[2] - parts[0], height: parts[3] - parts[1], dpi: parts[4],
  }
}

function move(hwnd, { aware, compatLayer }) {
  ps(`
[void][W]::MoveWindow([IntPtr]${hwnd}, ${REQUEST.x}, ${REQUEST.y}, ${REQUEST.width}, ${REQUEST.height}, $true)
'moved'
`, { aware, compatLayer })
}

const expectedScaled = {
  x: Math.round(REQUEST.x * SCALE),
  y: Math.round(REQUEST.y * SCALE),
  width: Math.round(REQUEST.width * SCALE),
  height: Math.round(REQUEST.height * SCALE),
}

const target = spawn('powershell', ['-NoProfile', '-STA', '-File', path.join(HERE, 'target.ps1'),
  '-Token', 'DPI-SHELL', '-Mode', 'text'], { stdio: 'ignore' })
// A hidden console suppresses the WinForms window this probe needs, so the
// child is left visible and the handle is polled instead of assumed.
let found = null
for (let attempt = 0; attempt < 20 && !found; attempt += 1) {
  await new Promise((resolve) => setTimeout(resolve, 700))
  found = findTarget()
}
if (!found) {
  console.log('no "LD Target" window appeared; aborting')
  target.kill()
  process.exit(1)
}
console.log(`target    : hwnd=${found.hwnd} "${found.title}"`)
console.log(`requested : ${JSON.stringify(REQUEST)}`)
console.log(`if scaled by ${SCALE}: ${JSON.stringify(expectedScaled)}\n`)

const results = []
const conditions = [
  { label: 'dpi-UNAWARE shell          ', aware: false },
  { label: 'dpi-AWARE   shell          ', aware: true },
  { label: 'UNAWARE + __COMPAT_LAYER  ', aware: false, compatLayer: 'HIGHDPIAWARE' },
]
for (const condition of conditions) {
  move(found.hwnd, condition)
  await new Promise((resolve) => setTimeout(resolve, 800))
  const rect = rectOf(found.hwnd)
  const matches = rect
    && Math.abs(rect.x - REQUEST.x) <= 2 && Math.abs(rect.y - REQUEST.y) <= 2
    && Math.abs(rect.width - REQUEST.width) <= 2 && Math.abs(rect.height - REQUEST.height) <= 2
  const scaled = rect
    && Math.abs(rect.x - expectedScaled.x) <= 2 && Math.abs(rect.y - expectedScaled.y) <= 2
    && Math.abs(rect.width - expectedScaled.width) <= 2 && Math.abs(rect.height - expectedScaled.height) <= 2
  console.log(`${condition.label} -> ${JSON.stringify(rect)}  ${matches ? 'EXACT' : scaled ? 'SCALED x' + SCALE : 'neither'}`)
  results.push({ ...condition, rect, matches, scaled })
}

console.log('')
const unaware = results.find((r) => !r.aware && !r.compatLayer)
const aware = results.find((r) => r.aware)
const compat = results.find((r) => r.compatLayer)
console.log(`verdict: a DPI-unaware shell ${unaware?.scaled ? 'DOES' : 'does NOT'} scale window coordinates by this display's factor`)
console.log(`         an in-process declaration ${aware?.matches ? 'fixes it' : 'does NOT fix it'}`)
console.log(`         __COMPAT_LAYER=${compat?.compatLayer} ${compat?.matches ? 'fixes it too (zero per-call cost)' : 'does NOT fix it'}`)
// Exit 0 only when the defect reproduces and at least one cheaper fix works.
process.exitCode = unaware?.scaled && aware?.matches ? 0 : 2

target.kill()
