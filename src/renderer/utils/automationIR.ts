import { nanoid } from 'nanoid';
import type { FlowScript, FlowNode, FlowEdge } from '../types';
import { flowToArpGraph } from '../platform/flowToArpGraph';
import type { ArpGraph, GraphValidation } from '../platform/contracts';
import { NODE_DEFINITIONS } from './nodeDefinitions';
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
  runtime?: AutomationRuntime;
}

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

function record(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

function flowParams(value: unknown): Record<string, unknown> {
  const params = clone(record(value) || {});
  delete params.allow_shell;
  delete params.preflight_contract;
  delete params.postflight_evidence;
  return params;
}

function flowSource(value: FlowScript['source'] | undefined): FlowScript['source'] | undefined {
  if (!value) return undefined;
  const source = clone(value);
  if (source.kind === 'wps' && record(source.raw)) {
    delete source.raw.safety;
    delete source.raw.fallback;
  }
  return source;
}

function preservedWpsMetadata(script: FlowScript): Record<string, unknown> | undefined {
  return script.source?.kind === 'wps' ? record(script.source.raw) : undefined;
}

function canonicalEvidence(value: unknown): Record<string, unknown> {
  if (Array.isArray(value)) return { required: clone(value) };
  return clone(record(value) || { required: [] });
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
  const source: Record<string, unknown> = {
    kind: 'wps',
    format: script.format || 'wps/2',
    source: script.source || '',
    source_run: script.source_run || '',
    task,
  };
  if (record(script.export_manifest)) source.export_manifest = clone(script.export_manifest);
  if (Array.isArray(script.source_runs)) source.source_runs = clone(script.source_runs);

  return {
    format: 'automation-ir/1',
    id,
    name: str(script.name) || task || '自动化任务',
    created: created(anyScript.created),
    source,
    conditions,
    evidence: Array.isArray(anyScript.evidence) ? { required: clone(anyScript.evidence) } : clone(anyScript.evidence || { required: [] }),
    steps: steps as Array<Record<string, unknown>>,
    variables: clone(script.vars || {}),
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
    ...(node.disabled !== undefined ? { disabled: Boolean(node.disabled) } : {}),
    params: flowParams(node.params),
    outgoing: edges
      .filter((edge) => edge.from_node === node.id)
      .map((edge) => ({
        edge_id: edge.id,
        from_port: edge.from_port,
        to_node: edge.to_node,
        to_port: edge.to_port,
        label: edge.label || '',
      })),
  };
}

export function automationIrFromFlowScript(script: FlowScript, id = `air-${nanoid(12)}`): AutomationIR {
  const graph = flowToArpGraph(script);
  const steps = (script.nodes || []).map((node) => serializeFlowStep(node, script.edges || []));
  const variables = clone(script.variables || {});
  const wps = preservedWpsMetadata(script);
  const conditions = clone(record(wps?.conditions) || {});
  const targetWindow = wps?.target_window ?? wps?.target;
  if (targetWindow !== undefined && conditions.target_window === undefined) {
    conditions.target_window = clone(targetWindow);
  }
  graph.metadata = {
    ...graph.metadata,
    source: 'automation-ir',
    source_ir: id,
  };
  return {
    format: 'automation-ir/1',
    id,
    name: str(script.meta?.name) || '流程自动化',
    created: nowSeconds(),
    source: {
      kind: 'flowscript',
      format: script.meta?.version || '1.0.0',
      flow_id: script.meta?.id || '',
      meta: clone(script.meta || {}),
      settings: clone(script.settings || {}),
      triggers: clone(script.triggers || []),
      ...(script.source ? { raw: flowSource(script.source) } : {}),
    },
    conditions,
    evidence: canonicalEvidence(wps?.evidence),
    steps,
    variables,
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
      'Automation IR 没有规范的 ARP 运行图；请在流程工作台中重新打开来源并再次保存',
    );
  }
  return clone(ir.runtime.graph);
}

