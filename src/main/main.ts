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

/** 安全累积子进程 stdout/stderr，避免 chunk 边界截断 UTF-8 多字节字符 */
function runPythonCollect(args: string[], options?: SpawnOptions, input?: string): Promise<{ stdout: string; stderr: string }> {
  return new Promise((resolve) => {
    const proc = spawnPython(args, options);
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

function createWindow() {
  const startNormal = process.argv.includes('--normal');

  mainWindow = new BrowserWindow({
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
  });

  if (isDev) {
    mainWindow.loadURL('http://localhost:5173');
  } else {
    mainWindow.loadFile(path.join(__dirname, '../renderer/index.html'));
  }

  mainWindow.once('ready-to-show', () => {
    mainWindow?.show();
    if (!startNormal) {
      mainWindow?.minimize();
    }
    _rendererReady = true;
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  /** 开发模式下 Vite 断开时自动重载，防止白屏 */
  if (isDev) {
    mainWindow.webContents.on('did-fail-load', () => {
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
          dialog.showMessageBox(mainWindow!, {
            type: 'info',
            title: '关于灵动脚本',
            message: '灵动脚本 v1.0.0',
            detail: [
              '高级可视化桌面自动化脚本编辑器',
              '',
              '开发者：邹嘉鹏',
              '技术栈：Electron + React + Python',
              '',
              '功能特性：',
              '• 可视化流程图编排自动化流程',
              '• 127+ 节点类型（点击、输入、OCR、条件、循环等）',
              '• DSL 脚本语言，可文本编辑和 AI 生成',
              '• 宏录制（F6 热键），一键转流程图',
              '• 图片识别、颜色检测、进度条监控',
              '• @feature 工具注册表（120+ 工具）',
              '• MCP 协议支持（Python + TypeScript 双服务器）',
              '• CLI 命令行工具（run/ask/create/serve）',
              '• HTTP API 桥接（127.0.0.1:5577）',
              '• 区域选择 / 滚轮选择器 / OCR 识别',
              '',
              '目录结构：',
              '  src/main/     Electron 主进程',
              '  src/renderer/ React 前端',
              '  python/       自动化引擎 + 工具',
              '  cli/          CLI 命令行',
              '  tests/        Python 测试',
            ].join('\n'),
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
      res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify(data));
    };

    if (req.method !== 'POST') {
      send(405, { error: 'Method not allowed' });
      return;
    }

    const chunks: Buffer[] = [];
    req.on('data', (chunk: Buffer) => chunks.push(chunk));
    req.on('end', () => {
      const body = Buffer.concat(chunks).toString('utf8');
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
          if (!payload.type) { send(400, { error: '缺少 type' }); return; }
          const nodeId = 'n_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 6);
          mainWindow.webContents.send('api-command', {
            action: 'addNode',
            payload: { type: payload.type, x: payload.x, y: payload.y, params: payload.params, id: nodeId },
          });
          send(200, { ok: true, id: nodeId });
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
          if (!payload.from_node || !payload.to_node) { send(400, { error: '缺少 from_node 或 to_node' }); return; }
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
          const _dslOut: Buffer[] = [];
          const _dslErr: Buffer[] = [];
          dslProc.stdout!.on('data', (chunk: Buffer) => _dslOut.push(chunk));
          dslProc.stderr!.on('data', (chunk: Buffer) => _dslErr.push(chunk));
          dslProc.on('close', (code: number) => {
            const dslStdout = Buffer.concat(_dslOut).toString('utf8');
            if (dslStdout.trim()) {
              try {
                const flow = JSON.parse(dslStdout);
                mainWindow!.webContents.send('api-command', { action: 'loadScript', payload: flow });
                send(200, { ok: true, flow });
              } catch {
                send(500, { error: 'DSL 转换失败', raw: dslStdout });
              }
            } else {
              const dslStderr = Buffer.concat(_dslErr).toString('utf8');
              send(500, { error: dslStderr.trim() || 'DSL 转换失败' });
            }
          });
          if (payload.dsl) {
            dslProc.stdin!.write(payload.dsl);
          }
          dslProc.stdin!.end();
        }


        break;

        case '/api/generate-dsl':
          if (!payload.description) { send(400, { error: '缺少 description' }); return; }
          (async () => {
            try {
              const apiKey = payload.apiKey || process.env.DEEPSEEK_KEY || '';
              if (!apiKey) { send(400, { error: '需要 apiKey' }); return; }
              const result = await callLLM({
                apiKey,
                messages: [
                  { role: 'system', content: 'You are a DSL generator for desktop automation. Generate ONLY raw DSL code, no markdown, no explanations.\n\nKeywords:\nCLICK x y [single|double|right|middle]\nINPUT_TEXT x y "text"\nKEYBOARD key1 [key2 ...]\nWAIT seconds\nSCREENSHOT\nLOOP count|INFINITE ... END\nIF condition ... ELSE ... END\nCLICK_IMAGE "path" [similarity]\nCLICK_TEXT "text" [similarity]\nSWIPE x1 y1 x2 y2 [duration]\nPOPUP_CLOSE [strategy]\nCOLOR_CHECK x y w h color [tolerance]\nPROGRESS_WAIT fill|empty|change x y w h [timeout]\nREGION_WAIT stable|change x y w h [timeout]\n\nGenerate appropriate coordinates, paths and parameters. Include WAIT steps between actions.' },
                  { role: 'user', content: payload.description },
                ],
                max_tokens: 4096,
              });
              if (result.error) { send(500, { error: result.error }); return; }
              let dsl = (result.choices?.[0]?.message?.content || '').trim();
              dsl = dsl.replace(/^`{3}[a-z]*\n?/i, '').replace(/\n?`{3}$/i, '');
              send(200, { ok: true, dsl });
            } catch (e: any) { send(500, { error: e.message }); }
          })();
          break;

        case '/api/create-flow':
          if (!payload.description && !payload.dsl) { send(400, { error: '缺少 description 或 dsl' }); return; }
          (async () => {
            try {
              let dsl = payload.dsl;
              if (!dsl) {
                const apiKey = payload.apiKey || process.env.DEEPSEEK_KEY || '';
                if (!apiKey) { send(400, { error: '需要 apiKey' }); return; }
                const result = await callLLM({
                  apiKey,
                  messages: [
                    { role: 'system', content: 'You are a DSL generator for desktop automation. Generate ONLY raw DSL code, no markdown, no explanations.\n\nKeywords:\nCLICK x y [single|double|right|middle]\nINPUT_TEXT x y "text"\nKEYBOARD key1 [key2 ...]\nWAIT seconds\nSCREENSHOT\nLOOP count|INFINITE ... END\nIF condition ... ELSE ... END\nCLICK_IMAGE "path" [similarity]\nCLICK_TEXT "text" [similarity]\nSWIPE x1 y1 x2 y2 [duration]\nPOPUP_CLOSE [strategy]\nCOLOR_CHECK x y w h color [tolerance]\nPROGRESS_WAIT fill|empty|change x y w h [timeout]\nREGION_WAIT stable|change x y w h [timeout]\n\nGenerate appropriate coordinates, paths and parameters. Include WAIT steps between actions.' },
                    { role: 'user', content: payload.description },
                  ],
                  max_tokens: 4096,
                });
                if (result.error) { send(500, { error: result.error }); return; }
                dsl = (result.choices?.[0]?.message?.content || '').trim();
                dsl = dsl.replace(/^`{3}[a-z]*\n?/i, '').replace(/\n?`{3}$/i, '');
              }
              if (!dsl) { send(500, { error: 'DSL 生成为空' }); return; }
              const scriptPath = path.join(__dirname, '../../tools/dsl_to_flow.py');
              const proc = spawnPython([scriptPath], {
                cwd: path.join(__dirname, '../..'),
                stdio: ['pipe', 'pipe', 'pipe'],
              });
              const _out: Buffer[] = [];
              const _err: Buffer[] = [];
              proc.stdout!.on('data', (chunk: Buffer) => _out.push(chunk));
              proc.stderr!.on('data', (chunk: Buffer) => _err.push(chunk));
              proc.on('close', () => {
          const stdout = Buffer.concat(_out).toString('utf8');
          const stderr = Buffer.concat(_err).toString('utf8');
                try {
                  if (!stdout.trim()) { send(500, { error: stderr.trim() || 'DSL 转换失败' }); return; }
                  const flow = JSON.parse(stdout);
                  if (mainWindow) {
                    mainWindow.webContents.send('api-command', { action: 'loadScript', payload: flow });
                  }
                  const pending_params = findIncompleteParams(flow.nodes || []);
                  send(200, { ok: true, flow, dsl, pending_params });
                } catch (e: any) { send(500, { error: 'JSON parse: ' + e.message, raw: stdout.slice(0, 500) }); }
              });
              proc.on('error', (err: Error) => send(500, { error: err.message }));
              proc.stdin!.write(dsl + '\n');
              proc.stdin!.end();
            } catch (e: any) { send(500, { error: e.message }); }
          })();
          break;

        case '/api/check-params':
          if (!payload.nodes) { send(400, { error: '缺少 nodes' }); return; }
          const pending = findIncompleteParams(payload.nodes);
          send(200, { ok: true, pending_params: pending, count: pending.length });
          break;

        case '/api/resolve-param':
          if (!mainWindow) { send(503, { error: '窗口未就绪' }); return; }
          if (!payload.node_id || !payload.param) { send(400, { error: '缺少 node_id 或 param' }); return; }
          mainWindow.webContents.send('api-command', {
            action: 'updateNodeParams',
            payload: { id: payload.node_id, params: { [payload.param]: payload.value } },
          });
          send(200, { ok: true });
          break;

        case '/api/interactive-fill':
          if (!mainWindow) { send(503, { error: '窗口未就绪' }); return; }
          if (!payload.node_id || !payload.param) { send(400, { error: '缺少 node_id 或 param' }); return; }
          (async () => {
            try {
              const tool = payload.tool || 'region_picker';
              const imgDir = path.join(__dirname, '../../assets/screenshots');
              switch (tool) {
                case 'region_picker': {
                  const result = await mainWindow!.webContents.executeJavaScript('window.electronAPI.selectRegion()', true);
                  if (result.error) { send(400, { error: result.error }); return; }
                  const { x, y, w, h } = result;
                  mainWindow!.webContents.send('api-command', {
                    action: 'updateNodeParams',
                    payload: { id: payload.node_id, params: { [payload.param]: { x, y, w, h } } },
                  });
                  send(200, { ok: true, value: { x, y, w, h } });
                  return;
                }
                case 'screenshot': {
                  const result2 = await mainWindow!.webContents.executeJavaScript('window.electronAPI.captureScreenshot()', true);
                  if (result2.error) { send(400, { error: result2.error }); return; }
                  if (!fs.existsSync(imgDir)) fs.mkdirSync(imgDir, { recursive: true });
                  const fileName = `img_${payload.node_id}_${Date.now()}.png`;
                  const filePath = path.join(imgDir, fileName);
                  const base64 = result2.dataURL.replace(/^data:image\/png;base64,/, '');
                  fs.writeFileSync(filePath, Buffer.from(base64, 'base64'));
                  const relativePath = `assets/screenshots/${fileName}`;
                  mainWindow!.webContents.send('api-command', {
                    action: 'updateNodeParams',
                    payload: { id: payload.node_id, params: { [payload.param]: [{ path: relativePath }] } },
                  });
                  send(200, { ok: true, value: [{ path: relativePath }] });
                  return;
                }
                case 'wheel_select': {
                  const options = payload.options || ['是', '否'];
                  const wResult = await mainWindow!.webContents.executeJavaScript(
                    `window.electronAPI.wheelSelect({options: ${JSON.stringify(options)}, title: ${JSON.stringify(payload.hint || '请选择')}})`, true);
                  if (wResult.error) { send(400, { error: wResult.error }); return; }
                  const wValue = wResult.value || wResult.selected || options[0];
                  mainWindow!.webContents.send('api-command', {
                    action: 'updateNodeParams',
                    payload: { id: payload.node_id, params: { [payload.param]: wValue } },
                  });
                  send(200, { ok: true, value: wValue });
                  return;
                }
                case 'text_input':
                  send(200, { ok: true, message: '请通过 /api/resolve-param 提供 value 参数', needs_value: true });
                  return;
                case 'coordinate_picker': {
                  const coordResult = await mainWindow!.webContents.executeJavaScript('window.electronAPI.selectRegion()', true);
                  if (coordResult.error) { send(400, { error: coordResult.error }); return; }
                  const cx = Math.round(coordResult.x + coordResult.w / 2);
                  const cy = Math.round(coordResult.y + coordResult.h / 2);
                  mainWindow!.webContents.send('api-command', {
                    action: 'updateNodeParams',
                    payload: { id: payload.node_id, params: { x: cx, y: cy } },
                  });
                  send(200, { ok: true, value: { x: cx, y: cy } });
                  return;
                }
                default:
                  send(400, { error: '未知交互工具: ' + tool });
              }
            } catch (e: any) { send(500, { error: e.message }); }
          })();
          break;

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
          // 利用 pushState 缓存的实时状态（每次 mutation 后自动推送）
          if (_cachedRendererState !== null) {
            send(200, _cachedRendererState);
            // 后台异步刷新缓存，确保下次请求最新
            if (_rendererReady) {
              mainWindow.webContents.send('api-command', { action: 'getState' });
            }
            return;
          }
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

        case '/api/engine/pause':
          sendToEngine({ type: 'pause' });
          send(200, { ok: true });
          break;

        case '/api/engine/resume':
          sendToEngine({ type: 'resume' });
          send(200, { ok: true });
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
            const _out: Buffer[] = [];
            const _err: Buffer[] = [];
            proc.stdout!.on('data', (chunk: Buffer) => _out.push(chunk));
            proc.stderr!.on('data', (chunk: Buffer) => _err.push(chunk));
            proc.on('close', () => {
          const stdout = Buffer.concat(_out).toString('utf8');
          const stderr = Buffer.concat(_err).toString('utf8');
              if (stdout.trim()) { try { resolve(JSON.parse(stdout)); } catch { resolve({ success: false, error: '解析失败' }); } }
              else { resolve({ success: false, error: stderr.trim() || '播放失败' }); }
            });
            proc.on('error', (err) => resolve({ success: false, error: err.message }));
          }).then(r => send(200, r));
          break;

        // --- Feature Registry HTTP API ---
        case '/api/features/list':
          new Promise((resolve) => {
            const proc = spawnPython(['-c', `
import sys, json
sys.path.insert(0, 'python')
from features._all import *
from features.registry import registry
sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
features = []
for f in registry.all():
    if f.hidden or f.deprecated:
        continue
    features.append({
        "name": f.name,
        "display_name": f.display_name,
        "description": f.description,
        "category": f.category.value,
        "params": [{"name": p.name, "type": p.type, "description": p.description,
                     "required": p.required, "default": p.default, "example": p.example} for p in f.params],
        "returns": f.returns,
        "deprecated": f.deprecated,
        "tags": f.tags,
    })
print(json.dumps(features, ensure_ascii=False))
sys.stdout.flush()
`], {
              cwd: path.join(__dirname, '../..'),
              stdio: ['pipe', 'pipe', 'pipe'],
            });
            const _out: Buffer[] = [];
            const _err: Buffer[] = [];
            proc.stdout!.on('data', (chunk: Buffer) => _out.push(chunk));
            proc.stderr!.on('data', (chunk: Buffer) => _err.push(chunk));
            proc.on('close', () => {
          const stdout = Buffer.concat(_out).toString('utf8');
          const stderr = Buffer.concat(_err).toString('utf8');
              if (stdout.trim()) {
                try { resolve(JSON.parse(stdout)); }
                catch { resolve({ success: false, error: 'JSON 解析失败', raw: stdout }); }
              } else {
                resolve({ success: false, error: stderr.trim() || '获取功能列表失败' });
              }
            });
            proc.on('error', (err) => resolve({ error: err.message }));
          }).then(r => send(200, r));
          break;

        case '/api/features/call':
          new Promise((resolve) => {
            const proc = spawnPython(['-c', `
import sys, json
sys.path.insert(0, 'python')
from features._all import *
from features.registry import registry
sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
try:
    raw = sys.stdin.read()
    data = json.loads(raw)
    result = registry.execute(data["name"], **data.get("params", {}))
    print(json.dumps(result, ensure_ascii=False))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False))
sys.stdout.flush()
`], {
              cwd: path.join(__dirname, '../..'),
              stdio: ['pipe', 'pipe', 'pipe'],
            });
            const _out: Buffer[] = [];
            const _err: Buffer[] = [];
            proc.stdout!.on('data', (chunk: Buffer) => _out.push(chunk));
            proc.stderr!.on('data', (chunk: Buffer) => _err.push(chunk));
            proc.on('close', () => {
          const stdout = Buffer.concat(_out).toString('utf8');
          const stderr = Buffer.concat(_err).toString('utf8');
              if (stdout.trim()) {
                try { resolve(JSON.parse(stdout)); }
                catch { resolve({ success: false, error: 'JSON 解析失败', raw: stdout }); }
              } else {
                resolve({ success: false, error: stderr.trim() || '调用失败' });
              }
            });
            proc.on('error', (err) => resolve({ success: false, error: err.message }));
            proc.stdin!.write(JSON.stringify({ name: payload.name, params: payload.params || {} }) + '\n');
            proc.stdin!.end();
          }).then(r => send(200, r));
          break;

        case '/api/ai/generate':
          if (!payload.apiKey || !payload.messages) { send(400, { error: '缺少 apiKey 或 messages' }); return; }
          callLLM({
            apiKey: payload.apiKey,
            messages: payload.messages,
            apiBase: payload.apiBase,
            model: payload.model,
            response_format: payload.response_format as 'json' | 'text' | undefined,
            max_tokens: payload.max_tokens,
          }).then(r => send(200, r));
          break;

        case '/api/screenshot/capture':
          _captureScreenshot().then(r => send(200, r));
          break;

        case '/api/screenshot/select-region':
          _selectRegion().then(r => send(200, r));
          break;

        case '/api/ocr/region':
          if (typeof payload.x !== 'number' || typeof payload.y !== 'number' || typeof payload.w !== 'number' || typeof payload.h !== 'number') {
            send(400, { error: '缺少 region 参数 (x,y,w,h)' }); return;
          }
          _ocrRegion({ x: payload.x, y: payload.y, w: payload.w, h: payload.h }).then(r => send(200, r));
          break;

        case '/api/wheel-select':
          if (!payload.options || !Array.isArray(payload.options)) { send(400, { error: '缺少 options 数组' }); return; }
          _wheelSelect({
            options: payload.options,
            values: payload.values,
            title: payload.title,
            message: payload.message,
            default: payload.default,
          }).then(r => send(200, r));
          break;

        case '/api/macro/delete':
          if (!payload.name) { send(400, { error: '缺少 name' }); return; }
          {
            const macrosDir = path.join(__dirname, '../../python/macros');
            const filePath = path.join(macrosDir, payload.name.endsWith('.json') ? payload.name : `${payload.name}.json`);
            try {
              fs.unlinkSync(filePath);
              send(200, { ok: true });
            } catch {
              send(404, { error: '文件不存在' });
            }
          }
          break;

        case '/api/macro/get-events':
          if (!payload.name) { send(400, { error: '缺少 name' }); return; }
          {
            const macrosDir = path.join(__dirname, '../../python/macros');
            const filePath = path.join(macrosDir, payload.name.endsWith('.json') ? payload.name : `${payload.name}.json`);
            try {
              const content = JSON.parse(fs.readFileSync(filePath, 'utf-8'));
              send(200, { ok: true, result: content });
            } catch {
              send(404, { error: '读取失败' });
            }
          }
          break;

        case '/api/macro/recording-status':
          send(200, {
            recording: _macroRecording,
            process_alive: macroRecorderProcess !== null && !macroRecorderProcess.killed,
          });
          break;

        case '/api/save-script':
          if (!mainWindow) { send(503, { error: '窗口未就绪' }); return; }
          mainWindow.webContents.send('api-command', { action: 'saveScript' });
          send(200, { ok: true });
          break;

        case '/api/set-script-name':
          if (!mainWindow) { send(503, { error: '窗口未就绪' }); return; }
          if (!payload.name) { send(400, { error: '缺少 name' }); return; }
          mainWindow.webContents.send('api-command', { action: 'setScriptName', payload: { name: payload.name } });
          send(200, { ok: true });
          break;

        case '/api/save-template':
          if (!mainWindow) { send(503, { error: '窗口未就绪' }); return; }
          if (!payload.name) { send(400, { error: '缺少 name' }); return; }
          // Save to filesystem (consistent with list-templates) with proper UTF-8 encoding
          try {
            const templatesDir = path.join(__dirname, '../../templates');
            if (!fs.existsSync(templatesDir)) fs.mkdirSync(templatesDir, { recursive: true });
            const safeName = payload.name.replace(/[\\/:*?"<>|]/g, '_');
            const filePath = path.join(templatesDir, safeName + '.flow');
            const scriptState = _cachedRendererState?.script || null;
            const flowContent = JSON.stringify({
              name: payload.name,
              description: payload.description || '',
              script: scriptState,
              created_at: new Date().toISOString(),
            }, null, 2);
            fs.writeFileSync(filePath, flowContent, 'utf-8');
          } catch (e: any) {
            console.error('[API] save-template write error:', e.message);
          }
          // Also notify renderer for localStorage consistency
          mainWindow.webContents.send('api-command', { action: 'saveTemplate', payload: { name: payload.name, description: payload.description } });
          send(200, { ok: true });
          break;

        case '/api/list-templates':
          {
            const templatesDir = path.join(__dirname, '../../templates');
            fs.readdir(templatesDir, (err, files) => {
              if (err) { send(500, { error: '读取模板目录失败' }); return; }
              const templates = files.filter(f => f.endsWith('.flow')).map(f => {
                const filePath = path.join(templatesDir, f);
                let displayName = f.replace(/\.flow$/, '');
                try {
                  const content = JSON.parse(fs.readFileSync(filePath, 'utf-8'));
                  if (content.name) displayName = content.name;
                } catch {}
                return { name: displayName, path: filePath };
              });
              send(200, { ok: true, templates });
            });
          }
          break;

        case '/api/set-ui':
          if (!mainWindow) { send(503, { error: '窗口未就绪' }); return; }
          if (!payload.updates) { send(400, { error: '缺少 updates' }); return; }
          mainWindow.webContents.send('api-command', { action: 'setUI', payload: payload.updates });
          send(200, { ok: true });
          break;

        case '/api/command':
          if (!mainWindow) { send(503, { error: '窗口未就绪' }); return; }
          if (!Array.isArray(payload.commands)) { send(400, { error: '缺少 commands 数组' }); return; }
          (async () => {
            for (const cmd of payload.commands) {
              if (cmd.delay) await new Promise(r => setTimeout(r, cmd.delay));
              mainWindow!.webContents.send('api-command', {
                action: cmd.action,
                payload: cmd.payload || {},
              });
            }
            send(200, { ok: true });
          })();
          break;

        case '/api/undo':
          if (!mainWindow) { send(503, { error: '窗口未就绪' }); return; }
          mainWindow.webContents.send('api-command', { action: 'undo' });
          send(200, { ok: true });
          break;

        case '/api/redo':
          if (!mainWindow) { send(503, { error: '窗口未就绪' }); return; }
          mainWindow.webContents.send('api-command', { action: 'redo' });
          send(200, { ok: true });
          break;

        case '/api/copy-selected':
          if (!mainWindow) { send(503, { error: '窗口未就绪' }); return; }
          mainWindow.webContents.send('api-command', { action: 'copySelected' });
          send(200, { ok: true });
          break;

        case '/api/cut-selected':
          if (!mainWindow) { send(503, { error: '窗口未就绪' }); return; }
          mainWindow.webContents.send('api-command', { action: 'cutSelected' });
          send(200, { ok: true });
          break;

        case '/api/paste':
          if (!mainWindow) { send(503, { error: '窗口未就绪' }); return; }
          mainWindow.webContents.send('api-command', { action: 'paste' });
          send(200, { ok: true });
          break;

        case '/api/select-all':
          if (!mainWindow) { send(503, { error: '窗口未就绪' }); return; }
          mainWindow.webContents.send('api-command', { action: 'selectAll' });
          send(200, { ok: true });
          break;

        case '/api/clear-selection':
          if (!mainWindow) { send(503, { error: '窗口未就绪' }); return; }
          mainWindow.webContents.send('api-command', { action: 'clearSelection' });
          send(200, { ok: true });
          break;

        case '/api/duplicate-nodes':
          if (!mainWindow) { send(503, { error: '窗口未就绪' }); return; }
          if (!Array.isArray(payload.ids)) { send(400, { error: '缺少 ids 数组' }); return; }
          mainWindow.webContents.send('api-command', { action: 'duplicateNodes', payload: { ids: payload.ids } });
          send(200, { ok: true });
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
    const lines = data.toString('utf8').split('\n').filter(Boolean);
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
    mainWindow?.webContents.send('engine-log', { level: 'error', message: data.toString('utf8') });
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

/** Call LLM API (DeepSeek-compatible) */
async function callLLM(opts: {
  apiKey: string;
  messages: { role: string; content: string }[];
  apiBase?: string;
  model?: string;
  response_format?: 'json' | 'text';
  max_tokens?: number;
}): Promise<any> {
  return new Promise((resolve) => {
    const apiBase = (opts.apiBase || process.env.LLM_API_BASE || 'https://api.deepseek.com/v1').replace(/\/+$/, '');
    const model = opts.model || process.env.LLM_MODEL || 'deepseek-chat';
    const body: any = {
      model,
      messages: opts.messages,
      temperature: 0.1,
      max_tokens: opts.max_tokens || 2048,
    };
    if (opts.response_format === 'json') {
      body.response_format = { type: 'json_object' };
    }
    const postData = JSON.stringify(body);
    const urlObj = new URL(apiBase + '/chat/completions');
    const httpMod = urlObj.protocol === 'https:' ? require('https') : require('http');
    const req = httpMod.request(urlObj, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + opts.apiKey,
      },
    }, (res: any) => {
      const _httpBuf: Buffer[] = [];
      res.on('data', (chunk: Buffer) => _httpBuf.push(chunk));
      res.on('end', () => {
        const data = Buffer.concat(_httpBuf).toString('utf8');
        try { resolve(JSON.parse(data)); }
        catch { resolve({ error: 'JSON parse error', raw: data.slice(0, 500) }); }
      });
    });
    req.on('error', (err: Error) => resolve({ error: err.message }));
    req.write(postData);
    req.end();
  });
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
}

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
}

ipcMain.handle('screenshot:selectRegion', async () => _selectRegion());

// --- Interaction Tools IPC ---
async function _wheelSelect(params: { options: string[]; values?: string[]; title?: string; message?: string; default?: number }): Promise<any> {
  return new Promise((resolve) => {
    const scriptPath = path.join(__dirname, '../../python/interaction/wheel_selector.py');
    const proc = spawnPython([scriptPath, '--ipc'], {
      cwd: path.join(__dirname, '../..'),
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    const _out: Buffer[] = [];
    const _err: Buffer[] = [];
    proc.stdout!.on('data', (chunk: Buffer) => _out.push(chunk));
    proc.stderr!.on('data', (chunk: Buffer) => _err.push(chunk));
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
    proc.stdin!.write(JSON.stringify(params) + '\n');
    proc.stdin!.end();
  });
}

ipcMain.handle('interaction:wheelSelect', async (_, params) => _wheelSelect(params));

async function _ocrRegion(region: { x: number; y: number; w: number; h: number }): Promise<any> {
  return new Promise((resolve) => {
    const scriptPath = path.join(__dirname, '../../tools/ocr_region.py');
    const proc = spawnPython([scriptPath], {
      cwd: path.join(__dirname, '../..'),
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    const _out: Buffer[] = [];
    const _err: Buffer[] = [];
    proc.stdout!.on('data', (chunk: Buffer) => _out.push(chunk));
    proc.stderr!.on('data', (chunk: Buffer) => _err.push(chunk));
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

    proc.stdin!.write(JSON.stringify(region) + '\n');
    proc.stdin!.end();
  });
}

ipcMain.handle('ocr:recognizeRegion', async (_, region) => _ocrRegion(region));

// --- DSL Conversion IPC ---
ipcMain.handle('dsl:toFlow', async (_, dslText: string) => {
  return new Promise((resolve) => {
    const scriptPath = path.join(__dirname, '../../tools/dsl_to_flow.py');
    const proc = spawnPython([scriptPath], {
      cwd: path.join(__dirname, '../..'),
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    const _out: Buffer[] = [];
    const _err: Buffer[] = [];
    proc.stdout!.on('data', (chunk: Buffer) => _out.push(chunk));
    proc.stderr!.on('data', (chunk: Buffer) => _err.push(chunk));
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
    proc.stdin!.write(dslText + '\n');
    proc.stdin!.end();
  });
});

// --- Feature Registry IPC ---

ipcMain.handle('features:list', async () => {
  return new Promise((resolve) => {
    const proc = spawnPython(['-c', `
import sys, json
sys.path.insert(0, 'python')
from features._all import *
from features.registry import registry
sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
features = []
for f in registry.all():
    if f.hidden or f.deprecated:
        continue
    features.append({
        "name": f.name,
        "display_name": f.display_name,
        "description": f.description,
        "category": f.category.value,
        "params": [{"name": p.name, "type": p.type, "description": p.description,
                     "required": p.required, "default": p.default, "example": p.example} for p in f.params],
        "returns": f.returns,
        "deprecated": f.deprecated,
        "tags": f.tags,
    })
print(json.dumps(features, ensure_ascii=False))
sys.stdout.flush()
`], {
      cwd: path.join(__dirname, '../..'),
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    const _out: Buffer[] = [];
    const _err: Buffer[] = [];
    proc.stdout!.on('data', (chunk: Buffer) => _out.push(chunk));
    proc.stderr!.on('data', (chunk: Buffer) => _err.push(chunk));
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
  return new Promise((resolve) => {
    const proc = spawnPython(['-c', `
import sys, json
sys.path.insert(0, 'python')
from features._all import *
from features.registry import registry
sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
try:
    raw = sys.stdin.read()
    data = json.loads(raw)
    result = registry.execute(data["name"], **data.get("params", {}))
    print(json.dumps(result, ensure_ascii=False))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False))
sys.stdout.flush()
`], {
      cwd: path.join(__dirname, '../..'),
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    const _out: Buffer[] = [];
    const _err: Buffer[] = [];
    proc.stdout!.on('data', (chunk: Buffer) => _out.push(chunk));
    proc.stderr!.on('data', (chunk: Buffer) => _err.push(chunk));
    proc.on('close', () => {
          const stdout = Buffer.concat(_out).toString('utf8');
          const stderr = Buffer.concat(_err).toString('utf8');
      if (stdout.trim()) {
        try { resolve(JSON.parse(stdout)); }
        catch { resolve({ success: false, error: 'JSON 解析失败', raw: stdout }); }
      } else {
        resolve({ success: false, error: stderr.trim() || '调用失败' });
      }
    });
    proc.on('error', (err) => resolve({ success: false, error: err.message }));
    proc.stdin!.write(JSON.stringify({ name: featureName, params }) + '\n');
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
      const text = chunk.toString('utf8');
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
