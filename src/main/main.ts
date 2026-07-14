import { app, BrowserWindow, globalShortcut, ipcMain, dialog, desktopCapturer, Menu, Notification } from 'electron';
import * as path from 'path';
import * as fs from 'fs';
import * as os from 'os';
import { randomBytes } from 'crypto';
const fsp = fs.promises;
import { spawn, ChildProcess } from 'child_process';
import * as http from 'http';
import * as https from 'https';
import { StringDecoder } from 'string_decoder';
import { SchedulerBridge, getSchedulerScriptPath, getPythonCommand } from './schedulerBridge';
import { spawnPython, spawnPythonOneShot, runPythonCollect, callLLM, startPythonEngine, stopPythonEngine } from './engine';
import { getBaseDir, isDev } from './env';
import { startApiServer, ApiDeps } from './api';
import { resolveMacroPath } from './macroPaths';
import { callFeature, stopFeatureProcess, isFeatureProcessReady } from './featureCallBridge';
import { startWinpilotServer, getWinpilotConnection, getWinpilotUrl, stopWinpilotServer, ensureWinpilotServer, restartWinpilotServer } from './winpilotServer';
import {
  mainWindow, setMainWindow,
  pythonProcess,
  engineRunning, setEngineRunning,
  lastScript, setLastScript,
  agentProcess, setAgentProcess,
  agentRunning, setAgentRunning,
  nextAgentToken, isAgentTokenCurrent,
} from './state';

// 缓存渲染器状态，通过 IPC 定期更新，避免 executeJavaScript 挂起
let _cachedRendererState: any = null;
let _rendererReady = false;
let apiServer: http.Server | null = null;
const LOCAL_CONTROL_TOKEN = process.env.LINGDONG_CONTROL_TOKEN || randomBytes(32).toString('base64url');
const _taskbarNotifiedRuns = new Set<string>();

type TaskbarNotificationKind = 'success' | 'error' | 'info';

function notifyTaskbarAttention(
  title: string,
  body: string,
  kind: TaskbarNotificationKind = 'info',
  runId = '',
): boolean {
  const window = mainWindow;
  if (!window || window.isDestroyed()) return false;
  if (runId && _taskbarNotifiedRuns.has(runId)) return true;
  if (runId) {
    if (_taskbarNotifiedRuns.size >= 200) _taskbarNotifiedRuns.clear();
    _taskbarNotifiedRuns.add(runId);
  }

  let cleared = false;
  const clearAttention = () => {
    if (cleared || window.isDestroyed()) return;
    cleared = true;
    window.removeListener('focus', clearAttention);
    window.flashFrame(false);
    window.setProgressBar(-1);
  };

  try {
    const mode = kind === 'error' ? 'error' : kind === 'success' ? 'normal' : 'paused';
    window.setProgressBar(1, { mode });
    if (!window.isFocused()) window.flashFrame(true);
    window.once('focus', clearAttention);
    setTimeout(clearAttention, window.isFocused() ? 4000 : 30000);

    if (Notification.isSupported()) {
      const notification = new Notification({
        title: title || '灵动脚本',
        body: body || '',
        silent: false,
      });
      notification.on('click', () => {
        if (!window.isVisible()) window.show();
        window.focus();
        clearAttention();
      });
      notification.show();
    }
    console.log(`[taskbar] notified kind=${kind} run_id=${runId || '-'} title=${title}`);
    return true;
  } catch {
    if (runId) _taskbarNotifiedRuns.delete(runId);
    clearAttention();
    return false;
  }
}

// ── 模板上下文（供 AI 提示词参考）──
let _cachedTemplateContext: string = '';

function refreshTemplateContext(): void {
  try {
    const templatesDir = getTemplatesDir();
    if (!fs.existsSync(templatesDir)) { _cachedTemplateContext = ''; return; }
    const files = fs.readdirSync(templatesDir).filter(f => f.endsWith('.dsl'));
    if (files.length === 0) { _cachedTemplateContext = ''; return; }
    let ctx = '\n\n## 可用 DSL 模板\n如果用户描述的流程与以下某个已存在模板匹配，直接使用 CALL 引用该模板，不要重新生成：\n';
    for (const f of files) {
      const filePath = path.join(templatesDir, f);
      try {
        const content = fs.readFileSync(filePath, 'utf-8');
        const lines = content.split('\n');
        let name = f.replace('.dsl', '');
        const aliases: string[] = [];
        const tags: string[] = [];
        let desc = '';
        let stepLines: string[] = [];
        let inSteps = false;
        for (const line of lines) {
          const s = line.trim();
          if (s.startsWith('# name:')) { name = s.replace('# name:', '').trim(); continue; }
          if (s.startsWith('# aliases:')) {
            try { aliases.push(...JSON.parse(s.replace('# aliases:', '').trim())); } catch (e) { console.debug('[templates] parse aliases error:', e); }
            continue;
          }
          if (s.startsWith('# tags:')) {
            try { tags.push(...JSON.parse(s.replace('# tags:', '').trim())); } catch (e) { console.debug('[templates] parse tags error:', e); }
            continue;
          }
          if (s.startsWith('# ---')) { inSteps = true; continue; }
          if (s.startsWith('#') && !inSteps) {
            desc = s.replace('#', '').trim();
            continue;
          }
          if (inSteps && s && !s.startsWith('#')) {
            stepLines.push(s.length > 80 ? s.slice(0, 77) + '...' : s);
          }
        }
        ctx += `### ${name} (${f})\n`;
        if (aliases.length) ctx += `  别名: ${aliases.join(', ')}\n`;
        if (tags.length) ctx += `  标签: ${tags.join(', ')}\n`;
        ctx += `  用途: ${desc}\n`;
        if (stepLines.length > 0) {
          ctx += `  步骤:\n`;
          for (const sl of stepLines.slice(0, 8)) {
            ctx += `    ${sl}\n`;
          }
          if (stepLines.length > 8) ctx += `    ...（共 ${stepLines.length} 行）\n`;
        }
        ctx += `  → 引用: CALL ${name}\n\n`;
      } catch (e) { console.debug('[templates] load error:', e); }
    }
    _cachedTemplateContext = ctx;
  } catch (e) { console.debug('[templates] build context error:', e); _cachedTemplateContext = ''; }
}

// ── 调度上下文（供 AI 提示词参考）──
let _cachedScheduleContext: string = '';

function refreshScheduleContext(): void {
  try {
    const sp = path.join(app.getPath('userData'), 'schedules.json');
    let schedules: any[] = [];
    try { if (fs.existsSync(sp)) schedules = JSON.parse(fs.readFileSync(sp, 'utf-8')); } catch (e) { console.debug('[schedules] parse error:', e); }
    if (!Array.isArray(schedules)) schedules = [];
    if (schedules.length === 0) { _cachedScheduleContext = ''; return; }
    const active = schedules.filter(s => s.enabled);
    const ctxParts: string[] = ['\n\n## 当前定时调度'];

    if (active.length > 0) {
      ctxParts.push(`（${active.length}/${schedules.length} 个启用）\n`);
      for (const s of active) {
        const t = s.triggers?.[0] || {};
        const triggerDesc = t.type === 'cron' ? `Cron: ${t.cron_expr}` :
          t.type === 'interval' ? `每 ${t.interval_seconds}s` :
          t.type === 'startup' ? '启动时' :
          t.type === 'event' ? `事件: ${t.event_type}` : t.type || '';
        const condCount = s.conditions?.rules?.length || 0;
        ctxParts.push(`- **${s.name}** (${s.template_name || s.template_id})`);
        ctxParts.push(`  - 触发: ${triggerDesc}`);
        if (condCount > 0) ctxParts.push(`  - 条件: ${condCount} 个 ${s.conditions?.operator || 'AND'}`);
      }
    } else {
      ctxParts.push('\n暂无定时调度。用户可以创建调度来自动执行模板。\n');
    }

    ctxParts.push('\n### 创建调度的 MCP 工具');
    ctxParts.push('通过 `lobster_scheduler_create` 创建调度，支持 ECA 模型：');
    ctxParts.push('- 触发器: cron / interval / startup / event');
    ctxParts.push('- 守卫条件: window_exists / process_running / is_weekday / is_weekend / day_of_week / time_between / image_exists / text_exists / color_match / scene_match');
    ctxParts.push('  - 条件用 AND/OR 组合，语法: {"operator":"AND","rules":[{"type":"...","params":{...}}]}');

    _cachedScheduleContext = ctxParts.join('\n');
  } catch (e) { console.debug('[schedules] build context error:', e); _cachedScheduleContext = ''; }
}

/** 获取模板目录路径 */
function getTemplatesDir(): string {
  return path.join(getBaseDir(), 'templates');
}

// Refresh on startup
refreshTemplateContext();
refreshScheduleContext();