export function validateAutomationRuntime(ir: AutomationIR): string | undefined {
  const error = validateAutomationIR(ir);
  if (error) return error;
  const kind = automationRuntimeKind(ir);
  if (kind === 'wps_replay') return undefined;
  if (kind !== 'arp_graph') return 'Automation IR 需要受支持的 runtime.kind';
  const graph = ir.runtime?.kind === 'arp_graph' ? ir.runtime.graph : undefined;
  if (!graph || graph.format !== 'arp/1') return 'Automation IR 的 arp_graph 运行时要求 graph 格式为 arp/1';
  if (!Array.isArray(graph.nodes) || !Array.isArray(graph.edges)) {
    return 'Automation IR 的 arp_graph 运行时必须包含 graph.nodes 和 graph.edges';
  }
  const expected = str(ir.runtime?.kind === 'arp_graph' ? ir.runtime.projection_fingerprint : '');
  if (!expected) return 'Automation IR 的 arp_graph 运行时必须包含 projection_fingerprint';
  const actual = expected.startsWith('fnv1a32:')
    ? legacyAutomationProjectionFingerprint(ir.steps, ir.variables, graph)
    : automationProjectionFingerprint(ir.steps, ir.variables, graph);
  if (expected !== actual) {
    return `Automation IR 运行时投影已过期：期望 ${expected}，实际计算为 ${actual}`;
  }
  return undefined;
}

function parseLocalControllerAction(value: unknown): { name: string; inputs: Record<string, unknown> } | undefined {
  const action = record(value);
  if (!action) return undefined;
  const explicitName = str(action.action || action.tool || action.name).toLowerCase().replace(/-/g, '_');
  let name = explicitName;
  let inputs: Record<string, unknown> = {
    ...(record(action.params) || {}),
    ...Object.fromEntries(Object.entries(action).filter(([key]) => !['action', 'tool', 'name', 'params'].includes(key))),
  };
  if (!name && Object.keys(action).length === 1) {
    const [shorthand, rawInputs] = Object.entries(action)[0];
    name = str(shorthand).toLowerCase().replace(/-/g, '_');
    if (record(rawInputs)) inputs = record(rawInputs)!;
    else if (name === 'hotkey') inputs = { keys: Array.isArray(rawInputs) ? rawInputs : [rawInputs] };
    else if (name === 'type_text' || name === 'type_into_element') inputs = { text: String(rawInputs ?? '') };
    else inputs = { value: rawInputs };
  }
  return name ? { name, inputs } : undefined;
}

function localControllerActionError(value: unknown): string | undefined {
  if (!record(value)) return '必须是对象';
  const parsed = parseLocalControllerAction(value);
  if (!parsed) return '缺少 action/tool 名称';
  const { name, inputs } = parsed;

  const present = (key: string) => inputs[key] !== undefined && inputs[key] !== null;
  const nonEmpty = (key: string) => str(inputs[key]).length > 0;
  if (name === 'click' && !(
    (present('x') && present('y'))
    || ['element_id', 'label', 'text_query', 'anchor'].some(nonEmpty)
    || (Array.isArray(inputs.fallback_text) && inputs.fallback_text.length > 0)
  )) return 'click 缺少坐标或视觉锚点';
  if (name === 'click_element' && !(
    ['label', 'text_query', 'anchor'].some(nonEmpty)
    || (Array.isArray(inputs.fallback_text) && inputs.fallback_text.length > 0)
  )) return 'click_element 缺少文本或视觉锚点';
  if (['type_text', 'type_into_element', 'clipboard_write'].includes(name) && !present('text')) {
    return `${name} 缺少 text`;
  }
  if (['type_text', 'type_into_element'].includes(name) && !String(inputs.text ?? '').length) {
    return `${name} 的 text 不能为空`;
  }
  if (name === 'hotkey' && !(Array.isArray(inputs.keys) ? inputs.keys.length : nonEmpty('value'))) {
    return 'hotkey 缺少按键';
  }
  if (['scroll', 'move_mouse'].includes(name) && !(present('x') && present('y'))) {
    return `${name} 缺少 x/y 坐标`;
  }
  if (name === 'scroll' && !present('amount')) return 'scroll 缺少 amount';
  if (name === 'drag' && !['x1', 'y1', 'x2', 'y2'].every(present)) return 'drag 缺少起止坐标';
  if (name === 'focus_window' && !(nonEmpty('title') || present('hwnd'))) return 'focus_window 缺少窗口标题或 hwnd';
  if (name === 'launch' && !nonEmpty('command')) return 'launch 缺少 command';
  if (name === 'wait' && !(
    ['duration_s', 'seconds', 'time', 'duration_ms', 'ms', 'value'].some((key) => typeof inputs[key] === 'number')
    || (record(inputs.condition) && Object.values(record(inputs.condition)!).some((item) => item !== undefined && item !== null && item !== ''))
  )) return 'wait 缺少时长或条件';
  return undefined;
}

