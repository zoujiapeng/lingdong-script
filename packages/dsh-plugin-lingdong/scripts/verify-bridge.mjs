#!/usr/bin/env node
/**
 * End-to-end verification for the flow bridge.
 *
 * This is not a shape check. It stands up a real `node:http` server, mounts the
 * real bridge route on it, starts (or attaches to) the repository's own WinPilot
 * runtime through the plugin's own sidecar, and then drives the exact request
 * sequence the browser workbench issues: list flows, read one, save one, ask the
 * runtime to validate one, run one, poll it to completion.
 *
 * The flow it runs is a single `ext.delay`, so nothing on the desktop is touched
 * and no physical input is produced. The temporary document is removed again.
 *
 * Requests go through `node:http` rather than `fetch` because the fence under
 * test reads the `Host` header, and `fetch` refuses to set it.
 *
 * Usage:  node packages/dsh-plugin-lingdong/scripts/verify-bridge.mjs
 */
import { createServer, request as httpRequest } from 'node:http'
import { existsSync, rmSync, readFileSync } from 'node:fs'
import { createContext, runInContext } from 'node:vm'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import * as React from 'react'
import * as jsxRuntime from 'react/jsx-runtime'
import { resolveConfig } from '../src/config.mjs'
import { WinPilotSidecar } from '../src/sidecar.mjs'
import { createClient } from '../src/client.mjs'
import { BRIDGE_PREFIX, loopbackFence, registerBridge } from '../src/bridge.mjs'

