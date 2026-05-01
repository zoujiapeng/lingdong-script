import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import { nanoid } from 'nanoid';
import {
  FlowScript,
  FlowNode,
  FlowEdge,
  NodeType,
  UIState,
  ExecutionState,
  CanvasTransform,
  ScriptMeta,
  LogEntry,
} from '../types';
import { NODE_DEFINITIONS } from '../utils/nodeDefinitions';
import { createFlowExecutor, ExecutionController } from './executor';

const DEFAULT_SETTINGS = {
  default_delay: 0.5,
  error_handling: 'stop' as const,
  max_retries: 3,
  retry_delay: 1,
  log_level: 'info' as const,
};

const DEFAULT_UI_STATE: UIState = {
  canvas_transform: { x: 0, y: 0, scale: 1 },
  selection: { nodes: [], edges: [] },
  connecting: { active: false },
  sidebar_width: 260,
  property_width: 320,
  show_grid: true,
  snap_to_grid: true,
  grid_size: 20,
  dark_mode: false,
};

// --- Preset Templates ---

interface ScriptTemplate {
  id: string;
  name: string;
  description: string;
  category: string;
  createScript: () => FlowScript;
}

const PRESET_TEMPLATES: ScriptTemplate[] = [
  {
    id: 'desktop-monitor',
    name: '桌面图标监控',
    category: 'image',
    description: '循环检测桌面多张目标图片，按优先级点击第一个匹配的',
    createScript: () => {
      const ids = { start: nanoid(), loop: nanoid(), img1: nanoid(), img2: nanoid(), img3: nanoid() };
      return {
        meta: { id: nanoid(), name: '桌面图标监控', description: '循环检测桌面目标图片，按优先级点击', created_at: new Date().toISOString(), updated_at: new Date().toISOString(), version: '1.0.0' },
        nodes: [
          { id: ids.start, type: 'start', x: 360, y: 40, params: { label: '开始' }, status: 'idle' },
          { id: ids.loop, type: 'loop', x: 360, y: 160, params: { loop_type: 'infinite' }, status: 'idle', label: '循环检测' },
          { id: ids.img1, type: 'image_click', x: 80, y: 340, params: { template_path: '', similarity: 85, timeout: 2, method: 'single', offset_x: 0, offset_y: 0, delay_after: 0.3, multi_scale: true, match_all: false, description: '优先级1' }, label: '目标图片 1', status: 'idle' },
          { id: ids.img2, type: 'image_click', x: 320, y: 340, params: { template_path: '', similarity: 85, timeout: 0.5, method: 'single', offset_x: 0, offset_y: 0, delay_after: 0.3, multi_scale: true, match_all: false, description: '优先级2' }, label: '目标图片 2', status: 'idle' },
          { id: ids.img3, type: 'image_click', x: 560, y: 340, params: { template_path: '', similarity: 85, timeout: 0.5, method: 'single', offset_x: 0, offset_y: 0, delay_after: 0.3, multi_scale: true, match_all: false, description: '优先级3' }, label: '目标图片 3', status: 'idle' },
        ],
        edges: [
          { id: nanoid(), from_node: ids.start, from_port: 'output', to_node: ids.loop, to_port: 'input' },
          { id: nanoid(), from_node: ids.loop, from_port: 'loop_body', to_node: ids.img1, to_port: 'input' },
          { id: nanoid(), from_node: ids.img1, from_port: 'fail', to_node: ids.img2, to_port: 'input' },
          { id: nanoid(), from_node: ids.img2, from_port: 'fail', to_node: ids.img3, to_port: 'input' },
        ],
        variables: {},
        settings: { ...DEFAULT_SETTINGS, error_handling: 'continue' },
      };
    },
  },
  {
    id: 'login-automation',
    name: '登录自动化',
    category: 'app',
    description: '自动打开登录页，输入账号密码，点击登录，截图确认结果',
    createScript: () => {
      const ids = { start: nanoid(), click: nanoid(), inputUser: nanoid(), inputPass: nanoid(), clickLogin: nanoid(), wait: nanoid(), screenshot: nanoid(), cond: nanoid(), endSucc: nanoid(), endFail: nanoid() };
      return {
        meta: { id: nanoid(), name: '登录自动化', description: '自动登录流程', created_at: new Date().toISOString(), updated_at: new Date().toISOString(), version: '1.0.0' },
        nodes: [
          { id: ids.start, type: 'start', x: 360, y: 20, params: { label: '开始' }, status: 'idle' },
          { id: ids.click, type: 'click', x: 360, y: 120, params: { x: 500, y: 400, method: 'single', delay_after: 1.0, description: '点击用户名输入框' }, label: '点击用户名框', status: 'idle' },
          { id: ids.inputUser, type: 'input_text', x: 360, y: 220, params: { text: 'admin', click_before: false, clear_before: true, delay_after: 0.5 }, label: '输入用户名', status: 'idle' },
          { id: ids.inputPass, type: 'input_text', x: 360, y: 320, params: { text: 'password123', click_before: true, clear_before: true, delay_after: 0.5 }, label: '输入密码', status: 'idle' },
          { id: ids.clickLogin, type: 'click', x: 360, y: 420, params: { x: 500, y: 550, method: 'single', delay_after: 2.0, description: '点击登录按钮' }, label: '点击登录', status: 'idle' },
          { id: ids.wait, type: 'wait', x: 360, y: 520, params: { wait_type: 'time', duration: 3, description: '等待页面加载' }, label: '等待加载', status: 'idle' },
          { id: ids.screenshot, type: 'screenshot', x: 360, y: 620, params: { description: '登录结果截图' }, label: '截图结果', status: 'idle' },
          { id: ids.cond, type: 'condition', x: 360, y: 720, params: { condition_type: 'always_true', description: '模拟判断' }, label: '判断结果', status: 'idle' },
          { id: ids.endSucc, type: 'end', x: 240, y: 820, params: { label: '登录成功' }, label: '登录成功', status: 'idle' },
          { id: ids.endFail, type: 'end', x: 480, y: 820, params: { label: '登录失败' }, label: '登录失败', status: 'idle' },
        ],
        edges: [
          { id: nanoid(), from_node: ids.start, from_port: 'output', to_node: ids.click, to_port: 'input' },
          { id: nanoid(), from_node: ids.click, from_port: 'output', to_node: ids.inputUser, to_port: 'input' },
          { id: nanoid(), from_node: ids.inputUser, from_port: 'output', to_node: ids.inputPass, to_port: 'input' },
          { id: nanoid(), from_node: ids.inputPass, from_port: 'output', to_node: ids.clickLogin, to_port: 'input' },
          { id: nanoid(), from_node: ids.clickLogin, from_port: 'output', to_node: ids.wait, to_port: 'input' },
          { id: nanoid(), from_node: ids.wait, from_port: 'output', to_node: ids.screenshot, to_port: 'input' },
          { id: nanoid(), from_node: ids.screenshot, from_port: 'output', to_node: ids.cond, to_port: 'input' },
          { id: nanoid(), from_node: ids.cond, from_port: 'yes', to_node: ids.endSucc, to_port: 'input' },
          { id: nanoid(), from_node: ids.cond, from_port: 'no', to_node: ids.endFail, to_port: 'input' },
        ],
        variables: {},
        settings: { ...DEFAULT_SETTINGS },
      };
    },
  },
  {
    id: 'form-filler',
    name: '表单填写器',
    category: 'app',
    description: '循环填写多组数据到表单，每次填写后截图保存',
    createScript: () => {
      const ids = { start: nanoid(), loop: nanoid(), clickField1: nanoid(), input1: nanoid(), clickField2: nanoid(), input2: nanoid(), submit: nanoid(), wait: nanoid(), screenshot: nanoid(), end: nanoid() };
      return {
        meta: { id: nanoid(), name: '表单填写器', description: '自动循环填写表单', created_at: new Date().toISOString(), updated_at: new Date().toISOString(), version: '1.0.0' },
        nodes: [
          { id: ids.start, type: 'start', x: 360, y: 20, params: { label: '开始' }, status: 'idle' },
          { id: ids.loop, type: 'loop', x: 360, y: 120, params: { loop_type: 'count', count: 5, description: '填写5组数据' }, label: '循环5次', status: 'idle' },
          { id: ids.clickField1, type: 'click', x: 80, y: 280, params: { x: 400, y: 350, method: 'single', delay_after: 0.5, description: '点击第一个输入框' }, label: '点击字段1', status: 'idle' },
          { id: ids.input1, type: 'input_text', x: 80, y: 380, params: { text: '测试数据A', click_before: false, clear_before: true, delay_after: 0.3 }, label: '输入字段1', status: 'idle' },
          { id: ids.clickField2, type: 'click', x: 80, y: 480, params: { x: 400, y: 420, method: 'single', delay_after: 0.5, description: '点击第二个输入框' }, label: '点击字段2', status: 'idle' },
          { id: ids.input2, type: 'input_text', x: 80, y: 580, params: { text: '测试数据B', click_before: false, clear_before: true, delay_after: 0.3 }, label: '输入字段2', status: 'idle' },
          { id: ids.submit, type: 'click', x: 360, y: 680, params: { x: 500, y: 500, method: 'single', delay_after: 1.0, description: '点击提交按钮' }, label: '提交', status: 'idle' },
          { id: ids.wait, type: 'wait', x: 360, y: 780, params: { wait_type: 'time', duration: 2 }, label: '等待', status: 'idle' },
          { id: ids.screenshot, type: 'screenshot', x: 560, y: 780, params: { description: '提交结果截图' }, label: '截图', status: 'idle' },
          { id: ids.end, type: 'end', x: 360, y: 900, params: { label: '结束' }, status: 'idle' },
        ],
        edges: [
          { id: nanoid(), from_node: ids.start, from_port: 'output', to_node: ids.loop, to_port: 'input' },
          { id: nanoid(), from_node: ids.loop, from_port: 'loop_body', to_node: ids.clickField1, to_port: 'input' },
          { id: nanoid(), from_node: ids.clickField1, from_port: 'output', to_node: ids.input1, to_port: 'input' },
          { id: nanoid(), from_node: ids.input1, from_port: 'output', to_node: ids.clickField2, to_port: 'input' },
          { id: nanoid(), from_node: ids.clickField2, from_port: 'output', to_node: ids.input2, to_port: 'input' },
          { id: nanoid(), from_node: ids.input2, from_port: 'output', to_node: ids.submit, to_port: 'input' },
          { id: nanoid(), from_node: ids.submit, from_port: 'output', to_node: ids.wait, to_port: 'input' },
          { id: nanoid(), from_node: ids.wait, from_port: 'output', to_node: ids.screenshot, to_port: 'input' },
          { id: nanoid(), from_node: ids.screenshot, from_port: 'output', to_node: ids.loop, to_port: 'loop_end' },
          { id: nanoid(), from_node: ids.loop, from_port: 'loop_end', to_node: ids.end, to_port: 'input' },
        ],
        variables: {},
        settings: { ...DEFAULT_SETTINGS },
      };
    },
  },
  {
    id: 'app-launcher',
    name: '应用启动器',
    category: 'app',
    description: '双击桌面图标启动应用 → 等待窗口 → 执行快捷键操作 → 关闭应用',
    createScript: () => {
      const ids = { start: nanoid(), clickIcon: nanoid(), waitWindow: nanoid(), keyboard: nanoid(), wait2: nanoid(), screenshot: nanoid(), end: nanoid() };
      return {
        meta: { id: nanoid(), name: '应用启动器', description: '启动应用并执行操作', created_at: new Date().toISOString(), updated_at: new Date().toISOString(), version: '1.0.0' },
        nodes: [
          { id: ids.start, type: 'start', x: 360, y: 20, params: { label: '开始' }, status: 'idle' },
          { id: ids.clickIcon, type: 'click', x: 360, y: 120, params: { x: 200, y: 300, method: 'double', delay_after: 3.0, description: '双击桌面图标' }, label: '双击启动', status: 'idle' },
          { id: ids.waitWindow, type: 'wait', x: 360, y: 220, params: { wait_type: 'window', window_title: '无标题 - 记事本', timeout: 10, description: '等待记事本窗口' }, label: '等待窗口', status: 'idle' },
          { id: ids.keyboard, type: 'keyboard', x: 360, y: 320, params: { keys: ['你好，世界！'], modifiers: [], delay_after: 0.5 }, label: '输入文字', status: 'idle' },
          { id: ids.wait2, type: 'wait', x: 360, y: 420, params: { wait_type: 'time', duration: 1 }, label: '等待', status: 'idle' },
          { id: ids.screenshot, type: 'screenshot', x: 360, y: 520, params: { description: '运行结果截图' }, label: '截图保存', status: 'idle' },
          { id: ids.end, type: 'end', x: 360, y: 620, params: { label: '结束' }, status: 'idle' },
        ],
        edges: [
          { id: nanoid(), from_node: ids.start, from_port: 'output', to_node: ids.clickIcon, to_port: 'input' },
          { id: nanoid(), from_node: ids.clickIcon, from_port: 'output', to_node: ids.waitWindow, to_port: 'input' },
          { id: nanoid(), from_node: ids.waitWindow, from_port: 'output', to_node: ids.keyboard, to_port: 'input' },
          { id: nanoid(), from_node: ids.keyboard, from_port: 'output', to_node: ids.wait2, to_port: 'input' },
          { id: nanoid(), from_node: ids.wait2, from_port: 'output', to_node: ids.screenshot, to_port: 'input' },
          { id: nanoid(), from_node: ids.screenshot, from_port: 'output', to_node: ids.end, to_port: 'input' },
        ],
        variables: {},
        settings: { ...DEFAULT_SETTINGS },
      };
    },
  },
  {
    id: 'screenshot-logger',
    name: '定时截图记录',
    category: 'system',
    description: '每隔一段时间自动截图并保存，用于监控或记录屏幕变化',
    createScript: () => {
      const ids = { start: nanoid(), loop: nanoid(), screenshot: nanoid(), wait: nanoid(), end: nanoid() };
      return {
        meta: { id: nanoid(), name: '定时截图记录', description: '定时自动截图', created_at: new Date().toISOString(), updated_at: new Date().toISOString(), version: '1.0.0' },
        nodes: [
          { id: ids.start, type: 'start', x: 360, y: 40, params: { label: '开始' }, status: 'idle' },
          { id: ids.loop, type: 'loop', x: 360, y: 140, params: { loop_type: 'infinite', description: '无限循环截图' }, label: '循环', status: 'idle' },
          { id: ids.screenshot, type: 'screenshot', x: 360, y: 260, params: { description: '定时截图' }, label: '截图', status: 'idle' },
          { id: ids.wait, type: 'wait', x: 360, y: 360, params: { wait_type: 'time', duration: 30, description: '间隔30秒' }, label: '等待30秒', status: 'idle' },
          { id: ids.end, type: 'end', x: 360, y: 480, params: { label: '结束' }, status: 'idle' },
        ],
        edges: [
          { id: nanoid(), from_node: ids.start, from_port: 'output', to_node: ids.loop, to_port: 'input' },
          { id: nanoid(), from_node: ids.loop, from_port: 'loop_body', to_node: ids.screenshot, to_port: 'input' },
          { id: nanoid(), from_node: ids.screenshot, from_port: 'output', to_node: ids.wait, to_port: 'input' },
          { id: nanoid(), from_node: ids.wait, from_port: 'output', to_node: ids.loop, to_port: 'loop_end' },
        ],
        variables: {},
        settings: { ...DEFAULT_SETTINGS, error_handling: 'continue' },
      };
    },
  },
];

