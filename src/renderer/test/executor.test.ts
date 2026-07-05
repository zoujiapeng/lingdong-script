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

function flowSequence(
  nodes: FlowScript['nodes'],
  edges: FlowScript['edges'],
  variables: Record<string, string> = {},
): FlowScript {
  return {
    meta: { name: 'executor-test', version: '1.0.0', created_at: '', updated_at: '' },
    nodes,
    edges,
    variables,
    settings: { error_handling: 'stop' },
  };
}

async function runFlow(script: FlowScript, callFeature?: (name: string, params: any) => Promise<any> | any) {
  const logs: Array<Omit<LogEntry, 'timestamp'>> = [];
  const statuses: Array<[string, string, string | undefined]> = [];
  (globalThis as any).window = callFeature ? { electronAPI: { callFeature } } : {};

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

  it('reports WPS-only tool_call reason even without a feature bridge', async () => {
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

    const result = await runFlow(script);

    expect(result.status).toBe('error');
    expect(result.statuses).toContainEqual([
      'draw',
      'error',
      'WinPilot-only replay step "draw_strokes" must run through the original .wps.json Replayer',
    ]);
  });

  it('requires a real feature bridge for tool_call nodes', async () => {
    const script = flowWith({
      id: 'call',
      type: 'tool_call',
      x: 0,
      y: 0,
      params: {
        feature_name: 'launch_program',
        params: { target: 'notepad.exe' },
      },
      status: 'idle',
    });

    const result = await runFlow(script);

    expect(result.status).toBe('error');
    expect(result.statuses).toContainEqual([
      'call',
      'error',
      'launch_program requires electronAPI.callFeature',
    ]);
  });

  it('treats bridge type:error responses as node failures', async () => {
    const script = flowWith({
      id: 'call',
      type: 'tool_call',
      x: 0,
      y: 0,
      params: {
        feature_name: 'launch_program',
        params: { target: 'missing.exe' },
      },
      status: 'idle',
    });

    const result = await runFlow(script, () => ({ type: 'error', error: 'feature child crashed' }));

    expect(result.status).toBe('error');
    expect(result.statuses).toContainEqual([
      'call',
      'error',
      'feature child crashed',
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

  it('requires a real bridge for WPS-imported coordinate clicks', async () => {
    const script = flowWith({
      id: 'click',
      type: 'click',
      x: 0,
      y: 0,
      params: { x: 10, y: 20, method: 'single', _source_wps_replay: true },
      status: 'idle',
    });

    const result = await runFlow(script);

    expect(result.status).toBe('error');
    expect(result.statuses).toContainEqual([
      'click',
      'error',
      'click_target requires electronAPI.callFeature',
    ]);
  });

  it('executes WPS-imported coordinate clicks through feature bridge', async () => {
    const calls: Array<[string, any]> = [];
    const script = flowWith({
      id: 'click',
      type: 'click',
      x: 0,
      y: 0,
      params: { x: 10, y: 20, method: 'single', _source_wps_replay: true },
      status: 'idle',
    });

    const result = await runFlow(script, (name, params) => {
      calls.push([name, params]);
      return { type: 'result', result: { success: true, result: true } };
    });

    expect(result.status).toBe('completed');
    expect(calls).toEqual([
      ['click_target', {
        target: '10,20',
        region: '',
        timeout: 5,
        human_like: true,
        double: false,
        allow_uia: false,
        physical: true,
      }],
    ]);
  });

  it('requires a real bridge for WPS-imported window waits', async () => {
    const script = flowWith({
      id: 'wait',
      type: 'wait',
      x: 0,
      y: 0,
      params: { wait_type: 'window', window_title: 'Save As', timeout: 1, _source_wps_replay: true },
      status: 'idle',
    });

    const result = await runFlow(script);

    expect(result.status).toBe('error');
    expect(result.statuses).toContainEqual([
      'wait',
      'error',
      'focus_window requires electronAPI.callFeature',
    ]);
  });

  it('executes WPS-imported window waits through focus_window feature bridge', async () => {
    const calls: Array<[string, any]> = [];
    const script = flowWith({
      id: 'wait',
      type: 'wait',
      x: 0,
      y: 0,
      params: { wait_type: 'window', window_title: 'Save As', timeout: 1, _source_wps_replay: true },
      status: 'idle',
    });

    const result = await runFlow(script, (name, params) => {
      calls.push([name, params]);
      return { success: true };
    });

    expect(result.status).toBe('completed');
    expect(calls).toEqual([
      ['focus_window', { title: 'Save As', exact: false }],
    ]);
  });

  it('uses clipboard typing for WPS-imported non-ASCII text without unsupported params', async () => {
    const calls: Array<[string, any]> = [];
    const script = flowWith({
      id: 'type',
      type: 'input_text',
      x: 0,
      y: 0,
      params: { text: '健康科技', click_before: false, clear_before: false, _source_wps_replay: true },
      status: 'idle',
    });

    const result = await runFlow(script, (name, params) => {
      calls.push([name, params]);
      return { type: 'result', result: { success: true, result: true } };
    });

    expect(result.status).toBe('completed');
    expect(calls).toEqual([
      ['type_clipboard', { text: '健康科技' }],
    ]);
  });

  it('treats wrapped feature false results as node failures and strips internal params', async () => {
    const calls: Array<[string, any]> = [];
    const script = flowWith({
      id: 'launch',
      type: 'launch_program',
      x: 0,
      y: 0,
      params: { target: 'missing.exe', _source_wps_replay: true },
      status: 'idle',
    });

    const result = await runFlow(script, (name, params) => {
      calls.push([name, params]);
      return { type: 'result', result: { success: true, result: false } };
    });

    expect(result.status).toBe('error');
    expect(calls).toEqual([
      ['launch_program', { target: 'missing.exe' }],
    ]);
    expect(result.statuses).toContainEqual([
      'launch',
      'error',
      'launch_program returned false',
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

  it('stores clipboard_get output into FlowScript variables', async () => {
    const script = flowWith({
      id: 'clip',
      type: 'clipboard_get',
      x: 0,
      y: 0,
      params: { into: 'clip_text' },
      status: 'idle',
    });

    const result = await runFlow(script, () => ({ success: true, result: { text: 'CLIP_VALUE' } }));

    expect(result.status).toBe('completed');
    expect(script.variables.clip_text).toBe('CLIP_VALUE');
  });

  it('routes variable conditions using runtime variables', async () => {
    const script = flowSequence(
      [
        { id: 'start', type: 'start', x: 0, y: 0, params: {}, status: 'idle' },
        { id: 'set', type: 'variable_set', x: 0, y: 0, params: { name: 'score', value: '5' }, status: 'idle' },
        { id: 'cond', type: 'condition', x: 0, y: 0, params: { condition_type: 'variable_greater', variable_name: 'score', variable_value: '3' }, status: 'idle' },
        { id: 'yes', type: 'variable_set', x: 0, y: 0, params: { name: 'branch', value: 'yes' }, status: 'idle' },
        { id: 'no', type: 'variable_set', x: 0, y: 0, params: { name: 'branch', value: 'no' }, status: 'idle' },
        { id: 'end', type: 'end', x: 0, y: 0, params: {}, status: 'idle' },
      ],
      [
        { id: 'e1', from_node: 'start', from_port: 'output', to_node: 'set', to_port: 'input' },
        { id: 'e2', from_node: 'set', from_port: 'output', to_node: 'cond', to_port: 'input' },
        { id: 'e3', from_node: 'cond', from_port: 'yes', to_node: 'yes', to_port: 'input' },
        { id: 'e4', from_node: 'cond', from_port: 'no', to_node: 'no', to_port: 'input' },
        { id: 'e5', from_node: 'yes', from_port: 'output', to_node: 'end', to_port: 'input' },
        { id: 'e6', from_node: 'no', from_port: 'output', to_node: 'end', to_port: 'input' },
      ],
    );

    const result = await runFlow(script, () => ({ success: true }));

    expect(result.status).toBe('completed');
    expect(script.variables.score).toBe('5');
    expect(script.variables.branch).toBe('yes');
  });

  it('keeps browser-preview simulations available by default', async () => {
    const script = flowWith({
      id: 'color',
      type: 'color_check',
      x: 0,
      y: 0,
      params: { expected_color: '#ffffff' },
      status: 'idle',
    });

    const result = await runFlow(script);

    expect(result.status).toBe('completed');
  });

  it('fails simulated-only nodes when require_real_features is enabled', async () => {
    const script = flowWith({
      id: 'color',
      type: 'color_check',
      x: 0,
      y: 0,
      params: { expected_color: '#ffffff' },
      status: 'idle',
    });
    script.settings.require_real_features = true;

    const result = await runFlow(script);

    expect(result.status).toBe('error');
    expect(result.statuses).toContainEqual([
      'color',
      'error',
      'color_check requires real feature execution; browser simulation is disabled',
    ]);
  });

  it('fails simulated waits when require_real_features is enabled', async () => {
    const script = flowWith({
      id: 'wait',
      type: 'wait',
      x: 0,
      y: 0,
      params: { wait_type: 'condition', timeout: 1 },
      status: 'idle',
    });
    script.settings.require_real_features = true;

    const result = await runFlow(script);

    expect(result.status).toBe('error');
    expect(result.statuses).toContainEqual([
      'wait',
      'error',
      'wait requires real feature execution; browser simulation is disabled',
    ]);
  });

  it('fails simulated unknown conditions when require_real_features is enabled', async () => {
    const script = flowWith({
      id: 'cond',
      type: 'condition',
      x: 0,
      y: 0,
      params: { condition_type: 'text_exists', text: 'Done' },
      status: 'idle',
    });
    script.settings.require_real_features = true;

    const result = await runFlow(script);

    expect(result.status).toBe('error');
    expect(result.statuses).toContainEqual([
      'cond',
      'error',
      'condition requires real feature execution; browser simulation is disabled',
    ]);
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

  it('fails unsupported node types instead of reporting success', async () => {
    const script = flowWith({
      id: 'mystery',
      type: 'not_a_real_node' as any,
      x: 0,
      y: 0,
      params: {},
      status: 'idle',
    });

    const result = await runFlow(script, () => ({ success: true }));

    expect(result.status).toBe('error');
    expect(result.statuses).toContainEqual([
      'mystery',
      'error',
      'Unsupported FlowScript node type: not_a_real_node',
    ]);
  });
});
