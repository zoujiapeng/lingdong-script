// ============================================================
// CORE DATA TYPES - Flow Script Engine
// ============================================================

export type NodeType =
  | 'start'
  | 'end'
  | 'click'
  | 'image_click'
  | 'wait'
  | 'input_text'
  | 'keyboard'
  | 'swipe'
  | 'condition'
  | 'loop'
  | 'sub_script'
  | 'screenshot'
  | 'variable_set'
  | 'variable_get';

export type ConditionType =
  | 'image_exists'
  | 'window_exists'
  | 'variable_equals'
  | 'variable_greater'
  | 'variable_less'
  | 'always_true';

export type ClickMethod = 'single' | 'double' | 'right' | 'middle';
export type WaitType = 'time' | 'image' | 'window';
export type LoopType = 'count' | 'condition' | 'infinite';
export type KeyModifier = 'ctrl' | 'alt' | 'shift' | 'win';

// --- Node Parameter Interfaces ---

export interface StartNodeParams {
  label?: string;
}

export interface EndNodeParams {
  label?: string;
}

export interface ClickNodeParams {
  x: number;
  y: number;
  offsetX?: number;
  offsetY?: number;
  method: ClickMethod;
  delay_after: number; // seconds
  description?: string;
}

export interface ImageTarget {
  path: string;
  base64?: string;
}

export interface ImageClickNodeParams {
  targets: ImageTarget[];
  similarity: number;       // 0-100
  timeout: number;          // seconds
  method: ClickMethod;
  offset_x: number;
  offset_y: number;
  delay_after: number;
  multi_scale: boolean;
  region?: { x: number; y: number; w: number; h: number };
  match_all: boolean;       // click all matches
  description?: string;
}

export interface WaitNodeParams {
  wait_type: WaitType;
  duration?: number;             // seconds, for time wait
  template_path?: string;
  template_base64?: string;
  similarity?: number;
  timeout?: number;
  window_title?: string;
  description?: string;
}

export interface InputTextNodeParams {
  text: string;
  x?: number;
  y?: number;
  click_before: boolean;
  clear_before: boolean;
  delay_after: number;
  description?: string;
}

export interface KeyboardNodeParams {
  keys: string[];              // e.g. ['ctrl', 'c']
  modifiers: KeyModifier[];
  delay_after: number;
  description?: string;
}

export interface SwipeNodeParams {
  start_x: number;
  start_y: number;
  end_x: number;
  end_y: number;
  duration: number;    // seconds
  delay_after: number;
  description?: string;
}

export interface ConditionNodeParams {
  condition_type: ConditionType;
  template_path?: string;
  template_base64?: string;
  similarity?: number;
  window_title?: string;
  variable_name?: string;
  variable_value?: string;
  description?: string;
}

export interface LoopNodeParams {
  loop_type: LoopType;
  count?: number;
  condition?: ConditionNodeParams;
  description?: string;
}

export interface SubScriptNodeParams {
  script_id: string;
  script_name: string;
  description?: string;
}

export interface VariableSetNodeParams {
  name: string;
  value: string;
  description?: string;
}

export type NodeParams =
  | StartNodeParams
  | EndNodeParams
  | ClickNodeParams
  | ImageClickNodeParams
  | WaitNodeParams
  | InputTextNodeParams
  | KeyboardNodeParams
  | SwipeNodeParams
  | ConditionNodeParams
  | LoopNodeParams
  | SubScriptNodeParams
  | VariableSetNodeParams;

// --- Flow Node & Edge ---

export interface FlowNode {
  id: string;
  type: NodeType;
  x: number;
  y: number;
  params: NodeParams;
  label?: string;         // user custom label
  disabled?: boolean;
  collapsed?: boolean;
  // runtime state
  status?: 'idle' | 'running' | 'success' | 'error' | 'skipped';
  error_msg?: string;
}

export type EdgeBranchLabel = 'yes' | 'no' | 'loop_body' | 'loop_end' | 'next' | string;

export interface FlowEdge {
  id: string;
  from_node: string;
  from_port: string;        // 'output' | 'yes' | 'no' | 'loop_body' | 'loop_end'
  to_node: string;
  to_port: string;          // 'input'
  label?: EdgeBranchLabel;
  animated?: boolean;       // for running state
}

// --- Script / Project ---

export interface ScriptMeta {
  id: string;
  name: string;
  description?: string;
  created_at: string;
  updated_at: string;
  version: string;
  author?: string;
  tags?: string[];
}

export interface FlowScript {
  meta: ScriptMeta;
  nodes: FlowNode[];
  edges: FlowEdge[];
  variables: Record<string, string>; // global variables
  settings: ScriptSettings;
}

export interface ScriptSettings {
  default_delay: number;
  error_handling: 'stop' | 'continue' | 'retry';
  max_retries: number;
  retry_delay: number;
  log_level: 'debug' | 'info' | 'warn' | 'error';
}

// --- Execution Engine Types ---

export type ExecutionStatus = 'idle' | 'running' | 'paused' | 'stopped' | 'completed' | 'error';

export interface ExecutionState {
  status: ExecutionStatus;
  current_node_id?: string;
  variables: Record<string, string>;
  log: LogEntry[];
  started_at?: string;
  finished_at?: string;
  error?: string;
}

export interface LogEntry {
  timestamp: string;
  level: 'debug' | 'info' | 'warn' | 'error';
  node_id?: string;
  message: string;
}

// --- IPC Messages (Electron <-> Python) ---

export interface IPCMessage {
  type: 'execute' | 'pause' | 'resume' | 'stop' | 'status' | 'log' | 'node_status';
  payload?: any;
}

export interface NodeStatusMessage {
  node_id: string;
  status: 'running' | 'success' | 'error';
  message?: string;
  timestamp: string;
}

// --- UI State ---

export interface CanvasTransform {
  x: number;
  y: number;
  scale: number;
}

export interface SelectionState {
  nodes: string[];
  edges: string[];
}

export interface UIState {
  canvas_transform: CanvasTransform;
  selection: SelectionState;
  connecting: {
    active: boolean;
    from_node?: string;
    from_port?: string;
  };
  dragging_node?: string;
  hovered_node?: string;
  hovered_edge?: string;
  sidebar_width: number;
  property_width: number;
  show_grid: boolean;
  snap_to_grid: boolean;
  grid_size: number;
  dark_mode: boolean;
  viewMode: 'canvas' | 'pseudocode' | 'ai' | 'settings';
}

// --- Node Library Definition ---

export interface NodeDefinition {
  type: NodeType;
  label: string;
  description: string;
  icon: string;
  category: 'basic' | 'image' | 'control' | 'device' | 'data' | 'system';
  color: string;
  defaultParams: NodeParams;
  ports: {
    inputs: PortDefinition[];
    outputs: PortDefinition[];
  };
}

export interface PortDefinition {
  id: string;
  label?: string;
  type: 'flow' | 'data';
}

// --- Template ---

export interface ScriptTemplate {
  id: string;
  name: string;
  description: string;
  category: string;
  thumbnail?: string;
  script: FlowScript;
}
