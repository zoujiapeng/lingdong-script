#!/usr/bin/env node
/**
 * End-to-end verification for the browser session surface.
 *
 * This drives a real browser, not a stub: the plugin's own `launch` starts a
 * headless Chromium-family browser with a private profile and a private CDP
 * port, the repository's own feature worker attaches to it over CDP, and every
 * assertion below is read back out of the live page.
 *
 * The page under test is a `data:` URL, so nothing is fetched from the network
 * and nothing about the user's own browsing is touched. The browser process and
 * its temporary profile are removed again, whichever way the run ends.
 *
 * Usage:  node packages/dsh-plugin-lingdong/scripts/verify-browser.mjs
 */
import { existsSync, statSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import { createServer } from 'node:net'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { BrowserSession, findBrowser, probeCdp } from '../src/browser.mjs'
import { resolveConfig } from '../src/config.mjs'
import { buildBrowserTool } from '../src/tools.mjs'

const failures = []
function check(label, ok, detail) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}${detail ? ` — ${detail}` : ''}`)
  if (!ok) failures.push(label)
}

/**
 * Ask the OS for a free port.
 *
 * A fixed port would collide with whatever else is on this machine - a browser
 * debug session the user is running, or another service - and the failure would
 * look like a broken plugin rather than a taken port.
 */
function freePort() {
  return new Promise((resolve, reject) => {
    const server = createServer()
    server.on('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address()
      server.close(() => resolve(port))
    })
  })
}

const PORT = Number(process.env.LINGDONG_BROWSER_VERIFY_PORT || await freePort())
const settings = resolveConfig({ port: 8793, browserPort: PORT })
const log = { info() {}, warn() {}, error() {} }
const session = new BrowserSession(settings, log)

const PAGE = `data:text/html;charset=utf-8,${encodeURIComponent(`
<html><head><title>Lingdong Browser Verify</title></head>
<body>
  <h1 id="heading">hello lingdong browser</h1>
  <button id="go" onclick="document.getElementById('result').textContent='clicked'">Go</button>
  <div id="result">idle</div>
  <input id="field" value="" />
  <div id="keys">none</div>
  <ul><li class="item">alpha</li><li class="item">beta</li><li class="item">gamma</li></ul>
  <script>
    document.getElementById('field').addEventListener('keydown', function (event) {
      if (event.key === 'Enter') document.getElementById('keys').textContent = 'enter';
    });
  </script>
</body></html>
`) }`

async function call(action, args = {}) {
  const tool = buildBrowserTool(session)
  const result = await tool.handler({ action, ...args })
  const text = result.content.find((entry) => entry.type === 'text')
  let parsed = null
  try { parsed = text ? JSON.parse(text.text) : null } catch { parsed = null }
  const images = result.content.filter((entry) => entry.type === 'image')
  return { parsed, images, content: result.content }
}

/** Read one feature's value out of a multi-step answer. */
function stepValue(answer, index) {
  return answer?.steps?.[index]?.result ?? null
}

let launchedPid = null
try {
  // ---- 1) nothing is spawned or attached before it is asked for -----------
  const idle = await call('status')
  check('status reports no session before one is asked for', idle.parsed?.attached === false, JSON.stringify(idle.parsed))
  check('status confirms no worker process is running at rest', idle.parsed?.worker_running === false, String(idle.parsed?.worker_running))

  let refused = null
  try { await session.call('browser_get_title', {}) } catch (error) { refused = error }
  check(
    'a browser action without a session is refused instead of opening a window',
    refused !== null && /no browser session/.test(String(refused.message)),
    refused ? String(refused.message) : 'no error',
  )
  check('the refusal left no browser process behind', !session.worker.alive, `worker alive=${session.worker.alive}`)

  let badConnect = null
  try { await session.connect({ port: PORT }) } catch (error) { badConnect = error }
  check(
    'connect refuses a port with no CDP endpoint rather than auto-launching',
    badConnect !== null && /no CDP endpoint/.test(String(badConnect.message)),
    badConnect ? String(badConnect.message).slice(0, 120) : 'no error',
  )

  // ---- 2) launch a browser this session owns ------------------------------
  const executable = findBrowser(settings.browserExecutable)
  if (!executable) {
    check('a Chromium-family browser is available to launch', false, 'none found; set browserExecutable')
  } else {
    const started = await session.launch({ headless: true })
    launchedPid = started.pid ?? null
    check('launch starts a browser and attaches to it', started.ok === true && started.attached !== false, JSON.stringify(started).slice(0, 160))
    check('launch reports the browser it attached to', typeof started.browser === 'string' && started.browser.length > 0, String(started.browser))
    check('the launched browser answers its own CDP endpoint', (await probeCdp(PORT)) !== null, `port ${PORT}`)
    check('the session now reports itself as attached', session.facts().attached === true, JSON.stringify(session.facts()))
  }

  // ---- 3) drive a real page ----------------------------------------------
  const opened = await call('open', { url: PAGE })
  check(
    'open navigates and reports the page it landed on',
    opened.parsed?.ok === true && stepValue(opened.parsed, 0)?.title === 'Lingdong Browser Verify',
    JSON.stringify(stepValue(opened.parsed, 0)),
  )

  const read = await call('read')
  check('read returns the live title', read.parsed?.title === 'Lingdong Browser Verify', String(read.parsed?.title))
  check('read returns the live text', String(read.parsed?.text || '').includes('hello lingdong browser'), String(read.parsed?.text || '').slice(0, 80))
  check('read returns the live URL', String(read.parsed?.url || '').startsWith('data:text/html'), String(read.parsed?.url || '').slice(0, 40))

  const evaluated = await call('eval', { code: "return document.querySelectorAll('.item').length" })
  check('eval runs page JavaScript and returns its value', stepValue(evaluated.parsed, 0) === 3, JSON.stringify(stepValue(evaluated.parsed, 0)))

  const found = await call('find', { selector: '.item' })
  const items = stepValue(found.parsed, 0)
  check(
    'find reports the matching elements',
    Array.isArray(items) && items.length === 3 && JSON.stringify(items).includes('beta'),
    JSON.stringify(items).slice(0, 160),
  )

  const clicked = await call('click', { selector: '#go' })
  check('click reports success', clicked.parsed?.ok === true && stepValue(clicked.parsed, 0)?.success !== false, JSON.stringify(clicked.parsed?.steps?.[0] ?? null).slice(0, 140))
  const afterClick = await call('eval', { code: "return document.getElementById('result').textContent" })
  check('the click changed the page, read back from the page itself', stepValue(afterClick.parsed, 0) === 'clicked', String(stepValue(afterClick.parsed, 0)))

  const typed = await call('type', { selector: '#field', value: 'lingdong text' })
  const afterType = await call('eval', { code: "return document.getElementById('field').value" })
  check('type fills the field, read back from the page itself', stepValue(afterType.parsed, 0) === 'lingdong text', JSON.stringify(stepValue(typed.parsed, 0)).slice(0, 100) + ' -> ' + String(stepValue(afterType.parsed, 0)))

  await call('press', { key: 'Enter' })
  const afterKey = await call('eval', { code: "return document.getElementById('keys').textContent" })
  check('press delivers a real key event to the page', stepValue(afterKey.parsed, 0) === 'enter', String(stepValue(afterKey.parsed, 0)))

  const waited = await call('wait', { condition: 'text_visible', target: 'clicked', timeout: 10 })
  check('wait resolves a condition that already holds', waited.parsed?.ok === true, JSON.stringify(waited.parsed?.steps?.[0] ?? null).slice(0, 120))

  const shot = await call('screenshot', { path: path.join(tmpdir(), `lingdong-browser-verify-${Date.now()}.png`) })
  const shotPath = stepValue(shot.parsed, 0)?.path
  check('screenshot writes a real PNG', Boolean(shotPath) && existsSync(shotPath) && statSync(shotPath).size > 1000, `${shotPath} ${shotPath && existsSync(shotPath) ? statSync(shotPath).size : 0} bytes`)
  if (shotPath && existsSync(shotPath)) {
    const bytes = await readFile(shotPath)
    check('the capture is a PNG by its own signature', bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47, bytes.subarray(0, 4).toString('hex'))
  }
  check(
    'the screenshot comes back as an image, not only a file name',
    shot.images.length === 1 && String(shot.images[0].mimeType).startsWith('image/'),
    `${shot.images.length} image block(s)`,
  )

  const tabs = await call('tabs')
  check('tabs lists the open page', Array.isArray(stepValue(tabs.parsed, 0)) && stepValue(tabs.parsed, 0).length >= 1, JSON.stringify(stepValue(tabs.parsed, 0)).slice(0, 140))

  // ---- 4) the tool's own surface refuses what it cannot do ----------------
  const tool = buildBrowserTool(session)
  let unknownAction = null
  try { await tool.handler({ action: 'fly' }) } catch (error) { unknownAction = error }
  check('an unknown action is refused', unknownAction !== null, unknownAction ? String(unknownAction.message) : 'no error')

  let missingSelector = null
  try { await tool.handler({ action: 'type', value: 'x' }) } catch (error) { missingSelector = error }
  check('type without a selector is refused', missingSelector !== null, missingSelector ? String(missingSelector.message) : 'no error')
} catch (error) {
  check('browser verification run', false, error?.stack ?? String(error))
} finally {
  const profile = session.profileDir
  await session.dispose()
  check('close detaches and stops the browser this session started', session.facts().attached === false && session.launched === null, JSON.stringify(session.facts()))
  check('close removes the private browser profile', !profile || !existsSync(profile), String(profile))
  check('dispose stops the feature worker', session.worker.alive === false, `alive=${session.worker.alive}`)
  check('the CDP endpoint is gone once the session is closed', (await probeCdp(PORT, 1000)) === null, `port ${PORT}`)
  if (launchedPid) {
    // The browser was started with windowsHide and a private profile; a
    // surviving process would still hold the port, which the check above
    // already refutes. This only reports the identity for a human reading logs.
    console.log(`note: the browser this run launched was pid ${launchedPid}`)
  }
}

console.log(`\n${failures.length === 0 ? 'ALL CHECKS PASSED' : `${failures.length} CHECK(S) FAILED: ${failures.join(', ')}`}`)
process.exitCode = failures.length === 0 ? 0 : 1
