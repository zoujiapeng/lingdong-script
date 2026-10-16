/**
 * Browser session surface for the Lingdong provider.
 *
 * Two decisions shape this module.
 *
 * First, it does not write a browser stack. The repository already owns a
 * comprehensive one - navigation, element interaction, extraction, script
 * execution, storage, waits and downloads, all over CDP - driven through the
 * feature registry by a long-lived JSON-lines worker. That worker is a process,
 * not an import: the vNext line must not take a dependency on the legacy
 * `features.*` namespace, and Electron already drives the same worker the same
 * way. Reusing it here adds a second host, not a second implementation.
 *
 * Second, it never silently opens a browser window on someone's desktop. The
 * underlying helper will lazily start a visible Chrome when a call arrives with
 * no session, on a five-second budget, and fall back to a Playwright build that
 * may not be downloaded. So this module owns session state: an action other
 * than `launch` or `connect` is refused until one of those has succeeded, and
 * `connect` verifies the CDP endpoint itself before letting anything attach.
 * @module dsh-plugin-lingdong/browser
 */
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'

/** One reply larger than this is not a browser reply. */
const MAX_REPLY_BYTES = 32 * 1024 * 1024

/** Where a Chromium-family browser usually lives, before PATH is consulted. */
const BROWSER_CANDIDATES = [
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
]

/**
 * Locate a Chromium-family executable.
 * @param configured - explicit path from plugin configuration, when set.
 * @returns an absolute path, or null when none exists.
 */
export function findBrowser(configured) {
  if (configured) return existsSync(configured) ? configured : null
  for (const candidate of BROWSER_CANDIDATES) {
    if (existsSync(candidate)) return candidate
  }
  for (const name of ['chrome', 'msedge', 'chromium']) {
    const found = which(name)
    if (found) return found
  }
  return null
}

/** Minimal PATH lookup, so the plugin needs no dependency for it. */
function which(name) {
  const exts = (process.env.PATHEXT || '.EXE;.CMD;.BAT').split(';')
  for (const dir of String(process.env.PATH || '').split(path.delimiter)) {
    if (!dir) continue
    for (const ext of exts) {
      const candidate = path.join(dir, name + ext.toLowerCase())
      if (existsSync(candidate)) return candidate
      const upper = path.join(dir, name + ext.toUpperCase())
      if (existsSync(upper)) return upper
    }
  }
  return null
}

/** Probe a CDP endpoint over its own HTTP discovery surface. */
export async function probeCdp(port, timeoutMs = 2000) {
  try {
    const response = await fetch(`http://127.0.0.1:${port}/json/version`, {
      signal: AbortSignal.timeout(timeoutMs),
    })
    if (!response.ok) return null
    const body = await response.json()
    return typeof body === 'object' && body !== null ? body : null
  } catch {
    return null
  }
}

/**
 * Ask a browser to exit through its own browser-level CDP endpoint.
 *
 * The process this plugin spawns is only a launcher: a Chromium-family browser
 * hands off to its own browser process and lets the launcher exit successfully,
 * so the child handle cannot stop it. `Browser.close` addresses the browser we
 * actually attached to, which is the only handle that means anything here.
 * @param port - CDP port the browser listens on.
 * @param timeoutMs - how long to wait for the socket to settle.
 * @returns whether the browser acknowledged and closed.
 */
export async function closeBrowserOverCdp(port, timeoutMs = 10_000) {
  const version = await probeCdp(port)
  const url = version?.webSocketDebuggerUrl
  if (!url || typeof WebSocket !== 'function') return false
  return new Promise((resolve) => {
    let settled = false
    const finish = (value) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      try { socket.close() } catch { /* already closing */ }
      resolve(value)
    }
    const timer = setTimeout(() => finish(false), timeoutMs)
    let socket = null
    try {
      socket = new WebSocket(String(url))
    } catch {
      finish(false)
      return
    }
    socket.addEventListener('open', () => {
      try { socket.send(JSON.stringify({ id: 1, method: 'Browser.close' })) } catch { finish(false) }
    })
    socket.addEventListener('error', () => finish(false))
    // The browser drops the socket as it exits, which is the acknowledgement.
    socket.addEventListener('close', () => finish(true))
  })
}

/** Keys a feature-registry envelope may carry, at either nesting level. */
const ENVELOPE_KEYS = ['success', 'result', 'error', 'feature', 'elapsed_ms']

