import type {
  ArpExecutionSnapshot,
  ArpGraph,
  CapabilitySpec,
  ChatGPTDesktopIntegration,
  CurrentRunResponse,
  GraphValidation,
  PlatformOverview,
  RunProjection,
  RuntimeEvent,
} from './contracts';

const FALLBACK_URL = 'http://127.0.0.1:8765/';

export function normalizeBaseUrl(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return FALLBACK_URL;
  return trimmed.endsWith('/') ? trimmed : `${trimmed}/`;
}

export function websocketUrl(baseUrl: string): string {
  const url = new URL('ws/events', normalizeBaseUrl(baseUrl));
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  return url.toString();
}

export async function resolveControlPlaneUrl(): Promise<string> {
  if (typeof window !== 'undefined' && window.electronAPI) {
    const ensured = await window.electronAPI.ensureWinpilot?.();
    if (ensured?.url) return normalizeBaseUrl(ensured.url);
    const existing = await window.electronAPI.getWinpilotUrl?.();
    if (existing) return normalizeBaseUrl(existing);
    if (ensured?.error) throw new Error(ensured.error);
  }
  return FALLBACK_URL;
}

async function readJson<T>(response: Response): Promise<T> {
  const text = await response.text();
  let data: any = {};
  if (text) {
    try { data = JSON.parse(text); } catch { data = { detail: text }; }
  }
  if (!response.ok) {
    const detail = data?.detail ?? data?.error ?? data?.message ?? `${response.status} ${response.statusText}`;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return data as T;
}

export class ControlPlaneClient {
  readonly baseUrl: string;

  constructor(baseUrl: string) {
    this.baseUrl = normalizeBaseUrl(baseUrl);
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(new URL(path.replace(/^\//, ''), this.baseUrl), {
      ...init,
      headers: {
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
        ...init?.headers,
      },
    });
    return readJson<T>(response);
  }

  overview(): Promise<PlatformOverview> {
    return this.request('api/v2/platform');
  }

  async capabilities(): Promise<CapabilitySpec[]> {
    const response = await this.request<{ ok: boolean; capabilities: CapabilitySpec[] }>('api/v2/capabilities');
    return response.capabilities;
  }

  currentRun(): Promise<CurrentRunResponse> {
    return this.request('api/v2/runs/current');
  }

  async runs(limit = 50): Promise<RunProjection[]> {
    const response = await this.request<{ ok: boolean; runs: RunProjection[] }>(`api/v2/runs?limit=${limit}`);
    return response.runs;
  }

  config(): Promise<Record<string, any>> {
    return this.request('api/config');
  }

  patchConfig(patch: Record<string, unknown>): Promise<Record<string, any>> {
    return this.request('api/config', { method: 'PATCH', body: JSON.stringify(patch) });
  }

  chatgptIntegration(): Promise<ChatGPTDesktopIntegration> {
    return this.request('api/v2/integrations/chatgpt');
  }

  traces(): Promise<Array<{ run_id: string; task: string; status: string; steps: number }>> {
    return this.request('api/traces');
  }

  scripts(): Promise<Array<{ file: string; name: string; task: string; steps: number }>> {
    return this.request('api/scripts');
  }

  memories(): Promise<Array<{
    id: string;
    task: string;
    app?: string;
    script?: string;
    status?: string;
    confidence?: number;
    uses?: number;
    successes?: number;
    provisional?: boolean;
  }>> {
    return this.request('api/memory');
  }

  script<T = Record<string, unknown>>(file: string): Promise<T> {
    return this.request(`api/script/${encodeURIComponent(file)}`);
  }

  replay(script: string): Promise<Record<string, unknown>> {
    return this.request('api/replay', { method: 'POST', body: JSON.stringify({ script }) });
  }

  exportTrace(runId: string): Promise<{ ok: boolean; script: string }> {
    return this.request(`api/export/${encodeURIComponent(runId)}`, { method: 'POST' });
  }

  humanStatus(): Promise<{ recording: boolean }> {
    return this.request('api/human/status');
  }

  humanStart(): Promise<Record<string, unknown>> {
    return this.request('api/human/start', { method: 'POST', body: '{}' });
  }

  humanStop(): Promise<Record<string, unknown>> {
    return this.request('api/human/stop', { method: 'POST' });
  }

  replayModes(): Promise<{ ok: boolean; modes: Array<Record<string, unknown>> }> {
    return this.request('api/v2/replay/modes');
  }

  apprenticeStatus(): Promise<{
    mode: 'apprentice';
    recording: boolean;
    session_id?: string | null;
    task?: string;
    app?: string;
    started_at?: number | null;
  }> {
    return this.request('api/v2/replay/apprentice/status');
  }

  apprenticeStart(task: string, app = ''): Promise<Record<string, any>> {
    return this.request('api/v2/replay/apprentice/start', {
      method: 'POST',
      body: JSON.stringify({ task, app }),
    });
  }

  apprenticeStop(): Promise<Record<string, any>> {
    return this.request('api/v2/replay/apprentice/stop', { method: 'POST' });
  }

  verifyMemory(memoryId: string, evidence: string): Promise<Record<string, any>> {
    return this.request(`api/memory/${encodeURIComponent(memoryId)}/verify`, {
      method: 'POST',
      body: JSON.stringify({ evidence, status: 'verified' }),
    });
  }

  traceScreenshotUrl(runId: string, path: string): string {
    const name = path.replace(/\\/g, '/').split('/').pop() || path;
    return new URL(`api/trace/${encodeURIComponent(runId)}/shot/${encodeURIComponent(name)}`, this.baseUrl).toString();
  }

  validateGraph(graph: ArpGraph): Promise<GraphValidation> {
    return this.request('api/v2/arp/validate', { method: 'POST', body: JSON.stringify(graph) });
  }

  async startGraph(graph: ArpGraph, inputs: Record<string, unknown> = {}): Promise<ArpExecutionSnapshot> {
    const response = await this.request<{ ok: boolean; run_id: string; run: ArpExecutionSnapshot }>('api/v2/arp/runs', {
      method: 'POST',
      body: JSON.stringify({ graph, inputs }),
    });
    return response.run;
  }

  async arpRun(runId: string): Promise<ArpExecutionSnapshot> {
    const response = await this.request<{ ok: boolean; run: ArpExecutionSnapshot }>(`api/v2/arp/runs/${encodeURIComponent(runId)}`);
    return response.run;
  }

  async cancelRun(runId: string): Promise<ArpExecutionSnapshot> {
    const response = await this.request<{ ok: boolean; run: ArpExecutionSnapshot }>(`api/v2/arp/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' });
    return response.run;
  }

  gate(runId: string, nodeId: string, approved: boolean, comment = ''): Promise<{ ok: boolean }> {
    return this.request(`api/v2/arp/runs/${encodeURIComponent(runId)}/gate`, {
      method: 'POST',
      body: JSON.stringify({ node_id: nodeId, approved, comment }),
    });
  }

  connectEvents(onEvent: (event: RuntimeEvent) => void, onState?: (connected: boolean) => void): () => void {
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let reconnectAttempt = 0;
    let disposed = false;

    const scheduleReconnect = () => {
      if (disposed || reconnectTimer) return;
      const delay = Math.min(500 * 2 ** reconnectAttempt, 5000);
      reconnectAttempt += 1;
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        connect();
      }, delay);
    };

    const connect = () => {
      if (disposed) return;
      socket = new WebSocket(websocketUrl(this.baseUrl));
      socket.addEventListener('open', () => {
        reconnectAttempt = 0;
        onState?.(true);
      });
      socket.addEventListener('close', () => {
        onState?.(false);
        scheduleReconnect();
      });
      socket.addEventListener('error', () => onState?.(false));
      socket.addEventListener('message', (message) => {
        try { onEvent(JSON.parse(String(message.data)) as RuntimeEvent); } catch { /* ignore malformed telemetry */ }
      });
    };

    connect();
    return () => {
      disposed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socket?.close();
    };
  }
}

export function createGoalGraph(goal: string, maxSteps = 30): ArpGraph {
  const timestamp = Date.now();
  return {
    format: 'arp/1',
    id: `goal-${timestamp}`,
    name: goal.slice(0, 80) || 'Agent goal',
    description: 'Memory-first goal run created by the Agent Cockpit.',
    nodes: [
      { id: 'start', kind: 'start', name: 'Start', config: {}, retry: { max_attempts: 1, delay_ms: 0, backoff: 1 }, disabled: false, x: 0, y: 0 },
      { id: 'skill', kind: 'skill_replay', name: 'Reuse verified skill', config: { task: goal }, retry: { max_attempts: 1, delay_ms: 0, backoff: 1 }, disabled: false, x: 0, y: 120 },
      { id: 'goal', kind: 'agent_goal', name: 'Agent exception handler', config: { goal, max_steps: maxSteps }, retry: { max_attempts: 1, delay_ms: 0, backoff: 1 }, disabled: false, x: 180, y: 240 },
      { id: 'end', kind: 'end', name: 'Done', config: {}, retry: { max_attempts: 1, delay_ms: 0, backoff: 1 }, disabled: false, x: 0, y: 360 },
    ],
    edges: [
      { id: 'start-skill', source: 'start', target: 'skill', branch: 'next', label: '' },
      { id: 'skill-end', source: 'skill', target: 'end', branch: 'next', label: 'reused' },
      { id: 'skill-miss-goal', source: 'skill', target: 'goal', branch: 'miss', label: 'explore' },
      { id: 'skill-fail-goal', source: 'skill', target: 'goal', branch: 'fail', label: 'recover' },
      { id: 'goal-end', source: 'goal', target: 'end', branch: 'next', label: '' },
    ],
    variables: {},
    policy: { max_node_visits: 20, fail_fast: true, require_evidence_on_done: true, allow_shell: false, allow_desktop_mutation: true },
    metadata: { source: 'agent-cockpit', routing: 'verified-skill-first-agent-on-miss-or-failure' },
  };
}
