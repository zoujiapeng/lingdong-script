export const ARP_FORMAT = 'arp/1' as const;

export type ArpNodeKind =
  | 'start'
  | 'end'
  | 'agent_goal'
  | 'action_batch'
  | 'replay_step'
  | 'skill_replay'
  | 'task_stage'
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
  model_call_count: number;
  stage_count: number;
  stage_failure_count: number;
  retry_count: number;
  controller_elapsed_total_ms: number;
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
  semantic_verify_count: number;
  semantic_verify_passed_count: number;
  semantic_verify_uia_query_count: number;
  semantic_verify_ocr_count: number;
  semantic_verify_latency_avg_ms: number;
  semantic_verify_latency_max_ms: number;
  semantic_verify_fast_path_rate: number;
  semantic_verify_ocr_rate: number;
  screen_delta_latency_avg_ms: number;
  screen_delta_latency_max_ms: number;
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
  visual_servo_count: number;
  visual_servo_correction_count: number;
  visual_servo_failure_count: number;
  visual_servo_latency_avg_ms: number;
  visual_servo_latency_max_ms: number;
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
  task_contract?: TaskContract | null;
  experience_policy?: 'allow' | 'disabled';
  skill_replay_node_count?: number;
  satisfiability?: CapabilitySatisfiabilityState | null;
  current_stage?: TaskStage | null;
  artifacts?: ArtifactStoreSnapshot;
  contract_evidence?: EvidenceRegistrySnapshot;
  completion_contract?: CompletionContractState | null;
  stage_trace?: StageRuntimeTrace[];
  history?: ArpHistoryRecord[];
  interaction_state?: InteractionRuntimeState | null;
  [key: string]: unknown;
}

export type TaskStageType =
  | 'OpenApplication' | 'FocusWindow' | 'CreateOrEditArtifact' | 'DrawArtifact'
  | 'CaptureArtifact' | 'SaveArtifact' | 'OpenConversation' | 'ResolveRecipient'
  | 'SendText' | 'SendArtifact' | 'VerifyArtifact' | 'VerifyDelivery'
  | 'ObserveScene' | 'InferInteraction' | 'PerformTurn' | 'VerifyLocalEffect'
  | 'WaitForExternalChange';

export interface InteractionActor {
  id: string;
  kind: string;
  display_name: string;
  role: string;
  attributes: Record<string, string>;
}

export interface InteractionIntent {
  active: boolean;
  mode: string;
  actors: InteractionActor[];
  role_assignments: Record<string, Record<string, string>>;
  turn_owner: string;
  session_mode: string;
  continuation_policy: string;
  target: string;
  region_hint: string;
  action_modalities: string[];
  user_started: boolean;
  expected_external_change: boolean;
  confidence: number;
  signals: string[];
  ambiguous_fields: string[];
  provenance: Record<string, string>;
}

export interface InteractionSessionContract {
  session_id: string;
  mode: string;
  lifecycle: string;
  actors: InteractionActor[];
  role_assignments: Record<string, Record<string, string>>;
  initial_turn_owner: string;
  target: string;
  authoritative_state_ref: string;
  interaction_region_ref: string;
  continuation_policy: string;
  max_agent_turns_per_activation: number;
  wait_for_external_change: boolean;
  requested_max_steps?: number;
  max_semantic_turns?: number;
  max_replans?: number;
  max_model_calls?: number;
  external_change_timeout_s?: number;
}

export interface TaskIntent {
  original_text: string;
  operation: string[];
  media_type: string;
  channel: string;
  recipient: string;
  source: string;
  capture_scope: string;
  destination: string;
  send_mode: string;
  subject: string;
  quality: string;
  confidence: number;
  ambiguous_fields: string[];
  provenance: Record<string, string>;
  interaction?: InteractionIntent;
}

export interface ArtifactBinding {
  name: string;
  media_type: string;
  source: string;
  artifact_id: string;
  required: boolean;
}

export interface StateBinding {
  name: string;
  state_type: string;
  source: string;
  scope: string;
  required: boolean;
  ephemeral: boolean;
}

export interface StageCapabilitySelection {
  required_capabilities: string[];
  verifier_requirements: string[];
  exposed_capabilities: string[];
  missing_capabilities: string[];
  tools: string[];
  model_tools: string[];
  controller_tools: string[];
  controller: string;
  adapter: string;
  model_role: string;
  satisfiable: boolean;
  expansions: string[];
  selected_evidence_providers: Record<string, string>;
}

export interface TaskStage {
  id: string;
  type: TaskStageType;
  name: string;
  parameters: Record<string, unknown>;
  depends_on: string[];
  inputs: ArtifactBinding[];
  outputs: ArtifactBinding[];
  state_inputs?: StateBinding[];
  state_outputs?: StateBinding[];
  required_capabilities: string[];
  obligations: Array<Record<string, unknown>>;
  execution: StageCapabilitySelection | null;
  allow_skill_replay: boolean;
}

export interface TaskContract {
  format: 'task-contract/1';
  task_id: string;
  task: string;
  intent: TaskIntent;
  stages: TaskStage[];
  obligations: Array<Record<string, unknown>>;
  final_conditions: string[];
  session?: InteractionSessionContract | null;
  satisfiable: boolean;
  missing_capabilities: string[];
  experience_policy: 'allow' | 'disabled';
  compiler_version: string;
}

export interface InteractionTargetBinding {
  hwnd?: number;
  app?: string;
  title?: string;
  status?: string;
  capture_scope?: string;
  frame_id?: number | string;
  frame_epoch?: number;
  policy?: string;
  source?: string;
  confidence?: number;
  [key: string]: unknown;
}

