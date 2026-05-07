// ============================================================
// CORE DATA TYPES - Flow Script Engine
// ============================================================

export type NodeType =
  | 'start'
  | 'end'
  | 'click'
  | 'image_click'
  | 'click_text'
  | 'wait'
  | 'input_text'
  | 'keyboard'
  | 'swipe'
  | 'condition'
  | 'loop'
  | 'sub_script'
  | 'screenshot'
  | 'variable_set'
  | 'variable_get'
  | 'popup_close'
  | 'color_check'
  | 'progress_wait'
  | 'region_wait'
  | 'scene_condition'
  | 'tool_call'
  | 'shutdown'
  | 'reboot'
  | 'logoff'
  | 'hibernate'
  // 扩展系统指令
  | 'lock_workstation'
  | 'volume_set'
  | 'volume_mute'
  | 'launch_program'
  | 'kill_process'
  | 'wait_process'
  | 'focus_window'
  | 'set_wallpaper'
  | 'display_off'
  | 'system_sleep'
  | 'show_desktop'
  | 'battery_status'
  | 'empty_recycle_bin'
  | 'display_brightness'
  | 'clipboard_set'
  | 'clipboard_get'
  | 'window_control';

export type ConditionType =
  | 'image_exists'
  | 'window_exists'
  | 'variable_equals'
  | 'variable_greater'
  | 'variable_less'
  | 'color_match'
  | 'scene_match'
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
  // color_match
  x?: number;
  y?: number;
  w?: number;
  h?: number;
  expected_color?: string;
  tolerance?: number;
  // scene_match
  scene_name?: string;
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

export interface ClickTextNodeParams {
  text: string;
  similarity: number;
  method: ClickMethod;
  offset_x: number;
  offset_y: number;
  timeout: number;
  delay_after: number;
  description?: string;
}

export interface PopupCloseNodeParams {
  strategies: string[];   // auto, close_btn, ocr_close, esc, click_away
  timeout: number;
  delay_after: number;
  description?: string;
}

export interface ColorCheckNodeParams {
  x: number;
  y: number;
  w: number;
  h: number;
  expected_color: string;  // hex or HSV
  tolerance: number;
  description?: string;
}

export interface ProgressWaitNodeParams {
  wait_type: 'fill' | 'empty' | 'change';
  timeout: number;
  region: { x: number; y: number; w: number; h: number };
  poll_interval: number;
  description?: string;
}

export interface RegionWaitNodeParams {
  region_type: 'stable' | 'change';
  x: number;
  y: number;
  w: number;
  h: number;
  timeout: number;
  poll_interval: number;
  description?: string;
}

export interface SceneConditionNodeParams {
  scene_name: string;
  similarity: number;
  description?: string;
}

export interface ToolCallNodeParams {
  feature_name: string;
  params: Record<string, any>;
  description?: string;
}

export interface SystemCommandParams {
  delay_seconds: number;
  confirm: boolean;
  description?: string;
}

export interface ScreenshotNodeParams {
  region?: { x: number; y: number; w: number; h: number };
  save_path?: string;
  format?: 'png' | 'jpg';
  description?: string;
}

// --- 扩展系统指令参数 ---

export interface LockWorkstationParams {
  confirm: boolean;
}

export interface VolumeSetParams {
  level: number;
}

export interface VolumeMuteParams {
  mute: boolean | null;
}

export interface LaunchProgramParams {
  target: string;
  args: string;
  wait: boolean;
}

export interface KillProcessParams {
  name: string;
  force: boolean;
}

export interface WaitProcessParams {
  name: string;
  timeout: number;
  wait_for: 'start' | 'exit';
}

export interface FocusWindowParams {
  title: string;
  exact: boolean;
}

export interface WallpaperParams {
  path: string;
  style: string;
}

export interface DisplayOffParams {
  confirm: boolean;
}

export interface SleepParams {
  confirm: boolean;
}

export interface ShowDesktopParams {
}

export interface BatteryStatusParams {
}

export interface EmptyRecycleBinParams {
  confirm: boolean;
}

export interface DisplayBrightnessParams {
  level: number;
}

export interface ClipboardSetParams {
  text: string;
}

export interface ClipboardGetParams {
}

export interface WindowControlParams {
  operation: 'minimize' | 'maximize' | 'close' | 'resize';
  title: string;
  width?: number;
  height?: number;
}

