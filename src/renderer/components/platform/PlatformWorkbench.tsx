import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  Bot,
  BrainCircuit,
  Braces,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleStop,
  Clock3,
  Database,
  Eye,
  FileCode2,
  Gauge,
  GraduationCap,
  GitBranch,
  History,
  Library,
  ListChecks,
  Loader2,
  Moon,
  Network,
  PanelLeft,
  Play,
  Radio,
  RefreshCw,
  Save,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Sun,
  TerminalSquare,
  UserCheck,
  Workflow,
  X,
  XCircle,
  Zap,
} from 'lucide-react';
import { FlowCanvas } from '../Canvas/FlowCanvas';
import { NodeLibrary } from '../NodeLibrary/NodeLibrary';
import { PropertyPanel } from '../PropertyPanel/PropertyPanel';
import { useStore } from '../../store';
import type { UIState } from '../../types';
import { useControlPlane } from '../../platform/useControlPlane';
import type { AgentSchedule, ArpGraph, ChatGPTDesktopIntegration, GraphValidation, RunProjection, RuntimeEvent } from '../../platform/contracts';
import { wpsToFlowScript, type WpsScript } from '../../utils/wpsToFlowScript';
import {
  automationIrFromFlowScript,
  automationIrToFlowScript,
  automationRuntimeKind,
  automationRuntimeGraph,
  type AutomationIR,
} from '../../utils/automationIR';

type PlatformView = 'agent' | 'studio' | 'runs' | 'assets' | 'system';
type Plane = ReturnType<typeof useControlPlane>;

const NAV_ITEMS: Array<{ id: PlatformView; label: string; icon: React.ElementType }> = [
  { id: 'agent', label: '智能代理', icon: Bot },
  { id: 'studio', label: '流程工作台', icon: GitBranch },
  { id: 'runs', label: '运行记录', icon: History },
  { id: 'assets', label: '技能与学习', icon: Library },
  { id: 'system', label: '系统设置', icon: Settings },
];

const ACTIVE_STATUSES = new Set(['queued', 'running', 'paused']);
const PHASES = ['planning', 'deciding', 'perceiving', 'acting', 'verifying'];
const PHASE_LABELS: Record<string, string> = {
  planning: '规划', deciding: '决策', perceiving: '感知', acting: '执行', verifying: '验证',
};
const STATUS_LABELS: Record<string, string> = {
  queued: '排队中', running: '运行中', paused: '已暂停', done: '已完成', completed: '已完成',
  success: '成功', failed: '失败', error: '错误', canceled: '已取消', idle: '就绪',
  provisional: '待验证', active: '有效', verified: '已验证', rejected: '已拒绝', inactive: '未启用',
};
const RUN_KIND_LABELS: Record<string, string> = {
  agent: '智能代理', arp: '流程', replay: '重放', schedule: '计划任务', controller: '控制器', automation: '自动化',
};
const EVENT_LABELS: Record<string, string> = {
  agent_start: '代理启动', agent_step: '代理步骤', agent_done: '代理完成', agent_tool: '代理工具',
  agent_tool_start: '工具开始', agent_usage: '模型用量', agent_model_call: '模型调用',
  arp_run_start: '流程开始', arp_run_done: '流程完成', arp_node_start: '节点开始', arp_node_done: '节点完成',
  arp_child_run: '子任务', arp_evidence: '证据记录', screen_watch: '屏幕观察', screen_delta: '画面变化',
  visual_feedback: '视觉反馈', visual_preflight: '执行前画面检查', done_check: '完成检查', run_state: '运行状态',
};
const TOOL_LABELS: Record<string, string> = {
  click: '点击', draw_strokes: '连续绘制', type_text: '输入文字', hotkey: '快捷键', scroll: '滚动',
  drag: '拖拽', open_app: '打开应用', focus_window: '聚焦窗口', observe: '观察', done: '完成',
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

function displayValue(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'string') return value;
  try { return JSON.stringify(value); } catch { return String(value); }
}

