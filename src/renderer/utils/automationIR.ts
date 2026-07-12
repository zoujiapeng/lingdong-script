import { nanoid } from 'nanoid';
import type { FlowScript, FlowNode, FlowEdge } from '../types';
import { flowToArpGraph } from '../platform/flowToArpGraph';
import type { ArpGraph } from '../platform/contracts';
import { wpsToFlowScript, type WpsScript, type WpsStep } from './wpsToFlowScript';

export type AutomationRuntime =
  | { kind: 'wps_replay' }
  | { kind: 'arp_graph'; graph: ArpGraph; projection_fingerprint: string };

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
  runtime?: AutomationRuntime;
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
const SHELL_TOOLS = new Set(['run_shell', 'run_detached', 'ext.run_shell']);

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
    runtime: { kind: 'wps_replay' },
  };
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`;
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record).sort().map((key) => (
      `${JSON.stringify(key)}:${stableJson(record[key])}`
    )).join(',')}}`;
  }
  return JSON.stringify(value ?? null);
}

const SHA256_K = [
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
];

function rotateRight(value: number, bits: number): number {
  return (value >>> bits) | (value << (32 - bits));
}

function sha256Hex(input: Uint8Array): string {
  const paddedLength = Math.ceil((input.length + 9) / 64) * 64;
  const padded = new Uint8Array(paddedLength);
  padded.set(input);
  padded[input.length] = 0x80;
  const bitLength = input.length * 8;
  const view = new DataView(padded.buffer);
  view.setUint32(paddedLength - 8, Math.floor(bitLength / 0x100000000), false);
  view.setUint32(paddedLength - 4, bitLength >>> 0, false);
  const hash = [
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
    0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ];
  const words = new Uint32Array(64);
  for (let offset = 0; offset < paddedLength; offset += 64) {
    for (let i = 0; i < 16; i += 1) words[i] = view.getUint32(offset + i * 4, false);
    for (let i = 16; i < 64; i += 1) {
      const s0 = rotateRight(words[i - 15], 7) ^ rotateRight(words[i - 15], 18) ^ (words[i - 15] >>> 3);
      const s1 = rotateRight(words[i - 2], 17) ^ rotateRight(words[i - 2], 19) ^ (words[i - 2] >>> 10);
      words[i] = (words[i - 16] + s0 + words[i - 7] + s1) >>> 0;
    }
    let [a, b, c, d, e, f, g, h] = hash;
    for (let i = 0; i < 64; i += 1) {
      const s1 = rotateRight(e, 6) ^ rotateRight(e, 11) ^ rotateRight(e, 25);
      const choice = (e & f) ^ (~e & g);
      const temp1 = (h + s1 + choice + SHA256_K[i] + words[i]) >>> 0;
      const s0 = rotateRight(a, 2) ^ rotateRight(a, 13) ^ rotateRight(a, 22);
      const majority = (a & b) ^ (a & c) ^ (b & c);
      const temp2 = (s0 + majority) >>> 0;
      h = g; g = f; f = e; e = (d + temp1) >>> 0;
      d = c; c = b; b = a; a = (temp1 + temp2) >>> 0;
    }
    const block = [a, b, c, d, e, f, g, h];
    for (let i = 0; i < 8; i += 1) hash[i] = (hash[i] + block[i]) >>> 0;
  }
  return hash.map((word) => word.toString(16).padStart(8, '0')).join('');
}

function legacyAutomationProjectionFingerprint(
  steps: Array<Record<string, unknown>>,
  variables: Record<string, unknown>,
  graph?: ArpGraph,
): string {
  const bytes = new TextEncoder().encode(stableJson({ steps, variables, ...(graph ? { graph } : {}) }));
  let hash = 0x811c9dc5;
  for (const byte of bytes) hash = Math.imul(hash ^ byte, 0x01000193) >>> 0;
  return `fnv1a32:${hash.toString(16).padStart(8, '0')}`;
}

export function automationProjectionFingerprint(
  steps: Array<Record<string, unknown>>,
  variables: Record<string, unknown>,
  graph?: ArpGraph,
): string {
  const bytes = new TextEncoder().encode(stableJson({
    steps,
    variables,
    ...(graph ? { graph } : {}),
  }));
  return `sha256:${sha256Hex(bytes)}`;
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
  const graph = flowToArpGraph(script);
  const steps = (script.nodes || []).map((node) => serializeFlowStep(node, script.edges || []));
  const variables = clone(script.variables || {});
  graph.metadata = {
    ...graph.metadata,
    source: 'automation-ir',
    source_ir: id,
  };
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
    steps,
    variables,
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
    runtime: {
      kind: 'arp_graph',
      graph,
      projection_fingerprint: automationProjectionFingerprint(steps, variables, graph),
    },
  };
}