export type NodeParams =
  | StartNodeParams
  | EndNodeParams
  | ClickNodeParams
  | ImageClickNodeParams
  | ClickTextNodeParams
  | WaitNodeParams
  | InputTextNodeParams
  | KeyboardNodeParams
  | SwipeNodeParams
  | ConditionNodeParams
  | LoopNodeParams
  | SubScriptNodeParams
  | VariableSetNodeParams
  | PopupCloseNodeParams
  | ColorCheckNodeParams
  | ProgressWaitNodeParams
  | RegionWaitNodeParams
  | SceneConditionNodeParams
  | ScreenshotNodeParams
  | ToolCallNodeParams
  | SystemCommandParams
  | LockWorkstationParams
  | VolumeSetParams
  | VolumeMuteParams
  | LaunchProgramParams
  | KillProcessParams
  | WaitProcessParams
  | FocusWindowParams
  | WallpaperParams
  | DisplayOffParams
  | SleepParams
  | ShowDesktopParams
  | BatteryStatusParams
  | EmptyRecycleBinParams
  | DisplayBrightnessParams
  | ClipboardSetParams
  | ClipboardGetParams
  | WindowControlParams;

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
  aliases?: string[];
  keywords?: string[];
  category?: string;
  source?: 'preset' | 'custom' | 'dsl' | 'flow';
  dsl_content?: string;
  dsl_file?: string;
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
  loop_iteration?: { current: number; total: number; node_id: string } | null;
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

// --- Sub-flow Navigation ---

export interface SubFlowNavEntry {
  scriptId: string;
  scriptName: string;
  sourceNodeId?: string;
  sourceNodeLabel?: string;
}

export interface SubFlowNavState {
  stack: SubFlowNavEntry[];
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
  viewMode: 'canvas' | 'pseudocode' | 'settings' | 'agent';
  panels_visible: boolean;
  show_node_library: boolean;
  show_property_panel: boolean;
  show_log_panel: boolean;
  show_tool_call_panel: boolean;
  show_ai_dialog: boolean;
}

// --- Node Library Definition ---

export interface NodeDefinition {
  type: NodeType;
  label: string;
  description: string;
  icon: string;
  category: 'basic' | 'image' | 'control' | 'device' | 'data' | 'system' | 'tools';
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

// --- Feature Registry (MCP tools) ---

export interface FeatureParam {
  name: string;
  type: string;
  description: string;
  required: boolean;
  default: any;
  example: any;
}

export interface FeatureDefinition {
  name: string;
  display_name: string;
  description: string;
  category: string;
  params: FeatureParam[];
  returns: string;
  dsl_keyword?: string;
  dsl_template?: string;
  examples?: string[];
  mcp_tool_name: string;
  cli_command: string;
  http_endpoint?: string;
  version: string;
  deprecated: boolean;
  tags: string[];
  visual?: {
    icon: string;
    color: string;
    ports_in: { id: string; label?: string; type: string }[];
    ports_out: { id: string; label?: string; type: string }[];
  };
  hidden?: boolean;
}

export interface ToolCallEntry {
  id: string;
  feature_name: string;
  display_name: string;
  params: Record<string, any>;
  result: any;
  success: boolean;
  elapsed_ms: number;
  timestamp: string;
  source: 'flow' | 'direct';
}

// --- Macro Recording ---

export interface MacroEvent {
  type: 'click' | 'move' | 'key';
  time: number;
  x?: number;
  y?: number;
  button?: string;
  pressed?: boolean;
  key?: string;
}

export interface MacroInfo {
  name: string;
  file: string;
  events: number;
  duration: number;
  created: string;
}

export interface RecordingState {
  active: boolean;
  macroName: string;
}

// --- Schedule Types ---

export type TriggerType = 'cron' | 'interval' | 'startup' | 'event';

export interface TriggerDef {
  type: TriggerType;
  cron_expr?: string;
  interval_seconds?: number;
  event_type?: string;     // window_open, process_start, file_created, clipboard_change, network_available
  event_config?: Record<string, any>;
}

export type ConditionOperator = 'AND' | 'OR';

export interface ConditionRule {
  type: string;            // window_exists, process_running, is_weekday, image_exists, text_exists, etc.
  params: Record<string, any>;
}

export interface ConditionGroup {
  operator: ConditionOperator;
  rules: ConditionRule[];
}

export interface ScheduleEntry {
  id: string;
  name: string;
  template_id: string;
  template_name: string;
  triggers: TriggerDef[];
  conditions?: ConditionGroup;
  enabled: boolean;
  last_run: string | null;
  next_run: string | null;
  created_at: string;
}
