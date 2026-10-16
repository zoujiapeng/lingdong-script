/**
 * Is the runtime's window geometry virtualized by display scaling?
 *
 * Both readers address the SAME window handle, so a disagreement cannot be two
 * different windows: the runtime that performed the change, and an independent
 * Win32 reader in another process.
 */
import { spawn, spawnSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { resolveConfig } from '../src/config.mjs'
import { WinPilotSidecar } from '../src/sidecar.mjs'
import { createClient } from '../src/client.mjs'
import { actionBatchGraph } from '../src/tools.mjs'

const HERE = path.dirname(fileURLToPath(import.meta.url))

/** Independent Win32 read of one exact window handle, from another process. */
function externalRect(hwnd, aware = false) {
  const script = `
Add-Type @"
using System; using System.Runtime.InteropServices;
public class W {
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr c);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
}
"@
${aware ? '[void][W]::SetProcessDpiAwarenessContext([IntPtr](-4))' : '# reader left at its default DPI awareness'}
$h = [IntPtr]${hwnd}
if (-not [W]::IsWindow($h)) { 'gone' } else {
  $r = New-Object W+RECT
  [void][W]::GetWindowRect($h, [ref]$r)
  "$($r.L),$($r.T),$($r.R),$($r.B),$([W]::GetDpiForWindow($h))"
}
`
  const out = spawnSync('powershell', ['-NoProfile', '-NonInteractive', '-Command', script], { encoding: 'utf8', timeout: 30_000 })
  const raw = String(out.stdout).trim()
  if (raw === 'gone' || !raw) return null
  const parts = raw.split(',').map(Number)
  if (parts.length !== 5 || parts.some((value) => !Number.isFinite(value))) return null
  return { left: parts[0], top: parts[1], width: parts[2] - parts[0], height: parts[3] - parts[1], dpi: parts[4] }
}

const settings = resolveConfig({ port: 8790 })
const sidecar = new WinPilotSidecar(settings, { info() {}, warn() {}, error() {} })
const client = createClient(settings, sidecar)
await sidecar.ensure()

const target = spawn('powershell', ['-NoProfile', '-STA', '-File', path.join(HERE, 'target.ps1'), '-Token', 'DPI-PROBE', '-Mode', 'text'], { stdio: 'ignore' })
await new Promise((r) => setTimeout(r, 7000))

const windows = (await client.get('/api/v2/desktop/windows')).windows ?? []
const matches = windows.filter((w) => String(w.title).includes('LD Target'))
console.log(`target windows the runtime sees: ${matches.length}`, matches.map((w) => `${w.hwnd}/${w.pid}`).join(' '))
const entry = matches[0]
if (!entry) {
  console.log('no target window; aborting')
  target.kill()
  await sidecar.stop()
  process.exit(1)
}
const hwnd = entry.hwnd

const readBoth = async (label) => {
  const runtime = await client.get(`/api/v2/desktop/window?hwnd=${hwnd}`)
  const unaware = externalRect(hwnd, false)
  const aware = externalRect(hwnd, true)
  console.log(`${label}`)
  console.log(`   runtime              rect ${JSON.stringify(runtime?.window?.rect ?? null)}`)
  console.log(`   external (unaware)   rect ${JSON.stringify(unaware)}`)
  console.log(`   external (dpi-aware) rect ${JSON.stringify(aware)}`)
  return { runtime: runtime?.window?.rect ?? null, unaware, aware }
}

await readBoth('before')

const REQUEST = { x: 200, y: 150, width: 640, height: 480 }
const started = await client.post('/api/v2/arp/runs', {
  graph: actionBatchGraph({ actions: [{ action: 'set_window_frame', hwnd, ...REQUEST }], name: 'dpi probe' }),
  inputs: {},
})
const runId = started.run?.run_id ?? started.run_id
await client.waitForRun(runId, { timeoutMs: 60_000 })
const detail = await client.get(`/api/v2/arp/runs/${encodeURIComponent(runId)}`)
const json = JSON.stringify(detail.run ?? detail)
console.log('requested       :', JSON.stringify(REQUEST))
console.log('action reported :', json.match(/"after":\{[^}]*\}/)?.[0] ?? '(none)')
const after = await readBoth('after')

const judge = (rect, who) => {
  if (!rect) return `${who}: no reading`
  const phys = rect.width === undefined ? rect[2] - rect[0] : rect.width
  const ok = Math.abs(phys - REQUEST.width) <= 2
  return `${who}: width ${phys} -> ${ok ? 'matches the request' : 'does NOT match'}`
}
console.log(`\nrequested width ${REQUEST.width}`)
console.log(judge(after.runtime, 'runtime            '))
console.log(judge(after.unaware, 'external (unaware) '))
console.log(judge(after.aware, 'external (dpi-aware)'))

target.kill()
await sidecar.stop()
