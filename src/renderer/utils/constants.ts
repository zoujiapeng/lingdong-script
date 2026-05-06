"use client";

// ── Node dimensions ──

export const NODE_DIMENSIONS = {
  normal: { w: 220, h: 80 },
  startEnd: { w: 160, h: 56 },
  subScript: { w: 240, h: 96 },
} as const;

export function getNodeDimensions(type: string) {
  if (type === 'start' || type === 'end') return NODE_DIMENSIONS.startEnd;
  if (type === 'sub_script') return NODE_DIMENSIONS.subScript;
  return NODE_DIMENSIONS.normal;
}

// ── View modes ──

export type ViewMode = 'canvas' | 'pseudocode' | 'settings';

// ── Execution status ──

export type ExecutionStatus = 'idle' | 'running' | 'paused' | 'completed' | 'error';

// ── Default values ──

export const DEFAULT_FLOW_NAME = '未命名流程';

export const DEFAULT_WAIT_TIMEOUT = 10;

export const DEFAULT_SIMILARITY = 0.8;

export const DEFAULT_LLM_API_BASE = 'https://api.deepseek.com/v1';

export const DEFAULT_LLM_MODEL = 'deepseek-chat';

// ── LLM storage keys ──

export const STORAGE_KEY_API_KEY = 'lingdong-deepseek-key';
export const STORAGE_KEY_API_BASE = 'lingdong-llm-base';
export const STORAGE_KEY_MODEL = 'lingdong-llm-model';

// ── Canvas ──

export const CANVAS_BACKGROUND_COLOR = '#f8f9fa';

export const SNAP_TO_GRID = 10;

export const MIN_ZOOM = 0.1;
export const MAX_ZOOM = 4;

// ── Categories (node library) ──

export const NODE_CATEGORIES = {
  action: { label: '动作', color: 'var(--accent)' },
  perception: { label: '感知', color: '#8b5cf6' },
  control: { label: '控制流', color: '#f59e0b' },
  system: { label: '系统', color: '#ef4444' },
  variable: { label: '变量', color: '#10b981' },
  debug: { label: '调试', color: '#6b7280' },
  interaction: { label: '交互', color: '#ec4899' },
  timer: { label: '定时', color: '#14b8a6' },
  loop: { label: '循环', color: '#f59e0b' },
  condition: { label: '条件', color: '#8b5cf6' },
} as const;

// ── Edge ──

export const EDGE_DEFAULT_COLOR = '#b1b1b7';
export const EDGE_ACTIVE_COLOR = '#22c55e';
export const EDGE_ERROR_COLOR = '#ef4444';