/** Fast, network-free shape checking for readable editor errors. */
export function validateAutomationLocally(ir: AutomationIR): GraphValidation {
  const runtimeError = validateAutomationRuntime(ir);
  if (runtimeError) {
    return { valid: false, errors: [runtimeError], warnings: [], entry_node: null, reachable_nodes: [] };
  }
  if (automationRuntimeKind(ir) === 'wps_replay') {
    return {
      valid: true,
      errors: [],
      warnings: ['本地格式检查已通过；重放会按脚本中的动作直接执行。'],
      entry_node: null,
      reachable_nodes: [],
    };
  }

  const graph = ir.runtime?.kind === 'arp_graph' ? ir.runtime.graph : undefined;
  if (!graph) {
    return { valid: false, errors: ['流程缺少 ARP 运行图。'], warnings: [], entry_node: null, reachable_nodes: [] };
  }

  const errors: string[] = [];
  const warnings: string[] = [];
  const nodeIds = graph.nodes.map((node) => node.id.trim());
  const edgeIds = graph.edges.map((edge) => edge.id.trim());
  if (nodeIds.some((id) => !id)) errors.push('每个节点都必须有非空 ID。');
  if (new Set(nodeIds).size !== nodeIds.length) errors.push('节点 ID 必须唯一。');
  if (edgeIds.some((id) => !id)) errors.push('每条连线都必须有非空 ID。');
  if (new Set(edgeIds).size !== edgeIds.length) errors.push('连线 ID 必须唯一。');

  const nodeMap = new Map(graph.nodes.map((node) => [node.id, node]));
  const starts = graph.nodes.filter((node) => node.kind === 'start').map((node) => node.id);
  const ends = graph.nodes.filter((node) => node.kind === 'end').map((node) => node.id);
  if (starts.length !== 1) errors.push('流程必须恰好有一个开始节点。');
  if (ends.length === 0) errors.push('流程必须至少有一个结束节点。');

  for (const node of graph.nodes) {
    if (node.disabled) continue;
    if (node.kind === 'legacy_flow') {
      errors.push(`节点 ${node.id} 的参数分支只能走旧执行器，不能由当前 ARP Runtime 执行。`);
    } else if (node.kind === 'action_batch') {
      const actions = node.config.actions;
      if (!Array.isArray(actions) || actions.length === 0) {
        errors.push(`动作批次节点 ${node.id} 必须包含至少一个动作。`);
      } else {
        actions.forEach((action, index) => {
          const error = localControllerActionError(action);
          if (error) errors.push(`动作批次节点 ${node.id} 的第 ${index + 1} 个动作无效：${error}。`);
        });
      }
    } else if (node.kind === 'replay_step') {
      const step = node.config.step;
      if (!step || typeof step !== 'object' || Array.isArray(step)) {
        errors.push(`复现步骤节点 ${node.id} 必须包含 WPS step 对象。`);
      } else if (!String((step as Record<string, unknown>).tool || '').trim()) {
        errors.push(`复现步骤节点 ${node.id} 的 WPS step 必须指定 tool。`);
      }
    } else if (node.kind === 'skill_replay') {
      if (!['script', 'task', 'memory_id'].some((key) => String(node.config[key] || '').trim())) {
        errors.push(`技能复用节点 ${node.id} 必须指定脚本、任务或 memory ID。`);
      }
    } else if (node.kind === 'evidence_gate') {
      const required = node.config.required;
      if (!Array.isArray(required) || required.length === 0) {
        errors.push(`证据门节点 ${node.id} 必须包含至少一项具体证据。`);
      }
    } else if (node.kind === 'set_variable' && !String(node.config.name || '').trim()) {
      errors.push(`设置变量节点 ${node.id} 必须指定变量名。`);
    }
  }

  for (const edge of graph.edges) {
    if (!nodeMap.has(edge.source)) errors.push(`连线 ${edge.id} 引用了不存在的起点 ${edge.source}。`);
    if (!nodeMap.has(edge.target)) errors.push(`连线 ${edge.id} 引用了不存在的终点 ${edge.target}。`);
  }
  const routeCounts = new Map<string, number>();
  for (const edge of graph.edges) {
    const key = `${edge.source}\u0000${edge.branch}`;
    routeCounts.set(key, (routeCounts.get(key) || 0) + 1);
  }
  for (const [key, count] of routeCounts) {
    if (count <= 1) continue;
    const [source, branch] = key.split('\u0000');
    errors.push(`节点 ${source} 的 ${branch} 分支存在多条路由。`);
  }

  const reachable: string[] = [];
  if (starts.length === 1 && errors.length === 0) {
    const outgoing = new Map<string, typeof graph.edges>();
    for (const node of graph.nodes) outgoing.set(node.id, []);
    for (const edge of graph.edges) outgoing.get(edge.source)?.push(edge);
    const pending = [starts[0]];
    const seen = new Set<string>();
    while (pending.length) {
      const nodeId = pending.shift()!;
      if (seen.has(nodeId)) continue;
      seen.add(nodeId);
      reachable.push(nodeId);
      for (const edge of outgoing.get(nodeId) || []) pending.push(edge.target);
    }
    const unreachable = graph.nodes.map((node) => node.id).filter((id) => !seen.has(id));
    if (unreachable.length) warnings.push(`不可达节点：${unreachable.join('、')}。`);
    if (!ends.some((id) => seen.has(id))) errors.push('从开始节点无法到达任何结束节点。');

    for (const nodeId of reachable) {
      const node = nodeMap.get(nodeId)!;
      const routes = outgoing.get(nodeId) || [];
      if (node.kind !== 'end' && routes.length === 0) errors.push(`可达节点 ${nodeId} 没有后续路由。`);
      if (node.kind === 'condition') {
        const branches = new Set(routes.map((edge) => edge.branch));
        const missing = ['yes', 'no'].filter((branch) => !branches.has(branch));
        if (missing.length) errors.push(`条件节点 ${nodeId} 缺少 ${missing.join('/')} 分支。`);
      }
      if (node.kind === 'skill_replay' && !String(node.config.script || '').trim()) {
        const branches = new Set(routes.map((edge) => edge.branch));
        if (!branches.has('miss')) errors.push(`记忆技能节点 ${nodeId} 缺少 miss 回退路由。`);
      }
    }

  }

  return {
    valid: errors.length === 0,
    errors,
    warnings,
    entry_node: starts.length === 1 ? starts[0] : null,
    reachable_nodes: reachable,
  };
}

