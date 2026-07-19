import type { RunProjection } from './contracts';
import {
  classifyTaskOutcome,
  isTerminalTaskStatus,
  taskResultText,
} from '../../shared/taskOutcome';

export type TaskbarNotificationKind = 'success' | 'error' | 'info';

export interface TaskbarRunState {
  runId: string;
  status: string;
}

export interface TaskbarNotificationRequest {
  title: string;
  body: string;
  kind: TaskbarNotificationKind;
  runId: string;
}

const ACTIVE_RUN_STATUSES = new Set(['queued', 'running', 'paused', 'canceling']);

export function taskbarNotificationForTransition(
  previous: TaskbarRunState | null,
  currentRun: RunProjection,
  observedLiveStart = false,
): TaskbarNotificationRequest | null {
  const current: TaskbarRunState = {
    runId: currentRun.run_id,
    status: String(currentRun.status || '').toLowerCase(),
  };
  if (!isTerminalTaskStatus(current.status)) return null;
  if (!previous && !observedLiveStart) return null;
  const observedActiveTransition = (
    previous?.runId === current.runId
    && ACTIVE_RUN_STATUSES.has(previous.status)
  );
  // A very short run can start and finish between two overview polls. After
  // initial hydration, a new terminal run is therefore also notification-worthy.
  const newlyObservedTerminalRun = Boolean(previous && previous.runId !== current.runId);
  if (!observedActiveTransition && !newlyObservedTerminalRun && !observedLiveStart) {
    return null;
  }

  const outcome = classifyTaskOutcome(
    current.status,
    currentRun.result,
    currentRun.error,
  );
  const success = outcome === 'success';
  const stopped = outcome === 'info';
  const title = success ? '任务完成' : stopped ? '任务已停止' : '任务失败';
  const body = String(
    currentRun.error
      || taskResultText(currentRun.result)
      || currentRun.goal
      || currentRun.run_id,
  ).slice(0, 220);
  return {
    title,
    body,
    kind: success ? 'success' : stopped ? 'info' : 'error',
    runId: current.runId,
  };
}
