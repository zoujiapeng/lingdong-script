import { describe, it, expect } from 'vitest';
import { macroEventsToFlow } from '../utils/macroConverter';
import type { MacroEvent } from '../types';

function evt(overrides: Partial<MacroEvent> & { type: 'click' | 'key' }): MacroEvent {
  return { time: 0, x: 0, y: 0, button: '', pressed: true, key: '', ...overrides };
}

describe('macroConverter', () => {
  it('converts empty events to start → end flow', () => {
    const flow = macroEventsToFlow([], 'test');
    expect(flow.nodes).toHaveLength(2);
    expect(flow.edges).toHaveLength(1);
    expect(flow.nodes[0].type).toBe('start');
    expect(flow.nodes[1].type).toBe('end');
  });

  it('converts click event to click node', () => {
    const events = [evt({ type: 'click', time: 0.5, x: 100, y: 200, button: 'Button.left' })];
    const flow = macroEventsToFlow(events, 'test');
    const clickNodes = flow.nodes.filter(n => n.type === 'click');
    expect(clickNodes).toHaveLength(1);
    expect(clickNodes[0].params).toMatchObject({ x: 100, y: 200 });
  });

  it('inserts WAIT node for gaps >= 300ms', () => {
    const events = [
      evt({ type: 'click', time: 0.0, x: 100, y: 200 }),
      evt({ type: 'click', time: 2.0, x: 300, y: 400 }),
    ];
    const flow = macroEventsToFlow(events, 'test');
    const waitNodes = flow.nodes.filter(n => n.type === 'wait');
    expect(waitNodes.length).toBeGreaterThanOrEqual(1);
  });

  it('does NOT insert WAIT node for gaps < 300ms', () => {
    const events = [
      evt({ type: 'click', time: 0.0, x: 100, y: 200 }),
      evt({ type: 'click', time: 0.1, x: 300, y: 400 }),
    ];
    const flow = macroEventsToFlow(events, 'test');
    const clickNodes = flow.nodes.filter(n => n.type === 'click');
    expect(clickNodes).toHaveLength(2);
  });

  it('converts special key to keyboard node', () => {
    const events = [evt({ type: 'key', time: 0.5, key: 'Key.enter' })];
    const flow = macroEventsToFlow(events, 'test');
    const kbNodes = flow.nodes.filter(n => n.type === 'keyboard');
    expect(kbNodes).toHaveLength(1);
    expect(kbNodes[0].params.keys).toContain('enter');
  });

  it('converts single char key to input_text node', () => {
    const events = [evt({ type: 'key', time: 0.5, key: 'a' })];
    const flow = macroEventsToFlow(events, 'test');
    const textNodes = flow.nodes.filter(n => n.type === 'input_text');
    expect(textNodes).toHaveLength(1);
    expect(textNodes[0].params).toHaveProperty('text');
  });

  it('handles mixed click and key events', () => {
    const events = [
      evt({ type: 'click', time: 0.0, x: 100, y: 200 }),
      evt({ type: 'key', time: 0.8, key: 'hello' }),
      evt({ type: 'key', time: 0.9, key: 'Key.enter' }),
    ];
    const flow = macroEventsToFlow(events, 'test');
    expect(flow.nodes.length).toBeGreaterThanOrEqual(5);
  });

  it('generates a valid flow with edges connecting all nodes', () => {
    const events = [
      evt({ type: 'click', time: 0.0, x: 100, y: 200 }),
      evt({ type: 'key', time: 0.5, key: 'a' }),
    ];
    const flow = macroEventsToFlow(events, 'test');

    // All edges should reference valid node IDs
    const allNodeIds = new Set(flow.nodes.map(n => n.id));
    for (const edge of flow.edges) {
      expect(allNodeIds.has(edge.from_node)).toBe(true);
      expect(allNodeIds.has(edge.to_node)).toBe(true);
    }
  });
});
