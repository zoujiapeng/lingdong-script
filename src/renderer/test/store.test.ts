import { describe, it, expect, vi } from 'vitest';
import { STORAGE_KEY } from '../store/defaults';

// Mock all Electron API methods that the store uses during initialization
const mockCleanup = vi.fn();
const mockElectronAPI = {
  onEngineMessage: vi.fn(() => mockCleanup),
  onEngineLog: vi.fn(() => mockCleanup),
  onApiCommand: vi.fn(() => mockCleanup),
  send: vi.fn(),
  on: vi.fn(() => mockCleanup),
  invoke: vi.fn(async () => null),
  selectRegion: vi.fn(async () => ({ x: 100, y: 200 })),
  platform: 'win32',
  minimize: vi.fn(),
  maximize: vi.fn(),
  close: vi.fn(),
  pushState: vi.fn(),
  openFileDialog: vi.fn(async () => null),
  saveFileDialog: vi.fn(async (_defaultName?: string): Promise<{ canceled: boolean; filePath?: string }> => ({ canceled: true })),
  readFile: vi.fn(async () => ''),
  writeFile: vi.fn(async (_filePath: string, _content: string): Promise<{ success: boolean; error?: string }> => ({ success: true })),
  engineStart: vi.fn(async () => {}),
  engineStop: vi.fn(async () => {}),
  enginePause: vi.fn(async () => {}),
  engineResume: vi.fn(async () => {}),
  engineStatus: vi.fn(async () => 'idle'),
  captureScreenshot: vi.fn(async () => ''),
  ocrRecognizeRegion: vi.fn(async () => ''),
  aiGenerate: vi.fn(async () => null),
  dslToFlow: vi.fn(async () => null),
  featuresList: vi.fn(async () => []),
  callFeature: vi.fn(async () => ({ success: true })),
  wheelSelect: vi.fn(async () => null),
};

(globalThis as any).window.electronAPI = mockElectronAPI;