/** 加载模板 DSL 内容（先查 .dsl，再查 .flow） */
function loadTemplateContent(templateId: string): string | null {
  const templatesDir = getTemplatesDir();
  // Try .dsl
  const dslPath = path.join(templatesDir, `${templateId}.dsl`);
  if (fs.existsSync(dslPath)) {
    return fs.readFileSync(dslPath, 'utf-8');
  }
  // Try .flow (JSON format — extract DSL from it if possible)
  const flowPath = path.join(templatesDir, `${templateId}.flow`);
  if (fs.existsSync(flowPath)) {
    try {
      const flow = JSON.parse(fs.readFileSync(flowPath, 'utf-8'));
      return flow.dsl_content || null;
    } catch { return null; }
  }
  return null;
}

/** 通过 spawnPython 执行 DSL */
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

// Scheduler bridge (Python daemon)
const scheduler = new SchedulerBridge(
  getPythonCommand(),
  getSchedulerScriptPath(getBaseDir()),
  getBaseDir(),
);

/** 检查是否有全屏窗口（演示/全屏视频等） */
function isFullscreenActive(): boolean {
  if (!mainWindow) return false;
  for (const w of BrowserWindow.getAllWindows()) {
    if (w.isFullScreen() && w.webContents?.getURL()) return true;
  }
  return false;
}

/** 检查系统是否空闲（用户离开）— 5分钟内无输入视为空闲 */
let _lastUserActivity = Date.now();
ipcMain.on('user-activity', () => { _lastUserActivity = Date.now(); });
function isUserIdle(minutes = 5): boolean {
  return (Date.now() - _lastUserActivity) > minutes * 60 * 1000;
}

// ── 预触发确认（高风险调度需要用户确认） ─────────────────
scheduler.on('pre_fire', async (event: any) => {
  const { schedule, confirm_id, risk_level } = event;
  if (!schedule || !confirm_id) return;

  try {
    // 全屏/演示模式 → 自动推迟
    if (isFullscreenActive()) {
      console.debug(`[scheduler] 全屏模式，自动推迟调度: ${schedule.name}`);
      scheduler.sendFireResponse(confirm_id, 'snooze');
      return;
    }

    // 用户不在 → 自动取消
    if (isUserIdle(10)) {
      console.debug(`[scheduler] 用户不在，自动取消: ${schedule.name}`);
      scheduler.sendFireResponse(confirm_id, 'cancel');
      return;
    }

    // 构建详情信息
    let detail = `时间: ${event.timestamp || '立即'}`;
    if (schedule.triggers?.length) {
      const t = schedule.triggers[0];
      detail += `\n触发: ${t.cron_expr || `每${t.interval_seconds || '?'}秒`}`;
    }
    if (schedule.notify_message) detail += `\n通知: ${schedule.notify_message}`;
    if (schedule.launch_target) detail += `\n启动: ${schedule.launch_target}`;
    if (schedule.template_id) detail += `\n运行: ${schedule.template_id}`;

    const warningIcon = risk_level === 'critical' ? 'warning' : 'question';
    const buttons = risk_level === 'critical'
      ? ['我确认要执行', '取消']  // critical: 确认按钮在前
      : ['执行', '取消'];         // confirm: 执行在前

    const result = await dialog.showMessageBox({
      type: warningIcon,
      title: `🚨 调度确认 — ${risk_level === 'critical' ? '高风险操作' : '确认执行'}`,
      message: `"${schedule.name}"`,
      detail,
      buttons,
      defaultId: 1,  // 默认选中"取消"（安全）
      cancelId: 1,
    });

    const action = result.response === 0 ? 'proceed' : 'cancel';
    scheduler.sendFireResponse(confirm_id, action);
  } catch (e) {
    console.error('[scheduler] pre_fire handler error:', e);
    scheduler.sendFireResponse(confirm_id, 'cancel');
  }
});

scheduler.on('fire', async (event: any) => {
  const entry = event.schedule;
  if (!entry) return;

  // 上下文守卫: 全屏/空闲 时跳过有副作用的执行
  const hasSideEffect = entry.launch_target || entry.template_id;
  if (hasSideEffect) {
    if (isFullscreenActive()) {
      console.debug(`[scheduler] 全屏模式，跳过副作用操作: ${entry.name}`);
      return;
    }
  }

  // 1. 桌面通知
  if (entry.notify_message) {
    const notif = new Notification({
      title: '灵动脚本 - 定时提醒',
      body: entry.notify_message,
      silent: false,
    });
    notif.show();
  }

  // 2. 启动软件
  if (entry.launch_target) {
    launchTarget(entry.launch_target);
  }

  // 3. 执行模板 DSL
  if (entry.template_id) {
    let dsl = loadTemplateContent(entry.template_id);
    if (!dsl && mainWindow) {
      try {
        const result = await mainWindow.webContents.executeJavaScript(
          `(function(){try{var r=window.__templateRegistry;if(!r)return null;var t=r.getById(${JSON.stringify(entry.template_id)});return t&&t.dsl_content?t.dsl_content:null;}catch(e){return null;}})()`,
          true
        );
        if (typeof result === 'string') dsl = result;
      } catch (e) { console.debug('[scheduler] load template via webContents error:', e); }
    }
    if (!dsl) {
      console.error(`[scheduler-daemon] 模板 '${entry.template_id}' 未找到`);
      return;
    }
    await runDsl(dsl);
  }

  // 更新 last_run
  await updateScheduleLastRun(entry.id);
});

async function updateScheduleLastRun(id: string): Promise<void> {
  const p = path.join(app.getPath('userData'), 'schedules.json');
  try {
    const raw = await fsp.readFile(p, 'utf-8');
    const schedules = JSON.parse(raw);
    const idx = schedules.findIndex((s: any) => s.id === id);
    if (idx !== -1) {
      schedules[idx].last_run = new Date().toISOString();
      await fsp.writeFile(p, JSON.stringify(schedules, null, 2), 'utf-8');
    }
  } catch (e) { console.debug('[scheduler] update last_run error:', e); }
}

function runStartupSchedules(): void {
  const p = path.join(app.getPath('userData'), 'schedules.json');
  let schedules: any[] = [];
  try {
    if (fs.existsSync(p)) schedules = JSON.parse(fs.readFileSync(p, 'utf-8'));
  } catch (e) { console.debug('[scheduler] read schedules error:', e); }
  for (const s of schedules) {
    if (!s.enabled) continue;
    const hasStartup = s.triggers?.some((t: any) => t.type === 'startup');
    if (!hasStartup) continue;
    let dsl = loadTemplateContent(s.template_id);
    if (!dsl && mainWindow) {
      mainWindow.webContents.executeJavaScript(
        `(function(){try{var r=window.__templateRegistry;if(!r)return null;var t=r.getById(${JSON.stringify(s.template_id)});return t&&t.dsl_content?t.dsl_content:null;}catch(e){return null;}})()`,
        true
      ).then((r: any) => {
        if (typeof r === 'string') dsl = r;
        if (dsl) runDsl(dsl).then(() => updateScheduleLastRun(s.id));
      }).catch(() => {});
    } else if (dsl) {
      runDsl(dsl).then(() => updateScheduleLastRun(s.id));
    }
  }
}

/** 启动软件（通过系统关联或直接路径） */
function launchTarget(target: string): void {
  try {
    spawn('cmd.exe', ['/c', 'start', '', target], { windowsHide: true, timeout: 5000 }).unref();
  } catch {
    try {
      spawn(target, [], { windowsHide: true, detached: true, stdio: 'ignore' }).unref();
    } catch (e) {
      console.error(`[scheduler] 启动失败: ${target}`, e);
    }
  }
}

