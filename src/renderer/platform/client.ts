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
  AgentSchedule,
  TaskContract,
  DesktopTargetContext,
} from './contracts';
import type { AutomationIR } from '../utils/automationIR';

const FALLBACK_URL = 'http://127.0.0.1:8765/';
const ELECTRON_SERVER_READY_TIMEOUT_MS = 35_000;
const ELECTRON_SERVER_POLL_MS = 200;
export const EXPECTED_RUNTIME_CONTRACT_VERSION = 2;
export const DESKTOP_RUNTIME_VERSIONS = ['legacy', 'vnext'] as const;
export type DesktopRuntimeVersion = typeof DESKTOP_RUNTIME_VERSIONS[number];

export class TaskClarificationRequired extends Error {
  readonly questions: string[];
  readonly contract: TaskContract;
  readonly graph: ArpGraph;

  constructor(questions: string[], contract: TaskContract, graph: ArpGraph) {
    super(`需要补充信息：${questions.join(' ') || '任务存在未解析的必要参数'}`);
    this.name = 'TaskClarificationRequired';
    this.questions = questions;
    this.contract = contract;
    this.graph = graph;
  }
}

export function runtimeCompatibilityError(overview: PlatformOverview | null | undefined): string {
  if (!overview) return '控制器运行时身份尚未加载。';
  const runtime = overview.status?.runtime;
  if (!runtime || Number(runtime.contract_version) !== EXPECTED_RUNTIME_CONTRACT_VERSION) {
    return '托管的 Python 控制器使用了不兼容的运行时协议。';
  }
  const source = runtime.source;
  const git = runtime.git;
  if (
    !source
    || !Object.prototype.hasOwnProperty.call(source, 'loaded_stamp')
    || !Object.prototype.hasOwnProperty.call(source, 'current_stamp')
    || typeof source.drifted !== 'boolean'
    || !git
    || !Object.prototype.hasOwnProperty.call(git, 'loaded_commit')
    || !Object.prototype.hasOwnProperty.call(git, 'current_commit')
  ) {
    return '托管的 Python 控制器缺少运行时身份字段。';
  }
  const loadedStamp = String(source.loaded_stamp || '');
  const currentStamp = String(source.current_stamp || '');
  if (!loadedStamp || !currentStamp) {
    return '托管的 Python 控制器源码身份不完整。';
  }
  if (source.drifted || loadedStamp !== currentStamp) {
    return 'Python 控制器源码在进程启动后发生了变化。';
  }
  const loadedCommit = String(git.loaded_commit || '');
  const currentCommit = String(git.current_commit || '');
  if (loadedCommit && currentCommit && loadedCommit !== currentCommit) {
    return '正在运行的 Python 控制器与当前仓库提交不一致。';
  }
  return '';
}

export function normalizeBaseUrl(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return FALLBACK_URL;
  return trimmed.endsWith('/') ? trimmed : `${trimmed}/`;
}

export interface ControlPlaneConnection {
  url: string;
  token: string;
}

export function websocketUrl(baseUrl: string, token = ''): string {
  const url = new URL('ws/events', normalizeBaseUrl(baseUrl));
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  if (token) url.searchParams.set('token', token);
  return url.toString();
}

export async function resolveControlPlaneConnection(): Promise<ControlPlaneConnection> {
  if (typeof window !== 'undefined' && window.electronAPI) {
    const api = window.electronAPI;
    const canResolveManagedServer = Boolean(api.ensureWinpilot || api.getWinpilotConnection || api.getWinpilotUrl);
    if (canResolveManagedServer) {
      const deadline = Date.now() + ELECTRON_SERVER_READY_TIMEOUT_MS;
      while (Date.now() < deadline) {
        const ensured = await api.ensureWinpilot?.();
        if (ensured?.url) {
          const connection = await api.getWinpilotConnection?.();
          return {
            url: normalizeBaseUrl(connection?.url || ensured.url),
            token: connection?.token || ensured.token || '',
          };
        }
        const connection = await api.getWinpilotConnection?.();
        if (connection?.url) {
          return { url: normalizeBaseUrl(connection.url), token: connection.token || '' };
        }
        const existing = await api.getWinpilotUrl?.();
        if (existing) return { url: normalizeBaseUrl(existing), token: '' };
        if (ensured?.error) throw new Error(ensured.error);
        await new Promise((resolve) => setTimeout(resolve, ELECTRON_SERVER_POLL_MS));
      }
      throw new Error('WinPilot 托管服务在 35 秒内未就绪。');
    }
  }
  return { url: FALLBACK_URL, token: '' };
}

