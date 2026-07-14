import { describe, expect, it } from 'vitest';
import {
  automationIrFromFlowScript,
  automationIrFromWps,
  automationDocumentToFlowScript,
  automationIrToFlowScript,
  automationIrToWps,
  automationProjectionFingerprint,
  automationRuntimeGraph,
  validateAutomationRuntime,
  validateAutomationIR,
} from '../utils/automationIR';
import type { FlowScript } from '../types';

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
});