function createWindow() {
  const startMinimized = process.argv.includes('--minimized');

  setMainWindow(new BrowserWindow({
    width: 900,
    height: 580,
    minWidth: 800,
    minHeight: 500,
    center: true,
    title: '灵动脚本',
    backgroundColor: '#eef2f8',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
    show: false,
  }));

  mainWindow!.webContents.session.setPermissionRequestHandler(
    (_wc, permission, callback) => {
      callback(permission === 'media');
    }
  );

  if (isDev) {
    mainWindow!.loadURL('http://localhost:5200');
  } else {
    mainWindow!.loadFile(path.join(__dirname, '../renderer/index.html'));
  }

  mainWindow!.once('ready-to-show', () => {
    mainWindow?.show();
    if (startMinimized) {
      mainWindow?.minimize();
    }
    _rendererReady = true;
    // Only non-secret model metadata is exposed to the renderer. API keys stay
    // in the main process / managed WinPilot configuration.
    const envKey = process.env.ANTHROPIC_API_KEY || '';
    const envBase = process.env.LLM_API_BASE || 'https://api.deepseek.com/v1';
    const envModel = process.env.LLM_MODEL || 'deepseek-chat';
    const envMultimodal = process.env.LLM_MULTIMODAL === 'true';
    mainWindow?.webContents.executeJavaScript(`
      (function() {
        window.__LLM_API_BASE__ = ${JSON.stringify(envBase)};
        window.__LLM_MODEL__ = ${JSON.stringify(envModel)};
        window.__LLM_MULTIMODAL__ = ${JSON.stringify(envMultimodal)};
      })();
    `);
    // 自动启动 Agent（如果设置了 AGENT_GOAL 环境变量）
    const autoGoal = process.env.AGENT_GOAL || '';
    if (autoGoal) {
      setTimeout(() => {
        startAgentProcess(autoGoal, {
          api_key: envKey,
          api_base: envBase,
          model: envModel,
          multimodal: envMultimodal ? 'true' : 'false',
        });
      }, 2000);
    }
    // 执行 startup 类型的调度
    runStartupSchedules();
  });

  mainWindow!.on('closed', () => {
    setMainWindow(null);
  });

  /** 开发模式下 Vite 断开时自动重载，防止白屏 */
  if (isDev) {
    mainWindow!.webContents.on('did-fail-load', () => {
      setTimeout(() => {
        if (mainWindow && !mainWindow.webContents.isLoading()) {
          mainWindow.webContents.reload();
        }
      }, 1000);
    });
  }
}

function buildAppMenu() {
  const isMac = process.platform === 'darwin';
  const template: any[] = [
    ...(isMac ? [{ role: 'appMenu', label: '灵动脚本' }] : []),
    {
      label: '文件',
      submenu: [
        { label: '新建脚本', accelerator: 'CmdOrCtrl+N', click: () => mainWindow?.webContents.send('menu-action', 'new') },
        { label: '打开脚本', accelerator: 'CmdOrCtrl+O', click: () => mainWindow?.webContents.send('menu-action', 'open') },
        { label: '保存脚本', accelerator: 'CmdOrCtrl+S', click: () => mainWindow?.webContents.send('menu-action', 'save') },
        { type: 'separator' },
        { role: 'quit', label: '退出' },
      ],
    },
    {
      label: '编辑',
      submenu: [
        { role: 'undo', label: '撤销' },
        { role: 'redo', label: '重做' },
        { type: 'separator' },
        { role: 'cut', label: '剪切' },
        { role: 'copy', label: '复制' },
        { role: 'paste', label: '粘贴' },
        { type: 'separator' },
        { role: 'selectAll', label: '全选' },
      ],
    },
    {
      label: '视图',
      submenu: [
        { role: 'reload', label: '重新加载' },
        { role: 'forceReload', label: '强制重新加载' },
        { role: 'toggleDevTools', label: '开发者工具' },
        { type: 'separator' },
        { role: 'resetZoom', label: '重置缩放' },
        { role: 'zoomIn', label: '放大' },
        { role: 'zoomOut', label: '缩小' },
        { type: 'separator' },
        { role: 'togglefullscreen', label: '全屏' },
      ],
    },
    {
      label: '窗口',
      submenu: [
        { role: 'minimize', label: '最小化' },
        { role: 'close', label: '关闭窗口' },
        ...(isMac ? [
          { type: 'separator' },
          { role: 'front', label: '全部置于顶部' },
        ] : []),
      ],
    },
    {
      label: '帮助',
      submenu: [
        { label: '关于灵动脚本', click: () => {
          const { BrowserWindow } = require("electron");
          const w = new BrowserWindow({
            width: 440, height: 640, parent: mainWindow, modal: true,
            resizable: false, frame: true, title: "关于灵动脚本",
            webPreferences: { nodeIntegration: false, contextIsolation: true },
          });
          const html = `<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><style>
body{margin:0;padding:32px 28px;font-family:-apple-system,Segoe UI,sans-serif;background:#202020;color:#ccc;display:flex;flex-direction:column;align-items:center;text-align:center;overflow:hidden}
h1{font-size:22px;color:#fff;margin:0 0 4px;font-weight:600}
.sub{font-size:13px;color:#888;margin-bottom:20px}
.section{font-size:12px;color:#999;line-height:1.8;margin-bottom:14px;width:100%}
.section-title{color:#4f8cff;font-weight:500;margin-bottom:6px}
.tags{display:flex;flex-wrap:wrap;justify-content:center;gap:4px}
.line{width:60px;height:1px;background:#444;margin:14px 0}
.tag{display:inline-block;padding:3px 10px;border-radius:10px;font-size:11px;background:#2d2d2d;color:#aaa;white-space:nowrap}
</style></head>
<body>
<h1>灵动脚本</h1>
<div class="sub">v1.0.0</div>
<div class="section"><div class="section-title">开发者</div>邹嘉鹏</div>
<div class="line"></div>
<div class="section"><div class="section-title">技术栈</div>Electron + React + Python</div>
<div class="line"></div>
<div class="section"><div class="section-title">核心能力</div>
<div class="tags">
<span class="tag">流程图编排</span>
<span class="tag">统一 Agent + RuntimeProfile</span>
<span class="tag">低延迟 GUI Controller</span>
<span class="tag">OCR / 视觉定位</span>
<span class="tag">@feature 注册表</span>
<span class="tag">ARP / Automation IR</span>
<span class="tag">宏录制 (支持视觉)</span>
<span class="tag">DSL 引擎 (~130 关键字)</span>
<span class="tag">MCP / HTTP API / CLI</span>
<span class="tag">摄像头监控</span>
<span class="tag">RuntimeJob / 证据门</span>
<span class="tag">条件 / 循环 / 分支</span>
<span class="tag">流程模板库</span>
<span class="tag">触发器 / 定时调度</span>
<span class="tag">在线 / 离线双模式</span>
<span class="tag">SoM 标号系统</span>
<span class="tag">经验复用学习</span>
</div></div>
<div class="line"></div>
<div class="section" style="font-size:11px;color:#666">Lingdong Script © 2026</div>
</body></html>`;
          w.loadURL("data:text/html;charset=utf-8," + encodeURIComponent(html));
          w.setMenu(null);
        }},
      ],
    },
  ];

  const menu = Menu.buildFromTemplate(template);
  Menu.setApplicationMenu(menu);
}


interface IncompleteParam {
  node_id: string;
  node_type: string;
  node_label: string;
  param: string;
  param_label: string;
  hint: string;
  tool: 'region_picker' | 'screenshot' | 'file_picker' | 'text_input' | 'coordinate_picker' | 'wheel_select' | 'none';
}

