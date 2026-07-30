import { describe, expect, it } from 'vitest';
import type { RunProjection } from '../platform/contracts';
import { taskbarNotificationForTransition } from '../platform/taskbarNotification';

function run(status: string, overrides: Partial<RunProjection> = {}): RunProjection {
  return {
    run_id: 'run-1',
    kind: 'agent',
    status,
    phase: status,
    goal: '整理桌面文件',
    started_at: 1,
    updated_at: 2,
    timeline: [],
    evidence: [],
    metrics: {} as RunProjection['metrics'],
    ...overrides,
  };
}

describe('taskbarNotificationForTransition', () => {
  it('notifies once when a running task completes', () => {
    expect(taskbarNotificationForTransition(
      { runId: 'run-1', status: 'running' },
      run('done', { result: '桌面整理完成' }),
    )).toEqual({
      title: '任务完成',
      body: '桌面整理完成',
      kind: 'success',
      runId: 'run-1',
    });
  });

  it('uses error details and an error taskbar state for failures', () => {
    expect(taskbarNotificationForTransition(
      { runId: 'run-1', status: 'running' },
      run('error', { error: '目标窗口已关闭' }),
    )).toEqual({
      title: '任务失败',
      body: '目标窗口已关闭',
      kind: 'error',
      runId: 'run-1',
    });
  });

  it('treats a recovered interrupted run as stopped', () => {
    expect(taskbarNotificationForTransition(
      { runId: 'run-1', status: 'running' },
      run('interrupted', { error: 'Recovered incomplete persisted run' }),
    )).toEqual({
      title: '任务已停止',
      body: 'Recovered incomplete persisted run',
      kind: 'info',
      runId: 'run-1',
    });
  });

  it('notifies a new terminal run that completed between overview polls', () => {
    expect(taskbarNotificationForTransition(
      { runId: 'old-run', status: 'done' },
      run('reply', { run_id: 'fast-run', result: '快速任务完成' }),
    )).toEqual({
      title: '任务完成',
      body: '快速任务完成',
      kind: 'success',
      runId: 'fast-run',
    });
  });

  it('treats a reply containing serialized failure signals as a failure', () => {
    const result = JSON.stringify({
      ok: false,
      blocked: true,
      safe_stop: true,
      error: '模型重复了被阻止的动作',
    });
    expect(taskbarNotificationForTransition(
      { runId: 'run-1', status: 'running' },
      run('reply', { result }),
    )).toEqual({
      title: '任务失败',
      body: result,
      kind: 'error',
      runId: 'run-1',
    });
  });

  it('treats nested object failure signals and a reply error as failures', () => {
    expect(taskbarNotificationForTransition(
      { runId: 'run-1', status: 'running' },
      run('reply', { result: { response: { ok: false } } }),
    )?.kind).toBe('error');
    expect(taskbarNotificationForTransition(
      { runId: 'run-1', status: 'running' },
      run('reply', { result: '没有完成', error: '目标窗口不可用' }),
    )).toMatchObject({
      title: '任务失败',
      body: '目标窗口不可用',
      kind: 'error',
    });
  });

  it('keeps a reply without explicit failure signals successful', () => {
    expect(taskbarNotificationForTransition(
      { runId: 'run-1', status: 'running' },
      run('reply', { result: JSON.stringify({ ok: true, answer: '已找到结果' }) }),
    )?.kind).toBe('success');
  });

  it('lets explicit failure signals override a done status', () => {
    expect(taskbarNotificationForTransition(
      { runId: 'run-1', status: 'running' },
      run('done', { result: { ok: false, error: '最终验证失败' } }),
    )).toMatchObject({
      title: '任务失败',
      kind: 'error',
    });
  });

  it('notifies the first run when its live start event was observed', () => {
    expect(taskbarNotificationForTransition(
      null,
      run('done', { run_id: 'first-live-run', result: '首个任务完成' }),
      true,
    )).toEqual({
      title: '任务完成',
      body: '首个任务完成',
      kind: 'success',
      runId: 'first-live-run',
    });
  });

  it('does not notify on initial load or non-terminal updates', () => {
    expect(taskbarNotificationForTransition(null, run('done'))).toBeNull();
    expect(taskbarNotificationForTransition(
      { runId: 'run-1', status: 'running' },
      run('paused'),
    )).toBeNull();
  });
});