describe('store', () => {
  it('restores the localStorage document as a normalized unsaved draft', async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      meta: {
        id: 'restored-draft', name: 'Restored draft', created_at: '', updated_at: '', version: '1.0.0',
      },
      nodes: [
        { id: 'start', type: 'start', x: 0, y: 0, params: {} },
        { id: 'end', type: 'end', x: 200, y: 0, params: {} },
      ],
      edges: [
        { id: 'edge', from_node: 'start', from_port: '', to_node: 'end', to_port: ' ' },
      ],
      variables: {},
      settings: { default_delay: 0, error_handling: 'stop', max_retries: 0, retry_delay: 0, log_level: 'info' },
    }));
    vi.resetModules();

    const { useStore } = await import('../store');
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(useStore.getState().script.meta.id).toBe('restored-draft');
    expect(useStore.getState().script.edges[0]).toMatchObject({
      from_port: 'output',
      to_port: 'input',
    });
    expect(useStore.getState().isDirty).toBe(true);

    useStore.getState().newScript();
    localStorage.removeItem(STORAGE_KEY);
  });

  it('should initialize with a template script and default UI', async () => {
    const { useStore } = await import('../store');
    const state = useStore.getState();

    expect(state.script).toBeDefined();
    // Store loads a valid, side-effect-free ARP workflow by default.
    expect(state.script.nodes.length).toBeGreaterThan(0);
    expect(state.script.edges.length).toBeGreaterThan(0);
    expect(state.script.meta.name).toBe('ARP 低延迟起步流程');
    expect(state.script.nodes.map((node) => node.type)).toEqual([
      'start', 'action_batch', 'human_gate', 'evidence_gate', 'end',
    ]);
    const humanGate = state.script.nodes.find((node) => node.type === 'human_gate');
    expect(humanGate?.params).toMatchObject({ message: expect.any(String) });
    expect(humanGate?.params).not.toHaveProperty('approved', true);
    expect(state.script.nodes.find((node) => node.type === 'evidence_gate')?.params).toMatchObject({
      required: [{ predicate: 'human.approved', source: 'human_gate', equals: true }],
    });
    expect(state.ui).toBeDefined();
    expect(state.ui.dark_mode).toBe(false);
    expect(state.ui.themeMode).toBe('system');
    expect(state.ui.show_grid).toBe(true);
    expect(state.execution.status).toBe('idle');
  });

  it('can add a node via addNode action', async () => {
    const { useStore } = await import('../store');
    const initialCount = useStore.getState().script.nodes.length;

    useStore.getState().addNode('click', 100, 200);
    const updated = useStore.getState();
    expect(updated.script.nodes).toHaveLength(initialCount + 1);
    expect(updated.script.nodes[updated.script.nodes.length - 1].type).toBe('click');
    expect(updated.script.nodes[updated.script.nodes.length - 1].x).toBe(100);
    expect(updated.script.nodes[updated.script.nodes.length - 1].y).toBe(200);
  });

  it('seeds history so the first edit can be undone and redone', async () => {
    const { useStore } = await import('../store');
    useStore.getState().newScript();

    const addedId = useStore.getState().addNode('click', 120, 220);
    expect(useStore.getState().script.nodes.some((node) => node.id === addedId)).toBe(true);

    useStore.getState().undo();
    expect(useStore.getState().script.nodes.some((node) => node.id === addedId)).toBe(false);

    useStore.getState().redo();
    expect(useStore.getState().script.nodes.some((node) => node.id === addedId)).toBe(true);
  });

  it('can add and then delete a node', async () => {
    const { useStore } = await import('../store');
    const initialCount = useStore.getState().script.nodes.length;

    useStore.getState().addNode('click', 0, 0);
    const afterAdd = useStore.getState();
    expect(afterAdd.script.nodes).toHaveLength(initialCount + 1);

    const nodeId = afterAdd.script.nodes.find(n => n.type === 'click' && n.x === 0 && n.y === 0)?.id;
    if (nodeId) {
      useStore.getState().deleteNodes([nodeId]);
      const afterDelete = useStore.getState();
      expect(afterDelete.script.nodes).toHaveLength(initialCount);
    }
  });

  it('protects start and end nodes inside the store without creating a history entry', async () => {
    const { useStore } = await import('../store');
    useStore.getState().newScript();
    const before = useStore.getState().script;
    const protectedIds = before.nodes
      .filter((node) => node.type === 'start' || node.type === 'end')
      .map((node) => node.id);

    useStore.getState().selectNodes(protectedIds);
    useStore.getState().deleteNodes(protectedIds);

    expect(useStore.getState().script.nodes.map((node) => node.id)).toEqual(before.nodes.map((node) => node.id));
    expect(useStore.getState().ui.selection.nodes).toEqual(protectedIds);
    expect(useStore.getState().isDirty).toBe(false);
    useStore.getState().undo();
    expect(useStore.getState().script.nodes.map((node) => node.id)).toEqual(before.nodes.map((node) => node.id));
  });

  it('allows imported duplicate boundary nodes to be repaired while retaining the last boundary', async () => {
    const { useStore } = await import('../store');
    useStore.getState().newScript();
    const imported = JSON.parse(JSON.stringify(useStore.getState().script));
    const start = imported.nodes.find((node: any) => node.type === 'start');
    const end = imported.nodes.find((node: any) => node.type === 'end');
    imported.nodes.push({ ...start, id: 'extra-start', x: start.x + 40 });
    imported.nodes.push({ ...end, id: 'extra-end', x: end.x + 40 });
    useStore.getState().loadScript(imported);

    useStore.getState().deleteNodes(['extra-start', 'extra-end']);

    expect(useStore.getState().script.nodes.filter((node) => node.type === 'start')).toHaveLength(1);
    expect(useStore.getState().script.nodes.filter((node) => node.type === 'end')).toHaveLength(1);
    useStore.getState().undo();
    expect(useStore.getState().script.nodes.filter((node) => node.type === 'start')).toHaveLength(2);
    expect(useStore.getState().script.nodes.filter((node) => node.type === 'end')).toHaveLength(2);
  });

  it('deletes only removable nodes when protected ids are mixed in', async () => {
    const { useStore } = await import('../store');
    useStore.getState().newScript();
    const startId = useStore.getState().script.nodes.find((node) => node.type === 'start')!.id;
    const addedId = useStore.getState().addNode('click', 100, 100);

    useStore.getState().deleteNodes([startId, addedId, 'missing']);

    expect(useStore.getState().script.nodes.some((node) => node.id === startId)).toBe(true);
    expect(useStore.getState().script.nodes.some((node) => node.id === addedId)).toBe(false);
    useStore.getState().undo();
    expect(useStore.getState().script.nodes.some((node) => node.id === addedId)).toBe(true);
  });

  it('keeps exactly one start and end across add, duplicate, and paste entry points', async () => {
    const { useStore } = await import('../store');
    useStore.getState().newScript();
    const startId = useStore.getState().script.nodes.find((node) => node.type === 'start')!.id;
    const endId = useStore.getState().script.nodes.find((node) => node.type === 'end')!.id;

    expect(useStore.getState().addNode('start', 10, 10)).toBe(startId);
    expect(useStore.getState().addNode('end', 20, 20)).toBe(endId);
    useStore.getState().duplicateNodes([startId, endId]);
    useStore.getState().selectNodes([startId, endId]);
    useStore.getState().copySelected();
    useStore.getState().paste();

    const state = useStore.getState();
    expect(state.script.nodes.filter((node) => node.type === 'start')).toHaveLength(1);
    expect(state.script.nodes.filter((node) => node.type === 'end')).toHaveLength(1);
    expect(state.isDirty).toBe(false);
    state.undo();
    expect(useStore.getState().script.nodes.filter((node) => node.type === 'start')).toHaveLength(1);
    expect(useStore.getState().script.nodes.filter((node) => node.type === 'end')).toHaveLength(1);
  });

  it('newScript resets to the default template', async () => {
    const { useStore } = await import('../store');
    const addedId = useStore.getState().addNode('click', 800, 800);
    expect(useStore.getState().script.nodes.some(n => n.id === addedId)).toBe(true);

    useStore.getState().newScript();
    const state = useStore.getState();
    expect(state.script.meta.name).toBe('ARP 低延迟起步流程');
    expect(state.script.nodes.some(n => n.id === addedId)).toBe(false);
    expect(state.isDirty).toBe(false);
  });

  it('uses one dirty-document confirmation owner before destructive replacement', async () => {
    const { confirmDocumentReplacement, useStore } = await import('../store');
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    useStore.getState().newScript();
    expect(confirmDocumentReplacement()).toBe(true);
    expect(confirm).not.toHaveBeenCalled();

    useStore.getState().setScriptName('unsaved');
    expect(confirmDocumentReplacement()).toBe(false);
    expect(confirm).toHaveBeenCalledTimes(1);
    confirm.mockReturnValue(true);
    expect(confirmDocumentReplacement()).toBe(true);
    confirm.mockRestore();
  });

  it('loads a script through a freshly seeded undo history', async () => {
    const { useStore } = await import('../store');
    useStore.getState().newScript();
    const loaded = JSON.parse(JSON.stringify(useStore.getState().script));
    loaded.meta.id = 'loaded-history-test';
    loaded.meta.name = 'Loaded history test';
    useStore.getState().loadScript(loaded);

    const addedId = useStore.getState().addNode('wait', 640, 240);
    useStore.getState().undo();

    expect(useStore.getState().script.meta.id).toBe('loaded-history-test');
    expect(useStore.getState().script.nodes.some((node) => node.id === addedId)).toBe(false);
  });

  it('saves exactly one canonical Automation IR artifact before clearing dirty state', async () => {
    const { useStore } = await import('../store');
    useStore.getState().newScript();
    useStore.getState().setScriptName('Canonical save');
    useStore.setState((state) => { state.script.meta.updated_at = '2000-01-01T00:00:00.000Z'; });
    mockElectronAPI.saveFileDialog.mockResolvedValueOnce({
      canceled: false,
      filePath: 'C:\\tmp\\canonical.automation.json',
    });
    mockElectronAPI.writeFile.mockClear();
    mockElectronAPI.writeFile.mockResolvedValueOnce({ success: true });

    const result = await useStore.getState().saveScript();

    expect(result).toEqual({ status: 'saved', filePath: 'C:\\tmp\\canonical.automation.json' });
    expect(mockElectronAPI.writeFile).toHaveBeenCalledTimes(1);
    const [filePath, content] = mockElectronAPI.writeFile.mock.calls[0];
    expect(filePath).toBe('C:\\tmp\\canonical.automation.json');
    expect(JSON.parse(content)).toMatchObject({
      format: 'automation-ir/1',
      source: { kind: 'flowscript' },
      runtime: { kind: 'arp_graph' },
    });
    expect(useStore.getState().isDirty).toBe(false);
    expect(useStore.getState().script.meta.updated_at).not.toBe('2000-01-01T00:00:00.000Z');
  });

  it('keeps the script dirty when Electron save is canceled or the write fails', async () => {
    const { useStore } = await import('../store');
    useStore.getState().newScript();
    useStore.getState().setScriptName('Unsaved changes');
    const previousUpdatedAt = useStore.getState().script.meta.updated_at;
    mockElectronAPI.writeFile.mockClear();
    mockElectronAPI.saveFileDialog.mockResolvedValueOnce({ canceled: true });

    await expect(useStore.getState().saveScript()).resolves.toEqual({ status: 'canceled' });
    expect(mockElectronAPI.writeFile).not.toHaveBeenCalled();
    expect(useStore.getState().isDirty).toBe(true);
    expect(useStore.getState().script.meta.updated_at).toBe(previousUpdatedAt);

    mockElectronAPI.saveFileDialog.mockResolvedValueOnce({
      canceled: false,
      filePath: 'C:\\tmp\\failed.automation.json',
    });
    mockElectronAPI.writeFile.mockResolvedValueOnce({ success: false, error: 'disk full' });
    await expect(useStore.getState().saveScript()).resolves.toEqual({ status: 'error', error: 'disk full' });
    expect(useStore.getState().isDirty).toBe(true);
    expect(useStore.getState().script.meta.updated_at).toBe(previousUpdatedAt);
  });

  it('does not mark edits made during an asynchronous save as persisted', async () => {
    const { useStore } = await import('../store');
    useStore.getState().newScript();
    useStore.getState().setScriptName('Snapshot being saved');
    useStore.setState((state) => { state.script.meta.updated_at = '2000-01-01T00:00:00.000Z'; });
    mockElectronAPI.saveFileDialog.mockResolvedValueOnce({
      canceled: false,
      filePath: 'C:\\tmp\\snapshot.automation.json',
    });
    mockElectronAPI.writeFile.mockImplementationOnce(async () => {
      useStore.getState().setScriptName('Edited while saving');
      return { success: true };
    });

    await expect(useStore.getState().saveScript()).resolves.toEqual({
      status: 'saved',
      filePath: 'C:\\tmp\\snapshot.automation.json',
    });

    expect(useStore.getState().script.meta.name).toBe('Edited while saving');
    expect(useStore.getState().script.meta.updated_at).toBe('2000-01-01T00:00:00.000Z');
    expect(useStore.getState().isDirty).toBe(true);
  });

  it('can update node params', async () => {
    const { useStore } = await import('../store');
    useStore.getState().addNode('click', 0, 0);
    const nodeId = useStore.getState().script.nodes.find(n => n.type === 'click' && n.x === 0 && n.y === 0)?.id;
    if (!nodeId) return;

    useStore.getState().updateNodeParams(nodeId, { x: 500, y: 600 });
    const node = useStore.getState().script.nodes.find(n => n.id === nodeId);
    expect(node?.params).toMatchObject({ x: 500, y: 600 });
  });

  it('can add and remove an edge', async () => {
    const { useStore } = await import('../store');
    useStore.getState().newScript();
    const sourceId = useStore.getState().addNode('click', 0, 100);
    const targetId = useStore.getState().addNode('wait', 300, 100);
    const initialEdgeCount = useStore.getState().script.edges.length;

    useStore.getState().addEdge({ from_node: sourceId, from_port: 'output', to_node: targetId, to_port: 'input' });
    expect(useStore.getState().script.edges).toHaveLength(initialEdgeCount + 1);

    const addedEdge = useStore.getState().script.edges.find(
      e => e.from_node === sourceId && e.to_node === targetId
    );
    if (addedEdge) {
      useStore.getState().deleteEdges([addedEdge.id]);
      expect(useStore.getState().script.edges).toHaveLength(initialEdgeCount);
    }
  });

  it('rejects a second route from the same source branch without dirtying or adding history', async () => {
    const { useStore } = await import('../store');
    useStore.getState().newScript();
    const sourceId = useStore.getState().addNode('click', 100, 100);
    const firstTargetId = useStore.getState().addNode('wait', 300, 100);
    const secondTargetId = useStore.getState().addNode('wait', 300, 300);

    useStore.getState().addEdge({
      from_node: sourceId,
      from_port: 'output',
      to_node: firstTargetId,
      to_port: 'input',
    });
    const edgesBeforeConflict = useStore.getState().script.edges.map((edge) => edge.id);
    useStore.setState((state) => { state.isDirty = false; });

    useStore.getState().addEdge({
      from_node: sourceId,
      from_port: 'output',
      to_node: secondTargetId,
      to_port: 'input',
    });

    expect(useStore.getState().script.edges.map((edge) => edge.id)).toEqual(edgesBeforeConflict);
    expect(useStore.getState().isDirty).toBe(false);
    useStore.getState().undo();
    expect(useStore.getState().script.edges.some((edge) => edge.from_node === sourceId)).toBe(false);
  });

  it('deduplicates routes by the compiler branch rather than the raw output port', async () => {
    const { useStore } = await import('../store');
    const { edgeBranch } = await import('../platform/flowToArpGraph');
    useStore.getState().newScript();
    const sourceId = useStore.getState().addNode('condition', 100, 100);
    const yesTargetId = useStore.getState().addNode('wait', 300, 100);
    const duplicateTargetId = useStore.getState().addNode('wait', 300, 200);
    const noTargetId = useStore.getState().addNode('wait', 300, 300);

    useStore.getState().addEdge({
      from_node: sourceId,
      from_port: 'output',
      to_node: yesTargetId,
      to_port: 'input',
      label: 'yes',
    });
    useStore.getState().addEdge({
      from_node: sourceId,
      from_port: 'yes',
      to_node: duplicateTargetId,
      to_port: 'input',
    });
    useStore.getState().addEdge({
      from_node: sourceId,
      from_port: 'output',
      to_node: noTargetId,
      to_port: 'input',
      label: 'no',
    });

    const routes = useStore.getState().script.edges.filter((edge) => edge.from_node === sourceId);
    expect(routes).toHaveLength(2);
    expect(routes.some((edge) => edge.to_node === duplicateTargetId)).toBe(false);
    expect(routes.map((edge) => edgeBranch(edge.from_port, edge.label)).sort()).toEqual(['no', 'yes']);
  });

  it('can select and clear selection', async () => {
    const { useStore } = await import('../store');
    const firstNode = useStore.getState().script.nodes[0];
    if (!firstNode) return;

    useStore.getState().selectNodes([firstNode.id]);
    expect(useStore.getState().ui.selection.nodes).toContain(firstNode.id);

    useStore.getState().clearSelection();
    expect(useStore.getState().ui.selection.nodes).toEqual([]);
  });

  // ── Learned Targets ─────────────────────────────────────

  describe('learned targets', () => {
    it('initial state is empty', async () => {
      const { useStore } = await import('../store');
      const state = useStore.getState();
      expect(state.learnedTargets).toEqual([]);
      expect(state.showLearnedPanel).toBe(false);
    });

    it('toggleLearnedPanel flips visibility', async () => {
      const { useStore } = await import('../store');
      expect(useStore.getState().showLearnedPanel).toBe(false);
      useStore.getState().toggleLearnedPanel();
      expect(useStore.getState().showLearnedPanel).toBe(true);
      useStore.getState().toggleLearnedPanel();
      expect(useStore.getState().showLearnedPanel).toBe(false);
    });

    it('fetchLearnedTargets calls callFeature', async () => {
      const { useStore } = await import('../store');
      const callFeature = mockElectronAPI.callFeature as ReturnType<typeof vi.fn>;
      callFeature.mockResolvedValueOnce({
        success: true,
        result: [
          { target_id: 'test.target', action: 'click', success_count: 5, failure_count: 1, uia: { name: 'Button' } },
        ],
      });

      await useStore.getState().fetchLearnedTargets();
      const state = useStore.getState();
      expect(state.learnedTargets).toHaveLength(1);
      expect(state.learnedTargets[0].target_id).toBe('test.target');
      expect(callFeature).toHaveBeenCalledWith('learned_list', { app: '', limit: 200 });
    });

    it('deleteLearnedTarget calls callFeature and refreshes', async () => {
      const { useStore } = await import('../store');
      const callFeature = mockElectronAPI.callFeature as ReturnType<typeof vi.fn>;
      callFeature
        .mockResolvedValueOnce({ success: true, result: [{ target_id: 'del.me', success_count: 1, failure_count: 0 }] })
        .mockResolvedValueOnce({ success: true })
        .mockResolvedValueOnce({ success: true, result: [] });

      await useStore.getState().fetchLearnedTargets();
      expect(useStore.getState().learnedTargets).toHaveLength(1);

      await useStore.getState().deleteLearnedTarget('del.me');
      expect(useStore.getState().learnedTargets).toHaveLength(0);
      expect(callFeature).toHaveBeenCalledWith('learned_delete', { target_id: 'del.me' });
    });
  });
});
