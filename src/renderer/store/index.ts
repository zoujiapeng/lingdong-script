import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import { nanoid } from 'nanoid';
import type {
  FlowScript, FlowNode, FlowEdge, NodeType,
  UIState, ExecutionState, CanvasTransform, ScriptMeta,
  LogEntry, FeatureDefinition, ToolCallEntry,
  MacroEvent, MacroInfo, RecordingState,
  LearnedTarget, ParamPromptItem, AgentRunRecord,
  PlanNodeState, PlanDAGState, SubFlowNavState,
} from '../types';
import { NODE_DEFINITIONS } from '../utils/nodeDefinitions';
import { getNodeDimensions } from '../utils/constants';
import { templateRegistry } from '../utils/templateRegistry';
import { matchExact } from '../utils/templateMatcher';
import { createFlowExecutor } from './executor';
import type { ExecutionController } from './executor';
import { generateText } from '../utils/textGenerator';
import { DEFAULT_SETTINGS, DEFAULT_UI_STATE, PRESET_TEMPLATES, PRESET_TEMPLATE_META, STORAGE_KEY, MAX_HISTORY } from './defaults';
import { loadCustomTemplates, saveCustomTemplates, loadAgentHistory, saveAgentHistory } from './persistence';
export { loadTemplateShortcuts, saveTemplateShortcut, deleteTemplateShortcut, loadCustomTemplates, saveCustomTemplates } from './persistence';
export type { CustomTemplate } from './persistence';

function createTemplateScript(templateId: string): FlowScript {
  const tpl = PRESET_TEMPLATES.find((t) => t.id === templateId);
  return tpl ? tpl.createScript() : PRESET_TEMPLATES[0].createScript();
}

interface AppStore {
  // Script state
  script: FlowScript;
  scripts: Record<string, FlowScript>;
  activeScriptId: string;
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

  // Feature registry (MCP tools)
  featureList: FeatureDefinition[];
  toolCallLog: ToolCallEntry[];

  // Sub-flow navigation
  subFlowNav: SubFlowNavState;

  // Macro recording
  recording: RecordingState;
  macroList: MacroInfo[];

  // Actions - Script
  newScript: () => void;
  loadScript: (script: FlowScript) => void;
  loadTemplate: (templateId: string) => void;
  loadTemplateByExactMatch: (query: string) => boolean;
  saveScript: () => void;
  setScriptName: (name: string) => void;
  saveAsTemplate: (name: string, description?: string) => void;
  registerScript: (script: FlowScript) => void;
  loadSubScript: (scriptId: string) => void;
  navigateToSubFlow: (scriptId: string, sourceNodeId?: string) => void;
  navigateBackFromSubFlow: () => void;
  canNavigateBack: boolean;

  // Actions - Nodes
  addNode: (type: NodeType, x: number, y: number, params?: any, providedId?: string) => string;
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
  // Debugger
  toggleBreakpoint: (nodeId: string) => void;
  stepOver: () => void;
  // Feature registry
  fetchFeatures: () => Promise<void>;
  callFeatureDirect: (featureName: string, displayName: string, params: Record<string, any>) => Promise<any>;
  addToolCallEntry: (entry: Omit<ToolCallEntry, 'id' | 'timestamp'>) => void;
  clearToolCallLog: () => void;

  // Actions - UI
  setUI: (updates: Partial<UIState>) => void;
  setViewMode: (mode: UIState['viewMode']) => void;
  startConnecting: (nodeId: string, portId: string) => void;
  stopConnecting: () => void;
  setHoveredNode: (id?: string) => void;
  setHoveredEdge: (id?: string) => void;
  setDraggingNode: (id?: string) => void;

  // Auto layout
  autoLayout: (context?: 'root' | 'sub_flow') => void;

  // Macro recording actions
  startRecording: (name?: string) => Promise<void>;
  stopRecording: () => Promise<void>;
  listMacros: () => Promise<void>;
  playMacro: (name: string) => Promise<void>;
  deleteMacro: (name: string) => Promise<void>;
  convertMacroToFlow: (name: string) => Promise<void>;

  // Learned targets
  learnedTargets: LearnedTarget[];
  showLearnedPanel: boolean;
  fetchLearnedTargets: (app?: string) => Promise<void>;
  deleteLearnedTarget: (targetId: string) => Promise<void>;
  recordLearnedTarget: (targetId: string, success: boolean) => Promise<void>;
  toggleLearnedPanel: () => void;

  // Agent execution history
  agentHistory: AgentRunRecord[];
  addAgentHistory: (record: AgentRunRecord) => void;
  clearAgentHistory: () => void;

  // Crystallized templates
  crystallizedTemplates: any[];
  newCrystallizedCount: number;
  fetchCrystallizedTemplates: () => Promise<void>;
  markCrystallizedSeen: () => void;

  // Triggers (runtime)
  triggers: import('../types').TriggerUIState[];
  setTriggers: (triggers: import('../types').TriggerUIState[]) => void;
  stopTrigger: (instanceId: string) => Promise<void>;
  stopAllTriggers: () => Promise<void>;

  // Script-level trigger config
  scriptTriggers: import('../types').TriggerDef[];
  addScriptTrigger: (trigger: import('../types').TriggerDef) => void;
  removeScriptTrigger: (index: number) => void;
  updateScriptTrigger: (index: number, trigger: import('../types').TriggerDef) => void;

