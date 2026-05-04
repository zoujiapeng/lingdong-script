import { app, BrowserWindow, globalShortcut, ipcMain, dialog, desktopCapturer, Menu } from 'electron';
import * as path from 'path';
import * as fs from 'fs';
import { spawn, ChildProcess, SpawnOptions } from 'child_process';
import * as http from 'http';
import * as https from 'https';

let mainWindow: BrowserWindow | null = null;
let pythonProcess: ChildProcess | null = null;
let _lastScript: string | null = null;
let _engineRunning = false;

// 缓存渲染器状态，通过 IPC 定期更新，避免 executeJavaScript 挂起
let _cachedRendererState: any = null;
let _rendererReady = false;

const isDev = !app.isPackaged;

/** 跨平台 Python 子进程启动：强制 UTF-8 环境防止中文乱码 */
function spawnPython(args: string[], options?: SpawnOptions) {
  return spawn('python', args, {
    ...options,
    env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1100,
    height: 700,
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
  });

  if (isDev) {
    mainWindow.loadURL('http://localhost:5173');
  } else {
    mainWindow.loadFile(path.join(__dirname, '../renderer/index.html'));
  }

  mainWindow.once('ready-to-show', () => {
    mainWindow?.show();
    _rendererReady = true;
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
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
          dialog.showMessageBox(mainWindow!, {
            type: 'info',
            title: '关于灵动脚本',
            message: '灵动脚本 v1.0.0',
            detail: '高级可视化自动化脚本编辑器\n\n可视化编排桌面自动化流程\n支持图片识别、条件分支、循环、变量等功能',
          });
        }},
      ],
    },
  ];

  const menu = Menu.buildFromTemplate(template);
  Menu.setApplicationMenu(menu);
}

// ── Local HTTP API (for Claude Code MCP bridge) ──
const API_PORT = 5577;
let apiServer: http.Server | null = null;

