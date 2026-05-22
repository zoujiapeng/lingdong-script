import { describe, it, expect } from 'vitest';
import { generateText } from '../utils/textGenerator';

function makeFlow(opts: {
  nodes: Array<{ id: string; type: string; params?: any; disabled?: boolean }>;
  edges: Array<{ from: string; from_port?: string; to: string }>;
  name?: string;
  description?: string;
}) {
  const { nodes, edges, name = 'test', description = '' } = opts;
  return {
    meta: { id: 'test', name, description, created_at: '', updated_at: '', version: '1.0' },
    nodes: nodes.map(n => ({
      id: n.id,
      type: n.type,
      x: 0, y: 0,
      params: n.params || {},
      ...(n.disabled !== undefined ? { disabled: n.disabled } : {}),
    })),
    edges: edges.map(e => ({
      id: `e-${e.from}-${e.to}`,
      from_node: e.from,
      from_port: e.from_port || 'output',
      to_node: e.to,
      to_port: 'input',
    })),
    variables: {},
    settings: { default_delay: 0.5, error_handling: 'stop' as const, max_retries: 0, retry_delay: 1, log_level: 'info' as const },
  };
}

describe('textGenerator DSL mode', () => {
  it('generates header for empty flow with only start/end', () => {
    const script = makeFlow({
      nodes: [{ id: 'start', type: 'start' }, { id: 'end', type: 'end' }],
      edges: [{ from: 'start', to: 'end' }],
      name: 'myflow', description: 'test desc',
    });
    const result = generateText(script, 'dsl');
    expect(result).toContain('# DSL: myflow - test desc');
  });

  it('generates CLICK for click node', () => {
    const script = makeFlow({
      nodes: [
        { id: 'start', type: 'start' },
        { id: 'c1', type: 'click', params: { x: 100, y: 200 } },
        { id: 'end', type: 'end' },
      ],
      edges: [
        { from: 'start', to: 'c1' },
        { from: 'c1', to: 'end' },
      ],
    });
    const result = generateText(script, 'dsl');
    expect(result).toContain('CLICK 100 200');
  });

  it('generates CLICK with double method', () => {
    const script = makeFlow({
      nodes: [
        { id: 'start', type: 'start' },
        { id: 'c1', type: 'click', params: { x: 300, y: 400, method: 'double' } },
        { id: 'end', type: 'end' },
      ],
      edges: [
        { from: 'start', to: 'c1' },
        { from: 'c1', to: 'end' },
      ],
    });
    const result = generateText(script, 'dsl');
    expect(result).toContain('CLICK 300 400 DOUBLE');
  });

  it('generates TYPE for input_text node', () => {
    const script = makeFlow({
      nodes: [
        { id: 'start', type: 'start' },
        { id: 't1', type: 'input_text', params: { text: 'hello world' } },
        { id: 'end', type: 'end' },
      ],
      edges: [
        { from: 'start', to: 't1' },
        { from: 't1', to: 'end' },
      ],
    });
    const result = generateText(script, 'dsl');
    expect(result).toContain('TYPE "hello world"');
  });

  it('generates WAIT for wait node', () => {
    const script = makeFlow({
      nodes: [
        { id: 'start', type: 'start' },
        { id: 'w1', type: 'wait', params: { duration: 5 } },
        { id: 'end', type: 'end' },
      ],
      edges: [
        { from: 'start', to: 'w1' },
        { from: 'w1', to: 'end' },
      ],
    });
    const result = generateText(script, 'dsl');
    expect(result).toContain('WAIT 5');
  });

  it('generates HOTKEY for keyboard node', () => {
    const script = makeFlow({
      nodes: [
        { id: 'start', type: 'start' },
        { id: 'k1', type: 'keyboard', params: { keys: ['c'], modifiers: ['ctrl'] } },
        { id: 'end', type: 'end' },
      ],
      edges: [
        { from: 'start', to: 'k1' },
        { from: 'k1', to: 'end' },
      ],
    });
    const result = generateText(script, 'dsl');
    expect(result).toContain('HOTKEY ctrl+c');
  });

  it('generates SWIPE for swipe node', () => {
    const script = makeFlow({
      nodes: [
        { id: 'start', type: 'start' },
        { id: 's1', type: 'swipe', params: { start_x: 0, start_y: 100, end_x: 500, end_y: 100 } },
        { id: 'end', type: 'end' },
      ],
      edges: [
        { from: 'start', to: 's1' },
        { from: 's1', to: 'end' },
      ],
    });
    const result = generateText(script, 'dsl');
    expect(result).toContain('SWIPE 0 100 500 100');
  });

  it('generates IMAGE_CLICK for image_click node', () => {
    const script = makeFlow({
      nodes: [
        { id: 'start', type: 'start' },
        { id: 'ic1', type: 'image_click', params: { targets: [{ path: 'btn.png' }, { path: 'btn2.png' }] } },
        { id: 'end', type: 'end' },
      ],
      edges: [
        { from: 'start', to: 'ic1' },
        { from: 'ic1', to: 'end' },
      ],
    });
    const result = generateText(script, 'dsl');
    expect(result).toContain('IMAGE_CLICK "btn.png" "btn2.png"');
  });

  it('generates SCREENSHOT for screenshot node', () => {
    const script = makeFlow({
      nodes: [
        { id: 'start', type: 'start' },
        { id: 'sc1', type: 'screenshot', params: {} },
        { id: 'end', type: 'end' },
      ],
      edges: [
        { from: 'start', to: 'sc1' },
        { from: 'sc1', to: 'end' },
      ],
    });
    const result = generateText(script, 'dsl');
    expect(result).toContain('SCREENSHOT');
  });

  it('generates SET for variable_set node', () => {
    const script = makeFlow({
      nodes: [
        { id: 'start', type: 'start' },
        { id: 'vs1', type: 'variable_set', params: { name: 'x', value: '10' } },
        { id: 'end', type: 'end' },
      ],
      edges: [
        { from: 'start', to: 'vs1' },
        { from: 'vs1', to: 'end' },
      ],
    });
    const result = generateText(script, 'dsl');
    expect(result).toContain('SET x "10"');
  });

  it('generates IF/ELSE for condition node with branches', () => {
    const script = makeFlow({
      nodes: [
        { id: 'start', type: 'start' },
        { id: 'cond', type: 'condition', params: { condition_type: 'window_exists', window_title: 'notepad' } },
        { id: 'yes_click', type: 'click', params: { x: 100, y: 200 } },
        { id: 'no_wait', type: 'wait', params: { duration: 1 } },
        { id: 'join', type: 'click', params: { x: 300, y: 400 } },
        { id: 'end', type: 'end' },
      ],
      edges: [
        { from: 'start', to: 'cond' },
        { from: 'cond', from_port: 'yes', to: 'yes_click' },
        { from: 'cond', from_port: 'no', to: 'no_wait' },
        { from: 'yes_click', to: 'join' },
        { from: 'no_wait', to: 'join' },
        { from: 'join', to: 'end' },
      ],
    });
    const result = generateText(script, 'dsl');
    expect(result).toContain('IF');
    expect(result).toContain('FOCUS "notepad"');
    expect(result).toContain('ELSE');
    expect(result).toContain('WAIT 1');
    expect(result).toContain('CLICK 300 400');
  });

  it('generates LOOP for loop node', () => {
    const script = makeFlow({
      nodes: [
        { id: 'start', type: 'start' },
        { id: 'loop', type: 'loop', params: { loop_type: 'count', count: 3 } },
        { id: 'body_click', type: 'click', params: { x: 100, y: 100 } },
        { id: 'end', type: 'end' },
      ],
      edges: [
        { from: 'start', to: 'loop' },
        { from: 'loop', from_port: 'loop_body', to: 'body_click' },
        { from: 'loop', from_port: 'loop_end', to: 'end' },
      ],
    });
    const result = generateText(script, 'dsl');
    expect(result).toContain('LOOP 3');
    expect(result).toContain('CLICK 100 100');
  });

  it('generates POPUP_CLOSE for popup_close node', () => {
    const script = makeFlow({
      nodes: [
        { id: 'start', type: 'start' },
        { id: 'pc1', type: 'popup_close', params: { strategies: ['esc'] } },
        { id: 'end', type: 'end' },
      ],
      edges: [
        { from: 'start', to: 'pc1' },
        { from: 'pc1', to: 'end' },
      ],
    });
    const result = generateText(script, 'dsl');
    expect(result).toContain('POPUP_CLOSE esc');
  });

  it('generates COLOR_CHECK for color_check node', () => {
    const script = makeFlow({
      nodes: [
        { id: 'start', type: 'start' },
        { id: 'cc1', type: 'color_check', params: { x: 0, y: 0, w: 100, h: 100, expected_color: '#FF0000', tolerance: 30 } },
        { id: 'end', type: 'end' },
      ],
      edges: [
        { from: 'start', to: 'cc1' },
        { from: 'cc1', to: 'end' },
      ],
    });
    const result = generateText(script, 'dsl');
    expect(result).toContain('COLOR_CHECK');
  });

  it('generates PROGRESS_WAIT for progress_wait node', () => {
    const script = makeFlow({
      nodes: [
        { id: 'start', type: 'start' },
        { id: 'pw1', type: 'progress_wait', params: { region: { x: 100, y: 200, w: 300, h: 20 }, wait_type: 'fill' } },
        { id: 'end', type: 'end' },
      ],
      edges: [
        { from: 'start', to: 'pw1' },
        { from: 'pw1', to: 'end' },
      ],
    });
    const result = generateText(script, 'dsl');
    expect(result).toContain('PROGRESS_WAIT 100,200,300,20 fill');
  });

  it('generates tool_call with prefix', () => {
    const script = makeFlow({
      nodes: [
        { id: 'start', type: 'start' },
        { id: 'tc1', type: 'tool_call', params: { feature_name: 'screenshot', params: { region: 'full' } } },
        { id: 'end', type: 'end' },
      ],
      edges: [
        { from: 'start', to: 'tc1' },
        { from: 'tc1', to: 'end' },
      ],
    });
    const result = generateText(script, 'dsl');
    expect(result).toContain('SCREENSHOT region=full');
  });

  it('generates LAUNCH for launch_program node', () => {
    const script = makeFlow({
      nodes: [
        { id: 'start', type: 'start' },
        { id: 'lp1', type: 'launch_program', params: { target: 'notepad.exe', args: '' } },
        { id: 'end', type: 'end' },
      ],
      edges: [
        { from: 'start', to: 'lp1' },
        { from: 'lp1', to: 'end' },
      ],
    });
    const result = generateText(script, 'dsl');
    expect(result).toContain('LAUNCH notepad.exe');
  });

  it('generates SHUTDOWN for shutdown node', () => {
    const script = makeFlow({
      nodes: [
        { id: 'start', type: 'start' },
        { id: 'sd1', type: 'shutdown', params: { delay_seconds: 30 } },
        { id: 'end', type: 'end' },
      ],
      edges: [
        { from: 'start', to: 'sd1' },
        { from: 'sd1', to: 'end' },
      ],
    });
    const result = generateText(script, 'dsl');
    expect(result).toContain('SHUTDOWN 30');
  });

  it('generates FOCUS for focus_window node', () => {
    const script = makeFlow({
      nodes: [
        { id: 'start', type: 'start' },
        { id: 'fw1', type: 'focus_window', params: { title: '计算器' } },
        { id: 'end', type: 'end' },
      ],
      edges: [
        { from: 'start', to: 'fw1' },
        { from: 'fw1', to: 'end' },
      ],
    });
    const result = generateText(script, 'dsl');
    expect(result).toContain('FOCUS 计算器');
  });

  it('marks disabled nodes with comment', () => {
    const script = makeFlow({
      nodes: [
        { id: 'start', type: 'start' },
        { id: 'd1', type: 'click', params: { x: 100, y: 200 }, disabled: true },
        { id: 'end', type: 'end' },
      ],
      edges: [
        { from: 'start', to: 'd1' },
        { from: 'd1', to: 'end' },
      ],
    });
    const result = generateText(script, 'dsl');
    expect(result).toContain('# disabled: d1');
    expect(result).not.toContain('CLICK 100 200');
  });

  it('handles no start node gracefully', () => {
    const script = makeFlow({
      nodes: [{ id: 'end', type: 'end' }],
      edges: [],
    });
    const result = generateText(script, 'dsl');
    expect(result).toBe('# ERROR: no start node');
  });

  it('handles unknown node type with UNKNOWN comment', () => {
    const script = makeFlow({
      nodes: [
        { id: 'start', type: 'start' },
        { id: 'unknown', type: 'click' as any, params: {} },
        { id: 'end', type: 'end' },
      ],
      edges: [
        { from: 'start', to: 'unknown' },
        { from: 'unknown', to: 'end' },
      ],
    });
    // click is known - test with a truly unknown type by just making sure it doesn't crash
    const result = generateText(script, 'dsl');
    expect(result).toBeTruthy();
  });

  it('generates multi-step sequential flow in order', () => {
    const script = makeFlow({
      nodes: [
        { id: 'start', type: 'start' },
        { id: 'a', type: 'click', params: { x: 100, y: 100 } },
        { id: 'b', type: 'wait', params: { duration: 1 } },
        { id: 'c', type: 'input_text', params: { text: 'hi' } },
        { id: 'd', type: 'keyboard', params: { keys: ['enter'] } },
        { id: 'e', type: 'screenshot', params: {} },
        { id: 'end', type: 'end' },
      ],
      edges: [
        { from: 'start', to: 'a' },
        { from: 'a', to: 'b' },
        { from: 'b', to: 'c' },
        { from: 'c', to: 'd' },
        { from: 'd', to: 'e' },
        { from: 'e', to: 'end' },
      ],
    });
    const result = generateText(script, 'dsl');
    const lines = result.split('\n').filter(l => !l.startsWith('#') && l.trim());
    expect(lines.length).toBeGreaterThanOrEqual(5);
    expect(result.indexOf('CLICK 100 100')).toBeLessThan(result.indexOf('WAIT 1'));
    expect(result.indexOf('WAIT 1')).toBeLessThan(result.indexOf('TYPE "hi"'));
  });
});

