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
    expect(ir.safety.mutates_desktop).toBe(true);
    expect(ir.fallback).toEqual({
      on_preflight_fail: 'stop',
      on_step_fail: 'stop',
    });
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
    expect(ir.safety.executor).toBe('flowscript');
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

  it('preserves WPS replay contracts through editable FlowScript and canonical save', () => {
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
    source.safety = { ...source.safety, audit_tag: 'keep-safety' };
    source.fallback = { ...source.fallback, recovery: 'keep-fallback' };

    expect(automationIrToWps(source).evidence).toEqual(source.evidence);

    const flow = automationIrToFlowScript(source);
    const replay = flow.nodes.find((node) => node.type === 'replay_step')!;
    expect((replay.params as any).session).toBe('lesson-42');
    expect((replay.params as any).postflight_evidence).toEqual(source.evidence);

    const saved = automationIrFromFlowScript(flow, 'air-saved');
    expect(saved.conditions).toEqual(source.conditions);
    expect(saved.evidence).toEqual(source.evidence);
    expect(saved.safety).toEqual(source.safety);
    expect(saved.fallback).toEqual(source.fallback);
    expect((saved.runtime as any).graph.nodes.find((node: any) => node.kind === 'replay_step').config)
      .toMatchObject({ postflight_evidence: source.evidence, session: 'lesson-42' });
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

  it('fails local validation for unsupported parameter branches and empty native configs', () => {
    const flow: FlowScript = {
      meta: { id: 'offline-invalid', name: 'offline-invalid', created_at: '', updated_at: '', version: '1.0.0' },
      nodes: [
        { id: 'start', type: 'start', x: 0, y: 0, params: {} },
        { id: 'legacy', type: 'screenshot', x: 100, y: 0, params: { save_path: 'legacy.png' } },
        { id: 'empty', type: 'action_batch', x: 200, y: 0, params: { actions: [] } },
        { id: 'end', type: 'end', x: 300, y: 0, params: {} },
      ],
      edges: [
        { id: 'e1', from_node: 'start', from_port: 'output', to_node: 'legacy', to_port: 'input' },
        { id: 'e2', from_node: 'legacy', from_port: 'output', to_node: 'empty', to_port: 'input' },
        { id: 'e3', from_node: 'empty', from_port: 'output', to_node: 'end', to_port: 'input' },
      ],
      variables: {},
      settings: { default_delay: 0, error_handling: 'stop', max_retries: 0, retry_delay: 0, log_level: 'info' },
    };

    expect(validateAutomationLocally(automationIrFromFlowScript(flow))).toMatchObject({
      valid: false,
      errors: expect.arrayContaining([
        expect.stringContaining('只能走旧执行器'),
        expect.stringContaining('至少一个动作'),
      ]),
    });
  });
});
