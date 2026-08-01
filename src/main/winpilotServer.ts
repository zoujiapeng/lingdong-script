/**
 * WinPilot server lifecycle — spawns the WinPilot FastAPI/uvicorn server as a
 * managed child process and exposes its authenticated localhost connection to
 * the native renderer workbench. The legacy server web UI remains available
 * only as a compatibility/diagnostic surface.
 */
import * as path from 'path';
import * as http from 'http';
import * as net from 'net';
import { randomBytes } from 'crypto';
import { ChildProcess } from 'child_process';
import { spawnPython } from './engine';
import { getBaseDir } from './env';

let _proc: ChildProcess | null = null;
let _port: number | null = null;
let _starting: Promise<number> | null = null;
let _lastError: string | null = null;
let _lifecycleGeneration = 0;
const _stderrTail: string[] = [];

const HOST = '127.0.0.1';
const RUNTIME_CONTRACT_VERSION = 3;
// Chromium rejects a fixed list of historically sensitive ports before a
// request reaches CORS or the server. The highest currently blocked port is
// 10080, so renderer-facing managed services deliberately use a high port.
// This keeps the allocation valid even when Chromium's exact list evolves.
export const MIN_RENDERER_SAFE_PORT = 12_000;
const CONTROL_TOKEN = process.env.WINPILOT_CONTROL_TOKEN || randomBytes(32).toString('base64url');

export function isRendererSafePort(port: number): boolean {
  return Number.isInteger(port) && port >= MIN_RENDERER_SAFE_PORT && port <= 65_535;
}

export function runtimeCompatibilityProblem(payload: any): string {
  const runtime = payload?.runtime;
  if (Number(runtime?.contract_version) !== RUNTIME_CONTRACT_VERSION) {
    return '托管的 Python 运行时协议已过期。';
  }
  const source = runtime?.source;
  const git = runtime?.git;
  if (
    !source
    || typeof source.loaded_stamp !== 'string'
    || !source.loaded_stamp
    || typeof source.current_stamp !== 'string'
    || !source.current_stamp
    || typeof source.drifted !== 'boolean'
    || !git
    || !Object.prototype.hasOwnProperty.call(git, 'loaded_commit')
    || !Object.prototype.hasOwnProperty.call(git, 'current_commit')
  ) {
    return '托管的 Python 运行时身份不完整。';
  }
  if (source.drifted || source.loaded_stamp !== source.current_stamp) {
    return '托管的 Python 源码在启动后发生了变化。';
  }
  const loadedCommit = String(git.loaded_commit || '');
  const currentCommit = String(git.current_commit || '');
  if (loadedCommit && currentCommit && loadedCommit !== currentCommit) {
    return '托管的 Python 进程来自不同的仓库提交。';
  }
  return '';
}

function pushStderr(line: string): void {
  for (const l of line.split(/\r?\n/)) {
    const t = l.trimEnd();
    if (t) _stderrTail.push(t);
  }
  while (_stderrTail.length > 25) _stderrTail.shift();
}

function requestPort(port: number): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    let closing = false;
    let settled = false;
    let selectedPort = 0;
    let pendingError: unknown = null;

    const settle = (error?: unknown) => {
      if (settled) return;
      settled = true;
      server.removeListener('error', onError);
      if (error) reject(error);
      else if (selectedPort) resolve(selectedPort);
      else reject(new Error('未找到可用端口'));
    };

    const closeAndSettle = (error?: unknown) => {
      if (error && !pendingError) pendingError = error;
      if (closing) return;
      closing = true;
      if (!server.listening) {
        settle(pendingError);
        return;
      }
      server.close((closeError) => settle(pendingError || closeError));
    };

    const onError = (error: Error) => {
      if (!pendingError) pendingError = error;
      // When close is already in progress, wait for its callback so the
      // promise never settles while the listening handle is still live.
      if (!closing) closeAndSettle(error);
    };

    server.once('error', onError);
    try {
      server.listen(port, HOST, () => {
        const address = server.address();
        selectedPort = typeof address === 'object' && address ? address.port : 0;
        closeAndSettle(selectedPort ? undefined : new Error('未找到可用端口'));
      });
    } catch (error) {
      settle(error);
    }
  });
}

