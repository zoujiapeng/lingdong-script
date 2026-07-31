import {
  classifyTaskOutcome,
  type TaskOutcomeKind,
} from '../../shared/taskOutcome';
import type { RunProjection } from './contracts';

export function taskOutcomeForRun(run: RunProjection): TaskOutcomeKind {
  const replyTextIsContent = (
    run.status === 'reply'
    && run.runtime_version === 'vnext'
    && run.no_legacy_fallback === true
    && typeof run.result === 'string'
    && !run.error
  );
  return classifyTaskOutcome(
    run.status,
    replyTextIsContent ? undefined : run.result,
    run.error,
  );
}
