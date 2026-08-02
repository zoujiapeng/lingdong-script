/**
 * 共享全局状态（替代 main.ts 中的 module-level let 变量）
 */
import { BrowserWindow } from 'electron';
import { ChildProcess } from 'child_process';

/** 主窗口引用 */
export let mainWindow: BrowserWindow | null = null;
export function setMainWindow(w: BrowserWindow | null) { mainWindow = w; }

/** Python 引擎进程 */
export let pythonProcess: ChildProcess | null = null;
export function setPythonProcess(p: ChildProcess | null) { pythonProcess = p; }

/** 引擎运行状态 */
export let engineRunning = false;
export function setEngineRunning(v: boolean) { engineRunning = v; }

/** 最近执行的脚本 JSON */
export let lastScript: string | null = null;
export function setLastScript(s: string | null) { lastScript = s; }

/** 语音服务器进程 */
export let voiceServerProcess: ChildProcess | null = null;
export function setVoiceServerProcess(p: ChildProcess | null) { voiceServerProcess = p; }

/** 语音服务器 WebSocket 端口 */
export let voiceServerPort: number | null = null;
export function setVoiceServerPort(p: number | null) { voiceServerPort = p; }