function findIncompleteParams(nodes: any[]): IncompleteParam[] {
  const results: IncompleteParam[] = [];
  for (const node of nodes) {
    const p = node.params || {};
    switch (node.type) {
      case 'image_click':
        if (!p.targets || p.targets.length === 0 || (typeof p.targets === 'string' && p.targets === '') || (Array.isArray(p.targets) && p.targets.every((t: any) => !t?.path)))
          results.push({ node_id: node.id, node_type: node.type, node_label: node.label || node.type, param: 'targets', param_label: '目标图片', hint: '请截取屏幕区域来选择目标图片(会弹出全屏截图工具,拖拽选择包含目标图片的区域)', tool: 'screenshot' });
        break;
      case 'click_text':
        if (!p.text) results.push({ node_id: node.id, node_type: node.type, node_label: node.label || node.type, param: 'text', param_label: '识别文字', hint: '请输入屏幕上要识别的文字内容,程序会自动找到匹配的文字并点击(例如:确定、提交、下一步、关闭等)', tool: 'text_input' });
        break;
      case 'input_text':
        if (!p.text) results.push({ node_id: node.id, node_type: node.type, node_label: node.label || node.type, param: 'text', param_label: '输入文字', hint: '请输入要在输入框中填写的文本内容(例如用户名、密码、搜索关键词、文件路径等)', tool: 'text_input' });
        break;
      case 'click':
        if ((!p.x || p.x === 0) && (!p.y || p.y === 0)) results.push({ node_id: node.id, node_type: node.type, node_label: node.label || node.type, param: 'x', param_label: '点击坐标', hint: '请输入点击的坐标值(x,y)或使用坐标拾取工具在屏幕上选择点击位置(会弹出选框,选择后自动取选框中心点)', tool: 'coordinate_picker' });
        break;
      case 'color_check':
        if (!p.expected_color) results.push({ node_id: node.id, node_type: node.type, node_label: node.label || node.type, param: 'expected_color', param_label: '检测颜色', hint: '请输入要检测的颜色值(支持格式:#FF0000、rgb(255,0,0)、或颜色名称如red,green,blue等)', tool: 'text_input' });
        break;
      case 'progress_wait':
        if (!p.region || (!p.region.x && p.region.w === undefined)) results.push({ node_id: node.id, node_type: node.type, node_label: node.label || node.type, param: 'region', param_label: '进度条区域', hint: '请在屏幕上框选进度条所在的矩形区域(会弹出全屏选框工具,拖拽选择进度条范围,程序会监控该区域的颜色填充变化来检测进度完成)', tool: 'region_picker' });
        break;
      case 'region_wait':
        if (!p.region || (!p.region.x && p.region.w === undefined)) results.push({ node_id: node.id, node_type: node.type, node_label: node.label || node.type, param: 'region', param_label: '监控区域', hint: '请在屏幕上框选要持续监控的矩形区域(会弹出全屏选框工具,拖拽选择监控范围,程序会检测该区域的画面变化或稳定性)', tool: 'region_picker' });
        break;
      case 'scene_condition':
        if (!p.scene_name) results.push({ node_id: node.id, node_type: node.type, node_label: node.label || node.type, param: 'scene_name', param_label: '场景名称', hint: '请输入要识别的场景名称,程序会通过OCR或图像匹配判断当前屏幕是否属于该场景(例如:登录界面、主界面、设置页面等)', tool: 'text_input' });
        break;
      case 'tool_call':
        if (!p.feature_name) results.push({ node_id: node.id, node_type: node.type, node_label: node.label || node.type, param: 'feature_name', param_label: '工具名称', hint: '请从滚轮列表中选择要调用的功能工具(转动滚轮找到目标选项,按下确认即可)', tool: 'wheel_select' });
        break;
      case 'popup_close':
        if (!p.strategies || p.strategies.length === 0) results.push({ node_id: node.id, node_type: node.type, node_label: node.label || node.type, param: 'strategies', param_label: '关闭策略', hint: '请从滚轮列表中选择弹窗关闭策略(例如:点击确定、点击取消、按Esc、点击关闭按钮、按回车等)', tool: 'wheel_select' });
        break;
      case 'wait':
        if (p.wait_type === 'image' && (!p.template_path && !p.template_base64)) results.push({ node_id: node.id, node_type: node.type, node_label: node.label || node.type, param: 'template_path', param_label: '等待图片', hint: '请选择要等待在屏幕上出现的图片文件,程序会持续检测直到图片出现或超时。可以使用截图工具截取或文件选择器选取目标图片', tool: 'file_picker' });
        break;
      case 'condition':
        if (p.condition_type === 'image_exists' && (!p.template_path && !p.template_base64)) results.push({ node_id: node.id, node_type: node.type, node_label: node.label || node.type, param: 'template_path', param_label: '条件图片', hint: '请选择用于判断条件的参考图片,程序会检测该图片是否出现在当前屏幕上。可以使用截图工具截取或文件选择器选取参考图片', tool: 'file_picker' });
        break;
    }
  }
  return results;
}

ipcMain.on('state:response', (_, state) => {
  _cachedRendererState = state;
  _rendererReady = true;
});

ipcMain.handle('dialog:openFile', async (_, filters?: { name: string; extensions: string[] }[]) => {
  const result = await dialog.showOpenDialog(mainWindow!, {
    properties: ['openFile'],
    filters: filters || [{ name: '脚本文件', extensions: ['json'] }],
  });
  return result;
});

ipcMain.handle('dialog:saveFile', async (_, defaultName?: string) => {
  const result = await dialog.showSaveDialog(mainWindow!, {
    defaultPath: defaultName,
    filters: [{ name: '脚本文件', extensions: ['json'] }],
  });
  return result;
});

ipcMain.handle('file:read', async (_, filePath: string) => {
  try {
    return { content: fs.readFileSync(filePath, 'utf-8') };
  } catch (e: any) {
    return { error: e.message };
  }
});

ipcMain.handle('file:write', async (_, filePath: string, content: string) => {
  try {
    fs.writeFileSync(filePath, content, 'utf-8');
    return { success: true };
  } catch (e: any) {
    return { error: e.message };
  }
});

ipcMain.handle('engine:start', (_, scriptJson: string) => {
  startPythonEngine(scriptJson);
  return { success: true };
});

ipcMain.handle('engine:stop', () => {
  stopPythonEngine();
  return { success: true };
});

function sendToEngine(message: any) {
  if (pythonProcess && pythonProcess.stdin) {
    pythonProcess.stdin.write(JSON.stringify(message) + '\n');
  }
}

ipcMain.handle('engine:pause', () => {
  sendToEngine({ type: 'pause' });
  return { success: true };
});

ipcMain.handle('engine:resume', () => {
  sendToEngine({ type: 'resume' });
  return { success: true };
});

ipcMain.handle('engine:status', () => {
  sendToEngine({ type: 'status' });
  return { success: true };
});

// ── Agent IPC ──────────────────────────────────────────────────

function sendToAgent(message: any) {
  if (agentProcess && agentProcess.stdin) {
    agentProcess.stdin.write(Buffer.from(JSON.stringify(message) + '\n', 'utf8'));
  }
}

/** Line buffer for agent stdout */
let _agentLineBuffer = '';
/** UTF-8 decoder for agent stdout (handles multi-byte chars split across chunks) */
let _agentDecoder: StringDecoder;

function startAgentProcess(goal: string, config?: Record<string, any>, supervise: boolean = true, steps?: any[]) {
  // Stop existing agent if running — kill immediately, then wait before spawning new one
  if (agentProcess && !agentProcess.killed) {
    sendToAgent({ type: 'stop' });
    agentProcess.kill();
    setAgentProcess(null);
    setAgentRunning(false);
  }

  const token = nextAgentToken();
  const agentDir = path.join(getBaseDir(), 'python');

  const proc = spawnPython(
    ['-u', '-m', 'agent'],
    { cwd: agentDir, stdio: ['pipe', 'pipe', 'pipe'] }
  );
  setAgentProcess(proc);
  setAgentRunning(true);

  // Reset buffers for new process
  _agentLineBuffer = '';
  _agentDecoder = new StringDecoder('utf8');

  proc.stdout!.on('data', (data: Buffer) => {
    _agentLineBuffer += _agentDecoder.write(data);
    const lines = _agentLineBuffer.split('\n');
    _agentLineBuffer = lines.pop() || ''; // keep incomplete trailing line
    for (const line of lines) {
      if (!line) continue;
      try {
        const msg = JSON.parse(line);
        console.log('[agent]', msg.type, JSON.stringify(msg).slice(0, 200));
        if (msg.type === 'agent_done') {
          setAgentRunning(false);
          const status = String(msg.status || '').toLowerCase();
          const success = status === 'done' || status === 'completed' || status === 'reply';
          const stopped = status === 'stopped' || status === 'canceled' || status === 'cancelled';
          notifyTaskbarAttention(
            success ? '任务完成' : stopped ? '任务已停止' : '任务失败',
            String(msg.result || msg.error || msg.task || msg.run_id || '').slice(0, 220),
            success ? 'success' : stopped ? 'info' : 'error',
            String(msg.run_id || ''),
          );
        } else if (msg.type === 'agent_error') {
          notifyTaskbarAttention(
            '任务失败',
            String(msg.error || msg.message || msg.run_id || '').slice(0, 220),
            'error',
            String(msg.run_id || ''),
          );
        }
        mainWindow?.webContents.send('agent-message', msg);
      } catch {
        mainWindow?.webContents.send('agent-log', { level: 'info', message: line });
      }
    }
  });

  // Re-create decoder for stderr too (used in agent-start injection)
  let _stderrDecoder = new StringDecoder('utf8');
  proc.stderr!.on('data', (data: Buffer) => {
    mainWindow?.webContents.send('agent-log', { level: 'error', message: _stderrDecoder.write(data) });
  });

  // Add a safety timeout: kill if agent runs longer than 30 minutes.
  // WinPilot multi-step runs with OCR/VLM can take several minutes; the
  // previous 300s cap would sever long-but-healthy runs mid-task.
  const AGENT_TIMEOUT_MS = 30 * 60 * 1000;
  const agentTimeout = setTimeout(() => {
    if (!isAgentTokenCurrent(token)) return;
    console.warn('[agent] Safety timeout — killing agent after 30min');
    mainWindow?.webContents.send('agent-log', { level: 'error', message: 'Agent 执行超时 (30 分钟)，已自动终止' });
    proc.kill();
    setAgentProcess(null);
    setAgentRunning(false);
  }, AGENT_TIMEOUT_MS);

  proc.on('exit', (code) => {
    clearTimeout(agentTimeout);
    if (!isAgentTokenCurrent(token)) return;  // stale event from previous process
    console.log('[agent] Process exited, code=' + code);
    setAgentProcess(null);
    setAgentRunning(false);
  });

  // Send start command (use a short delay to ensure Python process is listening)
  setTimeout(() => {
    sendToAgent({ type: 'start', goal, config: config || {}, supervise, steps });
  }, 300);
}

