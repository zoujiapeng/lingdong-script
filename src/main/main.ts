import { app, BrowserWindow, globalShortcut, ipcMain, dialog, desktopCapturer, Menu } from 'electron';
import * as path from 'path';
import { spawn, ChildProcess } from 'child_process';
import * as https from 'https';

let mainWindow: BrowserWindow | null = null;
let pythonProcess: ChildProcess | null = null;
let _lastScript: string | null = null;
let _engineRunning = false;

const isDev = !app.isPackaged;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1000,
    minHeight: 700,
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
    mainWindow.webContents.openDevTools({ mode: 'detach' });
  } else {
    mainWindow.loadFile(path.join(__dirname, '../renderer/index.html'));
  }

  mainWindow.once('ready-to-show', () => {
    mainWindow?.show();
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
    });
  } catch (e) {
    mainWindow?.webContents.send('engine-log', { level: 'error', message: `启动引擎失败: ${e}` });
    return;
  }

  _engineRunning = true;

  // Send script data
  pythonProcess.stdin!.write(JSON.stringify({ type: 'execute', payload: { script: JSON.parse(scriptJson) } }) + '\n');

  pythonProcess.stdout!.on('data', (data: Buffer) => {
    const lines = data.toString().split('\n').filter(Boolean);
    for (const line of lines) {
      try {
        const msg = JSON.parse(line);
        mainWindow?.webContents.send('engine-message', msg);
      } catch {
        mainWindow?.webContents.send('engine-log', { level: 'info', message: line });
      }
    }
  });

  pythonProcess.stderr!.on('data', (data: Buffer) => {
    mainWindow?.webContents.send('engine-log', { level: 'error', message: data.toString() });
  });

  pythonProcess.on('close', (code) => {
    _engineRunning = false;
    pythonProcess = null;
    mainWindow?.webContents.send('engine-message', { type: 'execution_done', status: 'stopped' });
    mainWindow?.webContents.send('engine-log', { level: 'info', message: `引擎进程退出 (代码: ${code})` });
  });
}

function stopPythonEngine() {
  if (pythonProcess) {
    pythonProcess.kill('SIGTERM');
    // Force immediate cleanup — on Windows SIGTERM is TerminateProcess
    pythonProcess = null;
    _engineRunning = false;
  } else {
    _engineRunning = false;
  }
}

function sendToEngine(message: any) {
  if (pythonProcess && pythonProcess.stdin) {
    pythonProcess.stdin.write(JSON.stringify(message) + '\n');
  }
}

// --- IPC Handlers ---

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

ipcMain.handle('ai:generate', (_, params: { apiKey: string; messages: { role: string; content: string }[] }) => {
  return new Promise((resolve) => {
    const body = JSON.stringify({
      model: 'deepseek-chat',
      messages: params.messages,
      response_format: { type: 'json_object' },
      temperature: 0.1,
      max_tokens: 2048,
    });
    const u = new URL('https://api.deepseek.com/v1/chat/completions');
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

// --- Menu Action IPC ---
ipcMain.on('menu-action', (_, action: string) => {
  mainWindow?.webContents.send('menu-action', action);
});

// --- App Lifecycle ---

app.whenReady().then(() => {
  buildAppMenu();
  createWindow();

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

app.on('window-all-closed', () => {
  stopPythonEngine();
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  globalShortcut.unregisterAll();
  stopPythonEngine();
});
