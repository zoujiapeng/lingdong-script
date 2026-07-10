export const ARP_FORMAT = 'arp/1' as const;

export type ArpNodeKind =
  | 'start'
  | 'end'
  | 'agent_goal'
  | 'action_batch'
  | 'replay_step'
  | 'skill_replay'
  | 'condition'
  | 'delay'
  | 'set_variable'
  | 'evidence_gate'
  | 'human_gate'
  | 'legacy_flow';

export interface ArpRetryPolicy {
  max_attempts: number;
  delay_ms: number;
  backoff: number;
}

export interface ArpNode {
  id: string;
  kind: ArpNodeKind;
  name: string;
  config: Record<string, unknown>;
  retry: ArpRetryPolicy;
  timeout_s?: number | null;
  disabled: boolean;
  x: number;
  y: number;
}

export interface ArpEdge {
  id: string;
  source: string;
  target: string;
  branch: string;
  label: string;
}

export interface ArpGraph {
  format: typeof ARP_FORMAT;
  id: string;
  name: string;
  description: string;
  nodes: ArpNode[];
  edges: ArpEdge[];
  variables: Record<string, unknown>;
  policy: {
    max_node_visits: number;
    fail_fast: boolean;
    require_evidence_on_done: boolean;
    allow_shell: boolean;
    allow_desktop_mutation: boolean;
  };
  metadata: Record<string, unknown>;
}

export interface GraphValidation {
  valid: boolean;
  errors: string[];
  warnings: string[];
  entry_node: string | null;
  reachable_nodes: string[];
}

export interface CapabilitySpec {
  id: string;
  name: string;
  category: string;
  description: string;
  node_kind: ArpNodeKind;
  latency_class: string;
  deterministic: boolean;
  requires_llm: boolean;
  input_schema: Record<string, unknown>;
}

export interface RunTimelineEvent {
  type: string;
  ts?: number;
  phase?: string;
  node_id?: string | null;
  tool?: string | null;
  status?: string | null;
  summary?: unknown;
}

export interface RunMetrics {
  event_count: number;
  decision_count: number;
  tool_count: number;
  batch_action_count: number;
  perception_count: number;
  vlm_call_count: number;
  done_checks: number;
  done_check_passed: number;
  tool_latency_avg_ms: number;
  tool_latency_max_ms: number;
  perception_latency_avg_ms: number;
  perception_latency_max_ms: number;
  screen_capture_latency_avg_ms: number;
  screen_capture_latency_max_ms: number;
  action_decision_latency_avg_ms: number;
  action_decision_latency_max_ms: number;
  action_execution_latency_avg_ms: number;
  action_execution_latency_max_ms: number;
  post_action_verify_latency_avg_ms: number;
  post_action_verify_latency_max_ms: number;
  replan_count: number;
  replan_frequency: number;
  vlm_call_frequency: number;
  skill_reuse_count: number;
  skill_suggestion_count: number;
  skill_reuse_rate: number;
  batch_action_ratio: number;
  anchor_cache_lookup_count: number;
  anchor_cache_hit_count: number;
  anchor_cache_miss_count: number;
  anchor_cache_fallback_count: number;
  anchor_cache_learned_count: number;
  anchor_cache_hit_rate: number;
  anchor_cache_lookup_latency_avg_ms: number;
  anchor_cache_lookup_latency_max_ms: number;
}

export interface RunProjection {
  run_id: string;
  kind: 'agent' | 'arp';
  status: string;
  phase: string;
  goal: string;
  graph_id?: string | null;
  current_node_id?: string | null;
  current_tool?: string | null;
  started_at: number;
  updated_at: number;
  finished_at?: number | null;
  result?: unknown;
  error?: string;
  usage?: Record<string, unknown> | null;
  timeline: RunTimelineEvent[];
  evidence: Array<Record<string, unknown>>;
  metrics: RunMetrics;
}

export interface ArpExecutionSnapshot {
  run_id: string;
  graph_id: string;
  graph_name: string;
  status: string;
  current_node_id?: string | null;
  error?: string;
  [key: string]: unknown;
}

export interface CurrentRunResponse {
  ok: boolean;
  run: RunProjection | null;
  arp: ArpExecutionSnapshot | null;
}

export interface PlatformOverview {
  api_version: string;
  product: string;
  status: Record<string, unknown>;
  current_run: RunProjection | null;
  capabilities: CapabilitySpec[];
  contracts: Record<string, string>;
}

export interface ChatGPTDesktopIntegration {
  ok: boolean;
  integration: 'chatgpt_desktop';
  transport: 'mcp_streamable_http';
  planner: string;
  requires_openai_api_key: boolean;
  enabled: boolean;
  server_ready: boolean;
  client_active: boolean;
  connection_status: 'active' | 'ready' | 'disabled' | 'error';
  last_client_seen_at: number | null;
  request_count: number;
  last_client: string;
  security_mode: 'loopback_only';
  proxy_bypass: string;
  allow_desktop_mutation: boolean;
  endpoint: string;
  tool_count: number;
  tools: string[];
  error?: string;
  registration?: { client: string; command: string; environment?: string };
}

export interface RuntimeEvent {
  type: string;
  ts?: number;
  run_id?: string;
  [key: string]: unknown;
}
