import { describe, expect, it, vi } from 'vitest';
import type { FlowScript } from '../types';
import {
  ControlPlaneClient,
  createGoalGraph,
  EXPECTED_RUNTIME_CONTRACT_VERSION,
  normalizeBaseUrl,
  resolveControlPlaneConnection,
  resolveControlPlaneUrl,
  runtimeCompatibilityError,
  websocketUrl,
} from '../platform/client';
import { edgeBranch, flowToArpGraph } from '../platform/flowToArpGraph';
import { automationIrFromFlowScript } from '../utils/automationIR';

function scriptFixture(): FlowScript {
  return {
    meta: {
      id: 'flow-1',
      name: 'Export report',
      created_at: '2026-01-01T00:00:00.000Z',
      updated_at: '2026-01-01T00:00:00.000Z',
      version: '1.0.0',
    },
    nodes: [
      { id: 'start', type: 'start', x: 10, y: 20, params: {} },
      {
        id: 'goal', type: 'agent_goal', x: 10, y: 120,
        label: 'Find report',
        params: {
          goal: 'Open the report', success_criteria: 'Report table is visible', max_steps: 20,
          retry_max_attempts: 2, retry_delay_ms: 250,
        },
      },
      {
        id: 'batch', type: 'action_batch', x: 10, y: 220,
        params: {
          subgoal: 'Export', stop_on_error: true,
          actions: [{ action: 'click_element', label: 'Export', expected_region: 'top-right' }],
        },
      },
      { id: 'gate', type: 'evidence_gate', x: 10, y: 320, params: { required: ['download'] } },
      { id: 'end', type: 'end', x: 10, y: 420, params: {} },
    ],
    edges: [
      { id: 'e1', from_node: 'start', from_port: 'output', to_node: 'goal', to_port: 'input' },
      { id: 'e2', from_node: 'goal', from_port: 'output', to_node: 'batch', to_port: 'input' },
      { id: 'e3', from_node: 'batch', from_port: 'output', to_node: 'gate', to_port: 'input' },
      { id: 'e4', from_node: 'gate', from_port: 'pass', to_node: 'end', to_port: 'input' },
    ],
    variables: { account: 'demo' },
    settings: {
      default_delay: 0.5,
      error_handling: 'stop',
      max_retries: 2,
      retry_delay: 1,
      log_level: 'info',
    },
  };
}

