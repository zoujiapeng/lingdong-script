/**
 * 调度器守护进程桥接 — 管理持久化 Python 调度器进程
 * 遵循 macro_recorder.py 的 stdin/stdout JSON-line 模式
 */
import { ChildProcess, spawn } from 'child_process';
import * as path from 'path';
import { EventEmitter } from 'events';

export interface SchedulerEvent {
  type: 'fire' | 'pre_fire' | 'error' | 'log' | 'ready' | 'reloaded' | 'pong';
  schedule_id?: string;
  schedule?: any;
  confirm_id?: string;
  risk_level?: string;
  message?: string;
  level?: string;
  timestamp?: string;
  pid?: number;
  count?: number;
}

export class SchedulerBridge extends EventEmitter {
  private _process: ChildProcess | null = null;
  private _buf = '';
  private _pythonPath: string;
  private _scriptPath: string;
  private _baseDir: string;
  private _schedulesPath: string = '';
  private _stopping = false;
  private _restartTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(pythonPath: string, scriptPath: string, baseDir: string) {
    super();
    this._pythonPath = pythonPath;
    this._scriptPath = scriptPath;
    this._baseDir = baseDir;
  }

  get isAlive(): boolean {
    return this._process !== null && !this._process.killed;
  }

  start(schedulesPath: string): void {
    if (this.isAlive) return;
    this._schedulesPath = schedulesPath;
    this._stopping = false;

    this._process = spawn(this._pythonPath, [this._scriptPath], {
      cwd: this._baseDir,
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
    });

    this._buf = '';

    this._process.stdout!.on('data', (chunk: Buffer) => {
      this._buf += chunk.toString('utf8');
      const lines = this._buf.split('\n');
      this._buf = lines.pop() || '';
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const msg = JSON.parse(line);
          this._handleMessage(msg);
        } catch {
          // non-JSON line (e.g., stray print) — ignore
        }
      }
    });

    this._process.stderr!.on('data', (chunk: Buffer) => {
      console.error('[scheduler-daemon]', chunk.toString('utf8').trim());
    });

    this._process.on('exit', (code) => {
      console.log(`[scheduler-daemon] exited (code=${code})`);
      this._process = null;
      if (!this._stopping) {
        console.log('[scheduler-daemon] unexpected exit — restarting in 2s');
        this._restartTimer = setTimeout(() => {
          if (!this._stopping) this.start(this._schedulesPath);
        }, 2000);
      }
    });

    this._process.on('error', (err) => {
      console.error('[scheduler-daemon] process error:', err.message);
      this._process = null;
      if (!this._stopping) {
        console.log('[scheduler-daemon] process error — restarting in 2s');
        this._restartTimer = setTimeout(() => {
          if (!this._stopping) this.start(this._schedulesPath);
        }, 2000);
      }
    });

    // Send init message
    this._send({
      type: 'init',
      config: {
        schedules_path: schedulesPath,
        poll_interval: 15,
      },
    });
  }

  stop(): void {
    this._stopping = true;
    if (this._restartTimer) { clearTimeout(this._restartTimer); this._restartTimer = null; }
    if (!this.isAlive) return;
    try {
      this._send({ type: 'shutdown' });
    } catch {}
    // Force kill after 2s if not already dead
    setTimeout(() => {
      if (this._process && !this._process.killed) {
        this._process.kill('SIGTERM');
      }
    }, 2000);
  }

  reload(schedules?: any[]): void {
    if (!this.isAlive) return;
    const msg: any = { type: 'reload' };
    if (schedules) msg.schedules = schedules;
    this._send(msg);
  }

  ping(): void {
    if (!this.isAlive) return;
    this._send({ type: 'ping' });
  }

  /** 发送用户对 pre_fire 的响应 */
  sendFireResponse(confirmId: string, action: 'proceed' | 'cancel' | 'snooze'): void {
    if (!this.isAlive) return;
    this._send({ type: 'fire_response', confirm_id: confirmId, action });
  }

  private _send(msg: any): void {
    if (this._process?.stdin?.writable) {
      this._process.stdin.write(JSON.stringify(msg) + '\n');
    }
  }

  private _handleMessage(msg: any): void {
    switch (msg.type) {
      case 'fire':
        this.emit('fire', {
          type: 'fire',
          schedule_id: msg.schedule_id,
          schedule: msg.schedule,
          timestamp: msg.timestamp,
        } as SchedulerEvent);
        break;
      case 'pre_fire':
        this.emit('pre_fire', {
          type: 'pre_fire',
          schedule_id: msg.schedule_id,
          schedule: msg.schedule,
          confirm_id: msg.confirm_id,
          risk_level: msg.risk_level,
          timestamp: msg.timestamp,
        } as SchedulerEvent);
        break;
      case 'ready':
        console.log(`[scheduler-daemon] ready (pid=${msg.pid})`);
        this.emit('ready', { type: 'ready', pid: msg.pid } as SchedulerEvent);
        break;
      case 'reloaded':
        this.emit('reloaded', { type: 'reloaded', count: msg.count } as SchedulerEvent);
        break;
      case 'pong':
        this.emit('pong', { type: 'pong' } as SchedulerEvent);
        break;
      case 'log':
        console.log(`[scheduler-daemon] ${msg.message}`);
        this.emit('log', {
          type: 'log',
          level: msg.level || 'info',
          message: msg.message,
        } as SchedulerEvent);
        break;
      case 'error':
        console.error('[scheduler-daemon]', msg.message);
        this.emit('error', {
          type: 'error',
          message: msg.message,
        } as SchedulerEvent);
        break;
    }
  }
}

/** 工具：获取 scheduler_daemon.py 的路径 */
export function getSchedulerScriptPath(baseDir: string): string {
  return path.join(baseDir, 'python', 'engine', 'scheduler_daemon.py');
}

/** 工具：获取 Python 命令 */
export function getPythonCommand(): string {
  return process.platform === 'win32' ? 'python' : 'python3';
}