/**
 * Unwrap a feature reply down to the feature's own payload.
 *
 * One call can come back wrapped twice: the registry wraps what the worker
 * printed, and the feature functions wrap their own return value. Rather than
 * hard-coding a depth, unwrap while the value still looks exactly like one of
 * those envelopes - a page's own result object will not.
 * @param value - the worker's reply, at any depth.
 * @returns `{ ok, result, error }`.
 */
export function unwrapFeature(value) {
  let current = value
  for (let depth = 0; depth < 4; depth += 1) {
    if (!current || typeof current !== 'object' || Array.isArray(current)) break
    if (typeof current.success !== 'boolean') break
    if (!Object.keys(current).every((key) => ENVELOPE_KEYS.includes(key))) break
    if (current.success === false) {
      return { ok: false, result: 'result' in current ? current.result : null, error: String(current.error || '') }
    }
    if (!('result' in current)) break
    current = current.result
  }
  return { ok: true, result: current, error: '' }
}

/**
 * The repository's own feature worker, as a process this plugin owns.
 *
 * Calls are serialized: the worker is one process holding global browser state,
 * so two overlapping calls would interleave on one page.
 */
export class FeatureWorker {
  /**
   * @param settings - resolved plugin settings.
   * @param log - logger receiving lifecycle diagnostics.
   */
  constructor(settings, log = console) {
    this.settings = settings
    this.log = log
    this.child = null
    this.ready = null
    this.buffer = ''
    this.pending = []
    this.queue = Promise.resolve()
    this.exited = null
  }

  /** @returns whether the worker process is currently alive. */
  get alive() {
    return Boolean(this.child)
  }

  /** Start the worker and wait for its readiness line. */
  async ensure() {
    if (!this.ready) {
      this.ready = this.#start().catch((error) => {
        this.ready = null
        throw error
      })
    }
    return this.ready
  }

  async #start() {
    if (!existsSync(this.settings.featureCallScript)) {
      throw new Error(`feature worker not found at ${this.settings.featureCallScript}`)
    }
    this.log.info?.(`[lingdong] starting feature worker ${this.settings.python} ${this.settings.featureCallScript}`)
    // The worker inserts a relative `python` on sys.path, so its own directory
    // layout decides the working directory: the repository root.
    this.child = spawn(this.settings.python, ['-u', this.settings.featureCallScript], {
      cwd: this.settings.repoRoot,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    })
    this.child.stdout.setEncoding('utf8')
    this.child.stdout.on('data', (chunk) => this.#onData(chunk))
    this.child.stderr.setEncoding('utf8')
    this.child.stderr.on('data', (chunk) => this.log.warn?.(`[lingdong:browser] ${String(chunk).trimEnd()}`))
    this.exited = new Promise((resolve) => {
      this.child.on('exit', (code, signal) => {
        this.log.warn?.(`[lingdong] feature worker exited (code=${code} signal=${signal})`)
        const pending = this.pending
        this.pending = []
        this.child = null
        this.ready = null
        for (const entry of pending) entry.reject(new Error('feature worker exited'))
        resolve()
      })
    })

    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`feature worker did not report ready within ${this.settings.browserStartupTimeoutMs}ms`)), this.settings.browserStartupTimeoutMs)
      this.pending.push({
        ready: true,
        resolve: () => { clearTimeout(timer); resolve() },
        reject: (error) => { clearTimeout(timer); reject(error) },
      })
    })
  }

  #onData(chunk) {
    this.buffer += chunk
    if (this.buffer.length > MAX_REPLY_BYTES) {
      this.buffer = ''
      const pending = this.pending
      this.pending = []
      for (const entry of pending) entry.reject(new Error('feature worker reply exceeded the bound'))
      return
    }
    let at = this.buffer.indexOf('\n')
    while (at !== -1) {
      const line = this.buffer.slice(0, at).trim()
      this.buffer = this.buffer.slice(at + 1)
      if (line) this.#onLine(line)
      at = this.buffer.indexOf('\n')
    }
  }

  #onLine(line) {
    let parsed = null
    try {
      parsed = JSON.parse(line)
    } catch {
      this.log.warn?.(`[lingdong:browser] unparsable worker line: ${line.slice(0, 200)}`)
      return
    }
    const entry = this.pending.shift()
    if (!entry) return
    if (entry.ready) {
      entry.resolve(parsed)
      return
    }
    if (parsed && parsed.type === 'error') {
      entry.reject(new Error(String(parsed.error || 'feature call failed')))
      return
    }
    entry.resolve(parsed)
  }

  /**
   * Call one registered feature through the worker.
   * @param name - feature name, as the registry knows it.
   * @param params - named parameters.
   * @returns the feature's own `{ ok, result, error }` envelope.
   */
  async call(name, params) {
    await this.ensure()
    const run = this.queue.then(() => this.#exchange(name, params))
    // Keep the chain alive whether or not this particular call failed.
    this.queue = run.then(() => undefined, () => undefined)
    return run
  }

  #exchange(name, params) {
    return new Promise((resolve, reject) => {
      const child = this.child
      if (!child) {
        reject(new Error('feature worker is not running'))
        return
      }
      const timer = setTimeout(() => {
        const at = this.pending.findIndex((entry) => entry.timer === timer)
        if (at !== -1) this.pending.splice(at, 1)
        reject(new Error(`feature ${name} did not answer within ${this.settings.browserTimeoutMs}ms`))
      }, this.settings.browserTimeoutMs)
      this.pending.push({
        timer,
        resolve: (reply) => {
          clearTimeout(timer)
          const payload = reply && typeof reply === 'object' ? reply.result : null
          const unwrapped = unwrapFeature(payload)
          resolve({
            ok: unwrapped.ok,
            result: unwrapped.result,
            error: unwrapped.error,
            feature: name,
            elapsed_ms: payload && typeof payload === 'object' ? payload.elapsed_ms : undefined,
          })
        },
        reject: (error) => { clearTimeout(timer); reject(error) },
      })
      child.stdin.write(`${JSON.stringify({ name, params: params || {} })}\n`)
    })
  }

  /** Stop the worker process. */
  async stop() {
    const child = this.child
    if (!child) return
    try { child.stdin.end() } catch { /* already closed */ }
    const exited = this.exited
    child.kill()
    const timer = setTimeout(() => { try { child.kill('SIGKILL') } catch { /* gone */ } }, 5000)
    try { await exited } catch { /* already reported */ }
    clearTimeout(timer)
  }
}

