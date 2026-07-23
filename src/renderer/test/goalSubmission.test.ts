import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ControlPlaneClient } from '../platform/client';
import { submitGoalThroughControlPlane } from '../platform/useControlPlane';

describe('desktop goal submission runtime selection', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    delete (window as any).electronAPI;
  });

  it('passes runtime identity only to the atomic run start boundary', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(900);
    const order: string[] = [];
    const graph = { format: 'arp/1', id: 'graph-1' };
    const contract = {
      format: 'task-contract/1',
      task_id: 'task-1',
      stages: [{
        type: 'ExecuteDesktopTask',
        required_capabilities: [],
        execution: null,
      }],
      session: null,
      intent: {},
    };
    (window as any).electronAPI = {
      prepareDesktopHandoff: vi.fn(async () => ({
        yielded: true,
        control_surface_pid: 7712,
        prepared_at: 800,
      })),
    };
    const client = {
      compileTask: vi.fn(async () => {
        order.push('compile');
        return { contract, graph };
      }),
      captureDesktopContext: vi.fn(async () => ({
        format: 'desktop-target/2',
        hwnd: 0,
        root_hwnd: 0,
        title: '',
        process_id: 0,
        process_identity: '',
        class_name: '',
        rect: [],
        captured_at: 800,
        foreground_verified: false,
        usable: false,
        failure_type: 'target_binding_missing',
        reason: 'no foreground target',
      })),
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
    expect(client.captureDesktopContext).toHaveBeenCalledWith(7712);
    expect(JSON.stringify({ submittedGraph, inputs })).not.toContain('runtime_version');
    expect(JSON.stringify({ submittedGraph, inputs })).not.toContain('vnext');
  });
});
