import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  AgentCockpit,
  RuntimeVersionSelector,
} from '../components/platform/PlatformWorkbench';
import { ControlPlaneClient } from '../platform/client';
import type {
  ArpExecutionSnapshot,
  DesktopTargetContext,
  RuntimeVersion,
  TaskContract,
} from '../platform/contracts';
import {
  buildVnextLiveCanaryGraph,
  startGoalWithRuntime,
} from '../platform/useControlPlane';

function target(): DesktopTargetContext {
  return {
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
  };
}

function snapshot(graphId = 'vnext-live-canary-test'): ArpExecutionSnapshot {
  return {
    run_id: 'run-vnext-test',
    graph_id: graphId,
    graph_name: 'Complete the visible task',
    runtime_version: 'vnext',
    runtime_budget: {
      max_turns: 30,
      max_runtime_s: 300,
      max_model_calls: 60,
    },
    status: 'queued',
  };
}

afterEach(() => {
  delete (window as any).electronAPI;
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe('runtime-version run transport', () => {
  it('serializes runtime_version only at the top level and defaults to legacy', async () => {
    const graph = buildVnextLiveCanaryGraph('Complete the visible task');
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      ok: true,
      run_id: 'run-vnext-test',
      run: snapshot(graph.id),
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    const client = new ControlPlaneClient('http://127.0.0.1:8765');

    await client.startGraph(graph, { marker: 'legacy-input' });
    await client.startGraph(graph, { marker: 'vnext-input' }, 'vnext');

    const calls = fetchMock.mock.calls as unknown as Array<
      [RequestInfo | URL, RequestInit?]
    >;
    const first = JSON.parse(String(calls[0][1]?.body));
    const second = JSON.parse(String(calls[1][1]?.body));
    expect(first).toEqual({
      graph,
      inputs: { marker: 'legacy-input' },
      runtime_version: 'legacy',
    });
    expect(second).toEqual({
      graph,
      inputs: { marker: 'vnext-input' },
      runtime_version: 'vnext',
    });
    expect(first.inputs).not.toHaveProperty('runtime_version');
    expect(second.inputs).not.toHaveProperty('runtime_version');
    await expect(client.startGraph(
      graph,
      { runtime_version: 'vnext' },
    )).rejects.toThrow('top-level run field');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('builds one deterministic full-goal node with one attempt', () => {
    const first = buildVnextLiveCanaryGraph('  Complete the visible task  ');
    const second = buildVnextLiveCanaryGraph('Complete the visible task');

    expect(first).toEqual(second);
    expect(first.nodes.map((node) => node.kind)).toEqual([
      'start',
      'agent_goal',
      'end',
    ]);
    expect(first.edges.map((edge) => [edge.source, edge.target])).toEqual([
      ['start', 'full-goal'],
      ['full-goal', 'end'],
    ]);
    const goal = first.nodes[1];
    expect(goal.config).toEqual({ goal: 'Complete the visible task' });
    expect(goal.retry).toEqual({
      max_attempts: 1,
      delay_ms: 0,
      backoff: 1,
    });
    expect(first.nodes.every((node) => node.retry.max_attempts === 1)).toBe(true);
    expect(first.policy.max_node_visits).toBe(1);
    expect(first.metadata).toEqual({});
    expect(first.variables).toEqual({});
  });

  it('does not compile or plan vNext and captures exactly one handoff target', async () => {
    const prepareDesktopHandoff = vi.fn(async () => ({
      yielded: true,
      control_surface_pid: 7712,
      prepared_at: 800,
    }));
    (window as any).electronAPI = { prepareDesktopHandoff };
    const captureDesktopContext = vi.fn(async () => target());
    const compileTask = vi.fn(async () => {
      throw new Error('vNext must not compile or plan');
    });
    const startGraph = vi.fn(async (
      graph,
      _inputs,
      _runtimeVersion,
    ) => snapshot(graph.id));

    await startGoalWithRuntime(
      {
        compileTask,
        captureDesktopContext,
        startGraph,
      } as never,
      'Complete the visible task',
      999,
      false,
    );

    expect(compileTask).not.toHaveBeenCalled();
    expect(prepareDesktopHandoff).toHaveBeenCalledOnce();
    expect(captureDesktopContext).toHaveBeenCalledOnce();
    expect(captureDesktopContext).toHaveBeenCalledWith(7712);
    expect(startGraph).toHaveBeenCalledOnce();
    const [graph, inputs, runtimeVersion] = startGraph.mock.calls[0];
    expect(runtimeVersion).toBe('vnext');
    expect(graph.nodes).toHaveLength(3);
    expect(inputs).toMatchObject({
      execution_context: {
        format: 'execution-context/1',
        entrypoint: 'native_agent_cockpit',
        task_id: graph.id,
        desktop_handoff: {
          required: true,
          yielded: true,
          control_surface_pid: 7712,
        },
        target_window: target(),
      },
    });
  });

  it('keeps the legacy compiler path and forwards its graph unchanged', async () => {
    const graph = buildVnextLiveCanaryGraph('Legacy graph fixture');
    const contract = {
      format: 'task-contract/1',
      task_id: 'task-legacy',
      task: 'Legacy graph fixture',
      intent: {
        original_text: 'Legacy graph fixture',
        operation: [],
        media_type: '',
        channel: '',
        recipient: '',
        source: '',
        capture_scope: '',
        destination: '',
        send_mode: 'send',
        subject: '',
        quality: 'normal',
        confidence: 1,
        ambiguous_fields: [],
        provenance: {},
      },
      stages: [],
      obligations: [],
      final_conditions: [],
      session: null,
      satisfiable: true,
      missing_capabilities: [],
      experience_policy: 'disabled',
      compiler_version: 'test',
    } satisfies TaskContract;
    const compileTask = vi.fn(async () => ({ contract, graph }));
    const startGraph = vi.fn(async () => snapshot(graph.id));
    const captureDesktopContext = vi.fn(async () => target());

    await startGoalWithRuntime(
      { compileTask, captureDesktopContext, startGraph } as never,
      'Legacy graph fixture',
      47,
      true,
      'legacy',
    );

    expect(compileTask).toHaveBeenCalledWith(
      'Legacy graph fixture',
      47,
      true,
    );
    expect(captureDesktopContext).not.toHaveBeenCalled();
    expect(startGraph).toHaveBeenCalledWith(
      graph,
      expect.objectContaining({
        execution_context: expect.objectContaining({
          task_id: 'task-legacy',
        }),
      }),
      'legacy',
    );
  });
});

describe('primary runtime selector', () => {
  function SelectorHarness({ canaryEnabled }: { canaryEnabled: boolean }) {
    const [value, setValue] = useState<RuntimeVersion>('legacy');
    return (
      <RuntimeVersionSelector
        value={value}
        canaryEnabled={canaryEnabled}
        disabled={false}
        onChange={setValue}
      />
    );
  }

  function cockpitPlane(
    liveCanaryEnabled: boolean | undefined,
    startGoal = vi.fn(async () => undefined),
  ) {
    return {
      currentRun: null,
      arpSnapshot: null,
      runtimeIssue: '',
      overview: liveCanaryEnabled === undefined ? undefined : {
        api_version: '2',
        product: 'WinPilot',
        status: {
          vnext: {
            live_canary_enabled: liveCanaryEnabled,
            budget: {
              max_turns: 30,
              max_runtime_s: 300,
              max_model_calls: 60,
            },
          },
        },
        current_run: null,
        capabilities: [],
        contracts: {},
      },
      events: [],
      baseUrl: '',
      startGoal,
      cancelCurrent: vi.fn(),
      pauseCurrent: vi.fn(),
      resumeCurrent: vi.fn(),
      decideGate: vi.fn(),
      getClient: vi.fn(),
    };
  }

  it('keeps vNext unavailable while the canary is explicitly off', () => {
    render(<SelectorHarness canaryEnabled={false} />);

    expect(screen.getByLabelText('运行时')).toHaveValue('legacy');
    expect(screen.getByRole('option', { name: 'vNext' })).toBeDisabled();
  });

  it('keeps vNext while status loads and downgrades only when explicitly disabled', async () => {
    const { rerender } = render(
      <AgentCockpit plane={cockpitPlane(undefined) as never} />,
    );
    const selector = screen.getByLabelText('运行时');

    expect(selector).toHaveValue('vnext');
    rerender(<AgentCockpit plane={cockpitPlane(true) as never} />);
    expect(selector).toHaveValue('vnext');
    rerender(<AgentCockpit plane={cockpitPlane(false) as never} />);
    await waitFor(() => expect(selector).toHaveValue('legacy'));
  });

  it('uses vNext by default and keeps it after submission', async () => {
    let finishRun: (() => void) | undefined;
    const startGoal = vi.fn(() => new Promise<void>((resolve) => {
      finishRun = resolve;
    }));
    const plane = cockpitPlane(true, startGoal);
    const user = userEvent.setup();
    render(<AgentCockpit plane={plane as never} />);

    const selector = screen.getByLabelText('运行时');
    expect(selector).toHaveValue('vnext');
    await user.type(
      screen.getByPlaceholderText('描述希望代理在这台电脑上完成的目标……'),
      'Complete the visible task',
    );
    await user.click(screen.getByRole('button', { name: '运行任务' }));

    expect(startGoal).toHaveBeenCalledWith(
      'Complete the visible task',
      40,
      false,
      'vnext',
    );
    expect(selector).toBeDisabled();
    finishRun?.();
    await waitFor(() => expect(selector).toHaveValue('vnext'));
    expect(selector).not.toBeDisabled();
  });
});
