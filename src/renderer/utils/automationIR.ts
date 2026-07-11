import { nanoid } from 'nanoid';
import type { FlowScript, FlowNode, FlowEdge } from '../types';
import { wpsToFlowScript, type WpsScript, type WpsStep } from './wpsToFlowScript';

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
    x: node.x,
    y: node.y,
    label: node.label || '',
    params: clone(node.params || {}),
    outgoing: edges
      .filter((edge) => edge.from_node === node.id)
      .map((edge) => ({
        edge_id: edge.id,
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
      meta: clone(script.meta || {}),
      settings: clone(script.settings || {}),
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
  const conditions = clone(ir.conditions || {}) as Record<string, unknown>;
  const evidence = ir.evidence;
  const script: WpsScript = {
    format: 'wps/2',
    name: str(ir.name) || 'Automation',
    task: str(source.task),
    source: 'automation-ir',
    source_ir: str(ir.id),
    created: created(ir.created),
    vars: clone(ir.variables || {}),
    steps: clone(ir.steps || []) as WpsStep[],
    safety: clone(ir.safety || {}),
    fallback: clone(ir.fallback || {}),
  };
  if (conditions.target_window !== undefined && conditions.target_window !== null && conditions.target_window !== '') {
    script.target_window = clone(conditions.target_window);
  }
  if (Object.keys(conditions).length > 0) {
    script.conditions = conditions;
  }
  if (Array.isArray((evidence as Record<string, unknown> | undefined)?.required)) {
    script.evidence = clone((evidence as Record<string, unknown>).required as unknown[]);
  } else if (Array.isArray(evidence)) {
    script.evidence = clone(evidence);
  }
  return script;
}

export function automationIrToFlowScript(ir: AutomationIR): FlowScript {
  const error = validateAutomationIR(ir);
  if (error) throw new Error(error);
  const source = ir.source || {};
  if (source.kind === 'wps') return wpsToFlowScript(automationIrToWps(ir));
  if (source.kind !== 'flowscript') {
    throw new Error(`Unsupported Automation IR source kind: ${String(source.kind || '')}`);
  }
  const nodes: FlowNode[] = (ir.steps || []).map((raw, index) => {
    const step = raw as Record<string, any>;
    return {
      id: str(step.node_id) || `ir-node-${index + 1}`,
      type: str(step.tool) as FlowNode['type'],
      x: Number.isFinite(Number(step.x)) ? Number(step.x) : 80,
      y: Number.isFinite(Number(step.y)) ? Number(step.y) : index * 112,
      params: clone(step.params || {}),
      ...(str(step.label) ? { label: str(step.label) } : {}),
    };
  });
  const edges: FlowEdge[] = [];
  for (const raw of ir.steps || []) {
    const step = raw as Record<string, any>;
    const fromNode = str(step.node_id);
    for (const outgoing of Array.isArray(step.outgoing) ? step.outgoing : []) {
      edges.push({
        id: str(outgoing.edge_id) || nanoid(8),
        from_node: fromNode,
        from_port: str(outgoing.from_port) || 'output',
        to_node: str(outgoing.to_node),
        to_port: str(outgoing.to_port) || 'input',
      });
    }
  }
  const meta = clone((source.meta as FlowScript['meta']) || {}) as FlowScript['meta'];
  const now = new Date(created(ir.created) * 1000).toISOString();
  const settings = clone((source.settings as FlowScript['settings']) || {}) as FlowScript['settings'];
  return {
    meta: {
      id: str(meta.id) || str(source.flow_id) || nanoid(10),
      name: str(meta.name) || ir.name,
      description: str(meta.description),
      created_at: str(meta.created_at) || now,
      updated_at: str(meta.updated_at) || now,
      version: str(meta.version) || str(source.format) || '1.0.0',
      ...(meta.source ? { source: meta.source } : {}),
      ...(meta.tags ? { tags: meta.tags } : {}),
    },
    nodes,
    edges,
    variables: clone(ir.variables || {}) as Record<string, string>,
    settings: {
      default_delay: Number(settings.default_delay ?? 0),
      error_handling: settings.error_handling || 'stop',
      max_retries: Number(settings.max_retries ?? 2),
      retry_delay: Number(settings.retry_delay ?? 1),
      log_level: settings.log_level || 'info',
      ...(settings.require_real_features !== undefined
        ? { require_real_features: settings.require_real_features }
        : {}),
    },
  };
}

export function automationDocumentToFlowScript(value: unknown): FlowScript {
  const document = value as Record<string, any>;
  if (!document || typeof document !== 'object' || Array.isArray(document)) {
    throw new Error('Automation document must be a JSON object');
  }
  if (document.format === 'automation-ir/1') {
    return automationIrToFlowScript(document as AutomationIR);
  }
  if (String(document.format || '').startsWith('wps/')) {
    return wpsToFlowScript(document as WpsScript);
  }
  if (document.meta && Array.isArray(document.nodes) && Array.isArray(document.edges)) {
    return clone(document) as FlowScript;
  }
  throw new Error(`Unsupported automation document: ${String(document.format || 'unknown format')}`);
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
