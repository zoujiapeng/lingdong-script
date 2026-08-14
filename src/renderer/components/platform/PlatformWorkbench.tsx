import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  Archive,
  ArrowUp,
  Bot,
  BrainCircuit,
  Braces,
  Check,
  CheckCircle2,
  Command,
  ChevronRight,
  CircleStop,
  Clock3,
  Database,
  Eye,
  FileCode2,
  FilePlus2,
  FolderOpen,
  Gauge,
  GitBranch,
  History,
  Info,
  Library,
  Layers3,
  ListChecks,
  Loader2,
  Maximize2,
  Minimize2,
  Minus,
  Network,
  PanelLeft,
  Pause,
  Play,
  Plus,
  Radio,
  Redo2,
  RefreshCw,
  Save,
  Search,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Square,
  TerminalSquare,
  Undo2,
  UserCheck,
  Workflow,
  X,
  XCircle,
  Zap,
} from 'lucide-react';
import { FlowCanvas } from '../Canvas/FlowCanvas';
import { NodeLibrary } from '../NodeLibrary/NodeLibrary';
import { PropertyPanel } from '../PropertyPanel/PropertyPanel';
import { FlowFragmentComposer } from '../FlowFragment/FlowFragmentComposer';
import { confirmDocumentReplacement, useStore } from '../../store';
import type { UIState } from '../../types';
import { useControlPlane } from '../../platform/useControlPlane';
import type {
  AgentSchedule,
  ArpExecutionSnapshot,
  ArpGraph,
  ChatGPTDesktopIntegration,
  CurrentRunResponse,
  GraphValidation,
  InteractionActionState,
  InteractionRuntimeState,
  RunProjection,
  RuntimeEvent,
  RuntimeJobSnapshot,
  StageRuntimeTrace,
  TaskContract,
} from '../../platform/contracts';
import { wpsToFlowScript, type WpsScript } from '../../utils/wpsToFlowScript';
import {
  automationIrFromFlowScript,
  automationIrToFlowScript,
  automationRuntimeKind,
  automationRuntimeGraph,
  validateAutomationLocally,
  type AutomationIR,
} from '../../utils/automationIR';
import { flowDocumentFingerprint } from '../../utils/flowFragmentProposal';
import {
  isTerminalTaskStatus,
  readableFailureStage,
  readableRunError,
} from '../../../shared/taskOutcome';
import { taskOutcomeForRun } from '../../platform/taskOutcome';

type PlatformView = 'agent' | 'studio' | 'runs' | 'assets' | 'system';
type Plane = ReturnType<typeof useControlPlane>;

export const isActiveRunStatus = (status: unknown) =>
  Boolean(String(status ?? '').trim()) && !isTerminalTaskStatus(status);
const UNSUCCESSFUL_STATUSES = new Set([
  'failed',
  'fail',
  'error',
  'canceled',
  'cancelled',
  'stopped',
  'interrupted',
  'blocked',
]);
const PHASES = ['planning', 'deciding', 'perceiving', 'acting', 'verifying'];
const PHASE_LABELS: Record<string, string> = {
  planning: '规划', deciding: '决策', perceiving: '感知', acting: '执行', verifying: '验证',
};
const STATUS_LABELS: Record<string, string> = {
  queued: '排队中', running: '运行中', paused: '已暂停', done: '已完成', completed: '已完成',
  success: '成功', reply: '已回复', failed: '失败', fail: '失败', error: '错误',
  canceled: '已取消', cancelled: '已取消', stopped: '已停止', interrupted: '已中断',
  blocked: '已阻塞', idle: '就绪',
  provisional: '待验证', active: '有效', verified: '已验证', rejected: '已拒绝', inactive: '未启用',
  waiting_external: '等待外部参与者', external_change_observed: '已检测到外部变化',
};
const RUN_KIND_LABELS: Record<string, string> = {
  agent: '智能代理', arp: '流程', replay: '重放', schedule: '计划任务', controller: '控制器', automation: '自动化',
};
const EVENT_LABELS: Record<string, string> = {
  agent_start: '代理启动', agent_step: '代理步骤', agent_done: '代理完成', agent_tool: '代理工具',
  agent_tool_start: '工具开始', agent_usage: '模型用量', agent_model_call: '模型调用',
  arp_run_start: '流程开始', arp_run_done: '流程完成', arp_node_start: '节点开始', arp_node_done: '节点完成',
  arp_child_run: '子任务', arp_evidence: '证据记录', screen_watch: '屏幕观察', screen_delta: '画面变化',
  task_stage_start: '阶段开始', task_stage_done: '阶段完成', task_stage_failed: '阶段失败',
  visual_feedback: '视觉反馈', visual_preflight: '执行前画面检查', done_check: '完成检查', run_state: '运行状态',
  interaction_session_start: '互动会话开始', interaction_scene_bound: '目标场景已绑定',
  interaction_protocol_inferred: '互动规则已推断', interaction_action_planned: '本轮动作已规划',
  interaction_action_executed: '本轮动作已执行', interaction_local_effect_verified: '局部效果已验证',
  interaction_wait_external_change: '等待外部变化', interaction_external_change: '检测到外部变化',
  surface_binding: '交互区域绑定',
  live_agent_activity: 'Agent 实时活动',
  vnext_model_reasoning: '模型原始思考',
  vnext_model_pointer_arguments: '模型原始指针参数',
  vnext_model_tool_shape: '模型工具参数形状',
  vnext_pointer_dispatch: '指针坐标映射',
};
const TOOL_LABELS: Record<string, string> = {
  click: '点击', draw_strokes: '连续绘制', type_text: '输入文字', hotkey: '快捷键', scroll: '滚动',
  drag: '拖拽', open_app: '打开应用', focus_window: '聚焦窗口', observe: '观察', done: '完成',
  capture_artifact: '创建工件', send_artifact: '发送工件', verify_delivery: '验证送达', open_conversation: '打开会话',
  run_shell: '运行命令', observe_desktop: '观察桌面', read_window_text: '读取窗口文字',
  capture_screen: '截取屏幕', list_windows: '读取窗口列表', paste_image: '粘贴图片',
  claim_obligation_done: '完成任务', blocked: '说明阻塞',
};
const LIVE_ACTIVITY_LABELS: Record<string, string> = {
  message: '模型思路',
  task_state: '任务板更新',
  tool_start: '开始执行',
  tool_result: '执行结果',
};

function viewFromStore(mode: UIState['viewMode']): PlatformView {
  if (mode === 'studio' || mode === 'canvas' || mode === 'pseudocode') return 'studio';
  if (mode === 'runs') return 'runs';
  if (mode === 'assets' || mode === 'replay' || mode === 'templates') return 'assets';
  if (mode === 'system' || mode === 'settings') return 'system';
  return 'agent';
}

function formatTime(value?: number | null): string {
  if (!value) return '--:--:--';
  return new Date(value * 1000).toLocaleTimeString([], { hour12: false });
}

function formatDuration(run: RunProjection): string {
  const end = run.finished_at || Date.now() / 1000;
  const seconds = Math.max(0, Math.round(end - run.started_at));
  if (seconds < 60) return `${seconds} 秒`;
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

function formatElapsed(startedAt?: number | null, finishedAt?: number | null): string {
  if (!startedAt) return '尚未开始';
  const seconds = Math.max(0, Math.round((finishedAt || Date.now() / 1000) - startedAt));
  if (seconds < 60) return `${seconds} 秒`;
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

function displayValue(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'string') return value;
  try { return JSON.stringify(value); } catch { return String(value); }
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return value != null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function latestEventWith(
  events: RuntimeEvent[],
  runId: string,
  keys: string[],
  types: string[] = [],
): Record<string, unknown> | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (runId && event.run_id && event.run_id !== runId) continue;
    if (types.length && !types.includes(event.type)) continue;
    if (keys.some((key) => event[key] != null)) return event;
  }
  return null;
}

/**
 * Build a backwards-compatible view of the live interaction state.
 * New runtimes publish interaction_state directly; older/in-flight snapshots
 * still get a useful view from the typed contract, current stage and events.
 */
export function interactionRuntimeView(
  arp: ArpExecutionSnapshot | null,
  events: RuntimeEvent[] = [],
): InteractionRuntimeState | null {
  const contract = arp?.task_contract;
  const intent = contract?.intent.interaction;
  const session = contract?.session;
  const direct = recordValue(arp?.interaction_state) as InteractionRuntimeState | null;
  const interactive = Boolean(direct || intent?.active || session);
  if (!interactive) return null;

  const runId = arp?.run_id || '';
  const stateEvent = latestEventWith(events, runId, ['interaction_state']);
  const eventState = recordValue(stateEvent?.interaction_state) as InteractionRuntimeState | null;
  const state: InteractionRuntimeState = { ...(eventState || {}), ...(direct || {}) };
  const currentStage = arp?.current_stage || null;
  const currentParams = currentStage?.parameters || {};
  const trace = (arp?.stage_trace || []).map(recordValue).filter(Boolean) as Array<Record<string, unknown>>;
  const latestFailure = [...trace].reverse().find((item) => item.status === 'failed');
  const latestVerification = [...trace].reverse().find((item) => item.stage_type === 'VerifyLocalEffect');
  const latestWait = [...trace].reverse().find((item) => item.stage_type === 'WaitForExternalChange');

  const targetEvent = latestEventWith(
    events, runId, ['target_binding'], ['interaction_scene_bound', 'surface_binding', 'visual_preflight'],
  );
  const regionEvent = latestEventWith(
    events, runId, ['interaction_region', 'region'], ['interaction_scene_bound', 'surface_binding', 'visual_preflight'],
  );
  const plannedEvent = latestEventWith(
    events, runId, ['planned_action', 'action'], ['interaction_action_planned'],
  );
  const executedEvent = latestEventWith(
    events, runId, ['executed_action', 'action'], ['interaction_action_executed'],
  );
  const verificationEvent = latestEventWith(
    events, runId, ['local_verification', 'verified'], ['interaction_local_effect_verified'],
  );
  const externalEvent = latestEventWith(
    events, runId, ['external_change', 'waiting', 'armed', 'changed'],
    ['interaction_wait_external_change', 'interaction_external_change'],
  );

  const targetBinding = state.target_binding
    || recordValue(targetEvent?.target_binding)
    || (session?.target || intent?.target || currentParams.target
      ? { source: String(session?.target || intent?.target || currentParams.target), status: 'declared' }
      : null);
  const eventRegion = recordValue(regionEvent?.interaction_region) || recordValue(regionEvent?.region);
  const interactionRegion = state.interaction_region
    || eventRegion
    || (session?.interaction_region_ref || intent?.region_hint || currentParams.interaction_region_ref
      ? {
          label: String(session?.interaction_region_ref || currentParams.interaction_region_ref || 'interaction_region'),
          source: String(intent?.region_hint || 'contract'),
        }
      : null);
  const plannedAction = state.last_mutating_plan
    ?? state.planned_action
    ?? plannedEvent?.planned_action
    ?? plannedEvent?.action
    ?? null;
  const executedAction = state.last_executed_action
    ?? state.executed_action
    ?? executedEvent?.executed_action
    ?? executedEvent?.action
    ?? null;
  const localVerification = state.last_local_verification
    || state.local_verification
    || recordValue(verificationEvent?.local_verification)
    || (verificationEvent
      ? {
          verified: verificationEvent.verified === true,
          status: String(verificationEvent.status || ''),
          reason: String(verificationEvent.reason || ''),
          provider: String(verificationEvent.provider || ''),
        }
      : latestVerification
        ? {
            verified: latestVerification.status === 'done',
            status: String(latestVerification.status || ''),
            reason: String(latestVerification.error || latestVerification.failure_type || ''),
          }
        : null);
  const externalChange = state.external_change
    || recordValue(externalEvent?.external_change)
    || (externalEvent
      ? {
          armed: externalEvent.armed === true || externalEvent.type === 'interaction_wait_external_change',
          waiting: externalEvent.waiting === true || externalEvent.type === 'interaction_wait_external_change',
          changed: externalEvent.changed === true || externalEvent.type === 'interaction_external_change',
          status: String(externalEvent.status || ''),
          watch_scope: String(externalEvent.watch_scope || ''),
        }
      : latestWait
        ? {
            armed: latestWait.status === 'done',
            waiting: latestWait.status !== 'failed',
            changed: false,
            status: String(latestWait.status || ''),
          }
        : currentStage?.type === 'WaitForExternalChange'
          ? { armed: true, waiting: true, changed: false, status: 'waiting' }
          : null);
  const directFailure = typeof state.failure_stage === 'string'
    ? {
        stage_id: String(state.current_stage_id || currentStage?.id || ''),
        stage_type: String(currentStage?.type || ''),
        failure_type: state.failure_stage,
        error: '',
      }
    : state.failure_stage;
  const failureStage = directFailure || (latestFailure ? {
    stage_id: String(latestFailure.stage_id || ''),
    stage_type: String(latestFailure.stage_type || ''),
    failure_type: String(latestFailure.failure_type || 'stage_failed'),
    error: String(latestFailure.error || ''),
    recovery: recordValue(latestFailure.recovery) || String(latestFailure.recovery || ''),
  } : null);
  const stageBindings = [
    ...(currentStage?.state_inputs || []),
    ...(currentStage?.state_outputs || []),
  ];

  return {
    ...state,
    session_id: state.session_id || session?.session_id,
    mode: state.mode || session?.mode || intent?.mode,
    current_stage_id: state.current_stage_id || currentStage?.id,
    target_binding: targetBinding,
    interaction_region: interactionRegion,
    turn_owner: state.turn_owner || intent?.turn_owner || session?.initial_turn_owner || 'unknown',
    planned_action: plannedAction as InteractionActionState | string | null,
    executed_action: executedAction as InteractionActionState | string | null,
    local_verification: localVerification,
    external_change: externalChange,
    failure_stage: failureStage,
    state_bindings: state.state_bindings?.length ? state.state_bindings : stageBindings,
  };
}

function interactionActionLabel(value: InteractionActionState | string | null | undefined): string {
  if (!value) return '待生成';
  if (typeof value === 'string') return value;
  const primary = value.summary || value.action || value.modality || value.status;
  if (primary) {
    const target = value.target ? ` → ${value.target}` : '';
    return `${primary}${target}`;
  }
  return displayValue(value);
}

function regionLabel(region: InteractionRuntimeState['interaction_region']): string {
  if (!region) return '尚未绑定';
  const bounds = Array.isArray(region.bounds) && region.bounds.length === 4
    ? `[${region.bounds.join(', ')}]`
    : '';
  return [region.label, bounds, region.coordinate_space].filter(Boolean).join(' · ') || '已绑定';
}

function turnOwnerLabel(owner?: string): string {
  if (owner === 'agent' || owner === 'assistant') return '代理';
  if (owner === 'user' || owner === 'human') return '用户';
  return owner || '待推断';
}

function stringList(value: unknown, fallback: string[] = []): string[] {
  if (!Array.isArray(value)) return fallback;
  return value.map((item) => String(item)).filter(Boolean);
}

