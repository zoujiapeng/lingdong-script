/**
 * DSL Generator: 将 FlowScript 转换为大写 DSL 文本
 * 生成格式如 CLICK 100 200 / LOOP 5 ... END / IF cond ... ELSE ... END
 */

import { FlowScript, FlowNode, FlowEdge } from '../types';

interface DSLGenState {
  lines: string[];
  indent: number;
  visited: Set<string>;
  nodeMap: Map<string, FlowNode>;
  outgoing: Map<string, Map<string, FlowEdge[]>>;
}

function emit(state: DSLGenState, line: string) {
  state.lines.push('  '.repeat(state.indent) + line);
}

function getEdge(
  outgoing: Map<string, Map<string, FlowEdge[]>>,
  nodeId: string,
  port: string
): FlowEdge | undefined {
  return outgoing.get(nodeId)?.get(port)?.[0];
}

function collectReachable(
  nodeId: string,
  outgoing: Map<string, Map<string, FlowEdge[]>>,
  visited: Set<string> = new Set()
): Set<string> {
  if (visited.has(nodeId)) return visited;
  visited.add(nodeId);
  const ports = outgoing.get(nodeId);
  if (!ports) return visited;
  for (const [, edges] of ports) {
    for (const e of edges) {
      collectReachable(e.to_node, outgoing, visited);
    }
  }
  return visited;
}

function findConvergence(
  aStart: string,
  bStart: string,
  outgoing: Map<string, Map<string, FlowEdge[]>>
): string | undefined {
  const aSet = collectReachable(aStart, outgoing, new Set());
  const bSet = collectReachable(bStart, outgoing, new Set());
  for (const id of aSet) {
    if (bSet.has(id) && id !== aStart && id !== bStart) return id;
  }
  return undefined;
}

function genClickLine(params: any): string {
  const x = params.x ?? 0;
  const y = params.y ?? 0;
  const method = params.method ?? 'single';
  const methodSuffix: Record<string, string> = { double: ' DOUBLE', right: ' RIGHT', middle: ' MIDDLE' };
  return `CLICK ${x} ${y}${methodSuffix[method] || ''}`;
}

function genWaitLine(params: any): string {
  const wt = params.wait_type ?? 'time';
  if (wt === 'time') return `WAIT ${params.duration ?? 1}`;
  if (wt === 'image') return `WAITSCREEN ${params.template_path ?? ''} ${params.timeout ?? 10}`;
  if (wt === 'window') return `WAIT ${params.window_title ?? ''}`;
  return 'WAIT 1';
}

function genTypeLine(params: any): string {
  return `TYPE "${params.text ?? ''}"`;
}

function genKeyboardLine(params: any): string {
  const keys = params.keys ?? [];
  const modifiers = params.modifiers ?? [];
  const all = [...modifiers.map((m: string) => m.toLowerCase()), ...keys.map((k: string) => k.toLowerCase())];
  return `HOTKEY ${all.join('+')}`;
}

function genSwipeLine(params: any): string {
  const sx = params.start_x ?? 0;
  const sy = params.start_y ?? 0;
  const ex = params.end_x ?? 0;
  const ey = params.end_y ?? 0;
  return `SWIPE ${sx} ${sy} ${ex} ${ey}`;
}

function genCondition(params: any): string {
  const ct = params.condition_type ?? 'always_true';
  if (ct === 'always_true') return 'true';
  if (ct === 'image_exists') return `IMAGE_FIND "${params.template_path ?? ''}"`;
  if (ct === 'window_exists') return `FOCUS "${params.window_title ?? ''}"`;
  if (ct === 'variable_equals') return `$eq(${params.variable_name ?? ''}, ${params.variable_value ?? ''})`;
  if (ct === 'variable_greater') return `$gt(${params.variable_name ?? ''}, ${params.variable_value ?? ''})`;
  if (ct === 'variable_less') return `$lt(${params.variable_name ?? ''}, ${params.variable_value ?? ''})`;
  return 'true';
}

function genImageClickLine(params: any): string {
  const targets = params.targets ?? [];
  const paths = targets.map((t: any) => `"${t.path}"`).join(' ');
  return `IMAGE_CLICK ${paths}`;
}

function genScreenshotLine(): string {
  return 'SCREENSHOT';
}

function genVariableSetLine(params: any): string {
  return `SET ${params.name ?? ''} "${params.value ?? ''}"`;
}

function genVariableGetLine(params: any): string {
  return `SET ${params.name ?? ''}`;
}

