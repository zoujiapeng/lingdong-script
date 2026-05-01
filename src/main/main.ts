import { app, BrowserWindow, globalShortcut, ipcMain, dialog } from 'electron';
import * as path from 'path';
import { spawn, ChildProcess } from 'child_process';

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
    setTimeout(() => {
      if (pythonProcess) {
        pythonProcess.kill('SIGKILL');
        pythonProcess = null;
      }
      _engineRunning = false;
    }, 3000);
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

// --- App Lifecycle ---

app.whenReady().then(() => {
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
