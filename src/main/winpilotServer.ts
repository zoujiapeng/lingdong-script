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
const _stderrTail: string[] = [];

const HOST = '127.0.0.1';
const CONTROL_TOKEN = process.env.WINPILOT_CONTROL_TOKEN || randomBytes(32).toString('base64url');

function pushStderr(line: string): void {
  for (const l of line.split(/\r?\n/)) {
    const t = l.trimEnd();
    if (t) _stderrTail.push(t);
  }
  while (_stderrTail.length > 25) _stderrTail.shift();
}

/** Find a free TCP port on the loopback interface. */
function findFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on('error', reject);
    srv.listen(0, HOST, () => {
      const addr = srv.address();
      const port = typeof addr === 'object' && addr ? addr.port : 0;
      srv.close(() => (port ? resolve(port) : reject(new Error('no free port'))));
    });
  });
}

/** Poll the server root until it answers 200 or the deadline passes. */
function waitReady(port: number, timeoutMs = 30000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const probe = () => {
      const req = http.get({
        host: HOST,
        port,
        path: '/api/ping',
        timeout: 2000,
        headers: { Authorization: `Bearer ${CONTROL_TOKEN}` },
      }, (res) => {
        res.resume();
        if (res.statusCode && res.statusCode < 500) resolve();
        else retry();
      });
      req.on('error', retry);
      req.on('timeout', () => { req.destroy(); retry(); });
    };
    const retry = () => {
      if (Date.now() > deadline) reject(new Error('WinPilot server did not become ready'));
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
    } catch (e) {
      _lastError = _lastError
        || (_stderrTail.slice(-12).join('\n') || (e instanceof Error ? e.message : String(e)));
      throw e;
    }
    _port = port;
    _starting = null;
    console.log(`[winpilot] server ready on http://${HOST}:${port}`);
    return port;
  })();

  _starting.catch(() => { _starting = null; });
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

/** Stop the server (called on app quit). */
export function stopWinpilotServer(): void {
  if (_proc && !_proc.killed) {
    try { _proc.kill(); } catch { /* ignore */ }
  }
  _proc = null;
  _port = null;
  _starting = null;
}
