/**
 * Same-origin HTTP bridge between the DSH Web GUI's flow workbench and the
 * bundled WinPilot runtime.
 *
 * Why this exists: the workbench is a browser client plugin, so it cannot hold
 * the runtime's control token and cannot fetch the runtime's loopback origin
 * cross-origin. This module registers one prefix route on DSH's own web server
 * and forwards the flow operations there, under DSH's own request fence.
 *
 * What it is NOT: it is not a second desktop surface. Every mutation still goes
 * to the runtime's canonical endpoints (`/api/script/*`,
 * `/api/v2/automation-ir/runs`), which own validation, the ARP pipeline and the
 * physical single writer. This file only moves JSON and never re-implements a
 * desktop action.
 * @module dsh-plugin-lingdong/bridge
 */

/** Path prefix this plugin owns on the DSH web server. */
export const BRIDGE_PREFIX = '/lingdong/api'

/** Bodies are flow documents; anything past this is not one. */
const MAX_BODY_BYTES = 4 * 1024 * 1024

/** Flow file suffixes the runtime's script library accepts. */
const FLOW_SUFFIXES = ['.automation.json', '.wps.json']

/** Loopback hostnames, used only when DSH's connection service is unavailable. */
const LOOPBACK = new Set(['127.0.0.1', 'localhost', '[::1]', '::1'])

/**
 * Read the request fence DSH itself uses for its own browser routes.
 *
 * `@deepseek-ai/dsh-client-connection` publishes `ctx.connection`, whose
 * `requestRejection(request)` composes the configured Host/Origin fence with
 * browser authentication and returns an HTTP status to refuse with. Reusing it
 * (rather than inventing a policy) means the bridge can never be more
 * permissive than the rest of the GUI's `/api` surface — including when the
 * operator deliberately binds the server to `0.0.0.0` so a phone can reach the
 * GUI, in which case the operator's trusted-host list is what admits a request.
 * @param ctx - host context that may carry the connection service.
 * @returns a predicate returning a refusal status, or undefined to admit.
 */
export function connectionFence(ctx) {
  const connection = ctx?.connection ?? Reflect.get(ctx ?? {}, 'connection')
  if (typeof connection?.requestRejection === 'function') {
    return (req) => connection.requestRejection(req)
  }
  return null
}

/**
 * The fallback fence, for a composition without a connection service.
 *
 * It is deliberately not a weaker policy: loopback Host only, same-Origin only,
 * and no cross-site fetch metadata. It admits strictly fewer requests than the
 * connection service does, so a missing dependency can never open the bridge.
 * @returns a predicate returning a refusal status, or undefined to admit.
 */
export function loopbackFence() {
  return (req) => {
    const headers = req.headers ?? {}
    const host = headers.host
    if (typeof host !== 'string' || !host) return 403
    let hostname
    try {
      hostname = new URL(`http://${host}`).hostname
    } catch {
      return 403
    }
    if (!LOOPBACK.has(hostname)) return 403
    if (String(headers['sec-fetch-site'] ?? '').toLowerCase() === 'cross-site') return 403
    const origin = headers.origin
    if (origin === undefined) return undefined
    try {
      return new URL(String(origin)).host === host ? undefined : 403
    } catch {
      return 403
    }
  }
}

/** A flow file name must be a plain leaf with a suffix the library accepts. */
export function normalizeFlowFile(raw) {
  const value = String(raw ?? '').trim()
  if (!value) throw new BridgeError(400, 'file is required')
  if (value.includes('/') || value.includes('\\') || value.includes('..')) {
    throw new BridgeError(400, 'file must be a plain name inside the script library')
  }
  if (!FLOW_SUFFIXES.some((suffix) => value.endsWith(suffix))) {
    throw new BridgeError(400, `file must end in ${FLOW_SUFFIXES.join(' or ')}`)
  }
  return value
}

/** Error carrying the HTTP status the bridge should answer with. */
export class BridgeError extends Error {
  constructor(status, message, body) {
    super(message)
    this.name = 'BridgeError'
    this.status = status
    this.body = body
  }
}

