import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ControlPlaneClient,
  resolveControlPlaneConnection,
  runtimeCompatibilityError,
} from './client';
import {
  buildClientRunInputs,
  type ExplicitDesktopHandoffDescriptor,
} from './executionContext';
import type {
  ArpExecutionSnapshot,
  ArpGraph,
  CapabilitySpec,
  GraphValidation,
  PlatformOverview,
  RunProjection,
  RuntimeEvent,
  RuntimeVersion,
} from './contracts';
import type { AutomationIR } from '../utils/automationIR';
import { taskbarNotificationForTransition, type TaskbarRunState } from './taskbarNotification';

export type ConnectionState = 'connecting' | 'online' | 'degraded' | 'offline';

export const CONTROL_PLANE_FALLBACK_REFRESH_MS = 5_000;
export const CONTROL_PLANE_EVENT_REFRESH_MS = 80;

const ONE_ATTEMPT = {
  max_attempts: 1,
  delay_ms: 0,
  backoff: 1,
} as const;

function deterministicGoalKey(goal: string): string {
  let hash = 0x811c9dc5;
  for (let index = 0; index < goal.length; index += 1) {
    hash = Math.imul(hash ^ goal.charCodeAt(index), 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, '0');
}

export function buildVnextLiveCanaryGraph(goal: string): ArpGraph {
  const fullGoal = goal.trim();
  if (!fullGoal) throw new Error('vNext live canary requires a non-empty full goal.');
  const graphId = `vnext-live-canary-${deterministicGoalKey(fullGoal)}`;
  return {
    format: 'arp/1',
    id: graphId,
    name: fullGoal,
    description: '',
    nodes: [
      {
        id: 'start',
        kind: 'start',
        name: 'start',
        config: {},
        retry: ONE_ATTEMPT,
        disabled: false,
        x: 0,
        y: 0,
      },
      {
        id: 'full-goal',
        kind: 'agent_goal',
        name: 'full goal',
        config: { goal: fullGoal },
        retry: ONE_ATTEMPT,
        disabled: false,
        x: 0,
        y: 100,
      },
      {
        id: 'end',
        kind: 'end',
        name: 'end',
        config: {},
        retry: ONE_ATTEMPT,
        disabled: false,
        x: 0,
        y: 200,
      },
    ],
    edges: [
      {
        id: 'start-to-full-goal',
        source: 'start',
        target: 'full-goal',
        branch: 'next',
        label: '',
      },
      {
        id: 'full-goal-to-end',
        source: 'full-goal',
        target: 'end',
        branch: 'next',
        label: '',
      },
    ],
    variables: {},
    policy: {
      max_node_visits: 1,
      fail_fast: true,
      require_evidence_on_done: true,
      allow_shell: false,
      allow_desktop_mutation: true,
    },
    metadata: {},
  };
}

type GoalRunClient = Pick<
  ControlPlaneClient,
  'compileTask' | 'captureDesktopContext' | 'startGraph'
>;

export async function startGoalWithRuntime(
  client: GoalRunClient,
  goal: string,
  maxSteps: number,
  coldStart: boolean,
  runtimeVersion: RuntimeVersion = 'legacy',
): Promise<ArpExecutionSnapshot> {
  const frozenRuntimeVersion = runtimeVersion;
  if (frozenRuntimeVersion === 'legacy') {
    const compiled = await client.compileTask(goal, maxSteps, coldStart);
    const inputs = await buildClientRunInputs(
      compiled.contract,
      Date.now(),
      (handoff) => client.captureDesktopContext(handoff.control_surface_pid),
    );
    return client.startGraph(compiled.graph, inputs, frozenRuntimeVersion);
  }

  const graph = buildVnextLiveCanaryGraph(goal);
  const handoff: ExplicitDesktopHandoffDescriptor = {
    format: 'explicit-desktop-handoff/1',
    task_id: graph.id,
    desktop_handoff_required: true,
  };
  const inputs = await buildClientRunInputs(
    handoff,
    Date.now(),
    (result) => client.captureDesktopContext(result.control_surface_pid),
  );
  return client.startGraph(graph, inputs, frozenRuntimeVersion);
}

export function useControlPlane() {
  const clientRef = useRef<ControlPlaneClient | null>(null);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const taskbarRunState = useRef<TaskbarRunState | null>(null);
  const liveStartedRuns = useRef(new Set<string>());
  const overviewRef = useRef<PlatformOverview | null>(null);
  const runtimeRestarting = useRef(false);
  const [connection, setConnection] = useState<ConnectionState>('connecting');
  const [baseUrl, setBaseUrl] = useState('');
  const [overview, setOverview] = useState<PlatformOverview | null>(null);
  const [capabilities, setCapabilities] = useState<CapabilitySpec[]>([]);
  const [currentRun, setCurrentRun] = useState<RunProjection | null>(null);
  const [arpSnapshot, setArpSnapshot] = useState<ArpExecutionSnapshot | null>(null);
  const [runs, setRuns] = useState<RunProjection[]>([]);
  const [events, setEvents] = useState<RuntimeEvent[]>([]);
  const [error, setError] = useState('');
  const [runtimeIssue, setRuntimeIssue] = useState('控制器运行时身份尚未加载。');

  const refresh = useCallback(async () => {
    const client = clientRef.current;
    if (!client) return;
    try {
      const [platform, current, recent] = await Promise.all([
        client.overview(),
        client.currentRun(),
        client.runs(40),
      ]);
      overviewRef.current = platform;
      setOverview(platform);
      setCapabilities(platform.capabilities || []);
      setCurrentRun(current.run);
      setArpSnapshot(current.arp);
      setRuns(recent);
      const compatibilityIssue = runtimeCompatibilityError(platform);
      setRuntimeIssue(compatibilityIssue);
      if (compatibilityIssue) {
        setError(compatibilityIssue);
        setConnection('degraded');
        const currentStatus = String(current.run?.status || '').toLowerCase();
        const active = ['queued', 'running', 'paused'].includes(currentStatus);
        const restart = window.electronAPI?.restartWinpilot;
        if (!active && restart && !runtimeRestarting.current) {
          runtimeRestarting.current = true;
          try {
            const restarted = await restart();
            if (!restarted?.url || restarted.error) {
              throw new Error(restarted?.error || '控制器重启后未返回连接信息。');
            }
            window.location.reload();
            return;
          } catch (restartError) {
            runtimeRestarting.current = false;
            setError(restartError instanceof Error ? restartError.message : String(restartError));
          }
        }
        return;
      }
      runtimeRestarting.current = false;
      setError('');
      setConnection((state) => state === 'online' ? state : 'degraded');
    } catch (refreshError) {
      setError(refreshError instanceof Error ? refreshError.message : String(refreshError));
      setConnection('offline');
    }
  }, []);

  const refreshLiveRun = useCallback(async () => {
    const client = clientRef.current;
    if (!client) return;
    try {
      const current = await client.currentRun();
      setCurrentRun(current.run);
      setArpSnapshot(current.arp);
      setConnection((state) => state === 'offline' ? 'degraded' : state);
    } catch (refreshError) {
      setError(refreshError instanceof Error ? refreshError.message : String(refreshError));
      setConnection('offline');
    }
  }, []);

  useEffect(() => {
    let disposed = false;
    let disconnect: (() => void) | undefined;
    let interval: ReturnType<typeof setInterval> | undefined;
    const connect = async () => {
      try {
        const connection = await resolveControlPlaneConnection();
        if (disposed) return;
        const client = new ControlPlaneClient(connection.url, connection.token);
        clientRef.current = client;
        setBaseUrl(connection.url);
        await refresh();
        if (disposed) return;
        disconnect = client.connectEvents((event) => {
          if (event.run_id && (event.type === 'agent_start' || event.type === 'arp_run_start')) {
            if (liveStartedRuns.current.size >= 200) liveStartedRuns.current.clear();
            liveStartedRuns.current.add(event.run_id);
          }
          if (event.type === 'user_notification') {
            void window.electronAPI?.notifyTaskbar?.(
              String(event.title || '提醒'),
              String(event.body || ''),
              event.kind === 'error' || event.kind === 'success' ? event.kind : 'info',
              String(event.run_id || ''),
            );
          }
          if (event.type === 'ping') return;
          setEvents((items) => [...items.slice(-199), event]);
          if (refreshTimer.current) clearTimeout(refreshTimer.current);
          const terminal = (
            event.type === 'agent_done'
            || event.type === 'arp_run_done'
            || ['done', 'failed', 'canceled'].includes(String(event.status || event.state || '').toLowerCase())
          );
          refreshTimer.current = setTimeout(
            terminal ? refresh : refreshLiveRun,
            CONTROL_PLANE_EVENT_REFRESH_MS,
          );
        }, (connected) => setConnection(connected ? 'online' : 'degraded'));
        interval = setInterval(refresh, CONTROL_PLANE_FALLBACK_REFRESH_MS);
      } catch (connectError) {
        if (!disposed) {
          setError(connectError instanceof Error ? connectError.message : String(connectError));
          setConnection('offline');
        }
      }
    };
    connect();
    return () => {
      disposed = true;
      disconnect?.();
      if (interval) clearInterval(interval);
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
    };
  }, [refresh, refreshLiveRun]);

  useEffect(() => {
    if (!currentRun) return;
    const current = {
      runId: currentRun.run_id,
      status: String(currentRun.status || '').toLowerCase(),
    };
    const previous = taskbarRunState.current;
    taskbarRunState.current = current;
    const observedLiveStart = liveStartedRuns.current.has(current.runId);
    const notification = taskbarNotificationForTransition(
      previous,
      currentRun,
      observedLiveStart,
    );
    if (!notification) return;
    liveStartedRuns.current.delete(current.runId);
    void window.electronAPI?.notifyTaskbar?.(
      notification.title,
      notification.body,
      notification.kind,
      notification.runId,
    );
  }, [currentRun]);

  const requireClient = () => {
    if (!clientRef.current) throw new Error('控制平面尚未连接。');
    return clientRef.current;
  };

  const requireCompatibleClient = () => {
    const compatibilityIssue = runtimeCompatibilityError(overviewRef.current);
    if (compatibilityIssue) {
      throw new Error(`${compatibilityIssue} 任务未启动。`);
    }
    return requireClient();
  };

  const startGoal = useCallback(async (
    goal: string,
    maxSteps = 30,
    coldStart = false,
    runtimeVersion: RuntimeVersion = 'legacy',
  ) => {
    const client = requireCompatibleClient();
    const run = await startGoalWithRuntime(
      client,
      goal,
      maxSteps,
      coldStart,
      runtimeVersion,
    );
    await refresh();
    return run;
  }, [refresh]);

  const startAutomation = useCallback(async (
    automation: AutomationIR,
    inputs: Record<string, unknown> = {},
  ) => {
    const run = await requireCompatibleClient().startAutomation(automation, inputs);
    await refresh();
    return run;
  }, [refresh]);

  const validateAutomation = useCallback(
    (automation: AutomationIR): Promise<GraphValidation> => (
      requireClient().validateAutomation(automation)
    ),
    [],
  );

  const cancelCurrent = useCallback(async () => {
    if (!currentRun) return;
    await requireClient().cancelRun(currentRun.run_id);
    await refresh();
  }, [currentRun, refresh]);

  const pauseCurrent = useCallback(async () => {
    if (!currentRun) return;
    await requireClient().pauseRun(currentRun.run_id);
    await refresh();
  }, [currentRun, refresh]);

  const resumeCurrent = useCallback(async () => {
    if (!currentRun) return;
    await requireClient().resumeRun(currentRun.run_id);
    await refresh();
  }, [currentRun, refresh]);

  const decideGate = useCallback(async (approved: boolean, comment = '') => {
    const nodeId = String(arpSnapshot?.current_node_id || currentRun?.current_node_id || '');
    if (!currentRun || !nodeId) throw new Error('当前没有等待处理的人工确认。');
    await requireClient().gate(currentRun.run_id, nodeId, approved, comment);
    await refresh();
  }, [arpSnapshot, currentRun, refresh]);

  return {
    connection,
    baseUrl,
    overview,
    capabilities,
    currentRun,
    arpSnapshot,
    runs,
    events,
    error,
    runtimeIssue,
    refresh,
    startGoal,
    startAutomation,
    validateAutomation,
    cancelCurrent,
    pauseCurrent,
    resumeCurrent,
    decideGate,
    getClient: requireClient,
  };
}