ipcMain.handle('agent:start', (_, goal: string, config?: Record<string, any>, supervise?: boolean, steps?: any[]) => {
  startAgentProcess(goal, config, supervise ?? true, steps);
  return { success: true };
});

ipcMain.handle('agent:stop', () => {
  sendToAgent({ type: 'stop' });
  setAgentRunning(false);
  setTimeout(() => {
    if (agentProcess && !agentProcess.killed) {
      agentProcess.kill();
      setAgentProcess(null);
    }
  }, 3000);
  return { success: true };
});

ipcMain.handle('agent:guidance', (_, text: string) => {
  sendToAgent({ type: 'guidance', text });
  return { success: true };
});

// WinPilot control-plane connection for the native renderer (null until ready).
ipcMain.handle('winpilot:url', () => getWinpilotUrl());
ipcMain.handle('winpilot:connection', () => getWinpilotConnection());

// Trigger a (re)start if the server isn't up, and report status (url/starting/
// error). The Agent panel polls this so a failed startup can recover from UI.
ipcMain.handle('winpilot:ensure', () => ensureWinpilotServer());
ipcMain.handle('winpilot:restart', () => restartWinpilotServer());

ipcMain.handle('agent:chat', (_, text: string) => {
  sendToAgent({ type: 'chat', text });
  return { success: true };
});

ipcMain.handle('agent:status', () => {
  sendToAgent({ type: 'status' });
  return { success: true };
});