function startApiServer() {
  apiServer = http.createServer((req, res) => {
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'POST, GET, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

    if (req.method === 'OPTIONS') {
      res.writeHead(204);
      res.end();
      return;
    }

    const url = new URL(req.url || '/', `http://localhost:${API_PORT}`);
    const send = (code: number, data: any) => {
      res.writeHead(code, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(data));
    };

    if (req.method !== 'POST') {
      send(405, { error: 'Method not allowed' });
      return;
    }

    let body = '';
    req.on('data', (chunk) => body += chunk);
    req.on('end', () => {
      let payload: any = {};
      try { payload = JSON.parse(body); } catch {}

      try {
      switch (url.pathname) {
        case '/api/ping':
          send(200, { ok: true });
          break;

        case '/api/load-script':
          if (!mainWindow) { send(503, { error: '窗口未就绪' }); return; }
          mainWindow.webContents.send('api-command', { action: 'loadScript', payload: payload.script });
          send(200, { ok: true });
          break;

        case '/api/add-node':
          if (!mainWindow) { send(503, { error: '窗口未就绪' }); return; }
          mainWindow.webContents.send('api-command', {
            action: 'addNode',
            payload: { type: payload.type, x: payload.x, y: payload.y, params: payload.params },
          });
          send(200, { ok: true });
          break;

        case '/api/update-node':
          if (!mainWindow) { send(503, { error: '窗口未就绪' }); return; }
          mainWindow.webContents.send('api-command', {
            action: 'updateNode',
            payload: { id: payload.id, updates: payload.updates },
          });
          send(200, { ok: true });
          break;

        case '/api/delete-nodes':
          if (!mainWindow) { send(503, { error: '窗口未就绪' }); return; }
          mainWindow.webContents.send('api-command', {
            action: 'deleteNodes',
            payload: { ids: payload.ids },
          });
          send(200, { ok: true });
          break;

        case '/api/add-edge':
          if (!mainWindow) { send(503, { error: '窗口未就绪' }); return; }
          mainWindow.webContents.send('api-command', {
            action: 'addEdge',
            payload: { from_node: payload.from_node, from_port: payload.from_port || 'output', to_node: payload.to_node, to_port: payload.to_port || 'input', label: payload.label },
          });
          send(200, { ok: true });
          break;

        case '/api/run-dsl': {
          if (!mainWindow) { send(503, { error: '窗口未就绪' }); return; }
          const scriptPath = path.join(__dirname, '../../tools/dsl_to_flow.py');
          const dslProc = spawnPython([scriptPath], {
            cwd: path.join(__dirname, '../..'),
            stdio: ['pipe', 'pipe', 'pipe'],
          });
          let dslStdout = '';
          let dslStderr = '';
          dslProc.stdout!.on('data', (chunk: Buffer) => dslStdout += chunk.toString());
          dslProc.stderr!.on('data', (chunk: Buffer) => dslStderr += chunk.toString());
          dslProc.on('close', (code: number) => {
            if (dslStdout.trim()) {
              try {
                const flow = JSON.parse(dslStdout);
                mainWindow!.webContents.send('api-command', { action: 'loadScript', payload: flow });
                send(200, { ok: true, flow });
              } catch {
                send(500, { error: 'DSL 转换失败', raw: dslStdout });
              }
            } else {
              send(500, { error: dslStderr.trim() || 'DSL 转换失败' });
            }
          });
          dslProc.on('error', (err: Error) => send(500, { error: err.message }));
          dslProc.stdin!.write(payload.dsl + '\n');
          dslProc.stdin!.end();
          break;
        }

        case '/api/auto-layout':
          if (!mainWindow) { send(503, { error: '窗口未就绪' }); return; }
          mainWindow.webContents.send('api-command', { action: 'autoLayout' });
          send(200, { ok: true });
          break;

        case '/api/update-node-params':
          if (!mainWindow) { send(503, { error: '窗口未就绪' }); return; }
          mainWindow.webContents.send('api-command', {
            action: 'updateNodeParams',
            payload: { id: payload.id, params: payload.params },
          });
          send(200, { ok: true });
          break;

        case '/api/delete-edges':
          if (!mainWindow) { send(503, { error: '窗口未就绪' }); return; }
          mainWindow.webContents.send('api-command', {
            action: 'deleteEdges',
            payload: { ids: payload.ids },
          });
          send(200, { ok: true });
          break;

        case '/api/new-script':
          if (!mainWindow) { send(503, { error: '窗口未就绪' }); return; }
          mainWindow.webContents.send('api-command', { action: 'newScript' });
          send(200, { ok: true });
          break;

        case '/api/set-view-mode':
          if (!mainWindow) { send(503, { error: '窗口未就绪' }); return; }
          mainWindow.webContents.send('api-command', {
            action: 'setViewMode',
            payload: { mode: payload.mode },
          });
          send(200, { ok: true });
          break;

        case '/api/fit-screen':
          if (!mainWindow) { send(503, { error: '窗口未就绪' }); return; }
          mainWindow.webContents.send('api-command', { action: 'fitScreen' });
          send(200, { ok: true });
          break;

        case '/api/get-state':
          if (!mainWindow) { send(503, { error: '窗口未就绪' }); return; }
          if (_cachedRendererState !== null) {
            send(200, _cachedRendererState);
          } else {
            // 尚未收到渲染器状态推送，请求一次并等待（最多 5s）
            if (_rendererReady) {
              mainWindow.webContents.send('api-command', { action: 'getState' });
            }
            (async () => {
              for (let i = 0; i < 5; i++) {
                await new Promise(r => setTimeout(r, 1000));
                if (_cachedRendererState !== null) {
                  send(200, _cachedRendererState);
                  return;
                }
              }
              send(503, { error: '渲染器未就绪' });
            })();
          }
          break;

        case '/api/run-flow':
          if (!mainWindow) { send(503, { error: '窗口未就绪' }); return; }
          try {
            const scriptJson = JSON.stringify(payload.script || {});
            startPythonEngine(scriptJson);
            send(200, { ok: true });
          } catch (e: any) {
            send(500, { error: e.message });
          }
          break;

        case '/api/stop-flow':
          stopPythonEngine();
          send(200, { ok: true });
          break;

        case '/api/engine-status':
          send(200, {
            running: _engineRunning,
            process_alive: pythonProcess !== null && !pythonProcess.killed,
            has_script: _lastScript !== null,
          });
          break;

        // --- Macro Recording HTTP API ---
        case '/api/macro/start-recording':
          if (!mainWindow) { send(503, { error: '窗口未就绪' }); return; }
          // Fire and forget — respond immediately (status polling via /api/macro/list)
          _ensureMacroRecorder();
          try {
            if (macroRecorderProcess?.stdin) {
              macroRecorderProcess.stdin.write(JSON.stringify({
                type: 'start',
                name: payload.name || `recording_${Date.now()}`,
                include_mouse: payload.include_mouse ?? true,
                include_keys: payload.include_keys ?? true,
              }) + '\n');
            }
          } catch {}
          _macroRecording = true;
          send(200, { success: true });
          break;

        case '/api/macro/stop-recording':
          try {
            if (macroRecorderProcess?.stdin) {
              macroRecorderProcess.stdin.write(JSON.stringify({ type: 'stop' }) + '\n');
            }
          } catch {}
          _macroRecording = false;
          send(200, { success: true });
          break;

        case '/api/macro/list': {
          const macrosDir = path.join(__dirname, '../../python/macros');
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
            send(200, { success: true, result: macros.sort((a, b) => b.created.localeCompare(a.created)) });
          } catch { send(200, { success: true, result: [] }); }
          break;
        }

        case '/api/macro/play':
          new Promise((resolve) => {
            const proc = spawnPython(['-c', `
import json, sys, time
sys.path.insert(0, '.')
from features.macro_features import macro_playback
result = macro_playback("${(payload.name || '').replace(/"/g, '\\"')}")
print(json.dumps(result))
sys.stdout.flush()
`], {
              cwd: path.join(__dirname, '../..'),
              stdio: ['pipe', 'pipe', 'pipe'],
            });
            let stdout = '';
            let stderr = '';
            proc.stdout!.on('data', (chunk: Buffer) => stdout += chunk.toString());
            proc.stderr!.on('data', (chunk: Buffer) => stderr += chunk.toString());
            proc.on('close', () => {
              if (stdout.trim()) { try { resolve(JSON.parse(stdout)); } catch { resolve({ success: false, error: '解析失败' }); } }
              else { resolve({ success: false, error: stderr.trim() || '播放失败' }); }
            });
            proc.on('error', (err) => resolve({ success: false, error: err.message }));
          }).then(r => send(200, r));
          break;

        default:
          send(404, { error: '未知端点' });
      }
      } catch (e: any) {
        console.error('[API] Handler error:', e);
        send(500, { error: 'Internal error: ' + (e.message || e) });
      }
    });
  });

  apiServer.listen(API_PORT, '127.0.0.1', () => {
    console.log(`[API] HTTP server listening on http://127.0.0.1:${API_PORT}`);
  });
}