function genClickTextLine(params: any): string {
  const text = params.text ?? '';
  const method = params.method === 'double' ? ' DOUBLE' : params.method === 'right' ? ' RIGHT' : '';
  return `OCR_FIND "${text}"${method}`;
}

function genPopupCloseLine(params: any): string {
  const s = (params.strategies ?? ['auto'])[0];
  return `POPUP_CLOSE ${s === 'auto' ? '' : s}`.trim();
}

function genColorCheckLine(params: any): string {
  const x = params.x ?? 0;
  const y = params.y ?? 0;
  const w = params.w ?? 100;
  const h = params.h ?? 100;
  const color = params.expected_color ?? '';
  return `COLOR_CHECK ${x} ${y} ${w} ${h} "${color}" ${params.tolerance ?? 30}`;
}

function genProgressWaitLine(params: any): string {
  const wt = params.wait_type ?? 'fill';
  const r = params.region ?? {};
  return `PROGRESS_WAIT ${r.x ?? 0},${r.y ?? 0},${r.w ?? 200},${r.h ?? 20} ${wt}`;
}

function genRegionWaitLine(params: any): string {
  const x = params.x ?? 0;
  const y = params.y ?? 0;
  const w = params.w ?? 200;
  const h = params.h ?? 200;
  const rt = params.region_type ?? 'stable';
  return `REGION_WAIT ${x} ${y} ${w} ${h} ${rt}`;
}

function genSceneConditionLine(params: any): string {
  const name = params.scene_name ?? '';
  const sim = params.similarity ?? 70;
  return `SCENE_CONDITION ${name} ${sim}`;
}

function genSubScriptLine(params: any): string {
  const name = params.script_name ?? params.script_id ?? '';
  return `CALL ${name}`;
}

// ─── Traversal ───

function generateNode(nodeId: string, state: DSLGenState, convergence?: string): void {
  if (!nodeId || state.visited.has(nodeId)) return;
  if (nodeId === convergence) return;

  const node = state.nodeMap.get(nodeId);
  if (!node) return;
  if (node.disabled) return;

  state.visited.add(nodeId);
  const p = node.params as any;

  switch (node.type) {
    case 'start':
      forward(nodeId, 'output', state, convergence);
      break;

    case 'end':
      emit(state, 'RETURN');
      break;

    case 'click':
      emit(state, genClickLine(p));
      forward(nodeId, 'output', state, convergence);
      break;

    case 'wait':
      emit(state, genWaitLine(p));
      forward(nodeId, 'output', state, convergence);
      break;

    case 'input_text':
      emit(state, genTypeLine(p));
      forward(nodeId, 'output', state, convergence);
      break;

    case 'keyboard':
      emit(state, genKeyboardLine(p));
      forward(nodeId, 'output', state, convergence);
      break;

    case 'swipe':
      emit(state, genSwipeLine(p));
      forward(nodeId, 'output', state, convergence);
      break;

    case 'image_click':
      emit(state, genImageClickLine(p));
      forward(nodeId, 'output', state, convergence);
      break;

    case 'screenshot':
      emit(state, genScreenshotLine());
      forward(nodeId, 'output', state, convergence);
      break;

    case 'variable_set':
      emit(state, genVariableSetLine(p));
      forward(nodeId, 'output', state, convergence);
      break;

    case 'variable_get':
      emit(state, genVariableGetLine(p));
      forward(nodeId, 'output', state, convergence);
      break;

    case 'click_text':
      emit(state, genClickTextLine(p));
      forward(nodeId, 'output', state, convergence);
      break;

    case 'popup_close':
      emit(state, genPopupCloseLine(p));
      forward(nodeId, 'output', state, convergence);
      break;

    case 'color_check':
      emit(state, genColorCheckLine(p));
      forward(nodeId, 'output', state, convergence);
      break;

    case 'progress_wait':
      emit(state, genProgressWaitLine(p));
      forward(nodeId, 'output', state, convergence);
      break;

    case 'region_wait':
      emit(state, genRegionWaitLine(p));
      forward(nodeId, 'output', state, convergence);
      break;

    case 'scene_condition':
      emit(state, genSceneConditionLine(p));
      forward(nodeId, 'yes', state, convergence);
      break;

    case 'sub_script':
      emit(state, genSubScriptLine(p));
      forward(nodeId, 'output', state, convergence);
      break;

    case 'condition': {
      const cond = genCondition(p);
      const yesEdge = getEdge(state.outgoing, nodeId, 'yes');
      const noEdge = getEdge(state.outgoing, nodeId, 'no');

      if (yesEdge && noEdge) {
        const conv = findConvergence(yesEdge.to_node, noEdge.to_node, state.outgoing);

        emit(state, `IF ${cond}`);
        state.indent++;
        generateLinear(yesEdge.to_node, state, conv);
        state.indent--;

        emit(state, 'ELSE');
        state.indent++;
        generateLinear(noEdge.to_node, state, conv);
        state.indent--;

        emit(state, 'END');
        if (conv) generateNode(conv, state, convergence);
      } else if (yesEdge) {
        emit(state, `IF ${cond}`);
        state.indent++;
        generateLinear(yesEdge.to_node, state, undefined);
        state.indent--;
        emit(state, 'END');
        forward(nodeId, 'output', state, convergence);
      } else {
        emit(state, `IF ${cond}`);
        emit(state, '  WAIT 0.1');
        emit(state, 'END');
        forward(nodeId, 'output', state, convergence);
      }
      break;
    }

    case 'loop': {
      const lt = p.loop_type ?? 'count';
      const count = p.count ?? 10;
      const bodyEdge = getEdge(state.outgoing, nodeId, 'loop_body');
      const endEdge = getEdge(state.outgoing, nodeId, 'loop_end');

      if (lt === 'infinite') {
        emit(state, 'LOOP INFINITE');
      } else if (lt === 'count') {
        emit(state, `LOOP ${count}`);
      } else {
        emit(state, 'LOOP 10');
      }

      state.indent++;
      const savedVisited = new Set(state.visited);
      if (bodyEdge) generateLinear(bodyEdge.to_node, state, undefined);
      state.visited = savedVisited;
      state.indent--;

      emit(state, 'END');

      if (endEdge) forward(nodeId, 'loop_end', state, convergence);
      break;
    }

    default:
      emit(state, `# UNKNOWN: ${node.type}`);
      forward(nodeId, 'output', state, convergence);
  }
}

