import { fireEvent, render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { FlowNode } from '../components/Canvas/FlowNode';
import { useStore } from '../store';

describe('FlowNode drag history', () => {
  it('keeps selected nodes relative and commits the drag as one undo step', () => {
    useStore.getState().newScript();
    useStore.getState().addNode('action_batch', 360, 180);
    useStore.getState().addNode('human_gate', 360, 330);
    useStore.getState().loadScript(JSON.parse(JSON.stringify(useStore.getState().script)));
    useStore.getState().setUI({ canvas_transform: { x: 0, y: 0, scale: 1 } });
    const first = useStore.getState().script.nodes.find((node) => node.type === 'action_batch')!;
    const second = useStore.getState().script.nodes.find((node) => node.type === 'human_gate')!;
    const original = {
      first: { x: first.x, y: first.y },
      second: { x: second.x, y: second.y },
    };
    useStore.getState().selectNodes([first.id, second.id]);

    const view = render(
      <FlowNode
        node={first}
        onPortMouseDown={vi.fn()}
        onPortMouseUp={vi.fn()}
      />,
    );
    const dragSurface = view.container.firstElementChild!;
    fireEvent.mouseDown(dragSurface, { button: 0, clientX: 100, clientY: 100 });
    fireEvent.mouseMove(document, { clientX: 140, clientY: 120 });
    fireEvent.mouseUp(document, { clientX: 140, clientY: 120 });

    const movedFirst = useStore.getState().script.nodes.find((node) => node.id === first.id)!;
    const movedSecond = useStore.getState().script.nodes.find((node) => node.id === second.id)!;
    expect(movedFirst).toMatchObject({ x: original.first.x + 40, y: original.first.y + 20 });
    expect(movedSecond).toMatchObject({ x: original.second.x + 40, y: original.second.y + 20 });
    expect(movedSecond.x - movedFirst.x).toBe(original.second.x - original.first.x);
    expect(movedSecond.y - movedFirst.y).toBe(original.second.y - original.first.y);

    useStore.getState().undo();
    expect(useStore.getState().script.nodes.find((node) => node.id === first.id)).toMatchObject(original.first);
    expect(useStore.getState().script.nodes.find((node) => node.id === second.id)).toMatchObject(original.second);

    // A second undo must be a no-op: one drag produces one history entry.
    useStore.getState().undo();
    expect(useStore.getState().script.nodes.find((node) => node.id === first.id)).toMatchObject(original.first);
    expect(useStore.getState().script.nodes.find((node) => node.id === second.id)).toMatchObject(original.second);

    useStore.getState().redo();
    expect(useStore.getState().script.nodes.find((node) => node.id === first.id)).toMatchObject({
      x: original.first.x + 40,
      y: original.first.y + 20,
    });
    expect(useStore.getState().script.nodes.find((node) => node.id === second.id)).toMatchObject({
      x: original.second.x + 40,
      y: original.second.y + 20,
    });
  });
});