export async function resolveControlPlaneUrl(): Promise<string> {
  return (await resolveControlPlaneConnection()).url;
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
  readonly token: string;

  constructor(baseUrl: string, token = '') {
    this.baseUrl = normalizeBaseUrl(baseUrl);
    this.token = token;
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(new URL(path.replace(/^\//, ''), this.baseUrl), {
      ...init,
      headers: {
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
        ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}),
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

  async captureDesktopContext(excludeProcessId = 0): Promise<DesktopTargetContext> {
    const response = await this.request<{ ok: boolean; target: DesktopTargetContext }>(
      `api/v2/desktop-context?exclude_process_id=${Math.max(0, Math.trunc(excludeProcessId))}`,
    );
    return response.target;
  }

  run(runId: string): Promise<CurrentRunResponse> {
    return this.request(`api/v2/runs/${encodeURIComponent(runId)}`);
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

  async schedules(): Promise<AgentSchedule[]> {
    const response = await this.request<{ ok: boolean; schedules: AgentSchedule[] }>('api/agent-schedules');
    return response.schedules;
  }

  async createSchedule(input: {
    task: string;
    name?: string;
    delay_seconds?: number;
    interval_seconds?: number | null;
    max_steps?: number;
  }): Promise<AgentSchedule> {
    const response = await this.request<{ ok: boolean; schedule: AgentSchedule }>('api/agent-schedules', {
      method: 'POST',
      body: JSON.stringify(input),
    });
    return response.schedule;
  }

  async updateSchedule(id: string, patch: Partial<AgentSchedule> & { delay_seconds?: number }): Promise<AgentSchedule> {
    const response = await this.request<{ ok: boolean; schedule: AgentSchedule }>(`api/agent-schedules/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    });
    return response.schedule;
  }

  cancelSchedule(id: string): Promise<{ ok: boolean; id: string }> {
    return this.request(`api/agent-schedules/${encodeURIComponent(id)}`, { method: 'DELETE' });
  }

  chatgptIntegration(): Promise<ChatGPTDesktopIntegration> {
    return this.request('api/v2/integrations/chatgpt');
  }

  traces(): Promise<Array<{ run_id: string; task: string; status: string; steps: number }>> {
    return this.request('api/traces');
  }

  scripts(): Promise<Array<{
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
    script_sha256?: string;
  }>> {
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
    conditions?: string[];
    evidence?: string[];
    verification?: Array<Record<string, unknown>>;
    applicability?: Record<string, unknown>;
    verification_state?: string;
    script_sha256?: string;
  }>> {
    return this.request('api/memory');
  }

  memoryLineage(memoryId: string): Promise<{
    ok: boolean;
    node: Record<string, any>;
    related: Array<Record<string, any>>;
    attempts: Array<Record<string, any>>;
  }> {
    return this.request(`api/memory/${encodeURIComponent(memoryId)}/lineage`);
  }

  updateMemory(memoryId: string, patch: Record<string, unknown>): Promise<Record<string, any>> {
    return this.request(`api/memory/${encodeURIComponent(memoryId)}`, {
      method: 'PUT',
      body: JSON.stringify(patch),
    });
  }

  retireMemory(memoryId: string): Promise<{ ok: boolean; id: string }> {
    return this.request(`api/memory/${encodeURIComponent(memoryId)}/retire`, { method: 'POST' });
  }

  deleteMemory(memoryId: string): Promise<{ ok: boolean }> {
    return this.request(`api/memory/${encodeURIComponent(memoryId)}`, { method: 'DELETE' });
  }

  sessions(): Promise<Array<{ task: string; status: string; result: string; run_id: string }>> {
    return this.request('api/session');
  }

  clearSessions(): Promise<{ ok: boolean }> {
    return this.request('api/session', { method: 'DELETE' });
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

  apprenticeStart(
    task: string,
    app = '',
    verification: Array<Record<string, unknown>> = [],
  ): Promise<Record<string, any>> {
    return this.request('api/v2/replay/apprentice/start', {
      method: 'POST',
      body: JSON.stringify({ task, app, verification }),
    });
  }

  apprenticeStop(): Promise<Record<string, any>> {
    return this.request('api/v2/replay/apprentice/stop', { method: 'POST' });
  }

  startMemoryVerification(memoryId: string): Promise<{
    ok: boolean;
    job: { replay_id: string; status: string };
  }> {
    return this.request(`api/memory/${encodeURIComponent(memoryId)}/verification-runs`, {
      method: 'POST',
    });
  }

  memoryVerificationStatus(memoryId: string, replayId: string): Promise<Record<string, any>> {
    return this.request(
      `api/memory/${encodeURIComponent(memoryId)}/verification-runs/${encodeURIComponent(replayId)}`,
    );
  }

  traceScreenshotUrl(runId: string, path: string): string {
    const name = path.replace(/\\/g, '/').split('/').pop() || path;
    const url = new URL(`api/trace/${encodeURIComponent(runId)}/shot/${encodeURIComponent(name)}`, this.baseUrl);
    if (this.token) url.searchParams.set('token', this.token);
    return url.toString();
  }

  artifactUrl(runId: string, artifactId: string): string {
    const url = new URL(
      `api/v2/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(artifactId)}`,
      this.baseUrl,
    );
    if (this.token) url.searchParams.set('token', this.token);
    return url.toString();
  }

  validateGraph(graph: ArpGraph): Promise<GraphValidation> {
    return this.request('api/v2/arp/validate', { method: 'POST', body: JSON.stringify(graph) });
  }

  validateAutomation(automation: AutomationIR): Promise<GraphValidation> {
    return this.request('api/v2/automation-ir/validate', {
      method: 'POST',
      body: JSON.stringify(automation),
    });
  }

  async startGraph(
    graph: ArpGraph,
    inputs: Record<string, unknown> = {},
    runtimeVersion: DesktopRuntimeVersion = 'legacy',
  ): Promise<ArpExecutionSnapshot> {
    if (!DESKTOP_RUNTIME_VERSIONS.includes(runtimeVersion)) {
      throw new Error(`不支持的桌面运行时：${String(runtimeVersion)}`);
    }
    const response = await this.request<{ ok: boolean; run_id: string; run: ArpExecutionSnapshot }>('api/v2/arp/runs', {
      method: 'POST',
      body: JSON.stringify({ graph, inputs, runtime_version: runtimeVersion }),
    });
    return response.run;
  }

  async compileTask(task: string, maxSteps = 40, coldStart = false, requireGui = true): Promise<{ contract: TaskContract; graph: ArpGraph }> {
    const response = await this.request<{
      ok: boolean;
      needs_input?: boolean;
      clarification_questions?: string[];
      contract: TaskContract;
      graph: ArpGraph;
    }>('api/v2/task-contracts/compile', {
      method: 'POST',
      body: JSON.stringify({ task, max_steps: maxSteps, cold_start: coldStart, require_gui: requireGui }),
    });
    const questions = response.clarification_questions || response.contract.clarification_questions || [];
    if (response.needs_input || questions.length > 0) {
      throw new TaskClarificationRequired(questions, response.contract, response.graph);
    }
    if (!response.contract.satisfiable) {
      throw new Error(`任务合同不可执行：${response.contract.missing_capabilities.join(', ') || '能力路径未知'}`);
    }
    return { contract: response.contract, graph: response.graph };
  }

  async startAutomation(
    automation: AutomationIR,
    inputs: Record<string, unknown> = {},
  ): Promise<ArpExecutionSnapshot> {
    const response = await this.request<{
      ok: boolean;
      runtime_kind: string;
      run?: ArpExecutionSnapshot;
    }>('api/v2/automation-ir/runs', {
      method: 'POST',
      body: JSON.stringify({ automation, inputs }),
    });
    if (!response.run) {
      throw new Error(`自动化运行时 ${response.runtime_kind || '未知'} 未创建 ARP 运行记录`);
    }
    return response.run;
  }

  cancelRun(runId: string): Promise<CurrentRunResponse> {
    return this.request(`api/v2/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' });
  }

  pauseRun(runId: string): Promise<CurrentRunResponse> {
    return this.request(`api/v2/runs/${encodeURIComponent(runId)}/pause`, { method: 'POST' });
  }

  resumeRun(runId: string): Promise<CurrentRunResponse> {
    return this.request(`api/v2/runs/${encodeURIComponent(runId)}/resume`, { method: 'POST' });
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
      socket = new WebSocket(websocketUrl(this.baseUrl, this.token));
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
