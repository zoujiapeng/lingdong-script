import { nanoid } from 'nanoid';
import { FlowScript, FlowNode, FlowEdge, NodeType, NodeParams } from '../types';

/**
 * Convert a WinPilot `.wps.json` semantic replay script into native FlowScript.
 *
 * This remains a pure bridge: WinPilot records/replays `.wps.json`; the flow
 * editor edits FlowScript. The mapping is conservative and uses existing node
 * types whenever the current editor/executor has a faithful equivalent. Steps
 * that are WinPilot-only are imported as `tool_call` placeholders with their
 * original payload preserved for manual refinement instead of being dropped.
 */

export interface WpsStep {
  tool: string;
  [k: string]: unknown;
}

export interface WpsScript {
  format?: string;
  name?: string;
  task?: string;
  source?: string;
  source_run?: string;
  vars?: Record<string, string>;
  steps: WpsStep[];
}

const X_STEP = 280;
const Y_STEP = 96;
const KEY_MODIFIERS = new Set(['ctrl', 'alt', 'shift', 'win']);

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

function point(v: unknown, fallback: [number, number] = [0, 0]): [number, number] {
  if (!Array.isArray(v)) return fallback;
  return [num(v[0], fallback[0]), num(v[1], fallback[1])];
}

function clickMethod(step: WpsStep): string {
  if (step.double) return 'double';
  if (step.button === 'right') return 'right';
  if (step.button === 'middle') return 'middle';
  return 'single';
}

function originalPayload(step: WpsStep): Record<string, unknown> {
  const { tool: _tool, elapsed_ms: _elapsed, ...rest } = step;
  return rest;
}

function normalizeKey(key: unknown): string {
  const value = str(key).toLowerCase();
  return value === 'cmd' ? 'win' : value;
}

function toolCall(featureName: string, step: WpsStep, extra: Record<string, unknown> = {}): { type: NodeType; params: NodeParams } {
  return {
    type: 'tool_call',
    params: {
      feature_name: featureName,
      params: { ...originalPayload(step), ...extra },
    } as NodeParams,
  };
}

function keyboardParams(keysValue: unknown): NodeParams {
  const raw = Array.isArray(keysValue) ? keysValue.map(normalizeKey).filter(Boolean) : [];
  const modifiers = raw.filter((k) => KEY_MODIFIERS.has(k));
  const keys = raw.filter((k) => !KEY_MODIFIERS.has(k));
  return {
    keys: keys.length ? keys : raw,
    modifiers,
    delay_after: 0.2,
  } as NodeParams;
}

function waitParams(step: WpsStep): NodeParams {
  const condition = step.condition as Record<string, unknown> | undefined;
  if (condition && typeof condition === 'object') {
    const windowTitle = str(condition.window_appears || condition.window_title).trim();
    if (windowTitle) {
      return {
        wait_type: 'window',
        window_title: windowTitle,
        timeout: num(step.timeout_s, 10),
        description: JSON.stringify(condition),
      } as NodeParams;
    }
  }
  return {
    wait_type: 'time',
    duration: num(step.timeout_s ?? step.seconds, 1),
    timeout: num(step.timeout_s, 10),
  } as NodeParams;
}

function scrollParams(step: WpsStep): NodeParams {
  const [x, y] = point(step.coords);
  const amount = num(step.amount, 0);
  return {
    sub_operation: 'scroll',
    x,
    y,
    scroll_direction: amount >= 0 ? 'up' : 'down',
    scroll_amount: Math.max(1, Math.abs(amount || 3)),
    delay_after: 0.3,
  } as NodeParams;
}

function screenshotParams(step: WpsStep): NodeParams {
  return {
    format: str(step.format || 'png') === 'jpg' ? 'jpg' : 'png',
    save_path: str(step.path || ''),
    region: step.region as any,
  } as NodeParams;
}