function createTemplateScript(templateId: string): FlowScript {
  const tpl = PRESET_TEMPLATES.find((t) => t.id === templateId);
  return tpl ? tpl.createScript() : PRESET_TEMPLATES[0].createScript();
}

interface AppStore {
  // Script state
  script: FlowScript;
  recentScripts: ScriptMeta[];
  isDirty: boolean;

  // UI state
  ui: UIState;

  // Execution state
  execution: ExecutionState;

  // Clipboard
  clipboard: { nodes: FlowNode[]; edges: FlowEdge[] } | null;

  // Internal executor controller
  _executor: ExecutionController | null;

  // Actions - Script
  newScript: () => void;
  loadScript: (script: FlowScript) => void;
  loadTemplate: (templateId: string) => void;
  saveScript: () => void;
  setScriptName: (name: string) => void;

  // Actions - Nodes
  addNode: (type: NodeType, x: number, y: number) => string;
  updateNode: (id: string, updates: Partial<FlowNode>) => void;
  updateNodeParams: (id: string, params: any) => void;
  deleteNodes: (ids: string[]) => void;
  duplicateNodes: (ids: string[]) => void;
  moveNode: (id: string, x: number, y: number) => void;
  moveNodes: (ids: string[], dx: number, dy: number) => void;

  // Actions - Edges
  addEdge: (edge: Omit<FlowEdge, 'id'>) => void;
  deleteEdges: (ids: string[]) => void;