function finiteNumber(value: unknown, fallback = 0): number {
  const number = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function runtimeHistoryOutput(
  arp: ArpExecutionSnapshot,
  stageId: string,
): Record<string, unknown> {
  const record = [...(arp.history || [])].reverse().find((item) => {
    const output = recordValue(item.output);
    return item.kind === 'task_stage'
      && String(output?.stage_id || item.node_id || '') === stageId;
  });
  return recordValue(record?.output) || {};
}

export interface StageRuntimeRow extends StageRuntimeTrace {
  stage_name: string;
  current: boolean;
  evidence_count: number;
  timing_reported: boolean;
}

/** Merge the executable contract with live trace/history without inventing success. */
export function stageRuntimeRows(
  contract: TaskContract,
  arp: ArpExecutionSnapshot,
): StageRuntimeRow[] {
  const evidence = arp.contract_evidence?.records || [];
  const stages = Array.isArray(contract.stages) ? contract.stages : [];
  return stages.map((stage) => {
    const trace = (arp.stage_trace || []).find((item) => item.stage_id === stage.id);
    const history = runtimeHistoryOutput(arp, stage.id);
    const execution = stage.execution;
    const current = stage.id === (arp.current_stage?.id || arp.current_node_id);
    const value = (key: keyof StageRuntimeTrace): unknown => (
      history[key] ?? trace?.[key]
    );
    const status = String(value('status') || (current ? 'running' : 'pending'));
    return {
      stage_id: stage.id,
      stage_type: String(value('stage_type') || stage.type),
      stage_name: stage.name,
      current,
      timing_reported: Boolean(trace || Object.keys(history).length),
      status,
      attempts: finiteNumber(trace?.attempts, finiteNumber(
        [...(arp.history || [])].reverse().find((item) => item.node_id === stage.id)?.attempts,
        0,
      )),
      tools: stringList(value('tools'), execution?.tools || []),
      model_tools: stringList(value('model_tools'), execution?.model_tools || []),
      controller_tools: stringList(value('controller_tools'), execution?.controller_tools || []),
      required_capabilities: stringList(
        value('required_capabilities'), execution?.required_capabilities || stage.required_capabilities,
      ),
      verifier_requirements: stringList(
        value('verifier_requirements'), execution?.verifier_requirements || [],
      ),
      exposed_capabilities: stringList(
        value('exposed_capabilities'), execution?.exposed_capabilities || [],
      ),
      missing_capabilities: stringList(
        value('missing_capabilities'), execution?.missing_capabilities || [],
      ),
      satisfiable: value('satisfiable') == null
        ? Boolean(execution?.satisfiable)
        : value('satisfiable') === true,
      adapter: String(value('adapter') || execution?.adapter || ''),
      model_role: String(value('model_role') || execution?.model_role || 'none'),
      selected_evidence_providers: (
        recordValue(value('selected_evidence_providers'))
        || execution?.selected_evidence_providers
        || {}
      ) as Record<string, string>,
      artifact_id: String(value('artifact_id') || ''),
      model_calls: finiteNumber(value('model_calls')),
      model_elapsed_ms: finiteNumber(value('model_elapsed_ms')),
      controller_elapsed_ms: finiteNumber(value('controller_elapsed_ms')),
      capture_elapsed_ms: finiteNumber(value('capture_elapsed_ms')),
      failure_type: String(value('failure_type') || ''),
      recovery: (value('recovery') || {}) as Record<string, unknown> | string,
      error: String(value('error') || ''),
      interaction_state: (value('interaction_state') || {}) as Record<string, unknown>,
      evidence_count: evidence.filter((item) => item.stage_id === stage.id).length,
    };
  });
}

/** Render persisted stage traces even when an older run predates contract snapshots. */
function projectedStageRuntimeRows(arp: ArpExecutionSnapshot): StageRuntimeRow[] {
  const evidence = arp.contract_evidence?.records || [];
  return (arp.stage_trace || []).map((trace) => ({
    ...trace,
    stage_name: trace.stage_type || trace.stage_id,
    current: trace.stage_id === arp.current_node_id,
    timing_reported: true,
    tools: stringList(trace.tools),
    model_tools: stringList(trace.model_tools),
    controller_tools: stringList(trace.controller_tools),
    required_capabilities: stringList(trace.required_capabilities),
    verifier_requirements: stringList(trace.verifier_requirements),
    exposed_capabilities: stringList(trace.exposed_capabilities),
    missing_capabilities: stringList(trace.missing_capabilities),
    selected_evidence_providers: trace.selected_evidence_providers || {},
    model_calls: finiteNumber(trace.model_calls),
    model_elapsed_ms: finiteNumber(trace.model_elapsed_ms),
    controller_elapsed_ms: finiteNumber(trace.controller_elapsed_ms),
    capture_elapsed_ms: finiteNumber(trace.capture_elapsed_ms),
    evidence_count: evidence.filter((item) => item.stage_id === trace.stage_id).length,
  }));
}

function capabilityLabel(values: string[]): string {
  return values.length ? values.join(', ') : '无';
}

function runtimeBudgetLabel(state: InteractionRuntimeState | null | undefined, contract?: TaskContract): string {
  const budget = state?.budget || {};
  const session = contract?.session;
  const usedCalls = finiteNumber(budget.model_calls_used, finiteNumber(state?.model_calls));
  const limitCalls = finiteNumber(budget.model_calls_limit, finiteNumber(session?.max_model_calls));
  const usedTurns = finiteNumber(budget.semantic_turns_used, finiteNumber(state?.turn_index));
  const limitTurns = finiteNumber(budget.semantic_turns_limit, finiteNumber(session?.max_semantic_turns));
  const usedReplans = finiteNumber(budget.replans_used, finiteNumber(state?.replan_count));
  const limitReplans = finiteNumber(budget.replans_limit, finiteNumber(session?.max_replans));
  if (!session && !Object.keys(budget).length) return '非持续交互任务';
  return `模型 ${usedCalls}/${limitCalls || '-'} · 轮次 ${usedTurns}/${limitTurns || '-'} · 重规划 ${usedReplans}/${limitReplans || '-'}`;
}

export function TaskRuntimeAuditPanel({
  contract,
  arp,
  artifactUrl,
}: {
  contract?: TaskContract;
  arp: ArpExecutionSnapshot;
  artifactUrl?: (artifactId: string) => string;
}) {
  const rows = contract ? stageRuntimeRows(contract, arp) : projectedStageRuntimeRows(arp);
  const artifacts = arp.artifacts?.artifacts || [];
  const evidence = arp.contract_evidence?.records || [];
  const completion = arp.completion_contract;
  const completionObligations = Array.isArray(completion?.obligations) ? completion.obligations : [];
  const completionMissing = Array.isArray(completion?.missing) ? completion.missing : [];
  const finalConditions = stringList(contract?.final_conditions);
  const contractErrors = stringList(completion?.contract_errors);
  const active = rows.find((row) => row.current)
    || [...rows].reverse().find((row) => row.status === 'done')
    || rows[0];
  const completionLabel = completion
    ? completion.ok
      ? `通过 ${completionObligations.filter((item) => item.satisfied).length}/${completionObligations.length}`
      : `未通过 · 缺少 ${completionMissing.length}`
    : `待验收 · ${finalConditions.length} 项条件`;

  return (
    <div className="arp-runtime-audit" aria-label="任务运行时审计">
      <div className="arp-runtime-audit-heading">
        <div><ShieldCheck size={14} /><strong>执行路径、能力与验收证据</strong></div>
        <StatusPill
          status={completion?.ok ? 'done' : arp.status === 'failed' ? 'failed' : 'running'}
          label={completionLabel}
        />
      </div>
      <div className="arp-runtime-audit-summary">
        <div><span>当前 typed stage</span><strong>{active ? `${active.stage_type} · ${active.stage_id}` : '无'}</strong></div>
        <div>
          <span>当前能力</span>
          <strong>{active ? `${active.exposed_capabilities.length} 暴露 / ${active.required_capabilities.length + active.verifier_requirements.length} 必需` : '无'}</strong>
          <small>{active?.missing_capabilities.length ? `缺失：${capabilityLabel(active.missing_capabilities)}` : active?.satisfiable ? '运行前可满足性已检查' : '历史 trace 未报告能力缺口'}</small>
        </div>
        <div><span>执行预算</span><strong>{runtimeBudgetLabel(arp.interaction_state, contract)}</strong></div>
        <div><span>Completion Contract</span><strong>{completionLabel}</strong><small>{completion?.reason || '等待所有 final condition 的精确证据'}</small></div>
      </div>

      <div className="arp-runtime-stage-list">
        {rows.map((row, rowIndex) => {
          const failed = row.status === 'failed' || Boolean(row.failure_type || row.error);
          const recoveryLabel = typeof row.recovery === 'string'
            ? row.recovery
            : Object.keys(row.recovery || {}).length ? displayValue(row.recovery) : '';
          const timing = row.timing_reported
            ? `模型 ${row.model_elapsed_ms || 0}ms · Controller ${row.controller_elapsed_ms || 0}ms · Capture ${row.capture_elapsed_ms || 0}ms · ${row.model_calls} 次调用`
            : '耗时尚未上报';
          const selectedProviders = Object.entries(
            row.selected_evidence_providers || {},
          ).map(([predicate, provider]) => `${predicate} → ${provider}`).join(', ');
          return (
            <div className={`arp-runtime-stage ${row.current ? 'active' : ''} ${failed ? 'failed' : ''}`} key={`${row.stage_id}-${rowIndex}`}>
              <div className="arp-runtime-stage-status">
                {failed ? <XCircle size={14} /> : row.status === 'done' ? <CheckCircle2 size={14} /> : row.current ? <Loader2 size={14} className="arp-spin" /> : <Clock3 size={14} />}
                <StatusPill status={failed ? 'failed' : row.status} label={failed ? '失败' : row.status} />
              </div>
              <div className="arp-runtime-stage-identity"><strong>{row.stage_name}</strong><code>{row.stage_type} · {row.stage_id}</code></div>
              <div className="arp-runtime-stage-detail">
                <span title={capabilityLabel(row.required_capabilities)}>阶段需要：{capabilityLabel(row.required_capabilities)}</span>
                <span title={capabilityLabel(row.verifier_requirements)}>Verifier 需要：{capabilityLabel(row.verifier_requirements)}</span>
                <span title={capabilityLabel(row.exposed_capabilities)}>当前暴露：{capabilityLabel(row.exposed_capabilities)}</span>
                {row.missing_capabilities.length > 0 && <span className="danger">缺失：{capabilityLabel(row.missing_capabilities)}</span>}
              </div>
              <div className="arp-runtime-stage-detail">
                <span>Controller：{row.controller_tools.join(', ') || 'local'}</span>
                <span>Model：{row.model_tools.join(', ') || row.model_role || 'none'}</span>
                <span>Adapter：{row.adapter || 'none'}</span>
                <span title={selectedProviders}>Evidence providers：{selectedProviders || 'none'}</span>
              </div>
              <div className="arp-runtime-stage-detail">
                <span aria-label={`${row.stage_id} timing`}>{timing}</span>
                <span>Artifact：{row.artifact_id || '无'} · Evidence：{row.evidence_count}</span>
                <span>尝试：{row.attempts || 0}</span>
              </div>
              {failed && (
                <div className="arp-runtime-stage-error" role="alert">
                  {row.failure_type || 'stage_failed'}{row.error ? `：${row.error}` : ''}
                  {recoveryLabel ? ` · 恢复策略：${recoveryLabel}` : ''}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="arp-runtime-audit-columns">
        <section aria-label="运行工件">
          <div className="arp-runtime-subheading"><strong>Artifacts</strong><span>{arp.artifacts?.count ?? artifacts.length}</span></div>
          {artifacts.length ? artifacts.map((artifact, artifactIndex) => (
            <div className="arp-runtime-record" key={`${artifact.artifact_id}-${artifactIndex}`}>
              {artifactUrl && artifact.mime_type.startsWith('image/') && (
                <img
                  className="arp-artifact-thumbnail"
                  src={artifactUrl(artifact.artifact_id)}
                  alt={`${artifact.sendable ? '任务工件' : '观察帧'} ${artifact.artifact_id}`}
                  loading="lazy"
                />
              )}
              <div><code>{artifact.artifact_id}</code><StatusPill status={artifact.sendable ? 'done' : 'provisional'} label={artifact.sendable ? '可发送' : '观察帧'} /></div>
              <span>{artifact.purpose} · {artifact.mime_type} · {artifact.width}×{artifact.height} · {artifact.size_bytes} B</span>
              <span>{artifact.source_window || '未知来源窗口'}{artifact.source_scope ? ` · ${artifact.source_scope}` : ''}</span>
              <small title={artifact.sha256}>sha256 {artifact.sha256.slice(0, 16)}… · {formatTime(artifact.created_at)}</small>
            </div>
          )) : <EmptyState icon={Database} title="尚无运行工件" />}
        </section>
        <section aria-label="合同证据">
          <div className="arp-runtime-subheading"><strong>Evidence</strong><span>{arp.contract_evidence?.count ?? evidence.length}</span></div>
          {evidence.length ? [...evidence].reverse().slice(0, 12).map((item, evidenceIndex) => (
            <div className={`arp-runtime-record ${item.verified ? 'verified' : 'rejected'}`} key={`${item.id}-${evidenceIndex}`}>
              <div><code>{item.predicate}</code><StatusPill status={item.verified ? 'done' : 'failed'} label={item.verified ? '已验证' : '未验证'} /></div>
              <span>{item.provider || item.source} · stage {item.stage_id || '-'}</span>
              <span>artifact {item.artifact_id || '-'}{item.recipient ? ` · recipient ${item.recipient}` : ''}</span>
              <small>{item.id} · {formatTime(item.captured_at)}</small>
            </div>
          )) : <EmptyState icon={ShieldCheck} title="尚无合同证据" />}
        </section>
        <section aria-label="完成合同">
          <div className="arp-runtime-subheading"><strong>Completion Contract</strong><span>{completionObligations.length || finalConditions.length}</span></div>
          {(completionObligations.length ? completionObligations : contract?.obligations || []).length ? (completionObligations.length ? completionObligations : contract?.obligations || []).map((raw, index) => {
            const item = recordValue(raw) || {};
            const id = String(item.obligation_id || item.id || finalConditions[index] || `condition-${index + 1}`);
            const satisfied = item.satisfied === true;
            const declaredProviders = Array.isArray(item.evidence_providers)
              ? item.evidence_providers.map((provider) => String(recordValue(provider)?.id || '')).filter(Boolean)
              : [];
            return (
              <div className={`arp-runtime-record ${satisfied ? 'verified' : ''}`} key={`${id}-${index}`}>
                <div><code>{id}</code><StatusPill status={satisfied ? 'done' : completion ? 'failed' : 'queued'} label={satisfied ? '满足' : completion ? '缺失' : '待证据'} /></div>
                <span>{String(item.description || item.predicate || '')}</span>
                <span>stage {String(item.stage_id || '-')} · provider {String(item.provider || declaredProviders.join(', ') || '-')}</span>
                <small>evidence {String(item.evidence_id || '-')} · artifact {String(item.artifact_id || item.artifact_ref || '-')}</small>
              </div>
            );
          }) : <EmptyState icon={ShieldCheck} title="合同未声明完成条件" />}
          {contractErrors.map((error, errorIndex) => <div className="arp-runtime-contract-error" role="alert" key={`${error}-${errorIndex}`}>{error}</div>)}
        </section>
      </div>
    </div>
  );
}

function statusTone(status?: string): string {
  if (status === 'done' || status === 'completed' || status === 'success' || status === 'reply') return 'success';
  if (UNSUCCESSFUL_STATUSES.has(status || '')) return 'danger';
  if (status === 'paused') return 'warning';
  if (status === 'running' || status === 'queued' || status === 'waiting_external') return 'active';
  return 'neutral';
}

function StatusPill({ status, label }: { status?: string; label?: string }) {
  return <span className={`arp-status arp-status-${statusTone(status)}`}><span className="arp-status-dot" />{label || STATUS_LABELS[status || 'idle'] || status || '就绪'}</span>;
}

function EmptyState({ icon: Icon, title, detail }: { icon: React.ElementType; title: string; detail?: string }) {
  return (
    <div className="arp-empty">
      <Icon size={20} />
      <strong>{title}</strong>
      {detail && <span>{detail}</span>}
    </div>
  );
}

function Metric({ label, value, unit }: { label: string; value: string | number; unit?: string }) {
  return (
    <div className="arp-metric">
      <span>{label}</span>
      <strong>{value}<small>{unit}</small></strong>
    </div>
  );
}

function RunTimeline({ run, events, compact = false }: { run: RunProjection | null; events?: RuntimeEvent[]; compact?: boolean }) {
  const entries = useMemo(() => {
    const projected = run?.timeline || [];
    if (projected.length) return projected.slice(compact ? -8 : -80).reverse();
    return (events || []).slice(compact ? -8 : -80).reverse().map((event) => ({
      type: event.type,
      ts: event.ts,
      phase: '',
      node_id: event.node_id as string | undefined,
      tool: event.tool as string | undefined,
      status: event.status as string | undefined,
      response_index: event.response_index as number | undefined,
      summary: event.reason || event.content || event.action || event.type,
    }));
  }, [run, events, compact]);

  if (!entries.length) return <EmptyState icon={Activity} title="暂无运行事件" />;
  return (
    <div className="arp-timeline">
      {entries.map((entry, index) => (
        <div className="arp-timeline-row" key={`${entry.type}-${entry.ts || index}-${index}`}>
          <div className={`arp-event-mark arp-event-${entry.type.includes('error') || entry.status === 'failed' ? 'danger' : entry.type.includes('done') ? 'success' : 'default'}`} />
          <time>{formatTime(entry.ts)}</time>
          <div className="arp-event-copy">
            <div><strong>{EVENT_LABELS[entry.type] || entry.type.replace(/^agent_|^arp_/, '').replaceAll('_', ' ')}</strong>{entry.response_index != null && <code>响应 {entry.response_index}</code>}{entry.node_id && <code>{entry.node_id}</code>}{entry.tool && <code>{TOOL_LABELS[entry.tool] || entry.tool}</code>}</div>
            {['vnext_model_reasoning', 'vnext_model_pointer_arguments', 'vnext_pointer_dispatch'].includes(entry.type) ? (
              <details className="arp-reasoning-detail">
                <summary>{entry.type === 'vnext_model_reasoning' ? '展开原始思考' : '展开完整坐标'}</summary>
                <pre>{displayValue(entry.summary)}</pre>
              </details>
            ) : <span>{displayValue(entry.summary)}</span>}
          </div>
        </div>
      ))}
    </div>
  );
}

function PhaseRail({ run }: { run: RunProjection | null }) {
  const phaseIndex = run?.phase === 'terminal' && statusTone(run.status) === 'success'
    ? PHASES.length
    : PHASES.indexOf(run?.phase || '');
  return (
    <div className="arp-phase-rail" aria-label="执行阶段">
      {PHASES.map((phase, index) => (
        <React.Fragment key={phase}>
          <div className={`arp-phase ${index === phaseIndex ? 'active' : ''} ${phaseIndex > index ? 'complete' : ''}`}>
            <span>{phaseIndex > index ? <Check size={11} /> : index + 1}</span>
            <label>{PHASE_LABELS[phase] || phase}</label>
          </div>
          {index < PHASES.length - 1 && <ChevronRight size={13} />}
        </React.Fragment>
      ))}
    </div>
  );
}

export function InteractionRuntimePanel({ state }: { state: InteractionRuntimeState }) {
  const target = state.target_binding;
  const region = state.interaction_region;
  const verification = state.local_verification;
  const external = state.external_change;
  const failure = typeof state.failure_stage === 'string'
    ? { failure_type: state.failure_stage }
    : state.failure_stage;
  const targetName = String(target?.title || target?.app || target?.source || '尚未绑定');
  const targetMeta = [
    target?.hwnd ? `hwnd ${target.hwnd}` : '',
    target?.capture_scope ? String(target.capture_scope) : '',
    target?.frame_id != null ? `frame ${target.frame_id}` : state.frame_id != null ? `frame ${state.frame_id}` : '',
  ].filter(Boolean).join(' · ');
  const regionMeta = [
    region?.policy ? String(region.policy) : '',
    typeof region?.confidence === 'number' ? `置信度 ${Math.round(region.confidence * 100)}%` : '',
    region?.generation != null ? `generation ${region.generation}` : '',
  ].filter(Boolean).join(' · ');
  const verificationLabel = verification?.verified === true
    ? '已通过'
    : verification?.verified === false
      ? '未通过'
      : verification?.status || '待验证';
  const externalLabel = external?.changed
    ? '已检测到外部变化'
    : external?.waiting || external?.armed
      ? '等待外部参与者操作'
      : external?.status || '尚未布防';
  const waitingForExternal = Boolean(
    state.status === 'waiting_external' || external?.waiting || external?.armed,
  );
  const statusLabel = failure
    ? failure.failure_type === 'external_change_timeout'
      ? '外部参与者未响应'
      : '阶段失败'
    : waitingForExternal
      ? '等待外部参与者'
      : STATUS_LABELS[state.status || ''] || state.status || state.mode || '已编译';
  const bindingNames = (state.state_bindings || []).map((item) => item.name).join(', ');

  return (
    <div className="arp-interaction-runtime" aria-label="实时视觉互动闭环">
      <div className="arp-interaction-heading">
        <div><Radio size={14} /><strong>实时视觉互动闭环</strong></div>
        <div>
          {state.session_id && <code>{state.session_id}</code>}
          <StatusPill
            status={failure ? 'failed' : waitingForExternal ? 'waiting_external' : state.status || 'provisional'}
            label={statusLabel}
          />
        </div>
      </div>
      <div className="arp-interaction-grid">
        <div className="arp-interaction-card">
          <span>目标绑定</span><strong>{targetName}</strong>
          <small>{targetMeta || target?.status || '等待权威目标帧'}</small>
        </div>
        <div className="arp-interaction-card">
          <span>交互区域</span><strong>{regionLabel(region)}</strong>
          <small>{regionMeta || region?.blocked_reason || '等待局部区域绑定'}</small>
        </div>
        <div className="arp-interaction-card">
          <span>当前轮次</span><strong>{turnOwnerLabel(state.turn_owner)}</strong>
          <small>{waitingForExternal ? 'Agent 本回合已验证，轮到外部参与者' : state.mode || '待推断互动模式'}</small>
        </div>
        <div className="arp-interaction-card">
          <span>计划动作</span><strong>{interactionActionLabel(state.planned_action)}</strong>
          <small>{state.current_stage_id || '等待动作规划'}</small>
        </div>
        <div className="arp-interaction-card">
          <span>已执行动作</span><strong>{interactionActionLabel(state.executed_action)}</strong>
          <small>{typeof state.executed_action === 'object' && state.executed_action?.transaction_id
            ? `transaction ${state.executed_action.transaction_id}` : '尚无已验证事务'}</small>
        </div>
        <div className={`arp-interaction-card ${verification?.verified === false ? 'danger' : verification?.verified ? 'success' : ''}`}>
          <span>局部验证</span><strong>{verificationLabel}</strong>
          <small>{verification?.reason || verification?.provider || '只接受绑定区域内的新鲜证据'}</small>
        </div>
        <div className={`arp-interaction-card ${external?.waiting ? 'active' : ''}`}>
          <span>外部变化</span><strong>{externalLabel}</strong>
          <small>{external?.watch_scope || '绑定交互区域'}</small>
        </div>
        <div className="arp-interaction-card">
          <span>会话状态</span><strong>{state.status || '已编译'}</strong>
          <small>{waitingForExternal ? '持续会话尚未终结' : `${state.model_calls ?? 0} 次模型调用`}{bindingNames ? ` · ${bindingNames}` : ''}</small>
        </div>
      </div>
      {(state.baseline_artifact_id || state.current_artifact_id) && (
        <div className="arp-interaction-artifacts">
          <span>基线帧 <code>{state.baseline_artifact_id || '-'}</code></span>
          <span>当前帧 <code>{state.current_artifact_id || '-'}</code></span>
        </div>
      )}
      {failure && (
        <div className="arp-interaction-failure" role="alert">
          <XCircle size={14} />
          <div>
            <strong>{failure.stage_id || '未知阶段'} · {failure.stage_type || 'StageRuntime'}</strong>
            <span>{failure.failure_type || 'stage_failed'}{failure.error ? `：${failure.error}` : ''}</span>
          </div>
        </div>
      )}
    </div>
  );
}

function TaskContractRuntimeSection({
  arp,
  events = [],
  artifactUrl,
}: {
  arp: ArpExecutionSnapshot;
  events?: RuntimeEvent[];
  artifactUrl?: (artifactId: string) => string;
}) {
  const taskContract = arp.task_contract;
  const taskIntent = recordValue(taskContract?.intent) || {};
  const operations = stringList(taskIntent.operation);
  const missingCapabilities = stringList(taskContract?.missing_capabilities);
  const contractSatisfiable = Boolean(taskContract)
    && taskContract?.satisfiable !== false
    && missingCapabilities.length === 0;
  const hasPersistedAudit = Boolean(
    arp.stage_trace?.length
    || arp.artifacts?.artifacts?.length
    || arp.contract_evidence?.records?.length
    || arp.completion_contract,
  );
  const interactionRuntime = useMemo(
    () => interactionRuntimeView(arp, events),
    [arp, events],
  );
  if (!taskContract && !hasPersistedAudit) return null;

  return (
    <section className="arp-panel arp-contract-panel">
      <div className="arp-panel-heading">
        <div><Network size={15} /><strong>{taskContract ? '任务合同与阶段运行时' : '历史阶段运行时与证据'}</strong></div>
        <StatusPill
          status={taskContract ? (contractSatisfiable ? 'done' : 'failed') : arp.completion_contract?.ok ? 'done' : arp.status}
          label={taskContract ? (contractSatisfiable ? '能力可满足' : '能力缺失') : '由持久化 trace 恢复'}
        />
      </div>
      {taskContract ? (
        <div className="arp-contract-summary">
          <span>意图</span><code>{operations.join(' → ') || 'semantic'}</code>
          <span>通道</span><code>{String(taskIntent.channel || '-')}</code>
          <span>接收方</span><code>{String(taskIntent.recipient || '-')}</code>
          <span>工件类型</span><code>{String(taskIntent.media_type || 'unknown')}</code>
          <span>经验策略</span><code>{taskContract.experience_policy === 'disabled' || arp.experience_policy === 'disabled' ? '长期经验记忆未启用（冷启动）' : '允许阶段技能'}</code>
        </div>
      ) : (
        <div className="arp-detail-note"><History size={14} />该运行早于合同快照持久化；阶段、能力、工件与证据直接由原始 trace 和受管理工件清单恢复，不重新推断或改写原任务意图。</div>
      )}
      {interactionRuntime && <InteractionRuntimePanel state={interactionRuntime} />}
      <TaskRuntimeAuditPanel contract={taskContract || undefined} arp={arp} artifactUrl={artifactUrl} />
      {taskContract && !contractSatisfiable && <div className="arp-inline-error"><XCircle size={14} />{missingCapabilities.join(', ') || '任务合同缺少可满足性信息'}</div>}
    </section>
  );
}

interface AgentCockpitProps {
  plane: Plane;
  onOpenResources?: () => void;
  onOpenSystem?: () => void;
  onOpenWorkbench?: () => void;
  onLoadConversationFlow?: (automation: AutomationIR) => void;
  onOpenCommand?: () => void;
  onCompact?: () => void;
}

function visualRunTone(run: RunProjection | null): string {
  if (!run) return 'idle';
  if (run.phase === 'awaiting_human') return 'waiting';
  if (run.status === 'paused') return 'paused';
  if (!isTerminalTaskStatus(run.status)) return 'running';
  return taskOutcomeForRun(run) === 'success'
    ? 'done'
    : 'stopped';
}

function runActivityLabel(run: RunProjection | null): string {
  if (!run) return '等待任务';
  if (run.phase === 'awaiting_human') return '等待你的确认';
  if (run.status === 'paused') return '任务已暂停';
  if (isTerminalTaskStatus(run.status)) {
    const outcome = taskOutcomeForRun(run);
    if (outcome === 'error') return '任务未完成';
    if (outcome === 'info') return '任务已停止';
    return run.status === 'reply' ? 'Agent 已回复' : '任务已完成';
  }
  const tool = TOOL_LABELS[run.current_tool || ''] || run.current_tool;
  return tool ? `正在${tool}` : PHASE_LABELS[run.phase] || 'Agent 正在处理';
}

function runVisualProgress(run: RunProjection | null): number {
  if (!run) return 0;
  if (statusTone(run.status) === 'success') return 100;
  if (statusTone(run.status) === 'danger') return 100;
  const index = PHASES.indexOf(run.phase);
  if (index < 0) return run.phase === 'awaiting_human' ? 72 : 12;
  return Math.round(((index + 1) / (PHASES.length + 1)) * 100);
}

const AGENT_THREAD_STORAGE_KEY = 'arp-agent-thread-run-ids';
const AGENT_THREAD_LIMIT = 40;

interface AgentThreadState {
  persisted: boolean;
  threadId: string;
  runIds: string[];
}

function readAgentThreadState(): AgentThreadState {
  try {
    const raw = localStorage.getItem(AGENT_THREAD_STORAGE_KEY);
    if (raw === null) {
      return { persisted: false, threadId: '', runIds: [] };
    }
    const parsed = JSON.parse(raw);
    const values = Array.isArray(parsed) ? parsed : parsed?.run_ids;
    if (!Array.isArray(values)) {
      return { persisted: true, threadId: '', runIds: [] };
    }
    const runIds = values
      .map((value) => String(value || '').trim())
      .filter(Boolean)
      .slice(-AGENT_THREAD_LIMIT);
    return {
      persisted: true,
      threadId: Array.isArray(parsed)
        ? ''
        : String(parsed?.thread_id || '').trim(),
      runIds: [...new Set(runIds)],
    };
  } catch {
    return { persisted: false, threadId: '', runIds: [] };
  }
}

function persistAgentThread(threadId: string, runIds: string[]) {
  try {
    localStorage.setItem(AGENT_THREAD_STORAGE_KEY, JSON.stringify({
      thread_id: threadId,
      run_ids: runIds,
    }));
  } catch {
    // A transient storage failure must not prevent a real Agent run.
  }
}

function CollapsibleAgentText({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const isLong = text.length > 720 || text.split(/\r?\n/).length > 10;
  return (
    <div className="ld-agent-response-text">
      <p className={isLong && !expanded ? 'is-collapsed' : ''}>{text}</p>
      {isLong && (
        <button
          className="ld-agent-response-expand"
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? '收起长内容' : '展开全文'}
        </button>
      )}
    </div>
  );
}

function AgentConversationTurn({
  run,
  isLatest,
  onOpenDetails,
  onEditGoal,
  editDisabled = false,
}: {
  run: RunProjection;
  isLatest: boolean;
  onOpenDetails?: () => void;
  onEditGoal?: (goal: string) => void;
  editDisabled?: boolean;
}) {
  const tone = visualRunTone(run);
  const terminal = isTerminalTaskStatus(run.status);
  const active = !terminal;
  const taskOutcome = taskOutcomeForRun(run);
  const successful = terminal && taskOutcome === 'success';
  const unsuccessful = terminal && taskOutcome !== 'success';
  const directReply = run.status === 'reply';
  const currentTool = TOOL_LABELS[run.current_tool || ''] || run.current_tool || '尚未选择工具';
  const liveEntries = (run.timeline || [])
    .filter((entry) => entry.type === 'live_agent_activity')
    .slice(-12);
  const taskState = String(run.task_state || '').trim();
  const resultText = displayValue(run.result).trim();
  const rawFailureText = displayValue(run.error || run.result).trim();
  // The model's own words are the honest explanation when it could not
  // finish; the rigid system error only supplements when there is none.
  const failureText = resultText
    ? resultText
    : rawFailureText
      ? readableRunError(rawFailureText, run.failure_stage)
      : '打开运行详情可查看失败位置、事件与证据。';

  return (
    <section className="ld-conversation-turn" aria-label="对话轮次" data-run-id={run.run_id}>
      <article className="ld-message-row user">
        <div className="ld-message-content">
          <p>{run.goal}</p>
          <small>{formatTime(run.started_at)}</small>
        </div>
      </article>

      {active && (
        <article className="ld-runtime-message" aria-label="Agent 当前状态">
          <div className={`ld-runtime-glyph ${tone}`}><Bot size={15} /></div>
          <div>
            <span>{runActivityLabel(run)}</span>
            <strong>{PHASE_LABELS[run.phase] || STATUS_LABELS[run.status] || '正在准备'}</strong>
            <small>{run.current_node_id ? `节点 ${run.current_node_id}` : '保持原始目标与当前画面同步'}{run.current_tool ? ` · ${currentTool}` : ''}</small>
          </div>
        </article>
      )}

      {active && (liveEntries.length > 0 || taskState) && (
        <article className="ld-live-work" aria-label="Agent 实时活动" role="status">
          <header className="ld-live-work-header">
            <div><Radio size={14} /><strong>实时活动</strong></div>
            <small>{formatTime(run.updated_at)}</small>
          </header>
          {liveEntries.length > 0 && (
            <div className="ld-live-activity-list" aria-live="polite">
              {liveEntries.map((entry, index) => {
                const kind = String(entry.kind || 'activity');
                const failed = entry.status === 'failed';
                const Icon = kind === 'message'
                  ? Bot
                  : kind === 'task_state'
                    ? ListChecks
                    : kind === 'tool_result'
                      ? failed ? XCircle : CheckCircle2
                      : TerminalSquare;
                const summary = displayValue(entry.summary).trim();
                return (
                  <div className={`ld-live-activity ${kind} ${failed ? 'failed' : ''}`} key={`${entry.ts || 0}-${kind}-${index}`}>
                    <Icon size={13} />
                    <div>
                      <span>
                        {LIVE_ACTIVITY_LABELS[kind] || '活动'}
                        {entry.tool && <code>{TOOL_LABELS[entry.tool] || entry.tool}</code>}
                      </span>
                      {summary && (kind === 'tool_start'
                        ? <pre>{summary}</pre>
                        : <p>{summary}</p>)}
                    </div>
                    <time>{formatTime(entry.ts)}</time>
                  </div>
                );
              })}
            </div>
          )}
          {taskState && (
            <section className="ld-task-board" aria-label="动态任务板">
              <div>
                <ListChecks size={14} />
                <strong>动态任务板</strong>
                <small>模型已更新 {run.task_state_revision || 1} 次</small>
              </div>
              <pre>{taskState}</pre>
            </section>
          )}
        </article>
      )}

      {active && liveEntries.length === 0 && !taskState && run.narration && (
        <article className="ld-agent-response" aria-label="Agent 进度汇报" role="status">
          <div className="ld-agent-response-icon"><Bot size={16} /></div>
          <div className="ld-agent-response-body">
            <div className="ld-agent-response-meta">
              <strong>Agent 正在处理</strong>
              <small>{formatTime(run.updated_at)}</small>
            </div>
            <CollapsibleAgentText text={run.narration} />
          </div>
        </article>
      )}



      {isLatest && run.phase === 'awaiting_human' && (
        <section className="ld-gate-card" aria-label="需要人工确认">
          <div className="ld-gate-icon"><UserCheck size={18} /></div>
          <div><span>需要确认</span><strong>等待人工确认</strong><p>Agent 已停在当前边界。</p></div>
        </section>
      )}

      {!active && successful && (
        <article className={`ld-agent-response ${directReply ? 'reply' : 'complete'}`} aria-label="任务结果" role="status">
          <div className="ld-agent-response-icon">{directReply ? <Bot size={16} /> : <CheckCircle2 size={16} />}</div>
          <div className="ld-agent-response-body">
            <div className="ld-agent-response-meta">
              <strong>{directReply ? 'Agent 回复' : '任务已完成'}</strong>
              <small>{formatTime(run.finished_at || run.updated_at)}</small>
            </div>
            <CollapsibleAgentText text={resultText || (directReply ? 'Agent 已回复。' : '任务已完成。')} />
            {onOpenDetails && (
              <button className="ld-agent-response-details" type="button" onClick={onOpenDetails}>
                <ShieldCheck size={13} />运行详情
              </button>
            )}
          </div>
        </article>
      )}

      {!active && unsuccessful && (
        <article className="ld-agent-response failure" role="alert">
          <div className="ld-agent-response-icon"><XCircle size={16} /></div>
          <div className="ld-agent-response-body">
            <div className="ld-agent-response-meta">
              <strong>{taskOutcome === 'info' ? STATUS_LABELS[run.status] || '任务已停止' : '任务未完成'}</strong>
              <small>{formatTime(run.finished_at || run.updated_at)}</small>
            </div>
            <CollapsibleAgentText text={failureText} />
            {run.failure_stage && (
              <small className="ld-agent-response-failure-stage">
                失败阶段：{readableFailureStage(run.failure_stage)}
              </small>
            )}
            {onEditGoal && run.goal && (
              <button className="ld-agent-response-details" type="button" disabled={editDisabled} onClick={() => onEditGoal(run.goal)}>
                再次编辑
              </button>
            )}
            {onOpenDetails && (
              <button className="ld-agent-response-details" type="button" onClick={onOpenDetails}>
                查看详情
              </button>
            )}
          </div>
        </article>
      )}
    </section>
  );
}

export function AgentCockpit({
  plane,
  onOpenResources,
  onOpenSystem,
  onOpenWorkbench,
  onLoadConversationFlow,
  onOpenCommand,
  onCompact,
}: AgentCockpitProps) {
  const [goal, setGoal] = useState(() => localStorage.getItem('arp-last-goal') || '');
  const [submitting, setSubmitting] = useState(false);
  const [exportingFlow, setExportingFlow] = useState(false);
  const [controlling, setControlling] = useState(false);
  const [actionError, setActionError] = useState('');
  const [composerFocused, setComposerFocused] = useState(false);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [activityOpen, setActivityOpen] = useState(false);
  const [threadState, setThreadState] = useState<AgentThreadState>(readAgentThreadState);
  const [runDetails, setRunDetails] = useState<Record<string, RunProjection>>({});
  const taskScrollRef = useRef<HTMLDivElement | null>(null);
  const composerInputRef = useRef<HTMLTextAreaElement | null>(null);
  const followConversationTail = useRef(true);
  const threadRunKey = threadState.runIds.join('|');
  const runLookup = useMemo(() => {
    const lookup = new Map<string, RunProjection>();
    for (const item of plane.runs || []) lookup.set(item.run_id, item);
    for (const item of Object.values(runDetails)) lookup.set(item.run_id, item);
    if (plane.currentRun) lookup.set(plane.currentRun.run_id, plane.currentRun);
    return lookup;
  }, [plane.runs, plane.currentRun, runDetails]);
  const conversationRuns = useMemo(
    () => threadState.runIds
      .map((runId) => runLookup.get(runId))
      .filter((item): item is RunProjection => Boolean(item)),
    [runLookup, threadRunKey],
  );
  const hasConversation = threadState.runIds.length > 0
    || (!threadState.persisted && Boolean(plane.currentRun));
  const run = conversationRuns[conversationRuns.length - 1]
    || (!threadState.persisted ? plane.currentRun : null);
  const arp = plane.arpSnapshot;
  const active = Boolean(run && isActiveRunStatus(run.status));
  const runtimeBusy = Boolean(plane.currentRun && isActiveRunStatus(plane.currentRun.status));
  const manuallyPaused = Boolean(arp?.manual_paused);
  const modelProfile = plane.overview?.status.model_profile;
  const showComposer = !hasConversation;
  const showFollowupComposer = hasConversation;
  const modelLabel = String(modelProfile?.model || 'Visual Actor');
  const connectionLabel = plane.connection === 'online'
    ? '运行时在线'
    : plane.connection === 'degraded'
      ? '运行时降级'
      : plane.connection === 'offline'
        ? '运行时离线'
        : '正在连接运行时';

  useEffect(() => {
    if (!modelMenuOpen) return;
    const closeModelMenu = (event: PointerEvent) => {
      if (event.target instanceof Element && event.target.closest('.ld-composer-config-field')) return;
      setModelMenuOpen(false);
    };
    window.addEventListener('pointerdown', closeModelMenu);
    return () => window.removeEventListener('pointerdown', closeModelMenu);
  }, [modelMenuOpen]);

  useEffect(() => {
    try {
      if (goal) localStorage.setItem('arp-last-goal', goal);
      else localStorage.removeItem('arp-last-goal');
    } catch {
      // A storage failure must not make the composer unusable.
    }
  }, [goal]);

  useEffect(() => {
    const field = composerInputRef.current;
    if (!field) return;
    const maxHeight = showFollowupComposer ? 196 : 236;
    field.style.height = '0px';
    const nextHeight = Math.min(field.scrollHeight, maxHeight);
    field.style.height = `${nextHeight}px`;
    field.style.overflowY = field.scrollHeight > maxHeight ? 'auto' : 'hidden';
  }, [goal, showFollowupComposer]);

  useEffect(() => {
    const current = plane.currentRun;
    if (!current) return;
    setRunDetails((items) => {
      const previous = items[current.run_id];
      if (
        previous
        && previous.updated_at === current.updated_at
        && previous.status === current.status
        && displayValue(previous.result) === displayValue(current.result)
        && previous.error === current.error
      ) {
        return items;
      }
      return { ...items, [current.run_id]: current };
    });
  }, [plane.currentRun]);

  useEffect(() => {
    const current = plane.currentRun;
    if (threadState.persisted || !current?.run_id) return;
    const next = [current.run_id];
    persistAgentThread(threadState.threadId, next);
    setThreadState({
      persisted: true,
      threadId: threadState.threadId,
      runIds: next,
    });
  }, [
    plane.currentRun?.run_id,
    threadState.persisted,
    threadState.threadId,
  ]);

  useEffect(() => {
    if (!plane.baseUrl || !threadState.runIds.length) return;
    const missing = threadState.runIds.filter(
      (runId) => runId !== plane.currentRun?.run_id && !runDetails[runId],
    );
    if (!missing.length) return;
    let disposed = false;
    let client: ReturnType<Plane['getClient']>;
    try {
      client = plane.getClient();
    } catch {
      return;
    }
    void Promise.all(missing.map(async (runId) => {
      try {
        return (await client.run(runId)).run;
      } catch {
        return null;
      }
    })).then((loaded) => {
      if (disposed) return;
      setRunDetails((items) => {
        const next = { ...items };
        let changed = false;
        for (const item of loaded) {
          if (!item) continue;
          next[item.run_id] = item;
          changed = true;
        }
        return changed ? next : items;
      });
    });
    return () => { disposed = true; };
  }, [plane.baseUrl, threadRunKey]);

  const conversationTailKey = conversationRuns
    .map((item) => `${item.run_id}:${item.status}:${item.updated_at}:${displayValue(item.result).length}`)
    .join('|');

  useEffect(() => {
    if (!followConversationTail.current) return;
    const frame = requestAnimationFrame(() => {
      const viewport = taskScrollRef.current;
      if (!viewport) return;
      if (typeof viewport.scrollTo === 'function') {
        viewport.scrollTo({ top: viewport.scrollHeight, behavior: 'smooth' });
      } else {
        viewport.scrollTop = viewport.scrollHeight;
      }
    });
    return () => cancelAnimationFrame(frame);
  }, [conversationTailKey, showFollowupComposer]);

  const execute = async () => {
    if (!goal.trim() || submitting) return;
    if (runtimeBusy) {
      setActionError('当前任务仍在运行；可以先写好下一条消息，完成后再发送。');
      return;
    }
    setSubmitting(true);
    setActionError('');
    followConversationTail.current = true;
    try {
      const started = await plane.startGoal(
        goal.trim(),
        threadState.threadId,
      );
      if (started?.run_id) {
        localStorage.removeItem('arp-last-goal');
        setThreadState((previous) => {
          const existing = previous.runIds.length
            ? previous.runIds
            : (run?.run_id ? [run.run_id] : []);
          const next = [
            ...existing.filter((runId) => runId !== started.run_id),
            started.run_id,
          ].slice(-AGENT_THREAD_LIMIT);
          persistAgentThread(started.thread_id, next);
          return {
            persisted: true,
            threadId: started.thread_id,
            runIds: next,
          };
        });
      }
      setGoal('');
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setActionError(message);
      void window.electronAPI?.notifyTaskbar?.('任务未启动', message, 'error');
    } finally {
      setSubmitting(false);
    }
  };

  const startNewConversation = () => {
    persistAgentThread('', []);
    localStorage.removeItem('arp-last-goal');
    setThreadState({ persisted: true, threadId: '', runIds: [] });
    setRunDetails({});
    setGoal('');
    setActionError('');
    setActivityOpen(false);
    followConversationTail.current = true;
  };

  const stop = async () => {
    if (controlling) return;
    setControlling(true);
    setActionError('');
    try {
      await plane.cancelCurrent();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
    } finally {
      setControlling(false);
    }
  };

  const togglePause = async () => {
    if (controlling) return;
    setControlling(true);
    setActionError('');
    try {
      if (manuallyPaused) await plane.resumeCurrent();
      else await plane.pauseCurrent();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
    } finally {
      setControlling(false);
    }
  };

  const exportConversationFlow = async () => {
    if (exportingFlow || runtimeBusy || !threadState.threadId || !onLoadConversationFlow) return;
    setExportingFlow(true);
    setActionError('');
    try {
      const client = plane.getClient();
      const exported = await client.exportThreadAutomation(threadState.threadId, threadState.runIds);
      const automation = await client.script<AutomationIR>(exported.ir);
      const manifest = automation.source?.export_manifest;
      const manifestRecord = manifest && typeof manifest === 'object' && !Array.isArray(manifest)
        ? manifest as Record<string, unknown>
        : {};
      const reasons = Array.isArray(manifestRecord.reasons)
        ? manifestRecord.reasons.map(String).filter(Boolean)
        : [];
      const unsupported = reasons.filter((reason) => (
        reason.includes('unsupported') || reason.includes('not_replayable')
      ));
      const partial = manifestRecord.partial === true
        || manifestRecord.complete === false
        || reasons.length > 0;
      const lines = [
        partial
          ? '这是部分导出（partial），请先在流程工作台中检查和编辑。'
          : '已提取本对话中可重放的动作。',
        `${automation.steps.length} 个动作将加载到流程工作台，不会自动运行。`,
      ];
      if (unsupported.length) lines.push(`不支持或未纳入的动作：${unsupported.join('；')}`);
      const remaining = reasons.filter((reason) => !unsupported.includes(reason));
      if (remaining.length) lines.push(`另有 ${remaining.length} 项完整性提示，可在流程元数据中检查。`);
      lines.push('是否继续加载？');
      if (!window.confirm(lines.join('\n'))) return;
      onLoadConversationFlow(automation);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
    } finally {
      setExportingFlow(false);
    }
  };

  const renderComposerField = (compact = false) => (
    <>
      <textarea
        ref={composerInputRef}
        className="ld-composer-input"
        aria-label="消息输入"
        rows={1}
        spellCheck={false}
        autoComplete="off"
        value={goal}
        onChange={(event) => setGoal(event.target.value)}
        onFocus={() => setComposerFocused(true)}
        onBlur={() => setComposerFocused(false)}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing || event.keyCode === 229) return;
          if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            void execute();
          }
        }}
        placeholder={runtimeBusy
          ? 'Agent 正在处理；可以先写好下一条消息…'
          : compact
            ? '继续说明下一步…'
            : '描述任务或直接告诉 Agent 你需要什么…'}
        disabled={submitting}
      />
      <div className="ld-composer-footer">
        <button className="ld-round-control" type="button" onClick={onOpenCommand} aria-label="添加上下文" title="添加上下文或打开隐藏功能（Ctrl+K）">
          <Plus size={18} />
        </button>
        <div
          className={`ld-composer-context ${compact || composerFocused || goal.trim() || modelMenuOpen ? '' : 'soft-hidden'}`}
          aria-label="输入框运行上下文"
        >
          <span className="ld-composer-config-field">
            <button
              className="ld-composer-config-trigger"
              type="button"
              aria-expanded={modelMenuOpen}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => setModelMenuOpen((value) => !value)}
            >
              {modelLabel}
            </button>
            {modelMenuOpen && (
              <span className="ld-composer-inline-options" role="listbox" aria-label="模型选项">
                <button
                  type="button"
                  role="option"
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => {
                    setModelMenuOpen(false);
                    onOpenSystem?.();
                  }}
                >
                  模型与接口设置
                </button>
              </span>
            )}
          </span>
          <span className="ld-composer-context-separator">·</span>
          <span className="ld-composer-runtime"><i className={`ld-status-dot ${plane.connection || 'connecting'}`} />{connectionLabel}</span>
        </div>
        <span className="ld-send-hint" hidden={!goal.trim()}>Enter 发送 · Shift + Enter 换行</span>
        <button
          className={`ld-round-control ld-send-control ${goal.trim() ? 'ready' : ''}`}
          type="button"
          aria-label="发送消息"
          onClick={() => void execute()}
          disabled={!goal.trim() || submitting || runtimeBusy}
        >
          {submitting ? <Loader2 size={17} className="arp-spin" /> : <ArrowUp size={18} />}
        </button>
      </div>
    </>
  );

  if (showComposer) {
    return (
      <div className="ld-agent-stage ld-agent-stage-pristine">
        <section className="ld-composer-window ld-floating-window" aria-label="新建对话">
          <button
            className="ld-corner-entry"
            type="button"
            onClick={onOpenResources}
            aria-label="打开资料"
            title="运行记录、技能与设置"
          >
            <Layers3 size={14} />
          </button>
          <span className="ld-drag-hint" aria-hidden="true" />
          {renderComposerField()}
          {composerFocused && !goal.trim() && (
            <div className="ld-starter-popover" aria-label="快速入口">
              <span>所有能力默认收起，需要时再打开</span>
              <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={onOpenResources}><History size={14} /><span><strong>运行与资料</strong><small>历史、技能、记忆和设置</small></span><ChevronRight size={14} /></button>
              <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={onOpenWorkbench}><Activity size={14} /><span><strong>执行工作台</strong><small>步骤、画面、证据和运行审计</small></span><ChevronRight size={14} /></button>
              <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={onOpenCommand}><Command size={14} /><span><strong>命令面板</strong><small>直接搜索任何隐藏功能</small></span><ChevronRight size={14} /></button>
            </div>
          )}
          {(actionError || plane.error || (modelProfile?.model && modelProfile.supports_vision === false)) && (
            <div className="ld-composer-notices">
              {(actionError || plane.error) && (
                <div className="ld-composer-notice" role="alert">
                  <XCircle size={13} />
                  <span>{actionError || plane.error}</span>
                </div>
              )}
              {modelProfile?.model && modelProfile.supports_vision === false && (
                <div className="ld-composer-notice warning">
                  <Eye size={13} />
                  <span>{String(modelProfile.model)} 不接受图像输入；视觉任务能力会受限。</span>
                </div>
              )}
            </div>
          )}
        </section>
      </div>
    );
  }

  const tone = visualRunTone(run);
  const progress = runVisualProgress(run);
  const currentTool = TOOL_LABELS[run?.current_tool || ''] || run?.current_tool || '尚未选择工具';
  const visibleConversationRuns = conversationRuns.length
    ? conversationRuns
    : (run ? [run] : []);
  const conversationCount = Math.max(threadState.runIds.length, visibleConversationRuns.length);

  return (
    <div className="ld-agent-stage ld-agent-stage-run">
      <article className={`ld-task-window ld-floating-window status-${tone} ${showFollowupComposer ? 'has-followup-composer' : ''}`}>
        <header className="ld-task-window-header">
          <button className="ld-task-identity" type="button" onClick={onOpenResources} aria-label="打开资料" title="打开运行记录和资料">
            <span className={`ld-window-signal ${tone}`} />
            <span>
              <strong>{visibleConversationRuns[0]?.goal || '当前对话'}</strong>
              <small>{conversationCount} 轮 · {runActivityLabel(run)}</small>
            </span>
          </button>
          <div className="ld-task-window-actions">
            <button
              type="button"
              aria-label="将本对话动作转为流程"
              title={!threadState.threadId
                ? '当前对话还没有可导出的运行记录'
                : runtimeBusy
                  ? '对话结束后可转为流程'
                  : '提取普通 ARP 可重放动作；确认后仅加载，不会运行'}
              disabled={!threadState.threadId || runtimeBusy || exportingFlow || !onLoadConversationFlow}
              onClick={() => void exportConversationFlow()}
            >
              {exportingFlow ? <Loader2 size={15} className="arp-spin" /> : <Workflow size={15} />}
            </button>
            <button type="button" aria-label="新建对话" title={runtimeBusy || submitting ? '当前提交结束后可新建对话' : '新建对话'} disabled={runtimeBusy || submitting} onClick={startNewConversation}><Plus size={15} /></button>
            <button type="button" aria-label="打开工作台" title="执行、画面、上下文与证据" onClick={onOpenWorkbench}><Activity size={15} /></button>
            <button type="button" aria-label="收起为迷你悬浮窗" title="收起为迷你悬浮窗" onClick={onCompact}><Minimize2 size={15} /></button>
          </div>
        </header>

        <div
          className="ld-task-scroll"
          ref={taskScrollRef}
          onScroll={(event) => {
            const viewport = event.currentTarget;
            followConversationTail.current = (
              viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight
            ) < 84;
          }}
        >
          <div className="ld-conversation-column">
            {visibleConversationRuns.map((item, index) => (
              <AgentConversationTurn
                key={item.run_id}
                run={item}
                isLatest={index === visibleConversationRuns.length - 1}
                onOpenDetails={onOpenResources}
                editDisabled={submitting}
                onEditGoal={(value) => {
                  setGoal(value);
                  setActionError('');
                  requestAnimationFrame(() => {
                    composerInputRef.current?.focus();
                    composerInputRef.current?.setSelectionRange(value.length, value.length);
                  });
                }}
              />
            ))}

            {!visibleConversationRuns.length && (
              <article className="ld-runtime-message ld-conversation-loading" aria-label="正在恢复对话">
                <div className="ld-runtime-glyph running"><Loader2 size={15} className="arp-spin" /></div>
                <div><span>对话记录</span><strong>正在恢复</strong><small>从原有运行记录读取完整结果。</small></div>
              </article>
            )}

            {actionError && (
              <div className="ld-inline-alert" role="alert"><XCircle size={14} /><span>{actionError}</span></div>
            )}
          </div>
        </div>

        <div className={`ld-task-dock ${showFollowupComposer ? 'with-composer' : ''}`}>
          <section
            className={`ld-activity-capsule ${tone} ${activityOpen ? 'is-open' : ''}`}
            aria-label="运行状态；悬停或点击查看完整执行"
          >
            <div className="ld-activity-details">
              <div className="ld-activity-details-scroll">
                <div className="ld-activity-head">
                  <div><StatusPill status={run?.status} /><strong>{run?.goal}</strong></div>
                  <span>{formatElapsed(run?.started_at, run?.finished_at)}</span>
                </div>
                <PhaseRail run={run} />
                <dl className="ld-runtime-facts">
                  <div><dt>当前节点</dt><dd>{run?.current_node_id || '-'}</dd></div>
                  <div><dt>当前工具</dt><dd>{currentTool}</dd></div>
                  <div><dt>事件</dt><dd>{run?.timeline.length || plane.events.length}</dd></div>
                  <div><dt>证据</dt><dd>{run?.evidence.length || 0}</dd></div>
                </dl>
                <div className="ld-runtime-controls">
                  {active && run?.phase !== 'awaiting_human' && <button type="button" onClick={() => void togglePause()} disabled={controlling}>{manuallyPaused ? <Play size={13} /> : <Pause size={13} />}{manuallyPaused ? '继续' : '暂停'}</button>}
                  {active && <button type="button" onClick={() => void stop()} disabled={controlling}>{controlling ? <Loader2 size={13} className="arp-spin" /> : <CircleStop size={13} />}停止</button>}
                  <button type="button" onClick={onOpenWorkbench}><Maximize2 size={13} />打开完整执行</button>
                </div>
                <RunTimeline run={run} events={plane.events} compact />
              </div>
            </div>
            <button
              className="ld-activity-summary"
              type="button"
              aria-expanded={activityOpen}
              onClick={() => setActivityOpen((value) => !value)}
            >
              <span className="ld-progress-ring" style={{ '--ld-progress': `${progress}%` } as React.CSSProperties}><i /></span>
              <span><strong>{runActivityLabel(run)}</strong><small>{currentTool}</small></span>
              <small>{formatElapsed(run?.started_at, run?.finished_at)}</small>
            </button>
          </section>
          {showFollowupComposer && (
            <section className="ld-compact-composer" aria-label="继续输入">
              {renderComposerField(true)}
            </section>
          )}
        </div>
      </article>
    </div>
  );
}

function Studio({ plane, onOpenAutomation }: { plane: Plane; onOpenAutomation?: () => Promise<void> }) {
  const script = useStore((state) => state.script);
  const saveScript = useStore((state) => state.saveScript);
  const newScript = useStore((state) => state.newScript);
  const isDirty = useStore((state) => state.isDirty);
  const undo = useStore((state) => state.undo);
  const redo = useStore((state) => state.redo);
  const setUI = useStore((state) => state.setUI);
  const fitToScreen = useStore((state) => state.fitToScreen);
  const [validation, setValidation] = useState<GraphValidation | null>(null);
  const [validationSource, setValidationSource] = useState<'local' | 'runtime'>('local');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [mobilePanel, setMobilePanel] = useState<'library' | 'properties' | null>(null);
  const processedNodeEvents = useRef(new Set<string>());
  const automation = useMemo(
    () => automationIrFromFlowScript(script, `air-${script.meta.id}`),
    [script],
  );
  const validatedDocumentFingerprint = useMemo(
    () => flowDocumentFingerprint(script),
    [script],
  );
  const assertCurrentDocument = () => {
    const currentScript = useStore.getState().script;
    if (flowDocumentFingerprint(currentScript) !== validatedDocumentFingerprint) {
      throw new Error('流程在验证期间已发生变化；旧结果已丢弃，请重新验证或运行当前版本。');
    }
  };
  const graph = useMemo<ArpGraph>(() => {
    return automationRuntimeGraph(automation);
  }, [automation]);
  const compilation = graph.metadata.compilation as { native_nodes?: number; legacy_nodes?: number; legacy_types?: string[] } | undefined;
  const activeRun = Boolean(plane.currentRun && isActiveRunStatus(plane.currentRun.status));
  const runtimeReady = Boolean(plane.baseUrl);

  useEffect(() => {
    setUI({ panels_visible: true, show_node_library: true, show_property_panel: true });
    requestAnimationFrame(fitToScreen);
  }, []);

  useEffect(() => {
    setValidation(null);
    setNotice('');
  }, [automation]);

  useEffect(() => {
    const relevant = plane.events.slice(-20);
    for (const event of relevant) {
      const nodeId = typeof event.node_id === 'string' ? event.node_id : '';
      const eventKey = `${event.ts || 0}:${event.type}:${nodeId}:${String(event.status || event.ok || '')}`;
      if (!nodeId || processedNodeEvents.current.has(eventKey)) continue;
      const node = useStore.getState().script.nodes.find((item) => item.id === nodeId);
      if (!node) continue;
      if (event.type === 'arp_node_start' && node.status !== 'running') {
        useStore.getState().updateNodeStatus(nodeId, 'running');
      }
      if (event.type === 'arp_node_done') {
        const nextStatus = event.ok === false ? 'error' : 'success';
        if (node.status !== nextStatus || (event.error && node.error_msg !== String(event.error))) {
          useStore.getState().updateNodeStatus(nodeId, nextStatus, String(event.error || ''));
        }
      }
      processedNodeEvents.current.add(eventKey);
    }
    if (processedNodeEvents.current.size > 500) {
      processedNodeEvents.current = new Set([...processedNodeEvents.current].slice(-250));
    }
  }, [plane.events]);

  const validate = useCallback(async () => {
    setBusy(true); setError('');
    try {
      const local = validateAutomationLocally(automation);
      setValidationSource('local');
      setValidation(local);
      if (!local.valid) return local;
      if (plane.connection !== 'online') {
        const offlineResult = {
          ...local,
          warnings: [...local.warnings, '本地结构检查已通过；连接本机运行时后才能执行。'],
        };
        setValidation(offlineResult);
        return offlineResult;
      }
      const result = await plane.validateAutomation(automation);
      assertCurrentDocument();
      setValidationSource('runtime');
      setValidation(result);
      return result;
    } catch (validateError) {
      setError(validateError instanceof Error ? validateError.message : String(validateError));
      return null;
    } finally { setBusy(false); }
  }, [plane, automation]);

  const run = async () => {
    setBusy(true); setError('');
    try {
      const local = validateAutomationLocally(automation);
      setValidationSource('local');
      setValidation(local);
      if (!local.valid) return;
      if (!runtimeReady) throw new Error('本机运行时尚未连接；当前流程仍可离线编辑和保存。');
      const result = await plane.validateAutomation(automation);
      assertCurrentDocument();
      setValidationSource('runtime');
      setValidation(result);
      if (!result.valid) return;
      await plane.startAutomation(automation);
    } catch (runError) {
      setError(runError instanceof Error ? runError.message : String(runError));
    } finally { setBusy(false); }
  };

  const stop = async () => {
    setBusy(true); setError('');
    try { await plane.cancelCurrent(); }
    catch (stopError) { setError(stopError instanceof Error ? stopError.message : String(stopError)); }
    finally { setBusy(false); }
  };

  const save = async () => {
    setError(''); setNotice('');
    try {
      const result = await saveScript();
      if (result.status === 'error') throw new Error(result.error || '保存失败');
      if (result.status === 'saved') setNotice('已保存');
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : String(saveError));
    }
  };

  const createNew = () => {
    if (!confirmDocumentReplacement()) return;
    newScript();
    setError('');
  };

  const open = async () => {
    if (!onOpenAutomation) return;
    setError('');
    try { await onOpenAutomation(); }
    catch (openError) { setError(openError instanceof Error ? openError.message : String(openError)); }
  };

  return (
    <div className="arp-studio-page">
      <header className="arp-studio-toolbar">
        <div className="arp-studio-title"><GitBranch size={16} /><div><strong>{script.meta.name}</strong><span>{graph.nodes.length} 个节点 / {graph.edges.length} 条连线 / {compilation?.native_nodes ?? graph.nodes.length} 个原生节点</span></div></div>
        {Boolean(compilation?.legacy_nodes) && <StatusPill status="failed" label={`${compilation?.legacy_nodes} 个节点不支持`} />}
        {isDirty && <StatusPill status="queued" label="未保存" />}
        {!runtimeReady && <StatusPill status="paused" label="本地编辑" />}
        {validation && <StatusPill status={validation.valid ? 'success' : 'failed'} label={validation.valid ? (validationSource === 'local' ? '本地结构通过' : '运行时有效') : `${validation.errors.length} 个错误`} />}
        <div className="arp-action-spacer" />
        <button className="arp-icon-button arp-mobile-panel-button" title="节点库" aria-label="显示/隐藏节点库" aria-pressed={mobilePanel === 'library'} onClick={() => setMobilePanel((current) => current === 'library' ? null : 'library')}><PanelLeft size={15} /></button>
        <button className="arp-icon-button arp-mobile-panel-button" title="节点属性" aria-label="显示/隐藏节点属性" aria-pressed={mobilePanel === 'properties'} onClick={() => setMobilePanel((current) => current === 'properties' ? null : 'properties')}><SlidersHorizontal size={15} /></button>
        <button className="arp-icon-button" title="适应工作流" aria-label="适应工作流" onClick={fitToScreen}><Network size={15} /></button>
        <button className="arp-icon-button" title="新建工作流 (Ctrl+N)" aria-label="新建工作流" onClick={createNew} disabled={busy}><FilePlus2 size={15} /></button>
        <button className="arp-icon-button" title="打开工作流 (Ctrl+O)" aria-label="打开工作流" onClick={() => void open()} disabled={busy || !onOpenAutomation}><FolderOpen size={15} /></button>
        <button className="arp-icon-button" title="撤销 (Ctrl+Z)" aria-label="撤销" onClick={undo} disabled={busy}><Undo2 size={15} /></button>
        <button className="arp-icon-button" title="重做 (Ctrl+Y)" aria-label="重做" onClick={redo} disabled={busy}><Redo2 size={15} /></button>
        <FlowFragmentComposer
          propose={plane.proposeFlowFragment}
          disabled={busy || activeRun || !runtimeReady}
          disabledReason={activeRun ? '当前任务结束后才能生成流程片段' : '本机运行时尚未连接'}
        />
        <button className="arp-button arp-studio-command" title="保存工作流" aria-label="保存工作流" onClick={() => void save()} disabled={busy}><Save size={14} /><span>保存</span></button>
        <button className="arp-button arp-studio-command" title="验证工作流" aria-label="验证工作流" onClick={validate} disabled={busy}><ShieldCheck size={14} /><span>验证</span></button>
        {activeRun ? (
          <button className="arp-button arp-button-danger arp-studio-command" title="停止当前运行" aria-label="停止当前运行" onClick={() => void stop()} disabled={busy}>{busy ? <Loader2 size={14} className="arp-spin" /> : <CircleStop size={14} />}<span>停止</span></button>
        ) : (
          <button className="arp-button arp-button-primary arp-studio-command" title={runtimeReady ? '运行工作流' : '本机运行时尚未连接'} aria-label="运行工作流" onClick={run} disabled={busy || !runtimeReady}>{busy ? <Loader2 size={14} className="arp-spin" /> : <Play size={14} fill="currentColor" />}<span>运行工作流</span></button>
        )}
      </header>
      {(error || notice) && (
        <div className={`arp-studio-message-bar ${error ? 'danger' : 'success'}`} role={error ? 'alert' : 'status'}>
          {error || notice}
        </div>
      )}
      {activeRun && (
        <div className="arp-studio-message-bar info" role="status">
          当前运行使用启动时快照；画布编辑只影响下一次运行。
        </div>
      )}
      {validation && (!validation.valid || validation.warnings.length > 0) && (
        <div className={`arp-validation-bar ${validation.valid ? 'warning' : 'danger'}`}>
          {(validation.errors.length ? validation.errors : validation.warnings).join(' · ')}
        </div>
      )}
      <div className={`arp-studio-shell${mobilePanel ? ` mobile-${mobilePanel}-open` : ''}${busy ? ' is-locked' : ''}`} aria-busy={busy}>
        <NodeLibrary capabilities={plane.capabilities} />
        <main className="arp-canvas-stage"><FlowCanvas /></main>
        <PropertyPanel />
        {mobilePanel && <button type="button" className="arp-studio-scrim" aria-label="关闭侧栏" onClick={() => setMobilePanel(null)} />}
      </div>
    </div>
  );
}

