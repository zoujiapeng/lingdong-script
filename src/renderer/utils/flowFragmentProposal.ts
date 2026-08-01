import type { FlowEdge, FlowNode, FlowScript, NodeType } from '../types';
import { RUNNABLE_FLOW_NODE_TYPES } from '../platform/flowToArpGraph';
import {
  automationIrFromFlowScript,
  automationProjectionFingerprint,
  validateAutomationLocally,
} from './automationIR';
import { NODE_DEFINITIONS } from './nodeDefinitions';

export const FLOW_FRAGMENT_PROPOSAL_FORMAT = 'flow-fragment-proposal/1' as const;

export interface FlowFragmentNodeDraft {
  local_id: string;
  type: NodeType;
  label?: string;
  params: Record<string, unknown>;
}

export interface FlowFragmentEdgeDraft {
  source: string;
  target: string;
  source_port: string;
}

export interface FlowFragmentProposal {
  format: typeof FLOW_FRAGMENT_PROPOSAL_FORMAT;
  base_fingerprint: string;
  summary: string;
  nodes: FlowFragmentNodeDraft[];
  edges: FlowFragmentEdgeDraft[];
}

export interface FlowFragmentRequestContext {
  base_fingerprint: string;
  document: {
    node_count: number;
    edge_count: number;
    node_types: string[];
  };
  catalog: Array<{
    type: NodeType;
    label: string;
    params: Record<string, unknown>;
    output_ports: string[];
  }>;
}

// This is the small mechanical surface offered to the opt-in helper, not the
// full hand-editing catalog. Keep semantic targeting and opaque executable
// nodes out of the model request; the editor can still open old documents
// containing them.
const FLOW_FRAGMENT_PARAM_KEYS = {
  click: ['x', 'y', 'method', 'delay_after'],
  wait: ['wait_type', 'duration', 'timeout', 'window_title'],
  input_text: ['text', 'click_before', 'clear_before', 'x', 'y', 'delay_after'],
  keyboard: ['keys', 'modifiers', 'delay_after'],
  swipe: ['start_x', 'start_y', 'end_x', 'end_y', 'duration', 'delay_after'],
  condition: ['condition_type', 'variable_name', 'variable_value'],
  screenshot: [],
  variable_set: ['name', 'value'],
  launch_program: ['target', 'args'],
  focus_window: ['title'],
  clipboard_set: ['text'],
  clipboard_get: ['into'],
  mouse_operation: [
    'sub_operation', 'x', 'y', 'method', 'scroll_direction', 'scroll_amount',
    'hover_x', 'hover_y', 'hover_duration', 'move_x', 'move_y', 'delay_after',
  ],
} as const satisfies Partial<Record<NodeType, readonly string[]>>;

const INSERTABLE_NODE_TYPES = Object.keys(FLOW_FRAGMENT_PARAM_KEYS)
  .filter((type): type is NodeType => RUNNABLE_FLOW_NODE_TYPES.has(type as NodeType))
  .sort();
const INSERTABLE_NODE_TYPE_SET = new Set<NodeType>(INSERTABLE_NODE_TYPES);

function modelParams(type: NodeType): Record<string, unknown> {
  const defaults = NODE_DEFINITIONS[type].defaultParams as Record<string, unknown>;
  return Object.fromEntries(
    (FLOW_FRAGMENT_PARAM_KEYS[type as keyof typeof FLOW_FRAGMENT_PARAM_KEYS] || [])
      .map((key) => [
        key,
        Object.prototype.hasOwnProperty.call(defaults, key) ? defaults[key] : null,
      ]),
  );
}

