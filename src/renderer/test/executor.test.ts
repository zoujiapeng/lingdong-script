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
});
