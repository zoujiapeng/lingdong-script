import { describe, expect, it, vi } from 'vitest';
import type { FlowScript, NodeType } from '../types';
import {
  ControlPlaneClient,
  EXPECTED_RUNTIME_CONTRACT_VERSION,
  normalizeBaseUrl,
  resolveControlPlaneConnection,
  resolveControlPlaneUrl,
  runtimeCompatibilityError,
  TaskClarificationRequired,
  websocketUrl,
} from '../platform/client';
import { edgeBranch, flowToArpGraph, RUNNABLE_FLOW_NODE_TYPES } from '../platform/flowToArpGraph';
import { automationIrFromFlowScript, validateAutomationLocally } from '../utils/automationIR';
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

  it('exports one conversation as Automation IR through the thread endpoint', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      ok: true,
      ir: 'conversation.automation.json',
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    try {
      const client = new ControlPlaneClient('http://127.0.0.1:8765');
      await expect(client.exportThreadAutomation('thread / 1', ['run-1', 'run-2'])).resolves.toEqual({
        ok: true,
        ir: 'conversation.automation.json',
      });
      const calls = fetchMock.mock.calls as unknown as Array<[RequestInfo | URL, RequestInit?]>;
      expect(String(calls[0][0])).toContain(
        '/api/export/thread/thread%20%2F%201/ir',
      );
      expect(calls[0][1]?.method).toBe('POST');
      expect(calls[0][1]?.body).toBe(JSON.stringify({ run_ids: ['run-1', 'run-2'] }));
    } finally {
      vi.unstubAllGlobals();
    }
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
      format: 'desktop-target/1',
      hwnd: 501,
      title: 'Visible target',
      process_id: 902,
      class_name: 'TargetWindow',
      rect: [10, 20, 1210, 820],
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

  it('exposes the replay job lifecycle instead of treating start as completion', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({
      replay_id: 'replay-1', status: 'running', ok: null,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);
    try {
      const client = new ControlPlaneClient('http://127.0.0.1:8765');
      await client.replayStatus('replay-1');
      await client.cancelRuntimeJob('replay-1');
      expect(String(fetchMock.mock.calls[0][0])).toContain('/api/replay/replay-1');
      expect(String(fetchMock.mock.calls[1][0])).toContain('/api/v2/jobs/replay-1/cancel');
      expect(fetchMock.mock.calls[1][1]?.method).toBe('POST');
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('preserves HTTP status for deterministic runtime-job lookup failures', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      detail: 'runtime job not found',
    }), {
      status: 404,
      headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    try {
      const client = new ControlPlaneClient('http://127.0.0.1:8765');
      const error = await client.replayStatus('missing').catch((caught) => caught);
      expect(error).toMatchObject({ status: 404, message: 'runtime job not found' });
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe('flow to ARP compiler', () => {
  const executableNodeMatrix: Array<{
    type: NodeType;
    dependency: 'local' | 'desktop' | 'human' | 'model';
    params: Record<string, unknown>;
  }> = [
    { type: 'agent_goal', dependency: 'model', params: { goal: 'Inspect the visible result' } },
    { type: 'action_batch', dependency: 'desktop', params: { actions: [{ action: 'wait', duration_ms: 0 }] } },
    { type: 'replay_step', dependency: 'desktop', params: { step: { tool: 'ext.delay', seconds: 0 } } },
    { type: 'skill_replay', dependency: 'desktop', params: { script: 'verified.wps.json' } },
    { type: 'evidence_gate', dependency: 'local', params: { required: ['result.visible'] } },
    { type: 'human_gate', dependency: 'human', params: { message: 'Continue?', timeout_s: 60 } },
    { type: 'click', dependency: 'desktop', params: { x: 10, y: 20, method: 'single' } },
    { type: 'click_text', dependency: 'desktop', params: { text: 'Save', method: 'single' } },
    { type: 'wait', dependency: 'local', params: { wait_type: 'time', duration: 0 } },
    { type: 'input_text', dependency: 'desktop', params: { text: 'hello', click_before: false } },
    { type: 'keyboard', dependency: 'desktop', params: { keys: ['enter'], modifiers: [] } },
    { type: 'swipe', dependency: 'desktop', params: { start_x: 1, start_y: 2, end_x: 3, end_y: 4 } },
    { type: 'condition', dependency: 'local', params: { condition_type: 'always_true' } },
    { type: 'screenshot', dependency: 'desktop', params: {} },
    { type: 'variable_set', dependency: 'local', params: { name: 'ready', value: true } },
    { type: 'launch_program', dependency: 'desktop', params: { target: 'notepad.exe' } },
    { type: 'focus_window', dependency: 'desktop', params: { title: 'Untitled' } },
    { type: 'clipboard_set', dependency: 'desktop', params: { text: 'hello' } },
    { type: 'clipboard_get', dependency: 'desktop', params: { into: 'clip_text' } },
    { type: 'mouse_operation', dependency: 'desktop', params: { sub_operation: 'scroll', x: 10, y: 20, scroll_amount: 1 } },
  ];

  function singleNodeFlow(type: NodeType, params: Record<string, unknown>): FlowScript {
    const script = scriptFixture();
    const needsCommonEvidenceGate = type !== 'evidence_gate';
    script.nodes = [
      { id: 'start', type: 'start', x: 0, y: 0, params: {} },
      { id: 'work', type, x: 100, y: 0, params },
      ...(needsCommonEvidenceGate
        ? [{ id: 'gate', type: 'evidence_gate' as const, x: 200, y: 0, params: { required: ['result.visible'] } }]
        : []),
      { id: 'end', type: 'end', x: 300, y: 0, params: {} },
    ];
    const primaryTarget = needsCommonEvidenceGate ? 'gate' : 'end';
    script.edges = [
      { id: 'e1', from_node: 'start', from_port: 'output', to_node: 'work', to_port: 'input' },
      {
        id: 'e2', from_node: 'work',
        from_port: type === 'condition' ? 'yes' : type === 'evidence_gate' ? 'pass' : type === 'human_gate' ? 'approved' : 'output',
        to_node: primaryTarget, to_port: 'input',
      },
      ...(needsCommonEvidenceGate
        ? [{ id: 'gate-end', from_node: 'gate', from_port: 'pass', to_node: 'end', to_port: 'input' }]
        : []),
    ];
    if (type === 'condition') {
      script.edges.push({ id: 'e3', from_node: 'work', from_port: 'no', to_node: primaryTarget, to_port: 'input' });
    }
    const work = flowToArpGraph(script).nodes.find((node) => node.id === 'work');
    const spatialActions = new Set([
      'click', 'double_click', 'click_element', 'scroll', 'move_mouse', 'drag',
      'draw_strokes', 'ensure_input_mode', 'select_color', 'mouse_down', 'mouse_up',
      'coordinate_click_test', 'paste_image_file',
    ]);
    let focused = false;
    const needsTargetSetup = work?.kind === 'action_batch'
      && Array.isArray(work.config.actions)
      && work.config.actions.some((value) => {
        const action = value as Record<string, unknown>;
        const name = String(action.action || action.tool || action.name || '').trim().toLowerCase().replace(/-/g, '_');
        if (name === 'focus_window' && (Boolean(action.hwnd) || String(action.title || '').trim())) {
          focused = true;
          return false;
        }
        return spatialActions.has(name) && !focused;
      });
    if (needsTargetSetup) {
      script.nodes.splice(1, 0, {
        id: 'target-setup', type: 'focus_window', x: 50, y: 0, params: { title: 'Fixture Target' },
      });
      script.edges[0] = {
        id: 'target-start', from_node: 'start', from_port: 'output', to_node: 'target-setup', to_port: 'input',
      };
      script.edges.splice(1, 0, {
        id: 'target-work', from_node: 'target-setup', from_port: 'output', to_node: 'work', to_port: 'input',
      });
    }
    return script;
  }

  it('keeps a complete dependency and locally valid fixture matrix for every editable node', () => {
    expect(new Set(executableNodeMatrix.map((item) => item.type))).toEqual(RUNNABLE_FLOW_NODE_TYPES);
    expect(executableNodeMatrix.filter((item) => item.dependency === 'model').map((item) => item.type)).toEqual(['agent_goal']);

    for (const item of executableNodeMatrix) {
      const flow = singleNodeFlow(item.type, item.params);
      const graph = flowToArpGraph(flow);
      expect(graph.metadata.compilation, item.type).toMatchObject({ legacy_nodes: 0 });
      expect(validateAutomationLocally(automationIrFromFlowScript(flow)), item.type).toMatchObject({ valid: true });
    }
  });

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

  it('keeps clipboard reads in the shared replay session and exposes managed screenshots', () => {
    const script = scriptFixture();
    script.nodes.splice(1, 0,
      { id: 'clip', type: 'clipboard_get', x: 0, y: 0, params: { into: 'clip_text' } },
      { id: 'shot', type: 'screenshot', x: 0, y: 0, params: {} },
    );

    const graph = flowToArpGraph(script);

    expect(graph.nodes.find((node) => node.id === 'clip')).toMatchObject({
      kind: 'replay_step',
      config: { step: { tool: 'ext.clipboard_get', into: 'clip_text' } },
    });
    expect(graph.nodes.find((node) => node.id === 'shot')).toMatchObject({
      kind: 'action_batch',
      config: { actions: [{
        action: 'screenshot',
        managed_artifact: true,
        scope: 'screen',
        purpose: 'user_output',
        sendable: true,
      }] },
    });
    expect(graph.metadata.compilation).toMatchObject({ legacy_nodes: 0 });
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
      { type: 'screenshot', params: {} },
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
      { type: 'mouse_operation', params: { sub_operation: 'scroll', scroll_direction: 'left', x: 1, y: 2 } },
      { type: 'input_text', params: { text: 'unsafe focus', click_before: true } },
      { type: 'screenshot', params: { save_path: 'legacy.png' } },
      { type: 'screenshot', params: { region: { x: 1, y: 2, w: 3, h: 4 } } },
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

  it('preserves mouse parameters and keyboard sequence semantics exactly', () => {
    const script = scriptFixture();
    script.nodes.splice(1, 0,
      { id: 'text-click', type: 'mouse_operation', x: 0, y: 0, params: { sub_operation: 'click', mode: 'text', text: 'Save', method: 'right', delay_after: 0.2 } },
      { id: 'scroll', type: 'mouse_operation', x: 0, y: 0, params: { sub_operation: 'scroll', scroll_direction: 'up', scroll_amount: 4, x: 50, y: 60, delay_after: 0.1 } },
      { id: 'keys', type: 'keyboard', x: 0, y: 0, params: { modifiers: ['ctrl'], keys: ['c', 'v'], delay_after: 0.3 } },
      { id: 'legacy-keys', type: 'keyboard', x: 0, y: 0, params: { modifiers: [], keys: ['ctrl', 'c'], delay_after: 0 } },
    );

    const graph = flowToArpGraph(script);
    expect(graph.nodes.find((node) => node.id === 'text-click')?.config.actions).toEqual([
      { action: 'click_element', label: 'Save', button: 'right', double: false },
      { action: 'wait', duration_ms: 200 },
    ]);
    expect(graph.nodes.find((node) => node.id === 'scroll')?.config.actions).toEqual([
      { action: 'scroll', x: 50, y: 60, amount: 4 },
      { action: 'wait', duration_ms: 100 },
    ]);
    expect(graph.nodes.find((node) => node.id === 'keys')?.config.actions).toEqual([
      { action: 'hotkey', keys: ['ctrl', 'c'] },
      { action: 'hotkey', keys: ['ctrl', 'v'] },
      { action: 'wait', duration_ms: 300 },
    ]);
    expect(graph.nodes.find((node) => node.id === 'legacy-keys')?.config.actions).toEqual([
      { action: 'hotkey', keys: ['ctrl', 'c'] },
    ]);
  });

  it('fails closed when a window wait exceeds the physical runtime contract', () => {
    const script = scriptFixture();
    script.nodes.splice(1, 0, {
      id: 'long-window-wait',
      type: 'wait',
      x: 0,
      y: 0,
      params: { wait_type: 'window', window_title: 'Target', timeout: 120.1 },
    });

    expect(flowToArpGraph(script).nodes.find((node) => node.id === 'long-window-wait')?.kind).toBe('legacy_flow');
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
