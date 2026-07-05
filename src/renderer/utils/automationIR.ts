import { nanoid } from 'nanoid';
import type { FlowScript, FlowNode, FlowEdge } from '../types';
import type { WpsScript, WpsStep } from './wpsToFlowScript';

export interface AutomationIR {
  format: 'automation-ir/1';
  id: string;
  name: string;
  created: number;
  source: Record<string, unknown>;
  conditions: Record<string, unknown>;
  evidence: Record<string, unknown>;
  steps: Array<Record<string, unknown>>;
  variables: Record<string, string>;
  safety: Record<string, unknown>;
  fallback: Record<string, unknown>;
}

const DESKTOP_MUTATION_TOOLS = new Set([
  'click',
  'type_text',
  'hotkey',
  'launch',
  'focus_window',
  'scroll',
  'drag',
  'draw_strokes',
  'run_detached',
  'tray_click',
  'ext.clipboard_set',
]);
const SHELL_TOOLS = new Set(['run_shell', 'ext.run_shell']);

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value ?? null));
}

function nowSeconds(): number {
  return Math.floor(Date.now() / 1000);
}

function created(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : nowSeconds();
}

function str(value: unknown): string {
  return value == null ? '' : String(value).trim();
}

function walkWpsSteps(steps: WpsStep[]): WpsStep[] {
  const out: WpsStep[] = [];
  for (const step of steps || []) {
    out.push(step);
    for (const key of ['then', 'else', 'body']) {
      const nested = step[key];
      if (Array.isArray(nested)) out.push(...walkWpsSteps(nested as WpsStep[]));
    }
  }
  return out;
}

function defaultSafetyForWps(steps: WpsStep[]): Record<string, unknown> {
  const flat = walkWpsSteps(steps);
  const mutatesDesktop = flat.some((step) => DESKTOP_MUTATION_TOOLS.has(String(step.tool || '')));
  const usesShell = flat.some((step) => SHELL_TOOLS.has(String(step.tool || '')));
  return {
    requires_preflight: mutatesDesktop || usesShell,
    mutates_desktop: mutatesDesktop,
    uses_shell: usesShell,
    desktop_action_queue_required: mutatesDesktop,
    shell_requires_explicit_runtime: usesShell,
  };
}

function targetWindowFromWps(script: WpsScript): unknown {
  const anyScript = script as Record<string, any>;
  if (anyScript.target_window) return clone(anyScript.target_window);
  if (anyScript.target) return clone(anyScript.target);
  const conditions = anyScript.conditions;
  if (conditions && typeof conditions === 'object' && !Array.isArray(conditions)) {
    for (const key of ['target_window', 'window_title', 'window_appears', 'target']) {
      if (conditions[key]) return clone(conditions[key]);
    }
  }
  return undefined;
}

export function automationIrFromWps(script: WpsScript, id = `air-${nanoid(12)}`): AutomationIR {
  const anyScript = script as Record<string, any>;
  const conditions = anyScript.conditions && typeof anyScript.conditions === 'object' && !Array.isArray(anyScript.conditions)
    ? clone(anyScript.conditions)
    : {};
  const targetWindow = targetWindowFromWps(script);
  if (targetWindow !== undefined) conditions.target_window = targetWindow;
  const steps = clone(script.steps || []) as WpsStep[];
  const task = str(script.task);

  return {
    format: 'automation-ir/1',
    id,
    name: str(script.name) || task || 'Automation',
    created: created(anyScript.created),
    source: {
      kind: 'wps',
      format: script.format || 'wps/2',
      source: script.source || '',
      source_run: script.source_run || '',
      task,
    },
    conditions,
    evidence: Array.isArray(anyScript.evidence) ? { required: clone(anyScript.evidence) } : clone(anyScript.evidence || { required: [] }),
    steps: steps as Array<Record<string, unknown>>,
    variables: clone(script.vars || {}),
    safety: defaultSafetyForWps(steps),
    fallback: {
      on_preflight_fail: 'live_agent_takeover',
      on_step_fail: 'resume_live_from_failed_step',
    },
  };
}

function serializeFlowStep(node: FlowNode, edges: FlowEdge[]): Record<string, unknown> {
  return {
    tool: node.type,
    node_id: node.id,
    params: clone(node.params || {}),
    outgoing: edges
      .filter((edge) => edge.from_node === node.id)
      .map((edge) => ({
        from_port: edge.from_port,
        to_node: edge.to_node,
        to_port: edge.to_port,
      })),
  };
}

export function automationIrFromFlowScript(script: FlowScript, id = `air-${nanoid(12)}`): AutomationIR {
  return {
    format: 'automation-ir/1',
    id,
    name: str(script.meta?.name) || 'Flow automation',
    created: nowSeconds(),
    source: {
      kind: 'flowscript',
      format: script.meta?.version || '1.0.0',
      flow_id: script.meta?.id || '',
    },
    conditions: {},
    evidence: { required: [] },
    steps: (script.nodes || []).map((node) => serializeFlowStep(node, script.edges || [])),
    variables: clone(script.variables || {}),
    safety: {
      requires_preflight: true,
      mutates_desktop: true,
      desktop_action_queue_required: true,
      executor: 'flowscript',
    },
    fallback: {
      on_preflight_fail: 'live_agent_takeover',
      on_step_fail: 'open_flow_editor_at_failed_node',
    },
  };
}

export function automationIrToWps(ir: AutomationIR): WpsScript {
  if (ir.format !== 'automation-ir/1') throw new Error('Unsupported Automation IR format');
  const source = ir.source || {};
  if (source.kind !== 'wps') throw new Error('Only WPS-sourced Automation IR can be converted to WPS');
  return {
    format: 'wps/2',
    name: str(ir.name) || 'Automation',
    task: str(source.task),
    source: 'automation-ir',
    source_run: str(source.source_run),
    vars: clone(ir.variables || {}),
    steps: clone(ir.steps || []) as WpsStep[],
  };
}

export function validateAutomationIR(value: unknown): string | undefined {
  const ir = value as AutomationIR;
  if (!ir || typeof ir !== 'object') return 'Automation IR must be an object';
  if (ir.format !== 'automation-ir/1') return `unknown Automation IR format: ${(ir as any).format}`;
  if (!str(ir.id)) return 'Automation IR requires id';
  if (!str(ir.name)) return 'Automation IR requires name';
  for (const key of ['source', 'conditions', 'evidence', 'safety', 'fallback'] as const) {
    if (!ir[key] || typeof ir[key] !== 'object' || Array.isArray(ir[key])) return `${key} must be an object`;
  }
  if (!ir.variables || typeof ir.variables !== 'object' || Array.isArray(ir.variables)) return 'variables must be an object';
  if (!Array.isArray(ir.steps)) return 'steps must be a list';
  for (let i = 0; i < ir.steps.length; i += 1) {
    const step = ir.steps[i] as Record<string, unknown>;
    if (!step || typeof step !== 'object' || Array.isArray(step)) return `steps[${i + 1}] must be an object`;
    if (!str(step.tool)) return `steps[${i + 1}] requires tool`;
  }
  return undefined;
}
