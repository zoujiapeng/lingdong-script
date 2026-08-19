import { describe, expect, it } from 'vitest';
import { useStore } from '../store';
import {
  buildFlowFragmentRequestContext,
  normalizeFlowFragmentProposal,
  validateFlowFragmentProposal,
} from '../utils/flowFragmentProposal';

describe('flow fragment proposals', () => {
  it('sends a bounded deterministic catalog without model-backed nodes', () => {
    useStore.getState().newScript();
    const context = buildFlowFragmentRequestContext(useStore.getState().script);

    expect(Object.keys(context).sort()).toEqual(['catalog', 'document']);
    expect(context.document).toMatchObject({
      node_count: useStore.getState().script.nodes.length,
      edge_count: useStore.getState().script.edges.length,
    });
    expect(context.catalog.some((item) => item.type === 'click')).toBe(true);
    expect(context.catalog.some((item) => item.type === 'agent_goal')).toBe(false);
    expect(context.catalog.some((item) => item.type === 'skill_replay')).toBe(false);
    expect(context.catalog.some((item) => item.type === 'action_batch')).toBe(false);
    expect(context.catalog.some((item) => item.type === 'replay_step')).toBe(false);
    expect(context.catalog.some((item) => item.type === 'click_text')).toBe(false);
    expect(context.catalog.find((item) => item.type === 'click')?.params).toEqual({
      x: 960,
      y: 540,
      method: 'single',
      delay_after: 0.5,
    });
    expect(context.catalog.find((item) => item.type === 'mouse_operation')?.params).not.toHaveProperty('offset_x');
    expect(JSON.stringify(context.catalog)).not.toContain('description');
  });

  it('rejects two targets on one runtime branch', () => {
    useStore.getState().newScript();
    const raw = {
      format: 'flow-fragment-proposal/1',
      summary: '',
      nodes: [
        { local_id: 'a', type: 'click', params: { x: 10, y: 20 } },
        { local_id: 'b', type: 'wait', params: { wait_type: 'time', duration: 1 } },
        { local_id: 'c', type: 'wait', params: { wait_type: 'time', duration: 2 } },
      ],
      edges: [
        { source: 'a', target: 'b', source_port: 'output' },
        { source: 'a', target: 'c', source_port: 'output' },
      ],
    };

    expect(() => normalizeFlowFragmentProposal(raw)).toThrow('只能连接一个目标');
  });

  it('locally validates then applies the fragment as one undo checkpoint', () => {
    useStore.getState().newScript();
    const before = useStore.getState().script.nodes.length;
    const proposal = normalizeFlowFragmentProposal({
      format: 'flow-fragment-proposal/1',
      summary: 'click then wait',
      nodes: [
        { local_id: 'a', type: 'click', label: '点击', params: { x: 420, y: 300 } },
        { local_id: 'b', type: 'wait', label: '等待', params: { wait_type: 'time', duration: 1 } },
      ],
      edges: [{ source: 'a', target: 'b', source_port: 'output' }],
    });

    expect(validateFlowFragmentProposal(proposal, useStore.getState().script)).toEqual([]);
    const inserted = useStore.getState().applyFlowFragment(proposal);
    expect(inserted.nodeIds).toHaveLength(2);
    expect(useStore.getState().script.nodes).toHaveLength(before + 2);
    expect(useStore.getState().ui.selection.nodes).toEqual(inserted.nodeIds);

    useStore.getState().undo();
    expect(useStore.getState().script.nodes).toHaveLength(before);
  });

  it('blocks opaque executable nodes before preview', () => {
    useStore.getState().newScript();
    expect(() => normalizeFlowFragmentProposal({
      format: 'flow-fragment-proposal/1',
      summary: '',
      nodes: [{ local_id: 'batch', type: 'action_batch', params: { actions: [] } }],
      edges: [],
    })).toThrow('不可插入');
  });

  it('rejects parameters outside the small mechanical catalog', () => {
    useStore.getState().newScript();
    expect(() => normalizeFlowFragmentProposal({
      format: 'flow-fragment-proposal/1',
      summary: '',
      nodes: [{ local_id: 'click', type: 'click', params: { x: 1, y: 2, offset_x: 9 } }],
      edges: [],
    })).toThrow('未开放参数');
  });

  it('rejects directed cycles at the response boundary', () => {
    useStore.getState().newScript();
    expect(() => normalizeFlowFragmentProposal({
      format: 'flow-fragment-proposal/1',
      summary: '',
      nodes: [
        { local_id: 'a', type: 'click', params: { x: 1, y: 2 } },
        { local_id: 'b', type: 'wait', params: { wait_type: 'time', duration: 1 } },
      ],
      edges: [
        { source: 'a', target: 'b', source_port: 'output' },
        { source: 'b', target: 'a', source_port: 'output' },
      ],
    })).toThrow('循环');
  });

  it('rejects model-backed node types and disconnected fragments at the response boundary', () => {
    useStore.getState().newScript();
    const base = {
      format: 'flow-fragment-proposal/1',
      summary: '',
      edges: [],
    };

    expect(() => normalizeFlowFragmentProposal({
      ...base,
      nodes: [{ local_id: 'agent', type: 'agent_goal', params: { goal: 'do it' } }],
    })).toThrow('不可插入');
    expect(() => normalizeFlowFragmentProposal({
      ...base,
      nodes: [
        { local_id: 'a', type: 'click', params: { x: 1, y: 2 } },
        { local_id: 'b', type: 'wait', params: { wait_type: 'time', duration: 1 } },
      ],
    })).toThrow('内部连线');
    expect(() => normalizeFlowFragmentProposal({
      ...base,
      nodes: [{ local_id: 'a', type: 'click', params: { x: 1, y: 2 } }],
      edges: [{ source: 'a', target: 'a', source_port: 'output' }],
    })).toThrow('自身');
  });
});