function generateLinear(startId: string, state: DSLGenState, convergence?: string): void {
  let current = startId;
  while (current && !state.visited.has(current)) {
    if (current === convergence) break;
    const node = state.nodeMap.get(current);
    if (!node) break;
    if (node.type === 'loop' && state.visited.has(current)) break;

    generateNode(current, state, convergence);

    if (node.type === 'condition') {
      const edge = getEdge(state.outgoing, current, 'output');
      if (edge && !state.visited.has(edge.to_node)) current = edge.to_node;
      else break;
    } else {
      const nextId = getNextNodeId(current, state);
      if (!nextId || state.visited.has(nextId)) break;
      current = nextId;
    }
  }
}

function getNextNodeId(nodeId: string, state: DSLGenState): string | undefined {
  const outputEdge = getEdge(state.outgoing, nodeId, 'output');
  if (outputEdge) return outputEdge.to_node;
  const node = state.nodeMap.get(nodeId);
  if (node?.type === 'end') return undefined;
  const ports = state.outgoing.get(nodeId);
  if (ports) {
    for (const [port, edges] of ports) {
      if (port !== 'input' && edges.length > 0) return edges[0].to_node;
    }
  }
  return undefined;
}

function forward(nodeId: string, port: string, state: DSLGenState, convergence?: string): void {
  const edge = getEdge(state.outgoing, nodeId, port);
  if (edge && edge.to_node !== convergence && !state.visited.has(edge.to_node)) {
    generateNode(edge.to_node, state, convergence);
  }
}

// ─── Public API ───

export function generateDSL(script: FlowScript): string {
  const state: DSLGenState = {
    lines: [],
    indent: 0,
    visited: new Set(),
    nodeMap: new Map(script.nodes.map((n) => [n.id, n])),
    outgoing: new Map(),
  };

  for (const node of script.nodes) {
    state.outgoing.set(node.id, new Map());
  }
  for (const edge of script.edges) {
    const portMap = state.outgoing.get(edge.from_node);
    if (portMap) {
      if (!portMap.has(edge.from_port)) portMap.set(edge.from_port, []);
      portMap.get(edge.from_port)!.push(edge);
    }
  }

  const startNode = script.nodes.find((n) => n.type === 'start');
  if (!startNode) return '# ERROR: no start node';

  state.lines.push(`# DSL: ${script.meta.name}${script.meta.description ? ` - ${script.meta.description}` : ''}`);
  state.lines.push('');

  generateNode(startNode.id, state, undefined);

  return state.lines.join('\n');
}
