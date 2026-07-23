import { afterEach, describe, expect, it, vi } from 'vitest';
import type { TaskContract, TaskStage } from '../platform/contracts';
import {
  buildClientRunInputs,
  requiresDesktopHandoff,
} from '../platform/executionContext';

function contractWith(stages: Partial<TaskStage>[]): TaskContract {
  return {
    format: 'task-contract/1',
    task_id: 'task-ctx-1',
    task: 'operate the visible interface',
    intent: {
      original_text: 'operate the visible interface',
      operation: ['interact'],
      media_type: '',
      channel: '',
      recipient: '',
      source: 'active_window',
      capture_scope: 'window',
      destination: '',
      send_mode: 'send',
      subject: '',
      quality: 'normal',
      confidence: 1,
      ambiguous_fields: [],
      provenance: {},
      interaction: {
        active: false,
        mode: '',
        actors: [],
        role_assignments: {},
        turn_owner: '',
        session_mode: '',
        continuation_policy: '',
        target: '',
        region_hint: '',
        action_modalities: [],
        user_started: false,
        expected_external_change: false,
        confidence: 1,
        signals: [],
        ambiguous_fields: [],
        provenance: {},
      },
    },
    stages: stages.map((stage, index) => ({
      id: `stage-${index + 1}`,
      type: 'ObserveScene',
      name: 'stage',
      parameters: {},
      depends_on: [],
      inputs: [],
      outputs: [],
      required_capabilities: [],
      obligations: [],
      execution: null,
      allow_skill_replay: false,
      ...stage,
    })),
    obligations: [],
    final_conditions: [],
    session: null,
    satisfiable: true,
    missing_capabilities: [],
    experience_policy: 'disabled',
    compiler_version: 'test',
  };
}

describe('native execution context', () => {
  afterEach(() => {
    delete (window as any).electronAPI;
  });

  it('derives foreground handoff from typed stages instead of task keywords', () => {
    expect(requiresDesktopHandoff(contractWith([{ type: 'ObserveScene' }]))).toBe(true);
    expect(requiresDesktopHandoff(contractWith([{ type: 'SendArtifact' }]))).toBe(true);
    expect(requiresDesktopHandoff(contractWith([{ type: 'ExecuteDesktopTask' }]))).toBe(true);
    expect(requiresDesktopHandoff(contractWith([{ type: 'ExecuteWorkspaceTask' }]))).toBe(false);
    expect(requiresDesktopHandoff(contractWith([{ type: 'ResearchInformation' }]))).toBe(false);
    expect(requiresDesktopHandoff(contractWith([{ type: 'TransformData' }]))).toBe(false);
    expect(requiresDesktopHandoff(contractWith([{ type: 'ManageAutomation' }]))).toBe(false);
    expect(requiresDesktopHandoff(contractWith([]))).toBe(false);
  });

  it('yields the native control surface and records provenance in run inputs', async () => {
    const prepareDesktopHandoff = vi.fn(async () => ({
      yielded: true,
      control_surface_pid: 7712,
      prepared_at: 800,
    }));
    const captureTarget = vi.fn(async () => ({
      format: 'desktop-target/2' as const,
      hwnd: 991,
      root_hwnd: 991,
      title: 'External target',
      process_id: 8812,
      process_identity: 'process-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
      class_name: 'TargetWindow',
      rect: [0, 0, 1200, 800] as [number, number, number, number],
      captured_at: 850,
      foreground_verified: true,
      usable: true,
      failure_type: '',
      reason: '',
    }));
    (window as any).electronAPI = { prepareDesktopHandoff };

    const inputs = await buildClientRunInputs(
      contractWith([{ type: 'PerformTurn' }]),
      900,
      captureTarget,
    );

    expect(prepareDesktopHandoff).toHaveBeenCalledOnce();
    expect(captureTarget).toHaveBeenCalledWith({
      yielded: true,
      control_surface_pid: 7712,
      prepared_at: 800,
    });
    expect(inputs).toEqual({
      execution_context: {
        format: 'execution-context/1',
        entrypoint: 'native_agent_cockpit',
        task_id: 'task-ctx-1',
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
          format: 'desktop-target/2',
          hwnd: 991,
          root_hwnd: 991,
          title: 'External target',
          process_id: 8812,
          process_identity: 'process-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
          class_name: 'TargetWindow',
          rect: [0, 0, 1200, 800],
          captured_at: 850,
          foreground_verified: true,
          usable: true,
          failure_type: '',
          reason: '',
        },
      },
    });
  });

  it('fails before run submission when the native surface keeps foreground', async () => {
    (window as any).electronAPI = {
      prepareDesktopHandoff: vi.fn(async () => ({
        yielded: false,
        control_surface_pid: 7712,
        prepared_at: 800,
        reason: 'control_surface_still_focused',
      })),
    };

    await expect(buildClientRunInputs(
      contractWith([{ type: 'PerformTurn' }]),
      900,
    )).rejects.toThrow('control_surface_still_focused');
  });
});
