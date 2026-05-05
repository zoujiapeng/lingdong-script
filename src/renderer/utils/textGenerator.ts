/**
 * Unified Text Generator
 * Merges DSL + PseudoCode into one generator with two modes.
 * Traversal logic is shared; only per-node text generation differs.
 */

import { FlowScript, FlowNode, FlowEdge } from '../types';

export type TextMode = 'pseudocode' | 'dsl';

interface GenState {
  lines: string[];
  indent: number;
  visited: Set<string>;
  nodeMap: Map<string, FlowNode>;
  outgoing: Map<string, Map<string, FlowEdge[]>>;
}

function emit(state: GenState, line: string) {
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

// ─── DSL-mode generators (English keywords) ───

function dslGenClick(params: any): string {
  const x = params.x ?? 0;
  const y = params.y ?? 0;
  const method = params.method ?? 'single';
  const suffix: Record<string, string> = { double: ' DOUBLE', right: ' RIGHT', middle: ' MIDDLE' };
  return `CLICK ${x} ${y}${suffix[method] || ''}`;
}

function dslGenWait(params: any): string {
  const wt = params.wait_type ?? 'time';
  if (wt === 'time') return `WAIT ${params.duration ?? 1}`;
  if (wt === 'image') return `WAITSCREEN ${params.template_path ?? ''} ${params.timeout ?? 10}`;
  if (wt === 'window') return `WAIT ${params.window_title ?? ''}`;
  return 'WAIT 1';
}

function dslGenType(params: any): string {
  return `TYPE "${params.text ?? ''}"`;
}

function dslGenKeyboard(params: any): string {
  const keys = params.keys ?? [];
  const modifiers = params.modifiers ?? [];
  const all = [...modifiers.map((m: string) => m.toLowerCase()), ...keys.map((k: string) => k.toLowerCase())];
  return `HOTKEY ${all.join('+')}`;
}

function dslGenSwipe(params: any): string {
  return `SWIPE ${params.start_x ?? 0} ${params.start_y ?? 0} ${params.end_x ?? 0} ${params.end_y ?? 0}`;
}

function dslGenImageClick(params: any): string {
  const targets = params.targets ?? [];
  return `IMAGE_CLICK ${targets.map((t: any) => `"${t.path}"`).join(' ')}`;
}

function dslGenScreenshot(): string {
  return 'SCREENSHOT';
}

function dslGenVariableSet(params: any): string {
  return `SET ${params.name ?? ''} "${params.value ?? ''}"`;
}

function dslGenVariableGet(params: any): string {
  return `SET ${params.name ?? ''}`;
}

function dslGenClickText(params: any): string {
  const text = params.text ?? '';
  const method = params.method === 'double' ? ' DOUBLE' : params.method === 'right' ? ' RIGHT' : '';
  return `OCR_FIND "${text}"${method}`;
}

function dslGenPopupClose(params: any): string {
  return `POPUP_CLOSE ${(params.strategies ?? ['auto'])[0] === 'auto' ? '' : (params.strategies ?? ['auto'])[0]}`.trim();
}

function dslGenColorCheck(params: any): string {
  return `COLOR_CHECK ${params.x ?? 0} ${params.y ?? 0} ${params.w ?? 100} ${params.h ?? 100} "${params.expected_color ?? ''}" ${params.tolerance ?? 30}`;
}

function dslGenProgressWait(params: any): string {
  const r = params.region ?? {};
  return `PROGRESS_WAIT ${r.x ?? 0},${r.y ?? 0},${r.w ?? 200},${r.h ?? 20} ${params.wait_type ?? 'fill'}`;
}

function dslGenRegionWait(params: any): string {
  return `REGION_WAIT ${params.x ?? 0} ${params.y ?? 0} ${params.w ?? 200} ${params.h ?? 200} ${params.region_type ?? 'stable'}`;
}

function dslGenSceneCondition(params: any): string {
  return `SCENE_CONDITION ${params.scene_name ?? ''} ${params.similarity ?? 70}`;
}

function dslGenSubScript(params: any): string {
  return `CALL ${params.script_name ?? params.script_id ?? ''}`;
}

function dslGenCondition(params: any): string {
  const ct = params.condition_type ?? 'always_true';
  if (ct === 'always_true') return 'true';
  if (ct === 'image_exists') return `IMAGE_FIND "${params.template_path ?? ''}"`;
  if (ct === 'window_exists') return `FOCUS "${params.window_title ?? ''}"`;
  if (ct === 'variable_equals') return `$eq(${params.variable_name ?? ''}, ${params.variable_value ?? ''})`;
  if (ct === 'variable_greater') return `$gt(${params.variable_name ?? ''}, ${params.variable_value ?? ''})`;
  if (ct === 'variable_less') return `$lt(${params.variable_name ?? ''}, ${params.variable_value ?? ''})`;
  return 'true';
}

// ─── Pseudocode-mode generators (Chinese keywords) ───

function pseudoGenClick(params: any): string {
  let line = `点击 (${params.x ?? 0}, ${params.y ?? 0})`;
  const method = params.method ?? 'single';
  const methodMap: Record<string, string> = { double: '双击', right: '右击', middle: '中键' };
  if (method !== 'single') line += ` ${methodMap[method] || method}`;
  if (params.delay_after && params.delay_after !== 0.5) line += ` 延迟=${params.delay_after}`;
  return line;
}

function pseudoGenWait(params: any): string {
  const wt = params.wait_type ?? 'time';
  if (wt === 'time') return `等待 ${params.duration ?? 1}`;
  if (wt === 'image') return `等待图片 "${params.template_path ?? ''}" 超时=${params.timeout ?? 10}`;
  if (wt === 'window') return `等待窗口 "${params.window_title ?? ''}" 超时=${params.timeout ?? 10}`;
  return '等待 1';
}

function pseudoGenType(params: any): string {
  let line = `输入 "${params.text ?? ''}"`;
  if (params.x != null && params.y != null && params.click_before) line += ` 位置 (${params.x}, ${params.y})`;
  if (!params.clear_before) line += ' 不清空';
  return line;
}

function pseudoGenKeyboard(params: any): string {
  const keys = params.keys ?? [];
  const modifiers = params.modifiers ?? [];
  if (modifiers.length > 0) return `按键 ${modifiers.map((m: string) => m).join('+')}+${keys[0] || ''}`;
  return `按键 ${keys.join('+')}`;
}

function pseudoGenSwipe(params: any): string {
  let line = `滑动 (${params.start_x ?? 0},${params.start_y ?? 0}) 到 (${params.end_x ?? 0},${params.end_y ?? 0})`;
  if (params.duration && params.duration !== 0.5) line += ` 时长=${params.duration}`;
  return line;
}

function pseudoGenImageClick(params: any): string {
  const targets = params.targets ?? [];
  let line = '点击图片';
  for (const t of targets) line += ` "${t.path}"`;
  const parts: string[] = [];
  if (params.similarity && params.similarity !== 90) parts.push(`相似度=${params.similarity}`);
  if (params.timeout && params.timeout !== 5) parts.push(`超时=${params.timeout}`);
  if (params.offset_x || params.offset_y) parts.push(`偏移(${params.offset_x ?? 0},${params.offset_y ?? 0})`);
  if (params.method && params.method !== 'single') {
    const m: Record<string, string> = { double: '双击', right: '右击', middle: '中键' };
    parts.push(m[params.method] || params.method);
  }
  if (parts.length > 0) line += ' ' + parts.join(' ');
  return line;
}

function pseudoGenScreenshot(): string {
  return '截图';
}

function pseudoGenVariableSet(params: any): string {
  return `$${params.name ?? ''} = "${params.value ?? ''}"`;
}

function pseudoGenVariableGet(params: any): string {
  return `$${params.name ?? ''}`;
}

function pseudoGenClickText(params: any): string {
  let line = `点击文本 "${params.text ?? ''}"`;
  const parts: string[] = [];
  if (params.similarity && params.similarity !== 80) parts.push(`相似度=${params.similarity}`);
  if (params.offset_x || params.offset_y) parts.push(`偏移(${params.offset_x ?? 0},${params.offset_y ?? 0})`);
  if (params.method && params.method !== 'single') {
    const m: Record<string, string> = { double: '双击', right: '右击', middle: '中键' };
    parts.push(m[params.method] || params.method);
  }
  if (parts.length > 0) line += ' ' + parts.join(' ');
  return line;
}

function pseudoGenPopupClose(params: any): string {
  if (params.strategies?.[0] && params.strategies[0] !== 'auto') return `关闭弹窗 策略=${params.strategies[0]}`;
  return '关闭弹窗';
}

function pseudoGenColorCheck(params: any): string {
  let line = `检测颜色 (${params.x ?? 0},${params.y ?? 0},${params.w ?? 100}x${params.h ?? 100})`;
  if (params.expected_color) line += ` 期望="${params.expected_color}"`;
  if (params.tolerance && params.tolerance !== 30) line += ` 容差=${params.tolerance}`;
  return line;
}

function pseudoGenProgressWait(params: any): string {
  const tm: Record<string, string> = { fill: '填满', empty: '清空', change: '变化' };
  let line = `等待进度 ${tm[params.wait_type ?? 'fill'] || params.wait_type}`;
  if (params.timeout && params.timeout !== 30) line += ` 超时=${params.timeout}`;
  return line;
}

function pseudoGenRegionWait(params: any): string {
  const tm: Record<string, string> = { stable: '稳定', change: '变化' };
  let line = `等待区域 ${tm[params.region_type ?? 'stable'] || params.region_type}`;
  if (params.x != null) line += ` (${params.x},${params.y},${params.w}x${params.h})`;
  return line;
}

function pseudoGenSceneCondition(params: any): string {
  return `场景判断 "${params.scene_name || ''}"`;
}

function pseudoGenSubScript(params: any): string {
  return `运行 "${params.script_name ?? params.script_id ?? ''}"`;
}

function pseudoGenCondition(params: any): string {
  const ct = params.condition_type ?? 'always_true';
  if (ct === 'always_true') return '真';
  if (ct === 'image_exists') return `图片存在 "${params.template_path ?? ''}" 相似度=${params.similarity ?? 85}`;
  if (ct === 'window_exists') return `窗口存在 "${params.window_title ?? ''}"`;
  if (ct === 'variable_equals') return `变量等于 ${params.variable_name ?? ''} "${params.variable_value ?? ''}"`;
  if (ct === 'variable_greater') return `变量大于 ${params.variable_name ?? ''} ${params.variable_value ?? ''}`;
  if (ct === 'variable_less') return `变量小于 ${params.variable_name ?? ''} ${params.variable_value ?? ''}`;
  if (ct === 'color_match') return `颜色匹配 (${params.x ?? 0},${params.y ?? 0},${params.w ?? 100}x${params.h ?? 100}) "${params.expected_color ?? ''}"`;
  if (ct === 'scene_match') return `场景匹配 "${params.scene_name ?? ''}"`;
  return '真';
}

// ─── Generator factory ───

interface GenFunctions {
  genClick: (p: any) => string;
  genWait: (p: any) => string;
  genType: (p: any) => string;
  genKeyboard: (p: any) => string;
  genSwipe: (p: any) => string;
  genImageClick: (p: any) => string;
  genScreenshot: () => string;
  genVariableSet: (p: any) => string;
  genVariableGet: (p: any) => string;
  genClickText: (p: any) => string;
  genPopupClose: (p: any) => string;
  genColorCheck: (p: any) => string;
  genProgressWait: (p: any) => string;
  genRegionWait: (p: any) => string;
  genSceneCondition: (p: any) => string;
  genSubScript: (p: any) => string;
  genCondition: (p: any) => string;
  ifKeyword: string;
  elseKeyword: string;
  endKeyword: string;
  loopInfinite: string;
  loopCount: string;
  toolCallPrefix: string;
  headerFormat: (name: string, desc?: string) => string;
}

function getGens(mode: TextMode): GenFunctions {
  if (mode === 'dsl') {
    return {
      genClick: dslGenClick, genWait: dslGenWait, genType: dslGenType,
      genKeyboard: dslGenKeyboard, genSwipe: dslGenSwipe, genImageClick: dslGenImageClick,
      genScreenshot: dslGenScreenshot, genVariableSet: dslGenVariableSet,
      genVariableGet: dslGenVariableGet, genClickText: dslGenClickText,
      genPopupClose: dslGenPopupClose, genColorCheck: dslGenColorCheck,
      genProgressWait: dslGenProgressWait, genRegionWait: dslGenRegionWait,
      genSceneCondition: dslGenSceneCondition, genSubScript: dslGenSubScript,
      genCondition: dslGenCondition,
      ifKeyword: 'IF', elseKeyword: 'ELSE', endKeyword: 'END',
      loopInfinite: 'LOOP INFINITE', loopCount: 'LOOP',
      toolCallPrefix: '# TOOL:',
      headerFormat: (name, desc) => `# DSL: ${name}${desc ? ` - ${desc}` : ''}`,
    };
  }
  return {
    genClick: pseudoGenClick, genWait: pseudoGenWait, genType: pseudoGenType,
    genKeyboard: pseudoGenKeyboard, genSwipe: pseudoGenSwipe, genImageClick: pseudoGenImageClick,
    genScreenshot: pseudoGenScreenshot, genVariableSet: pseudoGenVariableSet,
    genVariableGet: pseudoGenVariableGet, genClickText: pseudoGenClickText,
    genPopupClose: pseudoGenPopupClose, genColorCheck: pseudoGenColorCheck,
    genProgressWait: pseudoGenProgressWait, genRegionWait: pseudoGenRegionWait,
    genSceneCondition: pseudoGenSceneCondition, genSubScript: pseudoGenSubScript,
    genCondition: pseudoGenCondition,
    ifKeyword: '如果', elseKeyword: '否则', endKeyword: '结束',
    loopInfinite: '无限循环:', loopCount: '循环',
    toolCallPrefix: '# 工具:',
    headerFormat: (name, desc) => `# 灵动脚本: ${name}${desc ? ` - ${desc}` : ''}`,
  };
}

// ─── Main traversal (shared) ───

function generateNode(nodeId: string, state: GenState, gens: GenFunctions, convergence?: string): void {
  if (!nodeId || state.visited.has(nodeId)) return;
  if (nodeId === convergence) return;

  const node = state.nodeMap.get(nodeId);
  if (!node) return;
  if (node.disabled) { emit(state, `# disabled: ${nodeId}`); return; }

  state.visited.add(nodeId);
  const p = node.params as any;

  switch (node.type) {
    case 'start':
      forward(nodeId, 'output', state, gens, convergence);
      break;

    case 'end':
      break;

    case 'click':
      emit(state, gens.genClick(p));
      forward(nodeId, 'output', state, gens, convergence);
      break;

    case 'wait':
      emit(state, gens.genWait(p));
      forward(nodeId, 'output', state, gens, convergence);
      break;

    case 'input_text':
      emit(state, gens.genType(p));
      forward(nodeId, 'output', state, gens, convergence);
      break;

    case 'keyboard':
      emit(state, gens.genKeyboard(p));
      forward(nodeId, 'output', state, gens, convergence);
      break;

    case 'swipe':
      emit(state, gens.genSwipe(p));
      forward(nodeId, 'output', state, gens, convergence);
      break;

    case 'image_click':
      emit(state, gens.genImageClick(p));
      forward(nodeId, 'output', state, gens, convergence);
      forward(nodeId, 'fail', state, gens, convergence);
      break;

    case 'screenshot':
      emit(state, gens.genScreenshot());
      forward(nodeId, 'output', state, gens, convergence);
      break;

    case 'variable_set':
      emit(state, gens.genVariableSet(p));
      forward(nodeId, 'output', state, gens, convergence);
      break;

    case 'variable_get':
      emit(state, gens.genVariableGet(p));
      forward(nodeId, 'output', state, gens, convergence);
      break;

    case 'click_text':
      emit(state, gens.genClickText(p));
      forward(nodeId, 'output', state, gens, convergence);
      forward(nodeId, 'fail', state, gens, convergence);
      break;

    case 'popup_close':
      emit(state, gens.genPopupClose(p));
      forward(nodeId, 'output', state, gens, convergence);
      break;

    case 'color_check':
      emit(state, gens.genColorCheck(p));
      forward(nodeId, 'output', state, gens, convergence);
      forward(nodeId, 'fail', state, gens, convergence);
      break;

    case 'progress_wait':
      emit(state, gens.genProgressWait(p));
      forward(nodeId, 'output', state, gens, convergence);
      break;

    case 'region_wait':
      emit(state, gens.genRegionWait(p));
      forward(nodeId, 'output', state, gens, convergence);
      break;

    case 'scene_condition': {
      const sYesEdge = getEdge(state.outgoing, nodeId, 'yes');
      const sNoEdge = getEdge(state.outgoing, nodeId, 'no');
      if (sYesEdge && sNoEdge) {
        emit(state, `${gens.ifKeyword} ${gens.genSceneCondition(p)}:`);
        state.indent++;
        generateLinear(sYesEdge.to_node, state, gens, undefined);
        state.indent--;
        emit(state, `${gens.elseKeyword}:`);
        state.indent++;
        generateLinear(sNoEdge.to_node, state, gens, undefined);
        state.indent--;
      } else if (sYesEdge) {
        emit(state, `${gens.ifKeyword} ${gens.genSceneCondition(p)}:`);
        state.indent++;
        generateLinear(sYesEdge.to_node, state, gens, undefined);
        state.indent--;
      } else {
        emit(state, gens.genSceneCondition(p));
        forward(nodeId, 'yes', state, gens, convergence);
      }
      break;
    }

    case 'sub_script':
      emit(state, gens.genSubScript(p));
      forward(nodeId, 'output', state, gens, convergence);
      break;

    case 'tool_call': {
      const fn = p.feature_name ?? '';
      const args = p.params ? Object.entries(p.params).map(([k, v]) => `${k}=${v}`).join(' ') : '';
      emit(state, `${gens.toolCallPrefix} ${fn}${args ? ` ${args}` : ''}`);
      forward(nodeId, 'output', state, gens, convergence);
      break;
    }

    case 'condition': {
      const cond = gens.genCondition(p);
      const yesEdge = getEdge(state.outgoing, nodeId, 'yes');
      const noEdge = getEdge(state.outgoing, nodeId, 'no');

      if (yesEdge && noEdge) {
        const conv = findConvergence(yesEdge.to_node, noEdge.to_node, state.outgoing);
        emit(state, `${gens.ifKeyword} ${cond}:`);
        state.indent++;
        generateLinear(yesEdge.to_node, state, gens, conv);
        state.indent--;
        emit(state, `${gens.elseKeyword}:`);
        state.indent++;
        generateLinear(noEdge.to_node, state, gens, conv);
        state.indent--;
        if (conv) generateNode(conv, state, gens, convergence);
      } else if (yesEdge) {
        emit(state, `${gens.ifKeyword} ${cond}:`);
        state.indent++;
        generateLinear(yesEdge.to_node, state, gens, undefined);
        state.indent--;
      } else {
        emit(state, `# ${gens.ifKeyword} ${cond}: (no branch)`);
      }
      break;
    }

    case 'loop': {
      const lt = p.loop_type ?? 'count';
      const count = p.count ?? 10;
      const bodyEdge = getEdge(state.outgoing, nodeId, 'loop_body');
      const endEdge = getEdge(state.outgoing, nodeId, 'loop_end');

      if (lt === 'infinite') {
        emit(state, `${gens.loopInfinite}`);
      } else if (lt === 'count') {
        emit(state, `${gens.loopCount} ${count}:`);
      } else {
        emit(state, `${gens.loopCount}:`);
      }

      state.indent++;
      const saved = new Set(state.visited);
      if (bodyEdge) generateLinear(bodyEdge.to_node, state, gens, undefined);
      state.visited = saved;
      state.indent--;

      if (endEdge) forward(nodeId, 'loop_end', state, gens, convergence);
      break;
    }

    case 'shutdown':
    case 'reboot':
    case 'logoff':
    case 'hibernate': {
      const cmdLabels: Record<string, { dsl: string; pseudo: string }> = {
        shutdown: { dsl: 'SHUTDOWN', pseudo: '关机' },
        reboot: { dsl: 'REBOOT', pseudo: '重启' },
        logoff: { dsl: 'LOGOFF', pseudo: '注销' },
        hibernate: { dsl: 'HIBERNATE', pseudo: '休眠' },
      };
      const labels = cmdLabels[node.type];
      const delay = p.delay_seconds ?? 0;
      if (gens.ifKeyword === 'IF') {
        emit(state, delay > 0 ? `${labels.dsl} ${delay}` : labels.dsl);
      } else {
        emit(state, delay > 0 ? `${labels.pseudo} (${delay}秒后)` : labels.pseudo);
      }
      forward(nodeId, 'output', state, gens, convergence);
      break;
    }

    default:
      emit(state, `# UNKNOWN: ${node.type}`);
      forward(nodeId, 'output', state, gens, convergence);
  }
}

function generateLinear(startId: string, state: GenState, gens: GenFunctions, convergence?: string): void {
  let current = startId;
  while (current && !state.visited.has(current)) {
    if (current === convergence) break;
    const node = state.nodeMap.get(current);
    if (!node) break;
    if (node.type === 'loop' && state.visited.has(current)) break;
    generateNode(current, state, gens, convergence);
    if (node.type === 'condition') {
      // Condition handler in generateNode manages own continuation (branches + convergence)
      break;
    } else {
      const next = getNextNodeId(current, state);
      if (!next || state.visited.has(next)) break;
      current = next;
    }
  }
}

function getNextNodeId(nodeId: string, state: GenState): string | undefined {
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

function forward(nodeId: string, port: string, state: GenState, gens: GenFunctions, convergence?: string): void {
  const edge = getEdge(state.outgoing, nodeId, port);
  if (edge && edge.to_node !== convergence && !state.visited.has(edge.to_node)) {
    generateNode(edge.to_node, state, gens, convergence);
  }
}

// ─── Public API ───

export function generateText(script: FlowScript, mode: TextMode = 'pseudocode'): string {
  const gens = getGens(mode);
  const state: GenState = {
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

  state.lines.push(gens.headerFormat(script.meta.name, script.meta.description));
  state.lines.push('');

  generateNode(startNode.id, state, gens, undefined);

  return state.lines.join('\n');
}
