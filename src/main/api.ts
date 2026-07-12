/**
 * HTTP API 服务器（提取自 main.ts）
 * 负责 :7880 端口的 REST API，供 Claude Code 远程控制前端
 */
import * as http from 'http';
import * as path from 'path';
import * as fs from 'fs';
import { timingSafeEqual } from 'crypto';
import { ChildProcess } from 'child_process';
import { callLLM, spawnPython, spawnPythonOneShot } from './engine';
import { getBaseDir } from './env';
import { callFeature, stopFeatureProcess } from './featureCallBridge';
import { handleMCPRequest } from './mcp-handler';
import { resolveMacroPath } from './macroPaths';

export interface ApiDeps {
  mainWindow: { webContents: { send: (ch: string, ...args: any[]) => void; executeJavaScript: (script: string, hasUserGesture?: boolean) => Promise<any> } } | null;
  getCachedRendererState: () => any;
  getMacroRecording: () => boolean;
  setMacroRecording: (recording: boolean) => void;
  getMacroRecorderProcess: () => ChildProcess | null;
  ensureMacroRecorder: () => ChildProcess | null;
  captureScreenshot: () => Promise<any>;
  selectRegion: () => Promise<any>;
  wheelSelect: (params: any) => Promise<any>;
  ocrRecognizeRegion: (region: any) => Promise<any>;
  dslToFlow: (dslText: string) => Promise<any>;
  startAgent: (goal: string, config: Record<string, string>, supervise: boolean) => void;
  controlToken: string;
  allowedOrigins?: string[];
  allowUnsafeEval?: boolean;
}

const API_PORT = parseInt(process.env.API_PORT || '7880', 10);

