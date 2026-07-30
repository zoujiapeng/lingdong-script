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
    runs: unknown[] = [],
  ) {
    return {
      currentRun,
      arpSnapshot: null,
      runtimeIssue: '',
      overview: undefined,
      events: [],
      runs,
      connection: 'online',
      error: '',
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
    await user.click(screen.getByRole('button', { name: '发送消息' }));

    expect(startGoal).toHaveBeenCalledWith('Complete the visible task');
    expect(screen.queryByLabelText('执行约束')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '发送消息' })).toBeDisabled();
    finishRun?.();
    await waitFor(() => {
      expect(screen.getByRole('textbox', { name: '消息输入' })).not.toBeDisabled();
    });
    await user.type(screen.getByRole('textbox', { name: '消息输入' }), 'Run another task');
    expect(screen.getByRole('button', { name: '发送消息' })).not.toBeDisabled();
  });

  it('keeps secondary agent information hidden until the user asks for it', async () => {
    const user = userEvent.setup();
    const openResources = vi.fn();
    const openSystem = vi.fn();
    const openWorkbench = vi.fn();
    const openCommand = vi.fn();

    render(
      <AgentCockpit
        plane={cockpitPlane() as never}
        onOpenResources={openResources}
        onOpenSystem={openSystem}
        onOpenWorkbench={openWorkbench}
        onOpenCommand={openCommand}
      />,
    );

    expect(screen.queryByLabelText('快速入口')).not.toBeInTheDocument();
    expect(screen.queryByText('步骤、画面、证据和运行审计')).not.toBeInTheDocument();
    expect(screen.getByLabelText('输入框运行上下文')).toHaveClass('soft-hidden');

    await user.click(screen.getByRole('textbox', { name: '消息输入' }));

    expect(screen.getByLabelText('快速入口')).toBeInTheDocument();
    expect(screen.getByText('步骤、画面、证据和运行审计')).toBeInTheDocument();
    expect(screen.getByLabelText('输入框运行上下文')).not.toHaveClass('soft-hidden');

    await user.click(screen.getByRole('button', { name: 'Visual Actor' }));
    await user.click(screen.getByRole('option', { name: '模型与接口设置' }));
    expect(openSystem).toHaveBeenCalledOnce();

    await user.click(screen.getByRole('button', { name: /执行工作台/ }));
    expect(openWorkbench).toHaveBeenCalledOnce();
    expect(openResources).not.toHaveBeenCalled();
    expect(openCommand).not.toHaveBeenCalled();
  });

  it('reuses the original composer controls instead of replacing the input in conversation view', () => {
    const pristine = render(
      <AgentCockpit plane={cockpitPlane() as never} />,
    );

    const landingInput = screen.getByRole('textbox', { name: '消息输入' });
    expect(landingInput).toHaveClass('ld-composer-input');
    expect(landingInput.closest('.ld-composer-window')).not.toBeNull();
    expect(screen.getByRole('button', { name: '添加上下文' })).toHaveClass('ld-round-control');
    expect(screen.getByRole('button', { name: '发送消息' })).toHaveClass(
      'ld-round-control',
      'ld-send-control',
    );
    pristine.unmount();

    const completedRun = {
      run_id: 'run-shared-composer',
      kind: 'agent',
      status: 'reply',
      phase: 'done',
      goal: 'Continue the visible task',
      started_at: 1,
      updated_at: 2,
      finished_at: 2,
      result: 'Ready for the next message.',
      timeline: [],
      evidence: [],
      metrics: {},
    };
    localStorage.setItem(
      'arp-agent-thread-run-ids',
      JSON.stringify([completedRun.run_id]),
    );

    const conversation = render(
      <AgentCockpit
        plane={cockpitPlane(
          vi.fn(async () => undefined),
          completedRun,
          [completedRun],
        ) as never}
      />,
    );

    const conversationInput = screen.getByRole('textbox', { name: '消息输入' });
    expect(conversationInput).toHaveClass('ld-composer-input');
    expect(conversationInput.closest('.ld-compact-composer')).not.toBeNull();
    expect(conversation.container.querySelector('.ld-followup-input')).toBeNull();
    expect(screen.getByRole('button', { name: '添加上下文' })).toHaveClass('ld-round-control');
    expect(screen.getByRole('button', { name: '发送消息' })).toHaveClass(
      'ld-round-control',
      'ld-send-control',
    );
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

  it('keeps a direct Agent reply visible without labeling it as verified completion', () => {
    render(
      <AgentCockpit
        plane={cockpitPlane(
          vi.fn(async () => undefined),
          {
            run_id: 'run-reply',
            kind: 'agent',
            status: 'reply',
            phase: 'done',
            goal: 'Answer the visible question',
            started_at: 1,
            updated_at: 2,
            finished_at: 2,
            result: 'The visible total is 42.',
            timeline: [],
            evidence: [],
            metrics: {},
          },
        ) as never}
      />,
    );

    const visible = screen.getByRole('status', { name: '任务结果' });
    expect(visible).toHaveTextContent('Agent 回复');
    expect(visible).toHaveTextContent('The visible total is 42.');
    expect(visible).not.toHaveTextContent('已验证结果');
  });

  it('keeps a failed run truthful while leaving a new task composer available', async () => {
    const startGoal = vi.fn(async () => undefined);
    const user = userEvent.setup();
    render(
      <AgentCockpit
        plane={cockpitPlane(
          startGoal,
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
    const composer = screen.getByRole('textbox', { name: '消息输入' });
    expect(composer).toBeVisible();

    await user.type(composer, 'Retry with the visible window');
    await user.click(screen.getByRole('button', { name: '发送消息' }));
    expect(startGoal).toHaveBeenCalledWith('Retry with the visible window');
  });

  it('renders consecutive goal runs as one compact long conversation', async () => {
    const user = userEvent.setup();
    const firstRun = {
      run_id: 'run-thread-first',
      kind: 'agent',
      status: 'reply',
      phase: 'done',
      goal: 'Read the visible total',
      started_at: 1,
      updated_at: 2,
      finished_at: 2,
      result: 'The visible total is 42.',
      timeline: [],
      evidence: [],
      metrics: {},
    };
    const secondRun = {
      run_id: 'run-thread-second',
      kind: 'arp',
      status: 'done',
      phase: 'done',
      goal: 'Put that total into the visible form',
      started_at: 3,
      updated_at: 4,
      finished_at: 4,
      result: 'The value was entered and verified.',
      timeline: [],
      evidence: [],
      metrics: {},
    };
    localStorage.setItem(
      'arp-agent-thread-run-ids',
      JSON.stringify([firstRun.run_id, secondRun.run_id]),
    );

    render(
      <AgentCockpit
        plane={cockpitPlane(
          vi.fn(async () => undefined),
          secondRun,
          [secondRun, firstRun],
        ) as never}
      />,
    );

    expect(screen.getAllByLabelText('对话轮次')).toHaveLength(2);
    expect(screen.getAllByText('Read the visible total').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('The visible total is 42.')).toBeInTheDocument();
    expect(screen.getAllByText('Put that total into the visible form').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('The value was entered and verified.')).toBeInTheDocument();
    expect(screen.getByText(/2 轮/)).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: '消息输入' })).toBeVisible();

    await user.click(screen.getByRole('button', { name: '新建对话' }));
    expect(screen.getByRole('region', { name: '新建对话' })).toBeInTheDocument();
    expect(screen.queryByText('The visible total is 42.')).not.toBeInTheDocument();
    expect(localStorage.getItem('arp-agent-thread-run-ids')).toBe('[]');
  });

  it('keeps the composer visible for drafting while an Agent turn is active', async () => {
    const activeRun = {
      run_id: 'run-thread-active',
      kind: 'arp',
      status: 'running',
      phase: 'acting',
      goal: 'Complete the visible task',
      started_at: 1,
      updated_at: 2,
      timeline: [],
      evidence: [],
      metrics: {},
    };
    localStorage.setItem('arp-agent-thread-run-ids', JSON.stringify([activeRun.run_id]));
    const user = userEvent.setup();

    render(
      <AgentCockpit
        plane={cockpitPlane(
          vi.fn(async () => undefined),
          activeRun,
          [activeRun],
        ) as never}
      />,
    );

    const composer = screen.getByRole('textbox', { name: '消息输入' });
    expect(composer).toBeVisible();
    expect(composer).not.toBeDisabled();
    await user.type(composer, 'Continue with the next visible item');
    expect(screen.getByRole('button', { name: '发送消息' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '新建对话' })).toBeDisabled();
    expect(screen.getByLabelText('Agent 当前状态')).toBeInTheDocument();
  });

  it('collapses a long Agent answer without making it inaccessible', async () => {
    const longResult = Array.from(
      { length: 18 },
      (_, index) => `Line ${index + 1}: preserved Agent information.`,
    ).join('\n');
    const completedRun = {
      run_id: 'run-long-answer',
      kind: 'agent',
      status: 'reply',
      phase: 'done',
      goal: 'Explain every visible item',
      started_at: 1,
      updated_at: 2,
      finished_at: 2,
      result: longResult,
      timeline: [],
      evidence: [],
      metrics: {},
    };
    const user = userEvent.setup();

    render(
      <AgentCockpit
        plane={cockpitPlane(
          vi.fn(async () => undefined),
          completedRun,
          [completedRun],
        ) as never}
      />,
    );

    const expand = screen.getByRole('button', { name: '展开全文' });
    const resultParagraph = screen.getByRole('status', { name: '任务结果' })
      .querySelector('.ld-agent-response-text p');
    expect(expand).toHaveAttribute('aria-expanded', 'false');
    expect(resultParagraph).toHaveTextContent('Line 18: preserved Agent information.');
    expect(resultParagraph).toHaveClass('is-collapsed');
    await user.click(expand);
    expect(screen.getByRole('button', { name: '收起长内容' })).toHaveAttribute('aria-expanded', 'true');
    expect(resultParagraph).not.toHaveClass('is-collapsed');
  });

  it('presents an interrupted recovered run as terminal instead of active', () => {
    render(
      <AgentCockpit
        plane={cockpitPlane(
          vi.fn(async () => undefined),
          {
            run_id: 'run-interrupted',
            kind: 'agent',
            status: 'interrupted',
            phase: 'acting',
            goal: 'Complete the visible task',
            started_at: 1,
            updated_at: 2,
            finished_at: 2,
            error: 'Recovered incomplete persisted run from acting phase',
            timeline: [],
            evidence: [],
            metrics: {},
          },
        ) as never}
      />,
    );

    expect(screen.getByRole('alert')).toHaveTextContent('已中断');
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Recovered incomplete persisted run from acting phase',
    );
    expect(screen.queryByText('Agent 正在处理')).not.toBeInTheDocument();
  });
});
