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
import { flowToArpGraph } from '../../platform/flowToArpGraph';
import { useControlPlane } from '../../platform/useControlPlane';
import type { ChatGPTDesktopIntegration, GraphValidation, RunProjection, RuntimeEvent } from '../../platform/contracts';
import { wpsToFlowScript, type WpsScript } from '../../utils/wpsToFlowScript';

type PlatformView = 'agent' | 'studio' | 'runs' | 'assets' | 'system';
type Plane = ReturnType<typeof useControlPlane>;

const NAV_ITEMS: Array<{ id: PlatformView; label: string; icon: React.ElementType }> = [
  { id: 'agent', label: 'Agent', icon: Bot },
  { id: 'studio', label: 'ARP Studio', icon: GitBranch },
  { id: 'runs', label: 'Runs', icon: History },
  { id: 'assets', label: 'Skills', icon: Library },
  { id: 'system', label: 'System', icon: Settings },
];

const ACTIVE_STATUSES = new Set(['queued', 'running', 'paused']);
const PHASES = ['planning', 'deciding', 'perceiving', 'acting', 'verifying'];

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
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
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
  return <span className={`arp-status arp-status-${statusTone(status)}`}><span className="arp-status-dot" />{label || status || 'idle'}</span>;
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

  if (!entries.length) return <EmptyState icon={Activity} title="No runtime events" />;
  return (
    <div className="arp-timeline">
      {entries.map((entry, index) => (
        <div className="arp-timeline-row" key={`${entry.type}-${entry.ts || index}-${index}`}>
          <div className={`arp-event-mark arp-event-${entry.type.includes('error') || entry.status === 'failed' ? 'danger' : entry.type.includes('done') ? 'success' : 'default'}`} />
          <time>{formatTime(entry.ts)}</time>
          <div className="arp-event-copy">
            <div><strong>{entry.type.replace(/^agent_|^arp_/, '').replaceAll('_', ' ')}</strong>{entry.node_id && <code>{entry.node_id}</code>}{entry.tool && <code>{entry.tool}</code>}</div>
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
    <div className="arp-phase-rail" aria-label="Execution phases">
      {PHASES.map((phase, index) => (
        <React.Fragment key={phase}>
          <div className={`arp-phase ${index === phaseIndex ? 'active' : ''} ${phaseIndex > index ? 'complete' : ''}`}>
            <span>{phaseIndex > index ? <Check size={11} /> : index + 1}</span>
            <label>{phase}</label>
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
  const metrics = run?.metrics;
  const latestScreenEvent = [...plane.events].reverse().find((event) => ['screen_watch', 'screen_delta', 'perception'].includes(event.type))
    || [...(run?.timeline || [])].reverse().find((event) => ['screen_watch', 'screen_delta', 'perception'].includes(event.type));
  const screenshot = [...(run?.evidence || [])].reverse().find((item) => item.type === 'screenshot' && item.path);
  const screenshotKind = String(screenshot?.kind || 'screen').replaceAll('_', ' ');
  const screenshotRegion = screenshot?.region as Record<string, unknown> | undefined;
  const screenshotUrl = screenshot && plane.baseUrl && run
    ? new URL(`api/trace/${encodeURIComponent(run.run_id)}/shot/${encodeURIComponent(String(screenshot.path).replace(/\\/g, '/').split('/').pop() || '')}`, plane.baseUrl).toString()
    : '';

  const execute = async () => {
    if (!goal.trim() || submitting) return;
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
          <div><span className="arp-kicker">Goal</span><h2>Agent command</h2></div>
          {run ? <StatusPill status={run.status} /> : <StatusPill label="ready" />}
        </div>
        <textarea
          className="arp-goal-input"
          value={goal}
          onChange={(event) => setGoal(event.target.value)}
          onKeyDown={(event) => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') execute(); }}
          placeholder="Describe the outcome to achieve on this computer..."
          disabled={active}
        />
        <div className="arp-command-actions">
          <label className="arp-step-control">Step budget<input type="number" min={1} max={400} value={maxSteps} onChange={(event) => setMaxSteps(Number(event.target.value) || 40)} disabled={active} /></label>
          <div className="arp-action-spacer" />
          {active ? (
            <button className="arp-button arp-button-danger" onClick={stop}><CircleStop size={15} />Stop</button>
          ) : (
            <button className="arp-button arp-button-primary" onClick={execute} disabled={!goal.trim() || submitting}>
              {submitting ? <Loader2 size={15} className="arp-spin" /> : <Play size={15} fill="currentColor" />}Run goal
            </button>
          )}
        </div>
        {actionError && <div className="arp-inline-error"><XCircle size={14} />{actionError}</div>}
      </section>

      {run?.phase === 'awaiting_human' && (
        <section className="arp-gate-banner">
          <div><UserCheck size={18} /><div><strong>Approval required</strong><span>{String([...plane.events].reverse().find((event) => event.type === 'arp_human_gate')?.message || run.current_node_id || '')}</span></div></div>
          <div><button className="arp-button" onClick={() => plane.decideGate(false)}>Reject</button><button className="arp-button arp-button-primary" onClick={() => plane.decideGate(true)}>Approve</button></div>
        </section>
      )}

      <section className="arp-live-strip">
        <div className="arp-live-title"><Radio size={15} /><span>{run?.goal || 'No active run'}</span></div>
        <PhaseRail run={run} />
        <div className="arp-runtime-focus"><span>Node</span><code>{run?.current_node_id || '-'}</code><span>Tool</span><code>{run?.current_tool || '-'}</code></div>
      </section>

      <section className="arp-metrics-row">
        <Metric label="Capture" value={metrics?.screen_capture_latency_avg_ms || 0} unit="ms" />
        <Metric label="Perception" value={metrics?.perception_latency_avg_ms || 0} unit="ms" />
        <Metric label="Decision" value={metrics?.action_decision_latency_avg_ms || 0} unit="ms" />
        <Metric label="Execution" value={metrics?.action_execution_latency_avg_ms || 0} unit="ms" />
        <Metric label="Post verify" value={metrics?.post_action_verify_latency_avg_ms || 0} unit="ms" />
        <Metric label="Replan rate" value={`${Math.round((metrics?.replan_frequency || 0) * 100)}%`} />
        <Metric label="VLM rate" value={`${Math.round((metrics?.vlm_call_frequency || 0) * 100)}%`} />
        <Metric label="Skill reuse" value={`${Math.round((metrics?.skill_reuse_rate || 0) * 100)}%`} />
        <Metric label="Anchor hit" value={`${Math.round((metrics?.anchor_cache_hit_rate || 0) * 100)}%`} />
      </section>

      <div className="arp-agent-grid">
        <section className="arp-panel arp-timeline-panel">
          <div className="arp-panel-heading"><div><ListChecks size={15} /><strong>Live timeline</strong></div><span>{run?.timeline.length || plane.events.length} events</span></div>
          <RunTimeline run={run} events={plane.events} />
        </section>
        <div className="arp-agent-side">
          <section className="arp-panel arp-world-panel">
            <div className="arp-panel-heading"><div><Eye size={15} /><strong>World state</strong></div>{latestScreenEvent && <span className="arp-live-pulse">live</span>}</div>
            {screenshotUrl ? (
              <div className="arp-evidence-preview">
                <img className="arp-evidence-image" src={screenshotUrl} alt="Latest run evidence" />
                <span className={screenshot?.transient ? 'transient' : ''}>
                  <ShieldCheck size={11} />{screenshotKind}
                  {screenshotRegion && <code>{String(screenshotRegion.left ?? 0)},{String(screenshotRegion.top ?? 0)} · {String(screenshotRegion.width ?? 0)}×{String(screenshotRegion.height ?? 0)}</code>}
                </span>
              </div>
            ) : <div className="arp-screen-empty"><Eye size={22} /><span>Awaiting visual evidence</span></div>}
            <dl className="arp-world-state">
              <div><dt>Screen signal</dt><dd>{latestScreenEvent?.type || 'idle'}</dd></div>
              <div><dt>Updated</dt><dd>{formatTime(latestScreenEvent?.ts)}</dd></div>
              <div><dt>Evidence</dt><dd>{run?.evidence.length || 0}</dd></div>
              <div><dt>Done checks</dt><dd>{metrics?.done_check_passed || 0}/{metrics?.done_checks || 0}</dd></div>
            </dl>
          </section>
          <section className="arp-panel arp-evidence-panel">
            <div className="arp-panel-heading"><div><ShieldCheck size={15} /><strong>Evidence</strong></div></div>
            {(run?.evidence || []).length ? run!.evidence.slice(-5).reverse().map((item, index) => (
              <div className="arp-evidence-row" key={index}><CheckCircle2 size={14} /><span>{displayValue(item.reason || item.kind || item.type || item.path)}</span></div>
            )) : <EmptyState icon={ShieldCheck} title="No evidence collected" />}
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
  const graph = useMemo(() => flowToArpGraph(script), [script]);
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
        if (node.status !== nextStatus || (event.error && node.error !== String(event.error))) {
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
      const result = await plane.validateGraph(graph);
      setValidation(result);
      return result;
    } catch (validateError) {
      setError(validateError instanceof Error ? validateError.message : String(validateError));
      return null;
    } finally { setBusy(false); }
  }, [plane, graph]);

  const run = async () => {
    setBusy(true); setError('');
    try {
      const result = await plane.validateGraph(graph);
      setValidation(result);
      if (!result.valid) return;
      await plane.startGraph(graph);
    } catch (runError) {
      setError(runError instanceof Error ? runError.message : String(runError));
    } finally { setBusy(false); }
  };

  return (
    <div className="arp-studio-page">
      <header className="arp-studio-toolbar">
        <div className="arp-studio-title"><GitBranch size={16} /><div><strong>{script.meta.name}</strong><span>{graph.nodes.length} nodes / {graph.edges.length} edges / {compilation?.native_nodes ?? graph.nodes.length} native</span></div></div>
        {Boolean(compilation?.legacy_nodes) && <StatusPill status="failed" label={`${compilation?.legacy_nodes} unsupported`} />}
        {validation && <StatusPill status={validation.valid ? 'success' : 'failed'} label={validation.valid ? 'valid' : `${validation.errors.length} errors`} />}
        {error && <span className="arp-toolbar-error">{error}</span>}
        <div className="arp-action-spacer" />
        <button className="arp-icon-button arp-mobile-panel-button" title="Node library" aria-label="Toggle node library" aria-pressed={mobilePanel === 'library'} onClick={() => setMobilePanel((current) => current === 'library' ? null : 'library')}><PanelLeft size={15} /></button>
        <button className="arp-icon-button arp-mobile-panel-button" title="Node properties" aria-label="Toggle node properties" aria-pressed={mobilePanel === 'properties'} onClick={() => setMobilePanel((current) => current === 'properties' ? null : 'properties')}><SlidersHorizontal size={15} /></button>
        <button className="arp-icon-button" title="Fit workflow" aria-label="Fit workflow" onClick={fitToScreen}><Network size={15} /></button>
        <button className="arp-button arp-studio-command" title="Save workflow" aria-label="Save workflow" onClick={() => saveScript()}><Save size={14} /><span>Save</span></button>
        <button className="arp-button arp-studio-command" title="Validate workflow" aria-label="Validate workflow" onClick={validate} disabled={busy}><ShieldCheck size={14} /><span>Validate</span></button>
        <button className="arp-button arp-button-primary arp-studio-command" title="Run graph" aria-label="Run graph" onClick={run} disabled={busy || Boolean(plane.currentRun && ACTIVE_STATUSES.has(plane.currentRun.status))}>{busy ? <Loader2 size={14} className="arp-spin" /> : <Play size={14} fill="currentColor" />}<span>Run graph</span></button>
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
        {mobilePanel && <button type="button" className="arp-studio-scrim" aria-label="Close side panel" onClick={() => setMobilePanel(null)} />}
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
      <div className="arp-page-title"><div><span className="arp-kicker">History</span><h2>Runs</h2></div><button className="arp-button" onClick={plane.refresh}><RefreshCw size={14} />Refresh</button></div>
      <div className="arp-runs-layout">
        <section className="arp-run-list">
          <div className="arp-list-header"><span>Run</span><span>Status</span><span>Duration</span></div>
          {plane.runs.map((run) => (
            <button key={run.run_id} className={`arp-run-list-row ${selected?.run_id === run.run_id ? 'selected' : ''}`} onClick={() => setSelectedId(run.run_id)}>
              <div><strong>{run.goal || run.run_id}</strong><span>{run.kind} · {formatTime(run.started_at)}</span></div>
              <StatusPill status={run.status} />
              <code>{formatDuration(run)}</code>
            </button>
          ))}
          {!plane.runs.length && <EmptyState icon={History} title="No runs yet" />}
        </section>
        <section className="arp-run-detail">
          {selected ? (
            <>
              <div className="arp-run-detail-head"><div><span className="arp-kicker">{selected.kind}</span><h3>{selected.goal || selected.run_id}</h3><code>{selected.run_id}</code></div><StatusPill status={selected.status} /></div>
              <div className="arp-metrics-row compact">
                <Metric label="Duration" value={formatDuration(selected)} />
                <Metric label="Decisions" value={selected.metrics.decision_count} />
                <Metric label="Tools" value={selected.metrics.tool_count} />
                <Metric label="VLM" value={selected.metrics.vlm_call_count} />
                <Metric label="Anchor hit" value={`${Math.round((selected.metrics.anchor_cache_hit_rate || 0) * 100)}%`} />
              </div>
              {selected.error && <div className="arp-inline-error"><XCircle size={14} />{selected.error}</div>}
              <div className="arp-panel-heading"><div><Activity size={15} /><strong>Timeline</strong></div><span>{selected.timeline.length} events</span></div>
              <RunTimeline run={selected} />
            </>
          ) : <EmptyState icon={History} title="Select a run" />}
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
      const wps = await plane.getClient().script<WpsScript>(file);
      loadScript(wpsToFlowScript(wps));
      openStudio();
    } catch (skillError) { setError(skillError instanceof Error ? skillError.message : String(skillError)); }
    finally { setBusy(false); }
  };

  const replay = async (file: string) => {
    setBusy(true); setError('');
    try { await plane.getClient().replay(file); await plane.refresh(); }
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
      setError('Enter the workflow the apprentice should learn.');
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
      await plane.getClient().verifyMemory(item.id, `Human verified apprentice replay for: ${item.task || item.id}`);
      await refresh();
    } catch (verifyError) { setError(verifyError instanceof Error ? verifyError.message : String(verifyError)); }
    finally { setBusy(false); }
  };

  return (
    <div className="arp-page arp-assets-page">
      <div className="arp-page-title"><div><span className="arp-kicker">Replay</span><h2>Skills and learning</h2></div></div>
      {error && <div className="arp-inline-error"><XCircle size={14} />{error}</div>}
      <div className="arp-replay-toolbar">
        <div className="arp-replay-modes" role="tablist" aria-label="Replay mode">
          <button className={replayMode === 'offline_graph' ? 'active' : ''} onClick={() => setReplayMode('offline_graph')} role="tab" aria-selected={replayMode === 'offline_graph'} disabled={recording}><Workflow size={14} />Offline graph</button>
          <button className={replayMode === 'skill' ? 'active' : ''} onClick={() => setReplayMode('skill')} role="tab" aria-selected={replayMode === 'skill'} disabled={recording}><RefreshCw size={14} />Skill replay</button>
          <button className={replayMode === 'apprentice' ? 'active' : ''} onClick={() => setReplayMode('apprentice')} role="tab" aria-selected={replayMode === 'apprentice'}><GraduationCap size={14} />Apprentice</button>
        </div>
        {replayMode === 'apprentice' && (
          <div className="arp-apprentice-control">
            <input value={apprenticeTask} onChange={(event) => setApprenticeTask(event.target.value)} placeholder="Workflow to learn" disabled={recording || busy} aria-label="Workflow to learn" />
            <button className={`arp-button ${recording ? 'arp-button-danger' : 'arp-button-primary'}`} onClick={toggleRecording} disabled={busy || (!recording && !apprenticeTask.trim())}>{recording ? <CircleStop size={14} /> : <Radio size={14} />}{recording ? 'Finish lesson' : 'Start lesson'}</button>
          </div>
        )}
      </div>
      <div className="arp-assets-grid">
        <section className="arp-panel">
          <div className="arp-panel-heading"><div><Library size={15} /><strong>Reusable skills</strong></div><span>{scripts.length}</span></div>
          <div className="arp-asset-list">
            {scripts.map((item) => (
              <div className="arp-asset-row" key={item.file}>
                <div className="arp-asset-icon"><FileCode2 size={16} /></div>
                <div><strong>{item.name || item.file}</strong><span>{item.steps} steps · {item.task || 'Recorded workflow'}</span></div>
                <button className="arp-icon-button" title="Open in ARP Studio" onClick={() => openSkill(item.file)} disabled={busy}><Braces size={15} /></button>
                {replayMode === 'offline_graph' && <button className="arp-button arp-button-small" onClick={() => openSkill(item.file)} disabled={busy}><Workflow size={13} />Open graph</button>}
                {replayMode === 'skill' && <button className="arp-button arp-button-small" onClick={() => replay(item.file)} disabled={busy}><Play size={13} />Replay</button>}
                {replayMode === 'apprentice' && <button className="arp-button arp-button-small" onClick={() => openSkill(item.file)} disabled={busy}><GraduationCap size={13} />Review</button>}
              </div>
            ))}
            {!scripts.length && <EmptyState icon={Library} title="No reusable skills" />}
          </div>
        </section>
        <section className="arp-panel">
          <div className="arp-panel-heading"><div><Database size={15} /><strong>Exploration traces</strong></div><span>{traces.length}</span></div>
          <div className="arp-asset-list">
            {traces.map((item) => (
              <div className="arp-asset-row" key={item.run_id}>
                <div className="arp-asset-icon"><TerminalSquare size={16} /></div>
                <div><strong>{item.task || item.run_id}</strong><span>{item.steps} steps · {item.status}</span></div>
                <button className="arp-button arp-button-small" onClick={() => exportTrace(item.run_id)} disabled={busy}><Save size={13} />Extract skill</button>
              </div>
            ))}
            {!traces.length && <EmptyState icon={Database} title="No exploration traces" />}
          </div>
        </section>
        <section className="arp-panel arp-memory-assets">
          <div className="arp-panel-heading"><div><BrainCircuit size={15} /><strong>Memory graph</strong></div><span>{memories.length}</span></div>
          <div className="arp-asset-list">
            {memories.map((item) => (
              <div className="arp-asset-row" key={item.id}>
                <div className="arp-asset-icon"><BrainCircuit size={16} /></div>
                <div>
                  <strong>{item.task || item.id}</strong>
                  <span>{item.status || (item.provisional ? 'provisional' : 'active')} / {item.successes || 0}:{item.uses || 0} success / {Math.round((item.confidence || 0) * 100)}% confidence{item.app ? ` / ${item.app}` : ''}</span>
                </div>
                {item.script && <button className="arp-icon-button" title="Open linked skill in ARP Studio" onClick={() => openSkill(item.script!)} disabled={busy}><Braces size={15} /></button>}
                {item.script && item.provisional && <button className="arp-button arp-button-small" onClick={() => verifyMemory(item)} disabled={busy}><Check size={13} />Verify</button>}
                {item.script && !item.provisional && <button className="arp-button arp-button-small" onClick={() => replay(item.script!)} disabled={busy}><Play size={13} />Replay</button>}
              </div>
            ))}
            {!memories.length && <EmptyState icon={BrainCircuit} title="No reusable memories" />}
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

  useEffect(() => {
    if (!plane.baseUrl) return;
    const client = plane.getClient();
    Promise.all([client.config(), client.chatgptIntegration()])
      .then(([nextConfig, integration]) => { setConfig(nextConfig); setChatgptBridge(integration); })
      .catch((loadError) => setError(String(loadError)));
  }, [plane.baseUrl]);

  const patch = (section: string, key: string, value: unknown) => setConfig((current) => current ? { ...current, [section]: { ...current[section], [key]: value } } : current);
  const save = async () => {
    if (!config) return;
    setSaving(true); setError('');
    try {
      const next = await plane.getClient().patchConfig({ api: config.api, vlm: config.vlm, perception: config.perception, fusion: config.fusion, agent: config.agent, ocr: config.ocr, integrations: config.integrations });
      setConfig(next);
      setChatgptBridge(await plane.getClient().chatgptIntegration());
      setSaved(true); setTimeout(() => setSaved(false), 1800);
    } catch (saveError) { setError(saveError instanceof Error ? saveError.message : String(saveError)); }
    finally { setSaving(false); }
  };

  return (
    <div className="arp-page arp-system-page">
      <div className="arp-page-title"><div><span className="arp-kicker">Control plane</span><h2>System</h2></div><button className="arp-button arp-button-primary" onClick={save} disabled={!config || saving}>{saving ? <Loader2 size={14} className="arp-spin" /> : saved ? <Check size={14} /> : <Save size={14} />}{saved ? 'Saved' : 'Save configuration'}</button></div>
      {error && <div className="arp-inline-error"><XCircle size={14} />{error}</div>}
      <div className="arp-system-grid">
        <section className="arp-panel arp-config-panel">
          <div className="arp-panel-heading"><div><Bot size={15} /><strong>Models</strong></div></div>
          {config ? (
            <div className="arp-form-grid">
              <label><span>Provider</span><input value={config.api?.provider || ''} onChange={(event) => patch('api', 'provider', event.target.value)} /></label>
              <label><span>Main model</span><input value={config.api?.model || ''} onChange={(event) => patch('api', 'model', event.target.value)} /></label>
              <label className="wide"><span>API base URL</span><input value={config.api?.base_url || ''} onChange={(event) => patch('api', 'base_url', event.target.value)} /></label>
              <label className="wide"><span>API key</span><input type="password" value={config.api?.api_key || ''} onChange={(event) => patch('api', 'api_key', event.target.value)} /></label>
              <label><span>VLM model</span><input value={config.vlm?.model || ''} onChange={(event) => patch('vlm', 'model', event.target.value)} /></label>
              <label><span>VLM base URL</span><input value={config.vlm?.base_url || ''} onChange={(event) => patch('vlm', 'base_url', event.target.value)} /></label>
              <Toggle checked={Boolean(config.vlm?.enabled)} onChange={(value) => patch('vlm', 'enabled', value)} label="Strong VLM fallback" />
              <Toggle checked={Boolean(config.agent?.verify_done)} onChange={(value) => patch('agent', 'verify_done', value)} label="Evidence done gate" />
            </div>
          ) : <EmptyState icon={Loader2} title="Loading configuration" />}
        </section>
        <section className="arp-panel arp-config-panel">
          <div className="arp-panel-heading"><div><Eye size={15} /><strong>Perception ladder</strong></div></div>
          {config && <div className="arp-toggle-list">
            {['uia', 'win32', 'ocr', 'cv', 'vlm'].map((dimension, index) => (
              <div className="arp-perception-row" key={dimension}><span className="arp-level">L{index}</span><Toggle label={dimension.toUpperCase()} checked={Boolean(config.perception?.[dimension]?.enabled)} onChange={(value) => setConfig((current) => current ? { ...current, perception: { ...current.perception, [dimension]: { ...current.perception?.[dimension], enabled: value } } } : current)} /><code>P{config.perception?.[dimension]?.priority || index + 1}</code></div>
            ))}
            <Toggle checked={Boolean(config.fusion?.screen_watch_autostart)} onChange={(value) => patch('fusion', 'screen_watch_autostart', value)} label="Screen watcher autostart" />
            <Toggle checked={Boolean(config.fusion?.ocr_skip_when_uia_rich)} onChange={(value) => patch('fusion', 'ocr_skip_when_uia_rich', value)} label="Skip OCR on rich UIA" />
            <Toggle checked={Boolean(config.fusion?.vlm_skip_when_uia_rich)} onChange={(value) => patch('fusion', 'vlm_skip_when_uia_rich', value)} label="Skip VLM on rich UIA" />
          </div>}
        </section>
        <section className="arp-panel arp-config-panel">
          <div className="arp-panel-heading"><div><Gauge size={15} /><strong>Runtime</strong></div><StatusPill status={plane.connection === 'online' ? 'running' : 'failed'} label={plane.connection} /></div>
          <dl className="arp-diagnostics">
            <div><dt>Control plane</dt><dd>{plane.baseUrl || 'not connected'}</dd></div>
            <div><dt>API contract</dt><dd>{plane.overview?.contracts?.arp || '-'}</dd></div>
            <div><dt>Capabilities</dt><dd>{plane.capabilities.length}</dd></div>
            <div><dt>Current run</dt><dd>{plane.currentRun?.run_id || 'idle'}</dd></div>
            <div><dt>World model</dt><dd>{String((plane.overview?.status as any)?.world_model ?? config?.agent?.world_model ?? '-')}</dd></div>
            <div><dt>VLM configured</dt><dd>{String((plane.overview?.status as any)?.vlm_configured ?? '-')}</dd></div>
          </dl>
          <div className="arp-theme-control"><span>Theme</span><div>{(['light', 'dark', 'system'] as const).map((mode) => <button key={mode} className={themeMode === mode ? 'active' : ''} onClick={() => setUI({ themeMode: mode })}>{mode === 'light' ? <Sun size={13} /> : mode === 'dark' ? <Moon size={13} /> : <Settings size={13} />}{mode}</button>)}</div></div>
        </section>
        <section className="arp-panel arp-config-panel">
          <div className="arp-panel-heading"><div><Network size={15} /><strong>ChatGPT desktop</strong></div><StatusPill status={chatgptBridge?.client_active ? 'running' : chatgptBridge?.server_ready ? 'idle' : 'failed'} label={chatgptBridge?.client_active ? 'MCP active' : chatgptBridge?.server_ready ? 'Endpoint ready' : 'offline'} /></div>
          <dl className="arp-diagnostics">
            <div><dt>Planner</dt><dd>{chatgptBridge?.planner || 'current desktop client'}</dd></div>
            <div><dt>Client activity</dt><dd>{chatgptBridge?.client_active ? `${chatgptBridge.request_count} requests` : 'no recent client'}</dd></div>
            <div><dt>Transport</dt><dd>{chatgptBridge?.transport || 'mcp_streamable_http'}</dd></div>
            <div><dt>Endpoint</dt><dd title={chatgptBridge?.endpoint}>{chatgptBridge?.endpoint || '-'}</dd></div>
            <div><dt>Security</dt><dd>{chatgptBridge?.security_mode || 'loopback_only'}</dd></div>
            <div><dt>OpenAI API key</dt><dd>{chatgptBridge?.requires_openai_api_key ? 'required' : 'not used'}</dd></div>
            <div><dt>Tools</dt><dd>{chatgptBridge?.tool_count ?? 0}</dd></div>
          </dl>
          {config && <div className="arp-toggle-list arp-bridge-toggles">
            <Toggle checked={Boolean(config.integrations?.chatgpt_desktop?.enabled)} onChange={(value) => setConfig((current) => current ? { ...current, integrations: { ...current.integrations, chatgpt_desktop: { ...current.integrations?.chatgpt_desktop, enabled: value } } } : current)} label="Desktop MCP bridge" />
            <Toggle checked={Boolean(config.integrations?.chatgpt_desktop?.allow_desktop_mutation)} onChange={(value) => setConfig((current) => current ? { ...current, integrations: { ...current.integrations, chatgpt_desktop: { ...current.integrations?.chatgpt_desktop, allow_desktop_mutation: value } } } : current)} label="Allow controller actions" />
          </div>}
        </section>
        <section className="arp-panel arp-capability-panel">
          <div className="arp-panel-heading"><div><Zap size={15} /><strong>Capability registry</strong></div><span>{plane.capabilities.length}</span></div>
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
        <div className="arp-brand"><div className="arp-brand-mark"><Bot size={18} /></div><div><strong>WinPilot</strong><span>Agent Runtime Platform</span></div></div>
        <nav>
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            return <button key={item.id} aria-label={item.label} title={item.label} className={currentView === item.id ? 'active' : ''} onClick={() => setView(item.id)}><Icon size={16} /><span>{item.label}</span>{currentView === item.id && <ChevronRight size={13} />}</button>;
          })}
        </nav>
        <div className="arp-sidebar-status">
          <span className={`arp-connection-dot ${plane.connection}`} />
          <div><strong>{plane.connection}</strong><span>{plane.baseUrl ? new URL(plane.baseUrl).host : 'starting runtime'}</span></div>
        </div>
      </aside>
      <main className="arp-workspace">
        <header className="arp-topbar">
          <div className="arp-breadcrumb"><span>Workspace</span><ChevronRight size={13} /><strong>{NAV_ITEMS.find((item) => item.id === currentView)?.label}</strong></div>
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
