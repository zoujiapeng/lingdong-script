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

/** Agent 进程 */
export let agentProcess: ChildProcess | null = null;
export function setAgentProcess(p: ChildProcess | null) { agentProcess = p; }

/** Agent 运行状态 */
export let agentRunning = false;
export function setAgentRunning(v: boolean) { agentRunning = v; }

/** Agent 进程令牌，防止陈旧事件污染新进程 */
export let agentToken = 0;
export function nextAgentToken(): number { return ++agentToken; }
export function isAgentTokenCurrent(token: number): boolean { return token === agentToken; }