ipcMain.handle('ai:generate',(_, params: { apiKey: string; messages: { role: string; content: string }[]; apiBase?: string; model?: string }) => {
  return new Promise((resolve) => {
    const apiBase = (params.apiBase || process.env.LLM_API_BASE || 'https://api.deepseek.com/v1').replace(/\/+$/, '');
    const model = params.model || process.env.LLM_MODEL || 'deepseek-chat';
    const body = JSON.stringify({
      model,
      messages: params.messages,
      response_format: { type: 'json_object' },
      temperature: 0.1,
      max_tokens: 2048,
    });
    const u = new URL(`${apiBase}/chat/completions`);
    const req = https.request({
      hostname: u.hostname,
      path: u.pathname,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${params.apiKey}`,
        'Content-Length': Buffer.byteLength(body),
      },
      timeout: 30000,
    }, (res) => {
      const _http2Buf: Buffer[] = [];
      res.on('data', (chunk) => { _http2Buf.push(chunk) });
      res.on('end', () => {
        const d = Buffer.concat(_http2Buf).toString('utf8');
        try {
          const parsed = JSON.parse(d);
          if (res.statusCode && res.statusCode >= 400) {
            resolve({ error: `API 错误 (${res.statusCode}): ${d}` });
          } else {
            resolve(parsed);
          }
        } catch {
          resolve({ error: `响应解析失败: ${d.slice(0, 200)}` });
        }
      });
    });
    req.on('error', (e) => resolve({ error: `网络错误: ${e.message}` }));
    req.on('timeout', () => { req.destroy(); resolve({ error: '请求超时' }); });
    req.write(body);
    req.end();
  });
});

/** Capture full-screen screenshot with region selection overlay */
async function _captureScreenshot(): Promise<any> {
  if (!mainWindow) return { error: 'no window' };
  mainWindow.minimize();
  await new Promise((r) => setTimeout(r, 500));

  try {
    const { screen } = require('electron');
    const primaryDisplay = screen.getPrimaryDisplay();
    const { width: sw, height: sh } = primaryDisplay.size;
    const dpr = primaryDisplay.scaleFactor;

    const sources = await desktopCapturer.getSources({
      types: ['screen'],
      thumbnailSize: { width: sw, height: sh },
    });
    if (sources.length === 0) { mainWindow.restore(); return { error: 'no sources' }; }

    const fullDataURL = sources[0].thumbnail.toDataURL();

    // Create selection overlay window (needs nodeIntegration for data URL page)
    const selWin = new BrowserWindow({
      width: sw, height: sh,
      frame: false, resizable: false, show: false,
      webPreferences: { contextIsolation: false, nodeIntegration: true },
    });

    const selHTML = `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:100%;height:100%;overflow:hidden;cursor:crosshair;user-select:none}
#canvas{display:block;width:100%;height:100%}
#bar{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);
  background:rgba(0,0,0,0.75);color:#fff;padding:8px 18px;border-radius:8px;
  font:14px system-ui;display:flex;align-items:center;gap:14px;pointer-events:none;z-index:10}
#bar kbd{background:rgba(255,255,255,0.15);padding:2px 7px;border-radius:4px;font:11px system-ui}
</style>
</head>
<body>
<canvas id="c"></canvas>
<div id="bar"><span>拖拽选择截图区域</span><kbd>Enter</kbd> 确认 <kbd>Esc</kbd> 取消</div>
<script>
const {ipcRenderer}=require("electron");
const c=document.getElementById("c"),ctx=c.getContext("2d");
const img=new Image();
img.onload=()=>{c.width=img.width;c.height=img.height;ctx.drawImage(img,0,0);init()};
img.src='${fullDataURL}';
let sx,sy,ex,ey,drg=false;
function init(){
  c.onmousedown=e=>{sx=e.offsetX;sy=e.offsetY;drg=true};
  c.onmousemove=e=>{if(!drg)return;ex=e.offsetX;ey=e.offsetY;draw()};
  c.onmouseup=()=>{drg=false};
  c.ondblclick=confirm;
  onkeydown=e=>{if(e.key==="Enter")confirm();if(e.key==="Escape")ipcRenderer.send("ss:cancel")};
}
function draw(){
  ctx.drawImage(img,0,0);
  const x=Math.min(sx,ex),y=Math.min(sy,ey),w=Math.abs(ex-sx),h=Math.abs(ey-sy);
  ctx.fillStyle="rgba(0,0,0,0.5)";ctx.fillRect(0,0,c.width,y);
  ctx.fillRect(0,y,x,h);ctx.fillRect(x+w,y,c.width-x-w,h);ctx.fillRect(0,y+h,c.width,c.height-y-h);
  ctx.strokeStyle="#3b82f6";ctx.lineWidth=3;ctx.setLineDash([6,4]);
  ctx.strokeRect(x,y,w,h);ctx.setLineDash([]);
  ctx.fillStyle="rgba(59,130,246,0.9)";ctx.fillRect(x,y-24,Math.min(w+10,100),20);
  ctx.fillStyle="#fff";ctx.font="12px system-ui";ctx.textAlign="center";
  ctx.fillText(w+"×"+h,x+Math.min(w,95)/2,y-8);
}
function confirm(){
  if(sx===void 0||ex===void 0)return;
  const x=Math.round(Math.min(sx,ex)),y=Math.round(Math.min(sy,ey)),w=Math.round(Math.abs(ex-sx)),h=Math.round(Math.abs(ey-sy));
  if(w<5||h<5)return;
  ipcRenderer.send("ss:done",{x,y,w,h});
}
<\/script>
</body>
</html>`;

    selWin.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(selHTML));
    selWin.once('ready-to-show', () => selWin.show());

    // Wait for selection result, then crop
    const result = await new Promise<string | null>((resolve) => {
      const cleanup = () => {
        ipcMain.removeAllListeners('ss:done');
        ipcMain.removeAllListeners('ss:cancel');
        ipcMain.removeAllListeners('ss:cropped');
      };
      const timeout = setTimeout(() => { cleanup(); selWin.close(); mainWindow!.restore(); resolve(null); }, 60000);
      ipcMain.once('ss:done', (_: any, rect: { x: number; y: number; w: number; h: number }) => {
        const { x, y, w, h } = rect;
        const cropWin = new BrowserWindow({
          show: false,
          webPreferences: { contextIsolation: false, nodeIntegration: true },
        });
        const cropHTML = `<script>const{ipcRenderer}=require("electron");const i=new Image();i.onload=()=>{const c=document.createElement("canvas");c.width=${w};c.height=${h};c.getContext("2d").drawImage(i,${x},${y},${w},${h},0,0,${w},${h});ipcRenderer.send("ss:cropped",c.toDataURL("image/png"))};i.src='${fullDataURL}';<\/script>`;
        cropWin.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(cropHTML));
        ipcMain.once('ss:cropped', (_2: any, dataURL: string) => { cropWin.close(); clearTimeout(timeout); cleanup(); resolve(dataURL); });
        cropWin.once('closed', () => { clearTimeout(timeout); cleanup(); resolve(null); });
      });
      ipcMain.once('ss:cancel', () => { clearTimeout(timeout); cleanup(); resolve(null); });
      selWin.once('closed', () => { clearTimeout(timeout); cleanup(); resolve(null); });
    });

    selWin.close();
    mainWindow.restore();
    mainWindow.focus();
    if (result) return { dataURL: result };
    return { error: 'cancelled' };

  } catch (e: any) {
    mainWindow.restore();
    return { error: e.message };
  }
}

/**
 * 将 base64 图片保存到 templates/ 目录，供 Python 引擎执行时直接使用。
 */
ipcMain.handle('screenshot:saveBase64Image', async (_, filename: string, dataURL: string) => {
  try {
    const base64Data = dataURL.replace(/^data:image\/\w+;base64,/, '');
    const buffer = Buffer.from(base64Data, 'base64');
    const templateDir = path.join(getBaseDir(), 'templates');
    fs.mkdirSync(templateDir, { recursive: true });
    const filePath = path.join(templateDir, path.basename(filename));
    fs.writeFileSync(filePath, buffer);
    return { success: true, path: filePath };
  } catch (e: any) {
    return { success: false, error: e.message };
  }
});

ipcMain.handle('screenshot:capture', async () => _captureScreenshot());

async function _selectRegion(): Promise<any> {
  if (!mainWindow) return { error: 'no window' };
  mainWindow.minimize();
  await new Promise((r) => setTimeout(r, 500));

  try {
    const { screen } = require('electron');
    const primaryDisplay = screen.getPrimaryDisplay();
    const { width: sw, height: sh } = primaryDisplay.size;

    const sources = await desktopCapturer.getSources({
      types: ['screen'],
      thumbnailSize: { width: sw, height: sh },
    });
    if (sources.length === 0) { mainWindow.restore(); return { error: 'no sources' }; }

    const fullDataURL = sources[0].thumbnail.toDataURL();

    const selWin = new BrowserWindow({
      width: sw, height: sh,
      frame: false, resizable: false, show: false,
      webPreferences: { contextIsolation: false, nodeIntegration: true },
    });

    const selHTML = `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:100%;height:100%;overflow:hidden;cursor:crosshair;user-select:none}
#canvas{display:block;width:100%;height:100%}
#bar{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);
  background:rgba(0,0,0,0.75);color:#fff;padding:8px 18px;border-radius:8px;
  font:14px system-ui;display:flex;align-items:center;gap:14px;pointer-events:none;z-index:10}
#bar kbd{background:rgba(255,255,255,0.15);padding:2px 7px;border-radius:4px;font:11px system-ui}
</style>
</head>
<body>
<canvas id="c"></canvas>
<div id="bar"><span>拖拽选择监控区域</span><kbd>Enter</kbd> 确认 <kbd>Esc</kbd> 取消</div>
<script>
const{ipcRenderer}=require("electron");
const c=document.getElementById("c"),ctx=c.getContext("2d");
const img=new Image();
img.onload=()=>{c.width=img.width;c.height=img.height;ctx.drawImage(img,0,0);init()};
img.src='${fullDataURL}';
let sx,sy,ex,ey,drg=false;
function init(){
  c.onmousedown=e=>{sx=e.offsetX;sy=e.offsetY;drg=true};
  c.onmousemove=e=>{if(!drg)return;ex=e.offsetX;ey=e.offsetY;draw()};
  c.onmouseup=()=>{drg=false};
  c.ondblclick=confirm;
  onkeydown=e=>{if(e.key==="Enter")confirm();if(e.key==="Escape")ipcRenderer.send("ss:cancel")};
}
function draw(){
  ctx.drawImage(img,0,0);
  const x=Math.min(sx,ex),y=Math.min(sy,ey),w=Math.abs(ex-sx),h=Math.abs(ey-sy);
  ctx.fillStyle="rgba(0,0,0,0.5)";ctx.fillRect(0,0,c.width,y);
  ctx.fillRect(0,y,x,h);ctx.fillRect(x+w,y,c.width-x-w,h);ctx.fillRect(0,y+h,c.width,c.height-y-h);
  ctx.strokeStyle="#3b82f6";ctx.lineWidth=3;ctx.setLineDash([6,4]);
  ctx.strokeRect(x,y,w,h);ctx.setLineDash([]);
  ctx.fillStyle="rgba(59,130,246,0.9)";ctx.fillRect(x,y-24,Math.min(w+10,100),20);
  ctx.fillStyle="#fff";ctx.font="12px system-ui";ctx.textAlign="center";
  ctx.fillText(w+"×"+h,x+Math.min(w,95)/2,y-8);
}
function confirm(){
  if(sx===void 0||ex===void 0)return;
  const x=Math.round(Math.min(sx,ex)),y=Math.round(Math.min(sy,ey)),w=Math.round(Math.abs(ex-sx)),h=Math.round(Math.abs(ey-sy));
  if(w<5||h<5)return;
  ipcRenderer.send("ss:done",{x,y,w,h});
}
<\/script>
</body>
</html>`;

    selWin.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(selHTML));
    selWin.once('ready-to-show', () => selWin.show());

    // Wait for selection result, then crop the region to return both coords + image
    const result = await new Promise<{ x: number; y: number; w: number; h: number; dataURL: string } | null>((resolve) => {
      const cleanup = () => { ipcMain.removeAllListeners('ss:done'); ipcMain.removeAllListeners('ss:cancel'); ipcMain.removeAllListeners('ss:cropped'); };
      const timeout = setTimeout(() => { cleanup(); selWin.close(); mainWindow!.restore(); resolve(null); }, 60000);
      ipcMain.once('ss:done', (_: any, rect: { x: number; y: number; w: number; h: number }) => {
        const { x, y, w, h } = rect;
        // Crop the selected region from the full screenshot (same technique as _captureScreenshot)
        const cropWin = new BrowserWindow({
          show: false,
          webPreferences: { contextIsolation: false, nodeIntegration: true },
        });
        const cropHTML = `<script>const{ipcRenderer}=require("electron");const i=new Image();i.onload=()=>{const c=document.createElement("canvas");c.width=${w};c.height=${h};c.getContext("2d").drawImage(i,${x},${y},${w},${h},0,0,${w},${h});ipcRenderer.send("ss:cropped",c.toDataURL("image/png"))};i.src='${fullDataURL}';<\/script>`;
        cropWin.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(cropHTML));
        ipcMain.once('ss:cropped', (_2: any, dataURL: string) => {
          cropWin.close(); clearTimeout(timeout); cleanup();
          resolve({ x, y, w, h, dataURL });
        });
        cropWin.once('closed', () => { clearTimeout(timeout); cleanup(); resolve(null); });
      });
      ipcMain.once('ss:cancel', () => { clearTimeout(timeout); cleanup(); resolve(null); });
      selWin.once('closed', () => { clearTimeout(timeout); cleanup(); resolve(null); });
    });

    selWin.close();
    mainWindow.restore();
    mainWindow.focus();
    if (result) return { x: result.x, y: result.y, w: result.w, h: result.h, dataURL: result.dataURL };
    return { error: 'cancelled' };

  } catch (e: any) {
    mainWindow.restore();
    return { error: e.message };
  }
}

ipcMain.handle('screenshot:selectRegion', async () => _selectRegion());

// --- Interaction Tools IPC ---
async function _wheelSelect(params: { options: string[]; values?: string[]; title?: string; message?: string; default?: number }): Promise<any> {
  const scriptPath = path.join(getBaseDir(), 'python', 'interaction', 'wheel_selector.py');
  const proc = await spawnPythonOneShot([scriptPath, '--ipc'], {
    cwd: getBaseDir(),
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  const _out: Buffer[] = [];
  const _err: Buffer[] = [];
  proc.stdout!.on('data', (chunk: Buffer) => _out.push(chunk));
  proc.stderr!.on('data', (chunk: Buffer) => _err.push(chunk));
  proc.stdin!.write(JSON.stringify(params) + '\n');
  proc.stdin!.end();
  return new Promise((resolve) => {
    proc.on('close', (code) => {
          const stdout = Buffer.concat(_out).toString('utf8');
          const stderr = Buffer.concat(_err).toString('utf8');
      if (stdout.trim()) {
        try { resolve(JSON.parse(stdout)); }
        catch { resolve({ error: 'JSON parse error', raw: stdout }); }
      } else {
        resolve({ error: stderr.trim() || 'wheel select failed' });
      }
    });
    proc.on('error', (err) => resolve({ error: err.message }));
  });
}

ipcMain.handle('interaction:wheelSelect', async (_, params) => _wheelSelect(params));

async function _ocrRegion(region: { x: number; y: number; w: number; h: number }): Promise<any> {
  const scriptPath = path.join(getBaseDir(), 'tools', 'ocr_region.py');
  const proc = await spawnPythonOneShot([scriptPath], {
    cwd: getBaseDir(),
    stdio: ['pipe', 'pipe', 'pipe'],
  });

  const _out: Buffer[] = [];
  const _err: Buffer[] = [];
  proc.stdout!.on('data', (chunk: Buffer) => _out.push(chunk));
  proc.stderr!.on('data', (chunk: Buffer) => _err.push(chunk));
  proc.stdin!.write(JSON.stringify(region) + '\n');
  proc.stdin!.end();
  return new Promise((resolve) => {
    proc.on('close', () => {
          const stdout = Buffer.concat(_out).toString('utf8');
          const stderr = Buffer.concat(_err).toString('utf8');
      if (stdout.trim()) {
        try { resolve(JSON.parse(stdout)); }
        catch { resolve([]); }
      } else {
        resolve([]);
      }
    });
    proc.on('error', () => resolve([]));
  });
}

ipcMain.handle('ocr:recognizeRegion', async (_, region) => _ocrRegion(region));

// --- DSL Conversion IPC ---
ipcMain.handle('dsl:toFlow', async (_, dslText: string) => {
  const scriptPath = path.join(getBaseDir(), 'tools', 'dsl_to_flow.py');
  const proc = await spawnPythonOneShot([scriptPath], {
    cwd: getBaseDir(),
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  const _out: Buffer[] = [];
  const _err: Buffer[] = [];
  proc.stdout!.on('data', (chunk: Buffer) => _out.push(chunk));
  proc.stderr!.on('data', (chunk: Buffer) => _err.push(chunk));
  proc.stdin!.write(dslText + '\n');
  proc.stdin!.end();
  return new Promise((resolve) => {
    proc.on('close', (code) => {
          const stdout = Buffer.concat(_out).toString('utf8');
          const stderr = Buffer.concat(_err).toString('utf8');
      if (stdout.trim()) {
        try { resolve(JSON.parse(stdout)); }
        catch { resolve({ error: 'JSON 解析失败', raw: stdout }); }
      } else {
        resolve({ error: stderr.trim() || '转换失败' });
      }
    });
    proc.on('error', (err) => resolve({ error: err.message }));
  });
});

// --- Feature Registry IPC ---

ipcMain.handle('features:list', async () => {
  const scriptPath = path.join(getBaseDir(), 'python', 'tools', 'feature_list.py');
  const proc = await spawnPythonOneShot([scriptPath], {
    cwd: getBaseDir(),
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  const _out: Buffer[] = [];
  const _err: Buffer[] = [];
  proc.stdout!.on('data', (chunk: Buffer) => _out.push(chunk));
  proc.stderr!.on('data', (chunk: Buffer) => _err.push(chunk));
  return new Promise((resolve) => {
    proc.on('close', () => {
          const stdout = Buffer.concat(_out).toString('utf8');
          const stderr = Buffer.concat(_err).toString('utf8');
      if (stdout.trim()) {
        try { resolve(JSON.parse(stdout)); }
        catch { resolve({ error: 'JSON 解析失败', raw: stdout }); }
      } else {
        resolve({ error: stderr.trim() || '获取功能列表失败' });
      }
    });
    proc.on('error', (err) => resolve({ error: err.message }));
  });
});

ipcMain.handle('feature:call', async (_, featureName: string, params: Record<string, any>) => {
  return await callFeature(featureName, params);
});

// --- Macro Recording IPC ---

let macroRecorderProcess: ChildProcess | null = null;
let _macroRecording = false;
let _macroResultResolve: ((result: any) => void) | null = null;
let _macroStdoutBuffer = '';

function getMacroRecorderScriptPath(): string {
  return path.join(getBaseDir(), 'python', 'tools', 'macro_recorder.py');
}

function _ensureMacroRecorder(): ChildProcess | null {
  if (macroRecorderProcess && !macroRecorderProcess.killed) {
    return macroRecorderProcess;
  }
  const scriptPath = getMacroRecorderScriptPath();
  try {
    macroRecorderProcess = spawnPython([scriptPath], {
      cwd: getBaseDir(),
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    _macroStdoutBuffer = '';

    macroRecorderProcess.stdout!.on('data', (chunk: Buffer) => {
      const text = chunk.toString('utf8');
      _macroStdoutBuffer += text;
      // 防止缓冲区无限增长（最大 64KB）
      if (_macroStdoutBuffer.length > 65536) {
        _macroStdoutBuffer = _macroStdoutBuffer.slice(-32768);
      }
      // Process complete lines
      const lines = _macroStdoutBuffer.split('\n');
      _macroStdoutBuffer = lines.pop() || ''; // keep incomplete line
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const msg = JSON.parse(line);
          if (msg.type === 'result' && _macroResultResolve) {
            _macroResultResolve(msg);
            _macroResultResolve = null;
          }
          // Forward status updates to renderer
          if (msg.type === 'status' || msg.type === 'ready' || msg.type === 'pong' || msg.type === 'error') {
            mainWindow?.webContents.send('macro-message', msg);
          }
        } catch { /* ignore non-JSON stdout lines */ }
      }
    });

    macroRecorderProcess.stderr!.on('data', (chunk: Buffer) => {
      mainWindow?.webContents.send('engine-log', { level: 'error', message: `[Macro] ${chunk.toString('utf8')}` });
    });

    macroRecorderProcess.on('exit', () => {
      macroRecorderProcess = null;
      _macroRecording = false;
    });

    // Wait for ready signal
    return macroRecorderProcess;
  } catch (e: any) {
    mainWindow?.webContents.send('engine-log', { level: 'error', message: `启动宏录制器失败: ${e.message}` });
    return null;
  }
}

function _sendToMacroProcess(msg: any): Promise<any> {
  return new Promise((resolve) => {
    const proc = _ensureMacroRecorder();
    if (!proc) {
      resolve({ success: false, error: '宏录制器不可用' });
      return;
    }
    _macroResultResolve = resolve;
    try {
      proc.stdin!.write(JSON.stringify(msg) + '\n');
    } catch (e: any) {
      _macroResultResolve = null;
      resolve({ success: false, error: e.message });
    }
    // Timeout after 30s
    setTimeout(() => {
      if (_macroResultResolve) {
        _macroResultResolve = null;
        resolve({ success: false, error: '宏录制超时' });
      }
    }, 30000);
  });
}

ipcMain.handle('macro:start', async (_, params: { name?: string; include_mouse?: boolean; include_keys?: boolean }) => {
  const result = await _sendToMacroProcess({
    type: 'start',
    name: params.name || `recording_${Date.now()}`,
    include_mouse: params.include_mouse ?? true,
    include_keys: params.include_keys ?? true,
  });
  if (result?.success) _macroRecording = true;
  return result;
});

ipcMain.handle('macro:stop', async () => {
  const result = await _sendToMacroProcess({ type: 'stop' });
  _macroRecording = false;
  return result;
});

ipcMain.handle('macro:list', async () => {
  const macrosDir = path.join(getBaseDir(), 'python', 'macros');
  try {
    const files = fs.readdirSync(macrosDir);
    const macros = [];
    for (const file of files) {
      if (!file.endsWith('.json')) continue;
      try {
        const content = JSON.parse(fs.readFileSync(path.join(macrosDir, file), 'utf-8'));
        macros.push({
          name: content.name || file.replace('.json', ''),
          file: file,
          events: content.events?.length || 0,
          duration: content.duration || 0,
          created: content.created || '',
        });
      } catch {
        macros.push({ name: file.replace('.json', ''), file, events: 0, duration: 0, created: '' });
      }
    }
    return { success: true, result: macros.sort((a, b) => b.created.localeCompare(a.created)) };
  } catch {
    return { success: true, result: [] };
  }
});

ipcMain.handle('macro:play', async (_, name: string) => {
  const scriptPath = path.join(getBaseDir(), 'python', 'tools', 'macro_play_cli.py');
  const proc = await spawnPythonOneShot([scriptPath, name], {
    cwd: getBaseDir(),
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  return new Promise((resolve) => {
    const _out: Buffer[] = [];
    const _err: Buffer[] = [];
    proc.stdout!.on('data', (chunk: Buffer) => _out.push(chunk));
    proc.stderr!.on('data', (chunk: Buffer) => _err.push(chunk));
    proc.on('close', () => {
          const stdout = Buffer.concat(_out).toString('utf8');
          const stderr = Buffer.concat(_err).toString('utf8');
      if (stdout.trim()) {
        try { resolve(JSON.parse(stdout)); }
        catch { resolve({ success: false, error: '解析失败', raw: stdout }); }
      } else {
        resolve({ success: false, error: stderr.trim() || '播放失败' });
      }
    });
    proc.on('error', (err) => resolve({ success: false, error: err.message }));
  });
});

ipcMain.handle('macro:delete', async (_, name: string) => {
  const macrosDir = path.join(getBaseDir(), 'python', 'macros');
  try {
    const filePath = resolveMacroPath(macrosDir, name);
    fs.unlinkSync(filePath);
    return { success: true };
  } catch (error) {
    return { success: false, error: error instanceof Error ? error.message : '文件不存在' };
  }
});

ipcMain.handle('macro:get-events', async (_, name: string) => {
  const macrosDir = path.join(getBaseDir(), 'python', 'macros');
  try {
    const filePath = resolveMacroPath(macrosDir, name);
    const content = JSON.parse(fs.readFileSync(filePath, 'utf-8'));
    return { success: true, result: content };
  } catch (error) {
    return { success: false, error: error instanceof Error ? error.message : '读取失败' };
  }
});

ipcMain.handle('macro:recording-status', () => {
  return { recording: _macroRecording, process_alive: macroRecorderProcess !== null && !macroRecorderProcess.killed };
});

// --- Auto-start IPC ---
ipcMain.handle('settings:get-auto-start', () => {
  return app.getLoginItemSettings().openAtLogin;
});
ipcMain.handle('settings:set-auto-start', (_, enable: boolean) => {
  app.setLoginItemSettings({ openAtLogin: enable });
  return enable;
});

// --- Vision backend IPC ---
ipcMain.handle('settings:set-vision-backend', (_, backend: string) => {
  const { setVlmBackend } = require('./engine');
  setVlmBackend(backend);
  return true;
});

// --- Notification IPC ---
ipcMain.handle('notification:show', (_, title: string, body: string) => {
  try {
    const notif = new Notification({ title: title || '灵动脚本', body: body || '', silent: false });
    notif.show();
    return true;
  } catch { return false; }
});

ipcMain.handle(
  'taskbar:notify',
  (_, title: string, body: string, kind: TaskbarNotificationKind = 'info', runId = '') =>
    notifyTaskbarAttention(title, body, kind, runId),
);

// --- Schedule IPC ---
const SCHEDULES_FILE = path.join(app.getPath('userData'), 'schedules.json');

function migrateSchedule(entry: any): any {
  if (entry.triggers) return entry; // already new format
  // Old format: { type, cron_expr?, interval_seconds? } → { triggers: [{ type, cron_expr?, interval_seconds? }] }
  const trigger: any = { type: entry.type || 'startup' };
  if (entry.cron_expr) trigger.cron_expr = entry.cron_expr;
  if (entry.interval_seconds) trigger.interval_seconds = entry.interval_seconds;
  return {
    ...entry,
    triggers: [trigger],
    conditions: entry.conditions || undefined,
    type: undefined, cron_expr: undefined, interval_seconds: undefined,
  };
}

async function readSchedules(): Promise<any[]> {
  try {
    try { await fsp.access(SCHEDULES_FILE); } catch { return []; }
    const raw = await fsp.readFile(SCHEDULES_FILE, 'utf-8');
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    let changed = false;
    const migrated = parsed.map((s: any) => {
      const m = migrateSchedule(s);
      if (m !== s) changed = true;
      return m;
    });
    if (changed) await writeSchedules(migrated);
    return migrated;
  } catch { return []; }
}

async function writeSchedules(schedules: any[]): Promise<void> {
  try {
    await fsp.writeFile(SCHEDULES_FILE, JSON.stringify(schedules, null, 2), 'utf-8');
  } catch (e) {
    console.error('Failed to write schedules:', e);
  }
}

ipcMain.handle('schedule:list', async () => readSchedules());

ipcMain.handle('schedule:create', async (_, entry: any) => {
  const schedules = await readSchedules();
  const id = `sched_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
  const newEntry = { ...entry, id, created_at: new Date().toISOString(), last_run: null, next_run: null };
  schedules.push(newEntry);
  await writeSchedules(schedules);
  scheduler.reload();
  refreshScheduleContext();
  return newEntry;
});

