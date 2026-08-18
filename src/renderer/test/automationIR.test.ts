import { describe, expect, it } from 'vitest';
import {
  automationIrFromFlowScript,
  automationIrFromWps,
  automationDocumentToFlowScript,
  automationIrToFlowScript,
  automationIrToWps,
  automationProjectionFingerprint,
  automationRuntimeGraph,
  validateAutomationLocally,
  validateAutomationRuntime,
  validateAutomationIR,
} from '../utils/automationIR';
import type { FlowScript } from '../types';
import { flowToArpGraph } from '../platform/flowToArpGraph';

describe('Automation IR adapters', () => {
  it('wraps WPS scripts without losing replay metadata', () => {
    const ir = automationIrFromWps({
      format: 'wps/2',
      name: 'send',
      task: 'send message',
      source: 'agent',
      source_run: 'run-1',
      vars: { msg: 'hello' },
      steps: [
        { tool: 'click', coords: [1, 2] },
        { tool: 'type_text', text_input: '${msg}' },
      ],
    }, 'air-test');

    expect(validateAutomationIR(ir)).toBeUndefined();
    expect(ir.id).toBe('air-test');
    expect(ir.source.kind).toBe('wps');
    expect(ir.source.source_run).toBe('run-1');
    expect(ir.variables).toEqual({ msg: 'hello' });
    expect(ir).not.toHaveProperty('safety');
    expect(ir).not.toHaveProperty('fallback');
    expect(ir.steps[0]).toEqual({ tool: 'click', coords: [1, 2] });
    expect(ir.runtime).toEqual({ kind: 'wps_replay' });
    expect(validateAutomationRuntime(ir)).toBeUndefined();
  });

  it('roundtrips Automation IR back to a WPS envelope when steps are WPS-shaped', () => {
    const ir = automationIrFromWps({
      format: 'wps/2',
      name: 'demo',
      task: 'demo task',
      created: 123,
      conditions: { target_window: { title: 'Demo App' }, login_state: 'ready' },
      evidence: ['final text visible'],
      export_manifest: { complete: false, partial: true, reasons: ['one omitted action'] },
      source_runs: ['run-1', 'run-2'],
      vars: { a: '1' },
      steps: [{ tool: 'click', coords: [10, 20] }],
    }, 'air-roundtrip');

    const wps = automationIrToWps(ir);

    expect(wps.format).toBe('wps/2');
    expect(wps.source).toBe('automation-ir');
    expect(wps.source_ir).toBe('air-roundtrip');
    expect(wps.created).toBe(123);
    expect(wps.target_window).toEqual({ title: 'Demo App' });
    expect(wps.conditions).toEqual({ target_window: { title: 'Demo App' }, login_state: 'ready' });
    expect(wps.evidence).toEqual(['final text visible']);
    expect(wps.export_manifest).toEqual({ complete: false, partial: true, reasons: ['one omitted action'] });
    expect(wps.source_runs).toEqual(['run-1', 'run-2']);
    expect(wps.vars).toEqual({ a: '1' });
    expect(wps.steps).toEqual([{ tool: 'click', coords: [10, 20] }]);
  });

  it('wraps FlowScript as IR without pretending it is executable WPS', () => {
    const flow: FlowScript = {
      meta: {
        id: 'flow-1',
        name: 'flow demo',
        created_at: '',
        updated_at: '',
        version: '1.0.0',
      },
      nodes: [
        { id: 'start', type: 'start', x: 0, y: 0, params: {} },
        { id: 'click', type: 'click', x: 100, y: 0, params: { x: 1, y: 2, method: 'single', delay_after: 0 } },
      ],
      edges: [
        { id: 'e1', from_node: 'start', from_port: 'output', to_node: 'click', to_port: 'input' },
      ],
      variables: { token: 'x' },
      settings: {
        default_delay: 0,
        error_handling: 'stop',
        max_retries: 0,
        retry_delay: 0,
        log_level: 'info',
      },
    };

    const ir = automationIrFromFlowScript(flow, 'air-flow');

    expect(validateAutomationIR(ir)).toBeUndefined();
    expect(ir.source.kind).toBe('flowscript');
    expect(ir.steps[1]).toMatchObject({
      tool: 'click',
      node_id: 'click',
      params: { x: 1, y: 2, method: 'single', delay_after: 0 },
    });
    expect(ir).not.toHaveProperty('safety');
    expect(ir).not.toHaveProperty('fallback');
    expect(ir.runtime?.kind).toBe('arp_graph');
    expect(automationRuntimeGraph(ir).metadata.source_ir).toBe('air-flow');
    const stale = structuredClone(ir);
    stale.steps[1].tool = 'changed-after-compile';
    expect(validateAutomationRuntime(stale)).toContain('投影已过期');
    expect(() => automationRuntimeGraph(stale)).toThrow('投影已过期');

    const restored = automationIrToFlowScript(ir);
    expect(restored.meta.id).toBe('flow-1');
    expect(restored.nodes).toEqual(flow.nodes);
    expect(restored.edges).toEqual(flow.edges);
    expect(restored.variables).toEqual(flow.variables);
    expect(restored.settings).toEqual(flow.settings);
    expect(automationDocumentToFlowScript(ir)).toEqual(restored);
    expect(automationDocumentToFlowScript(flow)).toEqual(flow);
  });

  it('drops old replay gates while keeping editable action metadata', () => {
    const source = automationIrFromWps({
      format: 'wps/2',
      name: 'replay contract',
      task: 'replace a document',
      source_run: 'lesson-42',
      target_window: { title: 'Target', process: 'target.exe' },
      conditions: { checks: [{ file_exists: 'C:\\result.txt' }] },
      evidence: {
        required: [{ predicate: 'file.exists', value: 'C:\\result.txt' }],
        mode: 'strict',
      },
      steps: [{ tool: 'hotkey', keys: ['ctrl', 's'] }],
    }, 'air-source');
    (source as any).safety = { requires_preflight: true };
    (source as any).fallback = { on_preflight_fail: 'stop' };

    expect(automationIrToWps(source).evidence).toEqual(source.evidence);

    const flow = automationIrToFlowScript(source);
    const replay = flow.nodes.find((node) => node.type === 'replay_step')!;
    expect((replay.params as any).session).toBe('lesson-42');
    expect(replay.params).not.toHaveProperty('allow_shell');
    expect(replay.params).not.toHaveProperty('preflight_contract');
    expect(replay.params).not.toHaveProperty('postflight_evidence');

    const saved = automationIrFromFlowScript(flow, 'air-saved');
    expect(saved.conditions).toEqual(source.conditions);
    expect(saved.evidence).toEqual(source.evidence);
    expect(saved).not.toHaveProperty('safety');
    expect(saved).not.toHaveProperty('fallback');
    expect((saved.runtime as any).graph.nodes.find((node: any) => node.kind === 'replay_step').config)
      .toEqual(expect.objectContaining({ session: 'lesson-42' }));
  });

  it('roundtrips execution semantics while omitting transient canvas state', () => {
    const flow: FlowScript = {
      meta: { id: 'semantic-flow', name: 'semantic flow', created_at: '', updated_at: '', version: '1.0.0' },
      nodes: [
        { id: 'start', type: 'start', x: 0, y: 0, params: {} },
        {
          id: 'work', type: 'action_batch', x: 100, y: 0,
          params: { actions: [{ action: 'wait', duration_ms: 1 }] },
          disabled: true, collapsed: true,
        },
        { id: 'end', type: 'end', x: 200, y: 0, params: {} },
      ],
      edges: [
        { id: 'e1', from_node: 'start', from_port: 'output', to_node: 'work', to_port: 'input', label: 'yes', animated: true },
        { id: 'e2', from_node: 'work', from_port: 'output', to_node: 'end', to_port: 'input' },
      ],
      variables: {},
      settings: { default_delay: 0, error_handling: 'stop', max_retries: 0, retry_delay: 0, log_level: 'info' },
      triggers: [{ type: 'interval', interval_seconds: 30 }],
    };

    const ir = automationIrFromFlowScript(flow, 'air-semantics');
    const restored = automationIrToFlowScript(ir);

    expect(restored.nodes.find((node) => node.id === 'work')?.disabled).toBe(true);
    expect(restored.edges.find((edge) => edge.id === 'e1')?.label).toBe('yes');
    expect(restored.triggers).toEqual(flow.triggers);
    expect((ir.runtime as any).graph.nodes.find((node: any) => node.id === 'work').disabled).toBe(true);
    expect((ir.runtime as any).graph.edges.find((edge: any) => edge.id === 'e1').branch).toBe('yes');
    expect(restored.nodes.find((node) => node.id === 'work')?.collapsed).toBeUndefined();
    expect(restored.edges.find((edge) => edge.id === 'e1')?.animated).toBeUndefined();
  });

  it('does not convert FlowScript-sourced IR back to WPS', () => {
    const flow: FlowScript = {
      meta: {
        id: 'flow-1',
        name: 'flow demo',
        created_at: '',
        updated_at: '',
        version: '1.0.0',
      },
      nodes: [{ id: 'start', type: 'start', x: 0, y: 0, params: {} }],
      edges: [],
      variables: {},
      settings: {
        default_delay: 0,
        error_handling: 'stop',
        max_retries: 0,
        retry_delay: 0,
        log_level: 'info',
      },
    };
    const ir = automationIrFromFlowScript(flow, 'air-flow');

    expect(() => automationIrToWps(ir)).toThrow('来源为 WPS');
  });

  it('rejects malformed IR', () => {
    expect(validateAutomationIR({ format: 'wps/2' })).toContain('格式');
    expect(validateAutomationIR({
      format: 'automation-ir/1',
      id: 'x',
      name: 'x',
      source: {},
      conditions: {},
      evidence: {},
      safety: {},
      fallback: {},
      variables: {},
      steps: [{}],
    })).toContain('缺少 tool');
  });

  it('rejects an imported FlowScript that would crash the editor', () => {
    expect(() => automationDocumentToFlowScript({
      meta: { id: 'broken', name: 'broken' },
      nodes: [{ id: 'bad', type: 'unknown_node', x: 0, y: 0, params: {} }],
      edges: [],
    })).toThrow('不支持的节点类型');
  });

  it('normalizes imported coordinates and identifiers before exposing them to the canvas', () => {
    const restored = automationDocumentToFlowScript({
      meta: { id: 'normalized', name: 'normalized' },
      nodes: [
        { id: ' start ', type: 'start', x: '10', y: '20', params: {} },
        { id: 'end', type: 'end', x: '210', y: '20', params: {} },
      ],
      edges: [
        { id: ' edge ', from_node: 'start', from_port: ' ', to_node: ' end ', to_port: '' },
      ],
      variables: {},
      settings: { default_delay: 0, error_handling: 'stop', max_retries: 0, retry_delay: 0, log_level: 'info' },
    });

    expect(restored.nodes).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: 'start', x: 10, y: 20 }),
      expect.objectContaining({ id: 'end', x: 210, y: 20 }),
    ]));
    expect(restored.edges[0]).toMatchObject({
      id: 'edge', from_node: 'start', from_port: 'output', to_node: 'end', to_port: 'input',
    });
    expect(flowToArpGraph(restored).edges[0]).toMatchObject({ source: 'start', target: 'end' });

    expect(() => automationDocumentToFlowScript({
      ...restored,
      nodes: [{ ...restored.nodes[0], x: ' ' }],
      edges: [],
    })).toThrow('缺少有效坐标');
  });

  it('rejects Flow IR without a canonical runtime graph at execution time', () => {
    const ir = {
      format: 'automation-ir/1' as const,
      id: 'air-old-flow',
      name: 'old flow',
      created: 1,
      source: { kind: 'flowscript' },
      conditions: {},
      evidence: {},
      safety: {},
      fallback: {},
      variables: {},
      steps: [{ tool: 'start', node_id: 'start', params: {} }],
    };

    expect(validateAutomationIR(ir)).toBeUndefined();
    expect(validateAutomationRuntime(ir)).toContain('runtime.kind');
    expect(() => automationRuntimeGraph(ir)).toThrow('runtime.kind');
  });

  it('keeps the projection fingerprint cross-runtime stable', () => {
    expect(automationProjectionFingerprint(
      [{ tool: 'start', node_id: 's', params: { x: 1, text: '你好' } }],
      { token: 'x' },
    )).toBe('sha256:f6996f5a13fe5cbb9a6c41e57a3ee5feb366e844d6055b54737d6f28fafcb302');
  });

  it('rejects dangling editor projection edges', () => {
    expect(validateAutomationIR({
      format: 'automation-ir/1',
      id: 'air-edge',
      name: 'bad edge',
      created: 1,
      source: { kind: 'flowscript' },
      conditions: {},
      evidence: {},
      safety: {},
      fallback: {},
      variables: {},
      steps: [{
        tool: 'start',
        node_id: 'start',
        params: {},
        outgoing: [{ to_node: 'missing' }],
      }],
    })).toContain('未知的 to_node');
  });

  it('validates the editor graph without a control-plane connection', () => {
    const flow: FlowScript = {
      meta: { id: 'offline', name: 'offline', created_at: '', updated_at: '', version: '1.0.0' },
      nodes: [
        { id: 'start', type: 'start', x: 0, y: 0, params: {} },
        { id: 'work', type: 'action_batch', x: 100, y: 0, params: { actions: [{ action: 'wait', duration_ms: 1 }] } },
        { id: 'end', type: 'end', x: 200, y: 0, params: {} },
      ],
      edges: [
        { id: 'e1', from_node: 'start', from_port: 'output', to_node: 'work', to_port: 'input' },
        { id: 'e2', from_node: 'work', from_port: 'output', to_node: 'end', to_port: 'input' },
      ],
      variables: {},
      settings: { default_delay: 0, error_handling: 'stop', max_retries: 0, retry_delay: 0, log_level: 'info' },
    };

    expect(validateAutomationLocally(automationIrFromFlowScript(flow))).toMatchObject({
      valid: true,
      entry_node: 'start',
      reachable_nodes: ['start', 'work', 'end'],
    });

    flow.edges = flow.edges.slice(0, 1);
    expect(validateAutomationLocally(automationIrFromFlowScript(flow))).toMatchObject({
      valid: false,
      errors: expect.arrayContaining([expect.stringContaining('没有后续路由')]),
    });
  });

  it('allows human-authored screen coordinates without a focus-window gate', () => {
    const validate = (nodes: FlowScript['nodes'], edges: FlowScript['edges']) => validateAutomationLocally(
      automationIrFromFlowScript({
        meta: { id: 'target-path', name: 'target path', created_at: '', updated_at: '', version: '1.0.0' },
        nodes,
        edges,
        variables: {},
        settings: { default_delay: 0, error_handling: 'stop', max_retries: 0, retry_delay: 0, log_level: 'info' },
      }),
    );
    const start = { id: 'start', type: 'start' as const, x: 0, y: 0, params: {} };
    const end = { id: 'end', type: 'end' as const, x: 400, y: 0, params: {} };
    const click = {
      id: 'click', type: 'action_batch' as const, x: 200, y: 0,
      params: { actions: [{ action: 'click', x: 10, y: 20 }] },
    };
    const edge = (id: string, from_node: string, to_node: string, from_port = 'output') => ({
      id, from_node, from_port, to_node, to_port: 'input',
    });

    const direct = validate(
      [start, click, end],
      [edge('e1', 'start', 'click'), edge('e2', 'click', 'end')],
    );
    const sameBatch = validate(
      [
        start,
        {
          ...click,
          params: { actions: [
            { action: 'focus_window', title: 'Target' },
            { action: 'click', x: 10, y: 20 },
          ] },
        },
        end,
      ],
      [edge('e1', 'start', 'click'), edge('e2', 'click', 'end')],
    );
    const previousBatch = validate(
      [
        start,
        {
          id: 'focus', type: 'action_batch' as const, x: 100, y: 0,
          params: { actions: [{ action: 'focus_window', hwnd: 101 }] },
        },
        click,
        end,
      ],
      [edge('e1', 'start', 'focus'), edge('e2', 'focus', 'click'), edge('e3', 'click', 'end')],
    );
    const lateFocus = validate(
      [
        start,
        {
          ...click,
          params: { actions: [
            { action: 'click', x: 10, y: 20 },
            { action: 'focus_window', title: 'Target' },
          ] },
        },
        end,
      ],
      [edge('e1', 'start', 'click'), edge('e2', 'click', 'end')],
    );
    const branched = validate(
      [
        start,
        {
          id: 'branch', type: 'condition' as const, x: 100, y: 0,
          params: { condition_type: 'variable_equals', variable_name: 'use_focus', variable_value: 'yes' },
        },
        {
          id: 'focus', type: 'action_batch' as const, x: 200, y: -100,
          params: { actions: [{ action: 'focus_window', title: 'Target' }] },
        },
        { ...click, x: 300 },
        end,
      ],
      [
        edge('e1', 'start', 'branch'),
        edge('e2', 'branch', 'focus', 'yes'),
        edge('e3', 'branch', 'click', 'no'),
        edge('e4', 'focus', 'click'),
        edge('e5', 'click', 'end'),
      ],
    );

    for (const result of [direct, sameBatch, previousBatch, lateFocus, branched]) {
      expect(result.valid).toBe(true);
    }
  });

  it('fails local validation for unsupported parameter branches and empty native configs', () => {
    const flow: FlowScript = {
      meta: { id: 'offline-invalid', name: 'offline-invalid', created_at: '', updated_at: '', version: '1.0.0' },
      nodes: [
        { id: 'start', type: 'start', x: 0, y: 0, params: {} },
        { id: 'legacy', type: 'screenshot', x: 100, y: 0, params: { save_path: 'legacy.png' } },
        { id: 'empty', type: 'action_batch', x: 200, y: 0, params: { actions: [] } },
        { id: 'text', type: 'click_text', x: 400, y: 0, params: { text: '' } },
        { id: 'input', type: 'input_text', x: 500, y: 0, params: { text: '', click_before: false } },
        { id: 'replay', type: 'replay_step', x: 600, y: 0, params: { step: {} } },
        { id: 'launch', type: 'launch_program', x: 700, y: 0, params: { target: '' } },
        { id: 'focus', type: 'focus_window', x: 800, y: 0, params: { title: '' } },
        { id: 'end', type: 'end', x: 900, y: 0, params: {} },
      ],
      edges: [
        { id: 'e1', from_node: 'start', from_port: 'output', to_node: 'legacy', to_port: 'input' },
        { id: 'e2', from_node: 'legacy', from_port: 'output', to_node: 'empty', to_port: 'input' },
        { id: 'e3', from_node: 'empty', from_port: 'output', to_node: 'text', to_port: 'input' },
        { id: 'e4', from_node: 'text', from_port: 'output', to_node: 'input', to_port: 'input' },
        { id: 'e5', from_node: 'input', from_port: 'output', to_node: 'replay', to_port: 'input' },
        { id: 'e6', from_node: 'replay', from_port: 'output', to_node: 'launch', to_port: 'input' },
        { id: 'e7', from_node: 'launch', from_port: 'output', to_node: 'focus', to_port: 'input' },
        { id: 'e8', from_node: 'focus', from_port: 'output', to_node: 'end', to_port: 'input' },
      ],
      variables: {},
      settings: { default_delay: 0, error_handling: 'stop', max_retries: 0, retry_delay: 0, log_level: 'info' },
    };

    expect(validateAutomationLocally(automationIrFromFlowScript(flow))).toMatchObject({
      valid: false,
      errors: expect.arrayContaining([
        expect.stringContaining('只能走旧执行器'),
        expect.stringContaining('至少一个动作'),
        expect.stringContaining('click_element 缺少文本或视觉锚点'),
        expect.stringContaining('type_text 的 text 不能为空'),
        expect.stringContaining('WPS step 必须指定 tool'),
        expect.stringContaining('launch 缺少 command'),
        expect.stringContaining('focus_window 缺少窗口标题或 hwnd'),
      ]),
    });
  });
});