function stopApiServer() {
  if (apiServer) {
    apiServer.close();
    apiServer = null;
  }
}

// --- Python Engine Process Management ---

function getPythonPath(): string {
  return isDev
    ? 'python'
    : path.join(process.resourcesPath, 'python', 'python.exe');
}

function getEngineScriptPath(): string {
  return isDev
    ? path.join(__dirname, '../../src/python/engine.py')
    : path.join(process.resourcesPath, 'python', 'engine.py');
}

function startPythonEngine(scriptJson: string) {
  if (pythonProcess) {
    stopPythonEngine();
  }

  const pythonPath = getPythonPath();
  const scriptPath = getEngineScriptPath();

  _lastScript = scriptJson;

  try {
    pythonProcess = spawn(pythonPath, [scriptPath], {
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
    });
  } catch (e) {
    mainWindow?.webContents.send('engine-log', { level: 'error', message: `启动引擎失败: ${e}` });
    return;
  }

  _engineRunning = true;

  const proc = pythonProcess; // capture reference for close handler

  // Send script data AFTER setting up stdout handler to avoid race
  const scriptPayload = JSON.stringify({ type: 'execute', payload: { script: JSON.parse(scriptJson) } });

  proc.stdout!.on('data', (data: Buffer) => {
    const lines = data.toString().split('\n').filter(Boolean);
    for (const line of lines) {
      try {
        const msg = JSON.parse(line);
        if (msg.type === 'execution_done') {
          _engineRunning = false;
        }
        mainWindow?.webContents.send('engine-message', msg);
      } catch {
        mainWindow?.webContents.send('engine-log', { level: 'info', message: line });
      }
    }
  });

  proc.stderr!.on('data', (data: Buffer) => {
    mainWindow?.webContents.send('engine-log', { level: 'error', message: data.toString() });
  });

  proc.on('exit', () => { _engineRunning = false; });

  // Send stdin LAST (after all handlers are registered)
  proc.stdin!.write(scriptPayload + '\n');
}

function stopPythonEngine() {
  _engineRunning = false;
  const proc = pythonProcess;
  pythonProcess = null;
  if (proc) {
    // Step 1: Send graceful stop message via stdin
    try { if (proc.stdin) proc.stdin.write(JSON.stringify({ type: 'stop' }) + '\n'); } catch {}

    // Step 2: If process doesn't exit gracefully within 2s, force kill
    const timer = setTimeout(() => {
      try { if (!proc.killed) proc.kill('SIGTERM'); } catch {}
    }, 2000);
    timer.unref();
  }
}

function sendToEngine(message: any) {
  if (pythonProcess && pythonProcess.stdin) {
    pythonProcess.stdin.write(JSON.stringify(message) + '\n');
  }
}