export function RunsView({ plane }: { plane: Plane }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'success' | 'failed'>('all');
  const [detail, setDetail] = useState<CurrentRunResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const [detailRevision, setDetailRevision] = useState(0);
  const visibleRuns = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return plane.runs.filter((run) => {
      const status = String(run.status || '').toLowerCase();
      const statusMatches = statusFilter === 'all'
        || (statusFilter === 'active' && isActiveRunStatus(status))
        || (statusFilter === 'success' && ['done', 'completed', 'success'].includes(status))
        || (statusFilter === 'failed' && UNSUCCESSFUL_STATUSES.has(status));
      const textMatches = !needle
        || `${run.goal} ${run.run_id} ${run.kind}`.toLocaleLowerCase().includes(needle);
      return statusMatches && textMatches;
    });
  }, [plane.runs, query, statusFilter]);
  const selected = visibleRuns.find((run) => run.run_id === selectedId) || visibleRuns[0] || null;

  useEffect(() => {
    if (selected?.run_id !== selectedId) setSelectedId(selected?.run_id || null);
  }, [selected?.run_id, selectedId]);

  useEffect(() => {
    if (!selected?.run_id || !plane.baseUrl) {
      setDetail(null);
      return;
    }
    let disposed = false;
    setDetail(null);
    setDetailLoading(true);
    setDetailError('');
    plane.getClient().run(selected.run_id)
      .then((next) => { if (!disposed) setDetail(next); })
      .catch((error) => { if (!disposed) setDetailError(error instanceof Error ? error.message : String(error)); })
      .finally(() => { if (!disposed) setDetailLoading(false); });
    return () => { disposed = true; };
  }, [selected?.run_id, plane.baseUrl, detailRevision]);

  const liveDetail = selected?.run_id === plane.currentRun?.run_id
    ? { ok: true, run: plane.currentRun, arp: plane.arpSnapshot } satisfies CurrentRunResponse
    : null;
  const resolved = liveDetail || detail;
  const detailRun = resolved?.run || selected;
  const detailArp = resolved?.arp || null;
  const refresh = async () => {
    await plane.refresh();
    setDetailRevision((value) => value + 1);
  };

  return (
    <div className="arp-page arp-runs-page">
      <div className="arp-page-title"><div><span className="arp-kicker">历史记录</span><h2>运行记录</h2></div><button className="arp-button" onClick={refresh}><RefreshCw size={14} />刷新</button></div>
      <div className="arp-runs-layout">
        <section className="arp-run-list">
          <div className="arp-run-filters">
            <label><Search size={13} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索目标或 run id" /></label>
            <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as typeof statusFilter)} aria-label="运行状态筛选">
              <option value="all">全部状态</option>
              <option value="active">进行中</option>
              <option value="success">已完成</option>
              <option value="failed">失败/取消</option>
            </select>
          </div>
          <div className="arp-list-header"><span>任务</span><span>状态</span><span>耗时</span></div>
          {visibleRuns.map((run) => (
            <button key={run.run_id} className={`arp-run-list-row ${selected?.run_id === run.run_id ? 'selected' : ''}`} onClick={() => setSelectedId(run.run_id)}>
              <div><strong>{run.goal || run.run_id}</strong><span>{RUN_KIND_LABELS[run.kind] || run.kind} · {formatTime(run.started_at)}</span></div>
              <StatusPill status={run.status} />
              <code>{formatDuration(run)}</code>
            </button>
          ))}
          {!plane.runs.length && <EmptyState icon={History} title="暂无运行记录" />}
          {plane.runs.length > 0 && !visibleRuns.length && <EmptyState icon={Search} title="没有匹配的运行记录" detail="请调整关键词或状态筛选" />}
        </section>
        <section className="arp-run-detail">
          {detailRun ? (
            <>
              <div className="arp-run-detail-head"><div><span className="arp-kicker">{RUN_KIND_LABELS[detailRun.kind] || detailRun.kind}</span><h3>{detailRun.goal || detailRun.run_id}</h3><code>{detailRun.run_id}</code></div><StatusPill status={detailRun.status} /></div>
              {detailLoading && <div className="arp-detail-loading"><Loader2 size={14} className="arp-spin" />正在加载合同、工件与证据…</div>}
              {detailError && <div className="arp-inline-error"><XCircle size={14} />运行详情加载失败：{detailError}</div>}
              <div className="arp-metrics-row compact">
                <Metric label="耗时" value={formatDuration(detailRun)} />
                <Metric label="决策次数" value={detailRun.metrics.decision_count} />
                <Metric label="工具次数" value={detailRun.metrics.tool_count} />
                <Metric label="视觉模型调用" value={detailRun.metrics.vlm_call_count} />
                <Metric label="锚点命中" value={`${Math.round((detailRun.metrics.anchor_cache_hit_rate || 0) * 100)}%`} />
                <Metric label="视觉伺服修正" value={detailRun.metrics.visual_servo_correction_count || 0} />
              </div>
              {detailRun.error && <div className="arp-inline-error"><XCircle size={14} />{detailRun.error}</div>}
              {detailArp && (
                <TaskContractRuntimeSection
                  arp={detailArp}
                  events={selected?.run_id === plane.currentRun?.run_id ? plane.events : []}
                  artifactUrl={(artifactId) => plane.getClient().artifactUrl(detailArp.run_id, artifactId)}
                />
              )}
              {!detailLoading && !detailArp && detailRun.kind === 'agent' && (
                <div className="arp-detail-note"><Bot size={14} />该记录来自兼容 Agent 运行，没有 ARP typed contract；时间线与证据仍按运行投影展示。</div>
              )}
              <div className="arp-panel-heading"><div><Activity size={15} /><strong>时间线</strong></div><span>{detailRun.timeline.length} 个事件</span></div>
              <RunTimeline run={detailRun} />
            </>
          ) : <EmptyState icon={History} title="请选择一条运行记录" />}
        </section>
      </div>
    </div>
  );
}

