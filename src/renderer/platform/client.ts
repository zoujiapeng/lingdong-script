import type {
  AgentThreadSnapshot,
  ArpExecutionSnapshot,
  ArpGraph,
  ChatGPTDesktopIntegration,
  CurrentRunResponse,
  ClientExecutionContext,
  GraphValidation,
  GoalRunStart,
  PlatformOverview,
  RunProjection,
  RuntimeEvent,
  AgentSchedule,
  DesktopTargetContext,
} from './contracts';
import type { FlowFragmentProposal, FlowFragmentRequestContext } from '../utils/flowFragmentProposal';
import type { AutomationIR } from '../utils/automationIR';

const FALLBACK_URL = 'http://127.0.0.1:8765/';
const ELECTRON_SERVER_READY_TIMEOUT_MS = 35_000;
const ELECTRON_SERVER_POLL_MS = 200;
export const EXPECTED_RUNTIME_CONTRACT_VERSION = 3;

export function runtimeCompatibilityNotice(overview: PlatformOverview | null | undefined): string {
  if (!overview) return '';
  const runtime = overview.status?.runtime;
  if (!runtime) return '';
  if (Number(runtime.contract_version) !== EXPECTED_RUNTIME_CONTRACT_VERSION) {
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
    return '托管的 Python 控制器未提供完整的诊断身份。';
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
    throw Object.assign(
      new Error(typeof detail === 'string' ? detail : JSON.stringify(detail)),
      { status: response.status },
    );
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

  private async request<T>(
    path: string,
    init?: RequestInit,
    timeoutMs?: number,
  ): Promise<T> {
    if (!timeoutMs) {
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
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(new URL(path.replace(/^\//, ''), this.baseUrl), {
        ...init,
        signal: init?.signal || controller.signal,
        headers: {
          ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
          ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}),
          ...init?.headers,
        },
      });
      return await readJson<T>(response);
    } catch (error) {
      if (controller.signal.aborted) {
        throw new Error(`控制平面请求在 ${Math.round(timeoutMs / 1000)} 秒内未完成。`);
      }
      throw error;
    } finally {
      clearTimeout(timeout);
    }
  }

  overview(): Promise<PlatformOverview> {
    return this.request('api/v2/platform');
  }

  currentRun(): Promise<CurrentRunResponse> {
    return this.request('api/v2/runs/current');
  }

  async captureDesktopContext(excludeProcessId = 0): Promise<DesktopTargetContext> {
    const response = await this.request<{ ok: boolean; target: DesktopTargetContext }>(
      `api/v2/desktop-context?exclude_process_id=${Math.max(0, Math.trunc(excludeProcessId))}`,
      undefined,
      15_000,
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

  async listAgentThreads(limit = 100): Promise<AgentThreadSnapshot[]> {
    const boundedLimit = Math.max(1, Math.min(Math.trunc(limit), 100));
    const response = await this.request<{ ok: boolean; threads: AgentThreadSnapshot[] }>(
      `api/v2/threads?limit=${boundedLimit}`,
    );
    return response.threads;
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

  replayStatus(replayId: string): Promise<Record<string, any>> {
    return this.request(`api/replay/${encodeURIComponent(replayId)}`);
  }

  cancelRuntimeJob(jobId: string): Promise<Record<string, any>> {
    return this.request(`api/v2/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' });
  }

  exportTrace(runId: string): Promise<{ ok: boolean; script: string }> {
    return this.request(`api/export/${encodeURIComponent(runId)}`, { method: 'POST' });
  }

  exportThreadAutomation(threadId: string, runIds: string[]): Promise<{ ok: boolean; ir: string }> {
    return this.request(`api/export/thread/${encodeURIComponent(threadId)}/ir`, {
      method: 'POST',
      body: JSON.stringify({ run_ids: runIds }),
    });
  }

  apprenticeStatus(): Promise<{
    mode: 'apprentice';
    recording: boolean;
    session_id?: string | null;
    task?: string;
    name?: string;
    app?: string;
    started_at?: number | null;
  }> {
    return this.request('api/v2/replay/apprentice/status');
  }

  apprenticeStart(
    task: string,
    app = '',
    verification: Array<Record<string, unknown>> = [],
    name = '',
  ): Promise<Record<string, any>> {
    return this.request('api/v2/replay/apprentice/start', {
      method: 'POST',
      body: JSON.stringify({ task, app, verification, name }),
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

  async startGoalRun(
    goal: string,
    threadId = '',
  ): Promise<GoalRunStart> {
    return this.request<GoalRunStart>('api/v2/goals/runs', {
      method: 'POST',
      body: JSON.stringify({
        goal,
        thread_id: threadId,
      }),
    });
  }

  async proposeFlowFragment(
    goal: string,
    context: FlowFragmentRequestContext,
    signal?: AbortSignal,
  ): Promise<FlowFragmentProposal> {
    const started = await this.request<{ ok: boolean; proposal_job_id: string }>('api/v2/flow-fragments/proposals', {
      method: 'POST',
      body: JSON.stringify({
        goal,
        ...context,
      }),
    });
    const jobId = started.proposal_job_id;
    if (!jobId) throw new Error('流程片段生成任务未返回 job ID。');
    while (true) {
      if (signal?.aborted) {
        await this.cancelRuntimeJob(jobId).catch(() => undefined);
        throw new Error('已取消流程片段生成。');
      }
      const response = await this.request<{
        ok: boolean;
        job: { status: string; result?: FlowFragmentProposal; error?: string };
      }>(`api/v2/jobs/${encodeURIComponent(jobId)}`);
      const { job } = response;
      if (job.status === 'done') {
        if (!job.result) throw new Error('流程片段生成任务没有返回结果。');
        return job.result;
      }
      if (job.status === 'failed') throw new Error(job.error || '流程片段生成失败。');
      if (job.status === 'canceled') throw new Error('已取消流程片段生成。');
      await new Promise((resolve) => setTimeout(resolve, 120));
    }
  }

  bindDesktopLease(
    runId: string,
    leaseId: string,
    executionContext: ClientExecutionContext,
  ): Promise<Record<string, unknown>> {
    return this.request(`api/v2/goals/runs/${encodeURIComponent(runId)}/desktop-lease`, {
      method: 'POST',
      body: JSON.stringify({
        lease_id: leaseId,
        execution_context: executionContext,
      }),
    });
  }

  failDesktopLease(
    runId: string,
    leaseId: string,
    reason: string,
  ): Promise<Record<string, unknown>> {
    return this.request(`api/v2/goals/runs/${encodeURIComponent(runId)}/desktop-lease/fail`, {
      method: 'POST',
      body: JSON.stringify({
        lease_id: leaseId,
        reason,
      }),
    });
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
