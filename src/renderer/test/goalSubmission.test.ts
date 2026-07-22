import { describe, expect, it, vi } from 'vitest';
import type { ControlPlaneClient } from '../platform/client';
import { submitGoalThroughControlPlane } from '../platform/useControlPlane';

describe('desktop goal submission runtime selection', () => {
  it('passes runtime identity only to the atomic run start boundary', async () => {
    const order: string[] = [];
    const graph = { format: 'arp/1', id: 'graph-1' };
    const contract = {
      format: 'task-contract/1',
      task_id: 'task-1',
      stages: [],
      session: null,
      intent: {},
    };
    const client = {
      compileTask: vi.fn(async () => {
        order.push('compile');
        return { contract, graph };
      }),
      captureDesktopContext: vi.fn(),
      startGraph: vi.fn(async (
        _graph: unknown,
        _inputs: unknown,
        runtimeVersion: string,
      ) => {
        order.push(`start:${runtimeVersion}`);
        return { run_id: 'arp-1' };
      }),
    };

    await submitGoalThroughControlPlane(
      client as unknown as ControlPlaneClient,
      '完成当前界面上的测试。',
      24,
      true,
      'vnext',
    );

    expect(order).toEqual(['compile', 'start:vnext']);
    expect(client.compileTask).toHaveBeenCalledWith(
      '完成当前界面上的测试。', 24, true,
    );
    const [submittedGraph, inputs, runtimeVersion] = client.startGraph.mock.calls[0];
    expect(submittedGraph).toBe(graph);
    expect(runtimeVersion).toBe('vnext');
    expect(JSON.stringify({ submittedGraph, inputs })).not.toContain('runtime_version');
    expect(JSON.stringify({ submittedGraph, inputs })).not.toContain('vnext');
  });
});
