/**
 * Lifecycle for the bundled WinPilot runtime.
 *
 * The runtime is a separate Python process. This plugin never edits or depends on
 * DSH's own files: it either attaches to an already-running loopback runtime or
 * spawns the repository's own serve.py on a private port.
 * @module dsh-plugin-lingdong/sidecar
 */
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import path from 'node:path'

/** Owns one WinPilot runtime process for the lifetime of the plugin. */
export class WinPilotSidecar {
  /**
   * @param settings - resolved plugin settings.
   * @param log - logger receiving lifecycle diagnostics.
   */
  constructor(settings, log = console) {
    this.settings = settings
    this.log = log
    this.child = null
    this.ready = null
    this.external = false
  }

  /** @returns loopback base URL of the runtime. */
  get baseUrl() {
    return `http://${this.settings.host}:${this.settings.port}`
  }

  /**
   * Ensure a runtime answers /api/ping, attaching or spawning as needed.
   * @returns after the runtime is reachable.
   */
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
    if (await this.#healthy()) {
      this.external = true
      this.log.info?.(`[lingdong] attached to existing runtime at ${this.baseUrl}`)
      return
    }
    if (!this.settings.autoStart) {
      throw new Error(`no WinPilot runtime at ${this.baseUrl} and autoStart is disabled`)
    }
    if (!existsSync(this.settings.serveScript)) {
      throw new Error(`WinPilot serve.py not found at ${this.settings.serveScript}`)
    }
    this.log.info?.(
      `[lingdong] starting ${this.settings.python} serve.py ${this.settings.port} ${this.settings.host}`,
    )
    this.child = spawn(
      this.settings.python,
      ['-u', this.settings.serveScript, String(this.settings.port), this.settings.host],
      {
        cwd: path.dirname(this.settings.serveScript),
        env: {
          ...process.env,
          WINPILOT_CONTROL_TOKEN: this.settings.token,
          PYTHONIOENCODING: 'utf-8',
          PYTHONUTF8: '1',
        },
        stdio: ['ignore', 'pipe', 'pipe'],
        windowsHide: true,
      },
    )
    this.child.stdout?.on('data', (chunk) => this.log.info?.(`[lingdong:py] ${String(chunk).trimEnd()}`))
    this.child.stderr?.on('data', (chunk) => this.log.warn?.(`[lingdong:py] ${String(chunk).trimEnd()}`))
    this.child.on('exit', (code, signal) => {
      this.log.warn?.(`[lingdong] runtime exited (code=${code} signal=${signal})`)
      this.ready = null
      this.child = null
    })
    const deadline = Date.now() + this.settings.startupTimeoutMs
    while (Date.now() < deadline) {
      if (await this.#healthy()) return
      if (!this.child) throw new Error('WinPilot runtime exited during startup')
      await new Promise((resolve) => setTimeout(resolve, 250))
    }
    throw new Error(`WinPilot runtime did not become healthy within ${this.settings.startupTimeoutMs}ms`)
  }

  async #healthy() {
    try {
      const response = await fetch(`${this.baseUrl}/api/ping`, {
        signal: AbortSignal.timeout(1500),
      })
      return response.ok
    } catch {
      return false
    }
  }

  /** Stop a runtime this plugin spawned; leave an attached one untouched. */
  async stop() {
    if (!this.child) return
    const child = this.child
    this.child = null
    this.ready = null
    await new Promise((resolve) => {
      const timer = setTimeout(() => {
        try { child.kill('SIGKILL') } catch { /* already gone */ }
        resolve()
      }, 5000)
      child.once('exit', () => { clearTimeout(timer); resolve() })
      try { child.kill() } catch { clearTimeout(timer); resolve() }
    })
  }
}