export function automationIrToWps(ir: AutomationIR): WpsScript {
  if (ir.format !== 'automation-ir/1') throw new Error('不支持的 Automation IR 格式');
  const source = ir.source || {};
  if (source.kind !== 'wps') throw new Error('只有来源为 WPS 的 Automation IR 才能转换为 WPS');
  const conditions = clone(ir.conditions || {}) as Record<string, unknown>;
  const evidence = ir.evidence;
  const script: WpsScript = {
    format: 'wps/2',
    name: str(ir.name) || '自动化任务',
    task: str(source.task),
    source: 'automation-ir',
    source_ir: str(ir.id),
    source_run: str(source.source_run),
    created: created(ir.created),
    vars: clone(ir.variables || {}),
    steps: clone(ir.steps || []) as WpsStep[],
  };
  if (conditions.target_window !== undefined && conditions.target_window !== null && conditions.target_window !== '') {
    script.target_window = clone(conditions.target_window);
  }
  if (Object.keys(conditions).length > 0) {
    script.conditions = conditions;
  }
  const exportManifest = record(source.export_manifest);
  if (exportManifest) script.export_manifest = clone(exportManifest);
  if (Array.isArray(source.source_runs)) script.source_runs = clone(source.source_runs);
  const evidenceRecord = record(evidence);
  if (
    evidenceRecord
    && Object.keys(evidenceRecord).length === 1
    && Array.isArray(evidenceRecord.required)
  ) {
    script.evidence = clone(evidenceRecord.required as unknown[]);
  } else if (Array.isArray(evidence)) {
    script.evidence = clone(evidence);
  } else if (evidenceRecord && Object.keys(evidenceRecord).length > 0) {
    script.evidence = clone(evidenceRecord);
  }
  return script;
}