interface TraceItem { run_id: string; task: string; status: string; steps: number }
interface ScriptItem {
  file: string;
  name: string;
  task: string;
  steps: number;
  format: string;
  valid: boolean;
  validation_error?: string;
  trust: 'verified' | 'draft';
  memory_id?: string | null;
  memory_status?: string | null;
  verification_state?: string;
  verification_count?: number;
}
interface MemoryItem {
  id: string;
  task: string;
  app?: string;
  script?: string;
  status?: string;
  confidence?: number;
  uses?: number;
  successes?: number;
  provisional?: boolean;
  conditions?: string[];
  evidence?: string[];
  verification?: Array<Record<string, any>>;
  applicability?: Record<string, any>;
  verification_state?: string;
  script_sha256?: string;
}
interface SessionItem { task: string; status: string; result: string; run_id: string }
type ReplayJobKind = 'replay' | 'memory_verification';
const ACTIVE_REPLAY_JOB_STATUSES = new Set(['queued', 'waiting_resources', 'running', 'canceling']);
const MAX_REPLAY_POLL_ERRORS = 3;

function replayPollErrorStatus(error: unknown): number | null {
  if (!error || typeof error !== 'object' || !('status' in error)) return null;
  const status = Number((error as { status?: unknown }).status);
  return Number.isFinite(status) ? status : null;
}

