import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AgentCockpit } from '../components/platform/PlatformWorkbench';
import { ControlPlaneClient } from '../platform/client';
import type {
  ArpExecutionSnapshot,
  ArpGraph,
  ClientExecutionContext,
  DesktopTargetContext,
} from '../platform/contracts';
import { startGoalWithDesktopHandoff } from '../platform/useControlPlane';

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

function executionContext(): ClientExecutionContext {
  return {
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
    target_window: target(),
  };
}

function snapshot(): ArpExecutionSnapshot {
  return {
    run_id: 'run-goal-test',
    graph_id: 'server-owned-graph',
    graph_name: 'Complete the visible task',
    status: 'queued',
  };
}

function compatibilityGraph(): ArpGraph {
  return {
    format: 'arp/1',
    id: 'studio-graph',
    name: 'Studio graph',
    description: '',
    nodes: [],
    edges: [],
    variables: {},
    policy: {
      max_node_visits: 1,
      fail_fast: true,
      require_evidence_on_done: false,
      allow_shell: false,
      allow_desktop_mutation: true,
    },
    metadata: {},
  };
}

afterEach(() => {
  delete (window as any).electronAPI;
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe('primary goal-run transport', () => {
  it('posts only the goal and client-owned context', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      ok: true,
      run_id: 'run-goal-test',
      run: snapshot(),
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    const client = new ControlPlaneClient('http://127.0.0.1:8765');

    await client.startGoalRun(
      'Complete the visible task',
      executionContext(),
    );

    const [[request, init]] = fetchMock.mock.calls as unknown as Array<
      [RequestInfo | URL, RequestInit?]
    >;
    expect(String(request)).toBe('http://127.0.0.1:8765/api/v2/goals/runs');
    const submitted = JSON.parse(String(init?.body));
    expect(submitted).toEqual({
      goal: 'Complete the visible task',
      execution_context: executionContext(),
    });
    expect(submitted).not.toHaveProperty('graph');
    expect(submitted).not.toHaveProperty('runtime_version');
    expect(submitted.execution_context).not.toHaveProperty('task_id');
  });

  it('captures one handoff target before starting the server-owned goal run', async () => {
    const prepareDesktopHandoff = vi.fn(async () => ({
      yielded: true,
      control_surface_pid: 7712,
      prepared_at: 800,
    }));
    const bindDesktopHandoff = vi.fn(async () => true);
    const releaseDesktopHandoff = vi.fn(async () => true);
    (window as any).electronAPI = {
      prepareDesktopHandoff,
      bindDesktopHandoff,
      releaseDesktopHandoff,
    };
    const captureDesktopContext = vi.fn(async () => target());
    const startGoalRun = vi.fn(async (
      _goal: string,
      _context: ClientExecutionContext,
    ) => snapshot());

    await startGoalWithDesktopHandoff(
      { captureDesktopContext, startGoalRun } as never,
      '  Complete the visible task  ',
    );

    expect(prepareDesktopHandoff).toHaveBeenCalledOnce();
    expect(bindDesktopHandoff).toHaveBeenCalledWith('run-goal-test');
    expect(releaseDesktopHandoff).not.toHaveBeenCalled();
    expect(captureDesktopContext).toHaveBeenCalledWith(7712);
    expect(startGoalRun).toHaveBeenCalledOnce();
    const [goal, context] = startGoalRun.mock.calls[0];
    expect(goal).toBe('Complete the visible task');
    expect(context).toMatchObject({
      format: 'execution-context/1',
      entrypoint: 'native_agent_cockpit',
      desktop_handoff: {
        required: true,
        yielded: true,
        control_surface_pid: 7712,
      },
      target_window: target(),
    });
    expect(context).not.toHaveProperty('task_id');
  });

  it('keeps a created run truthful when taskbar handoff binding degrades', async () => {
    const prepareDesktopHandoff = vi.fn(async () => ({
      yielded: true,
      control_surface_pid: 7712,
      prepared_at: 800,
    }));
    const bindDesktopHandoff = vi.fn(async () => {
      throw new Error('ipc unavailable');
    });
    const releaseDesktopHandoff = vi.fn(async () => {
      throw new Error('release unavailable');
    });
    (window as any).electronAPI = {
      prepareDesktopHandoff,
      bindDesktopHandoff,
      releaseDesktopHandoff,
    };
    const created = snapshot();

    const result = await startGoalWithDesktopHandoff(
      {
        captureDesktopContext: vi.fn(async () => target()),
        startGoalRun: vi.fn(async () => created),
      } as never,
      'Complete the visible task',
    );

    expect(result).toBe(created);
    expect(releaseDesktopHandoff).toHaveBeenCalledOnce();
  });

  it('keeps compileTask and startGraph available as Studio compatibility APIs', async () => {
    const graph = compatibilityGraph();
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      ok: true,
      run_id: 'run-studio',
      run: { ...snapshot(), graph_id: graph.id },
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    const client = new ControlPlaneClient('http://127.0.0.1:8765');

    await client.startGraph(graph, { source: 'studio' });

    const body = JSON.parse(String(
      (fetchMock.mock.calls as unknown as Array<[RequestInfo | URL, RequestInit?]>)[0][1]?.body,
    ));
    expect(body).toEqual({
      graph,
      inputs: { source: 'studio' },
      runtime_version: 'legacy',
    });
    expect(typeof client.compileTask).toBe('function');
  });
});

describe('primary agent surface', () => {
  function cockpitPlane(
    startGoal = vi.fn(async () => undefined),
    currentRun: unknown = null,
  ) {
    return {
      currentRun,
      arpSnapshot: null,
      runtimeIssue: '',
      overview: undefined,
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

  it('submits the natural-language goal without execution policy controls', async () => {
    let finishRun: (() => void) | undefined;
    const startGoal = vi.fn(() => new Promise<void>((resolve) => {
      finishRun = resolve;
    }));
    const user = userEvent.setup();
    render(<AgentCockpit plane={cockpitPlane(startGoal) as never} />);

    await user.type(screen.getByRole('textbox'), 'Complete the visible task');
    await user.click(screen.getByRole('button', { name: '运行任务' }));

    expect(startGoal).toHaveBeenCalledWith('Complete the visible task');
    expect(screen.queryByLabelText('执行约束')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '运行任务' })).toBeDisabled();
    finishRun?.();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: '运行任务' })).not.toBeDisabled();
    });
  });

  it('shows the verified run result on the primary Agent surface', () => {
    const result = (
      'Photography group: planning a weekend photo walk.\n'
      + 'Running group: confirming the Saturday meeting time.'
    );
    const completedRun = {
      run_id: 'run-result',
      kind: 'arp',
      status: 'done',
      phase: 'done',
      goal: 'Summarize the visible conversations',
      started_at: 1,
      updated_at: 2,
      finished_at: 2,
      result,
      timeline: [],
      evidence: [],
      metrics: {},
    };

    render(
      <AgentCockpit
        plane={cockpitPlane(
          vi.fn(async () => undefined),
          completedRun,
        ) as never}
      />,
    );

    const visible = screen.getByRole('status', { name: '任务结果' });
    expect(visible).toHaveTextContent(
      'Photography group: planning a weekend photo walk.',
    );
    expect(visible).toHaveTextContent(
      'Running group: confirming the Saturday meeting time.',
    );
  });

  it('does not present a failed run result as verified success', () => {
    render(
      <AgentCockpit
        plane={cockpitPlane(
          vi.fn(async () => undefined),
          {
            run_id: 'run-failed-result',
            kind: 'arp',
            status: 'failed',
            phase: 'failed',
            goal: 'Summarize the visible conversations',
            started_at: 1,
            updated_at: 2,
            finished_at: 2,
            result: 'The task could not be verified.',
            timeline: [],
            evidence: [],
            metrics: {},
          },
        ) as never}
      />,
    );

    expect(screen.queryByRole('status', { name: '任务结果' })).not.toBeInTheDocument();
    expect(screen.queryByText('The task could not be verified.')).not.toBeInTheDocument();
  });
});
