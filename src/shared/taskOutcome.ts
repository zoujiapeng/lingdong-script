export type TaskOutcomeKind = 'success' | 'error' | 'info';

const UNCONDITIONAL_SUCCESS_STATUSES = new Set(['done', 'completed', 'success']);
const STOPPED_STATUSES = new Set(['canceled', 'cancelled', 'stopped']);
const FAILURE_STATUSES = new Set(['failed', 'fail', 'error', 'blocked']);
const TERMINAL_STATUSES = new Set([
  ...UNCONDITIONAL_SUCCESS_STATUSES,
  ...STOPPED_STATUSES,
  ...FAILURE_STATUSES,
  'reply',
]);

const FAILURE_TRUE_KEYS = new Set([
  'blocked',
  'failed',
  'has_error',
  'safe_stop',
]);
const FAILURE_FALSE_KEYS = new Set(['ok', 'success']);
const FAILURE_VALUE_KEYS = new Set([
  'error',
  'failure',
  'failure_reason',
  'failure_type',
]);

const MAX_INSPECTION_DEPTH = 8;
const MAX_INSPECTION_NODES = 500;

function parsedJson(value: string): unknown {
  const text = value.trim();
  if (!text || !['{', '['].includes(text[0])) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

function hasFailureValue(value: unknown): boolean {
  if (value === null || value === undefined || value === false) return false;
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase();
    return Boolean(normalized) && !['none', 'null', 'false', 'ok', 'success'].includes(normalized);
  }
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === 'object') return Object.keys(value).length > 0;
  return Boolean(value);
}

function inspectFailureSignal(
  value: unknown,
  seen: Set<object>,
  state: { nodes: number },
  depth: number,
): boolean {
  if (depth > MAX_INSPECTION_DEPTH || state.nodes >= MAX_INSPECTION_NODES) return false;
  state.nodes += 1;

  if (typeof value === 'string') {
    const parsed = parsedJson(value);
    return parsed !== undefined
      ? inspectFailureSignal(parsed, seen, state, depth + 1)
      : false;
  }
  if (!value || typeof value !== 'object') return false;
  if (value instanceof Error) return true;
  if (seen.has(value)) return false;
  seen.add(value);

  if (Array.isArray(value)) {
    return value.some(item => inspectFailureSignal(item, seen, state, depth + 1));
  }

  const record = value as Record<string, unknown>;
  for (const [rawKey, child] of Object.entries(record)) {
    const key = rawKey.trim().toLowerCase();
    if (FAILURE_TRUE_KEYS.has(key) && child === true) return true;
    if (FAILURE_FALSE_KEYS.has(key) && child === false) return true;
    if (FAILURE_VALUE_KEYS.has(key) && hasFailureValue(child)) return true;
    if (
      key === 'status'
      && typeof child === 'string'
      && FAILURE_STATUSES.has(child.trim().toLowerCase())
    ) {
      return true;
    }
  }
  return Object.values(record).some(
    child => inspectFailureSignal(child, seen, state, depth + 1),
  );
}

export function hasExplicitTaskFailure(value: unknown): boolean {
  return inspectFailureSignal(value, new Set<object>(), { nodes: 0 }, 0);
}

export function isTerminalTaskStatus(status: unknown): boolean {
  return TERMINAL_STATUSES.has(String(status || '').trim().toLowerCase());
}

export function classifyTaskOutcome(
  status: unknown,
  result?: unknown,
  error?: unknown,
): TaskOutcomeKind {
  const normalized = String(status || '').trim().toLowerCase();
  if (STOPPED_STATUSES.has(normalized)) return 'info';
  if (hasExplicitTaskFailure({ result, error })) return 'error';
  if (UNCONDITIONAL_SUCCESS_STATUSES.has(normalized) || normalized === 'reply') return 'success';
  return 'error';
}

export function taskResultText(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value && typeof value === 'object') {
    try {
      return JSON.stringify(value);
    } catch {
      return '';
    }
  }
  return value === null || value === undefined ? '' : String(value);
}
