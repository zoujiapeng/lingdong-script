import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ControlPlaneClient,
  resolveControlPlaneConnection,
  runtimeCompatibilityError,
} from './client';
import { buildClientExecutionContext } from './executionContext';
import type {
  ArpExecutionSnapshot,
  CapabilitySpec,
  ClientExecutionContext,
  CurrentRunResponse,
  GoalRunStart,
  GraphValidation,
  PlatformOverview,
  RunProjection,
  RuntimeEvent,
} from './contracts';
import type { AutomationIR } from '../utils/automationIR';
import { taskbarNotificationForTransition, type TaskbarRunState } from './taskbarNotification';

export type ConnectionState = 'connecting' | 'online' | 'degraded' | 'offline';

export const CONTROL_PLANE_FALLBACK_REFRESH_MS = 5_000;
export const CONTROL_PLANE_EVENT_REFRESH_MS = 80;

type DesktopLeaseClient = Pick<
  ControlPlaneClient,
  'bindDesktopLease' | 'captureDesktopContext' | 'failDesktopLease' | 'run'
>;

async function releaseDesktopHandoffBestEffort(): Promise<void> {
  try {
    await window.electronAPI?.releaseDesktopHandoff?.();
  } catch {
    // The run/start error remains authoritative even if the UI bridge is gone.
  }
}

export async function fulfillDesktopLease(
  client: DesktopLeaseClient,
  runId: string,
  leaseId: string,
): Promise<void> {
  const canonicalRunId = runId.trim();
  const canonicalLeaseId = leaseId.trim();
  if (!canonicalRunId || !canonicalLeaseId) {
    throw new Error('Desktop lease requires exact run and lease ids.');
  }
  let executionContext: ClientExecutionContext;
  try {
    executionContext = await buildClientExecutionContext(
      Date.now(),
      (result) => client.captureDesktopContext(result.control_surface_pid),
    );
  } catch (error) {
    try {
      await client.failDesktopLease(
        canonicalRunId,
        canonicalLeaseId,
        error instanceof Error ? error.message : String(error),
      );
    } catch {
      // The original handoff failure remains authoritative.
    }
    await releaseDesktopHandoffBestEffort();
    throw error;
  }
  try {
    const bound = await window.electronAPI?.bindDesktopHandoff?.(
      canonicalRunId,
    );
    if (bound === false) {
      throw new Error('Electron rejected the desktop handoff binding.');
    }
  } catch (error) {
    try {
      await client.failDesktopLease(
        canonicalRunId,
        canonicalLeaseId,
        error instanceof Error ? error.message : String(error),
      );
    } catch {
      // No server bind was attempted, so local release remains safe.
    }
    await releaseDesktopHandoffBestEffort();
    throw error;
  }
  try {
    await client.bindDesktopLease(
      canonicalRunId,
      canonicalLeaseId,
      executionContext,
    );
  } catch (error) {
    let exactRun: CurrentRunResponse | null = null;
    try {
      exactRun = await client.run(canonicalRunId);
    } catch {
      // An ambiguous bind must keep the surface yielded: the server may have
      // delivered the lease even though its response was lost.
      throw error;
    }
    const lease = exactRun.arp?.desktop_lease;
    if (
      lease
      && String(lease.run_id || '') === canonicalRunId
      && String(lease.lease_id || '') === canonicalLeaseId
      && String(lease.status || '') === 'delivered'
    ) {
      return;
    }
    const leaseStatus = String(lease?.status || '');
    const runStatus = String(
      exactRun.arp?.status || exactRun.run?.status || '',
    ).toLowerCase();
    const terminal = [
      'done',
      'reply',
      'failed',
      'fail',
      'error',
      'canceled',
      'cancelled',
      'stopped',
      'interrupted',
      'blocked',
    ].includes(runStatus);
    let releaseSafe = terminal || leaseStatus === 'closed';
    if (leaseStatus === 'waiting') {
      try {
        const failed = await client.failDesktopLease(
          canonicalRunId,
          canonicalLeaseId,
          error instanceof Error ? error.message : String(error),
        );
        const failedLease = failed.lease;
        releaseSafe = (
          typeof failedLease === 'object'
          && failedLease !== null
          && String((failedLease as Record<string, unknown>).run_id || '') === canonicalRunId
          && String((failedLease as Record<string, unknown>).lease_id || '') === canonicalLeaseId
          && String((failedLease as Record<string, unknown>).status || '') === 'closed'
        );
      } catch {
        // The original bind may have won the race and delivered the lease.
      }
    }
    if (releaseSafe) {
      await releaseDesktopHandoffBestEffort();
    }
    throw error;
  }
}

