import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PlatformWorkbench } from '../components/platform/PlatformWorkbench';
import { useStore } from '../store';
import platformCss from '../styles/platform.css?raw';

const controlPlane = vi.hoisted(() => ({ current: null as unknown }));

vi.mock('../platform/useControlPlane', () => ({
  useControlPlane: () => controlPlane.current,
}));

function createPlane(options: {
  runtimeJobs?: Array<Record<string, unknown>>;
  replayStatus?: ReturnType<typeof vi.fn>;
} = {}) {
  let serverRecording = false;
  const client = {
    traces: vi.fn(async () => []),
    scripts: vi.fn(async () => []),
    memories: vi.fn(async () => []),
    sessions: vi.fn(async () => []),
    apprenticeStatus: vi.fn(async () => ({
      mode: 'apprentice' as const,
      recording: serverRecording,
      task: serverRecording ? 'Record the visible workflow' : '',
      session_id: serverRecording ? 'recording-1' : '',
      event_count: 0,
    })),
    apprenticeStart: vi.fn(async () => {
      serverRecording = true;
      throw new Error('start response was lost');
    }),
    apprenticeStop: vi.fn(async () => {
      serverRecording = false;
      throw new Error('stop response was lost');
    }),
    replayStatus: options.replayStatus || vi.fn(async () => ({
      replay_id: 'replay-1', status: 'done', ok: true,
    })),
    cancelRuntimeJob: vi.fn(),
  };
  const plane = {
    connection: 'online',
    baseUrl: 'http://127.0.0.1:8765/',
    overview: { status: { runtime_jobs: options.runtimeJobs || [] }, capabilities: [] },
    capabilities: [],
    currentRun: null,
    arpSnapshot: null,
    runs: [],
    events: [],
    error: '',
    runtimeIssue: '',
    refresh: vi.fn(async () => undefined),
    startGoal: vi.fn(),
    startAutomation: vi.fn(),
    validateAutomation: vi.fn(),
    cancelCurrent: vi.fn(),
    pauseCurrent: vi.fn(),
    resumeCurrent: vi.fn(),
    decideGate: vi.fn(),
    getClient: () => client,
  };
  return { client, plane };
}

beforeEach(() => {
  useStore.getState().setViewMode('assets');
});

afterEach(() => {
  cleanup();
  useStore.getState().setViewMode('overview');
  controlPlane.current = null;
});

describe('apprentice recording UX', () => {
  it('reconciles both ambiguous start and stop errors from the runtime status', async () => {
    const { client, plane } = createPlane();
    controlPlane.current = plane;
    const user = userEvent.setup();
    render(<PlatformWorkbench />);

    await user.click(await screen.findByRole('tab', { name: '示教学习' }));
    await user.type(screen.getByRole('textbox', { name: '要学习的工作流' }), 'Record the visible workflow');
    await user.type(screen.getByRole('textbox', { name: '终态证据值' }), 'Expected visible result');
    await user.click(screen.getByRole('button', { name: '开始学习' }));

    expect(await screen.findByText('start response was lost')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: '结束学习' })).toBeEnabled());
    expect(client.apprenticeStart).toHaveBeenCalledOnce();

    await user.click(screen.getByRole('button', { name: '结束学习' }));

    expect(await screen.findByText('stop response was lost')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: '开始学习' })).toBeEnabled());
    expect(client.apprenticeStop).toHaveBeenCalledOnce();
    expect(client.apprenticeStatus).toHaveBeenCalledTimes(3);
  });

  it('fits apprentice controls inside the drawer at the default 905px window width', () => {
    const style = document.createElement('style');
    style.textContent = platformCss;
    document.head.append(style);
    const mediaRules = Array.from(style.sheet?.cssRules || [])
      .filter((rule): rule is CSSMediaRule => 'media' in rule && 'cssRules' in rule);
    const responsiveRule = mediaRules.find((rule) => {
      const maxWidth = Number(/max-width:\s*(\d+)px/.exec(rule.media.mediaText)?.[1] || 0);
      if (maxWidth < 905) return false;
      return Array.from(rule.cssRules).some(
        (nested) => 'selectorText' in nested && nested.selectorText === '.arp-replay-toolbar',
      );
    });
    expect(responsiveRule).toBeDefined();

    const styleRules = Array.from(responsiveRule!.cssRules)
      .filter((rule): rule is CSSStyleRule => 'selectorText' in rule && 'style' in rule);
    const toolbar = styleRules.find((rule) => rule.selectorText === '.arp-replay-toolbar');
    const apprenticeToolbar = styleRules.find(
      (rule) => rule.selectorText === '.arp-replay-toolbar:has(.arp-apprentice-control)',
    );
    const controls = styleRules.find((rule) => rule.selectorText === '.arp-apprentice-control');
    expect(toolbar?.style.flexDirection).toBe('column');
    expect(apprenticeToolbar?.style.minHeight).toBe('80px');
    expect(Number.parseFloat(controls?.style.minWidth || '')).toBe(0);
    expect(controls?.style.width).toBe('100%');
    expect([
      controls?.style.flexGrow,
      controls?.style.flexShrink,
      controls?.style.flexBasis,
    ]).toEqual(['0', '0', 'auto']);
  });
});

describe('runtime-job polling UX', () => {
  const runningJob = {
    job_id: 'replay-1',
    kind: 'replay',
    status: 'running',
    resources: ['interactive_runtime'],
    error: '',
    created_at: 1,
    metadata: { script: 'lesson.wps.json' },
  };

  it('turns a definitive missing job into a retryable failed state', async () => {
    const replayStatus = vi.fn()
      .mockResolvedValueOnce({ replay_id: 'replay-1', status: 'running', ok: null })
      .mockRejectedValueOnce(Object.assign(new Error('runtime job not found'), { status: 404 }));
    const { plane } = createPlane({ runtimeJobs: [runningJob], replayStatus });
    controlPlane.current = plane;
    render(<PlatformWorkbench />);

    expect(await screen.findByText('运行任务状态已不存在，可以重新运行。')).toBeInTheDocument();
    expect(document.querySelector('.arp-replay-job.failed')).toBeInTheDocument();
    expect(document.querySelector('.arp-replay-job .arp-button-danger')).toBeNull();
    expect(replayStatus).toHaveBeenCalledTimes(2);
  });

  it('stops polling after a finite run of status-read errors', async () => {
    const replayStatus = vi.fn()
      .mockResolvedValueOnce({ replay_id: 'replay-1', status: 'running', ok: null })
      .mockRejectedValue(new Error('control plane unavailable'));
    const { plane } = createPlane({ runtimeJobs: [runningJob], replayStatus });
    controlPlane.current = plane;
    render(<PlatformWorkbench />);

    expect(await screen.findByText(
      /\u8fde\u7eed 3 \u6b21\u65e0\u6cd5\u8bfb\u53d6\u8fd0\u884c\u4efb\u52a1\u72b6\u6001/,
      {},
      { timeout: 4_000 },
    )).toBeInTheDocument();
    expect(document.querySelector('.arp-replay-job.failed')).toBeInTheDocument();
    expect(replayStatus).toHaveBeenCalledTimes(4);
  });
});