export function startApiServer(deps: ApiDeps): http.Server {
  const apiServer = http.createServer((req, res) => {
    const allowedOrigins = new Set(deps.allowedOrigins || [
      'null',
      'http://localhost:5200',
      'http://127.0.0.1:5200',
    ]);
    const origin = String(req.headers.origin || '');
    const originAllowed = !origin || allowedOrigins.has(origin);
    if (origin && originAllowed) {
      res.setHeader('Access-Control-Allow-Origin', origin);
      res.setHeader('Vary', 'Origin');
    }
    res.setHeader('Access-Control-Allow-Methods', 'POST, GET, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Authorization, Content-Type, X-Lingdong-Token');

    if (!originAllowed) {
      res.writeHead(403, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ error: 'Origin not allowed' }));
      return;
    }

    if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return; }

    const url = new URL(req.url || '/', `http://localhost:${API_PORT}`);
    const send = (code: number, data: any) => {
      res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify(data));
    };
    const suppliedToken = String(
      req.headers.authorization?.replace(/^Bearer\s+/i, '')
      || req.headers['x-lingdong-token']
      || '',
    );
    const expected = Buffer.from(deps.controlToken);
    const supplied = Buffer.from(suppliedToken);
    const authenticated = expected.length > 0
      && expected.length === supplied.length
      && timingSafeEqual(expected, supplied);
    if (url.pathname !== '/api/ping' && !authenticated) {
      send(401, { error: 'Authentication required' });
      return;
    }
    if (req.method === 'GET') {
      try {
        switch (url.pathname) {
          case '/api/ping':
            send(200, { ok: true });
            return;
          case '/api/get-state':
            send(200, deps.getCachedRendererState() || { error: '渲染器状态未就绪' });
            return;
          case '/api/set-view-mode':
            if (!deps.mainWindow) { send(503, { error: '窗口未就绪' }); return; }
            deps.mainWindow.webContents.send('api-command', { action: 'setViewMode', payload: { mode: url.searchParams.get('mode') || '' } });
            send(200, { ok: true });
            return;
          case '/api/fit-screen':
            if (!deps.mainWindow) { send(503, { error: '窗口未就绪' }); return; }
            deps.mainWindow.webContents.send('api-command', { action: 'fitScreen', payload: {} });
            send(200, { ok: true });
            return;
          case '/api/execute-js':
            if (!deps.allowUnsafeEval) { send(404, { error: 'Endpoint disabled' }); return; }
            if (!deps.mainWindow) { send(503, { error: '窗口未就绪' }); return; }
            {
              const code = url.searchParams.get('code') || '';
              if (!code) { send(400, { error: '缺少 code' }); return; }
              deps.mainWindow.webContents.executeJavaScript(code, true)
                .then(result => send(200, { ok: true, result }))
                .catch((e: any) => send(500, { error: e.message }));
              return;
            }
        }
      } catch (e: any) {
        send(500, { error: e.message });
        return;
      }
    }
    if (req.method !== 'POST') { send(405, { error: 'Method not allowed' }); return; }

    const chunks: Buffer[] = [];
    req.on('data', (chunk: Buffer) => chunks.push(chunk));
    req.on('end', () => {
      const body = Buffer.concat(chunks).toString('utf8');
      let payload: any = {};
      try { payload = JSON.parse(body); } catch (e) { console.debug('[API] parse body error:', e); }

      try {
        switch (url.pathname) {
          // ─── Health ───
          case '/api/ping':
            send(200, { ok: true });
            break;

          // ─── Script operations ───
          case '/api/load-script':
            if (!deps.mainWindow) { send(503, { error: '窗口未就绪' }); return; }
            deps.mainWindow.webContents.send('api-command', { action: 'loadScript', payload: payload.script });
            send(200, { ok: true });
            break;

          case '/api/add-node':
            if (!deps.mainWindow) { send(503, { error: '窗口未就绪' }); return; }
            if (!payload.type) { send(400, { error: '缺少 type' }); return; }
            deps.mainWindow.webContents.send('api-command', {
              action: 'addNode', payload: { type: payload.type, x: payload.x, y: payload.y, params: payload.params, id: 'n_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 6) },
            });
            send(200, { ok: true });
            break;

          case '/api/update-node':
            if (!deps.mainWindow) { send(503, { error: '窗口未就绪' }); return; }
            deps.mainWindow.webContents.send('api-command', { action: 'updateNode', payload: { id: payload.id, updates: payload.updates } });
            send(200, { ok: true });
            break;

          case '/api/update-node-params':
            if (!deps.mainWindow) { send(503, { error: '窗口未就绪' }); return; }
            if (!payload.id || !payload.params) { send(400, { error: '缺少 id 或 params' }); return; }
            deps.mainWindow.webContents.send('api-command', { action: 'updateNodeParams', payload: { id: payload.id, params: payload.params } });
            send(200, { ok: true });
            break;

          case '/api/delete-nodes':
            if (!deps.mainWindow) { send(503, { error: '窗口未就绪' }); return; }
            deps.mainWindow.webContents.send('api-command', { action: 'deleteNodes', payload: { ids: payload.ids } });
            send(200, { ok: true });
            break;

          case '/api/add-edge':
            if (!deps.mainWindow) { send(503, { error: '窗口未就绪' }); return; }
            deps.mainWindow.webContents.send('api-command', { action: 'addEdge', payload });
            send(200, { ok: true });
            break;

          case '/api/delete-edges':
            if (!deps.mainWindow) { send(503, { error: '窗口未就绪' }); return; }
            deps.mainWindow.webContents.send('api-command', { action: 'deleteEdges', payload: { ids: payload.ids } });
            send(200, { ok: true });
            break;

          case '/api/check-params':
            if (!payload.nodes) { send(400, { error: '缺少 nodes' }); return; }
            try {
              const results: any[] = payload.nodes.map((n: any) => {
                const required: Record<string, string[]> = {
                  click: ['x', 'y'],
                  input_text: ['text'],
                  wait: [],
                  image_click: ['targets'],
                  click_text: ['text'],
                  keyboard: ['keys'],
                  swipe: ['start_x', 'start_y', 'end_x', 'end_y'],
                  color_check: ['region', 'expected_color'],
                  region_wait: ['region'],
                  progress_wait: ['region'],
                  variable_set: ['name', 'value'],
                  tool_call: ['feature_name'],
                  sub_script: ['script_id'],
                };
                const missing = (required[n.type] || []).filter((p: string) =>
                  n.params?.[p] === undefined || n.params[p] === null || n.params[p] === '' ||
                  (Array.isArray(n.params[p]) && n.params[p].length === 0)
                );
                if (n.type === 'wait') {
                  const waitType = n.params?.wait_type || 'time';
                  if (waitType === 'time' && (n.params?.duration === undefined || n.params.duration === null || n.params.duration === '')) {
                    missing.push('duration');
                  } else if (waitType === 'window' && !n.params?.window_title) {
                    missing.push('window_title');
                  } else if (waitType === 'image' && !n.params?.image_path && !n.params?.template_name) {
                    missing.push('image_path');
                  }
                }
                return { id: n.id, type: n.type, missing, ok: missing.length === 0 };
              });
              send(200, { ok: true, results });
            } catch (e: any) { send(500, { error: e.message }); }
            break;

          case '/api/auto-layout':
            if (!deps.mainWindow) { send(503, { error: '窗口未就绪' }); return; }
            deps.mainWindow.webContents.send('api-command', { action: 'autoLayout', payload: {} });
            send(200, { ok: true });
            break;

          // ─── DSL ───
          case '/api/run-dsl':
            if (!deps.mainWindow) { send(503, { error: '窗口未就绪' }); return; }
            if (!payload.dsl) { send(400, { error: '缺少 dsl' }); return; }
            (async () => {
              const scriptPath = path.join(getBaseDir(), 'tools', 'dsl_to_flow.py');
              const dslProc = await spawnPythonOneShot([scriptPath], { cwd: getBaseDir(), stdio: ['pipe', 'pipe', 'pipe'] });
              const out: Buffer[] = [];
              const errOut: Buffer[] = [];
              let sent = false;
              const sendOnce = (code: number, data: any) => { if (!sent) { sent = true; send(code, data); } };
              dslProc.stdout!.on('data', (c: Buffer) => out.push(c));
              dslProc.stderr!.on('data', (c: Buffer) => errOut.push(c));
              dslProc.on('close', () => {
                const dslStdout = Buffer.concat(out).toString('utf8');
                try {
                  const flow = JSON.parse(dslStdout);
                  deps.mainWindow!.webContents.send('api-command', { action: 'loadScript', payload: flow });
                  sendOnce(200, { ok: true, flow });
                } catch { sendOnce(500, { error: 'DSL 转换失败', raw: dslStdout }); }
              });
              dslProc.on('error', (e: Error) => sendOnce(500, { error: e.message }));
              dslProc.stdin!.write(payload.dsl);
              dslProc.stdin!.end();
            })();
            break;

          case '/api/generate-dsl':
            if (!deps.mainWindow) { send(503, { error: '窗口未就绪' }); return; }
            if (!payload.description) { send(400, { error: '缺少 description' }); return; }
            (async () => {
              const result = await callLLM({
                apiKey: payload.apiKey || '',
                messages: [
                  { role: 'system', content: 'You are a desktop automation script generator. Output ONLY Lobster DSL lines, one per line. No markdown, no explanation.\n\nAvailable commands:\nCLICK x y — click coordinate\nTYPE text — type text\nWAIT N — wait N seconds\nLAUNCH program — launch program (use Chinese names like 记事本)\nHOTKEY keys — press hotkey, e.g. HOTKEY ctrl+c\nFOCUS title — focus window\nSCREENSHOT — capture screen\nSCROLL up/down — scroll\n\nExample: "5秒后打开记事本" →\nWAIT 5\nLAUNCH 记事本\n\nIMPORTANT: Follow the user timing exactly. If they say "X秒后" (after X seconds), add a WAIT N step before the next action. Do NOT wrap arguments in quotes. Output ONLY the DSL.' },
                  { role: 'user', content: payload.description },
                ],
                apiBase: payload.apiBase,
                model: payload.model,
              });
              const dsl = result.choices?.[0]?.message?.content?.trim();
              if (dsl) {
                send(200, { ok: true, dsl });
              } else { send(500, { error: 'DSL 生成为空' }); }
            })();
            break;

          case '/api/create-flow': {
            if (!deps.mainWindow) { send(503, { error: '窗口未就绪' }); return; }
            (async () => {
              let sent = false;
              const sendOnce = (code: number, data: any) => { if (!sent) { sent = true; send(code, data); } };
              let dslText = payload.dsl;
              if (!dslText && payload.description) {
                const llmResult = await callLLM({
                  apiKey: payload.apiKey || '',
                  messages: [
                    { role: 'system', content: 'You are a desktop automation script generator. Output ONLY Lobster DSL lines, one per line. No markdown, no explanation.\n\nAvailable commands:\nCLICK x y — click coordinate\nTYPE text — type text\nWAIT N — wait N seconds\nLAUNCH program — launch program (use Chinese names like 记事本)\nHOTKEY keys — press hotkey, e.g. HOTKEY ctrl+c\nFOCUS title — focus window\nSCREENSHOT — capture screen\nSCROLL up/down — scroll\n\nExample: "5秒后打开记事本" →\nWAIT 5\nLAUNCH 记事本\n\nIMPORTANT: Follow the user timing exactly. If they say "X秒后" (after X seconds), add a WAIT N step before the next action. Do NOT wrap arguments in quotes. Output ONLY the DSL.' },
                    { role: 'user', content: payload.description },
                  ],
                  apiBase: payload.apiBase,
                  model: payload.model,
                });
                let raw = llmResult.choices?.[0]?.message?.content?.trim() || '';
                // Strip markdown code block fences if present (```dsl ... ``` or just ``` ... ```)
                raw = raw.replace(/^```(?:dsl)?\s*/i, '').replace(/```\s*$/i, '').trim();
                dslText = raw;
                if (!dslText) { sendOnce(500, { error: 'DSL 生成为空' }); return; }
              }
              if (dslText) {
                const scriptPath = path.join(getBaseDir(), 'tools', 'dsl_to_flow.py');
                const proc = await spawnPythonOneShot([scriptPath], { cwd: getBaseDir(), stdio: ['pipe', 'pipe', 'pipe'] });
                const out: Buffer[] = [];
                proc.stdout!.on('data', (c: Buffer) => out.push(c));
                proc.on('close', () => {
                  const stdout = Buffer.concat(out).toString('utf8');
                  try {
                    const flow = JSON.parse(stdout);
                    deps.mainWindow!.webContents.send('api-command', { action: 'loadScript', payload: flow });
                    sendOnce(200, { ok: true, flow });
                  } catch (e: any) { sendOnce(500, { error: 'JSON parse: ' + e.message, raw: stdout.slice(0, 500) }); }
                });
                proc.on('error', (e: Error) => sendOnce(500, { error: e.message }));
                proc.stdin!.write(dslText);
                proc.stdin!.end();
              } else {
                sendOnce(400, { error: '需要 dsl 或 description' });
              }
            })();
            break;
          }

          // ─── Engine control ───
          case '/api/engine/start':
            if (!deps.mainWindow) { send(503, { error: '窗口未就绪' }); return; }
            if (!payload.script) { send(400, { error: '缺少 script' }); return; }
            deps.mainWindow.webContents.send('api-command', { action: 'start', payload: { script: payload.script } });
            send(200, { ok: true });
            break;

          case '/api/engine/pause':
            if (!deps.mainWindow) { send(503, { error: '窗口未就绪' }); return; }
            deps.mainWindow.webContents.send('api-command', { action: 'pause', payload: {} });
            send(200, { ok: true });
            break;

          case '/api/engine/resume':
            if (!deps.mainWindow) { send(503, { error: '窗口未就绪' }); return; }
            deps.mainWindow.webContents.send('api-command', { action: 'resume', payload: {} });
            send(200, { ok: true });
            break;

          // ─── Screenshot ───
          case '/api/screenshot/capture':
            deps.captureScreenshot().then(r => send(200, r)).catch((e: any) => send(500, { error: e.message }));
            break;

          case '/api/screenshot/select-region':
            deps.selectRegion().then(r => send(200, r)).catch((e: any) => send(500, { error: e.message }));
            break;

          case '/api/ocr/region':
            deps.ocrRecognizeRegion(payload).then(r => send(200, r)).catch((e: any) => send(500, { error: e.message }));
            break;

          // ─── Wheel Select ───
          case '/api/wheel-select':
            deps.wheelSelect(payload).then(r => send(200, r)).catch((e: any) => send(500, { error: e.message }));
            break;

          // ─── Macro ───
          case '/api/macro/start-recording': {
            if (!deps.mainWindow) { send(503, { error: '窗口未就绪' }); return; }
            const recorder = deps.ensureMacroRecorder();
            try {
              if (recorder?.stdin) {
                recorder.stdin.write(JSON.stringify({
                  type: 'start', name: payload.name || `recording_${Date.now()}`,
                  include_mouse: payload.include_mouse ?? true, include_keys: payload.include_keys ?? true,
                }) + '\n');
                deps.setMacroRecording(true);
              }
            } catch (e) { console.debug('[macro] start recording error:', e); }
            send(200, { success: Boolean(recorder?.stdin) });
            break;
          }

          case '/api/macro/stop-recording':
            try {
              const recorder = deps.getMacroRecorderProcess();
              if (recorder?.stdin) {
                recorder.stdin.write(JSON.stringify({ type: 'stop' }) + '\n');
                deps.setMacroRecording(false);
              }
            } catch (e) { console.debug('[macro] stop recording error:', e); }
            send(200, { success: true });
            break;

          case '/api/macro/play':
            (async () => {
              const scriptPath = path.join(getBaseDir(), 'python', 'tools', 'macro_play_cli.py');
              const proc = await spawnPythonOneShot([scriptPath, payload.name || ''], {
                cwd: getBaseDir(), stdio: ['pipe', 'pipe', 'pipe'],
              });
              const _out: Buffer[] = []; const _err: Buffer[] = [];
              proc.stdout!.on('data', (chunk: Buffer) => _out.push(chunk));
              proc.stderr!.on('data', (chunk: Buffer) => _err.push(chunk));
              const result = await new Promise<any>((resolve) => {
                proc.on('close', () => {
                  const stdout = Buffer.concat(_out).toString('utf8');
                  const stderr = Buffer.concat(_err).toString('utf8');
                  if (stdout.trim()) { try { resolve(JSON.parse(stdout)); } catch { resolve({ success: false, error: '解析失败' }); } }
                  else { resolve({ success: false, error: stderr.trim() || '播放失败' }); }
                });
                proc.on('error', (err) => resolve({ success: false, error: err.message }));
              });
              send(200, result);
            })();
            break;

          case '/api/macro/list': {
            const macrosDir = path.join(getBaseDir(), 'python', 'macros');
            try {
              const files = fs.readdirSync(macrosDir).filter(f => f.endsWith('.json'));
              const macros = files.map(file => {
                const filePath = path.join(macrosDir, file);
                try {
                  const content = JSON.parse(fs.readFileSync(filePath, 'utf-8'));
                  return { name: file.replace('.json', ''), file, events: content.events?.length || 0, duration: content.duration || 0, created: content.created || '' };
                } catch {
                  return { name: file.replace('.json', ''), file, events: 0, duration: 0, created: '' };
                }
              });
              send(200, { success: true, result: macros.sort((a, b) => b.created.localeCompare(a.created)) });
            } catch { send(200, { success: true, result: [] }); }
            break;
          }

          case '/api/macro/delete':
            if (!payload.name) { send(400, { error: '缺少 name' }); return; }
            { const macrosDir = path.join(getBaseDir(), 'python', 'macros');
              try { const filePath = resolveMacroPath(macrosDir, payload.name); fs.unlinkSync(filePath); send(200, { ok: true }); }
              catch (error) { send(404, { error: error instanceof Error ? error.message : '文件不存在' }); } }
            break;

          case '/api/macro/get-events':
            if (!payload.name) { send(400, { error: '缺少 name' }); return; }
            { const macrosDir = path.join(getBaseDir(), 'python', 'macros');
              try { const filePath = resolveMacroPath(macrosDir, payload.name); const content = JSON.parse(fs.readFileSync(filePath, 'utf-8')); send(200, { ok: true, result: content }); }
              catch (error) { send(404, { error: error instanceof Error ? error.message : '读取失败' }); } }
            break;

          case '/api/macro/recording-status':
            send(200, { recording: deps.getMacroRecording() });
            break;

          // ─── Features (MCP) ───
          case '/api/features/list':
            (async () => {
              const scriptPath = path.join(getBaseDir(), 'python', 'tools', 'feature_list.py');
              const proc = await spawnPythonOneShot([scriptPath], { cwd: getBaseDir(), stdio: ['pipe', 'pipe', 'pipe'] });
              const _out: Buffer[] = []; const _err: Buffer[] = [];
              proc.stdout!.on('data', (chunk: Buffer) => _out.push(chunk));
              proc.stderr!.on('data', (chunk: Buffer) => _err.push(chunk));
              const result = await new Promise<any>((resolve) => {
                proc.on('close', () => {
                  const stdout = Buffer.concat(_out).toString('utf8');
                  const stderr = Buffer.concat(_err).toString('utf8');
                  if (stdout.trim()) { try { resolve(JSON.parse(stdout)); } catch { resolve({ success: false, error: 'JSON 解析失败', raw: stdout }); } }
                  else { resolve({ success: false, error: stderr.trim() || '获取功能列表失败' }); }
                });
                proc.on('error', (err) => resolve({ error: err.message }));
              });
              send(200, result);
            })();
            break;

          case '/api/features/reload':
            stopFeatureProcess();
            send(200, { ok: true, reloaded: true });
            break;

          case '/api/features/call':
            (async () => {
              const result = await callFeature(payload.name, payload.params || {});
              send(200, result);
            })();
            break;

          // ─── AI ───
          case '/api/ai/generate':
            if (!payload.apiKey || !payload.messages) { send(400, { error: '缺少 apiKey 或 messages' }); return; }
            callLLM(payload).then(r => send(200, r)).catch((e: any) => send(500, { error: e.message }));
            break;

          // ─── UI ───
          case '/api/set-view-mode':
            if (!deps.mainWindow) { send(503, { error: '窗口未就绪' }); return; }
            deps.mainWindow.webContents.send('api-command', { action: 'setViewMode', payload: { mode: payload.mode } });
            send(200, { ok: true });
            break;

          case '/api/set-ui':
            if (!deps.mainWindow) { send(503, { error: '窗口未就绪' }); return; }
            if (!payload.updates) { send(400, { error: '缺少 updates' }); return; }
            deps.mainWindow.webContents.send('api-command', { action: 'setUI', payload: payload.updates });
            send(200, { ok: true });
            break;

          case '/api/command':
            if (!deps.mainWindow) { send(503, { error: '窗口未就绪' }); return; }
            if (!Array.isArray(payload.commands)) { send(400, { error: '缺少 commands 数组' }); return; }
            deps.mainWindow.webContents.send('api-command', { action: 'batchCommands', payload: payload.commands });
            send(200, { ok: true });
            break;

          case '/api/new-script':
            if (!deps.mainWindow) { send(503, { error: '窗口未就绪' }); return; }
            deps.mainWindow.webContents.send('api-command', { action: 'newScript', payload: {} });
            send(200, { ok: true });
            break;

          case '/api/fit-screen':
            if (!deps.mainWindow) { send(503, { error: '窗口未就绪' }); return; }
            deps.mainWindow.webContents.send('api-command', { action: 'fitScreen', payload: {} });
            send(200, { ok: true });
            break;

          case '/api/get-state':
            send(200, deps.getCachedRendererState() || { error: '渲染器状态未就绪' });
            break;

          case '/api/save-script':
            if (!deps.mainWindow) { send(503, { error: '窗口未就绪' }); return; }
            deps.mainWindow.webContents.send('api-command', { action: 'saveScript', payload: {} });
            send(200, { ok: true });
            break;

          case '/api/set-script-name':
            if (!deps.mainWindow) { send(503, { error: '窗口未就绪' }); return; }
            deps.mainWindow.webContents.send('api-command', { action: 'setScriptName', payload: { name: payload.name } });
            send(200, { ok: true });
            break;

          case '/api/save-template':
            if (!deps.mainWindow) { send(503, { error: '窗口未就绪' }); return; }
            if (!payload.name) { send(400, { error: '缺少 name' }); return; }
            try {
              const templatesDir = path.join(getBaseDir(), 'templates');
              if (!fs.existsSync(templatesDir)) fs.mkdirSync(templatesDir, { recursive: true });
              const safeName = payload.name.replace(/[\\/:*?"<>|]/g, '_');
              const filePath = path.join(templatesDir, safeName + '.flow');
              const scriptState = deps.getCachedRendererState() || null;
              const flowContent = JSON.stringify(scriptState, null, 2);
              fs.writeFileSync(filePath, flowContent, 'utf-8');
              send(200, { ok: true, path: filePath });
            } catch (e: any) { console.error('[API] save-template write error:', e.message); send(500, { error: '保存失败' }); }
            break;

          case '/api/list-templates': {
            const templatesDir = path.join(getBaseDir(), 'templates');
            if (!fs.existsSync(templatesDir)) { send(200, { ok: true, templates: [] }); return; }
            fs.readdir(templatesDir, (err: any, files: string[]) => {
              if (err) { send(200, { ok: true, templates: [] }); return; }
              const templates = files.filter(f => f.endsWith('.flow')).map(f => {
                const filePath = path.join(templatesDir, f);
                let displayName = f.replace(/\.flow$/, '');
                try { const content = JSON.parse(fs.readFileSync(filePath, 'utf-8')); if (content.name) displayName = content.name; } catch (e) { console.debug('[API] parse template error:', e); }
                return { name: displayName, path: filePath };
              });
              send(200, { ok: true, templates });
            });
            break;
          }

          // ─── Item marks save/load ───
          case '/api/marks/save':
            if (!payload.marks) { send(400, { error: '缺少 marks' }); return; }
            try {
              const marksDir = path.join(getBaseDir(), 'data');
              if (!fs.existsSync(marksDir)) fs.mkdirSync(marksDir, { recursive: true });
              fs.writeFileSync(path.join(marksDir, 'item-marks.json'), JSON.stringify(payload.marks, null, 2), 'utf-8');
              send(200, { ok: true });
            } catch (e: any) { send(500, { error: e.message }); }
            break;

          case '/api/marks/load':
            try {
              const marksPath = path.join(getBaseDir(), 'data', 'item-marks.json');
              if (fs.existsSync(marksPath)) {
                const data = fs.readFileSync(marksPath, 'utf-8');
                send(200, { ok: true, marks: JSON.parse(data) });
              } else {
                send(200, { ok: true, marks: [] });
              }
            } catch (e: any) { send(500, { error: e.message }); }
            break;

          // ─── Undo/Redo ───
          case '/api/undo':
            if (!deps.mainWindow) { send(503, { error: '窗口未就绪' }); return; }
            deps.mainWindow.webContents.send('api-command', { action: 'undo', payload: {} });
            send(200, { ok: true });
            break;

          case '/api/redo':
            if (!deps.mainWindow) { send(503, { error: '窗口未就绪' }); return; }
            deps.mainWindow.webContents.send('api-command', { action: 'redo', payload: {} });
            send(200, { ok: true });
            break;

          // ─── Selection ───
          case '/api/select-all':
          case '/api/clear-selection':
          case '/api/copy-selected':
          case '/api/cut-selected':
          case '/api/paste':
          case '/api/duplicate-nodes':
            if (!deps.mainWindow) { send(503, { error: '窗口未就绪' }); return; }
            deps.mainWindow.webContents.send('api-command', {
              action: url.pathname.replace('/api/', ''),
              payload: payload.ids ? { ids: payload.ids } : {},
            });
            send(200, { ok: true });
            break;

          // ─── Execute JavaScript in renderer ───
          case '/api/execute-js':
            if (!deps.allowUnsafeEval) { send(404, { error: 'Endpoint disabled' }); return; }
            if (!deps.mainWindow) { send(503, { error: '窗口未就绪' }); return; }
            if (!payload.code) { send(400, { error: '缺少 code' }); return; }
            (async () => {
              try {
                const result = await deps.mainWindow!.webContents.executeJavaScript(payload.code, true);
                send(200, { ok: true, result });
              } catch (e: any) {
                send(500, { error: e.message });
              }
            })();
            break;

          // ─── MCP over HTTP (Claude Code 自动注册) ───
          case '/mcp':
            (async () => {
              const accept = req.headers['accept'] || '';
              const { status, contentType, body: mcpBody } = await handleMCPRequest(body, accept);
              if (mcpBody) {
                res.writeHead(status, { 'Content-Type': contentType });
                res.end(mcpBody);
              } else {
                res.writeHead(status);
                res.end();
              }
            })();
            break;

          // ─── AI Agent ───
          case '/api/agent-start':
            if (!payload.goal) { send(400, { error: '缺少 goal' }); return; }
            deps.startAgent(payload.goal, payload.config || {}, payload.supervise !== false);
            send(200, { ok: true });
            break;

          default:
            send(404, { error: `未知端点: ${url.pathname}` });
        }
      } catch (e: any) {
        console.error('[API] Handler error:', e);
        send(500, { error: e.message });
      }
    });
  });

  apiServer.listen(API_PORT, '127.0.0.1', () => {
    console.log(`[API] HTTP server listening on http://127.0.0.1:${API_PORT}`);
  });

  return apiServer;
}
