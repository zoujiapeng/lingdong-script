import { nanoid } from 'nanoid';
import { FlowScript, FlowNode, FlowEdge, NodeType, NodeParams } from '../types';

/**
 * Convert a WinPilot agent/recording `.wps.json` into a native FlowScript made
 * of the EXISTING 65 node types, so it loads in the mature flow editor and runs
 * on the existing engine, freely mixable with the instruction-library nodes.
 *
 * This is a pure code layer — the agent never sees the flow-chart format (that
 * would bloat it). Semantic clicks map to `click_text` (OCR), `ctrl.if`→
 * `condition` (yes/no ports), `ctrl.loop`→`loop` (loop_body/loop_end), etc.
 * The mapping is intentionally best-effort; the mature PropertyPanel lets the
 * user refine any node afterward.
 */

export interface WpsStep {
  tool: string;
  [k: string]: unknown;
}
export interface WpsScript {
  format?: string;
  name?: string;
  task?: string;
  vars?: Record<string, string>;
  steps: WpsStep[];
}

const X_STEP = 280;
const Y_STEP = 96;

interface Frontier { node: string; port: string }
interface Layout { entry: string | null; exits: Frontier[] }
interface Ctx { nodes: FlowNode[]; edges: FlowEdge[]; row: number }

function num(v: unknown, dflt = 0): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : dflt;
}
function str(v: unknown): string {
  return v == null ? '' : String(v);
}

function clickMethod(step: WpsStep): string {
  if (step.double) return 'double';
  if (step.button === 'right') return 'right';
  return 'single';
}

/** Map one leaf wps step → { type, params } for a native node. */
function leafNode(step: WpsStep): { type: NodeType; params: NodeParams } {
  const t = step.tool;
  switch (t) {
    case 'click': {
      const text = str(step.text).trim();
      const coords = Array.isArray(step.coords) ? (step.coords as number[]) : [0, 0];
      if (text) {
        return { type: 'click_text', params: { text, similarity: 80, method: clickMethod(step), timeout: 5, delay_after: 0.5 } };
      }
      return { type: 'click', params: { x: coords[0], y: coords[1], offsetX: 0, offsetY: 0, method: clickMethod(step), delay_after: 0.5 } };
    }
    case 'type_text':
      return { type: 'input_text', params: { text: str(step.text_input), click_before: true, clear_before: false, delay_after: 0.3 } };
    case 'hotkey':
      return { type: 'keyboard', params: { keys: Array.isArray(step.keys) ? step.keys : [], modifiers: [], delay_after: 0.2 } };
    case 'launch':
      return { type: 'launch_program', params: { target: str(step.command), args: '', wait: false } };
    case 'focus_window':
      return { type: 'focus_window', params: { title: str(step.title), exact: false } };
    case 'scroll': {
      const c = Array.isArray(step.coords) ? (step.coords as number[]) : [0, 0];
      return { type: 'mouse_operation', params: { sub_operation: 'scroll', x: c[0], y: c[1], amount: num(step.amount), delay_after: 0.3 } };
    }
    case 'drag': {
      const f = Array.isArray(step.from) ? (step.from as number[]) : [0, 0];
      const to = Array.isArray(step.to) ? (step.to as number[]) : [0, 0];
      return { type: 'swipe', params: { start_x: f[0], start_y: f[1], end_x: to[0], end_y: to[1], duration: num(step.duration, 0.4), delay_after: 0.3 } };
    }
    case 'tray_click':
      return { type: 'tool_call', params: { feature_name: 'tray_click', params: { name: str(step.name), double: !!step.double } } };
    case 'wait':
      return { type: 'wait', params: { wait_type: 'time', duration: num(step.timeout_s, 1), timeout: 10 } };
    case 'run_shell':
    case 'ext.run_shell':
      return { type: 'tool_call', params: { feature_name: 'run_shell', params: { command: str(step.command), shell: str(step.shell || 'powershell') } } };
    case 'ext.delay':
      return { type: 'wait', params: { wait_type: 'time', duration: num(step.seconds, 1) } };
    case 'ext.var_set':
      return { type: 'variable_set', params: { name: str(step.name), value: str(step.value) } };
    case 'ext.clipboard_set':
      return { type: 'clipboard_set', params: { text: str(step.text) } };
    case 'ext.clipboard_get':
      return { type: 'tool_call', params: { feature_name: 'clipboard_get', params: {} } };
    case 'ext.screenshot':
      return { type: 'screenshot', params: { format: 'png' } };
    default:
      // Unknown/unsupported tool → a tool_call placeholder so nothing is lost.
      return { type: 'tool_call', params: { feature_name: t, params: {} } };
  }
}