ipcMain.handle('schedule:update', async (_, id: string, updates: any) => {
  const schedules = await readSchedules();
  const idx = schedules.findIndex((s: any) => s.id === id);
  if (idx === -1) return null;
  schedules[idx] = { ...schedules[idx], ...updates };
  await writeSchedules(schedules);
  scheduler.reload();
  refreshScheduleContext();
  return schedules[idx];
});

ipcMain.handle('schedule:delete', async (_, id: string) => {
  const schedules = await readSchedules();
  const filtered = schedules.filter((s: any) => s.id !== id);
  if (filtered.length === schedules.length) return false;
  await writeSchedules(filtered);
  scheduler.reload();
  refreshScheduleContext();
  return true;
});

// --- Menu Action IPC ---
ipcMain.on('menu-action', (_, action: string) => {
  mainWindow?.webContents.send('menu-action', action);
});

// --- Single Instance Lock ---

const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    // Another instance was launched — focus the existing window
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  // --- App Lifecycle ---

  app.whenReady().then(() => {
    if (process.platform === 'win32') app.setAppUserModelId('com.lingdong.script');
    buildAppMenu();
    createWindow();
    // Launch the embedded WinPilot agent server (non-blocking; the Agent panel
    // iframe polls winpilot:url until it is ready).
    startWinpilotServer().catch((e) => console.error('[winpilot] start failed:', e));
    apiServer = startApiServer({
      mainWindow,
      getCachedRendererState: () => _cachedRendererState,
      getMacroRecording: () => _macroRecording,
      setMacroRecording: (recording: boolean) => { _macroRecording = recording; },
      getMacroRecorderProcess: () => macroRecorderProcess,
      ensureMacroRecorder: _ensureMacroRecorder,
      captureScreenshot: _captureScreenshot,
      selectRegion: _selectRegion,
      wheelSelect: _wheelSelect,
      ocrRecognizeRegion: _ocrRegion,
      dslToFlow: async (dslText: string) => {
        const scriptPath = path.join(getBaseDir(), 'tools', 'dsl_to_flow.py');
        const result = await runPythonCollect([scriptPath], { cwd: getBaseDir(), stdio: ['pipe', 'pipe', 'pipe'] }, dslText);
        try { return JSON.parse(result.stdout.trim()); }
        catch { return { error: result.stderr?.trim() || 'DSL转换失败' }; }
      },
      startAgent: startAgentProcess,
      controlToken: LOCAL_CONTROL_TOKEN,
      allowUnsafeEval: isDev && process.env.LINGDONG_ENABLE_UNSAFE_EVAL === '1',
    });

    // ── MCP 自动注册到 Claude Code ──
    (() => {
      try {
        const claudeJsonPath = path.join(os.homedir(), '.claude.json');
        let claudeConfig: any = {};
        try { claudeConfig = JSON.parse(fs.readFileSync(claudeJsonPath, 'utf-8')); } catch {}
        if (!claudeConfig.mcpServers) claudeConfig.mcpServers = {};
        claudeConfig.mcpServers['lingdong-script'] = {
          type: 'http',
          url: `http://127.0.0.1:${parseInt(process.env.API_PORT || '7880', 10)}/mcp`,
          headers: { Authorization: `Bearer ${LOCAL_CONTROL_TOKEN}` },
        };
        fs.writeFileSync(claudeJsonPath, JSON.stringify(claudeConfig, null, 2), 'utf-8');
        console.log('[MCP] Registered authenticated lingdong-script endpoint in ~/.claude.json');
      } catch (e: any) {
        console.error('[MCP] Auto-register failed:', e.message);
      }
    })();

    // 调度引擎
    scheduler.start(path.join(app.getPath('userData'), 'schedules.json'));

    // 启动语音对话服务器

    // 后台扫描已安装程序（填充程序别名注册表）
    setTimeout(() => {
      callFeature('program_refresh', { background: true }).catch(() => {});
    }, 5000);

    // 全局 F9 热键 — 在主进程直接控制引擎启停
    globalShortcut.register('F9', () => {
      if (!mainWindow) return;
      if (engineRunning) {
        stopPythonEngine();
        mainWindow.webContents.send('engine-message', { type: 'execution_done', status: 'stopped' });
        mainWindow.webContents.send('engine-log', { level: 'info', message: '⏹️ 脚本已停止 (全局F9)' });
      } else if (lastScript) {
        startPythonEngine(lastScript);
        mainWindow.webContents.send('engine-message', { type: 'status_report', status: 'running' });
        mainWindow.webContents.send('engine-log', { level: 'info', message: '🔄 脚本开始执行 (全局F9)' });
      } else {
        mainWindow.webContents.send('engine-log', { level: 'warn', message: '没有可执行的脚本，请先在编辑器中打开或创建脚本' });
      }
    });

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
  });
}

// ── 全局未捕获异常处理 ──
process.on('uncaughtException', (error) => {
  console.error('[FATAL] uncaughtException:', error);
  // 不退出进程，尽量保持 Electron 运行
});

process.on('unhandledRejection', (reason) => {
  console.error('[FATAL] unhandledRejection:', reason);
});

app.on('window-all-closed', () => {
  stopPythonEngine();
  stopFeatureProcess();
  stopWinpilotServer();
  apiServer?.close();
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  globalShortcut.unregisterAll();
  stopPythonEngine();
  stopFeatureProcess();
  stopWinpilotServer();
  scheduler.stop();
});
