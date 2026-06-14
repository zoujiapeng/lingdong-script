import { describe, it, expect } from 'vitest';
import { wpsToFlowScript, WpsScript } from '../utils/wpsToFlowScript';
import type { FlowScript, NodeType } from '../types';

function byId(s: FlowScript) {
  return Object.fromEntries(s.nodes.map((n) => [n.id, n]));
}
function hasEdge(s: FlowScript, fromType: NodeType, port: string, toType: NodeType): boolean {
  const m = byId(s);
  return s.edges.some((e) => m[e.from_node]?.type === fromType && e.from_port === port && m[e.to_node]?.type === toType);
}
function nodeOf(s: FlowScript, type: NodeType) {
  return s.nodes.find((n) => n.type === type);
}
// NodeParams is a union; read member-specific props through a loose view.
function P(node: { params: unknown } | undefined): Record<string, any> {
  return (node?.params ?? {}) as Record<string, any>;
}

describe('wpsToFlowScript → native 65-node FlowScript', () => {
  it('maps leaf steps to existing node types and links them', () => {
    const wps: WpsScript = {
      steps: [
        { tool: 'click', text: '确定', coords: [10, 20] },
        { tool: 'type_text', text_input: 'hi' },
        { tool: 'hotkey', keys: ['ctrl', 's'] },
        { tool: 'launch', command: 'notepad' },
        { tool: 'ext.delay', seconds: 2 },
        { tool: 'run_shell', command: 'echo hi' },
      ],
    };
    const s = wpsToFlowScript(wps);
    // semantic click → click_text (OCR), carrying the selector text
    expect(P(nodeOf(s, 'click_text')).text).toBe('确定');
    expect(P(nodeOf(s, 'input_text')).text).toBe('hi');
    expect(P(nodeOf(s, 'keyboard')).keys).toEqual(['ctrl', 's']);
    expect(P(nodeOf(s, 'launch_program')).target).toBe('notepad');
    expect(P(nodeOf(s, 'wait')).duration).toBe(2);
    expect(P(nodeOf(s, 'tool_call')).feature_name).toBe('run_shell');
    // chain via output ports, start → … → end, all terminate on input
    expect(hasEdge(s, 'start', 'output', 'click_text')).toBe(true);
    expect(s.edges.every((e) => e.to_port === 'input')).toBe(true);
    expect(nodeOf(s, 'end')).toBeTruthy();
  });

  it('coordinate-only click → click node (not click_text)', () => {
    const s = wpsToFlowScript({ steps: [{ tool: 'click', coords: [5, 6] }] });
    expect(P(nodeOf(s, 'click')).x).toBe(5);
    expect(nodeOf(s, 'click_text')).toBeFalsy();
  });

  it('ctrl.if → condition node with yes/no branches', () => {
    const s = wpsToFlowScript({
      steps: [{
        tool: 'ctrl.if', cond: { window_appears: '另存为' },
        then: [{ tool: 'click', text: '保存' }],
        else: [{ tool: 'hotkey', keys: ['esc'] }],
      }],
    });
    expect(P(nodeOf(s, 'condition')).condition_type).toBe('window_exists');
    expect(hasEdge(s, 'condition', 'yes', 'click_text')).toBe(true);
    expect(hasEdge(s, 'condition', 'no', 'keyboard')).toBe(true);
    expect(hasEdge(s, 'click_text', 'output', 'end')).toBe(true);
    expect(hasEdge(s, 'keyboard', 'output', 'end')).toBe(true);
  });

  it('ctrl.loop → loop node with loop_body back-edge + loop_end', () => {
    const s = wpsToFlowScript({
      steps: [{ tool: 'ctrl.loop', loop_type: 'count', count: 3, body: [{ tool: 'click', coords: [1, 1] }] }],
    });
    expect(P(nodeOf(s, 'loop')).count).toBe(3);
    expect(hasEdge(s, 'loop', 'loop_body', 'click')).toBe(true);
    expect(hasEdge(s, 'click', 'output', 'loop')).toBe(true);   // back-edge
    expect(hasEdge(s, 'loop', 'loop_end', 'end')).toBe(true);
  });

  it('produces a valid FlowScript envelope (meta/settings/variables)', () => {
    const s = wpsToFlowScript({ name: 'demo', vars: { u: 'a' }, steps: [{ tool: 'ext.var_set', name: 'u', value: 'a' }] });
    expect(s.meta.name).toBe('demo');
    expect(s.meta.version).toBe('1.0.0');
    expect(s.variables).toEqual({ u: 'a' });
    expect(s.settings.error_handling).toBe('stop');
    expect(P(nodeOf(s, 'variable_set')).name).toBe('u');
  });
});