  // Param prompt dialog (missing parameters flow)
  showParamPrompt: boolean;
  paramPromptQueue: ParamPromptItem[];
  cancelParamPrompt: () => void;
  finishParamPrompt: () => void;
}

// History for undo/redo
const MAX_HISTORY = 50;
let history: { nodes: FlowNode[]; edges: FlowEdge[] }[] = [];
let historyIndex = -1;

/**
 * Build a param prompt queue by scanning all nodes for missing required params.
 * Mirrors the logic in api.ts check-params endpoint.
 */
function _buildParamPromptQueue(nodes: FlowNode[]): ParamPromptItem[] {
  const required: Record<string, Array<{ key: string; label: string; group: 'region' | 'text' | 'file' }>> = {
    click: [
      { key: 'x', label: 'X 坐标', group: 'region' },
      { key: 'y', label: 'Y 坐标', group: 'region' },
    ],
    input_text: [{ key: 'text', label: '输入文字', group: 'text' }],
    image_click: [{ key: 'targets', label: '目标图片', group: 'file' }],
    click_text: [{ key: 'text', label: '目标文字', group: 'text' }],
    keyboard: [{ key: 'keys', label: '按键', group: 'text' }],
    swipe: [
      { key: 'start_x', label: '起点 X', group: 'region' },
      { key: 'start_y', label: '起点 Y', group: 'region' },
      { key: 'end_x', label: '终点 X', group: 'region' },
      { key: 'end_y', label: '终点 Y', group: 'region' },
    ],
    color_check: [{ key: 'expected_color', label: '期望颜色', group: 'text' }],
    region_wait: [{ key: 'region', label: '监控区域', group: 'region' }],
    progress_wait: [{ key: 'region', label: '进度条区域', group: 'region' }],
    variable_set: [
      { key: 'name', label: '变量名', group: 'text' },
      { key: 'value', label: '变量值', group: 'text' },
    ],
    tool_call: [{ key: 'feature_name', label: '工具名称', group: 'text' }],
    sub_script: [{ key: 'script_id', label: '子流程 ID', group: 'text' }],
    wait: [{ key: 'duration', label: '等待秒数', group: 'text' }],
  };

  const queue: ParamPromptItem[] = [];
  for (const node of nodes) {
    const p = node.params as any;

    // Special handling: wait node's required param depends on wait_type
    if (node.type === 'wait') {
      const waitType = p?.wait_type || 'time';
      if (waitType === 'time' && (p?.duration === undefined || p?.duration === null || p?.duration === '')) {
        queue.push({ nodeId: node.id, nodeType: node.type, nodeLabel: node.type, params: ['duration'], labels: ['等待秒数'], groupType: 'text' });
      } else if (waitType === 'window' && !p?.window_title) {
        queue.push({ nodeId: node.id, nodeType: node.type, nodeLabel: node.type, params: ['window_title'], labels: ['窗口标题'], groupType: 'text' });
      } else if (waitType === 'image' && !p?.image_path && !p?.template_name) {
        queue.push({ nodeId: node.id, nodeType: node.type, nodeLabel: node.type, params: ['image_path'], labels: ['图片路径'], groupType: 'file' });
      }
      continue;
    }

    // Special handling: mouse_operation depends on sub_operation + mode
    if (node.type === 'mouse_operation') {
      const mode = p?.mode || 'coordinate';
      if (mode === 'image' && (!p?.targets || !Array.isArray(p.targets) || p.targets.length === 0)) {
        queue.push({ nodeId: node.id, nodeType: node.type, nodeLabel: node.type, params: ['targets'], labels: ['目标图片'], groupType: 'file' });
      } else if (mode === 'coordinate' && (p?.x === undefined || p?.x === null || p?.x === '' || p?.y === undefined || p?.y === null || p?.y === '')) {
        queue.push({ nodeId: node.id, nodeType: node.type, nodeLabel: node.type, params: ['x', 'y'], labels: ['点击坐标'], groupType: 'region' });
      } else if (mode === 'text' && (!p?.text || p.text === '')) {
        queue.push({ nodeId: node.id, nodeType: node.type, nodeLabel: node.type, params: ['text'], labels: ['目标文字'], groupType: 'text' });
      }
      continue;
    }

    const needed = required[node.type as keyof typeof required];
    if (!needed) continue;

    const missing = needed.filter(pd => {
      const val = p[pd.key];
      return val === undefined || val === null || val === '' ||
             (Array.isArray(val) && val.length === 0);
    });
    if (missing.length === 0) continue;

    const regionKeys = missing.filter(m => m.group === 'region');
    const textKeys = missing.filter(m => m.group === 'text');
    const fileKeys = missing.filter(m => m.group === 'file');

    if (regionKeys.length > 0) {
      queue.push({ nodeId: node.id, nodeType: node.type, nodeLabel: node.type, params: regionKeys.map(m => m.key), labels: ['位置坐标'], groupType: 'region' });
    }
    if (textKeys.length > 0) {
      queue.push({ nodeId: node.id, nodeType: node.type, nodeLabel: node.type, params: textKeys.map(m => m.key), labels: textKeys.map(m => m.label), groupType: 'text' });
    }
    if (fileKeys.length > 0) {
      queue.push({ nodeId: node.id, nodeType: node.type, nodeLabel: node.type, params: fileKeys.map(m => m.key), labels: fileKeys.map(m => m.label), groupType: 'file' });
    }
  }
  return queue;
}