/** JSON response; availability and run state are live facts, never cached. */
function sendJson(res, status, payload) {
  const text = JSON.stringify(payload ?? null)
  res.statusCode = status
  res.setHeader('content-type', 'application/json; charset=utf-8')
  res.setHeader('cache-control', 'no-store')
  res.setHeader('content-length', Buffer.byteLength(text))
  res.end(text)
}

/** Read a bounded JSON request body. */
async function readJsonBody(req) {
  const chunks = []
  let size = 0
  for await (const chunk of req) {
    size += chunk.length
    if (size > MAX_BODY_BYTES) throw new BridgeError(413, 'request body too large')
    chunks.push(chunk)
  }
  const text = Buffer.concat(chunks).toString('utf8').trim()
  if (!text) return {}
  try {
    return JSON.parse(text)
  } catch (error) {
    throw new BridgeError(400, `request body is not JSON: ${error.message}`)
  }
}

/**
 * Build the prefix-route handler.
 *
 * @param options - runtime client plus the sidecar that owns its lifecycle.
 * @returns an async `(req, res)` handler answering every `/lingdong/api` path.
 */
export function createBridgeHandler({ client, sidecar, settings, log = console, fence = null }) {
  const refuse = fence

  /** Forward one runtime call, keeping the runtime's own status and message. */
  async function runtime(path, { method = 'GET', body, timeoutMs } = {}) {
    try {
      return await client.request(method, path, body, { timeoutMs })
    } catch (error) {
      const status = Number(error?.status) || 502
      const detail = typeof error?.message === 'string' && error.message ? error.message : 'runtime request failed'
      throw new BridgeError(status, detail, error?.body ?? null)
    }
  }

  async function listFlows() {
    const listed = await runtime('/api/scripts', { timeoutMs: 60_000 })
    const entries = Array.isArray(listed) ? listed : []
    return {
      ok: true,
      base_url: sidecar.baseUrl,
      flows: entries.map((entry) => ({
        file: entry.file,
        name: entry.name,
        task: entry.task ?? '',
        steps: entry.steps ?? 0,
        format: entry.format ?? '',
        valid: entry.valid !== false,
        validation_error: entry.validation_error ?? '',
        trust: entry.trust ?? '',
      })),
    }
  }

  /** Load one document, normalizing a legacy WPS script to Automation IR. */
  async function readFlow(file) {
    const name = normalizeFlowFile(file)
    return runtime(`/api/script/${encodeURIComponent(name)}`, { timeoutMs: 60_000 })
  }

  async function writeFlow(file, document) {
    const name = normalizeFlowFile(file)
    if (!document || typeof document !== 'object' || Array.isArray(document)) {
      throw new BridgeError(400, 'body must be one flow document object')
    }
    return runtime(`/api/script/${encodeURIComponent(name)}`, {
      method: 'PUT',
      body: { script: document },
      timeoutMs: 60_000,
    })
  }

  async function validateFlow(document) {
    if (!document || typeof document !== 'object' || Array.isArray(document)) {
      throw new BridgeError(400, 'body must be one flow document object')
    }
    return runtime('/api/v2/automation-ir/validate', {
      method: 'POST',
      body: document,
      timeoutMs: 60_000,
    })
  }

  /**
   * Submit one document and report how to poll it.
   *
   * The runtime answers in two shapes depending on the document's runtime kind:
   * a `wps_replay` document becomes a replay job, an `arp_graph` document
   * becomes an ARP run. Both are ordinary runtime work; the bridge adds no
   * queue, no lease and no scheduling of its own.
   */
  async function submitRun(document, inputs) {
    if (!document || typeof document !== 'object' || Array.isArray(document)) {
      throw new BridgeError(400, 'body must carry one automation document')
    }
    const submitted = await runtime('/api/v2/automation-ir/runs', {
      method: 'POST',
      body: { automation: document, inputs: inputs && typeof inputs === 'object' ? inputs : {} },
      timeoutMs: 60_000,
    })
    if (submitted?.replay_id) {
      return { ok: true, runtime_kind: submitted.runtime_kind ?? 'wps_replay', kind: 'replay', id: submitted.replay_id }
    }
    const runId = submitted?.run_id ?? submitted?.run?.run_id
    if (!runId) throw new BridgeError(502, 'runtime accepted the flow but reported no run identity', submitted)
    return { ok: true, runtime_kind: submitted.runtime_kind ?? 'arp_graph', kind: 'arp', id: runId }
  }

  async function runFlowFile(file, inputs) {
    const document = await readFlow(file)
    const started = await submitRun(document, inputs)
    return { ...started, file: normalizeFlowFile(file), name: document?.name ?? '' }
  }

  async function runStatus(kind, id) {
    if (kind === 'arp') {
      const detail = await runtime(`/api/v2/runs/${encodeURIComponent(id)}`, { timeoutMs: 60_000 })
      const run = detail?.run ?? detail
      return { ok: true, kind, id, status: String(run?.status ?? ''), run }
    }
    const job = await runtime(`/api/replay/${encodeURIComponent(id)}`, { timeoutMs: 60_000 })
    return { ok: true, kind: 'replay', id, status: String(job?.status ?? ''), job }
  }

  async function cancelRun(kind, id) {
    if (kind === 'arp') {
      return { ok: true, kind, id, ...(await runtime(`/api/v2/arp/runs/${encodeURIComponent(id)}/cancel`, { method: 'POST', body: {}, timeoutMs: 60_000 })) }
    }
    return { ok: true, kind: 'replay', id, ...(await runtime(`/api/replay/${encodeURIComponent(id)}/cancel`, { method: 'POST', body: {}, timeoutMs: 60_000 })) }
  }

  /**
   * Ask the runtime's own editor helper for one inert flow fragment.
   *
   * The request body is the runtime's own contract - a sentence, a summary of
   * the current document, and the node catalog the model may choose from - and
   * the runtime answers with a job identity. Nothing here decides what is
   * proposed: the bridge never builds a prompt and never touches the model, so
   * this stays one opt-in editor call, exactly as the standalone Studio has it.
   */
  async function proposeFragment(body) {
    const goal = String(body?.goal ?? '').trim()
    if (!goal) throw new BridgeError(400, 'goal is required')
    const started = await runtime('/api/v2/flow-fragments/proposals', {
      method: 'POST',
      body: { goal, document: body.document, catalog: body.catalog },
      timeoutMs: 60_000,
    })
    if (!started?.proposal_job_id) {
      throw new BridgeError(502, 'runtime accepted the request but returned no proposal job', started)
    }
    return { ok: true, job_id: started.proposal_job_id }
  }

  async function fragmentStatus(jobId) {
    const answer = await runtime(`/api/v2/jobs/${encodeURIComponent(jobId)}`, { timeoutMs: 60_000 })
    const job = answer?.job ?? answer
    return {
      ok: true,
      job_id: jobId,
      status: String(job?.status ?? ''),
      error: String(job?.error ?? ''),
      proposal: job?.result ?? null,
    }
  }

  async function fragmentCancel(jobId) {
    return {
      ok: true,
      job_id: jobId,
      ...(await runtime(`/api/v2/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST', body: {}, timeoutMs: 60_000 })),
    }
  }

  async function status() {
    const ping = await runtime('/api/ping', { timeoutMs: 15_000 })
    return { ok: true, base_url: sidecar.baseUrl, attached: sidecar.external, ping }
  }

  /**
   * Route one already-fenced request.
   *
   * Matching is exact and ordered: the flow routes are checked before the
   * catch-all so a file literally named `status` cannot shadow an operation.
   */
  async function dispatch(method, segments, req, url) {
    const [head, ...rest] = segments
    if (head === 'status' && rest.length === 0) {
      if (method !== 'GET') throw new BridgeError(405, 'status is GET')
      return status()
    }
    if (head === 'validate' && rest.length === 0) {
      if (method !== 'POST') throw new BridgeError(405, 'validate is POST')
      return validateFlow(await readJsonBody(req))
    }
    if (head === 'run' && rest.length === 0) {
      if (method !== 'POST') throw new BridgeError(405, 'run is POST')
      const body = await readJsonBody(req)
      return submitRun(body.automation ?? body.document, body.inputs)
    }
    if (head === 'runs' && rest.length >= 1) {
      const id = rest[0]
      const kind = url.searchParams.get('kind') === 'arp' ? 'arp' : 'replay'
      if (rest.length === 1 && method === 'GET') return runStatus(kind, id)
      if (rest.length === 2 && rest[1] === 'cancel' && method === 'POST') return cancelRun(kind, id)
      throw new BridgeError(404, 'unknown run operation')
    }
    if (head === 'fragments') {
      if (rest.length === 0) {
        if (method !== 'POST') throw new BridgeError(405, 'fragment proposal is POST')
        return proposeFragment(await readJsonBody(req))
      }
      const jobId = rest[0]
      if (rest.length === 1 && method === 'GET') return fragmentStatus(jobId)
      if (rest.length === 2 && rest[1] === 'cancel' && method === 'POST') return fragmentCancel(jobId)
      throw new BridgeError(404, 'unknown fragment operation')
    }
    if (head === 'flows') {
      if (rest.length === 0) {
        if (method !== 'GET') throw new BridgeError(405, 'flow listing is GET')
        return listFlows()
      }
      const file = rest[0]
      if (rest.length === 1) {
        if (method === 'GET') return readFlow(file)
        if (method === 'PUT') return writeFlow(file, await readJsonBody(req))
        throw new BridgeError(405, 'flow is GET or PUT')
      }
      if (rest.length === 2 && rest[1] === 'run' && method === 'POST') {
        const body = await readJsonBody(req)
        return runFlowFile(file, body.inputs)
      }
      throw new BridgeError(404, 'unknown flow operation')
    }
    throw new BridgeError(404, 'unknown bridge endpoint')
  }

  return async function handle(req, res) {
    try {
      const refusal = refuse ? refuse(req) : undefined
      if (refusal !== undefined) {
        sendJson(res, Number(refusal) || 403, { ok: false, error: 'request refused by the host request fence' })
        return
      }
      const url = new URL(String(req.url), 'http://localhost')
      const suffix = url.pathname.slice(BRIDGE_PREFIX.length)
      // One decode pass, here. Everything downstream sees plain segments, and
      // the runtime call re-encodes exactly once when it builds its own path.
      const segments = suffix.split('/').filter(Boolean).map((part) => decodeURIComponent(part))
      const result = await dispatch(String(req.method ?? 'GET').toUpperCase(), segments, req, url)
      sendJson(res, 200, result)
    } catch (error) {
      const status = Number(error?.status) || 500
      if (status >= 500) log.warn?.(`[lingdong] bridge ${req.method} ${req.url} failed: ${error?.message ?? error}`)
      sendJson(res, status, { ok: false, error: String(error?.message ?? error), detail: error?.body ?? null })
    }
  }
}

/**
 * Register the bridge on the DSH web server.
 *
 * Absent `webServer` (a headless composition) is not a failure: the tool
 * surface is unaffected and the workbench simply has nothing to talk to.
 * @param ctx - host context carrying `webServer`.
 * @param options - runtime client and sidecar.
 * @returns the disposer, or undefined when no web server is present.
 */
export function registerBridge(ctx, options) {
  if (!ctx?.webServer?.register) {
    options.log?.warn?.('[lingdong] no webServer in this composition; the flow bridge is not mounted')
    return undefined
  }
  const fence = connectionFence(ctx) ?? loopbackFence()
  const handler = createBridgeHandler({ ...options, fence })
  return ctx.webServer.register({ kind: 'prefix', path: BRIDGE_PREFIX, handler })
}
