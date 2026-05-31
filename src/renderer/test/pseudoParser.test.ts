import { describe, it, expect } from 'vitest';
import { parsePseudoToFlow } from '../utils/pseudoParser';

describe('pseudoParser', () => {
  it('parses an empty pseudocode into start-end flow', () => {
    const flow = parsePseudoToFlow('');
    expect(flow.nodes).toHaveLength(2); // start + end
    expect(flow.nodes[0].type).toBe('start');
    expect(flow.nodes[1].type).toBe('end');
    expect(flow.edges).toHaveLength(1);
  });

  it('parses simple click', () => {
    const flow = parsePseudoToFlow('click (100, 200)');
    const clickNodes = flow.nodes.filter(n => n.type === 'mouse_operation');
    expect(clickNodes).toHaveLength(1);
    expect(clickNodes[0].params).toMatchObject({ x: 100, y: 200, method: 'single' });
  });

  it('parses Chinese click', () => {
    const flow = parsePseudoToFlow('点击 (300, 400)');
    const clickNodes = flow.nodes.filter(n => n.type === 'mouse_operation');
    expect(clickNodes).toHaveLength(1);
    expect(clickNodes[0].params).toMatchObject({ x: 300, y: 400 });
  });

  it('parses click with double method', () => {
    const flow = parsePseudoToFlow('click (100, 200) double');
    const n = flow.nodes.find(n => n.type === 'mouse_operation');
    expect(n?.params).toMatchObject({ x: 100, y: 200, method: 'double' });
  });

  it('parses type text', () => {
    const flow = parsePseudoToFlow('type "hello world"');
    const inputNodes = flow.nodes.filter(n => n.type === 'input_text');
    expect(inputNodes).toHaveLength(1);
    expect((inputNodes[0].params as any).text).toBe('hello world');
  });

  it('parses Chinese type', () => {
    const flow = parsePseudoToFlow('输入 "你好世界"');
    const n = flow.nodes.find(n => n.type === 'input_text');
    expect((n?.params as any).text).toBe('你好世界');
  });

  it('parses press key', () => {
    const flow = parsePseudoToFlow('press ctrl+alt+v');
    const n = flow.nodes.find(n => n.type === 'keyboard');
    expect(n).toBeDefined();
    expect((n!.params as any).keys).toContain('v');
    expect((n!.params as any).modifiers).toContain('ctrl');
    expect((n!.params as any).modifiers).toContain('alt');
  });

  it('parses Chinese press key', () => {
    const flow = parsePseudoToFlow('按键 enter');
    const n = flow.nodes.find(n => n.type === 'keyboard');
    expect((n?.params as any).keys).toContain('enter');
  });

  it('parses wait', () => {
    const flow = parsePseudoToFlow('wait 5');
    const n = flow.nodes.find(n => n.type === 'wait');
    expect(n?.params).toMatchObject({ wait_type: 'time', duration: 5 });
  });

  it('parses Chinese wait', () => {
    const flow = parsePseudoToFlow('等待 3');
    const n = flow.nodes.find(n => n.type === 'wait');
    expect(n?.params).toMatchObject({ wait_type: 'time', duration: 3 });
  });

  it('parses screenshot', () => {
    const flow = parsePseudoToFlow('screenshot');
    expect(flow.nodes.some(n => n.type === 'screenshot')).toBe(true);
  });

  it('parses Chinese screenshot', () => {
    const flow = parsePseudoToFlow('截图');
    expect(flow.nodes.some(n => n.type === 'screenshot')).toBe(true);
  });

  it('parses loop with body', () => {
    const pseudo = `loop 3:
      click (100, 200)`;
    const flow = parsePseudoToFlow(pseudo);
    const loopNodes = flow.nodes.filter(n => n.type === 'loop');
    expect(loopNodes).toHaveLength(1);
    expect(loopNodes[0].params).toMatchObject({ loop_type: 'count', count: 3 });
    const clickNodes = flow.nodes.filter(n => n.type === 'mouse_operation');
    expect(clickNodes).toHaveLength(1);
  });

  it('parses Chinese loop', () => {
    const pseudo = `循环 5:
      等待 2`;
    const flow = parsePseudoToFlow(pseudo);
    expect(flow.nodes.filter(n => n.type === 'loop')).toHaveLength(1);
    expect(flow.nodes.some(n => n.type === 'wait')).toBe(true);
  });

  it('parses if/else condition', () => {
    const pseudo = `if true:
        click (100, 200)
      else:
        wait 2`;
    const flow = parsePseudoToFlow(pseudo);
    const condNodes = flow.nodes.filter(n => n.type === 'condition');
    expect(condNodes).toHaveLength(1);
    // Both branches should have nodes
    expect(flow.nodes.filter(n => n.type === 'mouse_operation')).toHaveLength(1);
    expect(flow.nodes.filter(n => n.type === 'wait')).toHaveLength(1);
  });

  it('parses Chinese if/else', () => {
    const pseudo = `如果 true:
        截图
      否则:
        等待 5`;
    const flow = parsePseudoToFlow(pseudo);
    expect(flow.nodes.filter(n => n.type === 'condition')).toHaveLength(1);
    expect(flow.nodes.filter(n => n.type === 'screenshot')).toHaveLength(1);
    expect(flow.nodes.filter(n => n.type === 'wait')).toHaveLength(1);
  });

  it('parses indented complex flow', () => {
    const pseudo = `点击 (500, 400)
      输入 "admin"
      按键 Tab
      输入 "password123"
      点击 (500, 550) 双击
      等待 3
      截图`;
    const flow = parsePseudoToFlow(pseudo);
    const clickNodes = flow.nodes.filter(n => n.type === 'mouse_operation');
    const inputNodes = flow.nodes.filter(n => n.type === 'input_text');
    const keyboardNodes = flow.nodes.filter(n => n.type === 'keyboard');
    const waitNodes = flow.nodes.filter(n => n.type === 'wait');
    const screenshotNodes = flow.nodes.filter(n => n.type === 'screenshot');
    expect(clickNodes).toHaveLength(2);
    expect(inputNodes).toHaveLength(2);
    expect(keyboardNodes).toHaveLength(1);
    expect(waitNodes).toHaveLength(1);
    expect(screenshotNodes).toHaveLength(1);
    // Total nodes: start + 7 action + end = 9
    expect(flow.nodes).toHaveLength(9);
  });

  it('generates valid edges for linear flow', () => {
    const flow = parsePseudoToFlow('click (100, 200)\nwait 2\nscreenshot');
    const nodeIds = new Set(flow.nodes.map(n => n.id));
    for (const edge of flow.edges) {
      expect(nodeIds.has(edge.from_node)).toBe(true);
      expect(nodeIds.has(edge.to_node)).toBe(true);
    }
  });

  it('ignores comments', () => {
    const pseudo = `# 这是一个注释
      click (100, 200)
      # 另一个注释`;
    const flow = parsePseudoToFlow(pseudo);
    const clickNodes = flow.nodes.filter(n => n.type === 'mouse_operation');
    expect(clickNodes).toHaveLength(1);
    // start + click + end = 3
    expect(flow.nodes).toHaveLength(3);
  });

  it('parses image_click', () => {
    const flow = parsePseudoToFlow('click image "icon.png" similarity=85 timeout=3');
    const n = flow.nodes.find(n => n.type === 'mouse_operation');
    expect(n).toBeDefined();
    expect((n!.params as any).targets).toHaveLength(1);
    expect((n!.params as any).targets[0].path).toBe('icon.png');
    expect((n!.params as any).similarity).toBe(85);
    expect((n!.params as any).timeout).toBe(3);
  });

  it('parses Chinese image_click', () => {
    const flow = parsePseudoToFlow('点击图片 "button.png" 相似度=90');
    const n = flow.nodes.find(n => n.type === 'mouse_operation');
    expect(n).toBeDefined();
    expect((n!.params as any).targets[0].path).toBe('button.png');
  });

  it('parses swipe', () => {
    const flow = parsePseudoToFlow('swipe (100, 500) to (100, 200) duration=1');
    const n = flow.nodes.find(n => n.type === 'swipe');
    expect(n).toBeDefined();
    expect(n!.params).toMatchObject({ start_x: 100, start_y: 500, end_x: 100, end_y: 200, duration: 1 });
  });

  it('parses Chinese swipe', () => {
    const flow = parsePseudoToFlow('滑动 (200, 600) 到 (200, 300)');
    const n = flow.nodes.find(n => n.type === 'swipe');
    expect(n).toBeDefined();
    expect((n!.params as any).start_x).toBe(200);
  });

  it('parses click_text', () => {
    const flow = parsePseudoToFlow('click_text "提交" similarity=90');
    const n = flow.nodes.find(n => n.type === 'mouse_operation');
    expect((n?.params as any).text).toBe('提交');
    expect((n?.params as any).similarity).toBe(90);
  });

  it('parses popup_close', () => {
    const flow = parsePseudoToFlow('popup_close');
    expect(flow.nodes.some(n => n.type === 'popup_close')).toBe(true);
  });

  it('parses infinite loop', () => {
    const flow = parsePseudoToFlow('无限循环:\n  截图');
    const n = flow.nodes.find(n => n.type === 'loop');
    expect(n?.params).toMatchObject({ loop_type: 'infinite' });
  });

  it('parses launch and focus', () => {
    const flow = parsePseudoToFlow('launch "notepad"\nfocus "无标题 - 记事本"');
    expect(flow.nodes.some(n => n.type === 'process_action' && (n.params as any)?.action === 'launch')).toBe(true);
    expect(flow.nodes.some(n => n.type === 'process_action' && (n.params as any)?.action === 'focus')).toBe(true);
  });

  it('handles mixed Chinese and English', () => {
    const pseudo = `click (500, 400)
      等待 2
      type "admin"
      按键 enter
      截图`;
    const flow = parsePseudoToFlow(pseudo);
    expect(flow.nodes.filter(n => n.type === 'mouse_operation')).toHaveLength(1);
    expect(flow.nodes.filter(n => n.type === 'wait')).toHaveLength(1);
    expect(flow.nodes.filter(n => n.type === 'input_text')).toHaveLength(1);
    expect(flow.nodes.filter(n => n.type === 'keyboard')).toHaveLength(1);
    expect(flow.nodes.filter(n => n.type === 'screenshot')).toHaveLength(1);
  });
});