function statusTone(status?: string): string {
  if (status === 'done' || status === 'completed' || status === 'success') return 'success';
  if (status === 'failed' || status === 'error' || status === 'canceled') return 'danger';
  if (status === 'paused') return 'warning';
  if (status === 'running' || status === 'queued') return 'active';
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
            <div><strong>{EVENT_LABELS[entry.type] || entry.type.replace(/^agent_|^arp_/, '').replaceAll('_', ' ')}</strong>{entry.node_id && <code>{entry.node_id}</code>}{entry.tool && <code>{TOOL_LABELS[entry.tool] || entry.tool}</code>}</div>
            <span>{displayValue(entry.summary)}</span>
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

function AgentCockpit({ plane }: { plane: Plane }) {
  const [goal, setGoal] = useState(() => localStorage.getItem('arp-last-goal') || '');
  const [maxSteps, setMaxSteps] = useState(40);
  const [submitting, setSubmitting] = useState(false);
  const [actionError, setActionError] = useState('');
  const run = plane.currentRun;
  const active = Boolean(run && ACTIVE_STATUSES.has(run.status));
  const runtimeIssue = plane.runtimeIssue;
  const modelProfile = (plane.overview?.status as any)?.model_profile;
  const metrics = run?.metrics;
  const latestScreenEvent = [...plane.events].reverse().find((event) => ['screen_watch', 'screen_delta', 'perception'].includes(event.type))
    || [...(run?.timeline || [])].reverse().find((event) => ['screen_watch', 'screen_delta', 'perception'].includes(event.type));
  const screenshot = [...(run?.evidence || [])].reverse().find((item) => item.type === 'screenshot' && item.path);
  const screenshotKind = String(screenshot?.kind || 'screen').replaceAll('_', ' ');
  const screenshotRegion = screenshot?.region as Record<string, unknown> | undefined;
  const screenshotUrl = screenshot && plane.baseUrl && run
    ? plane.getClient().traceScreenshotUrl(run.run_id, String(screenshot.path))
    : '';

  const execute = async () => {
    if (!goal.trim() || submitting) return;
    if (runtimeIssue) {
      setActionError(`${runtimeIssue} 任务未启动。`);
      return;
    }
    setSubmitting(true);
    setActionError('');
    try {
      localStorage.setItem('arp-last-goal', goal.trim());
      await plane.startGoal(goal.trim(), maxSteps);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
    } finally {
      setSubmitting(false);
    }
  };

  const stop = async () => {
    try { await plane.cancelCurrent(); } catch (error) { setActionError(error instanceof Error ? error.message : String(error)); }
  };

  return (
    <div className="arp-page arp-agent-page">
      <section className="arp-command-surface">
        <div className="arp-section-heading">
          <div><span className="arp-kicker">任务目标</span><h2>智能代理指令</h2></div>
          {run ? <StatusPill status={run.status} /> : <StatusPill label="就绪" />}
        </div>
        <textarea
          className="arp-goal-input"
          value={goal}
          onChange={(event) => setGoal(event.target.value)}
          onKeyDown={(event) => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') execute(); }}
          placeholder="描述希望代理在这台电脑上完成的目标……"
          disabled={active}
        />
        <div className="arp-command-actions">
          <label className="arp-step-control">步骤上限<input type="number" min={1} max={400} value={maxSteps} onChange={(event) => setMaxSteps(Number(event.target.value) || 40)} disabled={active} /></label>
          <div className="arp-action-spacer" />
          {active ? (
          <button className="arp-button arp-button-danger" onClick={stop}><CircleStop size={15} />停止</button>
          ) : (
            <button className="arp-button arp-button-primary" onClick={execute} disabled={!goal.trim() || submitting || Boolean(runtimeIssue)}>
              {submitting ? <Loader2 size={15} className="arp-spin" /> : <Play size={15} fill="currentColor" />}运行任务
            </button>
          )}
        </div>
        {actionError && <div className="arp-inline-error"><XCircle size={14} />{actionError}</div>}
        {runtimeIssue && !actionError && <div className="arp-inline-error"><XCircle size={14} />{runtimeIssue} 当前控制器版本不兼容，应用会自动替换空闲控制器；如果刚刚热更新过，请重启一次桌面应用。</div>}
        {modelProfile?.model && modelProfile?.supports_vision === false && <div className="arp-inline-error"><Eye size={14} />{String(modelProfile.model)} 不接受图像输入。像素密集型 GUI 任务将使用较慢的感知/控制器回退路径，请选择支持视觉的主模型。</div>}
      </section>

      {run?.phase === 'awaiting_human' && (
        <section className="arp-gate-banner">
          <div><UserCheck size={18} /><div><strong>需要人工确认</strong><span>{String([...plane.events].reverse().find((event) => event.type === 'arp_human_gate')?.message || run.current_node_id || '')}</span></div></div>
          <div><button className="arp-button" onClick={() => plane.decideGate(false)}>拒绝</button><button className="arp-button arp-button-primary" onClick={() => plane.decideGate(true)}>批准</button></div>
        </section>
      )}

      <section className="arp-live-strip">
        <div className="arp-live-title"><Radio size={15} /><span>{run?.goal || '当前没有运行中的任务'}</span></div>
        <PhaseRail run={run} />
        <div className="arp-runtime-focus"><span>节点</span><code>{run?.current_node_id || '-'}</code><span>工具</span><code>{TOOL_LABELS[run?.current_tool || ''] || run?.current_tool || '-'}</code></div>
      </section>

      <section className="arp-metrics-row">
        <Metric label="截图" value={metrics?.screen_capture_latency_avg_ms || 0} unit="毫秒" />
        <Metric label="感知" value={metrics?.perception_latency_avg_ms || 0} unit="毫秒" />
        <Metric label="决策" value={metrics?.action_decision_latency_avg_ms || 0} unit="毫秒" />
        <Metric label="执行" value={metrics?.action_execution_latency_avg_ms || 0} unit="毫秒" />
        <Metric label="执行后验证" value={metrics?.post_action_verify_latency_avg_ms || 0} unit="毫秒" />
        <Metric label="快速验证" value={`${Math.round((metrics?.semantic_verify_fast_path_rate || 0) * 100)}%`} />
        <Metric label="重规划率" value={`${Math.round((metrics?.replan_frequency || 0) * 100)}%`} />
        <Metric label="视觉模型调用率" value={`${Math.round((metrics?.vlm_call_frequency || 0) * 100)}%`} />
        <Metric label="技能复用率" value={`${Math.round((metrics?.skill_reuse_rate || 0) * 100)}%`} />
        <Metric label="锚点命中率" value={`${Math.round((metrics?.anchor_cache_hit_rate || 0) * 100)}%`} />
      </section>

      <div className="arp-agent-grid">
        <section className="arp-panel arp-timeline-panel">
          <div className="arp-panel-heading"><div><ListChecks size={15} /><strong>实时时间线</strong></div><span>{run?.timeline.length || plane.events.length} 个事件</span></div>
          <RunTimeline run={run} events={plane.events} />
        </section>
        <div className="arp-agent-side">
          <section className="arp-panel arp-world-panel">
            <div className="arp-panel-heading"><div><Eye size={15} /><strong>当前画面状态</strong></div>{latestScreenEvent && <span className="arp-live-pulse">实时</span>}</div>
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
          </section>
          <section className="arp-panel arp-evidence-panel">
            <div className="arp-panel-heading"><div><ShieldCheck size={15} /><strong>证据</strong></div></div>
            {(run?.evidence || []).length ? run!.evidence.slice(-5).reverse().map((item, index) => (
              <div className="arp-evidence-row" key={index}><CheckCircle2 size={14} /><span>{displayValue(item.reason || item.kind || item.type || item.path)}</span></div>
            )) : <EmptyState icon={ShieldCheck} title="尚未收集证据" />}
          </section>
        </div>
      </div>
    </div>
  );
}

function Studio({ plane }: { plane: Plane }) {
  const script = useStore((state) => state.script);
  const saveScript = useStore((state) => state.saveScript);
  const setUI = useStore((state) => state.setUI);
  const fitToScreen = useStore((state) => state.fitToScreen);
  const [validation, setValidation] = useState<GraphValidation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [mobilePanel, setMobilePanel] = useState<'library' | 'properties' | null>(null);
  const processedNodeEvents = useRef(new Set<string>());
  const automation = useMemo(
    () => automationIrFromFlowScript(script, `air-${script.meta.id}`),
    [script],
  );
  const graph = useMemo<ArpGraph>(() => {
    return automationRuntimeGraph(automation);
  }, [automation]);
  const compilation = graph.metadata.compilation as { native_nodes?: number; legacy_nodes?: number; legacy_types?: string[] } | undefined;

  useEffect(() => {
    setUI({ panels_visible: true, show_node_library: true, show_property_panel: true });
    requestAnimationFrame(fitToScreen);
  }, []);

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
      const result = await plane.validateAutomation(automation);
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
      const result = await plane.validateAutomation(automation);
      setValidation(result);
      if (!result.valid) return;
      await plane.startAutomation(automation);
    } catch (runError) {
      setError(runError instanceof Error ? runError.message : String(runError));
    } finally { setBusy(false); }
  };

  return (
    <div className="arp-studio-page">
      <header className="arp-studio-toolbar">
        <div className="arp-studio-title"><GitBranch size={16} /><div><strong>{script.meta.name}</strong><span>{graph.nodes.length} 个节点 / {graph.edges.length} 条连线 / {compilation?.native_nodes ?? graph.nodes.length} 个原生节点</span></div></div>
        {Boolean(compilation?.legacy_nodes) && <StatusPill status="failed" label={`${compilation?.legacy_nodes} 个节点不支持`} />}
        {validation && <StatusPill status={validation.valid ? 'success' : 'failed'} label={validation.valid ? '有效' : `${validation.errors.length} 个错误`} />}
        {error && <span className="arp-toolbar-error">{error}</span>}
        <div className="arp-action-spacer" />
        <button className="arp-icon-button arp-mobile-panel-button" title="节点库" aria-label="显示/隐藏节点库" aria-pressed={mobilePanel === 'library'} onClick={() => setMobilePanel((current) => current === 'library' ? null : 'library')}><PanelLeft size={15} /></button>
        <button className="arp-icon-button arp-mobile-panel-button" title="节点属性" aria-label="显示/隐藏节点属性" aria-pressed={mobilePanel === 'properties'} onClick={() => setMobilePanel((current) => current === 'properties' ? null : 'properties')}><SlidersHorizontal size={15} /></button>
        <button className="arp-icon-button" title="适应工作流" aria-label="适应工作流" onClick={fitToScreen}><Network size={15} /></button>
        <button className="arp-button arp-studio-command" title="保存工作流" aria-label="保存工作流" onClick={() => saveScript()}><Save size={14} /><span>保存</span></button>
        <button className="arp-button arp-studio-command" title="验证工作流" aria-label="验证工作流" onClick={validate} disabled={busy}><ShieldCheck size={14} /><span>验证</span></button>
        <button className="arp-button arp-button-primary arp-studio-command" title="运行工作流" aria-label="运行工作流" onClick={run} disabled={busy || Boolean(plane.currentRun && ACTIVE_STATUSES.has(plane.currentRun.status))}>{busy ? <Loader2 size={14} className="arp-spin" /> : <Play size={14} fill="currentColor" />}<span>运行工作流</span></button>
      </header>
      {validation && (!validation.valid || validation.warnings.length > 0) && (
        <div className={`arp-validation-bar ${validation.valid ? 'warning' : 'danger'}`}>
          {(validation.errors.length ? validation.errors : validation.warnings).join(' · ')}
        </div>
      )}
      <div className={`arp-studio-shell${mobilePanel ? ` mobile-${mobilePanel}-open` : ''}`}>
        <NodeLibrary />
        <main className="arp-canvas-stage"><FlowCanvas /></main>
        <PropertyPanel />
        {mobilePanel && <button type="button" className="arp-studio-scrim" aria-label="关闭侧栏" onClick={() => setMobilePanel(null)} />}
      </div>
    </div>
  );
}