// --- IPC Handlers ---

// 渲染器推送状态更新（替代 executeJavaScript）
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

ipcMain.handle('ai:generate', (_, params: { apiKey: string; messages: { role: string; content: string }[]; apiBase?: string; model?: string }) => {
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
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        try {
          const parsed = JSON.parse(data);
          if (res.statusCode && res.statusCode >= 400) {
            resolve({ error: `API 错误 (${res.statusCode}): ${data}` });
          } else {
            resolve(parsed);
          }
        } catch {
          resolve({ error: `响应解析失败: ${data.slice(0, 200)}` });
        }
      });
    });
    req.on('error', (e) => resolve({ error: `网络错误: ${e.message}` }));
    req.on('timeout', () => { req.destroy(); resolve({ error: '请求超时' }); });
    req.write(body);
    req.end();
  });
});

ipcMain.handle('screenshot:capture', async () => {
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
      fullscreen: true, frame: false, alwaysOnTop: true,
      resizable: false, show: false,
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
      ipcMain.once('ss:done', (_: any, rect: { x: number; y: number; w: number; h: number }) => {
        const { x, y, w, h } = rect;
        const cropWin = new BrowserWindow({
          show: false,
          webPreferences: { contextIsolation: false, nodeIntegration: true },
        });
        const cropHTML = `<script>const{ipcRenderer}=require("electron");const i=new Image();i.onload=()=>{const c=document.createElement("canvas");c.width=${w};c.height=${h};c.getContext("2d").drawImage(i,${x},${y},${w},${h},0,0,${w},${h});ipcRenderer.send("ss:cropped",c.toDataURL("image/png"))};i.src='${fullDataURL}';<\/script>`;
        cropWin.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(cropHTML));
        ipcMain.once('ss:cropped', (_2: any, dataURL: string) => { cropWin.close(); resolve(dataURL); });
      });
      ipcMain.once('ss:cancel', () => { resolve(null); });
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
});

ipcMain.handle('screenshot:selectRegion', async () => {
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
      fullscreen: true, frame: false, alwaysOnTop: true,
      resizable: false, show: false,
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

    // Wait for selection result, return coordinates directly (no cropping)
    const result = await new Promise<{ x: number; y: number; w: number; h: number } | null>((resolve) => {
      ipcMain.once('ss:done', (_: any, rect: { x: number; y: number; w: number; h: number }) => {
        resolve(rect);
      });
      ipcMain.once('ss:cancel', () => { resolve(null); });
    });

    selWin.close();
    mainWindow.restore();
    mainWindow.focus();
    if (result) return { x: result.x, y: result.y, w: result.w, h: result.h };
    return { error: 'cancelled' };

  } catch (e: any) {
    mainWindow.restore();
    return { error: e.message };
  }
});