/** condition_type best-effort from a wps verify cond. */
function condParams(cond: Record<string, unknown> | undefined): NodeParams {
  const c = cond || {};
  if ('window_appears' in c || 'window_gone' in c) return { condition_type: 'window_exists', similarity: 85, _wps_cond: c };
  if ('color_match' in c) return { condition_type: 'color_match', similarity: 85, _wps_cond: c };
  return { condition_type: 'image_exists', similarity: 85, _wps_cond: c };
}

export function wpsToFlowScript(script: WpsScript): FlowScript {
  const ctx: Ctx = { nodes: [], edges: [], row: 0 };

  const add = (type: NodeType, params: NodeParams, depth: number): FlowNode => {
    const node: FlowNode = { id: nanoid(8), type, x: depth * X_STEP, y: ctx.row++ * Y_STEP, params };
    ctx.nodes.push(node);
    return node;
  };
  const link = (from: string, fromPort: string, to: string): void => {
    ctx.edges.push({ id: nanoid(8), from_node: from, from_port: fromPort, to_node: to, to_port: 'input' });
  };

  function layout(steps: WpsStep[], depth: number): Layout {
    let entry: string | null = null;
    let exits: Frontier[] = [];
    const attach = (toNode: string) => {
      if (!entry) entry = toNode;
      exits.forEach((e) => link(e.node, e.port, toNode));
    };

    for (const step of steps) {
      if (step.tool === 'ctrl.if') {
        const node = add('condition', condParams(step.cond as Record<string, unknown>), depth);
        attach(node.id);
        const thenL = layout((step.then as WpsStep[]) || [], depth + 1);
        if (thenL.entry) link(node.id, 'yes', thenL.entry);
        const thenExits = thenL.entry ? thenL.exits : [{ node: node.id, port: 'yes' }];
        const elseSteps = (step.else as WpsStep[]) || [];
        let elseExits: Frontier[];
        if (elseSteps.length) {
          const elseL = layout(elseSteps, depth + 1);
          if (elseL.entry) link(node.id, 'no', elseL.entry);
          elseExits = elseL.entry ? elseL.exits : [{ node: node.id, port: 'no' }];
        } else {
          elseExits = [{ node: node.id, port: 'no' }];
        }
        exits = [...thenExits, ...elseExits];
        continue;
      }
      if (step.tool === 'ctrl.loop') {
        const lt = str(step.loop_type) || 'count';
        const node = add('loop', { loop_type: lt, count: num(step.count, 1) }, depth);
        attach(node.id);
        const bodyL = layout((step.body as WpsStep[]) || [], depth + 1);
        if (bodyL.entry) {
          link(node.id, 'loop_body', bodyL.entry);
          bodyL.exits.forEach((e) => link(e.node, e.port, node.id)); // back-edge
        }
        exits = [{ node: node.id, port: 'loop_end' }];
        continue;
      }
      const { type, params } = leafNode(step);
      const node = add(type, params, depth);
      attach(node.id);
      exits = [{ node: node.id, port: 'output' }];
    }
    return { entry, exits };
  }

  const start = add('start', { label: '开始' }, 0);
  const body = layout(script.steps || [], 0);
  if (body.entry) link(start.id, 'output', body.entry);
  const end = add('end', { label: '结束' }, 0);
  const tails = body.exits.length ? body.exits : [{ node: start.id, port: 'output' }];
  tails.forEach((tail) => link(tail.node, tail.port, end.id));

  const now = new Date().toISOString();
  const name = script.name || script.task || '导入的流程';
  return {
    meta: {
      id: nanoid(10),
      name,
      description: script.task || '',
      created_at: now,
      updated_at: now,
      version: '1.0.0',
      source: 'flow',
    },
    nodes: ctx.nodes,
    edges: ctx.edges,
    variables: script.vars || {},
    settings: {
      default_delay: 0.3,
      error_handling: 'stop',
      max_retries: 2,
      retry_delay: 1,
      log_level: 'info',
    },
  };
}
