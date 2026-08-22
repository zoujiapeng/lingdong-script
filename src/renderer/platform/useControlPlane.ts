import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ControlPlaneClient,
  resolveControlPlaneConnection,
  runtimeCompatibilityNotice,
} from './client';
import type {
  ArpExecutionSnapshot,
  CapabilitySpec,
  GoalRunStart,
  GraphValidation,
  PlatformOverview,
  RunProjection,
  RuntimeEvent,
} from './contracts';
import type { AutomationIR } from '../utils/automationIR';
import type { FlowFragmentRequestContext } from '../utils/flowFragmentProposal';
import { taskbarNotificationForTransition, type TaskbarRunState } from './taskbarNotification';

export type ConnectionState = 'connecting' | 'online' | 'degraded' | 'offline';

export const CONTROL_PLANE_FALLBACK_REFRESH_MS = 5_000;
export const CONTROL_PLANE_EVENT_REFRESH_MS = 80;
export const CONTROL_PLANE_RECONNECT_MS = 500;

export function useControlPlane() {
  const clientRef = useRef<ControlPlaneClient | null>(null);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const taskbarRunState = useRef<TaskbarRunState | null>(null);
  const liveStartedRuns = useRef(new Set<string>());
  const [connection, setConnection] = useState<ConnectionState>('connecting');
  const [baseUrl, setBaseUrl] = useState('');
  const [overview, setOverview] = useState<PlatformOverview | null>(null);
  const [capabilities, setCapabilities] = useState<CapabilitySpec[]>([]);
  const [currentRun, setCurrentRun] = useState<RunProjection | null>(null);
  const [arpSnapshot, setArpSnapshot] = useState<ArpExecutionSnapshot | null>(null);
  const [runs, setRuns] = useState<RunProjection[]>([]);
  const [events, setEvents] = useState<RuntimeEvent[]>([]);
  const [error, setError] = useState('');
  const [runtimeNotice, setRuntimeNotice] = useState('');

  const refresh = useCallback(async () => {
    const client = clientRef.current;
    if (!client) return;
    try {
      const [platform, current, recent] = await Promise.all([
        client.overview(),
        client.currentRun(),
        client.runs(40),
      ]);
      setOverview(platform);
      setCapabilities(platform.capabilities || []);
      setCurrentRun(current.run);
      setArpSnapshot(current.arp);
      setRuns(recent);
      setRuntimeNotice(runtimeCompatibilityNotice(platform));
      setError('');
      setConnection((state) => state === 'online' ? state : 'degraded');
      return true;
    } catch (refreshError) {
      setError(refreshError instanceof Error ? refreshError.message : String(refreshError));
      setConnection('offline');
      return false;
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
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let connecting = false;
    const scheduleReconnect = () => {
      if (disposed || reconnectTimer) return;
      reconnectTimer = setTimeout(() => {
        reconnectTimer = undefined;
        void connect();
      }, CONTROL_PLANE_RECONNECT_MS);
    };
    const connect = async () => {
      if (disposed || connecting) return;
      connecting = true;
      try {
        const connection = await resolveControlPlaneConnection();
        if (disposed) return;
        const client = new ControlPlaneClient(connection.url, connection.token);
        clientRef.current = client;
        const refreshed = await refresh();
        if (disposed) return;
        if (!refreshed) {
          clientRef.current = null;
          setBaseUrl('');
          scheduleReconnect();
          return;
        }
        setBaseUrl(connection.url);
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
        interval = setInterval(async () => {
          if (await refresh()) return;
          disconnect?.();
          disconnect = undefined;
          if (interval) clearInterval(interval);
          interval = undefined;
          clientRef.current = null;
          setBaseUrl('');
          scheduleReconnect();
        }, CONTROL_PLANE_FALLBACK_REFRESH_MS);
      } catch (connectError) {
        if (!disposed) {
          setError(connectError instanceof Error ? connectError.message : String(connectError));
          setConnection('offline');
          clientRef.current = null;
          setBaseUrl('');
          scheduleReconnect();
        }
      } finally {
        connecting = false;
      }
    };
    void connect();
    return () => {
      disposed = true;
      disconnect?.();
      if (interval) clearInterval(interval);
      if (reconnectTimer) clearTimeout(reconnectTimer);
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

  const startGoal = useCallback(async (
    goal: string,
    threadId = '',
  ): Promise<GoalRunStart> => {
    const client = requireClient();
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
    const run = await requireClient().startAutomation(automation, inputs);
    await refresh();
    return run;
  }, [refresh]);

  const proposeFlowFragment = useCallback((
    goal: string,
    context: FlowFragmentRequestContext,
    signal?: AbortSignal,
  ) => requireClient().proposeFlowFragment(goal, context, signal), []);

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

  // Global F7/F8 hotkeys arrive from the main process via the preload
  // bridge.  The run state is read from a ref so the handler never goes
  // stale between renders.
  const controlRef = useRef<{
    currentRun: RunProjection | null;
    paused: () => void;
    stop: () => void;
  } | null>(null);
  controlRef.current = {
    currentRun,
    paused: () => {
      if (currentRun) {
        void pauseCurrent();
      }
    },
    stop: () => {
      if (currentRun) {
        void cancelCurrent();
      }
    },
  };

  useEffect(() => {
    const onAgentControl = window.electronAPI?.onAgentControl;
    if (!onAgentControl) return undefined;
    const unsubscribe = onAgentControl((action) => {
      const control = controlRef.current;
      if (!control?.currentRun) return;
      if (action === 'toggle_pause') {
        void control.paused();
      } else if (action === 'stop') {
        void control.stop();
      }
    });
    return unsubscribe;
  }, []);

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
    runtimeNotice,
    refresh,
    startGoal,
    startAutomation,
    proposeFlowFragment,
    validateAutomation,
    cancelCurrent,
    pauseCurrent,
    resumeCurrent,
    getClient: requireClient,
  };
}