  // Actions - Selection
  selectNodes: (ids: string[], append?: boolean) => void;
  selectEdges: (ids: string[], append?: boolean) => void;
  clearSelection: () => void;
  selectAll: () => void;

  // Actions - Canvas
  setCanvasTransform: (t: Partial<CanvasTransform>) => void;
  fitToScreen: () => void;
  zoomIn: () => void;
  zoomOut: () => void;
  resetZoom: () => void;

  // Actions - Clipboard
  copySelected: () => void;
  paste: () => void;
  cutSelected: () => void;

  // Actions - History (undo/redo)
  undo: () => void;
  redo: () => void;

  // Actions - Execution
  startExecution: () => void;
  pauseExecution: () => void;
  resumeExecution: () => void;
  stopExecution: () => void;
  updateNodeStatus: (nodeId: string, status: FlowNode['status'], error?: string) => void;
  addLog: (entry: Omit<LogEntry, 'timestamp'>) => void;
  clearLog: () => void;

  // Actions - UI
  setUI: (updates: Partial<UIState>) => void;
  startConnecting: (nodeId: string, portId: string) => void;
  stopConnecting: () => void;
  setHoveredNode: (id?: string) => void;
  setHoveredEdge: (id?: string) => void;
  setDraggingNode: (id?: string) => void;

