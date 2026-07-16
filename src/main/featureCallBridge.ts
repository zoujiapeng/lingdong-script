/**
 * 持久化 feature_call.py 子进程管理。
 * 维护一个长驻 Python 进程，通过 stdin/stdout JSON 协议通信，
 * 使有状态的 @feature（如浏览器控制）能在多次调用间保持上下文。
 */
import { ChildProcess } from 'child_process';
import * as path from 'path';
import { spawnPython } from './engine';
import { getBaseDir } from './env';

let _process: ChildProcess | null = null;
let _stdoutBuffer = '';
let _pending: Array<(result: any) => void> = [];
let _ready = false;
let _pendingLines: string[] = [];

/** 行协议解析：从 stdout 缓冲区中提取完整 JSON 行 */
function _processStdout(text: string): void {
  _stdoutBuffer += text;
  const lines = _stdoutBuffer.split('\n');
  _stdoutBuffer = lines.pop() || '';
  for (const line of lines) {
    if (!line.trim()) continue;
    try {
      const msg = JSON.parse(line);
      if (msg.type === 'ready') {
        _ready = true;
        // flush queued lines
        for (const ql of _pendingLines) {
          _process?.stdin?.write(ql + '\n');
        }
        _pendingLines = [];
        return;
      }
      const resolve = _pending.shift();
      if (resolve) resolve(msg);
    } catch {
      // non-JSON line, ignore
    }
  }
}

/** 确保子进程已启动 */
function _ensureProcess(): ChildProcess {
  if (_process && !_process.killed) return _process;

  _ready = false;
  const scriptPath = path.join(getBaseDir(), 'python', 'tools', 'feature_call.py');
  _process = spawnPython([scriptPath], {
    cwd: getBaseDir(),
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  _stdoutBuffer = '';

  _process.stdout!.on('data', (chunk: Buffer) => {
    _processStdout(chunk.toString('utf8'));
  });

  _process.stderr!.on('data', (chunk: Buffer) => {
    // feature_call.py may log to stderr, forward for debug
    const text = chunk.toString('utf8').trim();
    if (text) console.error('[feature_call]', text);
  });

  _process.on('exit', (code) => {
    console.log(`[feature_call] process exited (code ${code})`);
    _process = null;
    _ready = false;
    // Reject any pending requests
    const pending = _pending;
    _pending = [];
    for (const p of pending) p({ type: 'error', error: '子进程意外退出' });
  });

  _process.on('error', (err) => {
    console.error('[feature_call] process error:', err.message);
  });

  return _process;
}

/**
 * 发送一条命令到持久化子进程，等待 JSON 响应。
 * 子进程会在第一个请求前自动按需启动。
 */
export async function callFeature(
  name: string,
  params: Record<string, any> = {}
): Promise<any> {
  const proc = _ensureProcess();
  const line = JSON.stringify({ name, params });

  return new Promise((resolve) => {
    _pending.push(resolve);
    if (_ready) {
      proc.stdin!.write(line + '\n');
    } else {
      _pendingLines.push(line);
    }
  });
}

/** 获取子进程状态 */
export function isFeatureProcessReady(): boolean {
  return _ready && !!(_process && !_process.killed);
}

/** 优雅关闭子进程。通常在应用退出时调用 */
export function stopFeatureProcess(): void {
  if (_process && !_process.killed) {
    try { _process.stdin!.end(); } catch {}
    setTimeout(() => {
      if (_process && !_process.killed) {
        try { _process.kill('SIGTERM'); } catch {}
      }
    }, 2000);
  }
  _process = null;
  _ready = false;
  _pending = [];
  _pendingLines = [];
}