const failures = []
function check(label, ok, detail) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}${detail ? ` — ${detail}` : ''}`)
  if (!ok) failures.push(label)
}

const settings = resolveConfig({ port: Number(process.env.LINGDONG_BRIDGE_PORT || 8791) })
const TEMP_FILE = 'dsh-bridge-verify.automation.json'
const TEMP_PATH = path.join(settings.repoRoot, 'python', 'winpilot_home', 'scripts', TEMP_FILE)
const CANVAS_FILE = 'dsh-canvas-verify.automation.json'
const CANVAS_PATH = path.join(settings.repoRoot, 'python', 'winpilot_home', 'scripts', CANVAS_FILE)

/**
 * Evaluate the browser bundle and return the canvas codec it exports.
 *
 * This is the real artifact the GUI loads, not a copy: the point of running it
 * here is that the document this test submits is produced by the same code the
 * canvas runs in the browser.
 * @returns the bundle's exported `flowCodec`.
 */
function loadCanvasCodec() {
  const bundle = fileURLToPath(new URL('../../dsh-client-ui-lingdong/lib/client.js', import.meta.url))
  let captured = null
  const sandbox = {
    window: { __ModuleLoader__: { load(descriptor) { captured = descriptor } } },
    console: { log() {}, warn() {}, error() {} },
  }
  runInContext(readFileSync(bundle, 'utf8'), createContext(sandbox), { filename: bundle })
  if (!captured) throw new Error('the client bundle did not register with the module loader')
  const loaded = captured.factory((name) => {
    if (name === 'react') return React
    if (name === 'react/jsx-runtime') return jsxRuntime
    throw new Error(`unexpected host module: ${name}`)
  })
  if (!loaded.flowCodec) throw new Error('the client bundle exports no canvas codec')
  return loaded.flowCodec
}

/** A document the runtime accepts, whose single step cannot touch the desktop. */
function tempDocument(name) {
  return {
    format: 'automation-ir/1',
    id: 'air-dsh-bridge-verify',
    name,
    created: 1_700_000_000,
    source: {
      kind: 'wps',
      format: 'wps/2',
      source: 'dsh-bridge-verify',
      source_run: '',
      task: 'bridge verification',
    },
    conditions: {},
    evidence: { required: [] },
    steps: [{ tool: 'ext.delay', seconds: 0.05 }],
    variables: {},
    runtime: { kind: 'wps_replay' },
  }
}

const sidecar = new WinPilotSidecar(settings, console)
const client = createClient(settings, sidecar)
const log = { info() {}, warn() {}, error() {} }

/** Mount one captured route on a real server and return a caller for it. */
function serve(route) {
  const server = createServer((req, res) => {
    const pathname = new URL(String(req.url), 'http://localhost').pathname
    if (pathname === route.path || pathname.startsWith(`${route.path}/`)) {
      Promise.resolve(route.handler(req, res)).catch((error) => {
        res.statusCode = 500
        res.end(String(error?.message ?? error))
      })
      return
    }
    res.statusCode = 404
    res.end('not found')
  })
  return {
    server,
    async listen() {
      await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
      return server.address().port
    },
    close: () => new Promise((resolve) => server.close(resolve)),
  }
}

/** One HTTP call with full header control, including `Host`. */
function call(port, method, urlPath, { body, headers = {} } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body === undefined
      ? null
      : Buffer.from(typeof body === 'string' ? body : JSON.stringify(body))
    const req = httpRequest({
      host: '127.0.0.1',
      port,
      method,
      path: urlPath,
      headers: {
        accept: 'application/json',
        ...(payload ? { 'content-type': 'application/json', 'content-length': payload.length } : {}),
        ...headers,
      },
    }, (res) => {
      const chunks = []
      res.on('data', (chunk) => chunks.push(chunk))
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8')
        let parsed = null
        try { parsed = text ? JSON.parse(text) : null } catch { parsed = { raw: text } }
        resolve({ status: res.statusCode, body: parsed, text })
      })
    })
    req.on('error', reject)
    if (payload) req.write(payload)
    req.end()
  })
}

const registrations = []
const connectionRegistrations = []
const fakeCtx = { webServer: { register(route) { registrations.push(route); return () => {} } } }
const dispose = registerBridge(fakeCtx, { client, sidecar, settings, log })

let createdFile = false
let createdCanvas = false
let port = 0
let raw = null
let denyPort = 0
let denyRaw = null

const api = (method, suffix, options) => call(port, method, `${BRIDGE_PREFIX}${suffix}`, options)

async function waitForRun(id, kind, budgetMs = 90_000) {
  const deadline = Date.now() + budgetMs
  let last = null
  while (Date.now() < deadline) {
    last = await api('GET', `/runs/${encodeURIComponent(id)}?kind=${kind}`)
    const status = String(last.body?.status ?? '')
    if (['done', 'failed', 'stopped', 'canceled', 'cancelled'].includes(status)) return last
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  return { ...(last ?? {}), timed_out: true }
}

try {
  check('bridge registered exactly one route', registrations.length === 1, `${registrations.length}`)
  check(
    'bridge route is a prefix at the documented path',
    registrations[0]?.kind === 'prefix' && registrations[0]?.path === BRIDGE_PREFIX,
    `${registrations[0]?.kind} ${registrations[0]?.path}`,
  )

  raw = serve(registrations[0])
  port = await raw.listen()
  await sidecar.ensure()
  check('runtime reachable for the bridge', true, sidecar.baseUrl)

  // ---- 1) the fence is DSH's own, and its fallback is never open ----------
  const status = await api('GET', '/status')
  check(
    'GET /status is admitted from loopback',
    status.status === 200 && status.body?.ok === true,
    JSON.stringify(status.body ?? {}).slice(0, 120),
  )
  check(
    'GET /status reports the runtime it proxies',
    status.body?.base_url === sidecar.baseUrl,
    String(status.body?.base_url),
  )

  const foreignHost = await api('GET', '/status', { headers: { host: 'evil.example.com' } })
  check('a foreign Host is refused', foreignHost.status === 403, `status=${foreignHost.status}`)
  const foreignOrigin = await api('GET', '/status', { headers: { origin: 'http://evil.example.com' } })
  check('a foreign Origin is refused', foreignOrigin.status === 403, `status=${foreignOrigin.status}`)
  const crossSite = await api('GET', '/status', { headers: { 'sec-fetch-site': 'cross-site' } })
  check('cross-site fetch metadata is refused', crossSite.status === 403, `status=${crossSite.status}`)
  const sameOrigin = await api('GET', '/status', { headers: { origin: `http://127.0.0.1:${port}` } })
  check('the page\'s own Origin is admitted', sameOrigin.status === 200, `status=${sameOrigin.status}`)

  const fence = loopbackFence()
  check('fallback fence admits 127.0.0.1', fence({ headers: { host: '127.0.0.1:3080' } }) === undefined)
  check('fallback fence admits localhost', fence({ headers: { host: 'localhost:3080' } }) === undefined)
  check('fallback fence refuses a LAN Host', fence({ headers: { host: '192.168.1.20:3080' } }) === 403)
  check('fallback fence refuses a missing Host', fence({ headers: {} }) === 403)

  // A composition that does publish the connection service must delegate to it,
  // so the bridge inherits DSH's own admission policy instead of a copy.
  const connectionCtx = {
    connection: { requestRejection(req) { return req.headers['x-verify-deny'] ? 401 : undefined } },
    webServer: { register(route) { connectionRegistrations.push(route); return () => {} } },
  }
  registerBridge(connectionCtx, { client, sidecar, settings, log })
  denyRaw = serve(connectionRegistrations[0])
  denyPort = await denyRaw.listen()
  const delegated = await call(denyPort, 'GET', `${BRIDGE_PREFIX}/status`)
  check('the connection service decides admission when present', delegated.status === 200, `status=${delegated.status}`)
  const delegatedDeny = await call(denyPort, 'GET', `${BRIDGE_PREFIX}/status`, { headers: { 'x-verify-deny': '1' } })
  check(
    'a refusal from the connection service is honoured verbatim',
    delegatedDeny.status === 401,
    `status=${delegatedDeny.status} ${JSON.stringify(delegatedDeny.body ?? {}).slice(0, 80)}`,
  )

  // ---- 2) validation is the runtime's, not the bridge's -------------------
  const validDoc = await api('POST', '/validate', { body: tempDocument('bridge verify') })
  check(
    'POST /validate accepts a valid document',
    validDoc.status === 200 && validDoc.body?.valid === true,
    JSON.stringify(validDoc.body ?? {}).slice(0, 160),
  )

  const brokenDoc = tempDocument('broken')
  brokenDoc.steps = [{ tool: 'no.such.tool' }]
  const invalidDoc = await api('POST', '/validate', { body: brokenDoc })
  check(
    'POST /validate rejects an unknown step tool with the runtime\'s own message',
    invalidDoc.status === 200 && invalidDoc.body?.valid === false
      && String(invalidDoc.body.errors?.[0] ?? '').includes('no.such.tool'),
    JSON.stringify(invalidDoc.body?.errors ?? []).slice(0, 160),
  )

  const notJson = await api('POST', '/validate', { body: '{not json' })
  check('a malformed request body is a 400, not a crash', notJson.status === 400, `status=${notJson.status}`)

  // ---- 3) file names never escape the library -----------------------------
  const traversal = await api('GET', '/flows/%2e%2e%2f%2e%2e%2fsecrets.automation.json')
  check(
    'path traversal in a flow name is refused',
    traversal.status === 400,
    `status=${traversal.status} ${JSON.stringify(traversal.body ?? {}).slice(0, 90)}`,
  )
  const badSuffix = await api('PUT', '/flows/notes.txt', { body: tempDocument('x') })
  check('a non-flow suffix is refused', badSuffix.status === 400, `status=${badSuffix.status}`)

  // ---- 4) write, read back, and list -------------------------------------
  const document = tempDocument('DSH bridge verify')
  const written = await api('PUT', `/flows/${TEMP_FILE}`, { body: document })
  createdFile = written.status === 200
  check('PUT a flow document', written.status === 200 && written.body?.file === TEMP_FILE, JSON.stringify(written.body ?? {}).slice(0, 120))

  const read = await api('GET', `/flows/${TEMP_FILE}`)
  check(
    'GET returns the document the runtime stored, field for field',
    read.status === 200 && JSON.stringify(read.body) === JSON.stringify(document),
    JSON.stringify(read.body ?? {}).slice(0, 160),
  )

  const listed = await api('GET', '/flows')
  const entry = (listed.body?.flows ?? []).find((item) => item.file === TEMP_FILE)
  check('GET /flows lists the saved document', listed.status === 200 && Boolean(entry), `count=${listed.body?.flows?.length ?? '?'}`)
  check('the listing reports validity and step count', entry?.valid === true && entry?.steps === 1, JSON.stringify(entry ?? {}).slice(0, 160))

  const rejectedWrite = await api('PUT', `/flows/${TEMP_FILE}`, { body: brokenDoc })
  check(
    'PUT refuses a document the runtime cannot execute',
    rejectedWrite.status === 400,
    `status=${rejectedWrite.status} ${JSON.stringify(rejectedWrite.body?.error ?? '').slice(0, 90)}`,
  )
  const stillIntact = await api('GET', `/flows/${TEMP_FILE}`)
  check('a refused write leaves the stored document untouched', stillIntact.body?.name === document.name, String(stillIntact.body?.name))

  // ---- 5) run it and poll it to completion -------------------------------
  const started = await api('POST', `/flows/${TEMP_FILE}/run`, { body: {} })
  check(
    'POST /flows/<file>/run submits a replay job',
    started.status === 200 && started.body?.kind === 'replay' && String(started.body?.id ?? '').startsWith('replay-'),
    JSON.stringify(started.body ?? {}).slice(0, 160),
  )

  const finished = await waitForRun(started.body?.id, 'replay')
  const job = finished.body?.job ?? {}
  check('the run reaches a terminal state', job.status === 'done', `status=${job.status} timed_out=${finished.timed_out === true}`)
  check('the run reports success', job.ok === true, JSON.stringify(job.error ?? ''))
  check(
    'per-step results come back for the canvas',
    Array.isArray(job.result?.results) && job.result.results.length === 1
      && job.result.results[0].tool === 'ext.delay' && job.result.results[0].ok === true,
    JSON.stringify(job.result?.results ?? null),
  )

  const inline = await api('POST', '/run', { body: { automation: document } })
  check('POST /run accepts an unsaved canvas document', inline.status === 200 && inline.body?.kind === 'replay', JSON.stringify(inline.body ?? {}).slice(0, 120))
  const inlineDone = await waitForRun(inline.body?.id, 'replay')
  check(
    'the unsaved document runs too',
    inlineDone.body?.job?.status === 'done' && inlineDone.body?.job?.ok === true,
    `status=${inlineDone.body?.job?.status}`,
  )

  // ---- 6) routing itself ---------------------------------------------------
  const unknown = await api('GET', '/nope')
  check('an unknown bridge endpoint is a 404', unknown.status === 404, `status=${unknown.status}`)
  const wrongMethod = await api('POST', '/status')
  check('a wrong method on a known endpoint is a 405', wrongMethod.status === 405, `status=${wrongMethod.status}`)
  const missingRun = await api('GET', '/runs/replay-does-not-exist')
  check('an unknown run id keeps the runtime\'s 404', missingRun.status === 404, `status=${missingRun.status}`)
  const missingFlow = await api('GET', '/flows/no-such-flow.automation.json')
  check('an unknown flow keeps the runtime\'s 404', missingFlow.status === 404, `status=${missingFlow.status}`)

  // ---- 7) the canvas codec's own output is executable by the runtime -------
  // The document below is produced by the browser bundle this plugin ships, not
  // by a fixture, so this closes the loop the workbench actually walks:
  // canvas -> codec -> bridge -> runtime validation -> runtime execution.
  const codec = loadCanvasCodec()
  const marker = path.join(settings.repoRoot, 'package.json')
  const canvasNodes = codec.layoutNodes([
    { kind: 'wait', params: { seconds: 0.05 } },
    { kind: 'condition', params: { key: 'file_exists', value: marker, timeout_s: 6 } },
    { kind: 'wait', params: { seconds: 0.05 } },
  ])
  const canvasDocument = codec.toDocument({ name: 'DSH canvas verify', nodes: canvasNodes })
  check(
    'the canvas compiles a condition into one nested ctrl.if',
    canvasDocument.steps.length === 2
      && canvasDocument.steps[1].tool === 'ctrl.if'
      && Array.isArray(canvasDocument.steps[1].then)
      && canvasDocument.steps[1].then.length === 1,
    JSON.stringify(canvasDocument.steps),
  )

  const canvasValidation = await api('POST', '/validate', { body: canvasDocument })
  check(
    'the runtime accepts the canvas document',
    canvasValidation.status === 200 && canvasValidation.body?.valid === true,
    JSON.stringify(canvasValidation.body?.errors ?? []),
  )

  const canvasWritten = await api('PUT', `/flows/${CANVAS_FILE}`, { body: canvasDocument })
  createdCanvas = canvasWritten.status === 200
  check('the canvas document saves into the flow library', canvasWritten.status === 200, JSON.stringify(canvasWritten.body ?? {}).slice(0, 120))

  const canvasStarted = await api('POST', `/flows/${CANVAS_FILE}/run`, { body: {} })
  const canvasJob = (await waitForRun(canvasStarted.body?.id, 'replay')).body?.job ?? {}
  check('the canvas flow runs to completion', canvasJob.status === 'done' && canvasJob.ok === true, `status=${canvasJob.status} error=${JSON.stringify(canvasJob.error ?? '')}`)

  const canvasRecords = canvasJob.result?.results ?? []
  const byIndex = {}
  for (const record of canvasRecords) byIndex[String(record.index)] = record
  check(
    'the replayer reports the nested index path the canvas predicted',
    Boolean(byIndex['1']) && Boolean(byIndex['2.1']),
    canvasRecords.map((record) => record.index).join(','),
  )
  check(
    'the condition evaluated true and its guarded step ran',
    byIndex['2.1']?.tool === 'ext.delay' && byIndex['2.1']?.ok === true,
    JSON.stringify(byIndex['2.1'] ?? null),
  )
  // The compile map is what the canvas colours nodes with; it must line up with
  // the indices the runtime really emitted, or per-node status would be a guess.
  const compiled = codec.compileNodes(canvasNodes)
  const mapped = canvasRecords.map((record) => compiled.owners[String(record.index)]?.nodeId ?? null)
  check(
    'every runtime step index maps back onto the node that produced it',
    mapped.length === 2 && mapped.every((id) => typeof id === 'string' && id.length > 0) && mapped[0] !== mapped[1],
    JSON.stringify(mapped),
  )

  // ---- 8) the opt-in fragment helper's plumbing ---------------------------
  // The model call itself belongs to the user, so this exercises everything up
  // to it: the route, the request the canvas builds, and the runtime's own
  // validation of that request. A catalog the runtime rejects fails the job
  // before any provider work, which is what makes the check cost nothing.
  const noGoal = await api('POST', '/fragments', { body: {} })
  check('a fragment request without a sentence is refused', noGoal.status === 400, `status=${noGoal.status}`)

  const fragmentWrongMethod = await api('PUT', '/fragments', { body: {} })
  check('the fragment route is POST only', fragmentWrongMethod.status === 405, `status=${fragmentWrongMethod.status}`)

  const unknownJob = await api('GET', '/fragments/flow-fragment-does-not-exist')
  check('an unknown fragment job keeps the runtime\'s 404', unknownJob.status === 404, `status=${unknownJob.status}`)

  const canvasContext = codec.fragmentContext(codec.layoutNodes([
    { kind: 'wait', params: { seconds: 1 } },
  ]))
  // Corrupt one catalog entry so the runtime's own validator, not the model,
  // is what answers; everything else is the request the canvas really builds.
  canvasContext.catalog[0] = Object.assign({}, canvasContext.catalog[0], { output_ports: [] })
  const badCatalog = await api('POST', '/fragments', {
    body: { goal: 'bridge verification', document: canvasContext.document, catalog: canvasContext.catalog },
  })
  check('the fragment route accepts a request shaped like the canvas builds it', badCatalog.status === 200 && Boolean(badCatalog.body?.job_id), JSON.stringify(badCatalog.body ?? {}).slice(0, 120))

  let fragmentJob = null
  if (badCatalog.body?.job_id) {
    const deadline = Date.now() + 60_000
    while (Date.now() < deadline) {
      const answer = await api('GET', `/fragments/${encodeURIComponent(badCatalog.body.job_id)}`)
      const status = String(answer.body?.status ?? '')
      if (['done', 'failed', 'canceled'].includes(status)) {
        fragmentJob = answer.body
        break
      }
      await new Promise((resolve) => setTimeout(resolve, 200))
    }
  }
  check(
    'a catalog the runtime rejects fails before any provider call',
    fragmentJob?.status === 'failed' && String(fragmentJob.error).includes('output ports'),
    `status=${fragmentJob?.status} error=${JSON.stringify(fragmentJob?.error ?? '')}`,
  )
  check(
    'the canvas document summary passed the runtime\'s exact-field-set check',
    fragmentJob !== null
      && String(fragmentJob.error).includes('catalog')
      && !String(fragmentJob.error).includes('document summary'),
    'the error names the catalog, so the document summary was accepted first',
  )

  dispose?.()
} catch (error) {
  check('bridge verification run', false, error?.stack ?? String(error))
} finally {
  if (raw) await raw.close()
  if (denyRaw) await denyRaw.close()
  await sidecar.stop()
  // Only the files this run created, and only after they were actually written.
  if (createdFile && existsSync(TEMP_PATH)) {
    try { rmSync(TEMP_PATH) } catch (error) { console.log(`note: could not remove ${TEMP_FILE}: ${error.message}`) }
  }
  if (createdCanvas && existsSync(CANVAS_PATH)) {
    try { rmSync(CANVAS_PATH) } catch (error) { console.log(`note: could not remove ${CANVAS_FILE}: ${error.message}`) }
  }
}

console.log(`\n${failures.length === 0 ? 'ALL CHECKS PASSED' : `${failures.length} CHECK(S) FAILED: ${failures.join(', ')}`}`)
process.exitCode = failures.length === 0 ? 0 : 1
