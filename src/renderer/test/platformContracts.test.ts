import { describe, expect, it, vi } from 'vitest';
import type { FlowScript, NodeType } from '../types';
import {
  ControlPlaneClient,
  type DesktopRuntimeVersion,
  EXPECTED_RUNTIME_CONTRACT_VERSION,
  normalizeBaseUrl,
  resolveControlPlaneConnection,
  resolveControlPlaneUrl,
  runtimeCompatibilityError,
  TaskClarificationRequired,
  websocketUrl,
} from '../platform/client';
import { edgeBranch, flowToArpGraph, RUNNABLE_FLOW_NODE_TYPES } from '../platform/flowToArpGraph';
import { automationIrFromFlowScript } from '../utils/automationIR';
import { NODE_DEFINITIONS } from '../utils/nodeDefinitions';
import { PRESET_TEMPLATE_META, PRESET_TEMPLATES } from '../store/defaults';

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
  it('sends the desktop runtime only as a typed top-level run product field', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      ok: true,
      run_id: 'arp-1',
      run: { run_id: 'arp-1' },
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    try {
      const client = new ControlPlaneClient('http://127.0.0.1:8765');
      const graph = { format: 'arp/1', id: 'graph-1' } as any;
      const inputs = { execution_context: { format: 'execution-context/1' } };
      await client.startGraph(graph, inputs, 'vnext');

      const [url, init] = fetchMock.mock.calls[0] as unknown as [URL, RequestInit];
      expect(String(url)).toContain('/api/v2/arp/runs');
      expect(init.method).toBe('POST');
      expect(JSON.parse(String(init.body))).toEqual({
        graph,
        inputs,
        runtime_version: 'vnext',
      });
      expect(JSON.stringify(graph)).not.toContain('runtime_version');
      expect(JSON.stringify(inputs)).not.toContain('runtime_version');
      await expect(
        client.startGraph(graph, inputs, 'future' as DesktopRuntimeVersion),
      ).rejects.toThrow('不支持的桌面运行时');
      expect(fetchMock).toHaveBeenCalledOnce();
    } finally {
      vi.unstubAllGlobals();
    }
  });

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
    expect(
      new ControlPlaneClient('https://example.test/runtime/', 'secret')
        .artifactUrl('run 1', 'art/1'),
    ).toBe('https://example.test/runtime/api/v2/runs/run%201/artifacts/art%2F1?token=secret');
  });

  it('uses generic run detail and control routes for both Agent and ARP runs', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({
      ok: true,
      run: null,
      arp: null,
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    try {
      const client = new ControlPlaneClient('http://127.0.0.1:8765');
      await client.run('agent run/1');
      await client.cancelRun('agent run/1');
      await client.pauseRun('agent run/1');
      await client.resumeRun('agent run/1');

      expect(String(fetchMock.mock.calls[0][0])).toContain('/api/v2/runs/agent%20run%2F1');
      expect(String(fetchMock.mock.calls[1][0])).toContain('/api/v2/runs/agent%20run%2F1/cancel');
      expect(fetchMock.mock.calls[1][1]?.method).toBe('POST');
      expect(String(fetchMock.mock.calls[2][0])).toContain('/api/v2/runs/agent%20run%2F1/pause');
      expect(String(fetchMock.mock.calls[3][0])).toContain('/api/v2/runs/agent%20run%2F1/resume');
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('captures the post-handoff desktop target through the authenticated control plane', async () => {
    const target = {
      format: 'desktop-target/2',
      hwnd: 501,
      root_hwnd: 501,
      title: 'Visible target',
      process_id: 902,
      process_identity: 'process-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
      class_name: 'TargetWindow',
      rect: [10, 20, 1210, 820] as [number, number, number, number],
      captured_at: 1_750_000,
      foreground_verified: true,
      usable: true,
      failure_type: '',
      reason: '',
    };
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ ok: true, target }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    try {
      await expect(
        new ControlPlaneClient('http://127.0.0.1:8765', 'managed-token')
          .captureDesktopContext(7712),
      ).resolves.toEqual(target);

      expect(String(fetchMock.mock.calls[0][0])).toContain(
        '/api/v2/desktop-context?exclude_process_id=7712',
      );
      expect(fetchMock.mock.calls[0][1]?.headers).toMatchObject({
        Authorization: 'Bearer managed-token',
      });
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('rejects malformed desktop-target responses before run submission', async () => {
    const valid = {
      format: 'desktop-target/2',
      hwnd: 501,
      root_hwnd: 501,
      title: 'Visible target',
      process_id: 902,
      process_identity: 'process-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
      class_name: 'TargetWindow',
      rect: [10, 20, 1210, 820],
      captured_at: 1_750_000,
      foreground_verified: true,
      usable: true,
      failure_type: '',
      reason: '',
    };
    const malformed = [
      { ...valid, format: 'desktop-target/1' },
      { ...valid, process_id: true },
      { ...valid, process_identity: 'process-weak' },
      { ...valid, rect: [10, 20, 1210, 820, 900] },
      { ...valid, unrecognized: true },
    ];

    try {
      for (const target of malformed) {
        vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ ok: true, target }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })));
        await expect(
          new ControlPlaneClient('http://127.0.0.1:8765', 'managed-token')
            .captureDesktopContext(7712),
        ).rejects.toThrow(/desktop target/i);
      }
    } finally {
      vi.unstubAllGlobals();
    }
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

  it('compiles a typed task contract before starting an ARP run', async () => {
    const graph = {
      format: 'arp/1', id: 'task-1', name: 'Export report', description: '',
      nodes: [
        { id: 'start', kind: 'start', name: 'start', config: {}, retry: { max_attempts: 1, delay_ms: 0, backoff: 1 }, disabled: false, x: 0, y: 0 },
        { id: 'stage-1', kind: 'task_stage', name: 'typed stage', config: {}, retry: { max_attempts: 1, delay_ms: 0, backoff: 1 }, disabled: false, x: 0, y: 100 },
        { id: 'end', kind: 'end', name: 'end', config: {}, retry: { max_attempts: 1, delay_ms: 0, backoff: 1 }, disabled: false, x: 0, y: 200 },
      ],
      edges: [], variables: {},
      policy: { max_node_visits: 20, fail_fast: true, require_evidence_on_done: true, allow_shell: false, allow_desktop_mutation: true },
      metadata: { routing: 'typed-stage-runtime' },
    } as any;
    const contract = {
      format: 'task-contract/1', task_id: 'task-1', task: 'Export the current report',
      intent: { original_text: 'Export the current report', operation: ['save'], media_type: 'file', channel: '', recipient: '', source: '', destination: '', send_mode: 'send', subject: '', quality: 'normal', confidence: 0.9, ambiguous_fields: [], provenance: {} },
      stages: [{ id: 'stage-1', type: 'SaveArtifact' }], obligations: [], final_conditions: [],
      satisfiable: true, missing_capabilities: [], compiler_version: 'typed-stage-1',
    } as any;
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true, contract, graph }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    try {
      const compiled = await new ControlPlaneClient('http://127.0.0.1:8765').compileTask('Export the current report', 24);
      expect(compiled.graph.nodes.map((node) => node.kind)).toContain('task_stage');
      expect(compiled.graph.nodes.map((node) => node.kind)).not.toContain('agent_goal');
      expect(compiled.contract.satisfiable).toBe(true);
      const calls = fetchMock.mock.calls as unknown as Array<[RequestInfo | URL, RequestInit?]>;
      expect(calls[0]?.[0].toString()).toContain('/api/v2/task-contracts/compile');
      expect(JSON.parse(String(calls[0]?.[1]?.body))).toEqual({ task: 'Export the current report', max_steps: 24, cold_start: false, require_gui: true });
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('surfaces compiler clarification questions instead of starting an incomplete task', async () => {
    const contract = {
      format: 'task-contract/1', task_id: 'task-needs-input', task: 'send it to my phone',
      intent: { original_text: 'send it to my phone', operation: ['send'], media_type: 'file', channel: '', recipient: '', source: '', destination: '', send_mode: 'send', subject: '', quality: 'normal', confidence: 0.4, ambiguous_fields: ['channel'], provenance: {} },
      stages: [], obligations: [], final_conditions: [], clarification_questions: ['Which phone channel should I use?'],
      satisfiable: false, missing_capabilities: [], compiler_version: 'typed-stage-5',
    } as any;
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      ok: false,
      needs_input: true,
      clarification_questions: ['Which phone channel should I use?'],
      contract,
      graph: { format: 'arp/1', nodes: [], edges: [] },
    }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    try {
      const result = new ControlPlaneClient('http://127.0.0.1:8765').compileTask(
        'send it to my phone', 24, true,
      );
      await expect(result).rejects.toThrow('Which phone channel should I use?');
      await result.catch((error) => {
        expect(error).toBeInstanceOf(TaskClarificationRequired);
        expect((error as TaskClarificationRequired).questions).toEqual([
          'Which phone channel should I use?',
        ]);
      });
      const calls = fetchMock.mock.calls as unknown as Array<[RequestInfo | URL, RequestInit?]>;
      expect(JSON.parse(String(calls[0]?.[1]?.body))).toEqual({
        task: 'send it to my phone',
        max_steps: 24,
        cold_start: true,
        require_gui: true,
      });
    } finally {
      vi.unstubAllGlobals();
    }
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
  it('compiles every node exposed by the executable-node directory without legacy fallback', () => {
    for (const type of RUNNABLE_FLOW_NODE_TYPES) {
      const script = scriptFixture();
      script.nodes.splice(1, 0, {
        id: `node-${type}`,
        type,
        x: 0,
        y: 0,
        params: structuredClone(NODE_DEFINITIONS[type].defaultParams),
      });
      const compiled = flowToArpGraph(script).nodes.find((node) => node.id === `node-${type}`);
      expect(compiled?.kind, type).not.toBe('legacy_flow');
    }
  });

  it('keeps preset metadata and executable templates in one registry', () => {
    expect(PRESET_TEMPLATE_META.map((item) => item.id)).toEqual(PRESET_TEMPLATES.map((item) => item.id));
    for (const template of PRESET_TEMPLATES) {
      const script = template.createScript();
      expect(script.nodes.every((node) => (
        node.type === 'start' || node.type === 'end' || RUNNABLE_FLOW_NODE_TYPES.has(node.type)
      ))).toBe(true);
      expect(flowToArpGraph(script).metadata.compilation).toMatchObject({ legacy_nodes: 0 });
    }
    const quickstart = PRESET_TEMPLATES.find((item) => item.id === 'arp-quickstart')!.createScript();
    expect((quickstart.nodes.find((node) => node.type === 'evidence_gate')?.params as any).required).toEqual([
      { predicate: 'human.approved', source: 'human_gate', equals: true },
    ]);
  });

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

  it('compiles every parameter branch exposed by the production property panels', () => {
    const cases: Array<{ type: NodeType; params: Record<string, unknown> }> = [
      { type: 'wait', params: { wait_type: 'time', duration: 1 } },
      { type: 'wait', params: { wait_type: 'window', window_title: 'Target', timeout: 5 } },
      { type: 'condition', params: { condition_type: 'always_true' } },
      { type: 'condition', params: { condition_type: 'variable_equals', variable_name: 'n', variable_value: 1 } },
      { type: 'condition', params: { condition_type: 'variable_greater', variable_name: 'n', variable_value: 1 } },
      { type: 'condition', params: { condition_type: 'variable_less', variable_name: 'n', variable_value: 1 } },
      { type: 'mouse_operation', params: { sub_operation: 'click', mode: 'coordinate', x: 1, y: 2 } },
      { type: 'mouse_operation', params: { sub_operation: 'click', mode: 'text', text: 'Save' } },
      { type: 'mouse_operation', params: { sub_operation: 'scroll', x: 1, y: 2, scroll_amount: 3 } },
      { type: 'mouse_operation', params: { sub_operation: 'move', move_x: 1, move_y: 2 } },
      { type: 'mouse_operation', params: { sub_operation: 'hover', hover_x: 1, hover_y: 2 } },
      { type: 'screenshot', params: { quality: 85 } },
    ];

    for (const [index, item] of cases.entries()) {
      const script = scriptFixture();
      script.nodes.splice(1, 0, { id: `branch-${index}`, type: item.type, x: 0, y: 0, params: item.params });
      const compiled = flowToArpGraph(script).nodes.find((node) => node.id === `branch-${index}`);
      expect(compiled?.kind, `${item.type}:${JSON.stringify(item.params)}`).not.toBe('legacy_flow');
    }
  });

  it('fails closed for historical parameter branches that ARP cannot execute', () => {
    const cases: Array<{ type: NodeType; params: Record<string, unknown> }> = [
      { type: 'wait', params: { wait_type: 'image', image_path: 'legacy.png' } },
      { type: 'condition', params: { condition_type: 'image_exists', image_path: 'legacy.png' } },
      { type: 'mouse_operation', params: { sub_operation: 'click', mode: 'image', targets: [] } },
      { type: 'screenshot', params: { save_path: 'legacy.png' } },
    ];

    for (const [index, item] of cases.entries()) {
      const script = scriptFixture();
      script.nodes.splice(1, 0, { id: `legacy-${index}`, type: item.type, x: 0, y: 0, params: item.params });
      const compiled = flowToArpGraph(script).nodes.find((node) => node.id === `legacy-${index}`);
      expect(compiled?.kind, `${item.type}:${JSON.stringify(item.params)}`).toBe('legacy_flow');
    }
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
