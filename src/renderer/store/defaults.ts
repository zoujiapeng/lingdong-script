/** Store default values and executable preset templates. */

import { nanoid } from 'nanoid';
import type { FlowScript, UIState } from '../types';

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
  themeMode: (() => {
    try {
      const saved = localStorage.getItem('lingdong-theme-mode');
      if (saved === 'light' || saved === 'dark' || saved === 'system') return saved;
    } catch {
      // Storage can be unavailable in tests or privacy-restricted contexts.
    }
    return 'system';
  })(),
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

export interface ScriptTemplate {
  id: string;
  name: string;
  description: string;
  category: string;
  aliases: string[];
  keywords: string[];
  tags: string[];
  createScript: () => FlowScript;
}

function meta(name: string, description: string) {
  const timestamp = new Date().toISOString();
  return {
    id: nanoid(),
    name,
    description,
    created_at: timestamp,
    updated_at: timestamp,
    version: '2.0.0',
  };
}

export const PRESET_TEMPLATES: ScriptTemplate[] = [
  {
    id: 'blank-flow',
    name: 'Blank flow',
    description: 'Start and end only; add the steps you need.',
    category: 'general',
    aliases: ['blank', 'empty flow', 'minimal flow'],
    keywords: ['flow', 'blank', 'offline'],
    tags: ['flow', 'minimal'],
    createScript: () => {
      const start = nanoid();
      const end = nanoid();
      return {
        meta: meta('Blank flow', 'Start and end only; add the steps you need.'),
        nodes: [
          { id: start, type: 'start', x: 360, y: 40, params: { label: 'Start' }, label: 'Start', status: 'idle' },
          { id: end, type: 'end', x: 360, y: 180, params: { label: 'End' }, label: 'End', status: 'idle' },
        ],
        edges: [
          { id: nanoid(), from_node: start, from_port: 'output', to_node: end, to_port: 'input' },
        ],
        variables: {},
        settings: { ...DEFAULT_SETTINGS },
      };
    },
  },
  {
    id: 'arp-quickstart',
    name: 'ARP 低延迟起步流程',
    description: '控制器批处理、人工批准和类型化证据门组成的通用起步流程',
    category: 'agent',
    aliases: ['ARP', 'GUI Agent', '低延迟流程', 'agent workflow'],
    keywords: ['Agent', 'ARP', '低延迟', '编排', '证据'],
    tags: ['agent', 'arp', 'workflow'],
    createScript: () => {
      const ids = {
        start: nanoid(),
        batch: nanoid(),
        approval: nanoid(),
        evidence: nanoid(),
        end: nanoid(),
      };
      return {
        meta: meta('ARP 低延迟起步流程', '控制器批处理、人工批准和类型化证据门'),
        nodes: [
          { id: ids.start, type: 'start', x: 360, y: 40, params: { label: '开始' }, label: '开始', status: 'idle' },
          {
            id: ids.batch,
            type: 'action_batch',
            x: 360,
            y: 180,
            params: {
              subgoal: '建立控制器执行基线',
              actions: [{ action: 'wait', duration_ms: 80 }],
              stop_on_error: true,
              sync: false,
            },
            label: '控制器批次',
            status: 'idle',
          },
          {
            id: ids.approval,
            type: 'human_gate',
            x: 360,
            y: 330,
            params: { message: '确认继续完成当前流程', timeout_s: 3600 },
            label: '人工批准',
            status: 'idle',
          },
          {
            id: ids.evidence,
            type: 'evidence_gate',
            x: 360,
            y: 480,
            params: {
              required: [{ predicate: 'human.approved', source: 'human_gate', equals: true }],
            },
            label: '证据门',
            status: 'idle',
          },
          { id: ids.end, type: 'end', x: 360, y: 630, params: { label: '完成' }, label: '完成', status: 'idle' },
        ],
        edges: [
          { id: nanoid(), from_node: ids.start, from_port: 'output', to_node: ids.batch, to_port: 'input' },
          { id: nanoid(), from_node: ids.batch, from_port: 'output', to_node: ids.approval, to_port: 'input' },
          { id: nanoid(), from_node: ids.approval, from_port: 'approved', to_node: ids.evidence, to_port: 'input' },
          { id: nanoid(), from_node: ids.evidence, from_port: 'pass', to_node: ids.end, to_port: 'input' },
        ],
        variables: {},
        settings: { ...DEFAULT_SETTINGS },
      };
    },
  },
];

export const PRESET_TEMPLATE_META = PRESET_TEMPLATES.map(({ createScript: _createScript, ...template }) => template);
