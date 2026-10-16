/**
 * Plugin configuration and resolved runtime settings.
 * @module dsh-plugin-lingdong/config
 */
import Schema from '@deepseek-ai/schemastery'
import { existsSync } from 'node:fs'
import { randomBytes } from 'node:crypto'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

/** Repository root inferred from this package's location (packages/<pkg>/src -> repo root). */
export const PACKAGE_ROOT = fileURLToPath(new URL('../', import.meta.url))
export const REPO_ROOT_FALLBACK = path.resolve(PACKAGE_ROOT, '..', '..')

/** Declared, schema-validated plugin configuration. */
export const Config = Schema.object({
  /** Repository root holding python/ and python-runtime/. Defaults to the monorepo this package lives in. */
  repoRoot: Schema.string().default(REPO_ROOT_FALLBACK),
  /** Python interpreter. Empty selects the bundled runtime, then PATH. */
  python: Schema.string().default(''),
  /** Loopback host for the WinPilot runtime. */
  host: Schema.string().default('127.0.0.1'),
  /** Loopback port for the WinPilot runtime. */
  port: Schema.number().step(1).min(1).max(65535).default(8790),
  /** Control token; empty generates one per plugin load. */
  token: Schema.string().default(''),
  /** Start the bundled runtime when nothing is listening. */
  autoStart: Schema.boolean().default(true),
  /** Claim the exclusive DSH computer-use provider slot. */
  registerComputerUse: Schema.boolean().default(true),
  /** Per-request timeout in milliseconds. */
  requestTimeoutMs: Schema.number().step(1000).min(1000).max(3_600_000).default(120_000),
  /** How long to wait for a freshly spawned runtime to answer /api/ping. */
  startupTimeoutMs: Schema.number().step(1000).min(1000).max(600_000).default(60_000),
})

/**
 * Resolve declared configuration into concrete paths and secrets.
 * @param config - validated plugin configuration.
 * @returns resolved settings used by the sidecar and the REST client.
 */
export function resolveConfig(config = {}) {
  const repoRoot = path.resolve(config.repoRoot || REPO_ROOT_FALLBACK)
  const bundled = path.join(
    repoRoot,
    'python-runtime',
    process.platform === 'win32' ? 'python.exe' : 'python',
  )
  const fallback = process.platform === 'win32' ? 'python' : 'python3'
  return {
    repoRoot,
    python: config.python || (existsSync(bundled) ? bundled : fallback),
    serveScript: path.join(repoRoot, 'python', 'winpilot_home', 'serve.py'),
    host: config.host || '127.0.0.1',
    port: config.port ?? 8790,
    token: config.token || process.env.WINPILOT_CONTROL_TOKEN || randomBytes(24).toString('hex'),
    autoStart: config.autoStart !== false,
    registerComputerUse: config.registerComputerUse !== false,
    requestTimeoutMs: config.requestTimeoutMs ?? 120_000,
    startupTimeoutMs: config.startupTimeoutMs ?? 60_000,
  }
}
