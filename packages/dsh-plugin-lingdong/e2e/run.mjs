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
import { createServer } from 'node:http'
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

/** Whether a specific process id is still running, read from outside the run. */
function pidIsAlive(pid) {
  const result = spawnSync('powershell', ['-NoProfile', '-NonInteractive', '-Command',
    `if (Get-Process -Id ${Number(pid)} -ErrorAction SilentlyContinue) { 'alive' } else { 'gone' }`], {
    encoding: 'utf8',
    timeout: 20_000,
  })
  return text(result.stdout).trim() === 'alive'
}

/**
 * Build the menu target if it is not already built.
 *
 * The harness owns this build: the point of the task is to drive a menu, not to
 * compile a program, and a task that made the agent build its own target would
 * be measuring the wrong thing.
 */
function ensureMenuTarget() {
  const exe = path.join(WORK, 'ld-menu-target.exe')
  if (existsSync(exe)) return exe
  mkdirSync(WORK, { recursive: true })
  const result = spawnSync(
    'C:\\Windows\\Microsoft.NET\\Framework64\\v4.0.30319\\csc.exe',
    ['/nologo', '/target:winexe', `/out:${exe}`,
     '/reference:System.Windows.Forms.dll,System.Drawing.dll',
     path.join(HERE, 'menu_target.cs')],
    { encoding: 'utf8', timeout: 120_000 },
  )
  if (!existsSync(exe)) {
    throw new Error(`the menu target could not be built: ${text(result.stderr || result.stdout).slice(0, 300)}`)
  }
  return exe
}

/** Start the menu target window and wait until it is on screen. */
async function startMenuTarget(out, tokenValue) {
  const exe = ensureMenuTarget()
  const child = spawn(exe, [out, tokenValue], { stdio: 'ignore', windowsHide: false })
  const deadline = Date.now() + 25_000
  while (Date.now() < deadline) {
    if (windowRect('LD Menu Target')) return child
    await sleep(400)
  }
  child.kill()
  throw new Error('the menu target window never appeared')
}

/** Start a Notepad window and return the process, so its end is checkable. */
async function startNotepad() {
  const child = spawn('notepad.exe', { stdio: 'ignore', windowsHide: false, detached: false })
  const deadline = Date.now() + 20_000
  while (Date.now() < deadline) {
    if (windowRect('Notepad') || windowRect('记事本')) return child
    await sleep(300)
  }
  return child
}

/**
 * A site with one page behind a session cookie.
 *
 * The login form posts to the server, which answers with a session cookie and
 * the token. The protected page requires that cookie. Nothing about the session
 * lives in the page, so a browser that loses its cookies cannot read the token
 * again - which is what makes this a test of the browser session rather than of
 * the page.
 */
function startSessionSite(tokenValue) {
  const server = createServer((request, response) => {
    const url = new URL(request.url ?? '/', 'http://127.0.0.1')
    const cookies = String(request.headers.cookie ?? '')
    const signedIn = cookies.includes('ld_session=ok')

    if (url.pathname === '/signin' && request.method === 'POST') {
      response.writeHead(200, {
        'content-type': 'text/html; charset=utf-8',
        'set-cookie': 'ld_session=ok; Path=/; Max-Age=3600',
      })
      response.end('<title>Signed in</title><h1>welcome</h1><a href="/private">private area</a>')
      return
    }
    if (url.pathname === '/private') {
      response.writeHead(signedIn ? 200 : 403, { 'content-type': 'text/html; charset=utf-8' })
      response.end(signedIn
        ? `<title>Private</title><h1>secret</h1><p>The code is ${tokenValue}</p>`
        : '<title>Denied</title><h1>not signed in</h1><p>Please sign in first.</p>')
      return
    }
    response.writeHead(200, { 'content-type': 'text/html; charset=utf-8' })
    response.end(`
<title>Session Site</title>
<h1>Please sign in</h1>
<form method="post" action="/signin">
  <label for="user">User</label><input id="user" name="user" value="" />
  <label for="pass">Password</label><input id="pass" name="pass" type="password" value="" />
  <button type="submit">Sign in</button>
</form>`)
  })
  return server
}

/**
 * A page with a slider and a drop target, for the drag task.
 *
 * The page reports its own state to the server whenever that state changes, and
 * the server keeps the latest report. The oracle reads that report, so the judge
 * is the page itself rather than anything the agent wrote down.
 */
