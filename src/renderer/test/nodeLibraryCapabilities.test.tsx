import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { NodeLibrary } from '../components/NodeLibrary/NodeLibrary';
import type { CapabilitySpec } from '../platform/contracts';
import { isRunnableFlowNodeType } from '../platform/flowToArpGraph';
import { useStore } from '../store';

function capability(flowNodeTypes: string[]): CapabilitySpec {
  return {
    id: 'test.capability',
    name: 'Test capability',
    category: 'controller',
    description: 'test capability',
    node_kind: 'action_batch',
    latency_class: 'local',
    deterministic: true,
    requires_llm: false,
    input_schema: {},
    flow_node_types: flowNodeTypes,
  };
}

function modelCapability(flowNodeTypes: string[]): CapabilitySpec {
  return { ...capability(flowNodeTypes), id: 'test.model', requires_llm: true };
}

describe('NodeLibrary capability gating', () => {
  it('keeps the locally compilable node library editable while runtime capabilities are unchecked', () => {
    render(<NodeLibrary capabilities={[]} />);

    expect(screen.getByText(/个可编辑节点 · 运行时尚未核验/)).toBeInTheDocument();
    expect(screen.getAllByText('鼠标操作').length).toBeGreaterThan(0);
    expect(screen.getAllByText('等待').length).toBeGreaterThan(0);
    expect(screen.getAllByText('条件判断').length).toBeGreaterThan(0);
    expect(screen.getAllByText('运行时尚未核验').length).toBeGreaterThan(0);
    expect(screen.queryByText('图像点击')).not.toBeInTheDocument();
    expect(screen.queryByText('循环')).not.toBeInTheDocument();
  });

  it('keeps local nodes visible and marks catalog exposure separately', () => {
    render(<NodeLibrary capabilities={[capability(['mouse_operation', 'wait'])]} />);

    expect(screen.getByText(/个可编辑节点 · 2 个当前可执行/)).toBeInTheDocument();
    expect(screen.getAllByText('鼠标操作').length).toBeGreaterThan(0);
    expect(screen.getAllByText('等待').length).toBeGreaterThan(0);
    expect(screen.getAllByText('条件判断').length).toBeGreaterThan(0);
    expect(screen.getAllByText('当前可执行').length).toBeGreaterThan(0);
    expect(screen.getAllByText('当前运行时未开放').length).toBeGreaterThan(0);
  });

  it('allows an unchecked locally compilable node to be added offline', () => {
    const before = useStore.getState().script.nodes.length;
    render(<><div className="flow-canvas-wrapper" /><NodeLibrary capabilities={[]} /></>);

    fireEvent.doubleClick(screen.getByText('点击').closest('.node-item')!);

    const nodes = useStore.getState().script.nodes;
    expect(nodes).toHaveLength(before + 1);
    expect(nodes.at(-1)?.type).toBe('click');
    expect(isRunnableFlowNodeType(nodes.at(-1)!.type)).toBe(true);
  });

  it('uses the runtime contract to identify nodes that need a model', () => {
    render(<NodeLibrary capabilities={[modelCapability(['agent_goal'])]} />);

    expect(screen.getAllByText('当前可执行 · 需要模型').length).toBeGreaterThan(0);
  });
});