function RunsView({ plane }: { plane: Plane }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = plane.runs.find((run) => run.run_id === selectedId) || plane.runs[0] || null;
  useEffect(() => { if (!selectedId && plane.runs[0]) setSelectedId(plane.runs[0].run_id); }, [plane.runs, selectedId]);
  return (
    <div className="arp-page arp-runs-page">
      <div className="arp-page-title"><div><span className="arp-kicker">历史记录</span><h2>运行记录</h2></div><button className="arp-button" onClick={plane.refresh}><RefreshCw size={14} />刷新</button></div>
      <div className="arp-runs-layout">
        <section className="arp-run-list">
          <div className="arp-list-header"><span>任务</span><span>状态</span><span>耗时</span></div>
          {plane.runs.map((run) => (
            <button key={run.run_id} className={`arp-run-list-row ${selected?.run_id === run.run_id ? 'selected' : ''}`} onClick={() => setSelectedId(run.run_id)}>
              <div><strong>{run.goal || run.run_id}</strong><span>{RUN_KIND_LABELS[run.kind] || run.kind} · {formatTime(run.started_at)}</span></div>
              <StatusPill status={run.status} />
              <code>{formatDuration(run)}</code>
            </button>
          ))}
          {!plane.runs.length && <EmptyState icon={History} title="暂无运行记录" />}
        </section>
        <section className="arp-run-detail">
          {selected ? (
            <>
              <div className="arp-run-detail-head"><div><span className="arp-kicker">{selected.kind}</span><h3>{selected.goal || selected.run_id}</h3><code>{selected.run_id}</code></div><StatusPill status={selected.status} /></div>
              <div className="arp-metrics-row compact">
                <Metric label="耗时" value={formatDuration(selected)} />
                <Metric label="决策次数" value={selected.metrics.decision_count} />
                <Metric label="工具次数" value={selected.metrics.tool_count} />
                <Metric label="视觉模型调用" value={selected.metrics.vlm_call_count} />
                <Metric label="锚点命中" value={`${Math.round((selected.metrics.anchor_cache_hit_rate || 0) * 100)}%`} />
                <Metric label="视觉伺服修正" value={selected.metrics.visual_servo_correction_count || 0} />
              </div>
              {selected.error && <div className="arp-inline-error"><XCircle size={14} />{selected.error}</div>}
              <div className="arp-panel-heading"><div><Activity size={15} /><strong>时间线</strong></div><span>{selected.timeline.length} 个事件</span></div>
              <RunTimeline run={selected} />
            </>
          ) : <EmptyState icon={History} title="请选择一条运行记录" />}
        </section>
      </div>
    </div>
  );
}