function startDragSite() {
  const state = { slider: null, zone: null }
  const server = createServer((request, response) => {
    if (request.method === 'POST' && request.url === '/report') {
      let body = ''
      request.on('data', (chunk) => { body += chunk })
      request.on('end', () => {
        try {
          const parsed = JSON.parse(body || '{}')
          state.slider = parsed.slider ?? state.slider
          state.zone = parsed.zone ?? state.zone
        } catch { /* a malformed report is simply not recorded */ }
        response.writeHead(204)
        response.end()
      })
      return
    }
    response.writeHead(200, { 'content-type': 'text/html; charset=utf-8' })
    response.end(`
<title>Drag Site</title>
<h1>Slide to 100</h1>
<input id="slider" type="range" min="0" max="100" value="0" />
<div id="shown">0</div>
<h2 id="zone" style="width:240px;height:80px;border:2px dashed #444">drop zone</h2>
<div id="dragger" style="width:80px;height:24px;background:#bbb">drag me</div>
<script>
  var dragging = false;
  var report = function () {
    fetch('/report', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        slider: document.getElementById('slider').value,
        zone: document.getElementById('zone').textContent,
      }),
    }).catch(function () {});
  };
  document.getElementById('slider').addEventListener('input', function () {
    document.getElementById('shown').textContent = this.value;
    report();
  });
  // The zone only says "entered" while a pointer is being held and moved over
  // it, which neither a click nor a scroll produces.
  document.getElementById('dragger').addEventListener('mousedown', function () { dragging = true; });
  document.addEventListener('mouseup', function () { dragging = false; report(); });
  document.getElementById('zone').addEventListener('mousemove', function () {
    if (dragging && this.textContent !== 'entered') { this.textContent = 'entered'; report(); }
  });
</script>`)
  })
  server.ldState = state
  return server
}

