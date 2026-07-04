import { describe, it, expect, afterEach } from 'vitest';
import { createFlowExecutor } from '../store/executor';
import type { FlowScript, LogEntry } from '../types';

function flowWith(node: FlowScript['nodes'][number]): FlowScript {
  return {
    meta: { name: 'executor-test', version: '1.0.0', created_at: '', updated_at: '' },
    nodes: [
      { id: 'start', type: 'start', x: 0, y: 0, params: {}, status: 'idle' },
      node,
      { id: 'end', type: 'end', x: 0, y: 0, params: {}, status: 'idle' },
    ],
    edges: [
      { id: 'e1', from_node: 'start', from_port: 'output', to_node: node.id, to_port: 'input' },
      { id: 'e2', from_node: node.id, from_port: 'output', to_node: 'end', to_port: 'input' },
    ],
    variables: {},
    settings: { error_handling: 'stop' },
  };
}

async function runFlow(script: FlowScript, callFeature: (name: string, params: any) => Promise<any> | any) {
  const logs: Array<Omit<LogEntry, 'timestamp'>> = [];
  const statuses: Array<[string, string, string | undefined]> = [];
  (globalThis as any).window = { electronAPI: { callFeature } };

  const done = new Promise<'completed' | 'stopped' | 'error'>((resolve) => {
    createFlowExecutor(script, {
      onNodeStatus: (nodeId, status, error) => statuses.push([nodeId, status, error]),
      onLog: (entry) => logs.push(entry),
      onDone: resolve,
      onCurrentNode: () => {},
    });
  });

  return { status: await done, logs, statuses };
}

afterEach(() => {
  delete (globalThis as any).window;
});

describe('createFlowExecutor replay/DSL bridges', () => {
  it('executes wait_process through the registered feature bridge', async () => {
    const calls: Array<[string, any]> = [];
    const script = flowWith({
      id: 'wait',
      type: 'wait_process',
      x: 0,
      y: 0,
      params: { name: 'demo.exe', wait_for: 'exit', timeout: 7 },
      status: 'idle',
    });

    const result = await runFlow(script, (name, params) => {
      calls.push([name, params]);
      return { success: true };
    });

    expect(result.status).toBe('completed');
    expect(calls).toEqual([
      ['wait_process_exit', { name: 'demo.exe', timeout: 7 }],
    ]);
  });

  it('fails WinPilot-only tool_call placeholders instead of reporting success', async () => {
    const calls: Array<[string, any]> = [];
    const script = flowWith({
      id: 'draw',
      type: 'tool_call',
      x: 0,
      y: 0,
      params: {
        feature_name: 'draw_strokes',
        params: { _requires_wps_replay: true, _wps_tool: 'draw_strokes' },
      },
      status: 'idle',
    });

    const result = await runFlow(script, (name, params) => {
      calls.push([name, params]);
      return { success: true };
    });

    expect(result.status).toBe('error');
    expect(calls).toEqual([]);
    expect(result.statuses).toContainEqual([
      'draw',
      'error',
      'WinPilot-only replay step "draw_strokes" must run through the original .wps.json Replayer',
    ]);
  });

  it('fails WPS-only loop placeholders instead of browser-simulating control flow', async () => {
    const calls: Array<[string, any]> = [];
    const script = flowWith({
      id: 'loop',
      type: 'loop',
      x: 0,
      y: 0,
      params: {
        loop_type: 'condition',
        _requires_wps_replay: true,
        _wps_tool: 'ctrl.loop',
        _wps_loop_type: 'until',
      },
      status: 'idle',
    });

    const result = await runFlow(script, (name, params) => {
      calls.push([name, params]);
      return { success: true };
    });

    expect(result.status).toBe('error');
    expect(calls).toEqual([]);
    expect(result.statuses).toContainEqual([
      'loop',
      'error',
      'WinPilot control-flow step "ctrl.loop:until" must run through the original .wps.json Replayer',
    ]);
  });

  it('executes process_action through feature bridge instead of simulating success', async () => {
    const calls: Array<[string, any]> = [];
    const script = flowWith({
      id: 'proc',
      type: 'process_action',
      x: 0,
      y: 0,
      params: { action: 'wait_exit', name: 'demo.exe', timeout: 9 },
      status: 'idle',
    });

    const result = await runFlow(script, (name, params) => {
      calls.push([name, params]);
      return { success: true };
    });

    expect(result.status).toBe('completed');
    expect(calls).toEqual([
      ['wait_process_exit', { name: 'demo.exe', timeout: 9 }],
    ]);
  });

  it('treats clipboard_action feature failure as node failure', async () => {
    const script = flowWith({
      id: 'clip',
      type: 'clipboard_action',
      x: 0,
      y: 0,
      params: { action: 'set', text: 'hello' },
      status: 'idle',
    });

    const result = await runFlow(script, () => ({ success: false, error: 'clipboard denied' }));

    expect(result.status).toBe('error');
    expect(result.statuses).toContainEqual(['clip', 'error', 'clipboard denied']);
  });

  it('treats default feature-map failure as node failure', async () => {
    const script = flowWith({
      id: 'launch',
      type: 'launch_program',
      x: 0,
      y: 0,
      params: { target: 'missing.exe' },
      status: 'idle',
    });

    const result = await runFlow(script, () => ({ success: false, error: 'not found' }));

    expect(result.status).toBe('error');
    expect(result.statuses).toContainEqual(['launch', 'error', 'not found']);
  });

  it('executes system_power through feature bridge', async () => {
    const calls: Array<[string, any]> = [];
    const script = flowWith({
      id: 'power',
      type: 'system_power',
      x: 0,
      y: 0,
      params: { action: 'hibernate', confirm: true },
      status: 'idle',
    });

    const result = await runFlow(script, (name, params) => {
      calls.push([name, params]);
      return { success: true };
    });

    expect(result.status).toBe('completed');
    expect(calls).toEqual([
      ['hibernate', { confirm: true }],
    ]);
  });

  it('does not pass unsupported delay params to direct hibernate nodes', async () => {
    const calls: Array<[string, any]> = [];
    const script = flowWith({
      id: 'hibernate',
      type: 'hibernate',
      x: 0,
      y: 0,
      params: { delay_seconds: 30, confirm: false },
      status: 'idle',
    });

    const result = await runFlow(script, (name, params) => {
      calls.push([name, params]);
      return { success: true };
    });

    expect(result.status).toBe('completed');
    expect(calls).toEqual([
      ['hibernate', { confirm: false }],
    ]);
  });

  it('treats web_browser feature failure as node failure', async () => {
    const script = flowWith({
      id: 'web',
      type: 'web_browser',
      x: 0,
      y: 0,
      params: { action: 'navigate', url: 'https://example.invalid' },
      status: 'idle',
    });

    const result = await runFlow(script, () => ({ success: false, error: 'browser unavailable' }));

    expect(result.status).toBe('error');
    expect(result.statuses).toContainEqual(['web', 'error', 'browser unavailable']);
  });

  it('treats window_control feature failure as node failure', async () => {
    const script = flowWith({
      id: 'win',
      type: 'window_control',
      x: 0,
      y: 0,
      params: { operation: 'close', title: 'Missing' },
      status: 'idle',
    });

    const result = await runFlow(script, () => ({ success: false, error: 'window missing' }));

    expect(result.status).toBe('error');
    expect(result.statuses).toContainEqual(['win', 'error', 'window missing']);
  });
});
