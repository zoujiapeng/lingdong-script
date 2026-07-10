import type { FlowNode, FlowScript } from '../types';
import type { ArpEdge, ArpGraph, ArpNode, ArpNodeKind } from './contracts';

const NATIVE_KINDS = new Set<ArpNodeKind>([
  'start', 'end', 'agent_goal', 'action_batch', 'skill_replay', 'evidence_gate', 'human_gate',
]);

function retryOf(params: Record<string, any>) {
  return {
    max_attempts: Math.max(1, Math.min(10, Number(params.retry_max_attempts) || 1)),
    delay_ms: Math.max(0, Math.min(60000, Number(params.retry_delay_ms) || 0)),
    backoff: 1,
  };
}

function withoutPolicyFields(params: Record<string, any>): Record<string, unknown> {
  const { retry_max_attempts: _attempts, retry_delay_ms: _delay, description: _description, ...config } = params;
  return config;
}

function conditionConfig(params: Record<string, any>): Record<string, unknown> | null {
  const variable = String(params.variable_name || '').trim();
  const value = params.variable_value;
  if (params.condition_type === 'always_true') return { source: 'inputs', operator: 'truthy' };
  if (!variable) return null;
  const operatorByType: Record<string, string> = {
    variable_equals: 'eq',
    variable_greater: 'gt',
    variable_less: 'lt',
  };
  const operator = operatorByType[params.condition_type];
  return operator ? { source: `variables.${variable}`, operator, value } : null;
}

function nodeToArp(node: FlowNode): ArpNode {
  const params = node.params as Record<string, any>;
  let kind: ArpNodeKind;
  let config: Record<string, unknown>;
  let timeout_s: number | null = null;

  if (NATIVE_KINDS.has(node.type as ArpNodeKind)) {
    kind = node.type as ArpNodeKind;
    config = withoutPolicyFields(params);
    if (kind === 'agent_goal' && params.success_criteria) {
      config.goal = `${String(params.goal || '').trim()}\n\nCompletion evidence: ${String(params.success_criteria).trim()}`;
    }
    if (kind === 'human_gate') timeout_s = Number(params.timeout_s) || 3600;
  } else if (node.type === 'wait' && params.wait_type === 'time') {
    kind = 'delay';
    config = { duration_ms: Math.max(0, Number(params.duration) || 0) * 1000 };
  } else if (node.type === 'variable_set') {
    kind = 'set_variable';
    config = { name: params.name, value: params.value };
  } else if (node.type === 'condition' && conditionConfig(params)) {
    kind = 'condition';
    config = conditionConfig(params)!;
  } else {
    kind = 'legacy_flow';
    config = { node_type: node.type, params: withoutPolicyFields(params) };
  }

  return {
    id: node.id,
    kind,
    name: node.label || node.type,
    config,
    retry: retryOf(params),
    timeout_s,
    disabled: Boolean(node.disabled),
    x: node.x,
    y: node.y,
  };
}

export function edgeBranch(fromPort: string, label?: string): string {
  const value = label || fromPort || 'next';
  if (value === 'output' || value === 'loop_end') return 'next';
  if (value === 'loop_body') return 'yes';
  return value;
}

export function flowToArpGraph(script: FlowScript): ArpGraph {
  const nodes = script.nodes.map(nodeToArp);
  const edges: ArpEdge[] = script.edges.map((edge) => ({
    id: edge.id,
    source: edge.from_node,
    target: edge.to_node,
    branch: edgeBranch(edge.from_port, edge.label),
    label: edge.label || '',
  }));
  return {
    format: 'arp/1',
    id: script.meta.id,
    name: script.meta.name || 'Untitled workflow',
    description: script.meta.description || '',
    nodes,
    edges,
    variables: { ...script.variables },
    policy: {
      max_node_visits: 100,
      fail_fast: script.settings.error_handling === 'stop',
      require_evidence_on_done: true,
      allow_shell: false,
      allow_desktop_mutation: true,
    },
    metadata: {
      source: 'flow-editor',
      source_version: script.meta.version,
      compiled_at: new Date().toISOString(),
    },
  };
}
