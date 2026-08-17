import type { FlowNode, FlowScript, NodeType } from '../types';
import type { ArpEdge, ArpGraph, ArpNode, ArpNodeKind } from './contracts';

const NATIVE_KINDS = new Set<ArpNodeKind>([
  'start', 'end', 'action_batch', 'replay_step', 'skill_replay', 'evidence_gate', 'human_gate',
]);

/**
 * Node types the renderer can compile without a legacy fallback.
 *
 * This is the local editing boundary, not proof that the connected runtime
 * currently exposes the corresponding capability.
 */
export const RUNNABLE_FLOW_NODE_TYPES = new Set<NodeType>([
  'action_batch',
  'replay_step',
  'skill_replay',
  'evidence_gate',
  'human_gate',
  'click',
  'click_text',
  'wait',
  'input_text',
  'keyboard',
  'swipe',
  'condition',
  'screenshot',
  'variable_set',
  'launch_program',
  'focus_window',
  'clipboard_set',
  'clipboard_get',
  'mouse_operation',
]);

export function isRunnableFlowNodeType(type: string): type is NodeType {
  return RUNNABLE_FLOW_NODE_TYPES.has(type as NodeType);
}

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
  if (params.condition_type === 'always_true') return { source: 'inputs', operator: 'always_true' };
  if (!variable) return null;
  const operatorByType: Record<string, string> = {
    variable_equals: 'eq',
    variable_greater: 'gt',
    variable_less: 'lt',
  };
  const operator = operatorByType[params.condition_type];
  return operator ? { source: `variables.${variable}`, operator, value } : null;
}

function list(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String).filter(Boolean);
  if (typeof value === 'string') return value.split(/[+,]/).map((item) => item.trim()).filter(Boolean);
  return [];
}

function withDelay(actions: Array<Record<string, unknown>>, params: Record<string, any>) {
  const seconds = Number(params.delay_after);
  if (Number.isFinite(seconds) && seconds > 0) {
    actions.push({ action: 'wait', duration_ms: Math.round(seconds * 1000) });
  }
  return actions;
}

function clickAction(params: Record<string, any>): Record<string, unknown> {
  const method = String(params.method || 'single').toLowerCase();
  return {
    action: 'click',
    x: params.x,
    y: params.y,
    button: method === 'right' || method === 'middle' ? method : 'left',
    double: method === 'double',
  };
}

function coordinateClickActions(params: Record<string, any>): Array<Record<string, unknown>> {
  if (params.method !== 'triple') return [clickAction(params)];
  return [clickAction({ ...params, method: 'single' }), clickAction({ ...params, method: 'single' }), clickAction({ ...params, method: 'single' })];
}

