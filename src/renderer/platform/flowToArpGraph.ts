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

function controllerActions(node: FlowNode): Array<Record<string, unknown>> | null {
  const params = node.params as Record<string, any>;
  switch (node.type) {
    case 'click': {
      const clicks = params.method === 'triple'
        ? [clickAction({ ...params, method: 'single' }), clickAction({ ...params, method: 'single' }), clickAction({ ...params, method: 'single' })]
        : [clickAction(params)];
      return withDelay(clicks, params);
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
      if (params.click_before !== false && params.x != null && params.y != null) {
        actions.push({ action: 'click', x: params.x, y: params.y });
      }
      if (params.clear_before) actions.push({ action: 'hotkey', keys: ['ctrl', 'a'] });
      actions.push({ action: 'type_text', text: String(params.text ?? '') });
      return withDelay(actions, params);
    }
    case 'keyboard':
      return withDelay([{
        action: 'hotkey',
        keys: [...new Set([...list(params.modifiers), ...list(params.keys)])],
      }], params);
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
      return params.save_path || params.output_path
        ? null
        : [{ action: 'screenshot', quality: Number(params.quality) || 85 }];
    case 'clipboard_set':
      return [{ action: 'clipboard_write', text: String(params.text ?? '') }];
    case 'clipboard_get':
      return params.into ? null : [{ action: 'clipboard_read' }];
    case 'wait':
      if (params.wait_type === 'window') {
        return [{
          action: 'wait',
          condition: { window_appears: params.window_title },
          timeout_s: Number(params.timeout) || 10,
        }];
      }
      return null;
    case 'mouse_operation': {
      const operation = String(params.sub_operation || 'click');
      if (operation === 'click' && params.mode === 'text') {
        return [{ action: 'click_element', label: params.text }];
      }
      if (operation === 'click' && params.mode === 'coordinate') return [clickAction(params)];
      if (operation === 'scroll') {
        const amount = Math.abs(Number(params.scroll_amount) || 3) * (params.scroll_direction === 'up' ? 1 : -1);
        return [{ action: 'scroll', x: params.x ?? params.move_x ?? 960, y: params.y ?? params.move_y ?? 540, amount }];
      }
      if (operation === 'move') return [{ action: 'move_mouse', x: params.move_x, y: params.move_y }];
      if (operation === 'hover') {
        return [
          { action: 'move_mouse', x: params.hover_x, y: params.hover_y },
          { action: 'wait', duration_ms: Math.round((Number(params.hover_duration) || 0.5) * 1000) },
        ];
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
      compilation: {
        native_nodes: nodes.length - legacyNodes.length,
        legacy_nodes: legacyNodes.length,
        legacy_types: [...new Set(legacyNodes.map((node) => String(node.config.node_type || 'unknown')))],
      },
    },
  };
}