function requestEphemeralPort(): Promise<number> {
  return requestPort(0);
}

function requestSpecificPort(port: number): Promise<number> {
  return requestPort(port);
}

/**
 * Generate renderer-safe candidates independently of the host's configured
 * ephemeral range. Some Windows installations allocate port 0 from a custom
 * low range (for example 1024-15000), so repeated port-0 allocations may not
 * reach Chromium's safe range before the startup retry budget is exhausted.
 */
export function rendererSafePortCandidates(seed: number, count = 256): number[] {
  const preferredStart = 49_152;
  const span = 65_535 - preferredStart + 1;
  const finiteSeed = Number.isFinite(seed) ? Math.trunc(seed) : 0;
  const normalizedSeed = ((finiteSeed % span) + span) % span;
  const limit = Math.max(1, Math.min(Math.trunc(count || 1), span));
  // The span is 2^14, so every odd stride visits every candidate exactly once.
  // A wide stride avoids getting trapped in one contiguous Windows excluded
  // port range while remaining deterministic and cheap to test.
  const stride = 10_127;
  return Array.from({ length: limit }, (_, index) => (
    preferredStart + ((normalizedSeed + (index * stride)) % span)
  ));
}

/** Find a free loopback port that an Electron renderer is allowed to fetch. */
export async function findFreePort(): Promise<number> {
  for (let attempt = 0; attempt < 32; attempt += 1) {
    const port = await requestEphemeralPort();
    if (isRendererSafePort(port)) return port;
  }
  const seed = Date.now() + process.pid;
  // Inspect the complete preferred high-port range in a dispersed order.
  // Windows can reserve contiguous blocks larger than the old 256-port retry
  // window; stopping after one such block incorrectly reported exhaustion.
  const preferredSpan = 65_535 - 49_152 + 1;
  for (const port of rendererSafePortCandidates(seed, preferredSpan)) {
    try {
      return await requestSpecificPort(port);
    } catch (error: any) {
      if (error?.code === 'EADDRINUSE' || error?.code === 'EACCES') continue;
      throw error;
    }
  }
  throw new Error('未找到浏览器可访问的本地端口');
}

export interface RuntimeReadinessProbeOptions {
  timeoutMs?: number;
  requestTimeoutMs?: number;
  retryDelayMs?: number;
}

/** Poll until the managed child exposes the runtime contract this app expects. */
export function waitReady(
  port: number,
  options: RuntimeReadinessProbeOptions = {},
): Promise<void> {
  const timeoutMs = options.timeoutMs ?? 120000;
  const requestTimeoutMs = options.requestTimeoutMs ?? 2000;
  const retryDelayMs = options.retryDelayMs ?? 400;
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    let settled = false;
    let retryTimer: NodeJS.Timeout | null = null;

    const finish = (error?: Error) => {
      if (settled) return;
      settled = true;
      if (retryTimer) {
        clearTimeout(retryTimer);
        retryTimer = null;
      }
      if (error) reject(error);
      else resolve();
    };

    const retry = () => {
      if (settled || retryTimer) return;
      if (Date.now() > deadline) {
        finish(new Error('WinPilot 服务未能就绪'));
        return;
      }
      retryTimer = setTimeout(() => {
        retryTimer = null;
        probe();
      }, retryDelayMs);
    };

    const probe = () => {
      if (settled) return;
      let attemptFinished = false;
      const retryOnce = () => {
        if (attemptFinished || settled) return;
        attemptFinished = true;
        retry();
      };
      const req = http.get({
        host: HOST,
        port,
        path: '/api/status',
        timeout: requestTimeoutMs,
        headers: { Authorization: `Bearer ${CONTROL_TOKEN}` },
      }, (res) => {
        const chunks: Buffer[] = [];
        res.on('data', (chunk: Buffer) => chunks.push(chunk));
        res.on('end', () => {
          if (attemptFinished || settled) return;
          if (res.statusCode !== 200) { retryOnce(); return; }
          try {
            const payload = JSON.parse(Buffer.concat(chunks).toString('utf8'));
            const compatibilityProblem = runtimeCompatibilityProblem(payload);
            if (!compatibilityProblem) {
              attemptFinished = true;
              finish();
              return;
            }
            _lastError = `${compatibilityProblem} 需要重启。`;
          } catch {
            _lastError = '托管的 Python 运行时返回了无效的状态数据。';
          }
          retryOnce();
        });
      });
      req.once('error', retryOnce);
      // Destroying the request emits ``error``. Let that one path schedule the
      // retry so a slow startup cannot double the number of live probes.
      req.once('timeout', () => {
        req.destroy(new Error('managed runtime readiness probe timed out'));
      });
    };
    probe();
  });
}

