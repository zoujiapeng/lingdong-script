import { FlowScript, FlowNode, FlowEdge } from '../types';

interface PseudoGenState {
  lines: string[];
  indent: number;
  visited: Set<string>;
  nodeMap: Map<string, FlowNode>;
  outgoing: Map<string, Map<string, FlowEdge[]>>;
}

function emit(state: PseudoGenState, line: string) {
  state.lines.push('  '.repeat(state.indent) + line);
}

function comment(state: PseudoGenState, text: string) {
  emit(state, `# ${text}`);
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
  for (const [port, edges] of ports) {
    if (port === 'loop_body' || port === 'loop_end') continue;
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

function genClickNode(params: any): string {
  const x = params.x ?? 0;
  const y = params.y ?? 0;
  const method = params.method ?? 'single';
  let line = `点击 (${x}, ${y})`;
  if (method !== 'single') {
    const methodMap: Record<string, string> = { 'double': '双击', 'right': '右击', 'middle': '中键' };
    line += ` ${methodMap[method] || method}`;
  }
  if (params.delay_after && params.delay_after !== 0.5) {
    line += ` 延迟=${params.delay_after}`;
  }
  return line;
}

function genImageClickNode(params: any): string {
  const targets = params.targets ?? [];
  let line = '点击图片';
  for (const t of targets) {
    line += ` "${t.path}"`;
  }
  const parts: string[] = [];
  if (params.similarity && params.similarity !== 90) {
    parts.push(`相似度=${params.similarity}`);
  }
  if (params.timeout && params.timeout !== 5) {
    parts.push(`超时=${params.timeout}`);
  }
  if (params.offset_x || params.offset_y) {
    parts.push(`偏移(${params.offset_x ?? 0},${params.offset_y ?? 0})`);
  }
  if (params.method && params.method !== 'single') {
    const methodMap: Record<string, string> = { 'double': '双击', 'right': '右击', 'middle': '中键' };
    parts.push(methodMap[params.method] || params.method);
  }
  if (parts.length > 0) {
    line += ' ' + parts.join(' ');
  }
  return line;
}

function genWaitNode(params: any): string {
  const wt = params.wait_type ?? 'time';
  if (wt === 'time') {
    return `等待 ${params.duration ?? 1}`;
  }
  if (wt === 'image') {
    const timeout = params.timeout ? ` 超时=${params.timeout}` : '';
    return `等待图片 "${params.template_path ?? ''}"${timeout}`;
  }
  if (wt === 'window') {
    const timeout = params.timeout ? ` 超时=${params.timeout}` : '';
    return `等待窗口 "${params.window_title ?? ''}"${timeout}`;
  }
  return '等待 1';
}

function genInputNode(params: any): string {
  const text = params.text ?? '';
  let line = `输入 ${JSON.stringify(text)}`;
  if (params.x != null && params.y != null && params.click_before) {
    line += ` 位置 (${params.x}, ${params.y})`;
  }
  if (params.clear_before !== undefined && !params.clear_before) {
    line += ' 不清空=False';
  }
  return line;
}

function genKeyboardNode(params: any): string {
  const keys = params.keys ?? [];
  const modifiers = params.modifiers ?? [];
  if (modifiers.length > 0) {
    return `按键 ${modifiers.map((m: string) => m.toLowerCase()).join('+')}+${keys[0]?.toLowerCase() || ''}`;
  }
  return `按键 ${keys.map((k: string) => k.toLowerCase()).join('+')}`;
}

function genSwipeNode(params: any): string {
  const sx = params.start_x ?? 0;
  const sy = params.start_y ?? 0;
  const ex = params.end_x ?? 0;
  const ey = params.end_y ?? 0;
  let line = `滑动 (${sx},${sy}) 到 (${ex},${ey})`;
  if (params.duration && params.duration !== 0.5) {
    line += ` 时长=${params.duration}`;
  }
  return line;
}

function genConditionExpr(params: any): string {
  const ct = params.condition_type ?? 'always_true';
  if (ct === 'always_true') return '真';
  if (ct === 'image_exists') {
    const sim = params.similarity ? ` 相似度=${params.similarity}` : '';
    return `图片存在 "${params.template_path ?? ''}"${sim}`;
  }
  if (ct === 'window_exists') {
    return `窗口存在 "${params.window_title ?? ''}"`;
  }
  if (ct === 'variable_equals') {
    return `变量等于 ${params.variable_name ?? ''} "${params.variable_value ?? ''}"`;
  }
  if (ct === 'variable_greater') {
    return `变量大于 ${params.variable_name ?? ''} ${params.variable_value ?? ''}`;
  }
  if (ct === 'variable_less') {
    return `变量小于 ${params.variable_name ?? ''} ${params.variable_value ?? ''}`;
  }
  return '真';
}

function genVariableSetNode(params: any): string {
  return `$${params.name ?? ''} = "${params.value ?? ''}"`;
}

function genVariableGetNode(params: any): string {
  return `$${params.name ?? ''}`;
}

function genSubScriptNode(params: any): string {
  return `运行 "${params.script_name ?? ''}"`;
}

// ─── Main traversal ───

function generateNode(
  nodeId: string,
  state: PseudoGenState,
  convergence?: string
): void {
  if (!nodeId || state.visited.has(nodeId)) return;
  if (nodeId === convergence) return;

  const node = state.nodeMap.get(nodeId);
  if (!node) return;
  if (node.disabled) {
    comment(state, `disabled: ${nodeId} (${node.type})`);
    return;
  }

  state.visited.add(nodeId);
  const params = node.params as any;
  const label = node.label;

  switch (node.type) {
    case 'start':
      // Start is implicit, just follow output
      forward(nodeId, 'output', state, convergence);
      break;

    case 'end':
      // End is implicit
      break;

    case 'click': {
      emit(state, genClickNode(params));
      forward(nodeId, 'output', state, convergence);
      break;
    }

    case 'image_click': {
      emit(state, genImageClickNode(params));
      const failEdge = getEdge(state.outgoing, nodeId, 'fail');
      if (failEdge) {
        // If there's a fail cascade, follow it at the same indent
        forward(nodeId, 'output', state, undefined);
      } else {
        forward(nodeId, 'output', state, convergence);
      }
      break;
    }

    case 'wait': {
      emit(state, genWaitNode(params));
      forward(nodeId, 'output', state, convergence);
      break;
    }

    case 'input_text': {
      emit(state, genInputNode(params));
      forward(nodeId, 'output', state, convergence);
      break;
    }

    case 'keyboard': {
      emit(state, genKeyboardNode(params));
      forward(nodeId, 'output', state, convergence);
      break;
    }

    case 'swipe': {
      emit(state, genSwipeNode(params));
      forward(nodeId, 'output', state, convergence);
      break;
    }

    case 'condition': {
      const condExpr = genConditionExpr(params);
      const yesEdge = getEdge(state.outgoing, nodeId, 'yes');
      const noEdge = getEdge(state.outgoing, nodeId, 'no');

      if (yesEdge && noEdge) {
        const conv = findConvergence(yesEdge.to_node, noEdge.to_node, state.outgoing);

        emit(state, `如果 ${condExpr}:`);
        state.indent++;
        generateLinear(yesEdge.to_node, state, conv);
        state.indent--;

        emit(state, `否则:`);
        state.indent++;
        generateLinear(noEdge.to_node, state, conv);
        state.indent--;

        if (conv) {
          generateNode(conv, state, convergence);
        }
      } else if (yesEdge) {
        // No explicit else edge, but there might be a 'no' port fallthrough
        const implicitNo = getEdge(state.outgoing, nodeId, 'no');
        if (implicitNo) {
          const conv = findConvergence(yesEdge.to_node, implicitNo.to_node, state.outgoing);
          emit(state, `如果 ${condExpr}:`);
          state.indent++;
          generateLinear(yesEdge.to_node, state, conv);
          state.indent--;

          if (conv) {
            generateNode(conv, state, convergence);
          }
        } else {
          emit(state, `如果 ${condExpr}:`);
          state.indent++;
          generateLinear(yesEdge.to_node, state, undefined);
          state.indent--;
          forward(nodeId, 'output', state, convergence);
        }
      } else {
        emit(state, `# if ${condExpr}: (no branch)`);
        forward(nodeId, 'output', state, convergence);
      }
      break;
    }

    case 'loop': {
      const lt = params.loop_type ?? 'count';
      const count = params.count ?? 10;
      const bodyEdge = getEdge(state.outgoing, nodeId, 'loop_body');
      const endEdge = getEdge(state.outgoing, nodeId, 'loop_end');

      if (lt === 'infinite') {
        emit(state, `无限循环:`);
      } else if (lt === 'count') {
        emit(state, `循环 ${count}:`);
      } else {
        const condExpr = genConditionExpr(params.condition ?? { condition_type: 'always_true' });
        emit(state, `条件循环 ${condExpr}:`);
      }

      state.indent++;
      const savedVisited = new Set(state.visited);
      if (bodyEdge) {
        generateLinear(bodyEdge.to_node, state, undefined);
      }
      state.visited = savedVisited;
      state.indent--;

      if (endEdge) {
        forward(nodeId, 'loop_end', state, convergence);
      }
      break;
    }

    case 'screenshot': {
      emit(state, '截图');
      forward(nodeId, 'output', state, convergence);
      break;
    }

    case 'variable_set': {
      emit(state, genVariableSetNode(params));
      forward(nodeId, 'output', state, convergence);
      break;
    }

    case 'variable_get': {
      emit(state, genVariableGetNode(params));
      forward(nodeId, 'output', state, convergence);
      break;
    }

    case 'sub_script': {
      emit(state, genSubScriptNode(params));
      forward(nodeId, 'output', state, convergence);
      break;
    }

    default:
      comment(state, `unknown: ${node.type} (${nodeId})`);
      forward(nodeId, 'output', state, convergence);
  }
}

function generateLinear(
  startId: string,
  state: PseudoGenState,
  convergence?: string
): void {
  let current = startId;
  while (current && !state.visited.has(current)) {
    if (current === convergence) break;
    const node = state.nodeMap.get(current);
    if (!node) break;
    if (node.type === 'loop' && state.visited.has(current)) break;

    generateNode(current, state, convergence);

    if (node.type === 'condition') {
      const edge = getEdge(state.outgoing, current, 'output');
      if (edge && !state.visited.has(edge.to_node)) {
        current = edge.to_node;
      } else {
        break;
      }
    } else {
      const nextId = getNextNodeId(current, state);
      if (!nextId || state.visited.has(nextId)) break;
      current = nextId;
    }
  }
}

function getNextNodeId(nodeId: string, state: PseudoGenState): string | undefined {
  const outputEdge = getEdge(state.outgoing, nodeId, 'output');
  if (outputEdge) return outputEdge.to_node;
  const node = state.nodeMap.get(nodeId);
  if (node?.type === 'end') return undefined;
  const ports = state.outgoing.get(nodeId);
  if (ports) {
    for (const [port, edges] of ports) {
      if (port !== 'input' && edges.length > 0) {
        return edges[0].to_node;
      }
    }
  }
  return undefined;
}

function forward(
  nodeId: string,
  port: string,
  state: PseudoGenState,
  convergence?: string
): void {
  const edge = getEdge(state.outgoing, nodeId, port);
  if (edge && edge.to_node !== convergence && !state.visited.has(edge.to_node)) {
    generateNode(edge.to_node, state, convergence);
  }
}

// ─── Public API ───

export function generatePseudoCode(script: FlowScript): string {
  const state: PseudoGenState = {
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
  if (!startNode) return '# Error: no start node found';

  // Metadata header
  state.lines.push(`# 灵动脚本: ${script.meta.name}${script.meta.description ? ` - ${script.meta.description}` : ''}`);
  state.lines.push('');

  generateNode(startNode.id, state, undefined);

  return state.lines.join('\n');
}