export function automationIrToFlowScript(ir: AutomationIR): FlowScript {
  const error = validateAutomationIR(ir);
  if (error) throw new Error(error);
  const source = ir.source || {};
  if (source.kind === 'wps') {
    const flow = wpsToFlowScript(automationIrToWps(ir));
    flow.source = {
      kind: 'wps',
      raw: {
        ...(flow.source?.raw || {}),
        conditions: clone(ir.conditions),
        evidence: clone(ir.evidence),
      },
    };
    return assertEditableFlowScript(flow);
  }
  if (source.kind !== 'flowscript') {
    throw new Error(`不支持的 Automation IR source.kind：${String(source.kind || '')}`);
  }
  const nodes: FlowNode[] = (ir.steps || []).map((raw, index) => {
    const step = raw as Record<string, any>;
    return {
      id: str(step.node_id) || `ir-node-${index + 1}`,
      type: str(step.tool) as FlowNode['type'],
      x: Number.isFinite(Number(step.x)) ? Number(step.x) : 80,
      y: Number.isFinite(Number(step.y)) ? Number(step.y) : index * 112,
      params: flowParams(step.params),
      ...(str(step.label) ? { label: str(step.label) } : {}),
      ...(step.disabled !== undefined ? { disabled: Boolean(step.disabled) } : {}),
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
        ...(str(outgoing.label) ? { label: str(outgoing.label) } : {}),
      });
    }
  }
  const meta = clone((source.meta as FlowScript['meta']) || {}) as FlowScript['meta'];
  const now = new Date(created(ir.created) * 1000).toISOString();
  const settings = clone((source.settings as FlowScript['settings']) || {}) as FlowScript['settings'];
  const rawSource = record(source.raw);
  return assertEditableFlowScript({
    meta: {
      ...meta,
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
      ...settings,
      default_delay: Number(settings.default_delay ?? 0),
      error_handling: settings.error_handling || 'stop',
      max_retries: Number(settings.max_retries ?? 2),
      retry_delay: Number(settings.retry_delay ?? 1),
      log_level: settings.log_level || 'info',
      ...(settings.require_real_features !== undefined
        ? { require_real_features: settings.require_real_features }
        : {}),
    },
    ...(Array.isArray(source.triggers) ? { triggers: clone(source.triggers) as FlowScript['triggers'] } : {}),
    ...(rawSource && str(rawSource.kind) && record(rawSource.raw)
      ? { source: flowSource(clone(rawSource) as unknown as FlowScript['source']) }
      : {}),
  });
}

function finiteCoordinate(value: unknown): number | undefined {
  if (typeof value === 'string' && !value.trim()) return undefined;
  if (typeof value !== 'number' && typeof value !== 'string') return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

export function assertEditableFlowScript(value: unknown): FlowScript {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('流程文档必须是 JSON 对象。');
  }
  const script = clone(value) as FlowScript;
  if (!script.meta || typeof script.meta !== 'object') throw new Error('流程文档缺少 meta。');
  if (!Array.isArray(script.nodes) || !Array.isArray(script.edges)) {
    throw new Error('流程文档必须包含 nodes 和 edges 数组。');
  }
  const ids = new Set<string>();
  for (const [index, node] of script.nodes.entries()) {
    if (!node || typeof node !== 'object') throw new Error(`nodes[${index + 1}] 必须是对象。`);
    const id = str(node.id);
    if (!id) throw new Error(`nodes[${index + 1}] 缺少 id。`);
    if (ids.has(id)) throw new Error(`节点 ID 重复：${id}。`);
    ids.add(id);
    node.id = id;
    if (!NODE_DEFINITIONS[node.type]) throw new Error(`不支持的节点类型：${String(node.type || '')}。`);
    if (!node.params || typeof node.params !== 'object' || Array.isArray(node.params)) {
      throw new Error(`节点 ${id} 的 params 必须是对象。`);
    }
    node.params = flowParams(node.params) as FlowNode['params'];
    const x = finiteCoordinate(node.x);
    const y = finiteCoordinate(node.y);
    if (x === undefined || y === undefined) {
      throw new Error(`节点 ${id} 缺少有效坐标。`);
    }
    node.x = x;
    node.y = y;
  }
  const edgeIds = new Set<string>();
  for (const [index, edge] of script.edges.entries()) {
    if (!edge || typeof edge !== 'object') throw new Error(`edges[${index + 1}] 必须是对象。`);
    const id = str(edge.id);
    if (!id) throw new Error(`edges[${index + 1}] 缺少 id。`);
    if (edgeIds.has(id)) throw new Error(`连线 ID 重复：${id}。`);
    edgeIds.add(id);
    edge.id = id;
    edge.from_node = str(edge.from_node);
    edge.to_node = str(edge.to_node);
    edge.from_port = str(edge.from_port) || 'output';
    edge.to_port = str(edge.to_port) || 'input';
    if (!ids.has(edge.from_node) || !ids.has(edge.to_node)) {
      throw new Error(`连线 ${id} 引用了不存在的节点。`);
    }
  }
  script.source = flowSource(script.source);
  return script;
}