interface TraceItem { run_id: string; task: string; status: string; steps: number }
interface ScriptItem { file: string; name: string; task: string; steps: number }
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
}

type ReplayMode = 'offline_graph' | 'skill' | 'apprentice';

function AssetsView({ plane, openStudio }: { plane: Plane; openStudio: () => void }) {
  const [traces, setTraces] = useState<TraceItem[]>([]);
  const [scripts, setScripts] = useState<ScriptItem[]>([]);
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [recording, setRecording] = useState(false);
  const [replayMode, setReplayMode] = useState<ReplayMode>('offline_graph');
  const [apprenticeTask, setApprenticeTask] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const loadScript = useStore((state) => state.loadScript);

  const refresh = useCallback(async () => {
    if (!plane.baseUrl) return;
    try {
      const client = plane.getClient();
      const [nextTraces, nextScripts, nextMemories, apprentice] = await Promise.all([
        client.traces(), client.scripts(), client.memories(), client.apprenticeStatus(),
      ]);
      setTraces(nextTraces); setScripts(nextScripts); setMemories(nextMemories); setRecording(Boolean(apprentice.recording));
      if (apprentice.task) setApprenticeTask((current) => current || apprentice.task || '');
      setError('');
    } catch (refreshError) { setError(refreshError instanceof Error ? refreshError.message : String(refreshError)); }
  }, [plane.baseUrl]);

  useEffect(() => { refresh(); }, [refresh]);

  const openSkill = async (file: string) => {
    setBusy(true);
    try {
      const asset = await plane.getClient().script<WpsScript | AutomationIR>(file);
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
    try {
      const client = plane.getClient();
      const asset = await client.script<WpsScript | AutomationIR>(file);
      if (
        asset.format === 'automation-ir/1'
        && automationRuntimeKind(asset as AutomationIR) === 'arp_graph'
      ) {
        await plane.startAutomation(asset as AutomationIR);
      } else {
        if (plane.runtimeIssue) throw new Error(`${plane.runtimeIssue} 重放未启动。`);
        await client.replay(file);
      }
      await plane.refresh();
    }
    catch (replayError) { setError(replayError instanceof Error ? replayError.message : String(replayError)); }
    finally { setBusy(false); }
  };

  const exportTrace = async (runId: string) => {
    setBusy(true);
    try { await plane.getClient().exportTrace(runId); await refresh(); }
    catch (exportError) { setError(exportError instanceof Error ? exportError.message : String(exportError)); }
    finally { setBusy(false); }
  };

  const toggleRecording = async () => {
    if (!recording && !apprenticeTask.trim()) {
      setError('请输入要让学习器学习的工作流。');
      return;
    }
    setBusy(true);
    try {
      if (recording) {
        const result = await plane.getClient().apprenticeStop();
        await refresh();
        if (result.file) await openSkill(String(result.file));
        return;
      }
      await plane.getClient().apprenticeStart(apprenticeTask.trim());
      await refresh();
    } catch (recordError) { setError(recordError instanceof Error ? recordError.message : String(recordError)); }
    finally { setBusy(false); }
  };

  const verifyMemory = async (item: MemoryItem) => {
    setBusy(true); setError('');
    try {
      await plane.getClient().verifyMemory(item.id, `人工已验证学习器重放：${item.task || item.id}`);
      await refresh();
    } catch (verifyError) { setError(verifyError instanceof Error ? verifyError.message : String(verifyError)); }
    finally { setBusy(false); }
  };

  return (
    <div className="arp-page arp-assets-page">
      <div className="arp-page-title"><div><span className="arp-kicker">复用</span><h2>技能与学习</h2></div></div>
      {error && <div className="arp-inline-error"><XCircle size={14} />{error}</div>}
      <div className="arp-replay-toolbar">
        <div className="arp-replay-modes" role="tablist" aria-label="重放模式">
          <button className={replayMode === 'offline_graph' ? 'active' : ''} onClick={() => setReplayMode('offline_graph')} role="tab" aria-selected={replayMode === 'offline_graph'} disabled={recording}><Workflow size={14} />离线流程</button>
          <button className={replayMode === 'skill' ? 'active' : ''} onClick={() => setReplayMode('skill')} role="tab" aria-selected={replayMode === 'skill'} disabled={recording}><RefreshCw size={14} />技能复用</button>
          <button className={replayMode === 'apprentice' ? 'active' : ''} onClick={() => setReplayMode('apprentice')} role="tab" aria-selected={replayMode === 'apprentice'}><GraduationCap size={14} />示教学习</button>
        </div>
        {replayMode === 'apprentice' && (
          <div className="arp-apprentice-control">
            <input value={apprenticeTask} onChange={(event) => setApprenticeTask(event.target.value)} placeholder="输入要学习的工作流" disabled={recording || busy} aria-label="要学习的工作流" />
            <button className={`arp-button ${recording ? 'arp-button-danger' : 'arp-button-primary'}`} onClick={toggleRecording} disabled={busy || (!recording && !apprenticeTask.trim())}>{recording ? <CircleStop size={14} /> : <Radio size={14} />}{recording ? '结束学习' : '开始学习'}</button>
          </div>
        )}
      </div>
      <div className="arp-assets-grid">
        <section className="arp-panel">
          <div className="arp-panel-heading"><div><Library size={15} /><strong>可复用技能</strong></div><span>{scripts.length}</span></div>
          <div className="arp-asset-list">
            {scripts.map((item) => (
              <div className="arp-asset-row" key={item.file}>
                <div className="arp-asset-icon"><FileCode2 size={16} /></div>
                <div><strong>{item.name || item.file}</strong><span>{item.steps} 步 · {item.task || '录制的工作流'}</span></div>
                <button className="arp-icon-button" title="在流程工作台中打开" onClick={() => openSkill(item.file)} disabled={busy}><Braces size={15} /></button>
                {replayMode === 'offline_graph' && <button className="arp-button arp-button-small" onClick={() => openSkill(item.file)} disabled={busy}><Workflow size={13} />打开流程</button>}
                {replayMode === 'skill' && <button className="arp-button arp-button-small" onClick={() => replay(item.file)} disabled={busy}><Play size={13} />复用</button>}
                {replayMode === 'apprentice' && <button className="arp-button arp-button-small" onClick={() => openSkill(item.file)} disabled={busy}><GraduationCap size={13} />查看</button>}
              </div>
            ))}
            {!scripts.length && <EmptyState icon={Library} title="暂无可复用技能" />}
          </div>
        </section>
        <section className="arp-panel">
          <div className="arp-panel-heading"><div><Database size={15} /><strong>探索轨迹</strong></div><span>{traces.length}</span></div>
          <div className="arp-asset-list">
            {traces.map((item) => (
              <div className="arp-asset-row" key={item.run_id}>
                <div className="arp-asset-icon"><TerminalSquare size={16} /></div>
                <div><strong>{item.task || item.run_id}</strong><span>{item.steps} 步 · {STATUS_LABELS[item.status] || item.status}</span></div>
                <button className="arp-button arp-button-small" onClick={() => exportTrace(item.run_id)} disabled={busy}><Save size={13} />提取技能</button>
              </div>
            ))}
            {!traces.length && <EmptyState icon={Database} title="暂无探索轨迹" />}
          </div>
        </section>
        <section className="arp-panel arp-memory-assets">
          <div className="arp-panel-heading"><div><BrainCircuit size={15} /><strong>记忆图谱</strong></div><span>{memories.length}</span></div>
          <div className="arp-asset-list">
            {memories.map((item) => (
              <div className="arp-asset-row" key={item.id}>
                <div className="arp-asset-icon"><BrainCircuit size={16} /></div>
                <div>
                  <strong>{item.task || item.id}</strong>
                  <span>{STATUS_LABELS[item.status || (item.provisional ? 'provisional' : 'active')] || item.status} · 成功 {item.successes || 0} / 使用 {item.uses || 0} 次 · 置信度 {Math.round((item.confidence || 0) * 100)}%{item.app ? ` · ${item.app}` : ''}</span>
                </div>
                {item.script && <button className="arp-icon-button" title="在流程工作台中打开关联技能" onClick={() => openSkill(item.script!)} disabled={busy}><Braces size={15} /></button>}
                {item.script && item.provisional && <button className="arp-button arp-button-small" onClick={() => verifyMemory(item)} disabled={busy}><Check size={13} />验证</button>}
                {item.script && !item.provisional && <button className="arp-button arp-button-small" onClick={() => replay(item.script!)} disabled={busy}><Play size={13} />复用</button>}
              </div>
            ))}
            {!memories.length && <EmptyState icon={BrainCircuit} title="暂无可复用记忆" />}
          </div>
        </section>
      </div>
    </div>
  );
}

function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (value: boolean) => void; label: string }) {
  return <label className="arp-toggle-row"><span>{label}</span><button type="button" className={`arp-toggle ${checked ? 'on' : ''}`} onClick={() => onChange(!checked)} aria-pressed={checked}><span /></button></label>;
}

