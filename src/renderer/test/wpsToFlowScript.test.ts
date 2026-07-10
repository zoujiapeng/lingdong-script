import { describe, expect, it } from 'vitest';
import type { FlowScript } from '../types';
import { wpsToFlowScript, type WpsScript } from '../utils/wpsToFlowScript';

function replayNodes(script: FlowScript) {
  return script.nodes.filter((node) => node.type === 'replay_step');
}

describe('wpsToFlowScript exact offline import', () => {
  it('preserves every top-level WPS step verbatim and links it in order', () => {
    const steps = [
      { tool: 'click', text: 'OK', coords: [10, 20], expect: { text_appears: 'Done' } },
      { tool: 'type_text', text_input: 'hello' },
      { tool: 'draw_strokes', strokes: [[[1, 1], [2, 2]]], duration: 0.1 },
      { tool: 'custom.future', answer: 42 },
    ];
    const flow = wpsToFlowScript({ format: 'wps/2', steps });
    const replay = replayNodes(flow);

    expect(replay).toHaveLength(steps.length);
    expect(replay.map((node) => (node.params as any).step)).toEqual(steps);
    expect(replay.every((node) => (node.params as any).pace_ms === 0)).toBe(true);
    expect(flow.nodes.map((node) => node.type)).toEqual([
      'start', 'replay_step', 'replay_step', 'replay_step', 'replay_step', 'end',
    ]);
    expect(flow.edges).toHaveLength(flow.nodes.length - 1);
    for (let index = 0; index < flow.edges.length; index += 1) {
      expect(flow.edges[index]).toMatchObject({
        from_node: flow.nodes[index].id,
        from_port: 'output',
        to_node: flow.nodes[index + 1].id,
        to_port: 'input',
      });
    }
  });

  it('keeps nested WPS control flow executable inside one editable step', () => {
    const controlStep = {
      tool: 'ctrl.if',
      cond: { window_appears: 'Save As' },
      then: [{ tool: 'click', text: 'Save' }],
      else: [{ tool: 'hotkey', keys: ['esc'] }],
    };
    const flow = wpsToFlowScript({ steps: [controlStep] });

    expect(replayNodes(flow)).toHaveLength(1);
    expect((replayNodes(flow)[0].params as any).step).toEqual(controlStep);
    expect(flow.nodes.some((node) => node.type === 'condition')).toBe(false);
  });

  it('shares one session and copies the stable target hint onto each step', () => {
    const flow = wpsToFlowScript({
      source_run: 'agent-run-42',
      target_window: { title: 'Notepad', process: 'notepad.exe' },
      steps: [{ tool: 'click', coords: [1, 2] }, { tool: 'hotkey', keys: ['enter'] }],
    });
    const replay = replayNodes(flow).map((node) => node.params as any);

    expect(replay.map((params) => params.session)).toEqual(['agent-run-42', 'agent-run-42']);
    expect(replay.map((params) => params.target_window)).toEqual([
      { title: 'Notepad', process: 'notepad.exe' },
      { title: 'Notepad', process: 'notepad.exe' },
    ]);
  });

  it('uses legacy condition metadata as a target hint', () => {
    const flow = wpsToFlowScript({
      conditions: { window_title: 'Calculator' },
      steps: [{ tool: 'hotkey', keys: ['esc'] }],
    });
    expect((replayNodes(flow)[0].params as any).target_window).toBe('Calculator');
  });

  it('produces an offline-mode FlowScript envelope', () => {
    const source: WpsScript = {
      format: 'wps/2',
      name: 'demo',
      task: 'Demonstrate replay',
      vars: { user: 'alice' },
      steps: [{ tool: 'ext.var_set', name: 'ready', value: 'yes' }],
    };
    const flow = wpsToFlowScript(source);

    expect(flow.meta.name).toBe('demo');
    expect(flow.meta.tags).toContain('replay-mode:offline');
    expect(flow.variables).toEqual({ user: 'alice' });
    expect(flow.settings.default_delay).toBe(0);
    expect(flow.settings.require_real_features).toBe(true);
  });
});