export function automationDocumentToFlowScript(value: unknown): FlowScript {
  const document = value as Record<string, any>;
  if (!document || typeof document !== 'object' || Array.isArray(document)) {
    throw new Error('自动化文档必须是 JSON 对象');
  }
  if (document.format === 'automation-ir/1') {
    return automationIrToFlowScript(document as AutomationIR);
  }
  if (String(document.format || '').startsWith('wps/')) {
    return assertEditableFlowScript(wpsToFlowScript(document as WpsScript));
  }
  if (document.meta && Array.isArray(document.nodes) && Array.isArray(document.edges)) {
    return assertEditableFlowScript(document);
  }
  throw new Error(`不支持的自动化文档：${String(document.format || '未知格式')}`);
}

export function validateAutomationIR(value: unknown): string | undefined {
  const ir = value as AutomationIR;
  if (!ir || typeof ir !== 'object') return 'Automation IR 必须是对象';
  if (ir.format !== 'automation-ir/1') return `未知的 Automation IR 格式：${(ir as any).format}`;
  if (!str(ir.id)) return 'Automation IR 缺少 id';
  if (!str(ir.name)) return 'Automation IR 缺少 name';
  for (const key of ['source', 'conditions', 'evidence'] as const) {
    if (!ir[key] || typeof ir[key] !== 'object' || Array.isArray(ir[key])) return `${key} 必须是对象`;
  }
  if (!ir.variables || typeof ir.variables !== 'object' || Array.isArray(ir.variables)) return 'variables 必须是对象';
  if (!Array.isArray(ir.steps)) return 'steps 必须是列表';
  for (let i = 0; i < ir.steps.length; i += 1) {
    const step = ir.steps[i] as Record<string, unknown>;
    if (!step || typeof step !== 'object' || Array.isArray(step)) return `steps[${i + 1}] 必须是对象`;
    if (!str(step.tool)) return `steps[${i + 1}] 缺少 tool`;
  }
  if (!str(ir.source.kind)) return 'Automation IR 缺少 source.kind';
  const sourceKind = str(ir.source.kind);
  if (!['wps', 'flowscript'].includes(sourceKind)) {
    return `不支持的 Automation IR source.kind：${sourceKind}`;
  }
  if (sourceKind === 'flowscript') {
    const nodeIds: string[] = [];
    for (let i = 0; i < ir.steps.length; i += 1) {
      const step = ir.steps[i] as Record<string, unknown>;
      const nodeId = str(step.node_id);
      if (!nodeId) return `steps[${i + 1}] 缺少 flowscript 来源所需的 node_id`;
      nodeIds.push(nodeId);
      if (!step.params || typeof step.params !== 'object' || Array.isArray(step.params)) {
        return `steps[${i + 1}].params 必须是对象`;
      }
      if (step.outgoing !== undefined && !Array.isArray(step.outgoing)) {
        return `steps[${i + 1}].outgoing 必须是列表`;
      }
    }
    if (new Set(nodeIds).size !== nodeIds.length) {
      return 'flowscript Automation IR 的 node_id 值必须唯一';
    }
    const known = new Set(nodeIds);
    for (let i = 0; i < ir.steps.length; i += 1) {
      const outgoing = Array.isArray(ir.steps[i].outgoing) ? ir.steps[i].outgoing as Array<Record<string, unknown>> : [];
      for (let j = 0; j < outgoing.length; j += 1) {
        const target = str(outgoing[j]?.to_node);
        if (!target || !known.has(target)) {
          return `steps[${i + 1}].outgoing[${j + 1}] 引用了未知的 to_node ${target}`;
        }
      }
    }
  }
  if (ir.runtime !== undefined) {
    if (!ir.runtime || typeof ir.runtime !== 'object') return 'Automation IR runtime 必须是对象';
    if (!['wps_replay', 'arp_graph'].includes(str(ir.runtime.kind))) {
      return `不支持的 Automation IR runtime.kind：${str(ir.runtime.kind) || '<空>'}`;
    }
  }
  return undefined;
}