export interface ReplayJobView {
  replay_id: string;
  kind: ReplayJobKind;
  memory_id?: string;
  script?: string;
  status: string;
  ok?: boolean | null;
  error?: string;
  result?: Record<string, any> | null;
}

export function replayJobFromRuntimeSnapshot(job: RuntimeJobSnapshot): ReplayJobView | null {
  if (job.kind !== 'replay' && job.kind !== 'memory_verification') return null;
  const memoryId = String(job.metadata.memory_id || '');
  if (job.kind === 'memory_verification' && !memoryId) return null;
  return {
    replay_id: job.job_id,
    kind: job.kind,
    ...(memoryId ? { memory_id: memoryId } : {}),
    script: String(job.metadata.script || ''),
    status: job.status,
    error: job.error,
  };
}

export const replayOutcomeStatus = (job: ReplayJobView | null): string => {
  if (!job) return '';
  return job.ok === false ? 'failed' : job.status;
};

type ReplayMode = 'offline_graph' | 'skill' | 'macro';

function AssetsView({ plane, openStudio }: { plane: Plane; openStudio: () => void }) {
  const [traces, setTraces] = useState<TraceItem[]>([]);
  const [scripts, setScripts] = useState<ScriptItem[]>([]);
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [recording, setRecording] = useState(false);
  const [replayMode, setReplayMode] = useState<ReplayMode>('offline_graph');
  const [replayJob, setReplayJob] = useState<ReplayJobView | null>(null);
  const [recordingTask, setRecordingTask] = useState('');
  const [recordingName, setRecordingName] = useState('');
  const [evidencePredicate, setEvidencePredicate] = useState('visible.text');
  const [evidenceValue, setEvidenceValue] = useState('');
  const [memoryView, setMemoryView] = useState<'long' | 'session'>('long');
  const [selectedMemoryId, setSelectedMemoryId] = useState('');
  const [memoryDetail, setMemoryDetail] = useState<Record<string, any> | null>(null);
  const [memoryEvidencePredicate, setMemoryEvidencePredicate] = useState('visible.text');
  const [memoryEvidenceValue, setMemoryEvidenceValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const loadScript = useStore((state) => state.loadScript);
  const replayActive = Boolean(replayJob && ACTIVE_REPLAY_JOB_STATUSES.has(replayJob.status));
  const replayStatus = replayOutcomeStatus(replayJob);

  const readReplayJob = useCallback(async (job: ReplayJobView): Promise<ReplayJobView> => {
    const client = plane.getClient();
    const current = job.kind === 'memory_verification'
      ? await client.memoryVerificationStatus(job.memory_id!, job.replay_id)
      : await client.replayStatus(job.replay_id);
    return {
      ...current,
      kind: job.kind,
      ...(job.memory_id ? { memory_id: job.memory_id } : {}),
    } as ReplayJobView;
  }, [plane.baseUrl]);

  const refresh = useCallback(async () => {
    if (!plane.baseUrl) return;
    try {
      const client = plane.getClient();
      const [nextTraces, nextScripts, nextMemories, nextSessions, apprentice] = await Promise.all([
        client.traces(), client.scripts(), client.memories(), client.sessions(), client.apprenticeStatus(),
      ]);
      setTraces(nextTraces); setScripts(nextScripts); setMemories(nextMemories); setSessions(nextSessions); setRecording(Boolean(apprentice.recording));
      if (apprentice.task) setRecordingTask((current) => current || apprentice.task || '');
      if (apprentice.name) setRecordingName((current) => current || apprentice.name || '');
      setError('');
    } catch (refreshError) { setError(refreshError instanceof Error ? refreshError.message : String(refreshError)); }
  }, [plane.baseUrl]);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => {
    if (replayJob || busy || !plane.baseUrl) return;
    const candidates = (plane.overview?.status.runtime_jobs || [])
      .map(replayJobFromRuntimeSnapshot)
      .filter((job): job is ReplayJobView => Boolean(job));
    const recent = candidates.find((job) => ACTIVE_REPLAY_JOB_STATUSES.has(job.status)) || candidates[0];
    if (!recent) return;
    let disposed = false;
    readReplayJob(recent)
      .then((job) => { if (!disposed) setReplayJob(job as ReplayJobView); })
      .catch(() => { /* the retained runtime summary may expire between polls */ });
    return () => { disposed = true; };
  }, [busy, plane.baseUrl, plane.overview, readReplayJob, replayJob]);

  useEffect(() => {
    const currentJob = replayJob;
    const replayId = currentJob?.replay_id;
    if (!currentJob || !replayId || !replayActive || !plane.baseUrl) return undefined;
    let disposed = false;
    let timer = 0;
    let consecutiveErrors = 0;
    const poll = async () => {
      try {
        const next = await readReplayJob(currentJob);
        if (disposed) return;
        consecutiveErrors = 0;
        setReplayJob(next);
        const outcomeError = next.ok === false || ['failed', 'stopped', 'canceled'].includes(next.status)
          ? next.error || String(next.result?.error || '重放未完成。')
          : '';
        setError(outcomeError);
        if (!['done', 'failed', 'stopped', 'canceled'].includes(next.status)) {
          timer = window.setTimeout(poll, 500);
        } else {
          await refresh();
          if (!disposed && outcomeError) setError(outcomeError);
        }
      } catch (pollError) {
        if (!disposed) {
          consecutiveErrors += 1;
          const message = pollError instanceof Error ? pollError.message : String(pollError);
          const missing = replayPollErrorStatus(pollError) === 404;
          if (missing || consecutiveErrors >= MAX_REPLAY_POLL_ERRORS) {
            const terminalError = missing
              ? '运行任务状态已不存在，可以重新运行。'
              : `连续 ${MAX_REPLAY_POLL_ERRORS} 次无法读取运行任务状态：${message}。可以重新运行。`;
            setReplayJob({
              ...currentJob,
              status: 'failed',
              ok: false,
              error: terminalError,
            });
            setError(terminalError);
            return;
          }
          setError(`暂时无法读取运行任务状态（${consecutiveErrors}/${MAX_REPLAY_POLL_ERRORS}）：${message}`);
          timer = window.setTimeout(poll, 1000);
        }
      }
    };
    timer = window.setTimeout(poll, 300);
    return () => { disposed = true; window.clearTimeout(timer); };
  }, [plane.baseUrl, readReplayJob, replayJob?.replay_id, replayActive, refresh]);

  const verifiedScripts = useMemo(
    () => scripts.filter((item) => item.valid && item.trust === 'verified'),
    [scripts],
  );
  const draftScripts = useMemo(
    () => scripts.filter((item) => !item.valid || item.trust !== 'verified'),
    [scripts],
  );

  const openSkill = async (file: string) => {
    setBusy(true);
    try {
      const asset = await plane.getClient().script<WpsScript | AutomationIR>(file);
      if (!confirmDocumentReplacement()) return;
      loadScript(
        asset.format === 'automation-ir/1'
          ? automationIrToFlowScript(asset as AutomationIR)
          : wpsToFlowScript(asset as WpsScript),
      );
      openStudio();
    } catch (skillError) { setError(skillError instanceof Error ? skillError.message : String(skillError)); }
    finally { setBusy(false); }
  };

  const replay = async (file: string) => {
    setBusy(true); setError('');
    setReplayJob(null);
    try {
      const client = plane.getClient();
      const asset = await client.script<WpsScript | AutomationIR>(file);
      if (
        asset.format === 'automation-ir/1'
        && automationRuntimeKind(asset as AutomationIR) === 'arp_graph'
      ) {
        await plane.startAutomation(asset as AutomationIR);
      } else {
        const started = await client.replay(file) as { replay_id?: string; job?: Omit<ReplayJobView, 'kind'> };
        if (!started.replay_id || !started.job) throw new Error('本机运行时没有返回重放任务。');
        setReplayJob({ ...started.job, kind: 'replay' });
      }
      await plane.refresh();
    }
    catch (replayError) { setError(replayError instanceof Error ? replayError.message : String(replayError)); }
    finally { setBusy(false); }
  };

  const cancelReplay = async () => {
    if (!replayJob?.replay_id) return;
    setBusy(true); setError('');
    try {
      await plane.getClient().cancelRuntimeJob(replayJob.replay_id);
      setReplayJob(await readReplayJob(replayJob));
    } catch (cancelError) {
      setError(cancelError instanceof Error ? cancelError.message : String(cancelError));
    } finally { setBusy(false); }
  };

  const exportTrace = async (runId: string) => {
    setBusy(true);
    try { await plane.getClient().exportTrace(runId); await refresh(); }
    catch (exportError) { setError(exportError instanceof Error ? exportError.message : String(exportError)); }
    finally { setBusy(false); }
  };

  const toggleRecording = async () => {
    if (!recording && !recordingTask.trim()) {
      setError('请输入本次宏录制要完成的任务。');
      return;
    }
    if (!recording && !evidenceValue.trim()) {
      setError('请填写可机器检查的终态证据。');
      return;
    }
    setBusy(true);
    try {
      if (recording) {
        const result = await plane.getClient().apprenticeStop();
        setRecordingName('');
        await refresh();
        if (result.file) await openSkill(String(result.file));
        return;
      }
      await plane.getClient().apprenticeStart(
        recordingTask.trim(),
        '',
        [{ predicate: evidencePredicate, value: evidenceValue.trim() }],
        recordingName.trim(),
      );
      await refresh();
    } catch (recordError) {
      try { await refresh(); } catch { /* Keep the original recording error visible. */ }
      if (recording) setRecordingName('');
      setError(recordError instanceof Error ? recordError.message : String(recordError));
    }
    finally { setBusy(false); }
  };

  const verifyMemory = async (item: MemoryItem) => {
    setBusy(true); setError('');
    try {
      if (!item.verification?.length) throw new Error('请先为候选技能保存结构化终态证据。');
      const client = plane.getClient();
      const started = await client.startMemoryVerification(item.id);
      setReplayJob({
        ...started.job,
        kind: 'memory_verification',
        memory_id: item.id,
      });
      await plane.refresh();
    } catch (verifyError) { setError(verifyError instanceof Error ? verifyError.message : String(verifyError)); }
    finally { setBusy(false); }
  };

  const inspectMemory = async (item: MemoryItem) => {
    setBusy(true); setError('');
    try {
      if (selectedMemoryId === item.id) {
        setSelectedMemoryId(''); setMemoryDetail(null); return;
      }
      const detail = await plane.getClient().memoryLineage(item.id);
      setSelectedMemoryId(item.id);
      setMemoryDetail(detail);
      const requirement = item.verification?.[0] || {};
      setMemoryEvidencePredicate(String(requirement.predicate || 'visible.text'));
      setMemoryEvidenceValue(String(requirement.value || ''));
    } catch (detailError) { setError(detailError instanceof Error ? detailError.message : String(detailError)); }
    finally { setBusy(false); }
  };

  const saveMemoryEvidence = async (item: MemoryItem) => {
    if (!memoryEvidenceValue.trim()) { setError('请填写证据值。'); return; }
    setBusy(true); setError('');
    try {
      await plane.getClient().updateMemory(item.id, {
        verification: [{ predicate: memoryEvidencePredicate, value: memoryEvidenceValue.trim() }],
      });
      await refresh();
      setMemoryDetail(await plane.getClient().memoryLineage(item.id));
    } catch (saveError) { setError(saveError instanceof Error ? saveError.message : String(saveError)); }
    finally { setBusy(false); }
  };

  const retireMemory = async (item: MemoryItem) => {
    setBusy(true); setError('');
    try {
      await plane.getClient().retireMemory(item.id);
      if (selectedMemoryId === item.id) { setSelectedMemoryId(''); setMemoryDetail(null); }
      await refresh();
    } catch (retireError) { setError(retireError instanceof Error ? retireError.message : String(retireError)); }
    finally { setBusy(false); }
  };

  const clearSessions = async () => {
    setBusy(true); setError('');
    try { await plane.getClient().clearSessions(); await refresh(); }
    catch (clearError) { setError(clearError instanceof Error ? clearError.message : String(clearError)); }
    finally { setBusy(false); }
  };

  return (
    <div className="arp-page arp-assets-page">
      <div className="arp-page-title"><div><span className="arp-kicker">离线自动化</span><h2>流程、技能与宏录制</h2></div></div>
      {error && <div className="arp-inline-error"><XCircle size={14} />{error}</div>}
      <div className="arp-replay-toolbar">
        <div className="arp-replay-modes" role="tablist" aria-label="重放模式">
          <button className={replayMode === 'offline_graph' ? 'active' : ''} onClick={() => setReplayMode('offline_graph')} role="tab" aria-selected={replayMode === 'offline_graph'} disabled={recording}><Workflow size={14} />离线流程</button>
          <button className={replayMode === 'skill' ? 'active' : ''} onClick={() => setReplayMode('skill')} role="tab" aria-selected={replayMode === 'skill'} disabled={recording}><RefreshCw size={14} />技能复用</button>
          <button className={replayMode === 'macro' ? 'active' : ''} onClick={() => setReplayMode('macro')} role="tab" aria-selected={replayMode === 'macro'}><Radio size={14} />宏录制</button>
        </div>
        {replayMode === 'macro' && (
          <div className="arp-apprentice-control">
            <input value={recordingName} onChange={(event) => setRecordingName(event.target.value)} placeholder="宏名称（可选）" disabled={recording || busy} aria-label="宏名称" />
            <input value={recordingTask} onChange={(event) => setRecordingTask(event.target.value)} placeholder="描述本次录制要完成的任务" disabled={recording || busy} aria-label="宏录制任务" />
            <select value={evidencePredicate} onChange={(event) => setEvidencePredicate(event.target.value)} disabled={recording || busy} aria-label="终态证据类型">
              <option value="visible.text">可见文本</option>
              <option value="window.visible">可见窗口</option>
              <option value="file.exists">文件存在</option>
              <option value="process.running">进程运行</option>
            </select>
            <input value={evidenceValue} onChange={(event) => setEvidenceValue(event.target.value)} placeholder="终态证据值" disabled={recording || busy} aria-label="终态证据值" />
            <button className={`arp-button ${recording ? 'arp-button-danger' : 'arp-button-primary'}`} onClick={toggleRecording} disabled={busy || (!recording && (!recordingTask.trim() || !evidenceValue.trim()))}>{recording ? <CircleStop size={14} /> : <Radio size={14} />}{recording ? '停止录制并生成流程' : '开始宏录制'}</button>
          </div>
        )}
      </div>
      {replayJob && (
        <div className={`arp-replay-job ${replayStatus}`} role="status">
          <span><strong>{STATUS_LABELS[replayStatus] || replayStatus}</strong><small>{replayJob.script || replayJob.replay_id}{Array.isArray(replayJob.result?.results) ? ` · ${replayJob.result.results.length} 步` : ''}</small></span>
          {replayActive && <button className="arp-button arp-button-danger arp-button-small" onClick={() => void cancelReplay()} disabled={busy}><CircleStop size={13} />停止</button>}
        </div>
      )}
      <div className="arp-assets-grid">
        <section className="arp-panel">
          <div className="arp-panel-heading"><div><ShieldCheck size={15} /><strong>已验证技能</strong></div><span>{verifiedScripts.length}</span></div>
          <div className="arp-asset-list">
            {verifiedScripts.map((item) => (
              <div className="arp-asset-row" key={item.file}>
                <div className="arp-asset-icon"><FileCode2 size={16} /></div>
                <div><strong>{item.name || item.file}</strong><span>{item.steps} 步 · {item.task || '录制的工作流'} · {item.verification_state || 'verified'}</span></div>
                <div className="arp-row-actions">
                  <button className="arp-icon-button" title="在流程工作台中打开" onClick={() => openSkill(item.file)} disabled={busy}><Braces size={15} /></button>
                  {replayMode === 'skill' ? <button className="arp-button arp-button-small" onClick={() => replay(item.file)} disabled={busy || replayActive}><Play size={13} />复用</button> : <button className="arp-button arp-button-small" onClick={() => openSkill(item.file)} disabled={busy}><Workflow size={13} />打开</button>}
                </div>
              </div>
            ))}
            {!verifiedScripts.length && <EmptyState icon={ShieldCheck} title="暂无已验证技能" />}
          </div>
        </section>
        <section className="arp-panel">
          <div className="arp-panel-heading"><div><FileCode2 size={15} /><strong>草稿与待处置脚本</strong></div><span>{draftScripts.length}</span></div>
          <div className="arp-asset-list">
            {draftScripts.map((item) => (
              <div className="arp-asset-row" key={item.file} title={item.validation_error || ''}>
                <div className="arp-asset-icon"><FileCode2 size={16} /></div>
                <div><strong>{item.name || item.file}</strong><span>{item.valid ? '待验证' : '格式无效'} · {item.steps} 步 · {item.task || item.file}</span></div>
                <div className="arp-row-actions">
                  <button className="arp-button arp-button-small" onClick={() => openSkill(item.file)} disabled={busy || !item.valid}><Workflow size={13} />打开</button>
                </div>
              </div>
            ))}
            {!draftScripts.length && <EmptyState icon={FileCode2} title="暂无草稿脚本" />}
          </div>
        </section>
        <section className="arp-panel">
          <div className="arp-panel-heading"><div><Database size={15} /><strong>探索轨迹</strong></div><span>{traces.length}</span></div>
          <div className="arp-asset-list">
            {traces.map((item) => (
              <div className="arp-asset-row" key={item.run_id}>
                <div className="arp-asset-icon"><TerminalSquare size={16} /></div>
                <div><strong>{item.task || item.run_id}</strong><span>{item.steps} 步 · {STATUS_LABELS[item.status] || item.status}</span></div>
                <div className="arp-row-actions"><button className="arp-button arp-button-small" onClick={() => exportTrace(item.run_id)} disabled={busy}><Save size={13} />提取草稿</button></div>
              </div>
            ))}
            {!traces.length && <EmptyState icon={Database} title="暂无探索轨迹" />}
          </div>
        </section>
        <section className="arp-panel">
          <div className="arp-panel-heading">
            <div><BrainCircuit size={15} /><strong>{memoryView === 'long' ? '长期记忆' : '会话记忆'}</strong></div>
            <div className="arp-memory-tabs">
              <button className={memoryView === 'long' ? 'active' : ''} onClick={() => setMemoryView('long')}>长期 {memories.length}</button>
              <button className={memoryView === 'session' ? 'active' : ''} onClick={() => setMemoryView('session')}>会话 {sessions.length}</button>
              {memoryView === 'session' && <button className="arp-icon-button" title="清空会话记忆" onClick={clearSessions} disabled={busy}><X size={13} /></button>}
            </div>
          </div>
          {memoryView === 'long' && <div className="arp-detail-note"><Info size={13} />这些长期记忆属于 Studio 回放与离线流程；当前 vNext 智能代理主链未启用长期经验记忆（experience_policy=disabled），不会读取或写入此项。</div>}
          <div className="arp-asset-list">
            {memoryView === 'long' && memories.map((item) => (
              <React.Fragment key={item.id}>
                <div className="arp-asset-row">
                  <div className="arp-asset-icon"><BrainCircuit size={16} /></div>
                  <div>
                    <strong>{item.task || item.id}</strong>
                    <span>{STATUS_LABELS[item.status || (item.provisional ? 'provisional' : 'active')] || item.status} · 成功 {item.successes || 0} / 使用 {item.uses || 0} 次 · 置信度 {Math.round((item.confidence || 0) * 100)}%{item.app ? ` · ${item.app}` : ''}</span>
                  </div>
                  <div className="arp-row-actions">
                    {item.script && <button className="arp-icon-button" title="打开关联脚本" onClick={() => openSkill(item.script!)} disabled={busy}><Braces size={15} /></button>}
                    <button className="arp-icon-button" title="查看适用条件与证据" onClick={() => inspectMemory(item)} disabled={busy}><Eye size={15} /></button>
                    {item.script && item.provisional && item.verification?.length ? <button className="arp-button arp-button-small" onClick={() => verifyMemory(item)} disabled={busy || replayActive}><Check size={13} />回放验证</button> : null}
                    {item.script && !item.provisional && item.script_sha256 ? <button className="arp-button arp-button-small" onClick={() => replay(item.script!)} disabled={busy || replayActive}><Play size={13} />复用</button> : null}
                    <button className="arp-icon-button" title="停用记忆" onClick={() => retireMemory(item)} disabled={busy}><Archive size={14} /></button>
                  </div>
                </div>
                {selectedMemoryId === item.id && memoryDetail && (
                  <div className="arp-memory-detail">
                    <div><strong>适用条件</strong><span>{(memoryDetail.node?.conditions || item.conditions || []).join(' · ') || '未声明'}</span></div>
                    <div><strong>证据要求</strong><span>{(memoryDetail.node?.evidence || item.evidence || []).join(' · ') || '未声明'}</span></div>
                    <div><strong>结构化条件</strong><code>{JSON.stringify(memoryDetail.node?.payload?.applicability || item.applicability || {})}</code></div>
                    <div><strong>验证记录</strong><span>{memoryDetail.attempts?.length || 0} 次 · 关联 {memoryDetail.related?.length || 0} 条</span></div>
                    {item.script && item.provisional && (
                      <div className="arp-evidence-editor">
                        <select value={memoryEvidencePredicate} onChange={(event) => setMemoryEvidencePredicate(event.target.value)} disabled={busy} aria-label="记忆证据类型">
                          <option value="visible.text">可见文本</option><option value="window.visible">可见窗口</option><option value="file.exists">文件存在</option><option value="process.running">进程运行</option>
                        </select>
                        <input value={memoryEvidenceValue} onChange={(event) => setMemoryEvidenceValue(event.target.value)} placeholder="证据值" disabled={busy} />
                        <button className="arp-button arp-button-small" onClick={() => saveMemoryEvidence(item)} disabled={busy || !memoryEvidenceValue.trim()}><Save size={13} />保存证据</button>
                      </div>
                    )}
                  </div>
                )}
              </React.Fragment>
            ))}
            {memoryView === 'long' && !memories.length && <EmptyState icon={BrainCircuit} title="暂无长期记忆" />}
            {memoryView === 'session' && sessions.map((item) => (
              <div className="arp-asset-row" key={`${item.run_id}-${item.task}`}>
                <div className="arp-asset-icon"><Clock3 size={16} /></div>
                <div><strong>{item.task}</strong><span>{STATUS_LABELS[item.status] || item.status} · {item.result || item.run_id}</span></div>
              </div>
            ))}
            {memoryView === 'session' && !sessions.length && <EmptyState icon={Clock3} title="暂无会话记忆" />}
          </div>
        </section>
      </div>
    </div>
  );
}

