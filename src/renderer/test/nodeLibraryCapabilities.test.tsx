import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { NodeLibrary } from '../components/NodeLibrary/NodeLibrary';
import type { CapabilitySpec } from '../platform/contracts';

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

describe('NodeLibrary capability gating', () => {
  it('does not reintroduce unsupported nodes through the recent-items footer', () => {
    render(<NodeLibrary capabilities={[]} />);

    expect(screen.getByText('0 个可执行节点')).toBeInTheDocument();
    expect(screen.queryByText('鼠标操作')).not.toBeInTheDocument();
    expect(screen.queryByText('等待')).not.toBeInTheDocument();
    expect(screen.queryByText('条件判断')).not.toBeInTheDocument();
  });

  it('shows recent nodes only after the backend catalog exposes them', () => {
    render(<NodeLibrary capabilities={[capability(['mouse_operation', 'wait'])]} />);

    expect(screen.getAllByText('鼠标操作').length).toBeGreaterThan(0);
    expect(screen.getAllByText('等待').length).toBeGreaterThan(0);
    expect(screen.queryByText('条件判断')).not.toBeInTheDocument();
  });
});
