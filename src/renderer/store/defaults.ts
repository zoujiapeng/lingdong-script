/** Store default values and preset templates */

import { nanoid } from 'nanoid';
import type {
  FlowScript, FlowNode, FlowEdge, UIState,
} from '../types';

export const DEFAULT_SETTINGS = {
  default_delay: 0.5,
  error_handling: 'stop' as const,
  max_retries: 3,
  retry_delay: 1,
  log_level: 'info' as const,
};

export const DEFAULT_UI_STATE: UIState = {
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
  viewMode: 'overview',
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

export const MAX_HISTORY = 50;

export const STORAGE_KEY = 'lingdong-script-data';
export const RECENT_KEY = 'lingdong-recent';
export const CUSTOM_TEMPLATES_KEY = 'lingdong-custom-templates';
export const AGENT_HISTORY_KEY = 'lingdong-agent-history';
export const TEMPLATE_SHORTCUTS_KEY = 'lingdong-template-shortcuts';

// ── Preset Templates ──

interface ScriptTemplate {
  id: string;
  name: string;
  description: string;
  category: string;
  createScript: () => FlowScript;
}

export const PRESET_TEMPLATES: ScriptTemplate[] = [
  {
    id: 'arp-quickstart', name: 'ARP 低延迟起步流程', category: 'agent',
    description: '可直接验证和运行的 Agent Runtime Platform 起步流程',
    createScript: () => {
      const ids = { start: nanoid(), batch: nanoid(), evidence: nanoid(), end: nanoid() };
      return {
        meta: {
          id: nanoid(),
          name: 'ARP 低延迟起步流程',
          description: '控制器批处理、证据门和确定性终态组成的安全起步流程',
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          version: '2.0.0',
        },
        nodes: [
          { id: ids.start, type: 'start', x: 360, y: 40, params: { label: 'Start' }, label: 'Start', status: 'idle' },
          {
            id: ids.batch,
            type: 'action_batch',
            x: 360,
            y: 180,
            params: {
              subgoal: 'Establish a stable controller baseline',
              actions: [{ action: 'wait', duration_ms: 80 }],
              stop_on_error: true,
              sync: false,
            },
            label: 'Controller batch',
            status: 'idle',
          },
          {
            id: ids.evidence,
            type: 'evidence_gate',
            x: 360,
            y: 340,
            params: { required: [] },
            label: 'Evidence gate',
            status: 'idle',
          },
          { id: ids.end, type: 'end', x: 360, y: 500, params: { label: 'Done' }, label: 'Done', status: 'idle' },
        ],
        edges: [
          { id: nanoid(), from_node: ids.start, from_port: 'output', to_node: ids.batch, to_port: 'input' },
          { id: nanoid(), from_node: ids.batch, from_port: 'output', to_node: ids.evidence, to_port: 'input' },
          { id: nanoid(), from_node: ids.evidence, from_port: 'pass', to_node: ids.end, to_port: 'input' },
        ],
        variables: {},
        settings: { ...DEFAULT_SETTINGS },
      };
    },
  },
  {
    id: 'desktop-monitor', name: '桌面图标监控', category: 'image',
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
    id: 'login-automation', name: '登录自动化', category: 'app',
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
    id: 'form-filler', name: '表单填写器', category: 'app',
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
    id: 'app-launcher', name: '应用启动器', category: 'app',
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
    id: 'screenshot-logger', name: '定时截图记录', category: 'system',
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
    id: 'filedialog-save', name: '保存文件对话框', category: 'system',
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
    id: 'complex-image-pipeline', name: '多目标图像流水线', category: 'image',
    description: '多目标图像检测流水线，支持优先级、兜底、重试',
    createScript: () => {
      const ids = { start: nanoid(), loop: nanoid(), img1: nanoid(), img2: nanoid(), popup: nanoid(), screenshot: nanoid(), end: nanoid() };
      return {
        meta: { id: nanoid(), name: '多目标图像流水线', description: '多目标图像检测流水线', created_at: new Date().toISOString(), updated_at: new Date().toISOString(), version: '1.0.0' },
        nodes: [
          { id: ids.start, type: 'start', x: 360, y: 20, params: { label: '开始' }, status: 'idle' },
          { id: ids.loop, type: 'loop', x: 360, y: 120, params: { loop_type: 'infinite' }, status: 'idle', label: '循环检测' },
          { id: ids.img1, type: 'image_click', x: 120, y: 280, params: { targets: ['target1.png'], similarity: 85, timeout: 2, method: 'single', multi_scale: true, match_all: false }, label: '主目标', status: 'idle' },
          { id: ids.img2, type: 'image_click', x: 360, y: 280, params: { targets: ['target2.png'], similarity: 80, timeout: 2, method: 'single' }, label: '次目标', status: 'idle' },
          { id: ids.popup, type: 'popup_close', x: 600, y: 280, params: { strategies: ['close', 'esc', 'click_away'], timeout: 3 }, label: '弹窗关闭', status: 'idle' },
          { id: ids.screenshot, type: 'screenshot', x: 360, y: 420, params: { description: '结果截图' }, label: '截图', status: 'idle' },
          { id: ids.end, type: 'end', x: 360, y: 540, params: { label: '结束' }, status: 'idle' },
        ],
        edges: [
          { id: nanoid(), from_node: ids.start, from_port: 'output', to_node: ids.loop, to_port: 'input' },
          { id: nanoid(), from_node: ids.loop, from_port: 'loop_body', to_node: ids.img1, to_port: 'input' },
          { id: nanoid(), from_node: ids.img1, from_port: 'fail', to_node: ids.img2, to_port: 'input' },
          { id: nanoid(), from_node: ids.img2, from_port: 'output', to_node: ids.popup, to_port: 'input' },
          { id: nanoid(), from_node: ids.popup, from_port: 'output', to_node: ids.screenshot, to_port: 'input' },
          { id: nanoid(), from_node: ids.screenshot, from_port: 'output', to_node: ids.loop, to_port: 'loop_end' },
        ],
        variables: {},
        settings: { ...DEFAULT_SETTINGS, error_handling: 'continue' },
      };
    },
  },
  {
    id: 'complex-web-scraper', name: '网页数据采集', category: 'app',
    description: '模拟滑动和等待，采集网页动态内容',
    createScript: () => {
      const ids = { start: nanoid(), loop: nanoid(), scroll: nanoid(), wait: nanoid(), screenshot: nanoid(), end: nanoid() };
      return {
        meta: { id: nanoid(), name: '网页数据采集', description: '模拟滑动和等待采集网页内容', created_at: new Date().toISOString(), updated_at: new Date().toISOString(), version: '1.0.0' },
        nodes: [
          { id: ids.start, type: 'start', x: 360, y: 20, params: { label: '开始' }, status: 'idle' },
          { id: ids.loop, type: 'loop', x: 360, y: 120, params: { loop_type: 'count', count: 3 }, label: '循环滑动3次', status: 'idle' },
          { id: ids.scroll, type: 'swipe', x: 100, y: 280, params: { start_x: 500, start_y: 700, end_x: 500, end_y: 100, duration: 1.5 }, label: '向下滑动', status: 'idle' },
          { id: ids.wait, type: 'wait', x: 360, y: 280, params: { wait_type: 'time', duration: 2 }, label: '等待加载', status: 'idle' },
          { id: ids.screenshot, type: 'screenshot', x: 360, y: 420, params: { description: '采集结果' }, label: '截图', status: 'idle' },
          { id: ids.end, type: 'end', x: 360, y: 540, params: { label: '结束' }, status: 'idle' },
        ],
        edges: [
          { id: nanoid(), from_node: ids.start, from_port: 'output', to_node: ids.loop, to_port: 'input' },
          { id: nanoid(), from_node: ids.loop, from_port: 'loop_body', to_node: ids.scroll, to_port: 'input' },
          { id: nanoid(), from_node: ids.scroll, from_port: 'output', to_node: ids.wait, to_port: 'input' },
          { id: nanoid(), from_node: ids.wait, from_port: 'output', to_node: ids.loop, to_port: 'loop_end' },
          { id: nanoid(), from_node: ids.loop, from_port: 'loop_end', to_node: ids.screenshot, to_port: 'input' },
          { id: nanoid(), from_node: ids.screenshot, from_port: 'output', to_node: ids.end, to_port: 'input' },
        ],
        variables: {},
        settings: { ...DEFAULT_SETTINGS },
      };
    },
  },
  {
    id: 'complex-form-submit', name: '智能表单提交', category: 'app',
    description: '复杂表单填写提交，含字段验证和OCR结果确认',
    createScript: () => {
      const ids = { start: nanoid(), click1: nanoid(), input1: nanoid(), click2: nanoid(), input2: nanoid(), submit: nanoid(), wait: nanoid(), ocr: nanoid(), screenshot: nanoid(), end: nanoid() };
      return {
        meta: { id: nanoid(), name: '智能表单提交', description: '复杂表单填写提交', created_at: new Date().toISOString(), updated_at: new Date().toISOString(), version: '1.0.0' },
        nodes: [
          { id: ids.start, type: 'start', x: 360, y: 20, params: { label: '开始' }, status: 'idle' },
          { id: ids.click1, type: 'click', x: 160, y: 140, params: { x: 400, y: 350, method: 'single', delay_after: 0.5 }, label: '点击字段1', status: 'idle' },
          { id: ids.input1, type: 'input_text', x: 160, y: 240, params: { text: 'value1', click_before: false, clear_before: true, delay_after: 0.3 }, label: '输入字段1', status: 'idle' },
          { id: ids.click2, type: 'click', x: 360, y: 140, params: { x: 400, y: 420, method: 'single', delay_after: 0.5 }, label: '点击字段2', status: 'idle' },
          { id: ids.input2, type: 'input_text', x: 360, y: 240, params: { text: 'value2', click_before: false, clear_before: true, delay_after: 0.3 }, label: '输入字段2', status: 'idle' },
          { id: ids.submit, type: 'click', x: 560, y: 140, params: { x: 500, y: 550, method: 'single', delay_after: 2.0 }, label: '提交', status: 'idle' },
          { id: ids.wait, type: 'wait', x: 560, y: 240, params: { wait_type: 'time', duration: 2 }, label: '等待', status: 'idle' },
          { id: ids.ocr, type: 'click_text', x: 360, y: 360, params: { text: '确认', similarity: 85, timeout: 5 }, label: 'OCR确认', status: 'idle' },
          { id: ids.screenshot, type: 'screenshot', x: 360, y: 480, params: { description: '提交结果' }, label: '截图', status: 'idle' },
          { id: ids.end, type: 'end', x: 360, y: 600, params: { label: '结束' }, status: 'idle' },
        ],
        edges: [
          { id: nanoid(), from_node: ids.start, from_port: 'output', to_node: ids.click1, to_port: 'input' },
          { id: nanoid(), from_node: ids.click1, from_port: 'output', to_node: ids.input1, to_port: 'input' },
          { id: nanoid(), from_node: ids.input1, from_port: 'output', to_node: ids.click2, to_port: 'input' },
          { id: nanoid(), from_node: ids.click2, from_port: 'output', to_node: ids.input2, to_port: 'input' },
          { id: nanoid(), from_node: ids.input2, from_port: 'output', to_node: ids.submit, to_port: 'input' },
          { id: nanoid(), from_node: ids.submit, from_port: 'output', to_node: ids.wait, to_port: 'input' },
          { id: nanoid(), from_node: ids.wait, from_port: 'output', to_node: ids.ocr, to_port: 'input' },
          { id: nanoid(), from_node: ids.ocr, from_port: 'output', to_node: ids.screenshot, to_port: 'input' },
          { id: nanoid(), from_node: ids.screenshot, from_port: 'output', to_node: ids.end, to_port: 'input' },
        ],
        variables: {},
        settings: { ...DEFAULT_SETTINGS },
      };
    },
  },
  {
    id: 'complex-batch-process', name: '批量文件处理', category: 'system',
    description: '批量处理文件，含弹窗监控、颜色检测状态灯',
    createScript: () => {
      const ids = { start: nanoid(), loop: nanoid(), click: nanoid(), popup: nanoid(), color: nanoid(), screenshot: nanoid(), end: nanoid() };
      return {
        meta: { id: nanoid(), name: '批量文件处理', description: '批量处理文件', created_at: new Date().toISOString(), updated_at: new Date().toISOString(), version: '1.0.0' },
        nodes: [
          { id: ids.start, type: 'start', x: 360, y: 20, params: { label: '开始' }, status: 'idle' },
          { id: ids.loop, type: 'loop', x: 360, y: 120, params: { loop_type: 'count', count: 5 }, label: '批量循环5次', status: 'idle' },
          { id: ids.click, type: 'click', x: 360, y: 240, params: { x: 500, y: 400, method: 'single' }, status: 'idle' },
          { id: ids.popup, type: 'popup_close', x: 120, y: 360, params: { strategies: ['close', 'esc'], timeout: 3 }, label: '弹窗处理', status: 'idle' },
          { id: ids.color, type: 'color_check', x: 360, y: 360, params: { x: 100, y: 100, w: 20, h: 20, expected_color: '#00ff00', tolerance: 30 }, label: '状态灯', status: 'idle' },
          { id: ids.screenshot, type: 'screenshot', x: 600, y: 360, params: { description: '处理结果' }, label: '截图', status: 'idle' },
          { id: ids.end, type: 'end', x: 360, y: 480, params: { label: '结束' }, status: 'idle' },
        ],
        edges: [
          { id: nanoid(), from_node: ids.start, from_port: 'output', to_node: ids.loop, to_port: 'input' },
          { id: nanoid(), from_node: ids.loop, from_port: 'loop_body', to_node: ids.click, to_port: 'input' },
          { id: nanoid(), from_node: ids.click, from_port: 'output', to_node: ids.popup, to_port: 'input' },
          { id: nanoid(), from_node: ids.popup, from_port: 'output', to_node: ids.color, to_port: 'input' },
          { id: nanoid(), from_node: ids.color, from_port: 'yes', to_node: ids.screenshot, to_port: 'input' },
          { id: nanoid(), from_node: ids.color, from_port: 'no', to_node: ids.loop, to_port: 'loop_end' },
          { id: nanoid(), from_node: ids.screenshot, from_port: 'output', to_node: ids.loop, to_port: 'loop_end' },
        ],
        variables: {},
        settings: { ...DEFAULT_SETTINGS, error_handling: 'continue' },
      };
    },
  },
];

export const PRESET_TEMPLATE_META = [
  { id: 'arp-quickstart', name: 'ARP 低延迟起步流程', description: '可直接验证和运行的 Agent Runtime Platform 起步流程', category: 'agent',
    aliases: ['ARP', 'GUI Agent', '低延迟流程', 'agent workflow'], keywords: ['Agent', 'ARP', '低延迟', '编排', '批处理'], tags: ['agent', 'arp', 'workflow'] },
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
] as const;
