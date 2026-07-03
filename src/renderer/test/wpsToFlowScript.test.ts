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

function nodesOf(s: FlowScript, type: NodeType) {
  return s.nodes.filter((n) => n.type === type);
}

function P(node: { params: unknown } | undefined): Record<string, any> {
  return (node?.params ?? {}) as Record<string, any>;
}

describe('wpsToFlowScript -> native FlowScript', () => {
  it('maps leaf steps to existing node types and links them', () => {
    const wps: WpsScript = {
      steps: [
        { tool: 'click', text: 'OK', coords: [10, 20] },
        { tool: 'type_text', text_input: 'hi', coords: [12, 22] },
        { tool: 'hotkey', keys: ['ctrl', 's'] },
        { tool: 'launch', command: 'notepad' },
        { tool: 'ext.delay', seconds: 2 },
        { tool: 'run_shell', command: 'echo hi', timeout_s: 5 },
      ],
    };
    const s = wpsToFlowScript(wps);

    expect(P(nodeOf(s, 'click_text')).text).toBe('OK');
    expect(P(nodeOf(s, 'click_text')).offset_x).toBe(0);
    expect(P(nodeOf(s, 'input_text')).text).toBe('hi');
    expect(P(nodeOf(s, 'input_text')).x).toBe(12);
    expect(P(nodeOf(s, 'keyboard')).modifiers).toEqual(['ctrl']);
    expect(P(nodeOf(s, 'keyboard')).keys).toEqual(['s']);
    expect(P(nodeOf(s, 'launch_program')).target).toBe('notepad');
    expect(P(nodeOf(s, 'wait')).duration).toBe(2);
    expect(P(nodeOf(s, 'tool_call')).feature_name).toBe('run_command');
    expect(P(nodeOf(s, 'tool_call')).params.command).toBe('echo hi');

    expect(hasEdge(s, 'start', 'output', 'click_text')).toBe(true);
    expect(s.edges.every((e) => e.to_port === 'input')).toBe(true);
    expect(nodeOf(s, 'end')).toBeTruthy();
  });

  it('coordinate-only click -> click node', () => {
    const s = wpsToFlowScript({ steps: [{ tool: 'click', coords: [5, 6] }] });
    expect(P(nodeOf(s, 'click')).x).toBe(5);
    expect(P(nodeOf(s, 'click')).y).toBe(6);
    expect(nodeOf(s, 'click_text')).toBeFalsy();
  });

  it('maps scroll to mouse_operation params used by the executor', () => {
    const s = wpsToFlowScript({ steps: [{ tool: 'scroll', coords: [100, 200], amount: -4 }] });
    const params = P(nodeOf(s, 'mouse_operation'));
    expect(params.sub_operation).toBe('scroll');
    expect(params.scroll_direction).toBe('down');
    expect(params.scroll_amount).toBe(4);
    expect(params.x).toBe(100);
    expect(params.y).toBe(200);
  });

  it('ctrl.if -> condition node with yes/no branches', () => {
    const s = wpsToFlowScript({
      steps: [{
        tool: 'ctrl.if',
        cond: { window_appears: 'Save As' },
        then: [{ tool: 'click', text: 'Save' }],
        else: [{ tool: 'hotkey', keys: ['esc'] }],
      }],
    });
    expect(P(nodeOf(s, 'condition')).condition_type).toBe('window_exists');
    expect(P(nodeOf(s, 'condition')).window_title).toBe('Save As');
    expect(hasEdge(s, 'condition', 'yes', 'click_text')).toBe(true);
    expect(hasEdge(s, 'condition', 'no', 'keyboard')).toBe(true);
    expect(hasEdge(s, 'click_text', 'output', 'end')).toBe(true);
    expect(hasEdge(s, 'keyboard', 'output', 'end')).toBe(true);
  });

  it('ctrl.loop -> loop node with loop_body back-edge + loop_end', () => {
    const s = wpsToFlowScript({
      steps: [{ tool: 'ctrl.loop', loop_type: 'count', count: 3, body: [{ tool: 'click', coords: [1, 1] }] }],
    });
    expect(P(nodeOf(s, 'loop')).count).toBe(3);
    expect(hasEdge(s, 'loop', 'loop_body', 'click')).toBe(true);
    expect(hasEdge(s, 'click', 'output', 'loop')).toBe(true);
    expect(hasEdge(s, 'loop', 'loop_end', 'end')).toBe(true);
  });

  it('preserves WinPilot-only and unknown steps as tool_call payloads', () => {
    const s = wpsToFlowScript({
      steps: [
        { tool: 'draw_strokes', strokes: [[[1, 1], [2, 2]]], duration: 0.1 },
        { tool: 'run_detached', script: 'Start-Sleep 1', name: 'demo' },
        { tool: 'custom.future', answer: 42 },
      ],
    });
    const calls = nodesOf(s, 'tool_call').map((n) => P(n));
    expect(calls[0].feature_name).toBe('draw_strokes');
    expect(calls[0].params.strokes).toEqual([[[1, 1], [2, 2]]]);
    expect(calls[1].feature_name).toBe('run_detached');
    expect(calls[1].params.script).toBe('Start-Sleep 1');
    expect(calls[2].feature_name).toBe('custom.future');
    expect(calls[2].params.answer).toBe(42);
  });

  it('maps clipboard and screenshot ext steps to native nodes', () => {
    const s = wpsToFlowScript({
      steps: [
        { tool: 'ext.clipboard_set', text: 'hello' },
        { tool: 'ext.clipboard_get', into: 'clip' },
        { tool: 'ext.screenshot', path: 'shots/a.jpg', format: 'jpg' },
      ],
    });
    expect(P(nodeOf(s, 'clipboard_set')).text).toBe('hello');
    expect(nodeOf(s, 'clipboard_get')).toBeTruthy();
    expect(P(nodeOf(s, 'screenshot')).save_path).toBe('shots/a.jpg');
    expect(P(nodeOf(s, 'screenshot')).format).toBe('jpg');
  });

  it('produces a valid FlowScript envelope', () => {
    const s = wpsToFlowScript({ format: 'wps/2', name: 'demo', vars: { u: 'a' }, steps: [{ tool: 'ext.var_set', name: 'u', value: 'a' }] });
    expect(s.meta.name).toBe('demo');
    expect(s.meta.version).toBe('1.0.0');
    expect(s.meta.tags).toContain('wps/2');
    expect(s.variables).toEqual({ u: 'a' });
    expect(s.settings.error_handling).toBe('stop');
    expect(P(nodeOf(s, 'variable_set')).name).toBe('u');
  });
});