export function automationRuntimeKind(ir: AutomationIR): AutomationRuntime['kind'] | '' {
  const explicit = ir.runtime?.kind;
  if (explicit === 'wps_replay' || explicit === 'arp_graph') return explicit;
  if (ir.source?.kind === 'wps') return 'wps_replay';
  return '';
}

export function automationRuntimeGraph(ir: AutomationIR): ArpGraph {
  const error = validateAutomationRuntime(ir);
  if (error) throw new Error(error);
  if (ir.runtime?.kind !== 'arp_graph' || !ir.runtime.graph) {
    throw new Error(
      'Automation IR has no canonical ARP runtime graph; reopen the source in Studio and save it again',
    );
  }
  return clone(ir.runtime.graph);
}

export function validateAutomationRuntime(ir: AutomationIR): string | undefined {
  const error = validateAutomationIR(ir);
  if (error) return error;
  const kind = automationRuntimeKind(ir);
  if (kind === 'wps_replay') return undefined;
  if (kind !== 'arp_graph') return 'Automation IR requires a supported runtime.kind';
  const graph = ir.runtime?.kind === 'arp_graph' ? ir.runtime.graph : undefined;
  if (!graph || graph.format !== 'arp/1') return 'Automation IR arp_graph runtime requires graph format arp/1';
  if (!Array.isArray(graph.nodes) || !Array.isArray(graph.edges)) {
    return 'Automation IR arp_graph runtime requires graph nodes and edges';
  }
  const expected = str(ir.runtime?.kind === 'arp_graph' ? ir.runtime.projection_fingerprint : '');
  if (!expected) return 'Automation IR arp_graph runtime requires projection_fingerprint';
  const actual = expected.startsWith('fnv1a32:')
    ? legacyAutomationProjectionFingerprint(ir.steps, ir.variables, graph)
    : automationProjectionFingerprint(ir.steps, ir.variables, graph);
  if (expected !== actual) {
    return `Automation IR runtime projection is stale: expected ${expected}, calculated ${actual}`;
  }
  return undefined;
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
  if (!str(ir.source.kind)) return 'Automation IR source.kind is required';
  const sourceKind = str(ir.source.kind);
  if (!['wps', 'flowscript'].includes(sourceKind)) {
    return `unsupported Automation IR source.kind: ${sourceKind}`;
  }
  if (sourceKind === 'flowscript') {
    const nodeIds: string[] = [];
    for (let i = 0; i < ir.steps.length; i += 1) {
      const step = ir.steps[i] as Record<string, unknown>;
      const nodeId = str(step.node_id);
      if (!nodeId) return `steps[${i + 1}] requires node_id for flowscript source`;
      nodeIds.push(nodeId);
      if (!step.params || typeof step.params !== 'object' || Array.isArray(step.params)) {
        return `steps[${i + 1}].params must be an object`;
      }
      if (step.outgoing !== undefined && !Array.isArray(step.outgoing)) {
        return `steps[${i + 1}].outgoing must be a list`;
      }
    }
    if (new Set(nodeIds).size !== nodeIds.length) {
      return 'flowscript Automation IR node_id values must be unique';
    }
    const known = new Set(nodeIds);
    for (let i = 0; i < ir.steps.length; i += 1) {
      const outgoing = Array.isArray(ir.steps[i].outgoing) ? ir.steps[i].outgoing as Array<Record<string, unknown>> : [];
      for (let j = 0; j < outgoing.length; j += 1) {
        const target = str(outgoing[j]?.to_node);
        if (!target || !known.has(target)) {
          return `steps[${i + 1}].outgoing[${j + 1}] references unknown to_node ${target}`;
        }
      }
    }
  }
  if (ir.runtime !== undefined) {
    if (!ir.runtime || typeof ir.runtime !== 'object') return 'Automation IR runtime must be an object';
    if (!['wps_replay', 'arp_graph'].includes(str(ir.runtime.kind))) {
      return `unsupported Automation IR runtime kind: ${str(ir.runtime.kind) || '<empty>'}`;
    }
  }
  return undefined;
}