function pushHistory(nodes: FlowNode[], edges: FlowEdge[]) {
  history = history.slice(0, historyIndex + 1);
  history.push({ nodes: JSON.parse(JSON.stringify(nodes)), edges: JSON.parse(JSON.stringify(edges)) });
  if (history.length > MAX_HISTORY) history.shift();
  historyIndex = history.length - 1;
}

export const useStore = create<AppStore>()(
  immer((set, get) => ({
    script: createTemplateScript('desktop-monitor'),
    scripts: {},
    activeScriptId: '',
    recentScripts: [],
    isDirty: false,
    ui: DEFAULT_UI_STATE,
    execution: {
      status: 'idle',
      variables: {},
      log: [],
      breakpoints: [],
      stepping: false,
      plan: undefined,
    },
    clipboard: null,
    _executor: null,
    featureList: [],
    toolCallLog: [],
    subFlowNav: { stack: [] },
    recording: { active: false, macroName: '' },
    macroList: [],
    learnedTargets: [],
    showLearnedPanel: false,
    crystallizedTemplates: [],
    newCrystallizedCount: 0,

    agentHistory: loadAgentHistory(),

    triggers: [],
    scriptTriggers: [],
    showParamPrompt: false,
    paramPromptQueue: [],

    newScript: () => {
      const s = createTemplateScript('desktop-monitor');
      history = [];
      historyIndex = -1;
      pushHistory(s.nodes, s.edges);
      set((state) => {
        state.script = s;
        state.scriptTriggers = s.triggers || [];
        state.isDirty = false;
        state.ui.selection = { nodes: [], edges: [] };
        state.execution = { status: 'idle', variables: {}, log: [], breakpoints: [], stepping: false, plan: undefined };
      });
      if (s.nodes.length > 0) get().fitToScreen();
    },

    loadTemplate: (templateId) => {
      const s = createTemplateScript(templateId);
      history = [];
      historyIndex = -1;
      pushHistory(s.nodes, s.edges);
      set((state) => {
        state.script = s;
        state.scriptTriggers = s.triggers || [];
        state.isDirty = false;
        state.ui.selection = { nodes: [], edges: [] };
        state.execution = { status: 'idle', variables: {}, log: [], breakpoints: [], stepping: false, plan: undefined };
      });
      if (s.nodes.length > 0) get().fitToScreen();
    },

    loadTemplateByExactMatch: (query) => {
      const result = matchExact(query);
      if (!result) return false;
      // Load via existing loadTemplate if it's a preset
      const entry = result.template;
      if (entry.source === 'preset' && entry.flowScript) {
        get().loadScript(entry.flowScript);
        return true;
      }
      if (entry.flowScript) {
        get().loadScript(entry.flowScript);
        return true;
      }
      return false;
    },

    loadScript: (script) => {
      history = [];
      historyIndex = -1;
      pushHistory(script.nodes, script.edges);
      const id = script.meta.id || nanoid();
      if (!script.meta.id) script.meta.id = id;
      // Ensure settings exist to prevent crashes (e.g. SettingsPanel)
      if (!script.settings) {
        script.settings = { ...DEFAULT_SETTINGS };
      }
      set((state) => {
        state.script = script;
        state.scriptTriggers = script.triggers || [];
        state.scripts[id] = script;
        state.activeScriptId = id;
        state.isDirty = false;
        state.ui.selection = { nodes: [], edges: [] };
      });
      if (script.nodes.length > 0) get().fitToScreen();
    },

    saveScript: async () => {
      set((state) => {
        state.script.meta.updated_at = new Date().toISOString();
        state.isDirty = false;
      });
      const script = get().script;
      const json = JSON.stringify(script, null, 2);
      const pseudoCode = (() => {
        try {
          return generateText(script, 'pseudocode');
        } catch { return '# 伪代码生成失败'; }
      })();

      const api = (window as any).electronAPI;
      if (api?.saveFileDialog) {
        const result = await api.saveFileDialog(`${script.meta.name}.json`);
        if (!result.canceled && result.filePath) {
          try {
            const basePath = result.filePath.replace(/\.json$/, '');
            await api.writeFile(result.filePath, json);
            await api.writeFile(basePath + '.flow', pseudoCode);
            return;
          } catch (e) {
            console.error('Save failed:', e);
          }
        }
      }
      // Browser fallback — trigger sequential downloads
      const dl = (content: string, name: string, type: string) => {
        const blob = new Blob([content], { type });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = name;
        a.click();
        URL.revokeObjectURL(url);
      };
      dl(json, `${script.meta.name}.json`, 'application/json');
      dl(pseudoCode, `${script.meta.name}.flow`, 'text/plain');
    },

    setScriptName: (name) => {
      set((state) => {
        state.script.meta.name = name;
        state.isDirty = true;
      });
    },

    saveAsTemplate: (name, description) => {
      const script = get().script;
      const templates = loadCustomTemplates();
      const id = 'custom-' + nanoid();
      templates.push({
        id,
        name,
        description: description || script.meta.description || '自定义模板',
        script: JSON.parse(JSON.stringify(script)),
      });
      saveCustomTemplates(templates);
    },

    registerScript: (script) => {
      const id = script.meta.id || nanoid();
      if (!script.meta.id) script.meta.id = id;
      set((state) => {
        state.scripts[id] = script;
      });
    },

    loadSubScript: (scriptId) => {
      const sub = get().scripts[scriptId];
      if (!sub) return;
      get().loadScript(sub);
    },

    navigateToSubFlow: (scriptId, sourceNodeId) => {
      const sub = get().scripts[scriptId];
      if (!sub) return;
      set((state) => {
        state.subFlowNav.stack.push({
          scriptId: state.activeScriptId,
          scriptName: state.script.meta.name,
          sourceNodeId,
        });
        state.activeScriptId = scriptId;
        state.script = sub;
        // Ensure settings exist to prevent crashes
        if (!state.script.settings) state.script.settings = { ...DEFAULT_SETTINGS };
        state.ui.selection = { nodes: [], edges: [] };
      });
      if (sub.nodes.length > 0) {
        get().autoLayout('sub_flow');
        get().fitToScreen();
      }
    },

    navigateBackFromSubFlow: () => {
      set((state) => {
        const entry = state.subFlowNav.stack.pop();
        if (entry) {
          const parentScript = state.scripts[entry.scriptId];
          if (parentScript) {
            state.activeScriptId = entry.scriptId;
            state.script = parentScript;
            if (entry.sourceNodeId) {
              state.ui.selection = { nodes: [entry.sourceNodeId], edges: [] };
            }
          }
        }
      });
    },

    get canNavigateBack() { return get().subFlowNav.stack.length > 0; },

    addNode: (type, x, y, params, providedId?) => {
      const def = NODE_DEFINITIONS[type];
      const id = providedId || nanoid();
      const { snap_to_grid, grid_size } = get().ui;
      const snap = (v: number) => snap_to_grid ? Math.round(v / grid_size) * grid_size : v;
      const node: FlowNode = {
        id,
        type,
        x: snap(x),
        y: snap(y),
        params: params ? { ...def.defaultParams, ...params } : { ...def.defaultParams },
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
      const { snap_to_grid, grid_size } = get().ui;
      const snap = (v: number) => snap_to_grid ? Math.round(v / grid_size) * grid_size : v;
      set((state) => {
        ids.forEach((id) => {
          const n = state.script.nodes.find((n) => n.id === id);
          if (n) { n.x = snap(n.x + dx); n.y = snap(n.y + dy); }
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
      // Allow multiple edges from the same output port (fan-out / parallel execution)
      set((state) => {
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
      const canvasHost = document.querySelector<HTMLElement>('.wb-canvas-zone, .canvas-area, .flow-canvas-wrapper');
      const rect = canvasHost?.getBoundingClientRect();
      const W = Math.max((rect?.width ?? window.innerWidth) - 80, 320);
      const H = Math.max((rect?.height ?? window.innerHeight) - 80, 240);
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
      const copyState = get();
      const selNodes = copyState.ui.selection.nodes;
      const nodes = copyState.script.nodes.filter((n: FlowNode) => selNodes.includes(n.id));
      const edges = copyState.script.edges.filter(
        (e: FlowEdge) => selNodes.includes(e.from_node) && selNodes.includes(e.to_node)
      );
      set((s) => { s.clipboard = { nodes: JSON.parse(JSON.stringify(nodes)), edges: JSON.parse(JSON.stringify(edges)) }; });
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

      // ── Check missing params before running ──
      const paramQueue = _buildParamPromptQueue(state.script.nodes);
      if (paramQueue.length > 0) {
        set((s) => {
          s.showParamPrompt = true;
          s.paramPromptQueue = paramQueue;
        });
        get().addLog({ level: 'warn', message: `⚠️ 有 ${paramQueue.length} 个参数未填写，请补全后再运行` });
        return; // Don't start engine — wait for user to fill params
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
            stepNext: () => api.engineStepNext?.(),
          };
        });
      } else {
        // --- BROWSER SIMULATOR (fallback for dev) ---
        const bp = freshState.execution.breakpoints || [];
        const stepping = freshState.execution.stepping;
        const ctrl = createFlowExecutor(
          freshState.script,
          {
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
                s.execution.loop_iteration = null;
                s._executor = null;
              });
            },
            onCurrentNode: (nodeId) => {
              set((s) => { s.execution.current_node_id = nodeId; });
            },
            onIteration: (current, total, nodeId) => {
              set((s) => { s.execution.loop_iteration = { current, total, node_id: nodeId }; });
            },
            onPausedAtBreakpoint: (nodeId) => {
              get().addLog({ level: 'warn', message: `🔴 断点暂停: 节点 ${nodeId}` });
            },
          },
          undefined,
          bp,
          stepping,
        );
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

    toggleBreakpoint: (nodeId) => {
      set((state) => {
        const bps = state.execution.breakpoints;
        const idx = bps.indexOf(nodeId);
        if (idx >= 0) {
          bps.splice(idx, 1);
        } else {
          bps.push(nodeId);
        }
      });
      get().addLog({ level: 'debug', message: `🔴 断点: ${useStore.getState().execution.breakpoints.includes(nodeId) ? '已设置' : '已移除'}` });
    },

    stepOver: () => {
      const ctrl = get()._executor;
      if (ctrl && 'stepNext' in ctrl) {
        (ctrl as any).stepNext();
      }
      // Also resume from regular pause (for breakpoint → step case)
      if (get().execution.status === 'paused') {
        get().resumeExecution();
      }
    },

    fetchFeatures: async () => {
      const api = (window as any).electronAPI;
      if (api?.featuresList) {
        try {
          const features = await api.featuresList();
          if (Array.isArray(features)) {
            set((state) => { state.featureList = features; });
          } else {
            console.warn('[fetchFeatures] invalid response (not an array):', features);
          }
        } catch { /* ignore */ }
      }
    },

    callFeatureDirect: async (featureName, displayName, params) => {
      const api = (window as any).electronAPI;
      if (!api?.callFeature) {
        get().addToolCallEntry({ feature_name: featureName, display_name: displayName, params, result: null, success: false, elapsed_ms: 0, source: 'direct' });
        return { success: false };
      }
      const start = Date.now();
      const result = await api.callFeature(featureName, params);
      const elapsed = Date.now() - start;
      get().addToolCallEntry({
        feature_name: featureName,
        display_name: displayName,
        params,
        result,
        success: result?.success ?? false,
        elapsed_ms: elapsed,
        source: 'direct',
      });
      return result;
    },

    addToolCallEntry: (entry) => {
      set((state) => {
        state.toolCallLog.push({
          ...entry,
          id: nanoid(),
          timestamp: new Date().toISOString(),
        });
        if (state.toolCallLog.length > 200) {
          state.toolCallLog = state.toolCallLog.slice(-200);
        }
      });
    },

    clearToolCallLog: () => {
      set((state) => { state.toolCallLog = []; });
    },

    setUI: (updates) => {
      set((state) => { Object.assign(state.ui, updates); });
    },

    setViewMode: (mode) => {
      set((state) => { state.ui.viewMode = mode; });
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

    autoLayout: (context = 'root') => {
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
      const scale = context === 'sub_flow' ? 0.75 : 1.0;
      const dims = getNodeDimensions('normal');
      const NODE_W = dims.w * scale, NODE_H = dims.h * scale, GAP_X = 50 * scale, GAP_Y = 30 * scale;
      const { snap_to_grid, grid_size } = get().ui;
      const snap = (v: number) => snap_to_grid ? Math.round(v / grid_size) * grid_size : v;
      set((state) => {
        layers.forEach((layer, li) => {
          const totalH = layer.length * NODE_H + (layer.length - 1) * GAP_Y;
          const startY = (window.innerHeight - totalH) / 2 - 50;
          layer.forEach((id, ni) => {
            const n = state.script.nodes.find((n) => n.id === id);
            if (n) {
              n.x = snap(li * (NODE_W + GAP_X) + 60);
              n.y = snap(startY + ni * (NODE_H + GAP_Y));
            }
          });
        });
        state.isDirty = true;
      });
      pushHistory(get().script.nodes, get().script.edges);
    },

    // --- Macro Recording Actions ---

    startRecording: async (name?: string) => {
      const api = (window as any).electronAPI;
      if (!api?.macroStart) return;
      const macroName = name || `录制_${new Date().toLocaleString('zh-CN').replace(/[/:]/g, '-')}`;
      set((s) => { s.recording = { active: true, macroName }; });
      get().addLog({ level: 'info', message: `🔴 开始录制宏: ${macroName}` });
      const result = await api.macroStart({ name: macroName });
      if (!result?.success) {
        set((s) => { s.recording = { active: false, macroName: '' }; });
        get().addLog({ level: 'error', message: `录制启动失败: ${result?.error || '未知错误'}` });
      }
    },

    stopRecording: async () => {
      const api = (window as any).electronAPI;
      if (!api?.macroStop) return;
      set((s) => { s.recording.active = false; });
      get().addLog({ level: 'info', message: '⏹️ 停止录制...' });
      const result = await api.macroStop();
      if (result?.success) {
        get().addLog({ level: 'info', message: `✅ 宏录制完成: ${result.events} 个事件` });
        await get().listMacros();
      } else {
        get().addLog({ level: 'error', message: `录制停止失败: ${result?.error || '未知错误'}` });
      }
    },

    listMacros: async () => {
      const api = (window as any).electronAPI;
      if (!api?.macroList) return;
      try {
        const result = await api.macroList();
        if (result?.success) {
          set((s) => { s.macroList = result.result || []; });
        }
      } catch { /* ignore */ }
    },

    playMacro: async (name: string) => {
      const api = (window as any).electronAPI;
      if (!api?.macroPlay) return;
      get().addLog({ level: 'info', message: `▶️ 播放宏: ${name}` });
      const result = await api.macroPlay(name);
      if (result?.success) {
        get().addLog({ level: 'info', message: '✅ 宏播放完成' });
      } else {
        get().addLog({ level: 'error', message: `宏播放失败: ${result?.error || '未知错误'}` });
      }
    },

    deleteMacro: async (name: string) => {
      const api = (window as any).electronAPI;
      if (!api?.macroDelete) return;
      const result = await api.macroDelete(name);
      if (result?.success) {
        get().addLog({ level: 'info', message: `🗑️ 已删除宏: ${name}` });
        await get().listMacros();
      } else {
        get().addLog({ level: 'error', message: `删除失败: ${result?.error || '未知错误'}` });
      }
    },

    convertMacroToFlow: async (name: string) => {
      const api = (window as any).electronAPI;
      if (!api?.macroGetEvents) return;
      get().addLog({ level: 'info', message: `🔄 转换宏 "${name}" 为流程图...` });
      const result = await api.macroGetEvents(name);
      if (!result?.success || !result?.result?.events) {
        get().addLog({ level: 'error', message: `读取宏失败: ${result?.error || '未知错误'}` });
        return;
      }
      try {
        const { macroEventsToFlow } = await import('../utils/macroConverter');
        const events: MacroEvent[] = result.result.events;
        const script = macroEventsToFlow(events, name);
        get().loadScript(script);
        get().addLog({ level: 'info', message: `✅ 宏 "${name}" 已转换为 ${script.nodes.length - 2} 个操作节点的流程图` });
      } catch (e: any) {
        get().addLog({ level: 'error', message: `转换失败: ${e.message}` });
      }
    },

    // ── Learned Targets ──────────────────────────────────────

    fetchLearnedTargets: async (app?: string) => {
      try {
        const api = (window as any).electronAPI;
        if (!api?.callFeature) return;
        const result = await api.callFeature('learned_list', { app: app || '', limit: 200 });
        if (result?.success && Array.isArray(result.result)) {
          set((s) => { s.learnedTargets = result.result; });
        } else if (Array.isArray(result)) {
          set((s) => { s.learnedTargets = result; });
        } else if (result?.result && Array.isArray(result.result)) {
          set((s) => { s.learnedTargets = result.result; });
        }
      } catch { /* ignore */ }
    },

    deleteLearnedTarget: async (targetId: string) => {
      try {
        const api = (window as any).electronAPI;
        if (!api?.callFeature) return;
        await api.callFeature('learned_delete', { target_id: targetId });
        get().addLog({ level: 'info', message: `🗑️ 已删除已学习目标: ${targetId}` });
        await get().fetchLearnedTargets();
      } catch { /* ignore */ }
    },

    recordLearnedTarget: async (targetId: string, success: boolean) => {
      try {
        const api = (window as any).electronAPI;
        if (!api?.callFeature) return;
        await api.callFeature('learned_record', { target_id: targetId, success });
      } catch { /* ignore */ }
    },

    toggleLearnedPanel: () => {
      set((s) => {
        s.showLearnedPanel = !s.showLearnedPanel;
        if (s.showLearnedPanel) s.newCrystallizedCount = 0;
      });
      if (!get().showLearnedPanel) get().fetchLearnedTargets();
    },

    // ── Agent Execution History ──

    addAgentHistory: (record) => {
      set((s) => {
        s.agentHistory = [record, ...s.agentHistory].slice(0, 50);
      });
      saveAgentHistory(get().agentHistory);
    },

    clearAgentHistory: () => {
      set((s) => { s.agentHistory = []; });
      saveAgentHistory([]);
    },

    // Crystallized templates
    fetchCrystallizedTemplates: async () => {
      try {
        const api = (window as any).electronAPI;
        if (!api?.callFeature) return;
        const result = await api.callFeature('crystallized_list', {});
        if (Array.isArray(result)) {
          set((s) => {
            const old = s.crystallizedTemplates.length;
            s.crystallizedTemplates = result;
            if (!s.showLearnedPanel && result.length > old) {
              s.newCrystallizedCount += result.length - old;
            }
          });
        } else if (result?.result && Array.isArray(result.result)) {
          set((s) => {
            const old = s.crystallizedTemplates.length;
            s.crystallizedTemplates = result.result;
            if (!s.showLearnedPanel && result.result.length > old) {
              s.newCrystallizedCount += result.result.length - old;
            }
          });
        } else if (result?.success && Array.isArray(result.result)) {
          set((s) => {
            const old = s.crystallizedTemplates.length;
            s.crystallizedTemplates = result.result;
            if (!s.showLearnedPanel && result.result.length > old) {
              s.newCrystallizedCount += result.result.length - old;
            }
          });
        }
      } catch { /* ignore */ }
    },

    markCrystallizedSeen: () => {
      set((s) => { s.newCrystallizedCount = 0; });
    },

    // Triggers
    setTriggers: (triggers) => {
      set((s) => { s.triggers = triggers; });
    },
    stopTrigger: async (instanceId: string) => {
      try {
        const api = (window as any).electronAPI;
        if (api?.callFeature) {
          await api.callFeature('trigger_stop', { instance_id: instanceId });
        }
      } catch { /* ignore */ }
    },
    stopAllTriggers: async () => {
      try {
        const api = (window as any).electronAPI;
        if (api?.callFeature) {
          await api.callFeature('trigger_stop_all', {});
        }
      } catch { /* ignore */ }
    },

    // Script-level trigger config (part of the script/template)
    addScriptTrigger: (trigger) => {
      set((s) => {
        s.scriptTriggers.push(trigger);
        s.script.triggers = [...s.scriptTriggers];
        s.isDirty = true;
      });
    },
    removeScriptTrigger: (index) => {
      set((s) => {
        s.scriptTriggers.splice(index, 1);
        s.script.triggers = [...s.scriptTriggers];
        s.isDirty = true;
      });
    },
    updateScriptTrigger: (index, trigger) => {
      set((s) => {
        s.scriptTriggers[index] = trigger;
        s.script.triggers = [...s.scriptTriggers];
        s.isDirty = true;
      });
    },

    // Param prompt dialog
    cancelParamPrompt: () => {
      set((s) => {
        s.showParamPrompt = false;
        s.paramPromptQueue = [];
      });
    },
    finishParamPrompt: () => {
      set((s) => {
        s.showParamPrompt = false;
        s.paramPromptQueue = [];
      });
      // Continue execution after params are filled
      const exe = get().execution;
      if (exe.status === 'paused') {
        get().resumeExecution();
      } else {
        get().startExecution();
      }
    },
  }))
);

// ── Template Registry Initialization ───────────────────────────

// Register preset templates
for (const meta of PRESET_TEMPLATE_META) {
  const preset = PRESET_TEMPLATES.find(t => t.id === meta.id);
  templateRegistry.register({
    id: meta.id,
    name: meta.name,
    description: meta.description,
    category: meta.category,
    aliases: meta.aliases,
    keywords: meta.keywords,
    tags: meta.tags,
    source: 'preset',
    hasScript: true,
    flowScript: preset?.createScript(),
  });
}

// Register custom templates from localStorage
try {
  const customTemplates = loadCustomTemplates();
  for (const ct of customTemplates) {
    templateRegistry.register({
      id: ct.id,
      name: ct.name,
      description: ct.description,
      category: ct.script.meta.category || 'custom',
      aliases: ct.script.meta.aliases || [],
      keywords: ct.script.meta.keywords || [],
      tags: ct.script.meta.tags || [],
      source: 'custom',
      hasScript: true,
      flowScript: ct.script,
    });
  }
} catch {}

// ── localStorage Persistence ---

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
          // Ensure settings exist to prevent crashes
          if (!s.script.settings) s.script.settings = { ...DEFAULT_SETTINGS };
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

// Auto-save test flow to tests/flows/ after execution completes
async function _autoSaveTestFlow() {
  try {
    const s = useStore.getState();
    const api = (window as any).electronAPI;
    if (!api?.callFeature) return;
    const testName = `test_${s.script.meta.name || 'unnamed'}_${Date.now()}`.replace(/[^a-zA-Z0-9_一-鿿-]/g, '_');
    await api.callFeature('save_test_flow', {
      flow_data: JSON.stringify({ nodes: s.script.nodes, edges: s.script.edges, meta: s.script.meta }),
      test_name: testName,
      tags: ['auto-saved'],
    });
  } catch {
    // non-blocking
  }
}

// ─── HMR-safe IPC listener registration ───
const _api = (window as any).electronAPI;
const _listenerCleanups: (() => void)[] = [];

function _registerListeners() {
  // Clean up listeners from previous HMR instance
  for (const cleanup of _listenerCleanups) cleanup();
  _listenerCleanups.length = 0;

  // Expose store state for HTTP API bridge
  (window as any).__bridge_getState = () => {
    const s = useStore.getState();
    return { nodes: s.script.nodes, edges: s.script.edges, meta: s.script.meta, selection: s.ui.selection, viewMode: s.ui.viewMode };
  };
  (window as any).__bridge_loadScript = (script: any) => {
    const s = useStore.getState();
    s.loadScript(script);
    s.setViewMode('canvas');
  };

  if (_api) {
    _listenerCleanups.push(_api.onEngineMessage((msg: any) => {
      if (!msg || !msg.type) return;
      switch (msg.type) {
        case 'node_status':
          useStore.getState().updateNodeStatus(msg.node_id, msg.status, msg.message);
          if (msg.status === 'running') useStore.setState((s) => { s.execution.current_node_id = msg.node_id; });
          break;
        case 'log':
          useStore.getState().addLog({ level: msg.level || 'info', message: msg.message, node_id: msg.node_id });
          break;
        case 'execution_done':
          useStore.setState((s) => {
            s.execution.status = msg.status || 'completed';
            s.execution.finished_at = new Date().toISOString();
            s.execution.loop_iteration = null;
            s._executor = null;
            s.script.nodes.forEach((n) => { if (n.status === 'running') n.status = 'idle'; });
          });
          // 执行成功后刷新结晶模板
          if (msg.status === 'completed') {
            useStore.getState().addLog({ level: 'info', message: '执行完成，已自动保存为模板（结晶化）' });
            setTimeout(() => useStore.getState().fetchCrystallizedTemplates(), 500);
            // 自动保存测试流程
            _autoSaveTestFlow();
          }
          break;
        case 'status_report':
          useStore.setState((s) => { s.execution.status = msg.status; });
          break;
        case 'loop_iteration':
          useStore.setState((s) => { s.execution.loop_iteration = { current: msg.current, total: msg.total, node_id: msg.node_id }; });
          break;
        case 'trigger_status':
          if (msg.triggers) {
            useStore.setState((s) => { s.triggers = msg.triggers; });
          }
          break;
      }
    }));

    _listenerCleanups.push(_api.onEngineLog((msg: any) => {
      useStore.getState().addLog({ level: msg.level || 'info', message: msg.message });
    }));

    // Agent run tracking: dedup agent_done by run_id, time the run.
    let _agentDoneRunId = '';
    let _agentStartTime = 0;
    let _agentRunId = '';
    let _agentGoal = '';

    _listenerCleanups.push(_api.onAgentMessage((msg: Record<string, unknown>) => {
      const msgType = typeof msg?.type === 'string' ? msg.type : '';
      if (!msgType) return;

      // WinPilot native events. The detailed step/tool/usage stream is rendered
      // by AgentView; the store only tracks coarse run status + history.
      if (msgType === 'agent_start') {
        _agentStartTime = Date.now();
        _agentDoneRunId = '';
        _agentRunId = typeof msg.run_id === 'string' ? msg.run_id : '';
        _agentGoal = typeof msg.task === 'string' ? msg.task : '';
        useStore.setState((s) => {
          s.execution.plan = undefined;
          s.execution.status = 'running';
          s.execution.current_node_id = undefined;
        });
        return;
      }

      if (msgType === 'agent_done') {
        const runId = typeof msg.run_id === 'string' ? msg.run_id : '';
        // Dedup: a given run_id finishes once.
        if (runId && _agentDoneRunId === runId) return;
        if (runId) _agentDoneRunId = runId;

        const status = (typeof msg.status === 'string' ? msg.status : 'error') as AgentRunRecord['status'];
        const steps = typeof msg.steps === 'number' ? msg.steps : 0;
        const result = typeof msg.result === 'string' ? msg.result : '';
        const usage = (msg.usage && typeof msg.usage === 'object')
          ? (msg.usage as AgentRunRecord['usage'])
          : undefined;

        useStore.setState((s) => {
          s.execution.status = (status === 'done' || status === 'reply') ? 'completed' : 'error';
          s.execution.current_node_id = undefined;
        });

        const elapsed = _agentStartTime > 0 ? Math.round((Date.now() - _agentStartTime) / 1000) : 0;
        const record: AgentRunRecord = {
          id: Math.random().toString(36).slice(2, 10),
          goal: _agentGoal || '',
          status,
          run_id: runId || _agentRunId || undefined,
          steps,
          result,
          usage,
          started_at: new Date(_agentStartTime || Date.now()).toISOString(),
          finished_at: new Date().toISOString(),
          elapsed_seconds: elapsed,
          summary: result,
        };
        useStore.getState().addAgentHistory(record);
        return;
      }
    }));

    _listenerCleanups.push(_api.onApiCommand((action: string, payload: any) => {
      const store = useStore.getState();
      const pushState = () => {
        const s = useStore.getState();
        _api.pushState({ nodes: s.script.nodes, edges: s.script.edges, meta: s.script.meta, selection: s.ui.selection, viewMode: s.ui.viewMode, ui: s.ui });
      };
      switch (action) {
        case 'loadScript': if (payload?.nodes) { store.loadScript(payload); pushState(); } break;
        case 'addNode': if (payload?.type) { store.addNode(payload.type, payload.x || 0, payload.y || 0, payload.params, payload.id); pushState(); } break;
        case 'updateNode': if (payload?.id && payload?.updates) { store.updateNode(payload.id, payload.updates); pushState(); } break;
        case 'deleteNodes': if (payload?.ids) { store.deleteNodes(payload.ids); pushState(); } break;
        case 'addEdge': if (payload?.from_node && payload?.to_node) { store.addEdge(payload); pushState(); } break;
        case 'autoLayout': store.autoLayout(); pushState(); break;
        case 'updateNodeParams': if (payload?.id && payload?.params) { store.updateNodeParams(payload.id, payload.params); pushState(); } break;
        case 'deleteEdges': if (payload?.ids) { store.deleteEdges(payload.ids); pushState(); } break;
        case 'newScript': store.newScript(); pushState(); break;
        case 'setViewMode': if (payload?.mode) { store.setViewMode(payload.mode); pushState(); } break;
        case 'fitScreen': store.fitToScreen(); break;
        case 'getState': _api.pushState({ nodes: store.script.nodes, edges: store.script.edges, meta: store.script.meta, selection: store.ui.selection, viewMode: store.ui.viewMode, ui: store.ui }); break;
        case 'start': if (payload?.script) { store.loadScript(payload.script); store.startExecution(); pushState(); } break;
        case 'pause': store.pauseExecution(); break;
        case 'resume': store.resumeExecution(); break;
        case 'undo': store.undo(); break;
        case 'redo': store.redo(); break;
        case 'saveScript': store.saveScript(); break;
        case 'setScriptName': if (payload?.name) store.setScriptName(payload.name); pushState(); break;
        case 'saveTemplate': if (payload?.name) store.saveAsTemplate(payload.name, payload.description); break;
        case 'setUI': if (payload) store.setUI(payload); pushState(); break;
        case 'copySelected': store.copySelected(); break;
        case 'cutSelected': store.cutSelected(); break;
        case 'paste': store.paste(); break;
        case 'selectAll': store.selectAll(); break;
        case 'clearSelection': store.clearSelection(); break;
        case 'duplicateNodes': if (payload?.ids) store.duplicateNodes(payload.ids); break;
        default: console.warn('[API] Unknown action:', action);
      }
    }));

    // 初始化时推送状态到主进程
    setTimeout(() => {
      const s = useStore.getState();
      _api.pushState({ nodes: s.script.nodes, edges: s.script.edges, meta: s.script.meta, selection: s.ui.selection, viewMode: s.ui.viewMode });
    }, 100);
  }
}

// Register immediately, clean up on HMR reload
_registerListeners();
if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    for (const cleanup of _listenerCleanups) cleanup();
    _listenerCleanups.length = 0;
  });
}