/** Start the WinPilot server (idempotent). Resolves once it is reachable. */
export function startWinpilotServer(): Promise<number> {
  if (_port) return Promise.resolve(_port);
  if (_starting) return _starting;

  _starting = (async () => {
    const generation = _lifecycleGeneration;
    const port = await findFreePort();
    const cwd = path.join(getBaseDir(), 'python', 'winpilot_home');
    const proc = spawnPython(['-u', 'serve.py', String(port), HOST], {
      cwd,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, WINPILOT_CONTROL_TOKEN: CONTROL_TOKEN },
    });
    _proc = proc;
    _lastError = null;

    proc.stdout?.on('data', (d: Buffer) => console.log('[winpilot]', d.toString().trimEnd()));
    proc.stderr?.on('data', (d: Buffer) => {
      const s = d.toString();
      pushStderr(s);
      console.log('[winpilot:err]', s.trimEnd());
    });
    proc.on('exit', (code) => {
      console.log('[winpilot] server exited, code=' + code);
      if (code && code !== 0 && _stderrTail.length) {
        _lastError = _stderrTail.slice(-12).join('\n');
      }
      if (_proc === proc) { _proc = null; _port = null; }
    });

    try {
      await waitReady(port);
      if (generation !== _lifecycleGeneration || _proc !== proc) {
        try { proc.kill(); } catch { /* ignore */ }
        throw new Error('WinPilot 服务启动已被后续重启取代。');
      }
    } catch (e) {
      _lastError = _lastError
        || (_stderrTail.slice(-12).join('\n') || (e instanceof Error ? e.message : String(e)));
      try { proc.kill(); } catch { /* ignore */ }
      if (_proc === proc) { _proc = null; _port = null; }
      throw e;
    }
    _port = port;
    _starting = null;
    console.log(`[winpilot] server ready on http://${HOST}:${port}`);
    return port;
  })();

  const starting = _starting;
  starting.catch(() => {
    if (_starting === starting) _starting = null;
  });
  return _starting;
}

/** The server URL, or null if not ready yet. */
export function getWinpilotUrl(): string | null {
  return _port ? `http://${HOST}:${_port}/` : null;
}

export function getWinpilotConnection(): { url: string; token: string } | null {
  const url = getWinpilotUrl();
  return url ? { url, token: CONTROL_TOKEN } : null;
}

/** Trigger a (re)start if not running, and report current status. Idempotent —
 *  safe to call repeatedly from the renderer poll. */
export async function ensureWinpilotServer(): Promise<{ url: string | null; token: string; starting: boolean; error: string | null }> {
  if (!_port && !_starting) {
    startWinpilotServer().catch(() => { /* error captured in _lastError */ });
  }
  return { url: getWinpilotUrl(), token: CONTROL_TOKEN, starting: !!_starting, error: _lastError };
}

/** Replace the managed Python child and wait for a contract-compatible service. */
export async function restartWinpilotServer(): Promise<{ url: string | null; token: string; starting: boolean; error: string | null }> {
  stopWinpilotServer();
  const port = await startWinpilotServer();
  return {
    url: `http://${HOST}:${port}/`,
    token: CONTROL_TOKEN,
    starting: false,
    error: _lastError,
  };
}

/** Stop the server (called on app quit). */
export function stopWinpilotServer(): void {
  _lifecycleGeneration += 1;
  if (_proc && !_proc.killed) {
    try { _proc.kill(); } catch { /* ignore */ }
  }
  _proc = null;
  _port = null;
  _starting = null;
}
