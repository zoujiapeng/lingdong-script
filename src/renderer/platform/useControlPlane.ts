import { useCallback, useEffect, useRef, useState } from 'react';
import { ControlPlaneClient, createGoalGraph, resolveControlPlaneUrl } from './client';
import type { ArpGraph, CapabilitySpec, GraphValidation, PlatformOverview, RunProjection, RuntimeEvent } from './contracts';

export type ConnectionState = 'connecting' | 'online' | 'degraded' | 'offline';

export function useControlPlane() {
  const clientRef = useRef<ControlPlaneClient | null>(null);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [connection, setConnection] = useState<ConnectionState>('connecting');
  const [baseUrl, setBaseUrl] = useState('');
  const [overview, setOverview] = useState<PlatformOverview | null>(null);
  const [capabilities, setCapabilities] = useState<CapabilitySpec[]>([]);
  const [currentRun, setCurrentRun] = useState<RunProjection | null>(null);
  const [arpSnapshot, setArpSnapshot] = useState<Record<string, unknown> | null>(null);
  const [runs, setRuns] = useState<RunProjection[]>([]);
  const [events, setEvents] = useState<RuntimeEvent[]>([]);
  const [error, setError] = useState('');

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
        const url = await resolveControlPlaneUrl();
        if (disposed) return;
        const client = new ControlPlaneClient(url);
        clientRef.current = client;
        setBaseUrl(url);
        await refresh();
        if (disposed) return;
        disconnect = client.connectEvents((event) => {
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

  const requireClient = () => {
    if (!clientRef.current) throw new Error('Control plane is not connected.');
    return clientRef.current;
  };

  const startGoal = useCallback(async (goal: string, maxSteps = 30) => {
    const run = await requireClient().startGraph(createGoalGraph(goal, maxSteps));
    await refresh();
    return run;
  }, [refresh]);

  const startGraph = useCallback(async (graph: ArpGraph, inputs: Record<string, unknown> = {}) => {
    const run = await requireClient().startGraph(graph, inputs);
    await refresh();
    return run;
  }, [refresh]);

  const validateGraph = useCallback((graph: ArpGraph): Promise<GraphValidation> => requireClient().validateGraph(graph), []);

  const cancelCurrent = useCallback(async () => {
    if (!currentRun) return;
    await requireClient().cancelRun(currentRun.run_id);
    await refresh();
  }, [currentRun, refresh]);

  const decideGate = useCallback(async (approved: boolean, comment = '') => {
    const nodeId = String(arpSnapshot?.current_node_id || currentRun?.current_node_id || '');
    if (!currentRun || !nodeId) throw new Error('No active human gate.');
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
    refresh,
    startGoal,
    startGraph,
    validateGraph,
    cancelCurrent,
    decideGate,
    getClient: requireClient,
  };
}
