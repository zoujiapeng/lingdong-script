import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FlowFragmentComposer } from '../components/FlowFragment/FlowFragmentComposer';
import { useStore } from '../store';

beforeEach(() => {
  useStore.getState().newScript();
  useStore.getState().setCanvasTransform({ x: 0, y: 0, scale: 1 });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('flow fragment composer UX', () => {
  it('closes only the dialog when Escape is pressed inside it', async () => {
    const user = userEvent.setup();
    const parentKeyDown = vi.fn();
    render(
      <div onKeyDown={parentKeyDown}>
        <FlowFragmentComposer propose={vi.fn()} />
      </div>,
    );

    await user.click(screen.getByRole('button', { name: '一句话生成流程片段' }));
    expect(screen.getByRole('dialog', { name: '一句话生成流程片段' })).toBeInTheDocument();
    await user.keyboard('{Escape}');

    expect(screen.queryByRole('dialog', { name: '一句话生成流程片段' })).not.toBeInTheDocument();
    expect(parentKeyDown).not.toHaveBeenCalled();
  });

  it('centers an applied fragment in the canvas viewport rather than the window', async () => {
    const user = userEvent.setup();
    const propose = vi.fn(async () => ({
      format: 'flow-fragment-proposal/1' as const,
      summary: '等待一下',
      nodes: [{ local_id: 'wait', type: 'wait' as const, label: '等待', params: { wait_type: 'time', duration: 1 } }],
      edges: [],
    }));
    const transform = vi.spyOn(useStore.getState(), 'setCanvasTransform');

    render(
      <>
        <div className="arp-studio-shell">
          <div
            className="flow-canvas-wrapper"
            ref={(element) => {
              if (!element) return;
              element.getBoundingClientRect = () => ({
                width: 400, height: 300, x: 50, y: 70, top: 70, left: 50,
                right: 450, bottom: 370, toJSON: () => ({}),
              });
            }}
          />
        </div>
        <FlowFragmentComposer propose={propose} />
      </>,
    );

    await user.click(screen.getByRole('button', { name: '一句话生成流程片段' }));
    await user.type(screen.getByRole('textbox', { name: '描述要插入的机械流程' }), '等一秒');
    await user.click(screen.getByRole('button', { name: '生成预览' }));
    await user.click(await screen.findByRole('button', { name: '应用到空白处' }));

    const insertedId = useStore.getState().ui.selection.nodes[0];
    const inserted = useStore.getState().script.nodes.find((node) => node.id === insertedId)!;
    expect(transform).toHaveBeenLastCalledWith({
      x: 200 - (inserted.x + 110),
      y: 150 - (inserted.y + 40),
    });
  });

  it('discards a proposal when the script object changes while generation is pending', async () => {
    const user = userEvent.setup();
    let resolveProposal!: (proposal: {
      format: 'flow-fragment-proposal/1';
      summary: string;
      nodes: Array<{ local_id: string; type: 'wait'; params: Record<string, unknown> }>;
      edges: [];
    }) => void;
    const propose = vi.fn(() => new Promise<Parameters<typeof resolveProposal>[0]>((resolve) => {
      resolveProposal = resolve;
    }));
    render(<FlowFragmentComposer propose={propose} />);

    await user.click(screen.getByRole('button', { name: '一句话生成流程片段' }));
    await user.type(screen.getByRole('textbox', { name: '描述要插入的机械流程' }), '等待');
    await user.click(screen.getByRole('button', { name: '生成预览' }));
    await waitFor(() => expect(propose).toHaveBeenCalledOnce());

    act(() => { useStore.getState().addNode('wait', 10, 10); });
    resolveProposal({
      format: 'flow-fragment-proposal/1',
      summary: '等待',
      nodes: [{ local_id: 'wait', type: 'wait', params: { wait_type: 'time', duration: 1 } }],
      edges: [],
    });

    expect(await screen.findByRole('alert')).toHaveTextContent('生成期间当前流程已经变化');
    expect(screen.queryByRole('button', { name: '应用到空白处' })).not.toBeInTheDocument();
  });

  it('discards a preview when the script object changes before apply', async () => {
    const user = userEvent.setup();
    const propose = vi.fn(async () => ({
      format: 'flow-fragment-proposal/1' as const,
      summary: '等待',
      nodes: [{ local_id: 'wait', type: 'wait' as const, params: { wait_type: 'time', duration: 1 } }],
      edges: [],
    }));
    render(<FlowFragmentComposer propose={propose} />);

    await user.click(screen.getByRole('button', { name: '一句话生成流程片段' }));
    await user.type(screen.getByRole('textbox', { name: '描述要插入的机械流程' }), '等待');
    await user.click(screen.getByRole('button', { name: '生成预览' }));
    const apply = await screen.findByRole('button', { name: '应用到空白处' });

    act(() => { useStore.getState().addNode('wait', 10, 10); });
    await user.click(apply);

    expect(await screen.findByRole('alert')).toHaveTextContent('当前流程已变化');
    expect(screen.queryByRole('button', { name: '应用到空白处' })).not.toBeInTheDocument();
  });
});
