/**
 * Python 引擎管理 — spawn、DSL 执行、LLM 调用
 */
import { spawn, SpawnOptions, ChildProcess } from 'child_process';
import * as path from 'path';
import * as fs from 'fs';
import * as http from 'http';
import * as https from 'https';
import { mainWindow, pythonProcess, setPythonProcess, setEngineRunning, setLastScript } from './state';
import { getBaseDir, isDev } from './env';

/** 优先使用 bundled Python runtime，否则 fallback 到系统 python */
function getPythonPath(): string {
  if (!isDev) {
    const bundled = path.join(process.resourcesPath, 'python-runtime', 'python.exe');
    if (fs.existsSync(bundled)) return bundled;
  }
  return 'python';
}

/** 跨平台 Python 子进程启动：强制 UTF-8 环境防止中文乱码 */
export function spawnPython(args: string[], options?: SpawnOptions) {
  return spawn(getPythonPath(), args, {
    ...options,
    env: { ...process.env, PYTHONIOENCODING: 'utf-8', ...options?.env },
  });
}

// ── 并发限制 ──────────────────────────────────────────────────

const MAX_CONCURRENT = 3;
let _activeOneShot = 0;
const _oneShotQueue: Array<() => void> = [];

function _drainOneShot() {
  while (_activeOneShot < MAX_CONCURRENT && _oneShotQueue.length > 0) {
    _oneShotQueue.shift()!();
  }
}

/**
 * 带并发限制的一次性 Python 子进程。
 * 排队等待，最多 MAX_CONCURRENT 个同时运行，避免 OOM。
 */
export function spawnPythonOneShot(args: string[], options?: SpawnOptions): Promise<ChildProcess> {
  return new Promise((resolve) => {
    const launch = () => {
      _activeOneShot++;
      const proc = spawn(getPythonPath(), args, {
        ...options,
        env: { ...process.env, PYTHONIOENCODING: 'utf-8', ...options?.env },
      });
      let done = false;
      const dec = () => { if (!done) { done = true; _activeOneShot--; _drainOneShot(); } };
      proc.on('exit', dec);
      proc.on('error', dec);
      resolve(proc);
    };
    if (_activeOneShot < MAX_CONCURRENT) {
      launch();
    } else {
      _oneShotQueue.push(launch);
    }
  });
}

/** 运行 Python 并收集 stdout/stderr (使用排队 one-shot 进程) */
export async function runPythonCollect(
  args: string[], options?: SpawnOptions, input?: string
): Promise<{ stdout: string; stderr: string }> {
  const proc = await spawnPythonOneShot(args, options);
  return new Promise((resolve) => {
    const outBufs: Buffer[] = [];
    const errBufs: Buffer[] = [];
    proc.stdout!.on('data', (c: Buffer) => outBufs.push(c));
    proc.stderr!.on('data', (c: Buffer) => errBufs.push(c));
    proc.on('close', () => resolve({
      stdout: Buffer.concat(outBufs).toString('utf8'),
      stderr: Buffer.concat(errBufs).toString('utf8'),
    }));
    proc.on('error', (e) => resolve({ stdout: '', stderr: e.message }));
    if (input !== undefined) {
      proc.stdin!.write(input);
      proc.stdin!.end();
    }
  });
}

/** Buffer for partial lines across stdout chunks (engine) */
let _engineLineBuffer = '';

/** 通过 spawnPython 执行 DSL (main.ts 有自己的本地版本) */
async function runDsl(dsl: string): Promise<{ success: boolean; error?: string }> {
  const scriptPath = path.join(getBaseDir(), 'python', 'tools', 'run_dsl.py');
  const result = await runPythonCollect([scriptPath], {
    cwd: getBaseDir(),
    stdio: ['pipe', 'pipe', 'pipe'],
  }, dsl);
  try {
    return JSON.parse(result.stdout.trim());
  } catch {
    return { success: false, error: result.stderr?.trim() || '执行失败' };
  }
}

/** 启动 Python 自动化引擎 */
export function startPythonEngine(scriptJson: string) {
  const proc = spawnPython(
    ['-u', path.join(getBaseDir(), 'python', 'engine', 'engine.py')],
    { cwd: getBaseDir(), stdio: ['pipe', 'pipe', 'pipe'] }
  );
  setPythonProcess(proc);
  setEngineRunning(true);
  setLastScript(scriptJson);
  _engineLineBuffer = '';

  proc.stdout!.on('data', (data: Buffer) => {
    _engineLineBuffer += data.toString('utf8');
    const lines = _engineLineBuffer.split('\n');
    _engineLineBuffer = lines.pop() || '';
    for (const line of lines) {
      if (!line) continue;
      try {
        const msg = JSON.parse(line);
        if (msg.type === 'execution_done') {
          setEngineRunning(false);
        }
        mainWindow?.webContents.send('engine-message', msg);
      } catch {
        mainWindow?.webContents.send('engine-log', { level: 'info', message: line });
      }
    }
  });

  proc.stderr!.on('data', (data: Buffer) => {
    mainWindow?.webContents.send('engine-log', { level: 'error', message: data.toString('utf8') });
  });

  proc.on('exit', () => { setEngineRunning(false); });

  proc.stdin!.write(scriptJson + '\n');
}

/** 停止 Python 引擎 */
export function stopPythonEngine() {
  setEngineRunning(false);
  const proc = pythonProcess;
  setPythonProcess(null);
  if (proc) {
    try { if (proc.stdin) proc.stdin.write(JSON.stringify({ type: 'stop' }) + '\n'); } catch {}
    setTimeout(() => {
      try { if (!proc.killed) proc.kill('SIGTERM'); } catch {}
    }, 2000);
  }
}

/** 调用 LLM (DeepSeek / 兼容 API) */
export async function callLLM(params: {
  apiKey: string;
  messages: { role: string; content: string }[];
  apiBase?: string;
  model?: string;
  response_format?: 'json' | 'text';
  max_tokens?: number;
}): Promise<any> {
  const apiBase = (params.apiBase || 'https://api.deepseek.com/v1').replace(/\/+$/, '');
  const model = params.model || 'deepseek-chat';
  const isHttps = apiBase.startsWith('https');
  const lib = isHttps ? https : http;

  return new Promise((resolve) => {
    const url = new URL(`${apiBase}/chat/completions`);
    const body = JSON.stringify({
      model,
      messages: params.messages,
      response_format: params.response_format === 'json' ? { type: 'json_object' } : undefined,
      temperature: 0.1,
      max_tokens: params.max_tokens || 2048,
    });

    const req = lib.request(
      url,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${params.apiKey}`,
        },
      },
      (res) => {
        let data = '';
        res.on('data', (chunk) => (data += chunk));
        res.on('end', () => {
          try { resolve(JSON.parse(data)); } catch { resolve({ error: 'JSON parse error', raw: data.slice(0, 500) }); }
        });
      }
    );
    req.on('error', (e) => resolve({ error: e.message }));
    req.write(body);
    req.end();
  });
}