function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (value: boolean) => void; label: string }) {
  return <label className="arp-toggle-row"><span>{label}</span><button type="button" className={`arp-toggle ${checked ? 'on' : ''}`} onClick={() => onChange(!checked)} aria-pressed={checked}><span /></button></label>;
}

function DayNightToggle({ isNight, onToggle }: { isNight: boolean; onToggle: () => void }) {
  return (
    <div className="ld-theme-toggle-control">
      <button
        className="ld-dnt-toggle"
        type="button"
        role="switch"
        data-state={isNight ? 'night' : 'day'}
        aria-checked={isNight}
        aria-label="切换日间与夜间模式"
        title="切换界面主题"
        onClick={onToggle}
        style={{ '--dnt-scale': 0.48, width: 177, height: 69.6 } as React.CSSProperties}
      >
        <span className="ld-dnt-scene" aria-hidden="true">
          <span className="ld-dnt-track">
            <span className="ld-dnt-sky ld-dnt-sky-day" />
            <span className="ld-dnt-sky ld-dnt-sky-night" />
            <span className="ld-dnt-rays">
              <span className="ld-dnt-ray ld-dnt-ray-inner" />
              <span className="ld-dnt-ray ld-dnt-ray-middle" />
              <span className="ld-dnt-ray ld-dnt-ray-outer" />
            </span>
            <span className="ld-dnt-stars">
              {Array.from({ length: 11 }, (_, index) => <span className="ld-dnt-star" key={`star-${index}`} />)}
            </span>
            <span className="ld-dnt-cloud-layer ld-dnt-cloud-shadow">
              {Array.from({ length: 7 }, (_, index) => <span className="ld-dnt-cloud" key={`shadow-cloud-${index}`} />)}
            </span>
            <span className="ld-dnt-cloud-layer ld-dnt-cloud-front">
              {Array.from({ length: 7 }, (_, index) => <span className="ld-dnt-cloud" key={`front-cloud-${index}`} />)}
            </span>
            <span className="ld-dnt-orb">
              <span className="ld-dnt-orb-face ld-dnt-sun" />
              <span className="ld-dnt-orb-face ld-dnt-moon">
                <span className="ld-dnt-crater ld-dnt-crater-one" />
                <span className="ld-dnt-crater ld-dnt-crater-two" />
                <span className="ld-dnt-crater ld-dnt-crater-three" />
              </span>
            </span>
            <span className="ld-dnt-rim" />
          </span>
        </span>
      </button>
    </div>
  );
}