function SystemView({ plane }: { plane: Plane }) {
  const themeMode = useStore((state) => state.ui.themeMode);
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
              <Toggle checked={Boolean(config.agent?.verify_done)} onChange={(value) => patch('agent', 'verify_done', value)} label="启用证据完成门" />
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
            <Toggle checked={Boolean(config.controller?.native_visual_thinking)} onChange={(value) => patch('controller', 'native_visual_thinking', value)} label="实验性空间推理" />
            <Toggle checked={config.controller?.target_window_capture_enabled !== false} onChange={(value) => patch('controller', 'target_window_capture_enabled', value)} label="自动裁剪目标窗口画面" />
            <Toggle checked={Boolean(config.controller?.multi_input_cooperation)} onChange={(value) => patch('controller', 'multi_input_cooperation', value)} label="实验性多鼠标键盘协同" />
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
            <div><dt>加载提交</dt><dd>{String((plane.overview?.status as any)?.runtime?.git?.loaded_commit || '-')}</dd></div>
            <div><dt>工作提交</dt><dd>{String((plane.overview?.status as any)?.runtime?.git?.current_commit || '-')}</dd></div>
            <div><dt>运行时协议</dt><dd>{String((plane.overview?.status as any)?.runtime?.contract_version || '-')}</dd></div>
            <div><dt>源码状态</dt><dd>{plane.runtimeIssue || '加载版本与当前版本一致'}</dd></div>
            <div><dt>主模型视觉能力</dt><dd>{String((plane.overview?.status as any)?.model_profile?.supports_vision ?? '-')}</dd></div>
            <div><dt>世界模型</dt><dd>{String((plane.overview?.status as any)?.world_model ?? config?.agent?.world_model ?? '-')}</dd></div>
            <div><dt>视觉模型状态</dt><dd>{String((plane.overview?.status as any)?.vlm_configured ?? '-')}</dd></div>
          </dl>
          <div className="arp-theme-control"><span>主题</span><div>{(['light', 'dark', 'system'] as const).map((mode) => <button key={mode} className={themeMode === mode ? 'active' : ''} onClick={() => setUI({ themeMode: mode })}>{mode === 'light' ? <Sun size={13} /> : mode === 'dark' ? <Moon size={13} /> : <Settings size={13} />}{mode === 'light' ? '浅色' : mode === 'dark' ? '深色' : '跟随系统'}</button>)}</div></div>
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

export function PlatformWorkbench() {
  const plane = useControlPlane();
  const storeMode = useStore((state) => state.ui.viewMode);
  const setViewMode = useStore((state) => state.setViewMode);
  const currentView = viewFromStore(storeMode);
  const setView = (view: PlatformView) => setViewMode(view);

  useEffect(() => {
    useStore.getState().fetchFeatures();
  }, []);

  return (
    <div className="arp-app">
      <aside className="arp-sidebar">
        <div className="arp-brand"><div className="arp-brand-mark"><Bot size={18} /></div><div><strong>WinPilot</strong><span>智能代理运行平台</span></div></div>
        <nav>
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            return <button key={item.id} aria-label={item.label} title={item.label} className={currentView === item.id ? 'active' : ''} onClick={() => setView(item.id)}><Icon size={16} /><span>{item.label}</span>{currentView === item.id && <ChevronRight size={13} />}</button>;
          })}
        </nav>
        <div className="arp-sidebar-status">
          <span className={`arp-connection-dot ${plane.connection}`} />
          <div><strong>{plane.connection === 'online' ? '在线' : plane.connection === 'connecting' ? '连接中' : plane.connection === 'degraded' ? '降级' : '离线'}</strong><span>{plane.baseUrl ? new URL(plane.baseUrl).host : '正在启动运行时'}</span></div>
        </div>
      </aside>
      <main className="arp-workspace">
        <header className="arp-topbar">
          <div className="arp-breadcrumb"><span>工作区</span><ChevronRight size={13} /><strong>{NAV_ITEMS.find((item) => item.id === currentView)?.label}</strong></div>
          {plane.error && <div className="arp-topbar-error"><XCircle size={13} />{plane.error}</div>}
          <div className="arp-topbar-run">
            {plane.currentRun && <><Clock3 size={13} /><span>{plane.currentRun.goal || plane.currentRun.run_id}</span><StatusPill status={plane.currentRun.status} /></>}
          </div>
        </header>
        <div className="arp-view">
          {currentView === 'agent' && <AgentCockpit plane={plane} />}
          {currentView === 'studio' && <Studio plane={plane} />}
          {currentView === 'runs' && <RunsView plane={plane} />}
          {currentView === 'assets' && <AssetsView plane={plane} openStudio={() => setView('studio')} />}
          {currentView === 'system' && <SystemView plane={plane} />}
        </div>
      </main>
    </div>
  );
}
