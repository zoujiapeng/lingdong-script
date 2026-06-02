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
  FeatureDefinition,
  ToolCallEntry,
  MacroEvent,
  MacroInfo,
  RecordingState,
  LearnedTarget,
  ParamPromptItem,
  AgentRunRecord,
  PlanNodeState,
  PlanDAGState,
} from '../types';
import { NODE_DEFINITIONS } from '../utils/nodeDefinitions';
import { getNodeDimensions } from '../utils/constants';
import { templateRegistry } from '../utils/templateRegistry';
import { matchExact } from '../utils/templateMatcher';
import { SubFlowNavState } from '../types';
import { createFlowExecutor, ExecutionController } from './executor';
import { generateText } from '../utils/textGenerator';

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
  themeMode: (() => { try { const s = localStorage.getItem('lingdong-theme-mode'); if (s === 'light' || s === 'dark' || s === 'system') return s; } catch {} return 'system'; })(),
  viewMode: 'canvas',
  panels_visible: false,
  show_node_library: false,
  show_property_panel: false,
  show_log_panel: false,
  show_tool_call_panel: false,
  show_ai_dialog: false,
  show_trigger_config: false,
  auto_learn: false,
  show_unified_search: false,
  unified_search_query: '',
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
          { id: ids.img1, type: 'image_click', x: 80, y: 340, params: { targets: [], similarity: 85, timeout: 2, method: 'single', offset_x: 0, offset_y: 0, delay_after: 0.3, multi_scale: true, match_all: false, description: '优先级1' }, label: '目标图片 1', status: 'idle' },
          { id: ids.img2, type: 'image_click', x: 320, y: 340, params: { targets: [], similarity: 85, timeout: 0.5, method: 'single', offset_x: 0, offset_y: 0, delay_after: 0.3, multi_scale: true, match_all: false, description: '优先级2' }, label: '目标图片 2', status: 'idle' },
          { id: ids.img3, type: 'image_click', x: 560, y: 340, params: { targets: [], similarity: 85, timeout: 0.5, method: 'single', offset_x: 0, offset_y: 0, delay_after: 0.3, multi_scale: true, match_all: false, description: '优先级3' }, label: '目标图片 3', status: 'idle' },
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
  {
    id: 'filedialog-save',
    name: '保存文件对话框',
    category: 'system',
    description: '触发保存快捷键(Ctrl+S) → 在保存对话框中输入路径并确认',
    createScript: () => {
      const ids = { start: nanoid(), hotkey: nanoid(), save: nanoid(), end: nanoid() };
      return {
        meta: { id: nanoid(), name: '保存文件对话框', description: 'Ctrl+S触发保存 → UIA自动化填写并确认', created_at: new Date().toISOString(), updated_at: new Date().toISOString(), version: '1.0.0' },
        nodes: [
          { id: ids.start, type: 'start', x: 400, y: 20, params: { label: '开始' }, status: 'idle' },
          { id: ids.hotkey, type: 'keyboard', x: 400, y: 120, params: { keys: ['s'], modifiers: ['ctrl'], delay_after: 2.0 }, status: 'idle', label: 'Ctrl+S 触发保存' },
          { id: ids.save, type: 'tool_call', x: 400, y: 240, params: { feature_name: 'filedialog_save', params: { path: '', overwrite: true, timeout: 15 } }, status: 'idle', label: '填写路径并保存' },
          { id: ids.end, type: 'end', x: 400, y: 360, params: { label: '结束' }, status: 'idle' },
        ],
        edges: [
          { id: nanoid(), from_node: ids.start, from_port: 'output', to_node: ids.hotkey, to_port: 'input' },
          { id: nanoid(), from_node: ids.hotkey, from_port: 'output', to_node: ids.save, to_port: 'input' },
          { id: nanoid(), from_node: ids.save, from_port: 'output', to_node: ids.end, to_port: 'input' },
        ],
        variables: {},
        settings: { ...DEFAULT_SETTINGS },
      };
    },
  },
  {
    id: 'filedialog-open',
    name: '打开文件对话框',
    category: 'system',
    description: '触发打开快捷键(Ctrl+O) → 在打开对话框中输入路径并确认',
    createScript: () => {
      const ids = { start: nanoid(), hotkey: nanoid(), open: nanoid(), end: nanoid() };
      return {
        meta: { id: nanoid(), name: '打开文件对话框', description: 'Ctrl+O触发打开 → UIA自动化填写并确认', created_at: new Date().toISOString(), updated_at: new Date().toISOString(), version: '1.0.0' },
        nodes: [
          { id: ids.start, type: 'start', x: 400, y: 20, params: { label: '开始' }, status: 'idle' },
          { id: ids.hotkey, type: 'keyboard', x: 400, y: 120, params: { keys: ['o'], modifiers: ['ctrl'], delay_after: 2.0 }, status: 'idle', label: 'Ctrl+O 触发打开' },
          { id: ids.open, type: 'tool_call', x: 400, y: 240, params: { feature_name: 'filedialog_open', params: { path: '', timeout: 15 } }, status: 'idle', label: '填写路径并打开' },
          { id: ids.end, type: 'end', x: 400, y: 360, params: { label: '结束' }, status: 'idle' },
        ],
        edges: [
          { id: nanoid(), from_node: ids.start, from_port: 'output', to_node: ids.hotkey, to_port: 'input' },
          { id: nanoid(), from_node: ids.hotkey, from_port: 'output', to_node: ids.open, to_port: 'input' },
          { id: nanoid(), from_node: ids.open, from_port: 'output', to_node: ids.end, to_port: 'input' },
        ],
        variables: {},
        settings: { ...DEFAULT_SETTINGS },
      };
    },
  },
  // ======== 复杂自动化模板 1: 多目标智能检测 ========
  {
    id: 'complex-image-pipeline',
    name: '多目标智能检测流水线',
    category: 'image',
    description: '多目标图片检测 + 条件分支 + OCR验证 + 失败重试 + 截图留证',
    createScript: () => {
      const ids: Record<string, string> = {};
      const id = (key: string) => { ids[key] = nanoid(); return ids[key]; };
      id('start'); id('loop'); id('cond_screen');
      id('img1'); id('img2'); id('img3'); id('img4'); id('img5');
      id('ocr_verify'); id('cond_ocr'); id('click_target');
      id('screenshot'); id('wait_after'); id('retry_check');
      id('end_succ'); id('end_fail');
      const N = (key: string, type: string, x: number, y: number, params: any, label?: string): FlowNode =>
        ({ id: ids[key], type: type as any, x, y, params, status: 'idle' as const, ...(label ? { label } : {}) });
      const E = (from: string, port: string, to: string) =>
        ({ id: nanoid(), from_node: ids[from], from_port: port, to_node: ids[to], to_port: 'input' });
      return {
        meta: { id: nanoid(), name: '多目标智能检测流水线', description: '5优先级图片检测→OCR验证→点击→截图留证', created_at: new Date().toISOString(), updated_at: new Date().toISOString(), version: '1.0.0' },
        nodes: [
          N('start','start',520,20,{label:'开始'}),
          N('loop','loop',520,140,{loop_type:'infinite'},'无限循环检测'),
          N('img1','image_click',40,320,{targets:[],similarity:90,timeout:2,method:'single',offset_x:0,offset_y:0,delay_after:0.3,multi_scale:true,match_all:false},'VIP图标'),
          N('img2','image_click',240,320,{targets:[],similarity:88,timeout:1.5,method:'single',offset_x:0,offset_y:0,delay_after:0.3,multi_scale:true,match_all:false},'高优图标'),
          N('img3','image_click',440,320,{targets:[],similarity:85,timeout:1,method:'single',offset_x:0,offset_y:0,delay_after:0.3},'中优图标'),
          N('img4','image_click',640,320,{targets:[],similarity:82,timeout:0.8,method:'single',offset_x:0,offset_y:0,delay_after:0.3},'普通图标'),
          N('img5','image_click',840,320,{targets:[],similarity:80,timeout:0.5,method:'single',offset_x:0,offset_y:0,delay_after:0.3},'兜底图标'),
          N('ocr_verify','click_text',520,460,{text:'确认|确定|完成|OK',timeout:3,single_match:true,method:'single',offset_x:0,offset_y:0},'OCR验证弹窗'),
          N('cond_ocr','condition',520,580,{condition_type:'always_true'},'验证是否通过'),
          N('click_target','click',240,700,{x:500,y:400,method:'single',delay_after:0.5},'点击目标区域'),
          N('screenshot','screenshot',520,700,{},'截图留证'),
          N('wait_after','wait',520,820,{wait_type:'time',duration:2},'等待2秒'),
          N('retry_check','condition',800,700,{condition_type:'always_true'},'检查重试次数'),
          N('end_succ','end',240,900,{label:'执行成功'}),
          N('end_fail','end',800,900,{label:'失败退出'}),
        ],
        edges: [
          E('start','output','loop'),
          E('loop','loop_body','img1'),
          E('img1','fail','img2'), E('img1','output','ocr_verify'),
          E('img2','fail','img3'), E('img2','output','ocr_verify'),
          E('img3','fail','img4'), E('img3','output','ocr_verify'),
          E('img4','fail','img5'), E('img4','output','ocr_verify'),
          E('img5','fail','retry_check'), E('img5','output','ocr_verify'),
          E('ocr_verify','output','cond_ocr'),
          E('cond_ocr','yes','click_target'), E('cond_ocr','no','retry_check'),
          E('click_target','output','screenshot'),
          E('screenshot','output','wait_after'),
          E('wait_after','output','loop'),
          E('loop','loop_end','end_succ'),
          { id: nanoid(), from_node: ids.retry_check, from_port: 'yes', to_node: ids.end_succ, to_port: 'input' },
          { id: nanoid(), from_node: ids.retry_check, from_port: 'no', to_node: ids.end_fail, to_port: 'input' },
        ],
        variables: { retry_count: '0' },
        settings: { default_delay: 0.5, error_handling: 'continue', max_retries: 3, retry_delay: 1, log_level: 'info' },
      };
    },
  },

  // ======== 复杂自动化模板 2: 网页数据采集 ========
  {
    id: 'complex-web-scraper',
    name: '网页数据采集与监控',
    category: 'app',
    description: '多轮页面数据采集 + 条件判断 + 截图保存 + 循环滚动 + 数据验证',
    createScript: () => {
      const ids: Record<string, string> = {};
      const id = (key: string) => { ids[key] = nanoid(); return ids[key]; };
      id('start'); id('loop_scroll'); id('scroll_down');
      id('wait_load'); id('screenshot_page');
      id('cond_has_data'); id('click_next'); id('wait_transition');
      id('collect_done'); id('end');
      const N = (key: string, type: string, x: number, y: number, params: any, label?: string): FlowNode =>
        ({ id: ids[key], type: type as any, x, y, params, status: 'idle' as const, ...(label ? { label } : {}) });
      const E = (from: string, port: string, to: string) =>
        ({ id: nanoid(), from_node: ids[from], from_port: port, to_node: ids[to], to_port: 'input' });
      return {
        meta: { id: nanoid(), name: '网页数据采集与监控', description: '自动滚动页面→截图保存→翻页→循环采集多页数据', created_at: new Date().toISOString(), updated_at: new Date().toISOString(), version: '1.0.0' },
        nodes: [
          N('start','start',360,20,{label:'开始采集'}),
          N('loop_scroll','loop',360,140,{loop_type:'count',count:5,description:'滚动采集5次'},'滚动采集循环'),
          N('scroll_down','swipe',120,280,{start_x:500,start_y:600,end_x:500,end_y:200,duration:0.5,delay_after:0.5},'向下滑动'),
          N('wait_load','wait',360,280,{wait_type:'time',duration:2,description:'等待内容加载'},'等待加载'),
          N('screenshot_page','screenshot',360,400,{description:'采集结果截图'},'截图保存'),
          N('cond_has_data','condition',120,520,{condition_type:'always_true'},'检查是否有下一页'),
          N('click_next','click',120,640,{x:700,y:750,method:'single',delay_after:1.5,description:'点击下一页按钮'},'点击下一页'),
          N('wait_transition','wait',360,640,{wait_type:'time',duration:3,description:'等待页面切换'},'等待翻页'),
          N('collect_done','screenshot',360,760,{description:'最终结果截图'},'最终截图'),
          N('end','end',360,880,{label:'采集完成'}),
        ],
        edges: [
          E('start','output','loop_scroll'),
          E('loop_scroll','loop_body','scroll_down'),
          E('scroll_down','output','wait_load'),
          E('wait_load','output','screenshot_page'),
          E('screenshot_page','output','cond_has_data'),
          E('cond_has_data','yes','click_next'),
          E('cond_has_data','no','loop_scroll'),
          E('click_next','output','wait_transition'),
          E('wait_transition','output','loop_scroll'),
          E('loop_scroll','loop_end','collect_done'),
          E('collect_done','output','end'),
        ],
        variables: {},
        settings: { default_delay: 1, error_handling: 'continue', max_retries: 2, retry_delay: 1, log_level: 'info' },
      };
    },
  },

  // ======== 复杂自动化模板 3: 智能表单提交流程 ========
  {
    id: 'complex-form-submit',
    name: '智能表单提交流程',
    category: 'app',
    description: '多步骤表单填写 + 实时OCR验证 + 失败重试 + 颜色状态检测 + 截图日志',
    createScript: () => {
      const ids: Record<string, string> = {};
      const id = (key: string) => { ids[key] = nanoid(); return ids[key]; };
      id('start'); id('open_page'); id('wait_form');
      id('input_name'); id('input_phone'); id('input_email');
      id('click_submit'); id('cond_validate');
      id('ocr_check'); id('cond_ocr_ok');
      id('screenshot_result'); id('color_status');
      id('retry_loop'); id('end_succ'); id('end_fail');
      const N = (key: string, type: string, x: number, y: number, params: any, label?: string): FlowNode =>
        ({ id: ids[key], type: type as any, x, y, params, status: 'idle' as const, ...(label ? { label } : {}) });
      const E = (from: string, port: string, to: string) =>
        ({ id: nanoid(), from_node: ids[from], from_port: port, to_node: ids[to], to_port: 'input' });
      return {
        meta: { id: nanoid(), name: '智能表单提交流程', description: '多字段表单→OCR验证→颜色状态检测→失败重试→截图', created_at: new Date().toISOString(), updated_at: new Date().toISOString(), version: '1.0.0' },
        nodes: [
          N('start','start',360,20,{label:'开始提交'}),
          N('open_page','click',360,120,{x:400,y:300,method:'double',delay_after:2,description:'双击打开表单页面'},'打开表单'),
          N('wait_form','wait',360,220,{wait_type:'time',duration:3,description:'等待表单加载完成'},'等待表单'),
          N('input_name','input_text',80,340,{text:'张三',click_before:true,clear_before:true,delay_after:0.5},'输入姓名'),
          N('input_phone','input_text',360,340,{text:'13800138000',click_before:true,clear_before:true,delay_after:0.5},'输入电话'),
          N('input_email','input_text',640,340,{text:'zhangsan@test.com',click_before:true,clear_before:true,delay_after:0.5},'输入邮箱'),
          N('click_submit','click',360,460,{x:500,y:600,method:'single',delay_after:1,description:'点击提交按钮'},'点击提交'),
          N('cond_validate','condition',360,580,{condition_type:'always_true'},'基本验证'),
          N('ocr_check','click_text',80,700,{text:'提交成功|成功提交|谢谢|感谢',timeout:5,single_match:true,method:'single',offset_x:0,offset_y:0},'OCR验证结果'),
          N('cond_ocr_ok','condition',360,820,{condition_type:'always_true'},'OCR验证判断'),
          N('screenshot_result','screenshot',80,940,{},'截图结果'),
          N('color_status','color_check',640,700,{x:400,y:400,w:60,h:40,expected_color:'30,200,200',tolerance:50,description:'检测提交按钮颜色变化'},'颜色状态检测'),
          N('retry_loop','loop',640,820,{loop_type:'count',count:3,description:'失败重试3次'},'重试循环'),
          N('end_succ','end',80,1060,{label:'提交成功'}),
          N('end_fail','end',640,1060,{label:'提交失败'}),
        ],
        edges: [
          E('start','output','open_page'),
          E('open_page','output','wait_form'),
          E('wait_form','output','input_name'),
          E('input_name','output','input_phone'),
          E('input_phone','output','input_email'),
          E('input_email','output','click_submit'),
          E('click_submit','output','cond_validate'),
          E('cond_validate','yes','ocr_check'),
          E('cond_validate','no','retry_loop'),
          E('ocr_check','output','cond_ocr_ok'),
          E('ocr_check','fail','retry_loop'),
          E('cond_ocr_ok','yes','color_status'),
          E('cond_ocr_ok','no','retry_loop'),
          E('color_status','output','screenshot_result'),
          E('screenshot_result','output','end_succ'),
          E('retry_loop','loop_body','click_submit'),
          E('retry_loop','loop_end','end_fail'),
        ],
        variables: { retry_count: '0' },
        settings: { default_delay: 0.5, error_handling: 'retry', max_retries: 3, retry_delay: 1, log_level: 'info' },
      };
    },
  },

  // ======== 复杂自动化模板 4: 批量文件处理 + 弹窗监控 ========
  {
    id: 'complex-batch-process',
    name: '批量文件处理监控',
    category: 'system',
    description: '批量处理多个项目 + 弹窗自动关闭 + 进度等待 + 颜色检测 + 并行分支截图',
    createScript: () => {
      const ids: Record<string, string> = {};
      const id = (key: string) => { ids[key] = nanoid(); return ids[key]; };
      id('start'); id('main_loop'); id('open_file');
      id('popup_monitor'); id('cond_popup');
      id('close_popup'); id('progress_wait');
      id('cond_progress'); id('process_action');
      id('color_check'); id('screenshot_a');
      id('screenshot_b'); id('wait_step');
      id('end');
      const N = (key: string, type: string, x: number, y: number, params: any, label?: string): FlowNode =>
        ({ id: ids[key], type: type as any, x, y, params, status: 'idle' as const, ...(label ? { label } : {}) });
      const E = (from: string, port: string, to: string) =>
        ({ id: nanoid(), from_node: ids[from], from_port: port, to_node: ids[to], to_port: 'input' });
      return {
        meta: { id: nanoid(), name: '批量文件处理监控', description: '10次循环处理→弹窗自动关闭→进度监控→颜色检测→并行截图', created_at: new Date().toISOString(), updated_at: new Date().toISOString(), version: '1.0.0' },
        nodes: [
          N('start','start',400,20,{label:'开始批量处理'}),
          N('main_loop','loop',400,140,{loop_type:'count',count:10,description:'批量处理10个项目'},'主循环 ×10'),
          N('open_file','click',400,260,{x:300,y:400,method:'double',delay_after:1,description:'双击打开项目文件'},'打开项目文件'),
          N('popup_monitor','region_wait',80,400,{region_type:'change',x:200,y:100,w:400,h:300,timeout:5,poll_interval:0.5},'监控弹窗出现'),
          N('cond_popup','condition',400,400,{condition_type:'window_exists'},'检查是否有弹窗'),
          N('close_popup','popup_close',80,540,{strategies:['close_button','enter','esc'],delay_after:0.5},'关闭弹窗'),
          N('progress_wait','progress_wait',400,540,{wait_type:'fill',region:{x:300,y:500,w:400,h:20},timeout:60,poll_interval:0.5,description:'等待进度条满'},'等待进度完成'),
          N('cond_progress','condition',400,660,{condition_type:'always_true'},'进度结果判断'),
          N('process_action','click',200,780,{x:500,y:550,method:'single',delay_after:0.5,description:'点击下一步处理'},'执行处理操作'),
          N('color_check','color_check',600,780,{x:350,y:450,w:40,h:30,expected_color:'120,200,100',tolerance:60},'处理状态灯检测'),
          N('screenshot_a','screenshot',200,900,{},'结果截图A'),
          N('screenshot_b','screenshot',600,900,{},'结果截图B'),
          N('wait_step','wait',400,1020,{wait_type:'time',duration:1,description:'处理间隔'},'间隔等待'),
          N('end','end',400,1140,{label:'批量处理完成'}),
        ],
        edges: [
          E('start','output','main_loop'),
          E('main_loop','loop_body','open_file'),
          E('open_file','output','popup_monitor'),
          E('popup_monitor','output','cond_popup'),
          E('cond_popup','yes','close_popup'),
          E('cond_popup','no','progress_wait'),
          E('close_popup','output','progress_wait'),
          E('progress_wait','output','cond_progress'),
          E('cond_progress','yes','process_action'),
          E('cond_progress','no','main_loop'),
          E('process_action','output','color_check'),
          // Parallel branches: both screenshot A and B execute
          { id: nanoid(), from_node: ids.color_check, from_port: 'output', to_node: ids.screenshot_a, to_port: 'input' },
          { id: nanoid(), from_node: ids.color_check, from_port: 'output', to_node: ids.screenshot_b, to_port: 'input' },
          E('screenshot_a','output','wait_step'),
          E('screenshot_b','output','wait_step'),
          E('wait_step','output','main_loop'),
          E('main_loop','loop_end','end'),
        ],
        variables: { processed: '0', failed: '0' },
        settings: { default_delay: 0.5, error_handling: 'continue', max_retries: 2, retry_delay: 1, log_level: 'info' },
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
  setViewMode: (mode: 'canvas' | 'pseudocode' | 'settings' | 'agent') => void;
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
    const needed = required[node.type as keyof typeof required];
    if (!needed) continue;

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
      const W = window.innerWidth - 120;
      const H = window.innerHeight - 120;
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
      console.log('[CONNECT] startConnecting', nodeId, portId);
      set((state) => {
        state.ui.connecting = { active: true, from_node: nodeId, from_port: portId };
      });
    },

    stopConnecting: () => {
      console.log('[CONNECT] stopConnecting');
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

const PRESET_TEMPLATE_META: Array<{
  id: string; name: string; description: string; category: string;
  aliases: string[]; keywords: string[]; tags: string[];
}> = [
  { id: 'desktop-monitor', name: '桌面图标监控', description: '循环检测桌面多张目标图片，按优先级点击第一个匹配的', category: 'image',
    aliases: ['桌面监控', '图标监控', 'monitor'], keywords: ['监控', '检测', '桌面', '图标', '图片'], tags: ['image', 'monitor', 'loop'] },
  { id: 'login-automation', name: '登录自动化', description: '自动打开登录页，输入账号密码，点击登录，截图确认结果', category: 'app',
    aliases: ['登录', 'login', 'signin', '登录流程', '自动登录'], keywords: ['账号', '密码', '登录', '认证', '表单'], tags: ['app', 'login', 'form'] },
  { id: 'form-filler', name: '表单自动填写', description: '自动填写表单字段，支持文本输入和下拉选择', category: 'app',
    aliases: ['填表', '表单', 'form', '自动填表'], keywords: ['表单', '填写', '输入', '字段'], tags: ['app', 'form', 'input'] },
  { id: 'app-launcher', name: '应用快速启动', description: '启动应用程序并等待窗口出现', category: 'system',
    aliases: ['启动', 'launcher', '启动应用'], keywords: ['启动', '应用', '程序', '打开'], tags: ['system', 'launch'] },
  { id: 'screenshot-logger', name: '定时截图记录', description: '按指定时间间隔循环截图，用于监控和记录', category: 'image',
    aliases: ['截图', '录屏', 'screenshot', '定时截图'], keywords: ['截图', '定时', '监控', '记录', '捕获'], tags: ['image', 'screenshot', 'timer'] },
  { id: 'filedialog-save', name: '保存文件对话框', description: '在文件保存对话框中输入路径并确认保存', category: 'system',
    aliases: ['保存', '保存文件', '另存为', 'save-as'], keywords: ['保存', '文件', '对话框', '保存文件'], tags: ['system', 'dialog', 'file'] },
  { id: 'filedialog-open', name: '打开文件对话框', description: '在文件打开对话框中输入路径并确认打开', category: 'system',
    aliases: ['打开文件', '打开', 'open'], keywords: ['打开', '文件', '对话框', '打开文件'], tags: ['system', 'dialog', 'file'] },
  { id: 'complex-image-pipeline', name: '多目标图像流水线', description: '多目标图像检测流水线，支持优先级、兜底、重试', category: 'image',
    aliases: ['图像流水线', '图片流水线', 'image-pipeline'], keywords: ['图片', '图像', '检测', '流水线', '优先级', '兜底'], tags: ['image', 'detection', 'pipeline'] },
  { id: 'complex-web-scraper', name: '网页数据采集', description: '模拟滑动和等待，采集网页动态内容', category: 'app',
    aliases: ['采集', '爬虫', '数据采集', 'scraper', 'web'], keywords: ['网页', '采集', '数据', '滑动', '抓取'], tags: ['app', 'scraper', 'data'] },
  { id: 'complex-form-submit', name: '智能表单提交', description: '复杂表单填写提交，含字段验证和OCR结果确认', category: 'app',
    aliases: ['提交表单', '表单提交', 'form-submit'], keywords: ['表单', '提交', '验证', 'OCR', '字段'], tags: ['app', 'form', 'submit', 'ocr'] },
  { id: 'complex-batch-process', name: '批量文件处理', description: '批量处理文件，含弹窗监控、颜色检测状态灯', category: 'system',
    aliases: ['批量处理', '批处理', 'batch'], keywords: ['批量', '文件', '处理', '弹窗', '颜色检测'], tags: ['system', 'batch', 'file'] },
];

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

const STORAGE_KEY = 'lingdong-script-data';
const RECENT_KEY = 'lingdong-recent';
const CUSTOM_TEMPLATES_KEY = 'lingdong-custom-templates';

export interface CustomTemplate {
  id: string;
  name: string;
  description: string;
  script: FlowScript;
  shortcut?: string;
}

// ── Template Shortcut Registry ──
const TEMPLATE_SHORTCUTS_KEY = 'lingdong-template-shortcuts';

export function loadTemplateShortcuts(): Record<string, string> {
  try {
    const saved = localStorage.getItem(TEMPLATE_SHORTCUTS_KEY);
    return saved ? JSON.parse(saved) : {};
  } catch { return {}; }
}

export function saveTemplateShortcut(templateId: string, shortcut: string) {
  try {
    const all = loadTemplateShortcuts();
    if (shortcut) all[templateId] = shortcut;
    else delete all[templateId];
    localStorage.setItem(TEMPLATE_SHORTCUTS_KEY, JSON.stringify(all));
  } catch {}
}

export function deleteTemplateShortcut(templateId: string) {
  saveTemplateShortcut(templateId, '');
}

export function loadCustomTemplates(): CustomTemplate[] {
  try {
    const saved = localStorage.getItem(CUSTOM_TEMPLATES_KEY);
    return saved ? JSON.parse(saved) : [];
  } catch { return []; }
}

export function saveCustomTemplates(templates: CustomTemplate[]) {
  try {
    localStorage.setItem(CUSTOM_TEMPLATES_KEY, JSON.stringify(templates));
  } catch {}
}

// ── Agent History Persistence ──

const AGENT_HISTORY_KEY = 'lingdong-agent-history';

export function loadAgentHistory(): AgentRunRecord[] {
  try {
    const saved = localStorage.getItem(AGENT_HISTORY_KEY);
    return saved ? JSON.parse(saved) : [];
  } catch { return []; }
}

export function saveAgentHistory(history: AgentRunRecord[]) {
  try {
    localStorage.setItem(AGENT_HISTORY_KEY, JSON.stringify(history.slice(0, 50)));
  } catch {}
}

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

    // Agent message dedup guard: track last agent_done to prevent duplicate handling
    let _agentDoneGoal = '';
    let _agentStartTime = 0;

    _listenerCleanups.push(_api.onAgentMessage((msg: Record<string, unknown>) => {
      const msgType = typeof msg?.type === 'string' ? msg.type : '';
      if (!msgType) return;

      if (msgType === 'plan_state') {
        const planData = msg.plan as Record<string, unknown> | undefined;
        if (!planData?.nodes) return;
        const nodes = (planData.nodes as PlanNodeState[]) ?? [];
        useStore.setState((s) => {
          s.execution.plan = {
            goal: (planData.goal as string) ?? '',
            status: (planData.status as string) ?? 'running',
            nodes,
            summary: planData.summary as PlanDAGState['summary'],
          };
          const runningNode = nodes.find((n: PlanNodeState) => n.status === 'running');
          const failedNode = nodes.find((n: PlanNodeState) => n.status === 'failed');
          s.execution.current_node_id = runningNode?.id ?? failedNode?.id ?? s.execution.current_node_id;
          if (msg.final) {
            s.execution.status = planData.status === 'completed' ? 'completed' : 'error';
            s.execution.current_node_id = undefined;
          }
        });
        return;
      }

      if (msgType === 'step_attempt') {
        const nodeId = typeof msg.node_id === 'string' ? msg.node_id : '';
        const tool = typeof msg.tool === 'string' ? msg.tool : '';
        if (!nodeId) return;
        useStore.setState((s) => {
          const node = s.execution.plan?.nodes?.find((n: PlanNodeState) => n.id === nodeId);
          if (node) {
            node.attempts = (node.attempts ?? 0) + 1;
            if (tool) node.tool = tool;
          }
        });
        return;
      }

      if (msgType === 'step_complete') {
        const tool = typeof msg.tool === 'string' ? msg.tool : '';
        const result = msg.result as Record<string, unknown> | undefined;
        if (tool === 'generate_flow_script' && result?.success) {
          const data = (result.data as Record<string, unknown>) ?? {};
          const dataResult = data.result as Record<string, unknown> | undefined;
          const pseudocode =
            (data.pseudocode as string) ??
            (dataResult?.pseudocode as string) ??
            ((dataResult?.result as Record<string, unknown> | undefined)?.pseudocode as string);
          if (pseudocode) {
            import('../utils/pseudoParser').then(({ parsePseudoToFlow }) => {
              try {
                const script = parsePseudoToFlow(pseudocode);
                const store = useStore.getState();
                script.meta = { ...script.meta, id: store.script.meta.id, name: store.script.meta.name + ' (Agent)' };
                store.loadScript(script);
                store.setViewMode('canvas');
              } catch (e) { console.error('[agent] loadScript failed:', e); }
            });
          }
        } else if (tool !== 'generate_flow_script' && result) {
          useStore.setState((s) => {
            s.execution.status = result.success ? 'running' : 'error';
            const stepNum = typeof msg.step_num === 'number' ? msg.step_num : undefined;
            s.execution.current_node_id = stepNum != null ? `agent_step_${stepNum}` : undefined;
          });
        }
        return;
      }

      if (msgType === 'agent_start') {
        // Reset state on new run; clear dedup guard; record start time
        _agentDoneGoal = '';
        _agentStartTime = Date.now();
        useStore.setState((s) => {
          s.execution.plan = undefined;
        });
        return;
      }

      if (msgType === 'agent_done') {
        const status = typeof msg.status === 'string' ? msg.status : 'failed';
        const totalSteps = typeof msg.total_steps === 'number' ? msg.total_steps : 0;
        const goal = typeof msg.goal === 'string' ? msg.goal : '';
        const failReason = typeof msg.fail_reason === 'string' ? msg.fail_reason : '';
        const summaryText = typeof msg.summary === 'string' ? msg.summary : '';

        // Dedup: agent_done with same goal should only fire once
        if (goal && _agentDoneGoal === goal) return;
        if (goal) _agentDoneGoal = goal;

        useStore.setState((s) => {
          s.execution.status = status === 'completed' ? 'completed' : 'error';
          s.execution.current_node_id = undefined;
        });

        // Record history
        const plan = useStore.getState().execution.plan;
        const elapsed = _agentStartTime > 0 ? Math.round((Date.now() - _agentStartTime) / 1000) : 0;
        const record: AgentRunRecord = {
          id: Math.random().toString(36).slice(2, 10),
          goal: goal || plan?.goal || '',
          status: status === 'completed' ? 'completed' : status === 'stopped' ? 'stopped' : 'failed',
          total_steps: totalSteps,
          completed_steps: plan?.summary?.completed ?? 0,
          failed_steps: plan?.summary?.failed ?? 0,
          skipped_steps: plan?.summary?.skipped ?? 0,
          started_at: new Date(_agentStartTime || Date.now()).toISOString(),
          finished_at: new Date().toISOString(),
          elapsed_seconds: elapsed,
          summary: summaryText || failReason || '',
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