// --- Interaction Tools IPC ---
ipcMain.handle('interaction:wheelSelect', async (_, params: { options: string[]; values?: string[]; title?: string; message?: string; default?: number }) => {
  return new Promise((resolve) => {
    const scriptPath = path.join(__dirname, '../../python/interaction/wheel_selector.py');
    const proc = spawnPython([scriptPath, '--ipc'], {
      cwd: path.join(__dirname, '../..'),
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    proc.stdout!.on('data', (chunk: Buffer) => stdout += chunk.toString());
    proc.stderr!.on('data', (chunk: Buffer) => stderr += chunk.toString());
    proc.on('close', (code) => {
      if (stdout.trim()) {
        try { resolve(JSON.parse(stdout)); }
        catch { resolve({ error: 'JSON parse error', raw: stdout }); }
      } else {
        resolve({ error: stderr.trim() || 'wheel select failed' });
      }
    });
    proc.on('error', (err) => resolve({ error: err.message }));
    proc.stdin!.write(JSON.stringify(params) + '\n');
    proc.stdin!.end();
  });
});

ipcMain.handle('ocr:recognizeRegion', async (_, region: { x: number; y: number; w: number; h: number }) => {
  return new Promise((resolve) => {
    const scriptPath = path.join(__dirname, '../../tools/ocr_region.py');
    const proc = spawnPython([scriptPath], {
      cwd: path.join(__dirname, '../..'),
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    let stdout = '';
    let stderr = '';
    proc.stdout!.on('data', (chunk: Buffer) => stdout += chunk.toString());
    proc.stderr!.on('data', (chunk: Buffer) => stderr += chunk.toString());
    proc.on('close', () => {
      if (stdout.trim()) {
        try { resolve(JSON.parse(stdout)); }
        catch { resolve([]); }
      } else {
        resolve([]);
      }
    });
    proc.on('error', () => resolve([]));

    proc.stdin!.write(JSON.stringify(region) + '\n');
    proc.stdin!.end();
  });
});

// --- DSL Conversion IPC ---
ipcMain.handle('dsl:toFlow', async (_, dslText: string) => {
  return new Promise((resolve) => {
    const scriptPath = path.join(__dirname, '../../tools/dsl_to_flow.py');
    const proc = spawnPython([scriptPath], {
      cwd: path.join(__dirname, '../..'),
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    proc.stdout!.on('data', (chunk: Buffer) => stdout += chunk.toString());
    proc.stderr!.on('data', (chunk: Buffer) => stderr += chunk.toString());
    proc.on('close', (code) => {
      if (stdout.trim()) {
        try { resolve(JSON.parse(stdout)); }
        catch { resolve({ error: 'JSON 解析失败', raw: stdout }); }
      } else {
        resolve({ error: stderr.trim() || '转换失败' });
      }
    });
    proc.on('error', (err) => resolve({ error: err.message }));
    proc.stdin!.write(dslText + '\n');
    proc.stdin!.end();
  });
});

// --- Macro Recording IPC ---

let macroRecorderProcess: ChildProcess | null = null;
let _macroRecording = false;
let _macroResultResolve: ((result: any) => void) | null = null;
let _macroStdoutBuffer = '';

function getMacroRecorderScriptPath(): string {
  return path.join(__dirname, '../../python/tools/macro_recorder.py');
}

function _ensureMacroRecorder(): ChildProcess | null {
  if (macroRecorderProcess && !macroRecorderProcess.killed) {
    return macroRecorderProcess;
  }
  const scriptPath = getMacroRecorderScriptPath();
  try {
    macroRecorderProcess = spawnPython([scriptPath], {
      cwd: path.join(__dirname, '../..'),
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    _macroStdoutBuffer = '';

    macroRecorderProcess.stdout!.on('data', (chunk: Buffer) => {
      const text = chunk.toString();
      _macroStdoutBuffer += text;
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
      mainWindow?.webContents.send('engine-log', { level: 'error', message: `[Macro] ${chunk.toString()}` });
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
  const macrosDir = path.join(__dirname, '../../python/macros');
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
  return new Promise((resolve) => {
    const scriptPath = path.join(__dirname, '../../python/interaction/play_macro.py');
    // Run macro playback via the existing feature system
    const proc = spawnPython(['-c', `
import json, sys, time
sys.path.insert(0, '.')
from features.macro_features import macro_playback
result = macro_playback("${name.replace(/"/g, '\\"')}")
print(json.dumps(result))
sys.stdout.flush()
`], {
      cwd: path.join(__dirname, '../..'),
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    proc.stdout!.on('data', (chunk: Buffer) => stdout += chunk.toString());
    proc.stderr!.on('data', (chunk: Buffer) => stderr += chunk.toString());
    proc.on('close', () => {
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
  const macrosDir = path.join(__dirname, '../../python/macros');
  // Try .json file
  const filePath = path.join(macrosDir, name.endsWith('.json') ? name : `${name}.json`);
  try {
    fs.unlinkSync(filePath);
    return { success: true };
  } catch {
    return { success: false, error: '文件不存在' };
  }
});

ipcMain.handle('macro:get-events', async (_, name: string) => {
  const macrosDir = path.join(__dirname, '../../python/macros');
  const filePath = path.join(macrosDir, name.endsWith('.json') ? name : `${name}.json`);
  try {
    const content = JSON.parse(fs.readFileSync(filePath, 'utf-8'));
    return { success: true, result: content };
  } catch {
    return { success: false, error: '读取失败' };
  }
});

ipcMain.handle('macro:recording-status', () => {
  return { recording: _macroRecording, process_alive: macroRecorderProcess !== null && !macroRecorderProcess.killed };
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
    buildAppMenu();
    createWindow();
    startApiServer();

    // 全局 F9 热键 — 在主进程直接控制引擎启停
    globalShortcut.register('F9', () => {
      if (!mainWindow) return;
      if (_engineRunning) {
        stopPythonEngine();
        mainWindow.webContents.send('engine-message', { type: 'execution_done', status: 'stopped' });
        mainWindow.webContents.send('engine-log', { level: 'info', message: '⏹️ 脚本已停止 (全局F9)' });
      } else if (_lastScript) {
        startPythonEngine(_lastScript);
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

app.on('window-all-closed', () => {
  stopPythonEngine();
  stopApiServer();
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  globalShortcut.unregisterAll();
  stopPythonEngine();
});