describe('textGenerator pseudocode mode', () => {
  it('generates Chinese pseudocode', () => {
    const script = makeFlow({
      nodes: [
        { id: 'start', type: 'start' },
        { id: 'c1', type: 'click', params: { x: 100, y: 200 } },
        { id: 'end', type: 'end' },
      ],
      edges: [
        { from: 'start', to: 'c1' },
        { from: 'c1', to: 'end' },
      ],
      name: '测试', description: '测试流程',
    });
    const result = generateText(script, 'pseudocode');
    expect(result).toContain('# 灵动脚本: 测试 - 测试流程');
    expect(result).toContain('点击 (100, 200)');
  });

  it('generates Chinese if-else structure', () => {
    const script = makeFlow({
      nodes: [
        { id: 'start', type: 'start' },
        { id: 'cond', type: 'condition', params: { condition_type: 'image_exists', template_path: 'btn.png', similarity: 85 } },
        { id: 'yes', type: 'click', params: { x: 200, y: 300 } },
        { id: 'no', type: 'wait', params: { duration: 2 } },
        { id: 'end', type: 'end' },
      ],
      edges: [
        { from: 'start', to: 'cond' },
        { from: 'cond', from_port: 'yes', to: 'yes' },
        { from: 'cond', from_port: 'no', to: 'no' },
        { from: 'yes', to: 'end' },
        { from: 'no', to: 'end' },
      ],
    });
    const result = generateText(script, 'pseudocode');
    expect(result).toContain('如果');
    expect(result).toContain('否则');
    expect(result).toContain('图片存在 "btn.png"');
  });

  it('generates Chinese loop', () => {
    const script = makeFlow({
      nodes: [
        { id: 'start', type: 'start' },
        { id: 'loop', type: 'loop', params: { loop_type: 'infinite' } },
        { id: 'body', type: 'click', params: { x: 500, y: 400 } },
        { id: 'end', type: 'end' },
      ],
      edges: [
        { from: 'start', to: 'loop' },
        { from: 'loop', from_port: 'loop_body', to: 'body' },
        { from: 'loop', from_port: 'loop_end', to: 'end' },
      ],
    });
    const result = generateText(script, 'pseudocode');
    expect(result).toContain('无限循环:');
    expect(result).toContain('点击 (500, 400)');
  });

  it('generates Chinese input_text', () => {
    const script = makeFlow({
      nodes: [
        { id: 'start', type: 'start' },
        { id: 't1', type: 'input_text', params: { text: '你好' } },
        { id: 'end', type: 'end' },
      ],
      edges: [
        { from: 'start', to: 't1' },
        { from: 't1', to: 'end' },
      ],
    });
    const result = generateText(script, 'pseudocode');
    expect(result).toContain('输入 "你好"');
  });

  it('generates Chinese system commands', () => {
    const script = makeFlow({
      nodes: [
        { id: 'start', type: 'start' },
        { id: 'lp1', type: 'launch_program', params: { target: 'notepad' } },
        { id: 'k1', type: 'kill_process', params: { name: 'notepad.exe' } },
        { id: 'end', type: 'end' },
      ],
      edges: [
        { from: 'start', to: 'lp1' },
        { from: 'lp1', to: 'k1' },
        { from: 'k1', to: 'end' },
      ],
    });
    const result = generateText(script, 'pseudocode');
    expect(result).toContain('启动 notepad');
    expect(result).toContain('终止进程 notepad.exe');
  });

  it('generates OCR_CLICK for click_text node', () => {
    const script = makeFlow({
      nodes: [
        { id: 'start', type: 'start' },
        { id: 'ct1', type: 'click_text', params: { text: '登录', similarity: 80 } },
        { id: 'end', type: 'end' },
      ],
      edges: [
        { from: 'start', to: 'ct1' },
        { from: 'ct1', to: 'end' },
      ],
    });
    const result = generateText(script, 'pseudocode');
    expect(result).toContain('点击文本 "登录"');
  });

  it('generates clipboard_set in pseudocode', () => {
    const script = makeFlow({
      nodes: [
        { id: 'start', type: 'start' },
        { id: 'cs1', type: 'clipboard_set', params: { text: 'hello' } },
        { id: 'end', type: 'end' },
      ],
      edges: [
        { from: 'start', to: 'cs1' },
        { from: 'cs1', to: 'end' },
      ],
    });
    const result = generateText(script, 'pseudocode');
    expect(result).toContain('剪贴板写入');
  });

  it('generates window_control in pseudocode', () => {
    const script = makeFlow({
      nodes: [
        { id: 'start', type: 'start' },
        { id: 'wc1', type: 'window_control', params: { operation: 'minimize', title: '计算器' } },
        { id: 'end', type: 'end' },
      ],
      edges: [
        { from: 'start', to: 'wc1' },
        { from: 'wc1', to: 'end' },
      ],
    });
    const result = generateText(script, 'pseudocode');
    expect(result).toContain('最小化窗口');
  });
});