/** The port a promise-resolved server is listening on. */
function portOf(server) {
  return server.address().port
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
        // Start from no Notepad at all: a window that is already open can be
        // adopted by the launch instead of a fresh one being created, and the
        // task should not depend on which happened.
        killByName(['notepad'])
        await sleep(700)
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
      id: 'fill_form',
      // Six interactions of five different kinds, and the result is read from the
      // application itself: a form that was only looked at cannot pass, because
      // every default is wrong.
      async setup() {
        const value = token('T9')
        const out = path.join(WORK, 't9.json')
        rmSync(out, { force: true })
        const child = spawn('powershell', ['-NoProfile', '-STA', '-File', path.join(HERE, 'form.ps1'), '-Out', out], {
          stdio: 'ignore',
          windowsHide: false,
        })
        const deadline = Date.now() + 25_000
        while (Date.now() < deadline) {
          if (windowRect('LD Form')) break
          await sleep(400)
        }
        return { value, out, child }
      },
      goal: ({ value }) => [
        'A window titled "LD Form" is open on the desktop. Fill it in exactly like this:',
        `Name must be ${value}.`,
        'City must be Chengdu.',
        'The newsletter check box must be ticked.',
        'Plan B must be selected, not Plan A.',
        'Then press the Submit button.',
        'The form writes what it actually received to a file when you submit, so check every field before submitting.',
      ].join(' '),
      async oracle({ value, out }) {
        if (!existsSync(out)) return { pass: false, detail: 'the form was never submitted' }
        let got = null
        try {
          got = JSON.parse(readFileSync(out, 'utf8'))
        } catch (error) {
          return { pass: false, detail: `unreadable submission: ${error.message}` }
        }
        const wrong = []
        if (got.name !== value) wrong.push(`name=${JSON.stringify(got.name)}`)
        if (got.city !== 'Chengdu') wrong.push(`city=${JSON.stringify(got.city)}`)
        if (got.subscribe !== true) wrong.push(`subscribe=${JSON.stringify(got.subscribe)}`)
        if (got.plan !== 'B') wrong.push(`plan=${JSON.stringify(got.plan)}`)
        return wrong.length
          ? { pass: false, detail: `submitted with ${wrong.join(' ')}` }
          : { pass: true, detail: 'all five controls submitted correctly' }
      },
      async teardown({ child }) {
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
    {
      id: 'menu_path',
      // A genuine Win32 menu three levels deep. The accessibility tree shows the
      // two headings and nothing below them, so this command cannot be reached
      // by clicking an addressable element; it can be reached by walking the
      // menu by path. The target writes the oracle file itself when the exact
      // command runs, so a wrong command cannot produce it.
      async setup() {
        const value = token('M1')
        const out = path.join(WORK, 'm1.txt')
        rmSync(out, { force: true })
        killByName(['ld-menu-target'])
        await sleep(600)
        const child = await startMenuTarget(out, value)
        return { value, out, child }
      },
      goal: ({ out }) => [
        'A window titled "LD Menu Target" is open on the desktop.',
        'It has a menu bar. Under the Report menu there is an Export submenu,',
        'and under that, the command "As text".',
        'Run that exact command. The application then writes a report file.',
        `Do not type into it and do not use a shell: the file must be written by that menu command.`,
        `The file is ${out} .`,
      ].join(' '),
      async oracle({ value, out }) {
        if (!existsSync(out)) return { pass: false, detail: 'the menu command never wrote its report' }
        const got = text(readFileSync(out, 'utf8')).trim()
        return got === `${value}|text`
          ? { pass: true, detail: `menu command wrote ${got}` }
          : { pass: false, detail: `expected ${value}|text got ${JSON.stringify(got.slice(0, 60))}` }
      },
      async teardown({ child }) {
        child?.kill()
        killByName(['ld-menu-target'])
      },
    },
    {
      id: 'terminate_app',
      // The oracle is the process this task started, checked by id: a window
      // that was merely closed leaves the process running, and only terminating
      // the application ends it. Whether the agent arrives by title or by id is
      // its own business.
      async setup() {
        killByName(['notepad'])
        await sleep(700)
        const child = await startNotepad()
        return { child }
      },
      goal: () => [
        'A Notepad window is open on the desktop.',
        'Terminate the Notepad application so that its process is no longer running at all.',
        'Closing the window is not enough: the process itself must be gone.',
      ].join(' '),
      async oracle({ child }) {
        if (!child?.pid) return { pass: false, detail: 'the task never started a Notepad process to end' }
        await sleep(1200)
        return pidIsAlive(child.pid)
          ? { pass: false, detail: `process ${child.pid} is still running` }
          : { pass: true, detail: `process ${child.pid} is gone` }
      },
      async teardown() {
        killByName(['notepad'])
      },
    },
    {
      id: 'browser_session',
      // The token exists on one page only, and that page answers only to a
      // request carrying the session cookie. The browser is asked to be closed
      // and reopened in between, so a browser that does not keep its session
      // cannot produce the token - the page has no other copy of it.
      async setup() {
        const value = token('S1')
        killByName(['chrome', 'msedge'])
        await sleep(800)
        const server = startSessionSite(value)
        await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
        return { value, server, out: path.join(WORK, 's1.txt'), url: `http://127.0.0.1:${portOf(server)}/` }
      },
      goal: ({ url, out }) => [
        `A local site is at ${url} .`,
        'Sign in using the form it shows: user "demo", password "demo".',
        'Then open its private area and read the code shown there.',
        'To be sure the sign-in really persisted, close the browser completely first and open it again,',
        'then go back to that site and open the private area. The sign-in must still hold after the restart.',
        `Write the code alone to ${out} .`,
      ].join(' '),
      async oracle({ value, out }) {
        if (!existsSync(out)) return { pass: false, detail: 'no code was written' }
        const got = text(readFileSync(out, 'utf8')).trim()
        return got === value
          ? { pass: true, detail: `read the private code ${got}` }
          : { pass: false, detail: `expected ${value} got ${JSON.stringify(got.slice(0, 40))}` }
      },
      async teardown({ server }) {
        killByName(['chrome', 'msedge'])
        await sleep(500)
        await new Promise((resolve) => server.close(resolve))
      },
    },
    {
      id: 'browser_drag',
      // Two states that only a pointer drag can produce: a slider whose value
      // follows the movement, and a zone that lights up while something is
      // being dragged across it. Neither is reachable by clicking or scrolling.
      async setup() {
        killByName(['chrome', 'msedge'])
        await sleep(800)
        const server = startDragSite()
        await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
        return { server, out: path.join(WORK, 's2.txt'), url: `http://127.0.0.1:${portOf(server)}/` }
      },
      goal: ({ url, out }) => [
        `A local page is at ${url} .`,
        'Set the slider on it to 100 by dragging it, not by typing or pressing keys.',
        'Then drag the small grey block labelled "drag me" onto the dashed drop zone so the zone reads "entered".',
        `When both are done, write exactly DONE to ${out} .`,
      ].join(' '),
      async oracle({ out, server }) {
        // The page is the judge: it reported what it showed as the state
        // changed, and the server kept the latest report. Neither a click nor a
        // scroll can produce these two values.
        if (!existsSync(out)) return { pass: false, detail: 'the task was not reported finished' }
        const reported = text(readFileSync(out, 'utf8')).trim()
        if (reported !== 'DONE') return { pass: false, detail: `wrote ${JSON.stringify(reported.slice(0, 30))}` }
        const state = server.ldState ?? {}
        const sliderOk = String(state.slider) === '100'
        const zoneOk = String(state.zone) === 'entered'
        return sliderOk && zoneOk
          ? { pass: true, detail: `page reported slider=${state.slider} zone=${state.zone}` }
          : { pass: false, detail: `page reported slider=${state.slider} zone=${JSON.stringify(state.zone)}` }
      },
      async teardown({ server }) {
        killByName(['chrome', 'msedge'])
        await sleep(500)
        await new Promise((resolve) => server.close(resolve))
      },
    },
  ]
}

