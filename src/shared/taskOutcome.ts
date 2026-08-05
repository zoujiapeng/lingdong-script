export type TaskOutcomeKind = 'success' | 'error' | 'info';

const UNCONDITIONAL_SUCCESS_STATUSES = new Set(['done', 'completed', 'success']);
const STOPPED_STATUSES = new Set(['canceled', 'cancelled', 'stopped', 'interrupted']);
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

const FAILURE_STAGE_LABELS: Record<string, string> = {
  model_routing: '模型决策',
  desktop_handoff: '桌面交接',
  target_binding: '目标窗口绑定',
  screenshot: '画面截图',
  tool_format: '工具调用格式',
  physical_input: '物理输入',
  result_verification: '结果验证',
  canceled: '已取消',
};

export function readableFailureStage(stage: unknown): string {
  const key = String(stage || '').trim().toLowerCase();
  return FAILURE_STAGE_LABELS[key] || key || '未知';
}

const READABLE_ERRORS: Array<[RegExp, string]> = [
  [/vNext turn budget exhausted/, '执行回合数用尽：模型在限制内未能完成任务'],
  [/vNext model-call budget exhausted/, '模型调用次数用尽'],
  [/vNext runtime time budget exhausted/, '执行时间超时'],
  [/vNext Runtime returned blocked/, '模型报告无法继续'],
  [/vNext Runtime returned invalid_proposal/, '模型提交了无效的工具调用'],
  [/Evidence decision failed closed/, '动作校验未通过，未执行'],
  [/permit[a-z_]* (?:rejected|changed|not)/, '内部执行许可未通过'],
  [/run canceled/, '运行已取消'],
];

export function readableRunError(error: unknown, stage?: unknown): string {
  const raw = String(error || '').trim();
  if (!raw) return '';
  for (const [pattern, label] of READABLE_ERRORS) {
    if (pattern.test(raw)) {
      const stageLabel = readableFailureStage(stage);
      return stageLabel ? `${label}（失败阶段：${stageLabel}）` : label;
    }
  }
  return raw;
}
