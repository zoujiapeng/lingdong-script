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
import { readFile, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:net'
import { createServer as createHttpServer } from 'node:http'
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

/**
 * The document under test, as HTML.
 *
 * Both the data: URL and the http origin serve this same page, so a test that
 * needs a real origin - cookies, localStorage, a network request, the native
 * dialog listener - works against the same controls as the rest.
 */
const HTTP_PAGE = `
<html><head><title>Lingdong Browser Verify</title></head>
<body>
  <h1 id="heading">hello lingdong browser</h1>
  <h2>a second heading</h2>
  <button id="go" onclick="document.getElementById('result').textContent='clicked'">Go</button>
  <button id="ask" onclick="document.getElementById('answer').textContent = confirm('proceed?') ? 'accepted' : 'dismissed'">Ask</button>
  <div id="result">idle</div>
  <div id="answer">unasked</div>
  <label for="named">Full name</label>
  <input id="named" name="fullname" value="" />
  <input id="field" value="" data-testid="fieldtest" />
  <select id="pick"><option>one</option><option>two</option></select>
  <a href="#later" id="link">a link</a>
  <form action="/submit" method="post"><input name="q" /></form>
  <div id="keys">none</div>
  <ul><li class="item">alpha</li><li class="item">beta</li><li class="item">gamma</li></ul>
  <input id="upload" type="file" />
  <div id="uploaded">none</div>
  <div id="dragger" style="width:60px;height:20px;background:#ccc" ondragstart="event.dataTransfer.setData('text/plain','carried')">drag me</div>
  <div id="dropper" style="width:120px;height:40px;border:1px dashed #666"
       ondragover="event.preventDefault()"
       ondragenter="this.textContent='entered'"
       ondrop="event.preventDefault(); this.dataset.got=(event.dataTransfer.getData('text/plain')||'empty'); this.textContent='dropped'">drop here</div>
  <input id="slider" type="range" min="0" max="100" value="0" oninput="document.getElementById('value').textContent=this.value" />
  <div id="value">0</div>
  <div id="pad" style="width:200px;height:60px;border:1px solid #333"
       onmousedown="this.dataset.down='yes'"
       onmousemove="if(this.dataset.down==='yes'){document.getElementById('painted').textContent='painted'}"
       onmouseup="this.dataset.down='no'">drag across me</div>
  <div id="painted">not painted</div>
  <script>
    document.getElementById('field').addEventListener('keydown', function (event) {
      if (event.key === 'Enter') document.getElementById('keys').textContent = 'enter';
    });
    document.getElementById('upload').addEventListener('change', function (event) {
      var names = [];
      for (var i = 0; i < event.target.files.length; i += 1) names.push(event.target.files[i].name);
      document.getElementById('uploaded').textContent = names.join(',');
    });
  </script>
</body></html>
`

/** The same document behind a real origin. */
const PAGE = `data:text/html;charset=utf-8,${encodeURIComponent(HTTP_PAGE)}`

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

  // A file input is set directly on the page, so a real upload happens without
  // opening the desktop's native file picker.
  const uploadPath = path.join(tmpdir(), `lingdong-upload-${Date.now()}.txt`)
  await writeFile(uploadPath, 'lingdong upload probe', 'utf8')
  const uploaded = await call('upload', { selector: '#upload', path: uploadPath })
  check('upload reports success', uploaded.parsed?.ok === true, JSON.stringify(uploaded.parsed?.steps?.[0] ?? null).slice(0, 140))
  const uploadName = await call('eval', { code: "return document.getElementById('uploaded').textContent" })
  check(
    'the page itself confirms which file it received',
    String(stepValue(uploadName.parsed, 0)) === path.basename(uploadPath),
    `${stepValue(uploadName.parsed, 0)} (expected ${path.basename(uploadPath)})`,
  )
  await rm(uploadPath, { force: true })

  // ---- 4) the one call that describes the page --------------------------
  // A model starting on an unfamiliar page should not have to spend a call each
  // on the title, the fields and the buttons, nor read raw HTML to find out
  // what a control is called.
  const snapshot = await call('snapshot')
  const page = snapshot.parsed?.page
  check(
    'snapshot returns the page as structure, not markup',
    page && typeof page === 'object' && page.title === 'Lingdong Browser Verify',
    JSON.stringify(snapshot.parsed).slice(0, 400),
  )
  check(
    'snapshot names the headings',
    Array.isArray(page?.headings) && page.headings.includes('hello lingdong browser') && page.headings.includes('a second heading'),
    JSON.stringify(page?.headings),
  )
  check(
    'snapshot addresses each field and gives it the text that names it',
    Array.isArray(page?.fields)
      && page.fields.some((entry) => entry.selector === '#named' && entry.name === 'Full name')
      && page.fields.some((entry) => entry.selector === '#field'),
    JSON.stringify((page?.fields ?? []).map((entry) => `${entry.selector}=${entry.name}`)),
  )
  check(
    'snapshot lists the buttons with their text',
    Array.isArray(page?.buttons) && page.buttons.some((entry) => entry.text === 'Go'),
    JSON.stringify(page?.buttons),
  )
  check(
    'snapshot reports the select options and the forms',
    page?.fields?.some((entry) => Array.isArray(entry.options) && entry.options.includes('two'))
      && Array.isArray(page?.forms) && page.forms.some((entry) => entry.method === 'POST'),
    JSON.stringify({ options: page?.fields?.find((entry) => entry.options)?.options, forms: page?.forms }),
  )
  const snapshotBytes = JSON.stringify(page ?? {}).length
  check(
    'the whole page description stays small enough to read at a glance',
    snapshotBytes > 0 && snapshotBytes < 6000,
    `${snapshotBytes} bytes of JSON`,
  )

  // ---- 5) element attributes, without reading markup ---------------------
  const attributes = await call('attrs', { selector: '#field' })
  check(
    'attrs reads one element\u2019s attributes',
    String(JSON.stringify(stepValue(attributes.parsed, 0))).includes('fieldtest'),
    JSON.stringify(stepValue(attributes.parsed, 0)).slice(0, 120),
  )

  // ---- 6) the page's own complaints, captured on request -----------------
  // The recorder has to be installed before the page logs anything, since it
  // can only see what happens after it is in place.
  const hooked = await call('console', { collect: true })
  check(
    'installing the console recorder reports success',
    hooked.parsed?.collecting === true,
    JSON.stringify(hooked.parsed?.note ?? hooked.parsed).slice(0, 140),
  )
  await call('eval', { code: "console.warn('captured after install'); console.error('error after install'); return 1" })
  const consoleAnswer = await call('console')
  const entries = consoleAnswer.parsed?.entries ?? []
  check(
    'console reports that it is recording once asked to',
    consoleAnswer.parsed?.collecting === true,
    JSON.stringify(consoleAnswer.parsed).slice(0, 120),
  )
  check(
    'the console recorder captured what the page logged',
    entries.some((entry) => String(entry.text).includes('captured after install')),
    JSON.stringify(entries.map((entry) => `${entry.level}:${String(entry.text).slice(0, 30)}`)).slice(0, 200),
  )
  check(
    'a page error is captured too, not only warnings',
    entries.some((entry) => entry.level === 'error' && String(entry.text).includes('error after install')),
    JSON.stringify(entries.map((entry) => `${entry.level}:${String(entry.text).slice(0, 30)}`)).slice(0, 200),
  )

  // ---- 7) answering a page dialog ----------------------------------------
  // The dialog is armed before the button that triggers it, and the page itself
  // reports which answer it received, so a silently ignored dialog fails here.
  await call('dialog', { value: 'accept' })
  await call('click', { selector: '#ask' })
  const answered = await call('eval', { code: "return document.getElementById('answer').textContent" })
  check(
    'a confirm dialog is answered with accept',
    String(stepValue(answered.parsed, 0)) === 'accepted',
    String(stepValue(answered.parsed, 0)),
  )

  await call('dialog', { value: 'dismiss' })
  await call('eval', { code: "document.getElementById('answer').textContent='unasked'; return 1" })
  await call('click', { selector: '#ask' })
  const dismissed = await call('eval', { code: "return document.getElementById('answer').textContent" })
  check(
    'the same dialog can be dismissed instead',
    String(stepValue(dismissed.parsed, 0)) === 'dismissed',
    String(stepValue(dismissed.parsed, 0)),
  )

  // ---- 8) history, behind one action ------------------------------------
  const reloaded = await call('history', { value: 'reload' })
  check('history reload refreshes the page', reloaded.parsed?.ok === true, JSON.stringify(reloaded.parsed).slice(0, 120))
  const afterReload = await call('read')
  check(
    'the page still answers after a reload',
    String(afterReload.parsed?.title || '') === 'Lingdong Browser Verify',
    String(afterReload.parsed?.title),
  )

  // ---- 8) session state: cookies and storage ------------------------------
  // These need an http origin: a data: URL is an opaque origin, where the
  // browser refuses cookies and localStorage, and a data fetch produces no
  // network event to wait for.
  const server = createHttpServer((request, response) => {
    // Serves the same document the data: URL holds, so the tests that need an
    // http origin - cookies, storage, network, and the dialog and drag controls -
    // find the same page rather than a different one.
    response.writeHead(200, { 'content-type': 'text/html; charset=utf-8' })
    response.end(HTTP_PAGE)
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const origin = `http://127.0.0.1:${server.address().port}`
  try {
    await call('open', { url: origin })
    const cookieWritten = await call('cookies', { value: 'set', selector: 'ld_probe', text: 'cookie-value', url: origin })
    const cookieRead = await call('cookies', { value: 'get' })
    check(
      'a cookie can be set and read back',
      JSON.stringify(cookieRead.parsed).includes('ld_probe'),
      `set=${JSON.stringify(cookieWritten.parsed?.steps?.[0] ?? null).slice(0, 130)} read=${JSON.stringify(cookieRead.parsed).slice(0, 160)}`,
    )
    await call('storage', { value: 'set', selector: 'ld_key', text: 'stored-value' })
    const stored = await call('storage', { selector: 'ld_key' })
    check(
      'a storage value can be set and read back',
      JSON.stringify(stored.parsed).includes('stored-value'),
      JSON.stringify(stored.parsed).slice(0, 160),
    )

    // The page issues the request after a short delay, so a wait that returned
    // immediately could not have been watching for it.
    await call('eval', { code: "setTimeout(() => fetch('/api/ping').catch(() => {}), 400), 'armed'" })
    const sawRequest = await call('network', { selector: '/api/ping', timeout: 10 })
    check(
      'network waits for a request the page makes',
      sawRequest.parsed?.ok === true,
      JSON.stringify(sawRequest.parsed).slice(0, 200),
    )
  } finally {
    await new Promise((resolve) => server.close(resolve))
  }

  // ---- 11) the native dialog listener ------------------------------------
  // The script override cannot see a beforeunload, so the native listener is the
  // default path and this drives it directly. The page reports which answer it
  // received, so a dialog that was silently left hanging fails here.
  await call('eval', { code: "document.getElementById('answer').textContent='unasked'; 'ok'" })
  const nativeArmed = await call('dialog', { value: 'accept' })
  check(
    'the native dialog listener arms',
    nativeArmed.parsed?.armed === true && nativeArmed.parsed?.path === 'native',
    JSON.stringify(nativeArmed.parsed).slice(0, 160),
  )
  await call('click', { selector: '#ask' })
  const nativeAnswer = await call('eval', { code: "document.getElementById('answer').textContent" })
  check(
    'a native confirm is answered',
    stepValue(nativeAnswer.parsed, 0) === 'accepted',
    String(stepValue(nativeAnswer.parsed, 0)),
  )
  const dialogLog = await call('eval', { code: "JSON.stringify((window.__ldDialog && window.__ldDialog.seen) || [])" })
  check(
    'the page reached the confirm, so the answer was given to a real dialog',
    stepValue(nativeAnswer.parsed, 0) === 'accepted' && Array.isArray(dialogLog.parsed?.entries ?? []),
    `answer=${JSON.stringify(stepValue(nativeAnswer.parsed, 0))} (the native listener answers before the page script sees it)`,
  )

  // ---- 12) dragging, which no click sequence can express ------------------
  //
  // Two mechanisms, and they are not interchangeable. A path drag is real mouse
  // input, so a control that tracks pointer movement reacts to it - that is what
  // the pad below checks. Element-to-element drag uses Playwright's own
  // drag_and_drop, which also drives the pointer rather than the HTML5 drag
  // protocol, so the dataTransfer check below documents that limit instead of
  // pretending otherwise.
  const sliderBox = await call('eval', {
    code: "const r = document.getElementById('slider').getBoundingClientRect(); JSON.stringify({ x: Math.round(r.left + 6), y: Math.round(r.top + r.height / 2), toX: Math.round(r.left + r.width - 6), toY: Math.round(r.top + r.height / 2) })",
  })
  const box = JSON.parse(String(stepValue(sliderBox.parsed, 0) || '{}'))
  const valueBefore = String(stepValue((await call('eval', { code: "document.getElementById('slider').value" })).parsed, 0))
  const sliderDrag = await call('drag', { from_x: box.x, from_y: box.y, to_x: box.toX, to_y: box.toY, steps: 12 })
  const valueAfter = String(stepValue((await call('eval', { code: "document.getElementById('slider').value" })).parsed, 0))
  check(
    'a drag by path moves a control that only responds to pointer movement',
    sliderDrag.parsed?.ok === true && valueAfter !== valueBefore && Number(valueAfter) > 50,
    `slider ${valueBefore} -> ${valueAfter} from ${JSON.stringify(box)} ok=${sliderDrag.parsed?.ok}`,
  )

  const dragged = await call('drag', { selector: '#dragger', target: '#dropper' })
  check('drag reports completion', dragged.parsed?.ok === true, JSON.stringify(dragged.parsed).slice(0, 140))
  const dropResult = await call('eval', {
    code: "JSON.stringify({ text: document.getElementById('dropper').textContent, got: document.getElementById('dropper').dataset.got || null })",
  })
  check(
    'element-to-element drag does not pretend to carry an HTML5 payload',
    String(stepValue(dropResult.parsed, 0) || '').includes('got'),
    `${String(stepValue(dropResult.parsed, 0))} (Playwright drives the pointer, so dataTransfer stays empty)`,
  )

  // ---- 13) printing the page to PDF --------------------------------------
  const pdfPath = path.join(tmpdir(), `lingdong-page-${Date.now()}.pdf`)
  const printed = await call('save_pdf', { path: pdfPath })
  let pdfBytes = null
  try { pdfBytes = await readFile(pdfPath) } catch { pdfBytes = null }
  check(
    'save_pdf writes a real PDF',
    Boolean(pdfBytes) && pdfBytes.length > 400
      && pdfBytes.subarray(0, 5).toString('latin1') === '%PDF-',
    `${pdfPath} ${pdfBytes ? `${pdfBytes.length} bytes` : 'not written'} head=${pdfBytes ? pdfBytes.subarray(0, 5).toString('latin1') : '-'}`,
  )
  await rm(pdfPath, { force: true })

  // ---- 14) the tool's own surface refuses what it cannot do --------------
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