/**
 * How to start dsh without a shell.
 *
 * Going through the `dsh` shim means going through cmd.exe, where a task text
 * containing a token that starts with `-` is parsed as a flag - which it was,
 * and dsh answered "`-` must be the only task argument" instead of running the
 * task. Spawning the launcher script directly removes that whole class of
 * accident.
 */
function dshLauncher() {
  if (process.env.LD_DSH_BIN) return { cmd: process.execPath, prefix: [process.env.LD_DSH_BIN] }
  const root = text(spawnSync('npm', ['root', '-g'], { encoding: 'utf8', shell: true, timeout: 60_000 }).stdout).trim()
  const bin = root ? path.join(root, '@deepseek-ai', 'dsh', 'lib', 'bin.js') : ''
  if (bin && existsSync(bin)) return { cmd: process.execPath, prefix: [bin] }
  return null
}

/**
 * What the run cost, read from the machine-readable event stream.
 *
 * Wall-clock is a poor comparison: it moves with machine load and with how long
 * a human takes to read the screen. Model calls and tokens for the same task
 * are the operational difference, and they are what a repeated task actually
 * costs.
 */
function metricsFrom(stdout) {
  let steps = 0
  let turns = 0
  let input = 0
  let output = 0
  let total = 0
  let finalText = ''
  const tools = []
  for (const line of stdout.split('\n')) {
    const trimmed = line.trim()
    if (!trimmed.startsWith('{')) continue
    let event
    try {
      event = JSON.parse(trimmed)
    } catch {
      continue
    }
    if (event.type === 'status' && event.phase === 'step_end') {
      steps += 1
      const usage = event.usage || {}
      input += usage.inputTokens || 0
      output += usage.outputTokens || 0
      total += usage.totalTokens || 0
    }
    if (event.type === 'status' && event.phase === 'turn_start') turns += 1
    if (event.type === 'tool') tools.push(String(event.name || event.tool || ''))
    if (event.type === 'final') finalText = text(event.text)
  }
  return { steps, turns, input_tokens: input, output_tokens: output, total_tokens: total, tools, final: finalText }
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
  const launcher = dshLauncher()
  if (!launcher) throw new Error('could not locate the dsh launcher')

  await new Promise((resolve) => {
    const child = spawn(
      launcher.cmd,
      [...launcher.prefix, '--profile', arm.profile, '--patch', arm.patch, '--json', goal],
      { cwd: REPO, windowsHide: true },
    )
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
  const metrics = metricsFrom(stdout)
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
    steps: metrics.steps,
    turns: metrics.turns,
    input_tokens: metrics.input_tokens,
    output_tokens: metrics.output_tokens,
    total_tokens: metrics.total_tokens,
    said: metrics.final.split('\n').filter(Boolean).slice(-1)[0]?.slice(0, 200) ?? '',
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
      console.log(`${result.pass ? 'PASS' : 'FAIL'}  ${String(result.steps).padStart(2)} steps  ${Math.round(result.elapsed_ms / 1000)}s  ${result.detail}`)
    }
  }

  const report = path.join(WORK, `report-${Date.now()}.json`)
  writeFileSync(report, JSON.stringify(results, null, 2), 'utf8')
  // A comparison run must not leave the machine changed for the next one.
  killByName(['notepad', 'CalculatorApp', 'Calculator', 'chrome', 'msedge'])
  console.log('\n=== summary ===')
  for (const arm of arms) {
    const rows = results.filter((row) => row.arm === arm)
    const passed = rows.filter((row) => row.pass).length
    const steps = rows.reduce((sum, row) => sum + row.steps, 0)
    const tokens = rows.reduce((sum, row) => sum + row.total_tokens, 0)
    const seconds = Math.round(rows.reduce((sum, row) => sum + row.elapsed_ms, 0) / 1000)
    console.log(`${arm.padEnd(9)} ${passed}/${rows.length} passed   ${String(steps).padStart(3)} model steps   ${String(tokens).padStart(8)} tokens   ${seconds}s`)
  }
  console.log(`report: ${report}`)
}

await main()