export interface InteractionRegionBinding {
  bounds?: number[];
  coordinate_space?: string;
  label?: string;
  source?: string;
  confidence?: number;
  generation?: number;
  policy?: string;
  retained_anchor?: boolean;
  blocked_reason?: string;
  [key: string]: unknown;
}

export interface InteractionActionState {
  action?: string;
  modality?: string;
  summary?: string;
  actor?: string;
  target?: string;
  status?: string;
  transaction_id?: string;
  coordinates?: unknown;
  ts?: number;
  [key: string]: unknown;
}

export interface InteractionVerificationState {
  verified?: boolean;
  status?: string;
  provider?: string;
  reason?: string;
  evidence_id?: string;
  frame_id?: number | string;
  [key: string]: unknown;
}

export interface ExternalChangeState {
  armed?: boolean;
  waiting?: boolean;
  changed?: boolean;
  status?: string;
  watch_scope?: string;
  baseline_frame_id?: number | string;
  latest_frame_id?: number | string;
  last_change_at?: number;
  [key: string]: unknown;
}

export interface InteractionFailureStage {
  stage_id?: string;
  stage_type?: TaskStageType | string;
  failure_type?: string;
  error?: string;
  recovery?: Record<string, unknown> | string;
  [key: string]: unknown;
}

/** Live state emitted by StageRuntime for a stateful visual interaction. */
export interface InteractionRuntimeState {
  session_id?: string;
  status?: string;
  mode?: string;
  frame_id?: number | string;
  baseline_artifact_id?: string;
  current_artifact_id?: string;
  current_stage_id?: string;
  model_calls?: number;
  target_binding?: InteractionTargetBinding | null;
  interaction_region?: InteractionRegionBinding | null;
  turn_owner?: string;
  planned_action?: InteractionActionState | string | null;
  executed_action?: InteractionActionState | string | null;
  local_verification?: InteractionVerificationState | null;
  last_mutating_plan?: InteractionActionState | string | null;
  last_executed_action?: InteractionActionState | string | null;
  last_local_verification?: InteractionVerificationState | null;
  external_change?: ExternalChangeState | null;
  failure_stage?: InteractionFailureStage | string | null;
  budget?: InteractionRuntimeBudget | null;
  turn_index?: number;
  replan_count?: number;
  state_bindings?: StateBinding[];
  [key: string]: unknown;
}

export interface ManagedArtifact {
  artifact_id: string;
  run_id: string;
  managed_path: string;
  sha256: string;
  mime_type: string;
  width: number;
  height: number;
  size_bytes: number;
  source_window: string;
  source_hwnd?: number;
  source_scope?: string;
  created_at: number;
  selected_at?: number;
  purpose: string;
  sendable: boolean;
  metadata?: Record<string, unknown>;
}

export interface CapabilitySatisfiabilityState {
  satisfiable: boolean;
  missing_capabilities: string[];
  [key: string]: unknown;
}

export interface InteractionRuntimeBudget {
  semantic_turns_used?: number;
  semantic_turns_limit?: number;
  replans_used?: number;
  replans_limit?: number;
  model_calls_used?: number;
  model_calls_limit?: number;
  external_change_timeout_s?: number;
  [key: string]: unknown;
}

export interface ContractEvidenceRecord {
  id: string;
  predicate: string;
  source: string;
  provider: string;
  verified: boolean;
  value?: unknown;
  stage_id: string;
  artifact_id: string;
  recipient: string;
  run_id: string;
  captured_at: number;
  metadata: Record<string, unknown>;
  text: string;
}

export interface EvidenceRegistrySnapshot {
  run_id: string;
  count: number;
  records: ContractEvidenceRecord[];
}

export interface ArtifactStoreSnapshot {
  run_id: string;
  root?: string;
  count: number;
  artifacts: ManagedArtifact[];
}

export interface CompletionObligationResult {
  obligation_id: string;
  predicate: string;
  description: string;
  satisfied: boolean;
  provider: string;
  evidence_id: string;
  stage_id: string;
  artifact_ref: string;
  artifact_id: string;
}

export interface CompletionConditionResult {
  condition: string;
  satisfied: boolean;
  evidence_id: string;
}

export interface CompletionContractState {
  ok: boolean;
  run_id: string;
  contract_id: string;
  obligations: CompletionObligationResult[];
  final_conditions: CompletionConditionResult[];
  missing: CompletionObligationResult[];
  contract_errors: string[];
  artifact_refs: Record<string, string>;
  primary_artifact_id: string;
  reason: string;
  [key: string]: unknown;
}

export interface StageRuntimeTrace {
  stage_id: string;
  stage_type: TaskStageType | string;
  status: string;
  attempts?: number;
  tools: string[];
  model_tools: string[];
  controller_tools: string[];
  required_capabilities: string[];
  verifier_requirements: string[];
  exposed_capabilities: string[];
  missing_capabilities: string[];
  satisfiable: boolean;
  adapter: string;
  model_role: string;
  artifact_id: string;
  model_calls: number;
  model_elapsed_ms?: number;
  controller_elapsed_ms: number;
  capture_elapsed_ms?: number;
  failure_type: string;
  recovery: Record<string, unknown> | string;
  error: string;
  interaction_state?: InteractionRuntimeState | Record<string, unknown>;
  [key: string]: unknown;
}

export interface ArpHistoryRecord {
  kind?: string;
  node_id?: string;
  ok?: boolean;
  attempts?: number;
  error?: string;
  output?: Record<string, unknown>;
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

export interface AgentSchedule {
  id: string;
  name: string;
  task: string;
  enabled: boolean;
  running: boolean;
  next_run_at?: string | null;
  interval_seconds?: number | null;
  max_steps?: number;
  last_run_at?: string | null;
  last_run_id?: string | null;
  last_status?: string | null;
  run_count: number;
}
