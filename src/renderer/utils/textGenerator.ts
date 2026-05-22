/**
 * Unified Text Generator
 * Merges DSL + PseudoCode into one generator with two modes.
 * Traversal logic is shared; only per-node text generation differs.
 */

import { FlowScript, FlowNode, FlowEdge, FeatureDefinition } from '../types';

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

function dslGenClickAction(params: any): string {
  const mode = params.mode ?? 'coordinate';
  if (mode === 'image') {
    const targets = params.targets ?? [];
    return `IMAGE_CLICK ${targets.map((t: any) => `"${t.path}"`).join(' ')}`;
  }
  if (mode === 'text') {
    const text = params.text ?? '';
    const method = params.method === 'double' ? ' DOUBLE' : params.method === 'right' ? ' RIGHT' : '';
    return `OCR_FIND "${text}"${method}`;
  }
  const x = params.x ?? 0;
  const y = params.y ?? 0;
  const method = params.method ?? 'single';
  const suffix: Record<string, string> = { double: ' DOUBLE', right: ' RIGHT', middle: ' MIDDLE' };
  return `CLICK ${x} ${y}${suffix[method] || ''}`;
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

function pseudoGenClickAction(params: any): string {
  const mode = params.mode ?? 'coordinate';
  if (mode === 'image') {
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
  if (mode === 'text') {
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
  let line = `点击 (${params.x ?? 0}, ${params.y ?? 0})`;
  const method = params.method ?? 'single';
  const methodMap: Record<string, string> = { double: '双击', right: '右击', middle: '中键' };
  if (method !== 'single') line += ` ${methodMap[method] || method}`;
  if (params.delay_after && params.delay_after !== 0.5) line += ` 延迟=${params.delay_after}`;
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
  genClickAction: (p: any) => string;
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
      genClick: dslGenClick, genClickAction: dslGenClickAction, genWait: dslGenWait, genType: dslGenType,
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
    genClick: pseudoGenClick, genClickAction: pseudoGenClickAction, genWait: pseudoGenWait, genType: pseudoGenType,
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

let _featureKwMap: Record<string, string> = {};

export function _setFeatureKeywordMap(map: Record<string, string>) {
  _featureKwMap = map;
}

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

    case 'click_action':
      emit(state, gens.genClickAction(p));
      forward(nodeId, 'output', state, gens, convergence);
      break;

    case 'mouse_operation':
      emit(state, gens.genClickAction(p));
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
      const dslKw = _featureKwMap[fn] || fn.toUpperCase().replace(/_/g, '_');
      const args = p.params ? Object.entries(p.params).map(([k, v]) => `${k}=${v}`).join(' ') : '';
      emit(state, args ? `${dslKw} ${args}` : dslKw);
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

    // ── 扩展系统指令 DSL/伪代码 ──
    case 'lock_workstation': {
      if (gens.ifKeyword === 'IF') { emit(state, 'LOCK'); }
      else { emit(state, '锁定工作站'); }
      forward(nodeId, 'output', state, gens, convergence);
      break;
    }
    case 'volume_set': {
      const level = p.level ?? 50;
      if (gens.ifKeyword === 'IF') { emit(state, `VOLUME ${level}`); }
      else { emit(state, `设置音量 ${level}%`); }
      forward(nodeId, 'output', state, gens, convergence);
      break;
    }
    case 'volume_mute': {
      if (gens.ifKeyword === 'IF') {
        emit(state, p.mute === null ? 'MUTE' : p.mute ? 'MUTE ON' : 'MUTE OFF');
      } else {
        emit(state, p.mute === null ? '切换静音' : p.mute ? '静音' : '取消静音');
      }
      forward(nodeId, 'output', state, gens, convergence);
      break;
    }
    case 'launch_program': {
      const target = p.target || '';
      if (gens.ifKeyword === 'IF') {
        emit(state, p.args ? `LAUNCH ${target} ${p.args}` : `LAUNCH ${target}`);
      } else {
        emit(state, p.args ? `启动 ${target} (${p.args})` : `启动 ${target}`);
      }
      forward(nodeId, 'output', state, gens, convergence);
      break;
    }
    case 'kill_process': {
      const name = p.name || '?';
      if (gens.ifKeyword === 'IF') { emit(state, `KILL ${name}`); }
      else { emit(state, `终止进程 ${name}`); }
      forward(nodeId, 'output', state, gens, convergence);
      break;
    }
    case 'wait_process': {
      const name = p.name || '?';
      const timeout = p.timeout ?? 30;
      if (gens.ifKeyword === 'IF') {
        emit(state, p.wait_for === 'exit' ? `WAIT_EXIT ${name} ${timeout}` : `WAIT_PROCESS ${name} ${timeout}`);
      } else {
        emit(state, p.wait_for === 'exit' ? `等待进程退出 ${name} (超时${timeout}s)` : `等待进程启动 ${name} (超时${timeout}s)`);
      }
      forward(nodeId, 'output', state, gens, convergence);
      break;
    }
    case 'focus_window': {
      const title = p.title || '?';
      if (gens.ifKeyword === 'IF') { emit(state, `FOCUS ${title}`); }
      else { emit(state, `激活窗口 ${title}`); }
      forward(nodeId, 'output', state, gens, convergence);
      break;
    }
    case 'set_wallpaper': {
      const path = p.path || '?';
      if (gens.ifKeyword === 'IF') { emit(state, `WALLPAPER ${path}`); }
      else { emit(state, `设置壁纸 ${path}`); }
      forward(nodeId, 'output', state, gens, convergence);
      break;
    }
    case 'display_off': {
      if (gens.ifKeyword === 'IF') { emit(state, 'DISPLAY_OFF'); }
      else { emit(state, '关闭显示器'); }
      forward(nodeId, 'output', state, gens, convergence);
      break;
    }
    case 'system_sleep': {
      if (gens.ifKeyword === 'IF') { emit(state, 'SLEEP'); }
      else { emit(state, '睡眠'); }
      forward(nodeId, 'output', state, gens, convergence);
      break;
    }
    case 'show_desktop': {
      if (gens.ifKeyword === 'IF') { emit(state, 'DESKTOP'); }
      else { emit(state, '显示桌面'); }
      forward(nodeId, 'output', state, gens, convergence);
      break;
    }
    case 'battery_status': {
      if (gens.ifKeyword === 'IF') { emit(state, 'BATTERY'); }
      else { emit(state, '读取电池状态'); }
      forward(nodeId, 'output', state, gens, convergence);
      break;
    }
    case 'empty_recycle_bin': {
      if (gens.ifKeyword === 'IF') { emit(state, 'EMPTY_BIN'); }
      else { emit(state, '清空回收站'); }
      forward(nodeId, 'output', state, gens, convergence);
      break;
    }
    case 'display_brightness': {
      const level = p.level ?? 80;
      if (gens.ifKeyword === 'IF') { emit(state, `BRIGHTNESS ${level}`); }
      else { emit(state, `设置亮度 ${level}%`); }
      forward(nodeId, 'output', state, gens, convergence);
      break;
    }
    case 'clipboard_set': {
      const text = p.text || '';
      if (gens.ifKeyword === 'IF') { emit(state, `CLIPBOARD_SET ${text}`); }
      else { emit(state, `剪贴板写入 "${text.substring(0, 30)}${text.length > 30 ? '…' : ''}"`); }
      forward(nodeId, 'output', state, gens, convergence);
      break;
    }
    case 'clipboard_get': {
      if (gens.ifKeyword === 'IF') { emit(state, 'CLIPBOARD_GET'); }
      else { emit(state, '读取剪贴板'); }
      forward(nodeId, 'output', state, gens, convergence);
      break;
    }
    case 'system_power': {
      const l: Record<string, { dsl: string; pseudo: string }> = {
        shutdown: { dsl:'SHUTDOWN', pseudo:'关机' }, reboot: { dsl:'REBOOT', pseudo:'重启' },
        logoff: { dsl:'LOGOFF', pseudo:'注销' }, hibernate: { dsl:'HIBERNATE', pseudo:'休眠' },
        lock_workstation: { dsl:'LOCK', pseudo:'锁定工作站' }, system_sleep: { dsl:'SLEEP', pseudo:'睡眠' },
        display_off: { dsl:'DISPLAY_OFF', pseudo:'关闭显示器' },
      };
      const m = l[p.action] || { dsl:'SHUTDOWN', pseudo:'关机' };
      if (gens.ifKeyword === 'IF') emit(state, p.delay_seconds > 0 ? `${m.dsl} ${p.delay_seconds}` : m.dsl);
      else emit(state, p.delay_seconds > 0 ? `${m.pseudo} (${p.delay_seconds}秒后)` : m.pseudo);
      forward(nodeId, 'output', state, gens, convergence);
      break;
    }
    case 'audio_action': {
      if (p.action === 'volume_mute') {
        if (gens.ifKeyword === 'IF') emit(state, p.mute === null ? 'MUTE' : p.mute ? 'MUTE ON' : 'MUTE OFF');
        else emit(state, p.mute === null ? '切换静音' : p.mute ? '静音' : '取消静音');
      } else {
        if (gens.ifKeyword === 'IF') emit(state, `VOLUME ${p.level ?? 50}`);
        else emit(state, `设置音量 ${p.level ?? 50}%`);
      }
      forward(nodeId, 'output', state, gens, convergence);
      break;
    }
    case 'desktop_action': {
      const dm: Record<string, { dsl: string; pseudo: string }> = {
        show_desktop: { dsl:'DESKTOP', pseudo:'显示桌面' },
        set_wallpaper: { dsl:'WALLPAPER', pseudo:'设置壁纸' },
        empty_recycle_bin: { dsl:'EMPTY_BIN', pseudo:'清空回收站' },
      };
      const m = dm[p.action] || { dsl:'DESKTOP', pseudo:'桌面操作' };
      if (gens.ifKeyword === 'IF') emit(state, p.action === 'set_wallpaper' ? `${m.dsl} ${p.path || ''}`.trim() : m.dsl);
      else emit(state, p.action === 'set_wallpaper' ? `${m.pseudo}: ${p.path || ''}`.trim() : m.pseudo);
      forward(nodeId, 'output', state, gens, convergence);
      break;
    }
    case 'clipboard_action': {
      const cm: Record<string, { dsl: string; pseudo: string }> = {
        set: { dsl:'CLIPBOARD_SET', pseudo:'剪贴板写入' },
        get: { dsl:'CLIPBOARD_GET', pseudo:'读取剪贴板' },
        get_image: { dsl:'CLIPBOARD_GET_IMAGE', pseudo:'获取剪贴板图片' },
        get_files: { dsl:'CLIPBOARD_GET_FILES', pseudo:'获取剪贴板文件列表' },
        clear: { dsl:'CLIPBOARD_CLEAR', pseudo:'清空剪贴板' },
      };
      const m = cm[p.action] || { dsl:'CLIPBOARD', pseudo:'剪贴板操作' };
      if (gens.ifKeyword === 'IF') emit(state, p.action === 'set' ? `${m.dsl} ${p.text || ''}` : m.dsl);
      else emit(state, p.action === 'set' ? `${m.pseudo} "${(p.text||'').substring(0,30)}${p.text?.length>30?'…':''}"` : m.pseudo);
      forward(nodeId, 'output', state, gens, convergence);
      break;
    }
    case 'dialog_action': {
      const dm: Record<string, { dsl: string; pseudo: string }> = {
        handle: { dsl:'DIALOG_HANDLE', pseudo:'处理对话框' },
        list: { dsl:'DIALOG_LIST', pseudo:'列出对话框' },
        take_action: { dsl:'DIALOG_ACTION', pseudo:'操作对话框' },
        fill_save: { dsl:'DIALOG_SAVE', pseudo:'填写保存对话框' },
      };
      const m = dm[p.action] || { dsl:'DIALOG', pseudo:'对话框操作' };
      if (gens.ifKeyword === 'IF') emit(state, p.action === 'fill_save' ? `${m.dsl} ${p.path || ''}`.trim() : p.action === 'take_action' ? `${m.dsl} ${p.button || ''}`.trim() : m.dsl);
      else emit(state, p.action === 'fill_save' ? `${m.pseudo}: ${p.path || ''}`.trim() : p.action === 'take_action' ? `${m.pseudo}: ${p.button || ''}`.trim() : m.pseudo);
      forward(nodeId, 'output', state, gens, convergence);
      break;
    }
    case 'a11y_action': {
      const am: Record<string, { dsl: string; pseudo: string }> = {
        get_tree: { dsl:'A11Y_TREE', pseudo:'获取无障碍树' },
        find_element: { dsl:'A11Y_FIND', pseudo:'查找元素' },
        click_element: { dsl:'A11Y_CLICK', pseudo:'点击元素' },
      };
      const m2 = am[p.action] || { dsl:'A11Y', pseudo:'无障碍操作' };
      if (gens.ifKeyword === 'IF') emit(state, p.action !== 'get_tree' ? `${m2.dsl} ${p.name || ''}`.trim() : m2.dsl);
      else emit(state, p.action !== 'get_tree' ? `${m2.pseudo}: ${p.name || ''}`.trim() : m2.pseudo);
      forward(nodeId, 'output', state, gens, convergence);
      break;
    }
    case 'wifi_action': {
      const wm: Record<string, { dsl: string; pseudo: string }> = {
        list: { dsl:'WIFI_LIST', pseudo:'列出WiFi' },
        connect: { dsl:'WIFI_CONNECT', pseudo:'连接WiFi' },
        disconnect: { dsl:'WIFI_DISCONNECT', pseudo:'断开WiFi' },
      };
      const m3 = wm[p.action] || { dsl:'WIFI', pseudo:'WiFi操作' };
      if (gens.ifKeyword === 'IF') emit(state, p.action === 'connect' ? `${m3.dsl} ${p.ssid || ''}`.trim() : m3.dsl);
      else emit(state, p.action === 'connect' ? `${m3.pseudo}: ${p.ssid || ''}`.trim() : m3.pseudo);
      forward(nodeId, 'output', state, gens, convergence);
      break;
    }
    case 'process_action': {
      if (p.action === 'launch') {
        const target = p.target || '';
        if (gens.ifKeyword === 'IF') emit(state, target ? `LAUNCH ${target}` : 'LAUNCH');
        else emit(state, target ? `启动 ${target}` : '启动程序');
      } else if (p.action === 'kill') {
        if (gens.ifKeyword === 'IF') emit(state, `KILL ${p.name || ''}`);
        else emit(state, `终止进程 ${p.name || ''}`);
      } else if (p.action === 'wait_start') {
        if (gens.ifKeyword === 'IF') emit(state, `WAIT_PROCESS ${p.name || ''} ${p.timeout ?? 30}`);
        else emit(state, `等待进程启动 ${p.name || ''} (超时${p.timeout ?? 30}s)`);
      } else if (p.action === 'wait_exit') {
        if (gens.ifKeyword === 'IF') emit(state, `WAIT_EXIT ${p.name || ''} ${p.timeout ?? 30}`);
        else emit(state, `等待进程退出 ${p.name || ''} (超时${p.timeout ?? 30}s)`);
      } else if (p.action === 'focus') {
        if (gens.ifKeyword === 'IF') emit(state, `FOCUS ${p.title || ''}`);
        else emit(state, `激活窗口 ${p.title || ''}`);
      }
      forward(nodeId, 'output', state, gens, convergence);
      break;
    }
    case 'window_control': {
      const opLabels: Record<string, string> = { minimize: '最小化', maximize: '最大化', restore: '还原', close: '关闭', resize: '调整大小', snap: '贴靠' };
      const opDsl: Record<string, string> = { minimize: 'MINIMIZE', maximize: 'MAXIMIZE', restore: 'RESTORE_WINDOW', close: 'CLOSE_WINDOW', resize: 'RESIZE_WINDOW', snap: 'SNAP_WINDOW' };
      const title = p.title || '';
      if (gens.ifKeyword === 'IF') {
        if (p.operation === 'resize') emit(state, `${opDsl[p.operation] || 'MINIMIZE'} ${p.width||800} ${p.height||600} ${title}`.trim());
        else if (p.operation === 'snap') emit(state, `${opDsl[p.operation]} ${p.snap_position||'left'}${title ? ' ' + title : ''}`);
        else emit(state, `${opDsl[p.operation] || 'MINIMIZE'}${title ? ' ' + title : ''}`);
      } else {
        if (p.operation === 'snap') emit(state, `窗口贴靠${p.snap_position||'左'}${title ? ' ' + title : ''}`);
        else emit(state, `窗口${opLabels[p.operation] || '最小化'}${title ? ' ' + title : ''}`);
      }
      forward(nodeId, 'output', state, gens, convergence);
      break;
    }
    case 'web_browser': {
      const actionLabels: Record<string, string> = { navigate: '导航', click: '点击', input_text: '输入', screenshot: '截图', wait: '等待', extract: '提取', scroll: '滚动' };
      const actionLabel = actionLabels[(p as any).action] || (p as any).action || '未知';
      const target = (p as any).action === 'navigate' ? ((p as any).url || '').substring(0, 40) : ((p as any).selector || '');
      if (gens.ifKeyword === 'IF') {
        const dslAction: Record<string, string> = { navigate: 'BROWSER_NAV', click: 'BROWSER_CLICK', input_text: 'BROWSER_TYPE', screenshot: 'BROWSER_SCREENSHOT', wait: 'BROWSER_WAIT', extract: 'BROWSER_TEXT', scroll: 'BROWSER_SCROLL' };
        emit(state, `${dslAction[(p as any).action] || 'BROWSER'} ${target}`);
      } else {
        emit(state, `浏览器${actionLabel} ${target}`);
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