  // Auto layout
  autoLayout: () => void;
}

// History for undo/redo
const MAX_HISTORY = 50;
let history: { nodes: FlowNode[]; edges: FlowEdge[] }[] = [];
let historyIndex = -1;

function pushHistory(nodes: FlowNode[], edges: FlowEdge[]) {
  history = history.slice(0, historyIndex + 1);
  history.push({ nodes: JSON.parse(JSON.stringify(nodes)), edges: JSON.parse(JSON.stringify(edges)) });
  if (history.length > MAX_HISTORY) history.shift();
  historyIndex = history.length - 1;
}

export const useStore = create<AppStore>()(
  immer((set, get) => ({
    script: createTemplateScript('desktop-monitor'),
    recentScripts: [],
    isDirty: false,
    ui: DEFAULT_UI_STATE,
    execution: {
      status: 'idle',
      variables: {},
      log: [],
    },
    clipboard: null,
    _executor: null,

    newScript: () => {
      const s = createTemplateScript('desktop-monitor');
      history = [];
      historyIndex = -1;
      pushHistory(s.nodes, s.edges);
      set((state) => {
        state.script = s;
        state.isDirty = false;
        state.ui.selection = { nodes: [], edges: [] };
        state.execution = { status: 'idle', variables: {}, log: [] };
      });
    },

    loadTemplate: (templateId) => {
      const s = createTemplateScript(templateId);
      history = [];
      historyIndex = -1;
      pushHistory(s.nodes, s.edges);
      set((state) => {
        state.script = s;
        state.isDirty = false;
        state.ui.selection = { nodes: [], edges: [] };
        state.execution = { status: 'idle', variables: {}, log: [] };
      });
    },

    loadScript: (script) => {
      history = [];
      historyIndex = -1;
      pushHistory(script.nodes, script.edges);
      set((state) => {
        state.script = script;
        state.isDirty = false;
        state.ui.selection = { nodes: [], edges: [] };
      });
    },

    saveScript: async () => {
      set((state) => {
        state.script.meta.updated_at = new Date().toISOString();
        state.isDirty = false;
      });
      const script = get().script;
      const json = JSON.stringify(script, null, 2);

      const api = (window as any).electronAPI;
      if (api?.saveFileDialog) {
        const result = await api.saveFileDialog(`${script.meta.name}.json`);
        if (!result.canceled && result.filePath) {
          try {
            const fs = (window as any).require?.('fs');
            if (fs) {
              fs.writeFileSync(result.filePath, json, 'utf-8');
              return;
            }
          } catch {}
        }
      }
      // Browser fallback
      const blob = new Blob([json], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${script.meta.name}.json`;
      a.click();
      URL.revokeObjectURL(url);
    },

    setScriptName: (name) => {
      set((state) => {
        state.script.meta.name = name;
        state.isDirty = true;
      });
    },

    addNode: (type, x, y) => {
      const def = NODE_DEFINITIONS[type];
      const id = nanoid();
      const node: FlowNode = {
        id,
        type,
        x,
        y,
        params: { ...def.defaultParams },
        status: 'idle',
      };
      set((state) => {
        state.script.nodes.push(node);
        state.isDirty = true;
        state.ui.selection = { nodes: [id], edges: [] };
      });
      pushHistory(get().script.nodes, get().script.edges);
      return id;
    },

    updateNode: (id, updates) => {
      set((state) => {
        const n = state.script.nodes.find((n) => n.id === id);
        if (n) Object.assign(n, updates);
        state.isDirty = true;
      });
    },

    updateNodeParams: (id, params) => {
      set((state) => {
        const n = state.script.nodes.find((n) => n.id === id);
        if (n) Object.assign(n.params, params);
        state.isDirty = true;
      });
    },

    deleteNodes: (ids) => {
      set((state) => {
        state.script.nodes = state.script.nodes.filter((n) => !ids.includes(n.id));
        state.script.edges = state.script.edges.filter(
          (e) => !ids.includes(e.from_node) && !ids.includes(e.to_node)
        );
        state.ui.selection = { nodes: [], edges: [] };
        state.isDirty = true;
      });
      pushHistory(get().script.nodes, get().script.edges);
    },

    duplicateNodes: (ids) => {
      const { nodes, edges } = get().script;
      const selectedNodes = nodes.filter((n) => ids.includes(n.id));
      const idMap: Record<string, string> = {};
      const newNodes = selectedNodes.map((n) => {
        const newId = nanoid();
        idMap[n.id] = newId;
        return { ...JSON.parse(JSON.stringify(n)), id: newId, x: n.x + 40, y: n.y + 40, status: 'idle' as const };
      });
      const newEdges = edges
        .filter((e) => ids.includes(e.from_node) && ids.includes(e.to_node))
        .map((e) => ({ ...e, id: nanoid(), from_node: idMap[e.from_node], to_node: idMap[e.to_node] }));
      set((state) => {
        state.script.nodes.push(...newNodes);
        state.script.edges.push(...newEdges);
        state.ui.selection = { nodes: newNodes.map((n) => n.id), edges: [] };
        state.isDirty = true;
      });
      pushHistory(get().script.nodes, get().script.edges);
    },

    moveNode: (id, x, y) => {
      const { snap_to_grid, grid_size } = get().ui;
      const snap = (v: number) => snap_to_grid ? Math.round(v / grid_size) * grid_size : v;
      set((state) => {
        const n = state.script.nodes.find((n) => n.id === id);
        if (n) { n.x = snap(x); n.y = snap(y); }
        state.isDirty = true;
      });
    },

    moveNodes: (ids, dx, dy) => {
      set((state) => {
        ids.forEach((id) => {
          const n = state.script.nodes.find((n) => n.id === id);
          if (n) { n.x += dx; n.y += dy; }
        });
        state.isDirty = true;
      });
    },

    addEdge: (edge) => {
      const { edges } = get().script;
      // Prevent duplicate
      const exists = edges.some(
        (e) => e.from_node === edge.from_node && e.from_port === edge.from_port && e.to_node === edge.to_node
      );
      if (exists) return;
      // Remove existing edge from same port
      set((state) => {
        state.script.edges = state.script.edges.filter(
          (e) => !(e.from_node === edge.from_node && e.from_port === edge.from_port)
        );
        state.script.edges.push({ ...edge, id: nanoid() });
        state.isDirty = true;
      });
      pushHistory(get().script.nodes, get().script.edges);
    },

    deleteEdges: (ids) => {
      set((state) => {
        state.script.edges = state.script.edges.filter((e) => !ids.includes(e.id));
        state.ui.selection.edges = [];
        state.isDirty = true;
      });
      pushHistory(get().script.nodes, get().script.edges);
    },

    selectNodes: (ids, append = false) => {
      set((state) => {
        if (append) {
          const existing = new Set(state.ui.selection.nodes);
          ids.forEach((id) => existing.has(id) ? existing.delete(id) : existing.add(id));
          state.ui.selection.nodes = Array.from(existing);
        } else {
          state.ui.selection = { nodes: ids, edges: [] };
        }
      });
    },

    selectEdges: (ids, append = false) => {
      set((state) => {
        if (append) {
          state.ui.selection.edges = [...new Set([...state.ui.selection.edges, ...ids])];
        } else {
          state.ui.selection = { nodes: [], edges: ids };
        }
      });
    },

    clearSelection: () => {
      set((state) => { state.ui.selection = { nodes: [], edges: [] }; });
    },

    selectAll: () => {
      set((state) => {
        state.ui.selection = {
          nodes: state.script.nodes.map((n) => n.id),
          edges: state.script.edges.map((e) => e.id),
        };
      });
    },

    setCanvasTransform: (t) => {
      set((state) => { Object.assign(state.ui.canvas_transform, t); });
    },

    fitToScreen: () => {
      const { nodes } = get().script;
      if (nodes.length === 0) return;
      const minX = Math.min(...nodes.map((n) => n.x));
      const minY = Math.min(...nodes.map((n) => n.y));
      const maxX = Math.max(...nodes.map((n) => n.x + 200));
      const maxY = Math.max(...nodes.map((n) => n.y + 80));
      const W = window.innerWidth - 580;
      const H = window.innerHeight - 100;
      const scaleX = W / (maxX - minX + 100);
      const scaleY = H / (maxY - minY + 100);
      const scale = Math.min(Math.max(Math.min(scaleX, scaleY), 0.3), 1.5);
      const x = (W - (maxX - minX) * scale) / 2 - minX * scale;
      const y = (H - (maxY - minY) * scale) / 2 - minY * scale;
      set((state) => { state.ui.canvas_transform = { x, y, scale }; });
    },

    zoomIn: () => {
      set((state) => {
        state.ui.canvas_transform.scale = Math.min(state.ui.canvas_transform.scale * 1.2, 3);
      });
    },

    zoomOut: () => {
      set((state) => {
        state.ui.canvas_transform.scale = Math.max(state.ui.canvas_transform.scale / 1.2, 0.2);
      });
    },

    resetZoom: () => {
      set((state) => { state.ui.canvas_transform.scale = 1; });
    },

    copySelected: () => {
      const { selection, script } = get() as any;
      const nodes = script.nodes.filter((n: FlowNode) => selection.nodes.includes(n.id));
      const edges = script.edges.filter(
        (e: FlowEdge) => selection.nodes.includes(e.from_node) && selection.nodes.includes(e.to_node)
      );
      set((state) => { state.clipboard = { nodes: JSON.parse(JSON.stringify(nodes)), edges: JSON.parse(JSON.stringify(edges)) }; });
    },

    cutSelected: () => {
      get().copySelected();
      get().deleteNodes(get().ui.selection.nodes);
    },

    paste: () => {
      const { clipboard } = get();
      if (!clipboard) return;
      const idMap: Record<string, string> = {};
      const newNodes = clipboard.nodes.map((n) => {
        const newId = nanoid();
        idMap[n.id] = newId;
        return { ...n, id: newId, x: n.x + 40, y: n.y + 40, status: 'idle' as const };
      });
      const newEdges = clipboard.edges.map((e) => ({
        ...e,
        id: nanoid(),
        from_node: idMap[e.from_node] || e.from_node,
        to_node: idMap[e.to_node] || e.to_node,
      }));
      set((state) => {
        state.script.nodes.push(...newNodes);
        state.script.edges.push(...newEdges);
        state.ui.selection = { nodes: newNodes.map((n) => n.id), edges: [] };
        state.isDirty = true;
      });
      pushHistory(get().script.nodes, get().script.edges);
    },

    undo: () => {
      if (historyIndex <= 0) return;
      historyIndex--;
      const snap = history[historyIndex];
      set((state) => {
        state.script.nodes = JSON.parse(JSON.stringify(snap.nodes));
        state.script.edges = JSON.parse(JSON.stringify(snap.edges));
        state.isDirty = true;
      });
    },

    redo: () => {
      if (historyIndex >= history.length - 1) return;
      historyIndex++;
      const snap = history[historyIndex];
      set((state) => {
        state.script.nodes = JSON.parse(JSON.stringify(snap.nodes));
        state.script.edges = JSON.parse(JSON.stringify(snap.edges));
        state.isDirty = true;
      });
    },

    startExecution: () => {
      const state = get();
      // Stop any existing executor
      if (state._executor) {
        state._executor.stop();
        set((s) => { s._executor = null; });
      }

      // Reset node states
      set((s) => {
        s.execution.status = 'running';
        s.execution.started_at = new Date().toISOString();
        s.execution.finished_at = undefined;
        s.execution.error = undefined;
        s.execution.log = [];
        s.execution.current_node_id = undefined;
        s.script.nodes.forEach((n) => { n.status = 'idle'; n.error_msg = undefined; });
      });

      const freshState = get();
      const api = (window as any).electronAPI;

      if (api) {
        // --- REAL PYTHON ENGINE via Electron IPC ---
        set((s) => { s.execution.log = []; });
        get().addLog({ level: 'info', message: '🔄 启动 Python 自动化引擎...' });

        // Send script to engine (IPC listeners are already registered at module level)
        api.engineStart(JSON.stringify(freshState.script));

        // Store a controller that sends IPC for stop
        set((s) => {
          s._executor = {
            pause: () => api.enginePause(),
            resume: () => api.engineResume(),
            stop: () => api.engineStop(),
          };
        });
      } else {
        // --- BROWSER SIMULATOR (fallback for dev) ---
        const ctrl = createFlowExecutor(freshState.script, {
          onNodeStatus: (nodeId, status, error) => {
            get().updateNodeStatus(nodeId, status, error);
          },
          onLog: (entry) => {
            get().addLog(entry);
          },
          onDone: (status) => {
            set((s) => {
              s.execution.status = status;
              s.execution.finished_at = new Date().toISOString();
              s._executor = null;
            });
          },
          onCurrentNode: (nodeId) => {
            set((s) => { s.execution.current_node_id = nodeId; });
          },
        });
        set((s) => { s._executor = ctrl; });
      }
    },

    pauseExecution: () => {
      const exe = get()._executor;
      if (exe) {
        exe.pause();
        set((s) => { s.execution.status = 'paused'; });
      }
    },

    resumeExecution: () => {
      const exe = get()._executor;
      if (exe) {
        exe.resume();
        set((s) => { s.execution.status = 'running'; });
      }
    },

    stopExecution: () => {
      const exe = get()._executor;
      if (exe) {
        exe.stop();
      }
      set((s) => {
        s.execution.status = 'stopped';
        s.execution.finished_at = new Date().toISOString();
        s._executor = null;
        s.script.nodes.forEach((n) => {
          if (n.status === 'running') n.status = 'idle';
        });
      });
    },

    updateNodeStatus: (nodeId, status, error) => {
      set((state) => {
        const n = state.script.nodes.find((n) => n.id === nodeId);
        if (n) {
          n.status = status;
          if (error) n.error_msg = error;
        }
        if (status === 'running') state.execution.current_node_id = nodeId;
      });
    },

    addLog: (entry) => {
      set((state) => {
        state.execution.log.push({ ...entry, timestamp: new Date().toISOString() });
        if (state.execution.log.length > 1000) state.execution.log.shift();
      });
    },

    clearLog: () => {
      set((state) => { state.execution.log = []; });
    },

    setUI: (updates) => {
      set((state) => { Object.assign(state.ui, updates); });
    },

    startConnecting: (nodeId, portId) => {
      set((state) => {
        state.ui.connecting = { active: true, from_node: nodeId, from_port: portId };
      });
    },

    stopConnecting: () => {
      set((state) => { state.ui.connecting = { active: false }; });
    },

    setHoveredNode: (id) => {
      set((state) => { state.ui.hovered_node = id; });
    },

    setHoveredEdge: (id) => {
      set((state) => { state.ui.hovered_edge = id; });
    },

    setDraggingNode: (id) => {
      set((state) => { state.ui.dragging_node = id; });
    },

    autoLayout: () => {
      const { nodes, edges } = get().script;
      // Topological sort + layered layout
      const adj: Record<string, string[]> = {};
      const inDeg: Record<string, number> = {};
      nodes.forEach((n) => { adj[n.id] = []; inDeg[n.id] = 0; });
      edges.forEach((e) => {
        adj[e.from_node].push(e.to_node);
        inDeg[e.to_node] = (inDeg[e.to_node] || 0) + 1;
      });
      const layers: string[][] = [];
      let queue = nodes.filter((n) => inDeg[n.id] === 0).map((n) => n.id);
      const visited = new Set<string>();
      while (queue.length > 0) {
        layers.push([...queue]);
        const next: string[] = [];
        queue.forEach((id) => {
          visited.add(id);
          (adj[id] || []).forEach((child) => {
            inDeg[child]--;
            if (inDeg[child] === 0 && !visited.has(child)) next.push(child);
          });
        });
        queue = next;
      }
      const NODE_W = 220, NODE_H = 80, GAP_X = 60, GAP_Y = 40;
      set((state) => {
        layers.forEach((layer, li) => {
          const totalH = layer.length * NODE_H + (layer.length - 1) * GAP_Y;
          const startY = (window.innerHeight - totalH) / 2 - 50;
          layer.forEach((id, ni) => {
            const n = state.script.nodes.find((n) => n.id === id);
            if (n) {
              n.x = li * (NODE_W + GAP_X) + 60;
              n.y = startY + ni * (NODE_H + GAP_Y);
            }
          });
        });
        state.isDirty = true;
      });
      pushHistory(get().script.nodes, get().script.edges);
    },
  }))
);

// --- localStorage Persistence ---

const STORAGE_KEY = 'lingdong-script-data';
const RECENT_KEY = 'lingdong-recent';

// Restore from localStorage on load
try {
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved) {
    const parsed = JSON.parse(saved);
    if (parsed && parsed.nodes && parsed.meta) {
      // Delay restore to let the store initialize first
      setTimeout(() => {
        useStore.setState((s) => {
          s.script = parsed;
          s.isDirty = false;
        });
      }, 0);
    }
  }
} catch {}

// Auto-save script to localStorage on every change
useStore.subscribe((state) => {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state.script));
  } catch {}
});

// --- Register persistent IPC listeners for engine messages ---
// This ensures global F9 (from main process) can control engine and update store
const _api = (window as any).electronAPI;
if (_api) {
  _api.onEngineMessage((msg: any) => {
    if (!msg || !msg.type) return;
    switch (msg.type) {
      case 'node_status':
        useStore.getState().updateNodeStatus(msg.node_id, msg.status, msg.message);
        if (msg.status === 'running') {
          useStore.setState((s) => { s.execution.current_node_id = msg.node_id; });
        }
        break;
      case 'log':
        useStore.getState().addLog({ level: msg.level || 'info', message: msg.message, node_id: msg.node_id });
        break;
      case 'execution_done':
        useStore.setState((s) => {
          s.execution.status = msg.status || 'completed';
          s.execution.finished_at = new Date().toISOString();
          s._executor = null;
        });
        break;
      case 'status_report':
        useStore.setState((s) => { s.execution.status = msg.status; });
        break;
    }
  });

  _api.onEngineLog((msg: any) => {
    useStore.getState().addLog({ level: msg.level || 'info', message: msg.message });
  });
}
