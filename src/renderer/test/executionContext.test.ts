import { afterEach, describe, expect, it, vi } from 'vitest';
import { buildClientExecutionContext } from '../platform/executionContext';

describe('client-owned execution context', () => {
  afterEach(() => {
    delete (window as any).electronAPI;
  });

  it('yields the native control surface and captures a target without assigning a task id', async () => {
    const prepareDesktopHandoff = vi.fn(async () => ({
      yielded: true,
      control_surface_pid: 7712,
      prepared_at: 800,
    }));
    const captureTarget = vi.fn(async () => ({
      format: 'desktop-target/1' as const,
      hwnd: 991,
      title: 'External target',
      process_id: 8812,
      class_name: 'TargetWindow',
      rect: [0, 0, 1200, 800],
      captured_at: 850,
      foreground_verified: true,
      usable: true,
      failure_type: '',
      reason: '',
    }));
    (window as any).electronAPI = { prepareDesktopHandoff };

    const context = await buildClientExecutionContext(900, captureTarget);

    expect(prepareDesktopHandoff).toHaveBeenCalledOnce();
    expect(captureTarget).toHaveBeenCalledWith({
      yielded: true,
      control_surface_pid: 7712,
      prepared_at: 800,
    });
    expect(context).toEqual({
      format: 'execution-context/1',
      entrypoint: 'native_agent_cockpit',
      submitted_at: 900,
      desktop_handoff: {
        required: true,
        strategy: 'foreground_after_control_surface_yield',
        yielded: true,
        control_surface_pid: 7712,
        prepared_at: 800,
        reason: '',
      },
      target_window: {
        format: 'desktop-target/1',
        hwnd: 991,
        title: 'External target',
        process_id: 8812,
        class_name: 'TargetWindow',
        rect: [0, 0, 1200, 800],
        captured_at: 850,
        foreground_verified: true,
        usable: true,
        failure_type: '',
        reason: '',
      },
    });
    expect(context).not.toHaveProperty('task_id');
  });

  it('keeps the web context client-owned when no native handoff bridge exists', async () => {
    const context = await buildClientExecutionContext(901);

    expect(context).toEqual({
      format: 'execution-context/1',
      entrypoint: 'web_workbench',
      submitted_at: 901,
      desktop_handoff: {
        required: true,
        strategy: 'foreground_after_control_surface_yield',
        yielded: false,
        control_surface_pid: 0,
        prepared_at: 0,
        reason: 'native_bridge_unavailable',
      },
      target_window: null,
    });
  });

  it('fails before submission when the native surface keeps foreground', async () => {
    (window as any).electronAPI = {
      prepareDesktopHandoff: vi.fn(async () => ({
        yielded: false,
        control_surface_pid: 7712,
        prepared_at: 800,
        reason: 'control_surface_still_focused',
      })),
    };

    await expect(buildClientExecutionContext(900)).rejects.toThrow(
      'control_surface_still_focused',
    );
  });
});
