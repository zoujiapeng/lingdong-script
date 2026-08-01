import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PropertyPanel } from '../components/PropertyPanel/PropertyPanel';
import { confirmDocumentReplacement, useStore } from '../store';

afterEach(() => {
  delete (window as any).electronAPI;
  vi.restoreAllMocks();
});

describe('PropertyPanel history', () => {
  it('checkpoints parameter, label, and disabled edits when their controls blur', () => {
    useStore.getState().newScript();
    const node = useStore.getState().script.nodes.find((item) => item.type === 'action_batch')!;
    const originalSubgoal = (node.params as any).subgoal;
    const originalLabel = node.label;
    useStore.getState().selectNodes([node.id]);
    const view = render(<PropertyPanel />);

    const subgoal = screen.getByText('子目标').parentElement!.querySelector('input')!;
    fireEvent.change(subgoal, { target: { value: 'edited subgoal' } });
    fireEvent.blur(subgoal);
    act(() => useStore.getState().undo());
    expect((useStore.getState().script.nodes.find((item) => item.id === node.id)!.params as any).subgoal).toBe(originalSubgoal);
    act(() => useStore.getState().redo());
    expect((useStore.getState().script.nodes.find((item) => item.id === node.id)!.params as any).subgoal).toBe('edited subgoal');

    const label = view.container.querySelector<HTMLInputElement>('.property-panel-name')!;
    fireEvent.change(label, { target: { value: 'Edited label' } });
    fireEvent.blur(label);
    act(() => useStore.getState().undo());
    expect(useStore.getState().script.nodes.find((item) => item.id === node.id)!.label).toBe(originalLabel);
    act(() => useStore.getState().redo());
    expect(useStore.getState().script.nodes.find((item) => item.id === node.id)!.label).toBe('Edited label');

    fireEvent.click(screen.getByText('高级设置'));
    const disabled = screen.getByText('禁用此节点').parentElement!.querySelector('button')!;
    fireEvent.click(disabled);
    fireEvent.blur(disabled);
    act(() => useStore.getState().undo());
    expect(useStore.getState().script.nodes.find((item) => item.id === node.id)!.disabled).toBeFalsy();
    act(() => useStore.getState().redo());
    expect(useStore.getState().script.nodes.find((item) => item.id === node.id)!.disabled).toBe(true);
  });

  it('blocks save and document replacement while JSON editor text is invalid', async () => {
    useStore.getState().newScript();
    const node = useStore.getState().script.nodes.find((item) => item.type === 'action_batch')!;
    useStore.getState().selectNodes([node.id]);
    render(<PropertyPanel />);

    const actions = screen.getByText('操作列表').parentElement!.querySelector('textarea')!;
    fireEvent.change(actions, { target: { value: '{invalid-json' } });
    const confirm = vi.spyOn(window, 'confirm');
    const alert = vi.spyOn(window, 'alert').mockImplementation(() => undefined);
    let saveResult: { status: string; error?: string } | undefined;

    await act(async () => { saveResult = await useStore.getState().saveScript(); });

    expect(saveResult).toMatchObject({ status: 'error' });
    let replacementAllowed = true;
    act(() => { replacementAllowed = confirmDocumentReplacement(); });
    expect(replacementAllowed).toBe(false);
    expect(confirm).not.toHaveBeenCalled();
    expect(alert).toHaveBeenCalledWith(expect.stringContaining('请先修正编辑器内容'));
    expect(actions.parentElement!.querySelector('.arp-field-error')).not.toBeNull();
  });

  it('commits replay JSON and target drafts into the artifact before saving', async () => {
    useStore.getState().newScript();
    const nodeId = useStore.getState().addNode('replay_step', 200, 200);
    useStore.getState().selectNodes([nodeId]);
    const saveFileDialog = vi.fn(async (_defaultName?: string) => ({
      canceled: false,
      filePath: 'C:\\tmp\\draft-safe.automation.json',
    }));
    const writeFile = vi.fn(async (_filePath: string, _content: string) => ({ success: true }));
    (window as any).electronAPI = { saveFileDialog, writeFile };
    render(<PropertyPanel />);

    const step = screen.getByText('WPS 步骤').parentElement!.querySelector('textarea')!;
    const target = screen.getByText('目标窗口').parentElement!.querySelector('textarea')!;
    fireEvent.change(step, { target: { value: '{"tool":"ext.delay","seconds":2}' } });
    fireEvent.change(target, { target: { value: '{"title":"Notepad"}' } });

    await act(async () => { await useStore.getState().saveScript(); });

    expect(saveFileDialog).toHaveBeenCalledOnce();
    expect(writeFile).toHaveBeenCalledOnce();
    const document = JSON.parse(writeFile.mock.calls[0][1]);
    const saved = document.steps.find((item: { node_id: string }) => item.node_id === nodeId);
    expect(saved.params).toMatchObject({
      step: { tool: 'ext.delay', seconds: 2 },
      target_window: { title: 'Notepad' },
    });
  });

  it('preserves structured evidence conditions instead of flattening them to text', () => {
    useStore.getState().newScript();
    const node = useStore.getState().script.nodes.find((item) => item.type === 'evidence_gate')!;
    const original = JSON.parse(JSON.stringify((node.params as any).required));
    useStore.getState().selectNodes([node.id]);
    render(<PropertyPanel />);

    const editor = screen.getByText('必需证据').parentElement!.querySelector('textarea')!;
    expect(JSON.parse(editor.value)).toEqual(original);
    const replacement = [{ predicate: 'artifact.exists', source: 'screenshot', equals: true, max_age_ms: 5000 }];
    fireEvent.change(editor, { target: { value: JSON.stringify(replacement, null, 2) } });
    fireEvent.blur(editor);

    expect((useStore.getState().script.nodes.find((item) => item.id === node.id)!.params as any).required).toEqual(replacement);
    act(() => useStore.getState().undo());
    expect((useStore.getState().script.nodes.find((item) => item.id === node.id)!.params as any).required).toEqual(original);
  });
});
