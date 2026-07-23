import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ControlPlaneClient } from '../platform/client';
import {
  resumeRunThroughControlPlane,
  submitGoalThroughControlPlane,
} from '../platform/useControlPlane';

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
        startPaused: boolean,
      ) => {
        order.push(`start:${runtimeVersion}:${String(startPaused)}`);
        return { run_id: 'arp-1' };
      }),
    };

    await submitGoalThroughControlPlane(
      client as unknown as ControlPlaneClient,
      '完成当前界面上的测试。',
      24,
      true,
      'vnext',
      true,
    );

    expect(order).toEqual(['compile', 'start:vnext:true']);
    expect(client.compileTask).toHaveBeenCalledWith(
      '完成当前界面上的测试。', 24, true,
    );
    const [submittedGraph, inputs, runtimeVersion, startPaused] = client.startGraph.mock.calls[0];
    expect(submittedGraph).toBe(graph);
    expect(runtimeVersion).toBe('vnext');
    expect(startPaused).toBe(true);
    expect(client.captureDesktopContext).toHaveBeenCalledWith(7712);
    expect(JSON.stringify({ submittedGraph, inputs })).not.toContain('runtime_version');
    expect(JSON.stringify({ submittedGraph, inputs })).not.toContain('vnext');
  });

  it('yields the native control surface before resuming a desktop run', async () => {
    const order: string[] = [];
    (window as any).electronAPI = {
      prepareDesktopHandoff: vi.fn(async () => {
        order.push('handoff');
        return { yielded: true, control_surface_pid: 7, prepared_at: 10 };
      }),
    };
    const client = {
      resumeRun: vi.fn(async (runId: string) => {
        order.push(`resume:${runId}`);
        return { run_id: runId };
      }),
    };

    await resumeRunThroughControlPlane(
      client as unknown as ControlPlaneClient,
      'arp-paused',
      {
        run_id: 'arp-paused',
        graph_id: 'graph',
        graph_name: 'desktop',
        status: 'paused',
        execution_context: {
          desktop_handoff: { required: true },
        },
      },
    );

    expect(order).toEqual(['handoff', 'resume:arp-paused']);
  });

  it('fails closed when resume handoff fails and skips it for non-desktop runs', async () => {
    (window as any).electronAPI = {
      prepareDesktopHandoff: vi.fn(async () => ({
        yielded: false,
        control_surface_pid: 7,
        prepared_at: 10,
        reason: 'still focused',
      })),
    };
    const client = { resumeRun: vi.fn(async () => ({ ok: true })) };
    const desktop = {
      run_id: 'arp-desktop', graph_id: 'graph', graph_name: 'desktop', status: 'paused',
      execution_context: { desktop_handoff: { required: true } },
    };
    await expect(resumeRunThroughControlPlane(
      client as unknown as ControlPlaneClient, 'arp-desktop', desktop,
    )).rejects.toThrow('still focused');
    expect(client.resumeRun).not.toHaveBeenCalled();

    await resumeRunThroughControlPlane(
      client as unknown as ControlPlaneClient,
      'arp-local',
      {
        run_id: 'arp-local', graph_id: 'graph', graph_name: 'local', status: 'paused',
        execution_context: { desktop_handoff: { required: false } },
      },
    );
    expect(client.resumeRun).toHaveBeenCalledWith('arp-local');
    expect((window as any).electronAPI.prepareDesktopHandoff).toHaveBeenCalledOnce();
  });
});
