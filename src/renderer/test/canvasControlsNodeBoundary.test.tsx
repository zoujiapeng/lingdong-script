import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ContextMenu } from '../components/Canvas/CanvasControls';
import { isRunnableFlowNodeType } from '../platform/flowToArpGraph';
import { useStore } from '../store';
import { NODE_DEFINITIONS } from '../utils/nodeDefinitions';

describe('canvas context-menu node boundary', () => {
  beforeEach(() => {
    useStore.setState((state) => ({
      ...state,
      clipboard: null,
      ui: { ...state.ui, selection: { nodes: [], edges: [] } },
    }));
  });

  it('offers only node types compiled by the local ARP projection', () => {
    render(<ContextMenu x={10} y={10} canvasX={20} canvasY={30} onClose={vi.fn()} />);

    expect(screen.getByText('添加可编辑节点')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '+ 点击' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '+ 图像点击' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '+ 循环' })).not.toBeInTheDocument();
    for (const button of screen.getAllByRole('button', { name: /^\+ / })) {
      const definition = Object.values(NODE_DEFINITIONS)
        .find((item) => `+ ${item.label}` === button.textContent?.trim());
      expect(definition && isRunnableFlowNodeType(definition.type)).toBe(true);
    }
  });

  it('adds a safe quick node through the store boundary', async () => {
    const user = userEvent.setup();
    const before = useStore.getState().script.nodes.length;
    render(<ContextMenu x={10} y={10} canvasX={120} canvasY={180} onClose={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: '+ 点击' }));

    const nodes = useStore.getState().script.nodes;
    expect(nodes).toHaveLength(before + 1);
    expect(nodes.at(-1)).toMatchObject({ type: 'click', x: 120, y: 180 });
  });
});