function record(value: unknown, name: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${name} 必须是对象。`);
  }
  return value as Record<string, unknown>;
}

function text(value: unknown, name: string, maxLength = 512): string {
  if (typeof value !== 'string' || !value.trim()) throw new Error(`${name} 不能为空。`);
  const result = value.trim();
  if (result.length > maxLength) throw new Error(`${name} 过长。`);
  return result;
}

export function flowDocumentFingerprint(script: FlowScript): string {
  const automation = automationIrFromFlowScript(script, `air-${script.meta.id}`);
  const runtime = automation.runtime;
  if (!runtime || runtime.kind !== 'arp_graph') throw new Error('当前流程没有可校验的 ARP 投影。');
  const graph = JSON.parse(JSON.stringify(runtime.graph));
  if (graph.metadata && typeof graph.metadata === 'object') delete graph.metadata.compiled_at;
  return automationProjectionFingerprint(automation.steps, automation.variables, graph);
}

export function buildFlowFragmentRequestContext(script: FlowScript): FlowFragmentRequestContext {
  return {
    base_fingerprint: flowDocumentFingerprint(script),
    document: {
      node_count: script.nodes.length,
      edge_count: script.edges.length,
      node_types: [...new Set(script.nodes.map((node) => node.type))].sort(),
    },
    catalog: INSERTABLE_NODE_TYPES.map((type) => {
      const definition = NODE_DEFINITIONS[type];
      return {
        type,
        label: definition.label,
        params: modelParams(type),
        output_ports: definition.ports.outputs.map((port) => port.id),
      };
    }),
  };
}

export function normalizeFlowFragmentProposal(
  value: unknown,
  expectedFingerprint: string,
): FlowFragmentProposal {
  const proposal = record(value, '流程片段');
  if (proposal.format !== FLOW_FRAGMENT_PROPOSAL_FORMAT) throw new Error('流程片段格式不受支持。');
  if (proposal.base_fingerprint !== expectedFingerprint) {
    throw new Error('生成期间当前流程已经变化，请重新生成片段。');
  }
  const rawNodes = proposal.nodes;
  const rawEdges = proposal.edges;
  if (!Array.isArray(rawNodes) || rawNodes.length === 0 || rawNodes.length > 32) {
    throw new Error('流程片段必须包含 1 到 32 个节点。');
  }
  if (!Array.isArray(rawEdges) || rawEdges.length > 64) throw new Error('流程片段连线数量无效。');

  const ids = new Set<string>();
  const nodes = rawNodes.map((raw, index): FlowFragmentNodeDraft => {
    const node = record(raw, `节点 ${index + 1}`);
    const localId = text(node.local_id, `节点 ${index + 1} ID`, 64);
    if (ids.has(localId)) throw new Error(`流程片段包含重复节点 ID：${localId}`);
    ids.add(localId);
    const type = text(node.type, `节点 ${localId} 类型`, 64);
    if (!INSERTABLE_NODE_TYPE_SET.has(type as NodeType)) {
      throw new Error(`节点 ${localId} 使用了不可插入的类型：${type}`);
    }
    const params = record(node.params ?? {}, `节点 ${localId} 参数`);
    const allowedParams = new Set<string>(
      FLOW_FRAGMENT_PARAM_KEYS[type as keyof typeof FLOW_FRAGMENT_PARAM_KEYS] || [],
    );
    const unknownParam = Object.keys(params).find((key) => !allowedParams.has(key));
    if (unknownParam) throw new Error(`节点 ${localId} 使用了未开放参数：${unknownParam}`);
    return {
      local_id: localId,
      type: type as NodeType,
      label: typeof node.label === 'string' && node.label.trim() ? node.label.trim().slice(0, 120) : undefined,
      params,
    };
  });

  const byId = new Map(nodes.map((node) => [node.local_id, node]));
  const seenBranches = new Set<string>();
  const edges = rawEdges.map((raw, index): FlowFragmentEdgeDraft => {
    const edge = record(raw, `连线 ${index + 1}`);
    const source = text(edge.source, `连线 ${index + 1} 起点`, 64);
    const target = text(edge.target, `连线 ${index + 1} 终点`, 64);
    const sourcePort = text(edge.source_port, `连线 ${index + 1} 端口`, 64);
    const sourceNode = byId.get(source);
    if (!sourceNode || !byId.has(target)) throw new Error(`连线 ${source} → ${target} 引用了片段之外的节点。`);
    if (source === target) throw new Error(`连线 ${source} 不能指向节点自身。`);
    const allowedPorts = NODE_DEFINITIONS[sourceNode.type].ports.outputs.map((port) => port.id);
    if (!allowedPorts.includes(sourcePort)) throw new Error(`节点 ${source} 没有输出端口 ${sourcePort}。`);
    const key = `${source}\0${sourcePort}`;
    if (seenBranches.has(key)) throw new Error(`节点 ${source} 的 ${sourcePort} 分支只能连接一个目标。`);
    seenBranches.add(key);
    return { source, target, source_port: sourcePort };
  });

  const outgoing = new Map(nodes.map((node) => [node.local_id, [] as string[]]));
  const indegree = new Map(nodes.map((node) => [node.local_id, 0]));
  edges.forEach((edge) => {
    outgoing.get(edge.source)!.push(edge.target);
    indegree.set(edge.target, indegree.get(edge.target)! + 1);
  });
  const roots = nodes
    .filter((node) => indegree.get(node.local_id) === 0)
    .map((node) => node.local_id);
  let ordered = 0;
  while (roots.length) {
    const current = roots.pop()!;
    ordered += 1;
    outgoing.get(current)!.forEach((next) => {
      const remaining = indegree.get(next)! - 1;
      indegree.set(next, remaining);
      if (remaining === 0) roots.push(next);
    });
  }
  if (ordered !== nodes.length) throw new Error('流程片段不能包含循环连线。');

  if (nodes.length > 1) {
    if (edges.length === 0) throw new Error('多节点流程片段必须包含内部连线。');
    const neighbours = new Map(nodes.map((node) => [node.local_id, new Set<string>()]));
    edges.forEach((edge) => {
      neighbours.get(edge.source)!.add(edge.target);
      neighbours.get(edge.target)!.add(edge.source);
    });
    const visited = new Set<string>();
    const pending = [nodes[0].local_id];
    while (pending.length) {
      const current = pending.pop()!;
      if (visited.has(current)) continue;
      visited.add(current);
      neighbours.get(current)!.forEach((next) => pending.push(next));
    }
    if (visited.size !== nodes.length) throw new Error('流程片段包含孤立或不相连的节点。');
  }

  return {
    format: FLOW_FRAGMENT_PROPOSAL_FORMAT,
    base_fingerprint: expectedFingerprint,
    summary: typeof proposal.summary === 'string' ? proposal.summary.trim().slice(0, 1000) : '',
    nodes,
    edges,
  };
}

export function validateFlowFragmentProposal(proposal: FlowFragmentProposal, base: FlowScript): string[] {
  const errors: string[] = [];
  proposal.nodes.forEach((draft, index) => {
    const definition = NODE_DEFINITIONS[draft.type];
    const nodeId = `fragment-node-${index}`;
    const startId = `fragment-start-${index}`;
    const targetId = `fragment-target-${index}`;
    const evidenceId = `fragment-evidence-${index}`;
    const endId = `fragment-end-${index}`;
    const script: FlowScript = {
      ...JSON.parse(JSON.stringify(base)),
      meta: { ...base.meta, id: `${base.meta.id}-fragment-${index}` },
      nodes: [
        { id: startId, type: 'start', x: 0, y: 0, params: { ...NODE_DEFINITIONS.start.defaultParams }, status: 'idle' },
        // A fragment has not been spliced into its final execution path yet.
        // Supply a generic target only to validate the draft node's own
        // parameters; full-document validation still requires a real upstream
        // focus_window on every reachable spatial-action path.
        {
          id: targetId,
          type: 'focus_window',
          x: 0,
          y: 120,
          params: { ...NODE_DEFINITIONS.focus_window.defaultParams, title: 'Flow fragment validation target' },
          status: 'idle',
        },
        {
          id: nodeId,
          type: draft.type,
          label: draft.label,
          x: 0,
          y: 240,
          params: { ...definition.defaultParams, ...draft.params },
          status: 'idle',
        },
        { id: evidenceId, type: 'evidence_gate', x: 0, y: 360, params: { required: ['fragment structure'] }, status: 'idle' },
        { id: endId, type: 'end', x: 0, y: 480, params: { ...NODE_DEFINITIONS.end.defaultParams }, status: 'idle' },
      ],
      edges: [
        { id: `fragment-entry-${index}`, from_node: startId, from_port: 'output', to_node: targetId, to_port: 'input' },
        { id: `fragment-target-${index}`, from_node: targetId, from_port: 'output', to_node: nodeId, to_port: 'input' },
        ...definition.ports.outputs.map((port) => ({
          id: `fragment-exit-${index}-${port.id}`,
          from_node: nodeId,
          from_port: port.id,
          to_node: evidenceId,
          to_port: 'input',
        })),
        { id: `fragment-evidence-pass-${index}`, from_node: evidenceId, from_port: 'pass', to_node: endId, to_port: 'input' },
        { id: `fragment-evidence-fail-${index}`, from_node: evidenceId, from_port: 'fail', to_node: endId, to_port: 'input' },
      ],
    };
    try {
      const result = validateAutomationLocally(automationIrFromFlowScript(script, `fragment-${index}`));
      errors.push(...result.errors.map((error) => `${draft.label || definition.label}：${error}`));
    } catch (error) {
      errors.push(`${draft.label || definition.label}：${error instanceof Error ? error.message : String(error)}`);
    }
  });
  return [...new Set(errors)];
}

export function flowFragmentBlankOrigin(nodes: FlowNode[]): { x: number; y: number } {
  if (nodes.length === 0) return { x: 120, y: 120 };
  const right = Math.max(...nodes.map((node) => node.x + (node.type === 'start' || node.type === 'end' ? 160 : 220)));
  const top = Math.min(...nodes.map((node) => node.y));
  return { x: right + 120, y: top };
}

export function materializeFlowFragment(
  proposal: FlowFragmentProposal,
  existingNodes: FlowNode[],
  createId: () => string,
): { nodes: FlowNode[]; edges: FlowEdge[] } {
  const origin = flowFragmentBlankOrigin(existingNodes);
  const idMap = new Map<string, string>();
  const nodes = proposal.nodes.map((draft, index): FlowNode => {
    const id = createId();
    idMap.set(draft.local_id, id);
    const definition = NODE_DEFINITIONS[draft.type];
    return {
      id,
      type: draft.type,
      label: draft.label,
      x: origin.x,
      y: origin.y + index * 130,
      params: { ...definition.defaultParams, ...draft.params },
      status: 'idle',
    };
  });
  const edges = proposal.edges.map((draft): FlowEdge => ({
    id: createId(),
    from_node: idMap.get(draft.source)!,
    from_port: draft.source_port,
    to_node: idMap.get(draft.target)!,
    to_port: 'input',
  }));
  return { nodes, edges };
}