/** Map one leaf WPS step to a native FlowScript node. */
function leafNode(step: WpsStep): { type: NodeType; params: NodeParams } {
  const t = step.tool;
  switch (t) {
    case 'click': {
      const text = str(step.text || step.element_text).trim();
      const [x, y] = point(step.coords);
      if (text) {
        return {
          type: 'click_text',
          params: {
            text,
            similarity: 80,
            method: clickMethod(step),
            offset_x: 0,
            offset_y: 0,
            timeout: 5,
            delay_after: 0.5,
          } as NodeParams,
        };
      }
      return {
        type: 'click',
        params: {
          x,
          y,
          offsetX: 0,
          offsetY: 0,
          method: clickMethod(step),
          delay_after: 0.5,
        } as NodeParams,
      };
    }
    case 'type_text': {
      const [x, y] = point(step.coords, [NaN, NaN]);
      return {
        type: 'input_text',
        params: {
          text: str(step.text_input),
          ...(Number.isFinite(x) && Number.isFinite(y) ? { x, y } : {}),
          click_before: !!step.coords,
          clear_before: false,
          delay_after: 0.3,
        } as NodeParams,
      };
    }
    case 'hotkey':
      return { type: 'keyboard', params: keyboardParams(step.keys) };
    case 'launch':
      return {
        type: 'launch_program',
        params: { target: str(step.command), args: '', wait: false } as NodeParams,
      };
    case 'focus_window':
      return {
        type: 'focus_window',
        params: { title: str(step.title), exact: false } as NodeParams,
      };
    case 'scroll':
      return { type: 'mouse_operation', params: scrollParams(step) };
    case 'drag': {
      const [sx, sy] = point(step.from);
      const [ex, ey] = point(step.to);
      return {
        type: 'swipe',
        params: {
          start_x: sx,
          start_y: sy,
          end_x: ex,
          end_y: ey,
          duration: num(step.duration, 0.4),
          delay_after: 0.3,
        } as NodeParams,
      };
    }
    case 'draw_strokes':
      return toolCall('draw_strokes', step, { _wps_tool: 'draw_strokes' });
    case 'tray_click':
      return toolCall('tray_click', step);
    case 'wait':
      return { type: 'wait', params: waitParams(step) };
    case 'run_shell':
    case 'ext.run_shell':
      return {
        type: 'tool_call',
        params: {
          feature_name: 'run_command',
          params: {
            command: str(step.command),
            timeout: num(step.timeout_s, 30),
            _wps_shell: str(step.shell || 'powershell'),
          },
        } as NodeParams,
      };
    case 'run_detached':
      return toolCall('run_detached', step, { _wps_tool: 'run_detached' });
    case 'ext.delay':
      return {
        type: 'wait',
        params: { wait_type: 'time', duration: num(step.seconds, 1), timeout: 10 } as NodeParams,
      };
    case 'ext.var_set':
      return {
        type: 'variable_set',
        params: { name: str(step.name), value: str(step.value) } as NodeParams,
      };
    case 'ext.clipboard_set':
      return { type: 'clipboard_set', params: { text: str(step.text) } as NodeParams };
    case 'ext.clipboard_get':
      return { type: 'clipboard_get', params: {} as NodeParams };
    case 'ext.screenshot':
      return { type: 'screenshot', params: screenshotParams(step) };
    default:
      return toolCall(t || 'unknown_wps_step', step, { _wps_tool: t });
  }
}

/** Best-effort condition params from a WPS verify condition. */
function condParams(cond: Record<string, unknown> | undefined): NodeParams {
  const c = cond || {};
  if ('window_appears' in c || 'window_gone' in c) {
    return {
      condition_type: 'window_exists',
      window_title: str(c.window_appears || c.window_gone),
      similarity: 85,
      _wps_cond: c,
    } as NodeParams;
  }
  if ('color_match' in c) {
    return { condition_type: 'color_match', similarity: 85, _wps_cond: c } as NodeParams;
  }
  if (!Object.keys(c).length) {
    return { condition_type: 'always_true', similarity: 85 } as NodeParams;
  }
  return { condition_type: 'image_exists', similarity: 85, _wps_cond: c } as NodeParams;
}

function loopParams(step: WpsStep): NodeParams {
  const loopType = str(step.loop_type) || 'count';
  return {
    loop_type: loopType === 'infinite' ? 'infinite' : loopType === 'count' ? 'count' : 'condition',
    count: num(step.count, 1),
    condition: step.cond ? condParams(step.cond as Record<string, unknown>) : undefined,
    description: loopType === 'while' || loopType === 'until' ? `wps ${loopType}` : undefined,
  } as NodeParams;
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
        const node = add('loop', loopParams(step), depth);
        attach(node.id);
        const bodyL = layout((step.body as WpsStep[]) || [], depth + 1);
        if (bodyL.entry) {
          link(node.id, 'loop_body', bodyL.entry);
          bodyL.exits.forEach((e) => link(e.node, e.port, node.id));
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

  const start = add('start', { label: 'Start' } as NodeParams, 0);
  const body = layout(script.steps || [], 0);
  if (body.entry) link(start.id, 'output', body.entry);
  const end = add('end', { label: 'End' } as NodeParams, 0);
  const tails = body.exits.length ? body.exits : [{ node: start.id, port: 'output' }];
  tails.forEach((tail) => link(tail.node, tail.port, end.id));

  const now = new Date().toISOString();
  const name = script.name || script.task || 'Imported replay';
  return {
    meta: {
      id: nanoid(10),
      name,
      description: script.task || '',
      created_at: now,
      updated_at: now,
      version: '1.0.0',
      source: 'flow',
      tags: ['wps-import', script.format || 'wps'],
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