function SystemView({ plane }: { plane: Plane }) {
  const themeMode = useStore((state) => state.ui.themeMode);
  const darkMode = useStore((state) => state.ui.dark_mode);
  const setUI = useStore((state) => state.setUI);
  const [config, setConfig] = useState<Record<string, any> | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');
  const [chatgptBridge, setChatgptBridge] = useState<ChatGPTDesktopIntegration | null>(null);
  const [schedules, setSchedules] = useState<AgentSchedule[]>([]);
  const [scheduleTask, setScheduleTask] = useState('');
  const [scheduleDelayMinutes, setScheduleDelayMinutes] = useState(10);
  const [scheduleIntervalMinutes, setScheduleIntervalMinutes] = useState(0);
  const [scheduleSaving, setScheduleSaving] = useState(false);
  const platformStatus = plane.overview?.status;
  const runtime = platformStatus?.runtime;
  const runtimeJobs = platformStatus?.runtime_jobs || [];
  const activeRuntimeJobs = runtimeJobs.filter((job) => ['queued', 'waiting_resources', 'running', 'canceling'].includes(job.status));
  const desktopQueue = platformStatus?.desktop_action_queue;

  useEffect(() => {
    if (!plane.baseUrl) return;
    const client = plane.getClient();
    Promise.all([client.config(), client.chatgptIntegration(), client.schedules()])
      .then(([nextConfig, integration, nextSchedules]) => {
        setConfig(nextConfig);
        setChatgptBridge(integration);
        setSchedules(nextSchedules);
      })
      .catch((loadError) => setError(String(loadError)));
  }, [plane.baseUrl]);

  const patch = (section: string, key: string, value: unknown) => setConfig((current) => current ? { ...current, [section]: { ...current[section], [key]: value } } : current);
  const save = async () => {
    if (!config) return;
    setSaving(true); setError('');
    try {
      const next = await plane.getClient().patchConfig({ api: config.api, vlm: config.vlm, perception: config.perception, fusion: config.fusion, controller: config.controller, agent: config.agent, ocr: config.ocr, integrations: config.integrations });
      setConfig(next);
      setChatgptBridge(await plane.getClient().chatgptIntegration());
      setSaved(true); setTimeout(() => setSaved(false), 1800);
    } catch (saveError) { setError(saveError instanceof Error ? saveError.message : String(saveError)); }
    finally { setSaving(false); }
  };

  const createSchedule = async () => {
    const task = scheduleTask.trim();
    if (!task) return;
    setScheduleSaving(true); setError('');
    try {
      const created = await plane.getClient().createSchedule({
        task,
        delay_seconds: Math.max(0, scheduleDelayMinutes) * 60,
        interval_seconds: scheduleIntervalMinutes > 0 ? scheduleIntervalMinutes * 60 : null,
      });
      setSchedules((items) => [...items, created]);
      setScheduleTask('');
    } catch (scheduleError) { setError(scheduleError instanceof Error ? scheduleError.message : String(scheduleError)); }
    finally { setScheduleSaving(false); }
  };

  const toggleSchedule = async (schedule: AgentSchedule) => {
    setError('');
    try {
      const updated = await plane.getClient().updateSchedule(schedule.id, { enabled: !schedule.enabled });
      setSchedules((items) => items.map((item) => item.id === updated.id ? updated : item));
    } catch (scheduleError) { setError(scheduleError instanceof Error ? scheduleError.message : String(scheduleError)); }
  };

  const cancelSchedule = async (schedule: AgentSchedule) => {
    setError('');
    try {
      await plane.getClient().cancelSchedule(schedule.id);
      setSchedules((items) => items.map((item) => item.id === schedule.id ? { ...item, enabled: false, running: false } : item));
    } catch (scheduleError) { setError(scheduleError instanceof Error ? scheduleError.message : String(scheduleError)); }
  };

  return (
    <div className="arp-page arp-system-page">
      <div className="arp-page-title"><div><span className="arp-kicker">控制平面</span><h2>系统设置</h2></div><button className="arp-button arp-button-primary" onClick={save} disabled={!config || saving}>{saving ? <Loader2 size={14} className="arp-spin" /> : saved ? <Check size={14} /> : <Save size={14} />}{saved ? '已保存' : '保存配置'}</button></div>
      {error && <div className="arp-inline-error"><XCircle size={14} />{error}</div>}
      <div className="arp-system-grid">
        <section className="arp-panel arp-config-panel">
          <div className="arp-panel-heading"><div><Bot size={15} /><strong>模型</strong></div></div>
          {config ? (
            <div className="arp-form-grid">
              <label><span>服务商</span><input value={config.api?.provider || ''} onChange={(event) => patch('api', 'provider', event.target.value)} /></label>
              <label><span>主模型</span><input value={config.api?.model || ''} onChange={(event) => patch('api', 'model', event.target.value)} /></label>
              <label className="wide"><span>API 基础地址</span><input value={config.api?.base_url || ''} onChange={(event) => patch('api', 'base_url', event.target.value)} /></label>
              <label className="wide"><span>API 密钥</span><input type="password" value={config.api?.api_key || ''} onChange={(event) => patch('api', 'api_key', event.target.value)} /></label>
              <label><span>视觉模型</span><input value={config.vlm?.model || ''} onChange={(event) => patch('vlm', 'model', event.target.value)} /></label>
              <label><span>视觉模型地址</span><input value={config.vlm?.base_url || ''} onChange={(event) => patch('vlm', 'base_url', event.target.value)} /></label>
              <Toggle checked={Boolean(config.vlm?.enabled)} onChange={(value) => patch('vlm', 'enabled', value)} label="启用强化视觉回退" />
            </div>
          ) : <EmptyState icon={Loader2} title="正在加载配置" />}
        </section>
        <section className="arp-panel arp-config-panel">
          <div className="arp-panel-heading"><div><Eye size={15} /><strong>感知层级</strong></div></div>
          {config && <div className="arp-toggle-list">
            {['uia', 'win32', 'ocr', 'cv', 'vlm'].map((dimension, index) => (
              <div className="arp-perception-row" key={dimension}><span className="arp-level">L{index}</span><Toggle label={dimension.toUpperCase()} checked={Boolean(config.perception?.[dimension]?.enabled)} onChange={(value) => setConfig((current) => current ? { ...current, perception: { ...current.perception, [dimension]: { ...current.perception?.[dimension], enabled: value } } } : current)} /><code>P{config.perception?.[dimension]?.priority || index + 1}</code></div>
            ))}
            <Toggle checked={Boolean(config.fusion?.screen_watch_autostart)} onChange={(value) => patch('fusion', 'screen_watch_autostart', value)} label="自动启动屏幕监视" />
            <Toggle checked={Boolean(config.controller?.native_visual_thinking)} onChange={(value) => patch('controller', 'native_visual_thinking', value)} label="原生视觉推理（更慢）" />
            <Toggle checked={Boolean(config.fusion?.ocr_skip_when_uia_rich)} onChange={(value) => patch('fusion', 'ocr_skip_when_uia_rich', value)} label="UIA 信息丰富时跳过 OCR" />
            <Toggle checked={Boolean(config.fusion?.vlm_skip_when_uia_rich)} onChange={(value) => patch('fusion', 'vlm_skip_when_uia_rich', value)} label="UIA 信息丰富时跳过视觉模型" />
          </div>}
        </section>
        <section className="arp-panel arp-config-panel">
          <div className="arp-panel-heading"><div><Gauge size={15} /><strong>运行时</strong></div><StatusPill status={plane.connection === 'online' ? 'running' : 'failed'} label={plane.connection === 'online' ? '在线' : '离线'} /></div>
          <dl className="arp-diagnostics">
            <div><dt>控制平面</dt><dd>{plane.baseUrl || '未连接'}</dd></div>
            <div><dt>API 协议</dt><dd>{plane.overview?.contracts?.arp || '-'}</dd></div>
            <div><dt>能力数量</dt><dd>{plane.capabilities.length}</dd></div>
            <div><dt>当前任务</dt><dd>{plane.currentRun?.run_id || '空闲'}</dd></div>
            <div><dt>加载提交</dt><dd>{runtime?.git.loaded_commit || '-'}</dd></div>
            <div><dt>工作提交</dt><dd>{runtime?.git.current_commit || '-'}</dd></div>
            <div><dt>运行时协议</dt><dd>{runtime?.contract_version ?? '-'}</dd></div>
            <div><dt>源码状态</dt><dd>{plane.runtimeNotice || '加载版本与当前版本一致'}</dd></div>
            <div><dt>主模型视觉能力</dt><dd>{platformStatus?.model_profile ? (platformStatus.model_profile.supports_vision ? '支持' : '不支持') : '-'}</dd></div>
            <div><dt>世界模型</dt><dd>{config ? (config.agent?.world_model !== false ? '启用' : '停用') : '-'}</dd></div>
            <div><dt>视觉模型状态</dt><dd>{platformStatus ? (platformStatus.vlm_configured ? '已配置' : platformStatus.vlm_reason || '未配置') : '-'}</dd></div>
          </dl>
          <div className="arp-theme-control ld-theme-showcase">
            <div className="ld-theme-copy">
              <span>界面主题</span>
              <button type="button" className={themeMode === 'system' ? 'active' : ''} onClick={() => setUI({ themeMode: 'system' })}>
                {themeMode === 'system' ? '正在跟随系统' : '跟随系统'}
              </button>
            </div>
            <DayNightToggle isNight={darkMode} onToggle={() => setUI({ themeMode: darkMode ? 'light' : 'dark' })} />
          </div>
        </section>
        <section className="arp-panel arp-config-panel arp-runtime-health-panel">
          <div className="arp-panel-heading"><div><Activity size={15} /><strong>执行运行时</strong></div><StatusPill status={activeRuntimeJobs.length ? 'running' : 'idle'} label={activeRuntimeJobs.length ? `${activeRuntimeJobs.length} 个活动任务` : '空闲'} /></div>
          <div className="arp-runtime-health-grid">
            <div><span>Runtime jobs</span><strong>{activeRuntimeJobs.length}/{runtimeJobs.length}</strong><small>活动 / 最近摘要</small></div>
            <div><span>桌面动作队列</span><strong>{desktopQueue?.completed ?? 0}/{desktopQueue?.submitted ?? 0}</strong><small>{desktopQueue?.failed ?? 0} 失败 · {desktopQueue?.resource_waiters ?? 0} 等待资源</small></div>
            <div><span>异步桌面动作</span><strong>{desktopQueue?.async_status?.running ?? 0}</strong><small>{(desktopQueue?.async_status?.queued ?? 0) + (desktopQueue?.async_status?.waiting_resources ?? 0)} 排队</small></div>
          </div>
          <div className="arp-runtime-job-list">
            {runtimeJobs.slice(0, 8).map((job) => {
              const title = displayValue(job.metadata.task || job.metadata.graph_name || job.metadata.script || job.kind);
              return (
                <div className="arp-runtime-job-row" key={job.job_id}>
                  <div><strong>{title}</strong><code>{job.job_id}</code></div>
                  <span>{job.kind} · {stringList(job.resources).join(', ') || '无独占资源'} · {formatElapsed(job.started_at || job.created_at, job.finished_at)}</span>
                  <StatusPill status={job.status} />
                  {job.error && <small>{job.error}</small>}
                </div>
              );
            })}
            {!runtimeJobs.length && <EmptyState icon={Activity} title="暂无运行时任务" detail="任务启动后会在这里显示队列、资源和失败原因" />}
          </div>
        </section>
        <section className="arp-panel arp-config-panel arp-schedule-panel">
          <div className="arp-panel-heading"><div><Clock3 size={15} /><strong>代理计划</strong></div><span>{schedules.filter((item) => item.enabled).length} 个启用</span></div>
          <div className="arp-schedule-create">
            <label className="wide"><span>任务</span><input value={scheduleTask} onChange={(event) => setScheduleTask(event.target.value)} placeholder="输入已验证的代理任务" /></label>
            <label><span>延迟（分钟）</span><input type="number" min="0" value={scheduleDelayMinutes} onChange={(event) => setScheduleDelayMinutes(Number(event.target.value) || 0)} /></label>
            <label><span>重复间隔（分钟，0 = 仅一次）</span><input type="number" min="0" value={scheduleIntervalMinutes} onChange={(event) => setScheduleIntervalMinutes(Number(event.target.value) || 0)} /></label>
            <button className="arp-button arp-button-primary" onClick={createSchedule} disabled={!scheduleTask.trim() || scheduleSaving}>{scheduleSaving ? <Loader2 size={14} className="arp-spin" /> : <Clock3 size={14} />}创建计划</button>
          </div>
          <div className="arp-schedule-list">
            {schedules.length === 0 && <EmptyState icon={Clock3} title="暂无计划任务" />}
            {schedules.map((schedule) => (
              <div className="arp-schedule-row" key={schedule.id}>
                <div><strong>{schedule.name}</strong><span>{schedule.next_run_at ? new Date(schedule.next_run_at).toLocaleString() : STATUS_LABELS[schedule.last_status || 'inactive'] || schedule.last_status}</span></div>
                <StatusPill status={schedule.running ? 'running' : schedule.enabled ? 'queued' : 'canceled'} label={schedule.running ? '运行中' : schedule.enabled ? '已启用' : '已停用'} />
                <button className={`arp-toggle ${schedule.enabled ? 'on' : ''}`} onClick={() => toggleSchedule(schedule)} aria-label={`${schedule.enabled ? '停用' : '启用'} ${schedule.name}`} aria-pressed={schedule.enabled}><span /></button>
                <button className="arp-icon-button" onClick={() => cancelSchedule(schedule)} aria-label={`取消 ${schedule.name}`} title="取消计划"><X size={14} /></button>
              </div>
            ))}
          </div>
        </section>
        <section className="arp-panel arp-config-panel">
          <div className="arp-panel-heading"><div><Network size={15} /><strong>ChatGPT 桌面连接</strong></div><StatusPill status={chatgptBridge?.client_active ? 'running' : chatgptBridge?.server_ready ? 'idle' : 'failed'} label={chatgptBridge?.client_active ? 'MCP 已连接' : chatgptBridge?.server_ready ? '端点已就绪' : '离线'} /></div>
          <dl className="arp-diagnostics">
            <div><dt>规划器</dt><dd>{chatgptBridge?.planner || '当前桌面客户端'}</dd></div>
            <div><dt>客户端活动</dt><dd>{chatgptBridge?.client_active ? `${chatgptBridge.request_count} 次请求` : '近期无客户端请求'}</dd></div>
            <div><dt>传输方式</dt><dd>{chatgptBridge?.transport || 'mcp_streamable_http'}</dd></div>
            <div><dt>端点</dt><dd title={chatgptBridge?.endpoint}>{chatgptBridge?.endpoint || '-'}</dd></div>
            <div><dt>安全模式</dt><dd>{chatgptBridge?.security_mode || '仅本机回环'}</dd></div>
            <div><dt>OpenAI API 密钥</dt><dd>{chatgptBridge?.requires_openai_api_key ? '需要' : '未使用'}</dd></div>
            <div><dt>工具数量</dt><dd>{chatgptBridge?.tool_count ?? 0}</dd></div>
          </dl>
          {config && <div className="arp-toggle-list arp-bridge-toggles">
            <Toggle checked={Boolean(config.integrations?.chatgpt_desktop?.enabled)} onChange={(value) => setConfig((current) => current ? { ...current, integrations: { ...current.integrations, chatgpt_desktop: { ...current.integrations?.chatgpt_desktop, enabled: value } } } : current)} label="启用桌面 MCP 连接" />
            <Toggle checked={Boolean(config.integrations?.chatgpt_desktop?.allow_desktop_mutation)} onChange={(value) => setConfig((current) => current ? { ...current, integrations: { ...current.integrations, chatgpt_desktop: { ...current.integrations?.chatgpt_desktop, allow_desktop_mutation: value } } } : current)} label="允许控制器执行动作" />
          </div>}
        </section>
        <section className="arp-panel arp-capability-panel">
          <div className="arp-panel-heading"><div><Zap size={15} /><strong>能力注册表</strong></div><span>{plane.capabilities.length}</span></div>
          <div className="arp-capability-list">
            {plane.capabilities.map((capability) => (
              <div key={capability.id}><code>{capability.id}</code><span>{capability.category}</span><span>{capability.latency_class}</span>{capability.requires_llm ? <Bot size={13} /> : <Zap size={13} />}</div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

type ResourceView = 'runs' | 'assets' | 'system';
type WorkbenchView = 'activity' | 'context' | 'flow';

function MiniRunWindow({
  plane,
  onExpand,
  onOpenWorkbench,
}: {
  plane: Plane;
  onExpand: () => void;
  onOpenWorkbench: () => void;
}) {
  const run = plane.currentRun;
  const tone = visualRunTone(run);
  const progress = runVisualProgress(run);

  if (!run) return null;

  return (
    <div className="ld-mini-stage" aria-label="迷你任务悬浮窗">
      <aside className={`ld-mini-run ld-floating-window ${tone}`}>
        <button className="ld-mini-main" type="button" onClick={onExpand} aria-label="返回完整任务窗口">
          <span className="ld-progress-ring" style={{ '--ld-progress': `${progress}%` } as React.CSSProperties}><i /></span>
          <span><strong>{runActivityLabel(run)}</strong><small>{run.goal}</small></span>
          <span>{formatElapsed(run.started_at, run.finished_at)}</span>
        </button>
        <button className="ld-mini-detail" type="button" onClick={onOpenWorkbench} aria-label="打开执行详情" title="打开执行详情">
          <Maximize2 size={13} />
        </button>
      </aside>
    </div>
  );
}

function ResourceDrawer({
  plane,
  view,
  onView,
  onClose,
  onOpenFlow,
}: {
  plane: Plane;
  view: ResourceView;
  onView: (view: ResourceView) => void;
  onClose: () => void;
  onOpenFlow: () => void;
}) {
  return (
    <>
      <button className="ld-layer-scrim" type="button" aria-label="关闭资料浮层" onClick={onClose} />
      <aside className="ld-resource-drawer ld-floating-window" aria-label="资料">
        <header className="ld-layer-header">
          <div><span>灵动脚本</span><strong>资料</strong></div>
          <button type="button" onClick={onClose} aria-label="关闭资料"><X size={17} /></button>
        </header>
        <nav className="ld-layer-tabs" aria-label="资料区域">
          <button type="button" className={view === 'runs' ? 'active' : ''} onClick={() => onView('runs')}><History size={14} /><span>运行记录</span></button>
          <button type="button" className={view === 'assets' ? 'active' : ''} onClick={() => onView('assets')}><Library size={14} /><span>技能与记忆</span></button>
          <button type="button" className={view === 'system' ? 'active' : ''} onClick={() => onView('system')}><Settings size={14} /><span>设置</span></button>
        </nav>
        <div className="ld-resource-body">
          {view === 'runs' && <RunsView plane={plane} />}
          {view === 'assets' && <AssetsView plane={plane} openStudio={onOpenFlow} />}
          {view === 'system' && <SystemView plane={plane} />}
        </div>
      </aside>
    </>
  );
}

function RuntimeWorkbench({
  plane,
  view,
  onView,
  onClose,
  onOpenFlow,
}: {
  plane: Plane;
  view: WorkbenchView;
  onView: (view: WorkbenchView) => void;
  onClose: () => void;
  onOpenFlow: () => void;
}) {
  const run = plane.currentRun;
  const arp = plane.arpSnapshot;
  const script = useStore((state) => state.script);
  const [controlling, setControlling] = useState(false);
  const [controlError, setControlError] = useState('');
  const metrics = run?.metrics;
  const latestScreenEvent = [...plane.events].reverse().find((event) => ['screen_watch', 'screen_delta', 'perception'].includes(event.type))
    || [...(run?.timeline || [])].reverse().find((event) => ['screen_watch', 'screen_delta', 'perception'].includes(event.type));
  const screenshot = [...(run?.evidence || [])].reverse().find((item) => item.type === 'screenshot' && item.path);
  const screenshotKind = String(screenshot?.kind || 'screen').replaceAll('_', ' ');
  const screenshotRegion = screenshot?.region as Record<string, unknown> | undefined;
  const screenshotUrl = screenshot && plane.baseUrl && run
    ? plane.getClient().traceScreenshotUrl(run.run_id, String(screenshot.path))
    : '';
  const active = Boolean(run && isActiveRunStatus(run.status));
  const manuallyPaused = Boolean(arp?.manual_paused);

  const control = async (action: 'pause' | 'resume' | 'stop') => {
    if (controlling) return;
    setControlling(true);
    setControlError('');
    try {
      if (action === 'pause') await plane.pauseCurrent();
      if (action === 'resume') await plane.resumeCurrent();
      if (action === 'stop') await plane.cancelCurrent();
    } catch (error) {
      setControlError(error instanceof Error ? error.message : String(error));
    } finally {
      setControlling(false);
    }
  };

  return (
    <>
      <button className="ld-layer-scrim" type="button" aria-label="关闭工作台浮层" onClick={onClose} />
      <aside className="ld-workbench-drawer ld-floating-window" aria-label="工作台">
        <header className="ld-layer-header">
          <div><span>灵动脚本</span><strong>工作台</strong></div>
          <button type="button" onClick={onClose} aria-label="关闭工作台"><X size={17} /></button>
        </header>
        <nav className="ld-layer-tabs" aria-label="工作台区域">
          <button type="button" className={view === 'activity' ? 'active' : ''} onClick={() => onView('activity')}><Activity size={14} /><span>执行</span></button>
          <button type="button" className={view === 'context' ? 'active' : ''} onClick={() => onView('context')}><Layers3 size={14} /><span>上下文与证据</span></button>
          <button type="button" className={view === 'flow' ? 'active' : ''} onClick={() => onView('flow')}><GitBranch size={14} /><span>流程</span></button>
        </nav>
        <div className="ld-workbench-body">
          {view === 'activity' && (
            <section className="ld-workbench-section">
              {run ? (
                <>
                  <div className="ld-workbench-task">
                    <div><StatusPill status={run.status} /><strong>{run.goal}</strong><small>{run.run_id}</small></div>
                    <span>{formatElapsed(run.started_at, run.finished_at)}</span>
                  </div>
                  <PhaseRail run={run} />
                  <dl className="ld-runtime-facts large">
                    <div><dt>阶段</dt><dd>{PHASE_LABELS[run.phase] || run.phase || '-'}</dd></div>
                    <div><dt>节点</dt><dd>{run.current_node_id || '-'}</dd></div>
                    <div><dt>工具</dt><dd>{TOOL_LABELS[run.current_tool || ''] || run.current_tool || '-'}</dd></div>
                    <div><dt>事件</dt><dd>{run.timeline.length || plane.events.length}</dd></div>
                  </dl>
                  {active && (
                    <div className="ld-runtime-controls">
                      {run.phase !== 'awaiting_human' && <button type="button" onClick={() => void control(manuallyPaused ? 'resume' : 'pause')} disabled={controlling}>{manuallyPaused ? <Play size={13} /> : <Pause size={13} />}{manuallyPaused ? '继续' : '暂停'}</button>}
                      <button type="button" className="danger" onClick={() => void control('stop')} disabled={controlling}>{controlling ? <Loader2 size={13} className="arp-spin" /> : <CircleStop size={13} />}停止</button>
                    </div>
                  )}
                  {controlError && <div className="ld-inline-alert" role="alert"><XCircle size={13} />{controlError}</div>}
                  <div className="ld-subsection-heading"><span><ListChecks size={14} />事件时间线</span><small>{run.timeline.length || plane.events.length} 条</small></div>
                  <RunTimeline run={run} events={plane.events} />
                  <details className="ld-disclosure">
                    <summary><Gauge size={14} /><span>性能与执行统计</span><ChevronRight size={13} /></summary>
                    <div className="arp-metrics-row compact">
                      <Metric label="截图" value={metrics?.screen_capture_latency_avg_ms || 0} unit="毫秒" />
                      <Metric label="感知" value={metrics?.perception_latency_avg_ms || 0} unit="毫秒" />
                      <Metric label="决策" value={metrics?.action_decision_latency_avg_ms || 0} unit="毫秒" />
                      <Metric label="执行" value={metrics?.action_execution_latency_avg_ms || 0} unit="毫秒" />
                      <Metric label="执行后验证" value={metrics?.post_action_verify_latency_avg_ms || 0} unit="毫秒" />
                      <Metric label="模型调用" value={metrics?.model_call_count || 0} />
                      <Metric label="阶段重试" value={metrics?.retry_count || 0} />
                      <Metric label="重规划率" value={`${Math.round((metrics?.replan_frequency || 0) * 100)}%`} />
                    </div>
                  </details>
                </>
              ) : <EmptyState icon={Activity} title="当前没有运行" detail="提交任务后，阶段、事件和控制会在这里出现。" />}
            </section>
          )}

          {view === 'context' && (
            <section className="ld-workbench-section">
              <div className="ld-subsection-heading"><span><Eye size={14} />当前画面</span>{latestScreenEvent && <small className="arp-live-pulse">实时</small>}</div>
              {screenshotUrl ? (
                <div className="arp-evidence-preview">
                  <img className="arp-evidence-image" src={screenshotUrl} alt="最近一次运行证据" />
                  <span className={screenshot?.transient ? 'transient' : ''}>
                    <ShieldCheck size={11} />{screenshotKind}
                    {screenshotRegion && <code>{String(screenshotRegion.left ?? 0)},{String(screenshotRegion.top ?? 0)} · {String(screenshotRegion.width ?? 0)}×{String(screenshotRegion.height ?? 0)}</code>}
                  </span>
                </div>
              ) : <div className="arp-screen-empty"><Eye size={22} /><span>等待视觉证据</span></div>}
              <dl className="arp-world-state">
                <div><dt>屏幕信号</dt><dd>{EVENT_LABELS[latestScreenEvent?.type || ''] || latestScreenEvent?.type || '就绪'}</dd></div>
                <div><dt>更新时间</dt><dd>{formatTime(latestScreenEvent?.ts)}</dd></div>
                <div><dt>证据数量</dt><dd>{run?.evidence.length || 0}</dd></div>
                <div><dt>完成检查</dt><dd>{metrics?.done_check_passed || 0}/{metrics?.done_checks || 0}</dd></div>
              </dl>
              <div className="ld-subsection-heading"><span><ShieldCheck size={14} />最近证据</span><small>{run?.evidence.length || 0} 条</small></div>
              <div className="ld-evidence-stack">
                {(run?.evidence || []).slice(-8).reverse().map((item, index) => (
                  <div className="arp-evidence-row" key={index}><CheckCircle2 size={14} /><span>{displayValue(item.reason || item.kind || item.type || item.path)}</span></div>
                ))}
                {!run?.evidence.length && <EmptyState icon={ShieldCheck} title="尚未收集证据" />}
              </div>
              {arp ? (
                <TaskContractRuntimeSection
                  arp={arp}
                  events={plane.events}
                  artifactUrl={(artifactId) => plane.getClient().artifactUrl(arp.run_id, artifactId)}
                />
              ) : <div className="arp-detail-note"><Archive size={14} />运行合同、工件和完成证据会在任务启动后出现。</div>}
            </section>
          )}

          {view === 'flow' && (
            <section className="ld-workbench-section ld-flow-preview-section">
              <div className="ld-flow-preview-copy"><span>隐藏式工作区</span><strong>流程编辑器只在需要时出现</strong><p>当前流程保留完整节点库、属性面板、拖拽、连线、验证与真实运行能力。</p></div>
              <button className="ld-flow-preview" type="button" onClick={onOpenFlow} aria-label="展开流程工作台">
                <div><Sparkles size={18} /><strong>{script.meta.name}</strong><span>{script.nodes.length} 个节点 · {script.edges.length} 条连线</span></div>
                <Maximize2 size={17} />
              </button>
              <button className="arp-button arp-button-primary" type="button" onClick={onOpenFlow}><GitBranch size={14} />展开完整编辑器</button>
            </section>
          )}
        </div>
      </aside>
    </>
  );
}

interface FloatingCommand {
  id: string;
  label: string;
  detail: string;
  icon: React.ElementType;
  action: () => void;
}

function CommandPalette({
  commands,
  query,
  onQuery,
  onClose,
}: {
  commands: FloatingCommand[];
  query: string;
  onQuery: (query: string) => void;
  onClose: () => void;
}) {
  const needle = query.trim().toLocaleLowerCase();
  const visible = commands.filter((command) => !needle || `${command.label} ${command.detail}`.toLocaleLowerCase().includes(needle));
  const runCommand = (command: FloatingCommand) => {
    onClose();
    command.action();
  };

  return (
    <div className="ld-command-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="ld-command-palette" role="dialog" aria-modal="true" aria-label="命令面板">
        <div className="ld-command-input">
          <Search size={18} />
          <input
            autoFocus
            value={query}
            onChange={(event) => onQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && visible[0]) runCommand(visible[0]);
              if (event.key === 'Escape') onClose();
            }}
            placeholder="搜索隐藏功能或命令…"
            aria-label="搜索命令"
          />
          <kbd>Esc</kbd>
        </div>
        <div className="ld-command-list">
          {visible.map((command, index) => {
            const Icon = command.icon;
            return (
              <button key={command.id} type="button" className={index === 0 ? 'active' : ''} onClick={() => runCommand(command)}>
                <span><Icon size={15} /></span>
                <span><strong>{command.label}</strong><small>{command.detail}</small></span>
                <ChevronRight size={14} />
              </button>
            );
          })}
          {!visible.length && <EmptyState icon={Search} title="没有匹配命令" detail="换一个更短的关键词。" />}
        </div>
        <footer><span><kbd>Enter</kbd> 打开</span><span><kbd>Esc</kbd> 关闭</span><span><kbd>Ctrl K</kbd> 随时呼出</span></footer>
      </section>
    </div>
  );
}

export function PlatformWorkbench({ onOpenAutomation }: { onOpenAutomation?: () => Promise<void> }) {
  const plane = useControlPlane();
  const desktopWindowApi = window.electronAPI;
  const storeMode = useStore((state) => state.ui.viewMode);
  const setViewMode = useStore((state) => state.setViewMode);
  const initialView = viewFromStore(storeMode);
  const [windowMode, setWindowMode] = useState<'expanded' | 'compact'>('expanded');
  const [resourceView, setResourceView] = useState<ResourceView | null>(
    initialView === 'runs' || initialView === 'assets' || initialView === 'system' ? initialView : null,
  );
  const [workbenchView, setWorkbenchView] = useState<WorkbenchView | null>(null);
  const [flowOpen, setFlowOpen] = useState(initialView === 'studio');
  const [commandOpen, setCommandOpen] = useState(false);
  const [commandQuery, setCommandQuery] = useState('');

  useEffect(() => {
    useStore.getState().fetchFeatures();
  }, []);

  useEffect(() => {
    if (!plane.currentRun && windowMode === 'compact') setWindowMode('expanded');
  }, [plane.currentRun, windowMode]);

  const openAgent = useCallback(() => {
    setViewMode('agent');
    setWindowMode('expanded');
    setResourceView(null);
    setWorkbenchView(null);
    setFlowOpen(false);
  }, [setViewMode]);

  const openResources = useCallback((view: ResourceView = 'runs') => {
    setViewMode(view);
    setResourceView(view);
    setWorkbenchView(null);
    setFlowOpen(false);
  }, [setViewMode]);

  const openWorkbench = useCallback((view: WorkbenchView = 'activity') => {
    setViewMode('agent');
    setWorkbenchView(view);
    setResourceView(null);
    setFlowOpen(false);
  }, [setViewMode]);

  const openFlow = useCallback(() => {
    setViewMode('studio');
    setFlowOpen(true);
    setResourceView(null);
    setWorkbenchView(null);
  }, [setViewMode]);

  const loadConversationFlow = useCallback((automation: AutomationIR) => {
    if (!confirmDocumentReplacement()) return;
    useStore.getState().loadScript(automationIrToFlowScript(automation), { dirty: true });
    openFlow();
  }, [openFlow]);

  const closeFlow = useCallback(() => {
    setFlowOpen(false);
    setViewMode('agent');
  }, [setViewMode]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase() === 'k') {
        event.preventDefault();
        setCommandOpen((value) => !value);
        setCommandQuery('');
        return;
      }
      if (event.key !== 'Escape') return;
      if (commandOpen) {
        setCommandOpen(false);
        setCommandQuery('');
      } else if (flowOpen) {
        closeFlow();
      } else if (resourceView) {
        setResourceView(null);
        setViewMode('agent');
      } else if (workbenchView) {
        setWorkbenchView(null);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [closeFlow, commandOpen, flowOpen, resourceView, setViewMode, workbenchView]);

  const closeCommand = () => {
    setCommandOpen(false);
    setCommandQuery('');
  };

  const commands: FloatingCommand[] = [
    { id: 'agent', label: '智能代理', detail: '新建任务或返回当前任务', icon: Bot, action: openAgent },
    { id: 'activity', label: '当前执行', detail: '阶段、事件、控制和性能统计', icon: Activity, action: () => openWorkbench('activity') },
    { id: 'context', label: '上下文与证据', detail: '当前画面、证据、工件与完成合同', icon: Layers3, action: () => openWorkbench('context') },
    { id: 'runs', label: '运行记录', detail: '搜索历史任务并查看完整运行详情', icon: History, action: () => openResources('runs') },
    { id: 'assets', label: '技能与记忆', detail: '已验证技能、轨迹和经验记忆', icon: Library, action: () => openResources('assets') },
    { id: 'flow', label: '流程工作台', detail: '打开完整可编辑流程图', icon: GitBranch, action: openFlow },
    { id: 'system', label: '系统设置', detail: '模型、运行时、计划、连接与能力注册表', icon: Settings, action: () => openResources('system') },
  ];

  return (
    <div className="arp-app ld-floating-app">
      <div className="ld-native-titlebar" aria-label="灵动脚本窗口标题栏">
        <button type="button" className="ld-title-brand" onClick={openAgent} aria-label="返回智能代理"><Bot size={13} /><strong>灵动脚本</strong></button>
        <button type="button" className="ld-title-status" onClick={() => openResources('system')} aria-label="查看运行时状态" title={plane.baseUrl || '运行时正在启动'}>
          <span className={`ld-status-dot ${plane.connection}`} />
          <span>{plane.connection === 'online' ? '在线' : plane.connection === 'degraded' ? '降级' : plane.connection === 'offline' ? '离线' : '连接中'}</span>
        </button>
        {desktopWindowApi?.minimizeWindow && (
          <div className="ld-window-controls" aria-label="窗口控制">
            <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => void desktopWindowApi.minimizeWindow?.()} aria-label="最小化窗口" title="最小化">
              <Minus size={14} />
            </button>
            <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => void desktopWindowApi.toggleMaximizeWindow?.()} aria-label="最大化或还原窗口" title="最大化或还原">
              <Square size={11} />
            </button>
            <button className="close" type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => void desktopWindowApi.closeWindow?.()} aria-label="关闭窗口" title="关闭">
              <X size={14} />
            </button>
          </div>
        )}
      </div>
      <main className="ld-floating-stage" aria-label="灵动脚本桌面悬浮工作区">
        {windowMode === 'compact' && plane.currentRun ? (
          <MiniRunWindow plane={plane} onExpand={() => setWindowMode('expanded')} onOpenWorkbench={() => openWorkbench('activity')} />
        ) : (
          <AgentCockpit
            plane={plane}
            onOpenResources={() => openResources('runs')}
            onOpenSystem={() => openResources('system')}
            onOpenWorkbench={() => openWorkbench(plane.currentRun ? 'activity' : 'flow')}
            onLoadConversationFlow={loadConversationFlow}
            onOpenCommand={() => { setCommandOpen(true); setCommandQuery(''); }}
            onCompact={() => { if (plane.currentRun) setWindowMode('compact'); }}
          />
        )}
      </main>

      {resourceView && (
        <ResourceDrawer
          plane={plane}
          view={resourceView}
          onView={(view) => { setResourceView(view); setViewMode(view); }}
          onClose={() => { setResourceView(null); setViewMode('agent'); }}
          onOpenFlow={openFlow}
        />
      )}

      {workbenchView && (
        <RuntimeWorkbench
          plane={plane}
          view={workbenchView}
          onView={setWorkbenchView}
          onClose={() => setWorkbenchView(null)}
          onOpenFlow={openFlow}
        />
      )}

      {flowOpen && (
        <>
          <button className="ld-layer-scrim flow" type="button" aria-label="关闭流程工作台浮层" onClick={closeFlow} />
          <section className="ld-flow-window ld-floating-window" aria-label="流程工作台">
            <header className="ld-flow-window-header">
              <div><span>流程工作台</span><strong>完整编辑、验证与运行</strong></div>
              <button type="button" onClick={closeFlow} aria-label="关闭流程工作台"><X size={17} /></button>
            </header>
            <div className="ld-flow-window-body"><Studio plane={plane} onOpenAutomation={onOpenAutomation} /></div>
          </section>
        </>
      )}

      {commandOpen && (
        <CommandPalette
          commands={commands}
          query={commandQuery}
          onQuery={setCommandQuery}
          onClose={closeCommand}
        />
      )}

      <button className="ld-command-orb" type="button" onClick={() => { setCommandOpen(true); setCommandQuery(''); }} aria-label="打开命令面板" title="Ctrl+K">
        <Command size={14} />
      </button>
    </div>
  );
}