/**
 * One browser session: the feature worker plus the window it drives.
 *
 * The session facts are what make the tool safe to hand to a model. A model
 * that has to remember whether it launched a browser will eventually forget;
 * refusing an action with no session cannot be forgotten.
 */
export class BrowserSession {
  /**
   * @param settings - resolved plugin settings.
   * @param log - logger receiving lifecycle diagnostics.
   */
  constructor(settings, log = console) {
    this.settings = settings
    this.log = log
    this.worker = new FeatureWorker(settings, log)
    this.launched = null
    this.launchPort = null
    this.profileDir = null
    this.attached = false
  }

  /** @returns the session summary reported to the model. */
  facts() {
    return {
      worker_running: this.worker.alive,
      attached: this.attached,
      port: this.settings.browserPort,
      launched_pid: this.launched ? this.launched.pid : null,
      launched_executable: this.settings.browserExecutable || '',
    }
  }

  /**
   * Start a browser this session owns and attach to it.
   *
   * A private profile keeps the user's own browser untouched, and the port is
   * verified before anything attaches so a failure is reported as a failed
   * launch rather than as a broken session.
   * @returns the browser facts observed at attach time.
   */
  async launch({ headless = true, port = this.settings.browserPort } = {}) {
    if (this.attached) return { ok: true, already_attached: true, ...(await probeCdp(port) ?? {}) }
    const executable = findBrowser(this.settings.browserExecutable)
    if (!executable) {
      throw new Error(
        'no Chromium-family browser found; set the browserExecutable plugin option, '
        + 'or start a browser yourself with --remote-debugging-port and use action "connect"',
      )
    }
    const existing = await probeCdp(port)
    if (existing) {
      this.attached = true
      const connected = await this.worker.call('browser_connect', { port })
      if (!connected.ok) throw new Error(connected.error || 'could not attach to the existing CDP endpoint')
      return { ok: true, reused_endpoint: true, browser: String(existing.Browser || ''), executable }
    }
    // Anything that fails between here and a live session must leave nothing
    // behind: no process holding a port, no temporary profile on disk.
    try {
      this.profileDir = await mkdtemp(path.join(tmpdir(), 'lingdong-browser-'))
      const args = [
        `--remote-debugging-port=${port}`,
        `--user-data-dir=${this.profileDir}`,
        '--no-first-run',
        '--no-default-browser-check',
        'about:blank',
      ]
      if (headless) args.unshift('--headless=new')
      this.log.info?.(`[lingdong] starting ${executable} on CDP port ${port}`)
      this.launched = spawn(executable, args, { stdio: 'ignore', windowsHide: true })
      this.launchPort = port
      const deadline = Date.now() + this.settings.browserStartupTimeoutMs
      let version = null
      while (Date.now() < deadline) {
        // The spawned process is only the launcher: a Chromium-family browser
        // may hand off to its own browser process and let this one exit
        // successfully, so a live CDP endpoint - not the child's lifetime - is
        // what says the browser is up.
        version = await probeCdp(port)
        if (version) break
        await new Promise((resolve) => setTimeout(resolve, 250))
      }
      if (!version) {
        const exitCode = this.launched.exitCode
        throw new Error(
          `the browser exposed no CDP endpoint on port ${port} within ${this.settings.browserStartupTimeoutMs}ms`
          + (exitCode === null ? '' : ` (the launcher exited with code ${exitCode}; is the port taken by something that is not a CDP endpoint?)`),
        )
      }
      const connected = await this.worker.call('browser_connect', { port })
      if (!connected.ok) throw new Error(connected.error || 'could not attach to the launched browser')
      this.attached = true
      return { ok: true, launched: true, browser: String(version.Browser || ''), executable, pid: this.launched.pid }
    } catch (error) {
      await this.close()
      throw error
    }
  }

