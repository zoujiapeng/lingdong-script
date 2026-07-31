import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AgentCockpit } from '../components/platform/PlatformWorkbench';
import { ControlPlaneClient } from '../platform/client';
import type {
  ArpExecutionSnapshot,
  ArpGraph,
  ClientExecutionContext,
  DesktopTargetContext,
  GoalRunStart,
} from '../platform/contracts';
import { fulfillDesktopLease } from '../platform/useControlPlane';

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

function snapshot(): ArpExecutionSnapshot {
  return {
    run_id: 'run-goal-test',
    graph_id: 'server-owned-graph',
    graph_name: 'Complete the visible task',
    status: 'queued',
  };
}

function goalRunStart(
  run: ArpExecutionSnapshot = snapshot(),
  threadId = 'thread-goal-test',
  turnId = 'turn-goal-test',
): GoalRunStart {
  return {
    ok: true,
    run_id: run.run_id,
    thread_id: threadId,
    turn_id: turnId,
    runtime_version: 'vnext',
    admission: {
      format: 'goal-admission/1',
      goal: run.graph_name,
      route: 'live_agent',
      runtime_version: 'vnext',
      reason: 'single_semantic_agent',
      no_legacy_fallback: true,
    },
    run,
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
  it('posts only the goal and existing thread id before any desktop handoff', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(
      goalRunStart(snapshot(), 'thread-existing'),
    ), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    const client = new ControlPlaneClient('http://127.0.0.1:8765');

    await client.startGoalRun(
      'Complete the visible task',
      'thread-existing',
    );

    const [[request, init]] = fetchMock.mock.calls as unknown as Array<
      [RequestInfo | URL, RequestInit?]
    >;
    expect(String(request)).toBe('http://127.0.0.1:8765/api/v2/goals/runs');
    const submitted = JSON.parse(String(init?.body));
    expect(submitted).toEqual({
      goal: 'Complete the visible task',
      thread_id: 'thread-existing',
    });
    expect(submitted).not.toHaveProperty('graph');
    expect(submitted).not.toHaveProperty('runtime_version');
    expect(submitted).not.toHaveProperty('execution_context');
  });

  it('prepares, captures, locally binds, then server-binds only after a desktop lease request', async () => {
    const order: string[] = [];
    const prepareDesktopHandoff = vi.fn(async () => {
      order.push('prepare');
      return {
        yielded: true,
        control_surface_pid: 7712,
        prepared_at: 800,
      };
    });
    const bindDesktopHandoff = vi.fn(async () => {
      order.push('local-bind');
      return true;
    });
    const releaseDesktopHandoff = vi.fn(async () => true);
    (window as any).electronAPI = {
      prepareDesktopHandoff,
      bindDesktopHandoff,
      releaseDesktopHandoff,
    };
    const captureDesktopContext = vi.fn(async () => {
      order.push('capture');
      return target();
    });
    const bindDesktopLease = vi.fn(async (
      _runId: string,
      _leaseId: string,
      _context: ClientExecutionContext,
    ) => {
      order.push('server-bind');
      return {};
    });
    const failDesktopLease = vi.fn(async () => ({}));

    expect(prepareDesktopHandoff).not.toHaveBeenCalled();
    expect(captureDesktopContext).not.toHaveBeenCalled();
    expect(bindDesktopHandoff).not.toHaveBeenCalled();
    expect(bindDesktopLease).not.toHaveBeenCalled();

    await fulfillDesktopLease(
      { captureDesktopContext, bindDesktopLease, failDesktopLease } as never,
      'run-goal-test',
      'lease-goal-test',
    );

    expect(prepareDesktopHandoff).toHaveBeenCalledOnce();
    expect(bindDesktopHandoff).toHaveBeenCalledWith('run-goal-test');
    expect(releaseDesktopHandoff).not.toHaveBeenCalled();
    expect(captureDesktopContext).toHaveBeenCalledWith(7712);
    expect(failDesktopLease).not.toHaveBeenCalled();
    expect(order).toEqual(['prepare', 'capture', 'local-bind', 'server-bind']);
    expect(bindDesktopLease).toHaveBeenCalledOnce();
    const [runId, leaseId, context] = bindDesktopLease.mock.calls[0];
    expect(runId).toBe('run-goal-test');
    expect(leaseId).toBe('lease-goal-test');
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

  it('keeps the surface yielded when a delivered lease response is lost', async () => {
    const releaseDesktopHandoff = vi.fn(async () => true);
    (window as any).electronAPI = {
      prepareDesktopHandoff: vi.fn(async () => ({
        yielded: true,
        control_surface_pid: 7712,
        prepared_at: 800,
      })),
      bindDesktopHandoff: vi.fn(async () => true),
      releaseDesktopHandoff,
    };
    const failDesktopLease = vi.fn(async () => ({}));

    await expect(fulfillDesktopLease(
      {
        captureDesktopContext: vi.fn(async () => target()),
        bindDesktopLease: vi.fn(async () => {
          throw new Error('response lost');
        }),
        failDesktopLease,
        run: vi.fn(async () => ({
          ok: true,
          run: snapshot(),
          arp: {
            ...snapshot(),
            desktop_lease: {
              format: 'desktop-lease/1',
              run_id: 'run-goal-test',
              thread_id: 'thread-goal-test',
              turn_id: 'turn-goal-test',
              lease_id: 'lease-goal-test',
              status: 'delivered',
              error: '',
            },
          },
        })),
      } as never,
      'run-goal-test',
      'lease-goal-test',
    )).resolves.toBeUndefined();

    expect(failDesktopLease).not.toHaveBeenCalled();
    expect(releaseDesktopHandoff).not.toHaveBeenCalled();
  });

  it('keeps the surface yielded when a waiting snapshot loses a fail race', async () => {
    const releaseDesktopHandoff = vi.fn(async () => true);
    (window as any).electronAPI = {
      prepareDesktopHandoff: vi.fn(async () => ({
        yielded: true,
        control_surface_pid: 7712,
        prepared_at: 800,
      })),
      bindDesktopHandoff: vi.fn(async () => true),
      releaseDesktopHandoff,
    };
    const failDesktopLease = vi.fn(async () => {
      throw new Error('already delivered');
    });

    await expect(fulfillDesktopLease(
      {
        captureDesktopContext: vi.fn(async () => target()),
        bindDesktopLease: vi.fn(async () => {
          throw new Error('response lost');
        }),
        failDesktopLease,
        run: vi.fn(async () => ({
          ok: true,
          run: snapshot(),
          arp: {
            ...snapshot(),
            desktop_lease: {
              format: 'desktop-lease/1',
              run_id: 'run-goal-test',
              thread_id: 'thread-goal-test',
              turn_id: 'turn-goal-test',
              lease_id: 'lease-goal-test',
              status: 'waiting',
              error: '',
            },
          },
        })),
      } as never,
      'run-goal-test',
      'lease-goal-test',
    )).rejects.toThrow('response lost');

    expect(failDesktopLease).toHaveBeenCalledOnce();
    expect(releaseDesktopHandoff).not.toHaveBeenCalled();
  });

  it('fails the matching lease and releases the handoff when local binding degrades', async () => {
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
    const failDesktopLease = vi.fn(async () => ({}));
    const bindDesktopLease = vi.fn(async () => ({}));

    await expect(fulfillDesktopLease(
      {
        captureDesktopContext: vi.fn(async () => target()),
        bindDesktopLease,
        failDesktopLease,
      } as never,
      'run-goal-test',
      'lease-goal-test',
    )).rejects.toThrow('ipc unavailable');

    expect(bindDesktopLease).not.toHaveBeenCalled();
    expect(failDesktopLease).toHaveBeenCalledWith(
      'run-goal-test',
      'lease-goal-test',
      'ipc unavailable',
    );
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
    startGoal = vi.fn(async () => goalRunStart()),
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
    let finishRun: ((started: GoalRunStart) => void) | undefined;
    const startGoal = vi.fn(() => new Promise<GoalRunStart>((resolve) => {
      finishRun = resolve;
    }));
    const user = userEvent.setup();
    render(<AgentCockpit plane={cockpitPlane(startGoal) as never} />);

    await user.type(screen.getByRole('textbox'), 'Complete the visible task');
    await user.click(screen.getByRole('button', { name: '发送消息' }));

    expect(startGoal).toHaveBeenCalledWith('Complete the visible task', '');
    expect(screen.queryByLabelText('执行约束')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '发送消息' })).toBeDisabled();
    finishRun?.(goalRunStart());
    await waitFor(() => {
      expect(screen.getByRole('textbox', { name: '消息输入' })).not.toBeDisabled();
    });
    expect(localStorage.getItem('arp-last-goal')).toBeNull();
    expect(JSON.parse(localStorage.getItem('arp-agent-thread-run-ids') || '{}')).toEqual({
      thread_id: 'thread-goal-test',
      run_ids: ['run-goal-test'],
    });
    await user.type(screen.getByRole('textbox', { name: '消息输入' }), 'Run another task');
    expect(screen.getByRole('button', { name: '发送消息' })).not.toBeDisabled();
  });

  it('does not send while a Chinese IME is committing text with Enter', async () => {
    const startGoal = vi.fn(async () => goalRunStart());
    const user = userEvent.setup();
    render(<AgentCockpit plane={cockpitPlane(startGoal) as never} />);

    const composer = screen.getByRole('textbox', { name: '消息输入' });
    await user.type(composer, '继续说明');
    fireEvent.keyDown(composer, {
      key: 'Enter',
      code: 'Enter',
      keyCode: 229,
      isComposing: true,
    });

    expect(startGoal).not.toHaveBeenCalled();
    expect(composer).toHaveValue('继续说明');
    fireEvent.keyDown(composer, { key: 'Enter', code: 'Enter', keyCode: 13 });
    await waitFor(() => expect(startGoal).toHaveBeenCalledOnce());
  });

  it('keeps the draft and recovery value when starting a task fails', async () => {
    const startGoal = vi.fn(async () => { throw new Error('Provider unavailable'); });
    const user = userEvent.setup();
    render(<AgentCockpit plane={cockpitPlane(startGoal) as never} />);

    const composer = screen.getByRole('textbox', { name: '消息输入' });
    await user.type(composer, 'Keep this exact request');
    await user.click(screen.getByRole('button', { name: '发送消息' }));

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Provider unavailable'));
    expect(composer).toHaveValue('Keep this exact request');
    expect(localStorage.getItem('arp-last-goal')).toBe('Keep this exact request');
  });

  it('restores a failed submission draft without clearing it during run hydration', () => {
    localStorage.setItem('arp-last-goal', 'Recover this failed submission');
    localStorage.setItem('arp-agent-thread-run-ids', JSON.stringify({
      thread_id: 'thread-previous',
      run_ids: ['run-previous'],
    }));

    render(
      <AgentCockpit
        plane={cockpitPlane(
          vi.fn(async () => goalRunStart()),
          {
            run_id: 'run-previous',
            kind: 'agent',
            status: 'reply',
            phase: 'done',
            goal: 'Previous successful request',
            started_at: 1,
            updated_at: 2,
            finished_at: 2,
            result: 'Previous answer',
            timeline: [],
            evidence: [],
            metrics: {},
          },
        ) as never}
      />,
    );

    expect(screen.getByRole('textbox', { name: '消息输入' })).toHaveValue(
      'Recover this failed submission',
    );
    expect(localStorage.getItem('arp-last-goal')).toBe('Recover this failed submission');
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
      JSON.stringify({
        thread_id: 'thread-shared-composer',
        run_ids: [completedRun.run_id],
      }),
    );

    const conversation = render(
      <AgentCockpit
        plane={cockpitPlane(
          vi.fn(async () => goalRunStart()),
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
          vi.fn(async () => goalRunStart()),
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
          vi.fn(async () => goalRunStart()),
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
    const startGoal = vi.fn(async () => goalRunStart());
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
    expect(screen.getByRole('alert')).toHaveTextContent('The task could not be verified.');
    const composer = screen.getByRole('textbox', { name: '消息输入' });
    expect(composer).toBeVisible();

    const threadBeforeEdit = localStorage.getItem('arp-agent-thread-run-ids');
    await user.click(screen.getByRole('button', { name: '再次编辑' }));
    expect(composer).toHaveValue('Summarize the visible conversations');
    expect(startGoal).not.toHaveBeenCalled();
    expect(localStorage.getItem('arp-agent-thread-run-ids')).toBe(threadBeforeEdit);
    await user.clear(composer);

    await user.type(composer, 'Retry with the visible window');
    await user.click(screen.getByRole('button', { name: '发送消息' }));
    expect(startGoal).toHaveBeenCalledWith('Retry with the visible window', '');
  });

  it('shows an explicit failure result as a failure even when the status says reply', () => {
    render(
      <AgentCockpit
        plane={cockpitPlane(
          vi.fn(async () => goalRunStart()),
          {
            run_id: 'run-failed-reply',
            kind: 'agent',
            status: 'reply',
            phase: 'done',
            goal: 'Answer without using the desktop',
            started_at: 1,
            updated_at: 2,
            finished_at: 2,
            result: { ok: false, error: 'Provider connection failed.' },
            timeline: [],
            evidence: [],
            metrics: {},
          },
        ) as never}
      />,
    );

    expect(screen.queryByRole('status', { name: '任务结果' })).not.toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('Provider connection failed.');
    expect(screen.getByRole('alert')).toHaveTextContent('任务未完成');
    expect(screen.getByRole('alert')).not.toHaveTextContent('已回复');
    expect(screen.getByRole('button', { name: '再次编辑' })).toBeVisible();
    expect(screen.getByRole('button', { name: '打开资料' })).toHaveTextContent('任务未完成');
    expect(document.querySelector('.ld-window-signal.stopped')).not.toBeNull();
  });

  it('keeps vnext JSON reply text as conversation content', () => {
    const result = JSON.stringify({ ok: false, error: 'Example payload only' });
    render(
      <AgentCockpit
        plane={cockpitPlane(
          vi.fn(async () => goalRunStart()),
          {
            run_id: 'run-json-reply',
            kind: 'arp',
            runtime_version: 'vnext',
            no_legacy_fallback: true,
            status: 'reply',
            phase: 'terminal',
            goal: 'Show a JSON failure example',
            started_at: 1,
            updated_at: 2,
            finished_at: 2,
            result,
            timeline: [],
            evidence: [],
            metrics: {},
          },
        ) as never}
      />,
    );

    expect(screen.getByRole('status', { name: '任务结果' })).toHaveTextContent(result);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '打开资料' })).toHaveTextContent('Agent 已回复');
  });

  it('locks conversation mutation controls while a follow-up is submitting', async () => {
    const startGoal = vi.fn(() => new Promise<GoalRunStart>(() => {}));
    const user = userEvent.setup();
    render(
      <AgentCockpit
        plane={cockpitPlane(
          startGoal,
          {
            run_id: 'run-edit-race',
            kind: 'arp',
            status: 'failed',
            phase: 'failed',
            goal: 'Original failed request',
            started_at: 1,
            updated_at: 2,
            finished_at: 2,
            error: 'Provider unavailable',
            timeline: [],
            evidence: [],
            metrics: {},
          },
        ) as never}
      />,
    );

    await user.type(screen.getByRole('textbox', { name: '消息输入' }), 'Try a new request');
    await user.click(screen.getByRole('button', { name: '发送消息' }));

    expect(startGoal).toHaveBeenCalledOnce();
    expect(screen.getByRole('button', { name: '再次编辑' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '新建对话' })).toBeDisabled();
  });

  it.each(['waiting_external', 'canceling'])(
    'keeps the %s transition active without rendering a failure card',
    (status) => {
      render(
        <AgentCockpit
          plane={cockpitPlane(
            vi.fn(async () => goalRunStart()),
            {
              run_id: `run-${status}`,
              kind: 'agent',
              status,
              phase: status,
              goal: 'Wait for the current task transition',
              started_at: 1,
              updated_at: 2,
              timeline: [],
              evidence: [],
              metrics: {},
            },
          ) as never}
        />,
      );

      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
      expect(screen.getByLabelText('Agent 当前状态')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '发送消息' })).toBeDisabled();
    },
  );

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
      JSON.stringify({
        thread_id: 'thread-context',
        run_ids: [firstRun.run_id, secondRun.run_id],
      }),
    );
    const thirdRun = {
      ...snapshot(),
      run_id: 'run-thread-third',
      graph_name: 'Continue with the same visible context',
    };
    const startGoal = vi.fn(async () => goalRunStart(
      thirdRun,
      'thread-context',
      'turn-thread-third',
    ));

    render(
      <AgentCockpit
        plane={cockpitPlane(
          startGoal,
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

    await user.type(
      screen.getByRole('textbox', { name: '消息输入' }),
      'Continue with the same visible context',
    );
    await user.click(screen.getByRole('button', { name: '发送消息' }));
    expect(startGoal).toHaveBeenCalledWith(
      'Continue with the same visible context',
      'thread-context',
    );
    await waitFor(() => {
      expect(JSON.parse(localStorage.getItem('arp-agent-thread-run-ids') || '{}')).toEqual({
        thread_id: 'thread-context',
        run_ids: [firstRun.run_id, secondRun.run_id, thirdRun.run_id],
      });
    });

    await user.click(screen.getByRole('button', { name: '新建对话' }));
    expect(screen.getByRole('region', { name: '新建对话' })).toBeInTheDocument();
    expect(screen.queryByText('The visible total is 42.')).not.toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem('arp-agent-thread-run-ids') || '{}')).toEqual({
      thread_id: '',
      run_ids: [],
    });
    expect(localStorage.getItem('arp-last-goal')).toBeNull();
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
    localStorage.setItem('arp-agent-thread-run-ids', JSON.stringify({
      thread_id: 'thread-active',
      run_ids: [activeRun.run_id],
    }));
    const user = userEvent.setup();

    render(
      <AgentCockpit
        plane={cockpitPlane(
          vi.fn(async () => goalRunStart()),
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
          vi.fn(async () => goalRunStart()),
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
          vi.fn(async () => goalRunStart()),
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