function controllerActions(node: FlowNode): Array<Record<string, unknown>> | null {
  const params = node.params as Record<string, any>;
  switch (node.type) {
    case 'click': {
      return withDelay(coordinateClickActions(params), params);
    }
    case 'click_text':
      return withDelay([{
        action: 'click_element',
        label: params.text,
        fallback_text: params.fallback_text || [],
        button: params.method === 'right' || params.method === 'middle' ? params.method : 'left',
        double: params.method === 'double',
      }], params);
    case 'input_text': {
      const actions: Array<Record<string, unknown>> = [];
      const hasX = params.x != null;
      const hasY = params.y != null;
      if (hasX !== hasY || (params.click_before !== false && !(hasX && hasY))) return null;
      if (params.click_before !== false) {
        actions.push({ action: 'click', x: params.x, y: params.y });
      }
      if (params.clear_before) actions.push({ action: 'hotkey', keys: ['ctrl', 'a'] });
      actions.push({ action: 'type_text', text: String(params.text ?? '') });
      return withDelay(actions, params);
    }
    case 'keyboard': {
      const modifierNames = new Set(['ctrl', 'alt', 'shift', 'win']);
      const rawKeys = list(params.keys).map((key) => key.toLowerCase());
      const modifiers = [...new Set([
        ...list(params.modifiers).map((key) => key.toLowerCase()),
        ...rawKeys.filter((key) => modifierNames.has(key)),
      ])];
      const keys = rawKeys.filter((key) => !modifierNames.has(key));
      if (!keys.length) return null;
      return withDelay(keys.map((key) => ({
        action: 'hotkey',
        keys: [...new Set([...modifiers, key])],
      })), params);
    }
    case 'swipe':
      return withDelay([{
        action: 'drag',
        x1: params.start_x,
        y1: params.start_y,
        x2: params.end_x,
        y2: params.end_y,
        duration: Number(params.duration) || 0.4,
      }], params);
    case 'focus_window':
      return [{ action: 'focus_window', title: params.title || params.window_title }];
    case 'launch_program': {
      const target = String(params.target || params.path || params.program || '').trim();
      const args = String(params.args || '').trim();
      return [{ action: 'launch', command: `${target}${args ? ` ${args}` : ''}` }];
    }
    case 'screenshot':
      if (params.save_path || params.output_path || params.format) return null;
      if (params.region == null) {
        return [{
          action: 'screenshot',
          managed_artifact: true,
          scope: 'screen',
          purpose: 'user_output',
          sendable: true,
        }];
      }
      if (typeof params.region !== 'object' || Array.isArray(params.region)) return null;
      {
        const { x, y, w, h } = params.region as Record<string, unknown>;
        if (![x, y, w, h].every((value) => (
          typeof value === 'number' && Number.isSafeInteger(value)
        )) || Number(w) <= 0 || Number(h) <= 0) return null;
        return [{
          action: 'screenshot',
          managed_artifact: true,
          scope: 'region',
          coordinate_space: 'screen_physical_px',
          region: { x, y, width: w, height: h },
          purpose: 'user_output',
          sendable: true,
        }];
      }
    case 'clipboard_set':
      return [{ action: 'clipboard_write', text: String(params.text ?? '') }];
    case 'wait':
      if (params.wait_type === 'window') {
        const timeout = Number(params.timeout ?? 10);
        if (!Number.isFinite(timeout) || timeout <= 0 || timeout > 120) return null;
        return [{
          action: 'wait',
          condition: { window_appears: params.window_title },
          timeout_s: timeout,
        }];
      }
      return null;
    case 'mouse_operation': {
      const operation = String(params.sub_operation || 'click');
      if (operation === 'click' && params.mode === 'text') {
        return withDelay([{
          action: 'click_element',
          label: params.text,
          button: params.method === 'right' || params.method === 'middle' ? params.method : 'left',
          double: params.method === 'double',
        }], params);
      }
      if (operation === 'click' && params.mode === 'coordinate') return withDelay(coordinateClickActions(params), params);
      if (operation === 'scroll') {
        const direction = String(params.scroll_direction || 'down');
        if (direction !== 'up' && direction !== 'down') return null;
        const amount = Math.abs(Number(params.scroll_amount) || 3) * (direction === 'up' ? 1 : -1);
        return withDelay([{ action: 'scroll', x: params.x ?? 960, y: params.y ?? 540, amount }], params);
      }
      if (operation === 'move') return withDelay([{ action: 'move_mouse', x: params.move_x, y: params.move_y }], params);
      if (operation === 'hover') {
        return withDelay([
          { action: 'move_mouse', x: params.hover_x, y: params.hover_y },
          { action: 'wait', duration_ms: Math.round((Number(params.hover_duration) || 0.5) * 1000) },
        ], params);
      }
      return null;
    }
    case 'process_action': {
      if (params.action === 'focus') return [{ action: 'focus_window', title: params.title }];
      if (params.action === 'launch') {
        const target = String(params.target || '').trim();
        const args = String(params.args || '').trim();
        return [{ action: 'launch', command: `${target}${args ? ` ${args}` : ''}` }];
      }
      return null;
    }
    case 'clipboard_action': {
      if (params.action === 'set') return [{ action: 'clipboard_write', text: String(params.text ?? '') }];
      if (params.action === 'get') return [{ action: 'clipboard_read' }];
      if (params.action === 'paste') return [{ action: 'clipboard_paste' }];
      return null;
    }
    default:
      return null;
  }
}

function nodeToArp(node: FlowNode): ArpNode {
  const params = node.params as Record<string, any>;
  let kind: ArpNodeKind;
  let config: Record<string, unknown>;
  let timeout_s: number | null = null;
  const actions = controllerActions(node);

  if (node.type === 'clipboard_get') {
    const into = String(params.into || 'clipboard_text').trim() || 'clipboard_text';
    kind = 'replay_step';
    config = { step: { tool: 'ext.clipboard_get', into } };
  } else if (NATIVE_KINDS.has(node.type as ArpNodeKind)) {
    kind = node.type as ArpNodeKind;
    config = withoutPolicyFields(params);
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
  } else if (actions) {
    kind = 'action_batch';
    config = {
      actions,
      subgoal: node.label || String(params.description || node.type),
      stop_on_error: true,
    };
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
  const legacyNodes = nodes.filter((node) => node.kind === 'legacy_flow');
  const replayNodes = nodes.filter((node) => node.kind === 'replay_step');
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
    name: script.meta.name || '未命名工作流',
    description: script.meta.description || '',
    nodes,
    edges,
    variables: { ...script.variables },
    policy: {
      max_node_visits: 100,
      fail_fast: script.settings.error_handling === 'stop',
      require_evidence_on_done: true,
      allow_shell: replayNodes.some((node) => node.config.allow_shell === true),
      allow_desktop_mutation: true,
    },
    metadata: {
      source: 'flow-editor',
      source_version: script.meta.version,
      compiled_at: new Date().toISOString(),
      compilation: {
        native_nodes: nodes.length - legacyNodes.length,
        replay_nodes: replayNodes.length,
        legacy_nodes: legacyNodes.length,
        legacy_types: [...new Set(legacyNodes.map((node) => String(node.config.node_type || 'unknown')))],
      },
    },
  };
}