  /**
   * Attach to a browser someone else started.
   *
   * The endpoint is verified here first: the underlying helper would otherwise
   * launch a visible Chrome of its own on a five-second budget, which is not
   * something a tool call should do to someone's desktop.
   * @returns the browser facts observed at attach time.
   */
  async connect({ port = this.settings.browserPort } = {}) {
    const version = await probeCdp(port)
    if (!version) {
      throw new Error(
        `no CDP endpoint answers on port ${port}; start a Chromium-family browser with `
        + `--remote-debugging-port=${port} first, or use action "launch"`,
      )
    }
    const connected = await this.worker.call('browser_connect', { port })
    if (!connected.ok) throw new Error(connected.error || `could not attach on port ${port}`)
    this.attached = true
    return { ok: true, attached: true, browser: String(version.Browser || ''), port }
  }

  /**
   * Run one browser feature, refusing until a session exists.
   * @param name - feature name.
   * @param params - named parameters.
   * @returns the feature envelope.
   */
  async call(name, params) {
    if (!this.attached) {
      throw new Error('no browser session; call action "launch" or action "connect" first')
    }
    return this.worker.call(name, params)
  }

  /** Detach, stop a browser this session launched, and release its profile. */
  async close() {
    const launched = this.launched
    if (this.attached) {
      try { await this.worker.call('browser_close', {}) } catch { /* the worker may already be gone */ }
      this.attached = false
    }
    this.launched = null
    if (launched) {
      // Only a browser this session started is asked to exit; one the caller
      // attached to is theirs and is left running.
      await closeBrowserOverCdp(this.launchPort ?? this.settings.browserPort)
      if (launched.exitCode === null) {
        try { launched.kill() } catch { /* already gone */ }
        await new Promise((resolve) => {
          const timer = setTimeout(() => { try { launched.kill('SIGKILL') } catch { /* gone */ } ; resolve() }, 5000)
          launched.once('exit', () => { clearTimeout(timer); resolve() })
        })
      }
    }
    this.launchPort = null
    const dir = this.profileDir
    this.profileDir = null
    if (dir) {
      // A browser that is still shutting down holds its profile open briefly.
      for (let attempt = 0; attempt < 10; attempt += 1) {
        try {
          await rm(dir, { recursive: true, force: true })
          break
        } catch {
          await new Promise((resolve) => setTimeout(resolve, 300))
        }
      }
    }
  }

  /** Stop everything this session owns, including the worker process. */
  async dispose() {
    await this.close()
    await this.worker.stop()
  }
}
