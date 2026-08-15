import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PlatformWorkbench } from '../components/platform/PlatformWorkbench';
import { useStore } from '../store';

const controlPlane = vi.hoisted(() => ({ current: null as unknown }));

vi.mock('../platform/useControlPlane', () => ({
  useControlPlane: () => controlPlane.current,
}));

const historicalRun = {
  run_id: 'run-history-1',
  kind: 'agent',
  status: 'reply',
  phase: 'terminal',
  goal: 'Remember the project marker',
  started_at: 10,
  updated_at: 12,
  finished_at: 12,
  result: 'The marker is ORANGE.',
  timeline: [],
  evidence: [],
  metrics: {},
};

function createPlane(currentRun: Record<string, unknown> | null = null) {
  const client = {
    listAgentThreads: vi.fn(async () => [{
      format: 'agent-thread/1',
      thread_id: 'thread-history-1',
      created_at: 10,
      updated_at: 12,
      turns: [{
        turn_id: 'turn-history-1',
        goal: historicalRun.goal,
        status: historicalRun.status,
        result: historicalRun.result,
        run_id: historicalRun.run_id,
        created_at: 10,
        updated_at: 12,
      }],
    }]),
    run: vi.fn(async () => ({ ok: true, run: historicalRun, arp: null })),
    traces: vi.fn(async () => []),
    scripts: vi.fn(async () => []),
    memories: vi.fn(async () => []),
    sessions: vi.fn(async () => []),
  };
  const startGoal = vi.fn(async (goal: string, threadId: string) => ({
    ok: true,
    run_id: 'run-followup-1',
    thread_id: threadId || 'thread-new-1',
    turn_id: 'turn-followup-1',
    runtime_version: 'vnext',
    admission: {
      format: 'goal-admission/1',
      goal,
      route: 'live_agent',
      runtime_version: 'vnext',
      reason: 'single_semantic_agent',
      no_legacy_fallback: true,
    },
    run: {
      run_id: 'run-followup-1',
      graph_id: 'graph-followup-1',
      graph_name: goal,
      status: 'queued',
    },
  }));
  const plane = {
    connection: 'online',
    baseUrl: 'http://127.0.0.1:8765/',
    overview: { status: { runtime_jobs: [], model_profile: { model: 'Visual Actor' } }, capabilities: [] },
    capabilities: [],
    currentRun,
    arpSnapshot: null,
    runs: currentRun ? [currentRun] : [historicalRun],
    events: [],
    error: '',
    runtimeNotice: '',
    refresh: vi.fn(async () => undefined),
    startGoal,
    startAutomation: vi.fn(),
    validateAutomation: vi.fn(),
    cancelCurrent: vi.fn(),
    pauseCurrent: vi.fn(),
    resumeCurrent: vi.fn(),
    decideGate: vi.fn(),
    getClient: () => client,
  };
  return { client, plane, startGoal };
}

beforeEach(() => {
  localStorage.clear();
  useStore.getState().setViewMode('agent');
});

afterEach(() => {
  cleanup();
  localStorage.clear();
  useStore.getState().setViewMode('overview');
  controlPlane.current = null;
});

describe('conversation and workbench navigation', () => {
  it('uses the former resource surface as a resumable conversation list', async () => {
    const { client, plane, startGoal } = createPlane();
    controlPlane.current = plane;
    const user = userEvent.setup();
    render(<PlatformWorkbench />);

    await user.click(screen.getByRole('button', { name: '打开对话列表' }));

    expect(await screen.findByRole('complementary', { name: '对话列表' })).toBeInTheDocument();
    expect(client.listAgentThreads).toHaveBeenCalledOnce();
    expect(screen.queryByRole('button', { name: '运行记录' })).not.toBeInTheDocument();

    await user.click(await screen.findByRole('button', { name: `继续对话：${historicalRun.goal}` }));

    expect(screen.queryByRole('complementary', { name: '对话列表' })).not.toBeInTheDocument();
    expect(screen.getByText(historicalRun.result)).toBeInTheDocument();
    await user.type(screen.getByRole('textbox', { name: '消息输入' }), 'Continue from that marker');
    await user.click(screen.getByRole('button', { name: '发送消息' }));

    await waitFor(() => expect(startGoal).toHaveBeenCalledWith(
      'Continue from that marker',
      'thread-history-1',
    ));
  });

  it('moves resources into the workbench and keeps the task board in the input capsule', async () => {
    const activeRun = {
      ...historicalRun,
      run_id: 'run-active-1',
      status: 'running',
      phase: 'acting',
      finished_at: null,
      current_tool: 'run_shell',
      task_state: '1. Run the focused check\n2. Report the result',
      task_state_revision: 2,
      timeline: [{
        type: 'live_agent_activity',
        ts: 13,
        kind: 'tool_start',
        tool: 'run_shell',
        status: 'running',
        summary: 'Running the focused check',
      }],
    };
    const { plane } = createPlane(activeRun);
    controlPlane.current = plane;
    const user = userEvent.setup();
    render(<PlatformWorkbench />);

    await waitFor(() => expect(document.querySelector('.ld-capsule-task-board')).not.toBeNull());
    const taskBoard = document.querySelector('.ld-capsule-task-board');
    expect(taskBoard).toHaveTextContent('Run the focused check');
    expect(taskBoard).toHaveTextContent('模型已更新 2 次');
    expect(document.querySelector('.ld-activity-capsule .ld-progress-ring')).not.toBeNull();

    const commandOrb = screen.getByRole('button', { name: '打开命令面板' });
    await user.click(commandOrb);
    expect(screen.getByRole('dialog', { name: '命令面板' })).toBeInTheDocument();
    await user.keyboard('{Escape}');
    await user.click(screen.getByRole('button', { name: '打开工作台' }));

    const workbench = screen.getByRole('complementary', { name: '工作台' });
    expect(workbench).toBeInTheDocument();
    expect(within(workbench).getByRole('button', { name: '运行记录' })).toBeInTheDocument();
    expect(within(workbench).getByRole('button', { name: '技能与记忆' })).toBeInTheDocument();
    expect(within(workbench).getByRole('button', { name: '设置' })).toBeInTheDocument();
  });
});