export function useControlPlane() {
  const clientRef = useRef<ControlPlaneClient | null>(null);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const taskbarRunState = useRef<TaskbarRunState | null>(null);
  const liveStartedRuns = useRef(new Set<string>());
  const overviewRef = useRef<PlatformOverview | null>(null);
  const runtimeRestarting = useRef(false);
  const desktopLeaseHandled = useRef(new Set<string>());
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

  const handleDesktopLease = useCallback(
    async (runId: string, leaseId: string) => {
      const key = `${runId}:${leaseId}`;
      if (!runId || !leaseId || desktopLeaseHandled.current.has(key)) return;
      if (desktopLeaseHandled.current.size >= 200) {
        desktopLeaseHandled.current.clear();
      }
      desktopLeaseHandled.current.add(key);
      const client = clientRef.current;
      if (!client) {
        desktopLeaseHandled.current.delete(key);
        return;
      }
      try {
        await fulfillDesktopLease(client, runId, leaseId);
      } catch (leaseError) {
        setError(
          leaseError instanceof Error
            ? leaseError.message
            : String(leaseError),
        );
        setConnection('degraded');
      }
    },
    [],
  );

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
          if (event.type === 'desktop_lease_requested') {
            void handleDesktopLease(
              String(event.run_id || ''),
              String(event.lease_id || ''),
            );
          }
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
          if (event.type === 'arp_run_done' && event.run_id) {
            const status = String(event.status || '').toLowerCase();
            const failed = [
              'failed',
              'fail',
              'error',
              'canceled',
              'cancelled',
              'stopped',
              'interrupted',
              'blocked',
            ].includes(status);
            void window.electronAPI?.notifyTaskbar?.(
              failed ? '任务失败' : status === 'reply' ? 'Agent 已回复' : '任务完成',
              String(event.user_result || event.error || event.result || '').slice(0, 220),
              failed ? 'error' : 'success',
              String(event.run_id),
              true,
            );
          }
          if (event.type === 'ping') return;
          setEvents((items) => [...items.slice(-199), event]);
          if (refreshTimer.current) clearTimeout(refreshTimer.current);
          const terminal = (
            event.type === 'agent_done'
            || event.type === 'arp_run_done'
            || [
              'done',
              'completed',
              'success',
              'reply',
              'failed',
              'fail',
              'error',
              'canceled',
              'cancelled',
              'stopped',
              'interrupted',
              'blocked',
            ].includes(String(event.status || event.state || '').toLowerCase())
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
  }, [handleDesktopLease, refresh, refreshLiveRun]);

  useEffect(() => {
    const lease = arpSnapshot?.desktop_lease;
    if (
      !lease
      || typeof lease !== 'object'
      || String(lease.status || '') !== 'waiting'
    ) {
      return;
    }
    void handleDesktopLease(
      String(lease.run_id || arpSnapshot?.run_id || ''),
      String(lease.lease_id || ''),
    );
  }, [arpSnapshot, handleDesktopLease]);

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
      true,
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
    threadId = '',
  ): Promise<GoalRunStart> => {
    const client = requireCompatibleClient();
    const started = await client.startGoalRun(
      goal,
      threadId,
    );
    await refresh();
    return started;
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
