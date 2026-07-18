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
const RUNTIME_CONTRACT_VERSION = 2;
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

function requestEphemeralPort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once('error', reject);
    server.listen(0, HOST, () => {
      const address = server.address();
      const port = typeof address === 'object' && address ? address.port : 0;
      server.close((error) => {
        if (error) reject(error);
        else if (port) resolve(port);
        else reject(new Error('未找到可用端口'));
      });
    });
  });
}

function requestSpecificPort(port: number): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once('error', reject);
    server.listen(port, HOST, () => {
      server.close((error) => {
        if (error) reject(error);
        else resolve(port);
      });
    });
  });
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
  const normalizedSeed = Math.abs(Math.trunc(seed || 0)) % span;
  const limit = Math.max(1, Math.min(Math.trunc(count || 1), span));
  return Array.from({ length: limit }, (_, index) => (
    preferredStart + ((normalizedSeed + index) % span)
  ));
}

/** Find a free loopback port that an Electron renderer is allowed to fetch. */
export async function findFreePort(): Promise<number> {
  for (let attempt = 0; attempt < 32; attempt += 1) {
    const port = await requestEphemeralPort();
    if (isRendererSafePort(port)) return port;
  }
  const seed = Date.now() + process.pid;
  for (const port of rendererSafePortCandidates(seed)) {
    try {
      return await requestSpecificPort(port);
    } catch (error: any) {
      if (error?.code === 'EADDRINUSE' || error?.code === 'EACCES') continue;
      throw error;
    }
  }
  throw new Error('未找到浏览器可访问的本地端口');
}

/** Poll until the managed child exposes the runtime contract this app expects. */
function waitReady(port: number, timeoutMs = 30000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const probe = () => {
      const req = http.get({
        host: HOST,
        port,
        path: '/api/status',
        timeout: 2000,
        headers: { Authorization: `Bearer ${CONTROL_TOKEN}` },
      }, (res) => {
        const chunks: Buffer[] = [];
        res.on('data', (chunk: Buffer) => chunks.push(chunk));
        res.on('end', () => {
          if (res.statusCode !== 200) { retry(); return; }
          try {
            const payload = JSON.parse(Buffer.concat(chunks).toString('utf8'));
            const compatibilityProblem = runtimeCompatibilityProblem(payload);
            if (!compatibilityProblem) {
              resolve();
              return;
            }
            _lastError = `${compatibilityProblem} 需要重启。`;
          } catch {
            _lastError = '托管的 Python 运行时返回了无效的状态数据。';
          }
          retry();
        });
      });
      req.on('error', retry);
      req.on('timeout', () => { req.destroy(); retry(); });
    };
    const retry = () => {
      if (Date.now() > deadline) reject(new Error('WinPilot 服务未能就绪'));
      else setTimeout(probe, 400);
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
