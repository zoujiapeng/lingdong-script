import { afterEach, describe, expect, it, vi } from 'vitest';
import type { DesktopTargetContext, TaskContract, TaskStage } from '../platform/contracts';
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

function canonicalTargetAbsence(
  overrides: Partial<DesktopTargetContext> = {},
): DesktopTargetContext {
  return {
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
    ...overrides,
  };
}

function installYieldedHandoff(preparedAt = 800, now = 900) {
  vi.spyOn(Date, 'now').mockReturnValue(now);
  const prepareDesktopHandoff = vi.fn(async () => ({
    yielded: true,
    control_surface_pid: 7712,
    prepared_at: preparedAt,
  }));
  (window as any).electronAPI = { prepareDesktopHandoff };
  return prepareDesktopHandoff;
}

describe('native execution context', () => {
  afterEach(() => {
    vi.restoreAllMocks();
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

  it('allows only vNext to preserve a canonical target absence after handoff', async () => {
    installYieldedHandoff();
    const absence = canonicalTargetAbsence();

    const inputs = await buildClientRunInputs(
      contractWith([{ type: 'ExecuteDesktopTask' }]),
      900,
      vi.fn(async () => absence),
      'vnext',
    );

    expect((inputs.execution_context as Record<string, unknown>).target_window).toBe(absence);
    expect(JSON.stringify(inputs)).not.toContain('runtime_version');
    expect(JSON.stringify(inputs)).not.toContain('"vnext"');
  });

  it('accepts an absence captured at the exact handoff boundary', async () => {
    installYieldedHandoff(801);
    const absence = canonicalTargetAbsence({ captured_at: 801 });

    await expect(buildClientRunInputs(
      contractWith([{ type: 'ExecuteDesktopTask' }]),
      900,
      vi.fn(async () => absence),
      'vnext',
    )).resolves.toBeDefined();
  });

  it('keeps legacy target binding fail-closed for the same canonical absence', async () => {
    installYieldedHandoff();

    await expect(buildClientRunInputs(
      contractWith([{ type: 'ExecuteDesktopTask' }]),
      900,
      vi.fn(async () => canonicalTargetAbsence()),
      'legacy',
    )).rejects.toThrow('no foreground target');
  });

  it.each([
    ['future prepared_at', 901, 901, 900],
    ['stale prepared_at', 800, 15_801, 15_801],
    ['future captured_at', 800, 901, 900],
    ['stale captured_at', 800, 800, 15_801],
  ])('rejects vNext target absence with %s', async (
    _name,
    preparedAt,
    capturedAt,
    now,
  ) => {
    installYieldedHandoff(preparedAt, now);

    await expect(buildClientRunInputs(
      contractWith([{ type: 'ExecuteDesktopTask' }]),
      now,
      vi.fn(async () => canonicalTargetAbsence({ captured_at: capturedAt })),
      'vnext',
    )).rejects.toThrow();
  });

  it.each([
    ['missing field', (target: Record<string, unknown>) => {
      const { reason: _reason, ...rest } = target;
      return rest;
    }],
    ['extra field', (target: Record<string, unknown>) => ({ ...target, extra: true })],
    ['wrong format', (target: Record<string, unknown>) => ({ ...target, format: 'desktop-target/1' })],
    ['boolean hwnd', (target: Record<string, unknown>) => ({ ...target, hwnd: false })],
    ['text root hwnd', (target: Record<string, unknown>) => ({ ...target, root_hwnd: '0' })],
    ['fractional process id', (target: Record<string, unknown>) => ({ ...target, process_id: 0.5 })],
    ['nonempty title', (target: Record<string, unknown>) => ({ ...target, title: 'window' })],
    ['nonempty identity', (target: Record<string, unknown>) => ({ ...target, process_identity: 'process-x' })],
    ['nonempty class', (target: Record<string, unknown>) => ({ ...target, class_name: 'Window' })],
    ['nonempty rect', (target: Record<string, unknown>) => ({ ...target, rect: [0, 0, 1, 1] })],
    ['zero timestamp', (target: Record<string, unknown>) => ({ ...target, captured_at: 0 })],
    ['fractional timestamp', (target: Record<string, unknown>) => ({ ...target, captured_at: 800.5 })],
    ['unsafe timestamp', (target: Record<string, unknown>) => ({ ...target, captured_at: Number.MAX_SAFE_INTEGER + 1 })],
    ['timestamp before handoff', (target: Record<string, unknown>) => ({ ...target, captured_at: 799 })],
    ['foreground verified', (target: Record<string, unknown>) => ({ ...target, foreground_verified: true })],
    ['usable target', (target: Record<string, unknown>) => ({ ...target, usable: true })],
    ['other failure', (target: Record<string, unknown>) => ({ ...target, failure_type: 'capture_failed' })],
    ['non-text reason', (target: Record<string, unknown>) => ({ ...target, reason: null })],
    ['oversized reason', (target: Record<string, unknown>) => ({ ...target, reason: 'x'.repeat(513) })],
  ])('rejects a noncanonical vNext absence: %s', async (_name, rewrite) => {
    installYieldedHandoff();
    const target = rewrite(canonicalTargetAbsence() as unknown as Record<string, unknown>);

    await expect(buildClientRunInputs(
      contractWith([{ type: 'ExecuteDesktopTask' }]),
      900,
      vi.fn(async () => target as unknown as DesktopTargetContext),
      'vnext',
    )).rejects.toThrow();
  });
});
