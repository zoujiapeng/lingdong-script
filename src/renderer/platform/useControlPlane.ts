import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ControlPlaneClient,
  resolveControlPlaneConnection,
  runtimeCompatibilityError,
} from './client';
import type { ArpExecutionSnapshot, ArpGraph, CapabilitySpec, GraphValidation, PlatformOverview, RunProjection, RuntimeEvent } from './contracts';
import type { AutomationIR } from '../utils/automationIR';
import { taskbarNotificationForTransition, type TaskbarRunState } from './taskbarNotification';

export type ConnectionState = 'connecting' | 'online' | 'degraded' | 'offline';

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
          if (event.type !== 'ping') setEvents((items) => [...items.slice(-199), event]);
          if (refreshTimer.current) clearTimeout(refreshTimer.current);
          refreshTimer.current = setTimeout(refresh, 60);
        }, (connected) => setConnection(connected ? 'online' : 'degraded'));
        interval = setInterval(refresh, 1000);
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
  }, [refresh]);

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

  const startGoal = useCallback(async (goal: string, maxSteps = 30, coldStart = false) => {
    const client = requireCompatibleClient();
    const compiled = await client.compileTask(goal, maxSteps, coldStart);
    const run = await client.startGraph(compiled.graph);
    await refresh();
    return run;
  }, [refresh]);

  const startGraph = useCallback(async (graph: ArpGraph, inputs: Record<string, unknown> = {}) => {
    const run = await requireCompatibleClient().startGraph(graph, inputs);
    await refresh();
    return run;
  }, [refresh]);

  const validateGraph = useCallback((graph: ArpGraph): Promise<GraphValidation> => requireClient().validateGraph(graph), []);

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
    startGraph,
    validateGraph,
    startAutomation,
    validateAutomation,
    cancelCurrent,
    decideGate,
    getClient: requireClient,
  };
}