describe('control plane client contracts', () => {
  it('fails closed for missing, stale, or mismatched controller identity', () => {
    const current = {
      api_version: '2',
      product: 'WinPilot',
      status: {
        runtime: {
          contract_version: EXPECTED_RUNTIME_CONTRACT_VERSION,
          source: { loaded_stamp: 'same', current_stamp: 'same', drifted: false },
          git: { loaded_commit: 'abc', current_commit: 'abc' },
        },
      },
      current_run: null,
      capabilities: [],
      contracts: {},
    };

    expect(runtimeCompatibilityError(null)).toContain('尚未加载');
    expect(runtimeCompatibilityError({ ...current, status: {} })).toContain('不兼容');
    expect(runtimeCompatibilityError(current)).toBe('');
    expect(runtimeCompatibilityError({
      ...current,
      status: {
        runtime: {
          ...current.status.runtime,
          source: { loaded_stamp: 'old', current_stamp: 'new', drifted: false },
        },
      },
    })).toContain('启动后发生了变化');
    expect(runtimeCompatibilityError({
      ...current,
      status: {
        runtime: {
          ...current.status.runtime,
          git: { loaded_commit: 'old', current_commit: 'new' },
        },
      },
    })).toContain('仓库提交');
  });

  it('normalizes REST and WebSocket addresses', () => {
    expect(normalizeBaseUrl('http://127.0.0.1:8765')).toBe('http://127.0.0.1:8765/');
    expect(websocketUrl('https://example.test/runtime/')).toBe('wss://example.test/runtime/ws/events');
    expect(websocketUrl('https://example.test/runtime/', 'secret')).toBe('wss://example.test/runtime/ws/events?token=secret');
    expect(
      new ControlPlaneClient('https://example.test/runtime/', 'secret')
        .traceScreenshotUrl('run 1', 'nested\\after 1.jpg'),
    ).toBe('https://example.test/runtime/api/trace/run%201/shot/after%201.jpg?token=secret');
  });

  it('keeps the managed control token in the client transport', async () => {
    (window as any).electronAPI = {
      ensureWinpilot: vi.fn(async () => ({
        url: 'http://127.0.0.1:23456', token: 'managed-token', starting: false, error: null,
      })),
      getWinpilotConnection: vi.fn(async () => ({
        url: 'http://127.0.0.1:23456', token: 'managed-token',
      })),
    };
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    try {
      const connection = await resolveControlPlaneConnection();
      expect(connection).toEqual({ url: 'http://127.0.0.1:23456/', token: 'managed-token' });
      await new ControlPlaneClient(connection.url, connection.token).overview();
      expect(fetchMock.mock.calls[0][1]?.headers).toMatchObject({
        Authorization: 'Bearer managed-token',
      });
    } finally {
      vi.unstubAllGlobals();
      delete (window as any).electronAPI;
    }
  });

  it('waits for the Electron-managed server instead of using a stale fallback port', async () => {
    let calls = 0;
    (window as any).electronAPI = {
      ensureWinpilot: vi.fn(async () => {
        calls += 1;
        return calls === 1
          ? { url: null, starting: true, error: null }
          : { url: 'http://127.0.0.1:23456', starting: false, error: null };
      }),
      getWinpilotUrl: vi.fn(async () => null),
    };

    try {
      await expect(resolveControlPlaneUrl()).resolves.toBe('http://127.0.0.1:23456/');
      expect(calls).toBe(2);
    } finally {
      delete (window as any).electronAPI;
    }
  });

  it('creates a valid high-level goal graph', () => {
    const graph = createGoalGraph('Export the current report', 24);
    expect(graph.format).toBe('arp/1');
    expect(graph.nodes.map((node) => node.kind)).toEqual(['start', 'skill_replay', 'agent_goal', 'end']);
    expect(graph.nodes[1].config).toMatchObject({ task: 'Export the current report' });
    expect(graph.nodes[2].config).toMatchObject({ goal: 'Export the current report', max_steps: 24 });
    expect(graph.edges.map((edge) => edge.branch)).toEqual(['next', 'next', 'miss', 'fail', 'next']);
    expect(graph.metadata.routing).toBe('verified-skill-first-agent-on-miss-or-failure');
  });

  it('reconnects the runtime event socket after a backend restart', () => {
    vi.useFakeTimers();
    const sockets: EventTarget[] = [];

    class FakeSocket extends EventTarget {
      close = vi.fn();
      constructor(public readonly url: string) {
        super();
        sockets.push(this);
      }
    }

    vi.stubGlobal('WebSocket', FakeSocket);
    const states: boolean[] = [];
    const client = new ControlPlaneClient('http://127.0.0.1:8765');
    const disconnect = client.connectEvents(() => undefined, (connected: boolean) => states.push(connected));

    sockets[0].dispatchEvent(new Event('open'));
    sockets[0].dispatchEvent(new Event('close'));
    vi.advanceTimersByTime(500);

    expect(sockets).toHaveLength(2);
    expect(states).toEqual([true, false]);
    disconnect();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('submits Automation IR directly for validation and execution', async () => {
    const automation = automationIrFromFlowScript(scriptFixture(), 'air-client');
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      const body = url.endsWith('/validate')
        ? { valid: true, errors: [], warnings: [], entry_node: 'start', reachable_nodes: [] }
        : { ok: true, runtime_kind: 'arp_graph', run: { run_id: 'arp-1', status: 'queued' } };
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', fetchMock);
    try {
      const client = new ControlPlaneClient('http://127.0.0.1:8765');
      await expect(client.validateAutomation(automation)).resolves.toMatchObject({ valid: true });
      await expect(client.startAutomation(automation)).resolves.toMatchObject({ run_id: 'arp-1' });

      expect(String(fetchMock.mock.calls[0][0])).toContain('/api/v2/automation-ir/validate');
      expect(String(fetchMock.mock.calls[1][0])).toContain('/api/v2/automation-ir/runs');
      const submitted = JSON.parse(String(fetchMock.mock.calls[1][1]?.body));
      expect(submitted.automation.runtime.kind).toBe('arp_graph');
      expect(submitted.automation.runtime.projection_fingerprint).toMatch(/^sha256:[0-9a-f]{64}$/);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe('flow to ARP compiler', () => {
  it('preserves native runtime nodes, anchors, evidence, and retry policy', () => {
    const graph = flowToArpGraph(scriptFixture());
    const goal = graph.nodes.find((node) => node.id === 'goal')!;
    const batch = graph.nodes.find((node) => node.id === 'batch')!;

    expect(goal.kind).toBe('agent_goal');
    expect(goal.config.goal).toContain('Completion evidence: Report table is visible');
    expect(goal.retry).toEqual({ max_attempts: 2, delay_ms: 250, backoff: 1 });
    expect(goal.config).not.toHaveProperty('retry_max_attempts');
    expect(batch.config.actions).toEqual([
      { action: 'click_element', label: 'Export', expected_region: 'top-right' },
    ]);
    expect(graph.variables).toEqual({ account: 'demo' });
    expect(graph.edges.at(-1)?.branch).toBe('pass');
  });

  it('maps delays and supported variable conditions to runtime built-ins', () => {
    const script = scriptFixture();
    script.nodes.splice(1, 0,
      { id: 'wait', type: 'wait', x: 0, y: 0, params: { wait_type: 'time', duration: 0.25 } },
      { id: 'condition', type: 'condition', x: 0, y: 0, params: { condition_type: 'variable_equals', variable_name: 'ready', variable_value: 'yes' } },
    );
    const graph = flowToArpGraph(script);
    expect(graph.nodes.find((node) => node.id === 'wait')).toMatchObject({ kind: 'delay', config: { duration_ms: 250 } });
    expect(graph.nodes.find((node) => node.id === 'condition')).toMatchObject({ kind: 'condition', config: { source: 'variables.ready', operator: 'eq', value: 'yes' } });
  });

  it('compiles exact replay steps as native zero-token nodes with explicit shell policy', () => {
    const script = scriptFixture();
    script.nodes.splice(1, 0, {
      id: 'offline',
      type: 'replay_step',
      x: 0,
      y: 0,
      params: {
        step: { tool: 'ext.run_shell', command: 'Write-Output ok' },
        session: 'recording-1',
        pace_ms: 0,
        allow_shell: true,
        target_window: { title: 'Target' },
      },
    });

    const graph = flowToArpGraph(script);
    expect(graph.nodes.find((node) => node.id === 'offline')).toMatchObject({
      kind: 'replay_step',
      config: {
        step: { tool: 'ext.run_shell', command: 'Write-Output ok' },
        session: 'recording-1',
        target_window: { title: 'Target' },
      },
    });
    expect(graph.policy.allow_shell).toBe(true);
    expect(graph.metadata.compilation).toMatchObject({ replay_nodes: 1, legacy_nodes: 0 });
  });

  it('compiles common GUI nodes into one controller batch per node', () => {
    const script = scriptFixture();
    script.nodes.splice(1, 0,
      { id: 'open', type: 'launch_program', x: 0, y: 0, params: { target: 'notepad.exe', args: '' } },
      { id: 'focus', type: 'focus_window', x: 0, y: 0, params: { title: 'Notepad' } },
      { id: 'type', type: 'input_text', x: 0, y: 0, params: { text: 'hello', x: 300, y: 200, click_before: true, clear_before: true, delay_after: 0.2 } },
      { id: 'send', type: 'click_text', x: 0, y: 0, params: { text: 'Send', method: 'single', delay_after: 0 } },
    );

    const graph = flowToArpGraph(script);

    expect(graph.nodes.find((node) => node.id === 'open')).toMatchObject({
      kind: 'action_batch', config: { actions: [{ action: 'launch', command: 'notepad.exe' }] },
    });
    expect(graph.nodes.find((node) => node.id === 'type')?.config.actions).toEqual([
      { action: 'click', x: 300, y: 200 },
      { action: 'hotkey', keys: ['ctrl', 'a'] },
      { action: 'type_text', text: 'hello' },
      { action: 'wait', duration_ms: 200 },
    ]);
    expect(graph.nodes.find((node) => node.id === 'send')?.config.actions).toEqual([
      { action: 'click_element', label: 'Send', fallback_text: [], button: 'left', double: false },
    ]);
    expect((graph.metadata.compilation as any).legacy_nodes).toBe(0);
  });

  it('encapsulates unsupported legacy nodes instead of silently changing semantics', () => {
    const script = scriptFixture();
    script.nodes.splice(1, 0, { id: 'legacy', type: 'tool_call', x: 2, y: 3, params: { feature_name: 'future_tool', params: {} } });
    const graph = flowToArpGraph(script);
    expect(graph.nodes.find((node) => node.id === 'legacy')).toMatchObject({
      kind: 'legacy_flow',
      config: { node_type: 'tool_call', params: { feature_name: 'future_tool', params: {} } },
    });
    expect(graph.metadata.compilation).toMatchObject({ legacy_nodes: 1, legacy_types: ['tool_call'] });
  });

  it('normalizes only flow-control aliases', () => {
    expect(edgeBranch('output')).toBe('next');
    expect(edgeBranch('loop_body')).toBe('yes');
    expect(edgeBranch('fail')).toBe('fail');
  });
});
