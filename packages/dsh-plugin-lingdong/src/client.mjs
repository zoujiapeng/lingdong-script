/**
 * REST client for the bundled WinPilot runtime.
 * @module dsh-plugin-lingdong/client
 */
/** Terminal ARP/agent run states. */
const TERMINAL = new Set(['done', 'failed', 'canceled', 'cancelled', 'reply'])
/** Tokens on the WinPilot wire that must never be printed back to the model. */
const REDACTED_HEADERS = new Set(['x-winpilot-token', 'authorization'])

/** Error carrying the HTTP status and the runtime's own error body. */
export class WinPilotError extends Error {
  constructor(message, status, body) {
    super(message)
    this.name = 'WinPilotError'
    this.status = status
    this.body = body
  }
}

/**
 * Create the request surface used by the plugin's tools.
 * @param settings - resolved plugin settings.
 * @param sidecar - runtime lifecycle owner.
 * @returns request helpers plus run polling and artifact download.
 */
export function createClient(settings, sidecar) {
  async function request(method, path, body, { timeoutMs, raw = false } = {}) {
    await sidecar.ensure()
    const response = await fetch(`${sidecar.baseUrl}${path}`, {
      method,
      headers: {
        accept: 'application/json',
        'X-WinPilot-Token': settings.token,
        ...(body === undefined ? {} : { 'content-type': 'application/json' }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(timeoutMs ?? settings.requestTimeoutMs),
    })
    if (raw) {
      if (!response.ok) throw new WinPilotError(await safeMessage(response), response.status, null)
      return {
        bytes: Buffer.from(await response.arrayBuffer()),
        contentType: response.headers.get('content-type') || 'application/octet-stream',
      }
    }
    const text = await response.text()
    let parsed = null
    try { parsed = text ? JSON.parse(text) : null } catch { parsed = { raw: text } }
    if (!response.ok) {
      const detail = parsed?.detail ?? parsed?.error ?? text
      throw new WinPilotError(typeof detail === 'string' ? detail : JSON.stringify(detail), response.status, parsed)
    }
    return parsed
  }

  async function safeMessage(response) {
    try { return (await response.text()) || `HTTP ${response.status}` } catch { return `HTTP ${response.status}` }
  }

  /** @returns the node's reported status, accepting projection and ARP detail shapes. */
  function statusOf(data) {
    const candidates = [data?.run?.status, data?.arp?.status, data?.status]
    for (const candidate of candidates) {
      if (typeof candidate === 'string' && candidate) return candidate.toLowerCase()
    }
    return ''
  }

  /**
   * Poll a run until it reaches a terminal state.
   * @param runId - run identity returned by a submission endpoint.
   * @param options - polling budget.
   * @returns the last observed run document.
   */
  async function waitForRun(runId, { timeoutMs = settings.requestTimeoutMs, intervalMs = 400 } = {}) {
    const deadline = Date.now() + timeoutMs
    let last = null
    while (Date.now() < deadline) {
      last = await request('GET', `/api/v2/runs/${encodeURIComponent(runId)}`, undefined, { timeoutMs: 30_000 })
      if (TERMINAL.has(statusOf(last))) return last
      await new Promise((resolve) => setTimeout(resolve, intervalMs))
    }
    return { ...(last ?? {}), timed_out: true }
  }

  return {
    request,
    get: (path, options) => request('GET', path, undefined, options),
    post: (path, body, options) => request('POST', path, body, options),
    put: (path, body, options) => request('PUT', path, body, options),
    statusOf,
    waitForRun,
  }
}
