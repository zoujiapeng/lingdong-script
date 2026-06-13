/**
 * WinPilot server lifecycle — spawns the WinPilot FastAPI/uvicorn server as a
 * managed child process and exposes its localhost URL to the renderer. The
 * Agent panel embeds this server's native web UI in an iframe, so the full
 * WinPilot experience (chat, execution log, settings, traces, scripts, memory)
 * and its entire HTTP/WebSocket API are used directly — nothing reimplemented.
 */
import * as path from 'path';
import * as http from 'http';
import * as net from 'net';
import { ChildProcess } from 'child_process';
import { spawnPython } from './engine';
import { getBaseDir } from './env';

let _proc: ChildProcess | null = null;
let _port: number | null = null;
let _starting: Promise<number> | null = null;

const HOST = '127.0.0.1';

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
      const req = http.get({ host: HOST, port, path: '/', timeout: 2000 }, (res) => {
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
    });
    _proc = proc;

    proc.stdout?.on('data', (d: Buffer) => console.log('[winpilot]', d.toString().trimEnd()));
    proc.stderr?.on('data', (d: Buffer) => console.log('[winpilot:err]', d.toString().trimEnd()));
    proc.on('exit', (code) => {
      console.log('[winpilot] server exited, code=' + code);
      if (_proc === proc) { _proc = null; _port = null; }
    });

    await waitReady(port);
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

/** Stop the server (called on app quit). */
export function stopWinpilotServer(): void {
  if (_proc && !_proc.killed) {
    try { _proc.kill(); } catch { /* ignore */ }
  }
  _proc = null;
  _port = null;
  _starting = null;
}
