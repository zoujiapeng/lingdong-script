import { describe, it, expect, vi } from 'vitest';

// Mock all Electron API methods that the store uses during initialization
const mockCleanup = vi.fn();
const mockElectronAPI = {
  onEngineMessage: vi.fn(() => mockCleanup),
  onEngineLog: vi.fn(() => mockCleanup),
  onAgentMessage: vi.fn(() => mockCleanup),
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
  saveFileDialog: vi.fn(async () => null),
  readFile: vi.fn(async () => ''),
  writeFile: vi.fn(async () => {}),
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
  it('should initialize with a template script and default UI', async () => {
    const { useStore } = await import('../store');
    const state = useStore.getState();

    expect(state.script).toBeDefined();
    // Store loads "desktop-monitor" template by default
    expect(state.script.nodes.length).toBeGreaterThan(0);
    expect(state.script.edges.length).toBeGreaterThan(0);
    expect(state.script.meta.name).toBe('桌面图标监控');
    expect(state.ui).toBeDefined();
    expect(state.ui.dark_mode).toBe(false);
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

  it('newScript resets to the default template', async () => {
    const { useStore } = await import('../store');
    const addedId = useStore.getState().addNode('click', 800, 800);
    expect(useStore.getState().script.nodes.some(n => n.id === addedId)).toBe(true);

    useStore.getState().newScript();
    const state = useStore.getState();
    // newScript loads the "desktop-monitor" template
    expect(state.script.meta.name).toBe('桌面图标监控');
    expect(state.script.nodes.some(n => n.id === addedId)).toBe(false);
    expect(state.isDirty).toBe(false);
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
    const initialEdgeCount = useStore.getState().script.edges.length;

    useStore.getState().addNode('start', 0, 100);
    useStore.getState().addNode('end', 300, 100);
    const nodes = useStore.getState().script.nodes;
    const startNode = nodes.find(n => n.type === 'start');
    const endNode = nodes.find(n => n.type === 'end');
    if (!startNode || !endNode) return;

    useStore.getState().addEdge({ from_node: startNode.id, from_port: 'output', to_node: endNode.id, to_port: 'input' });
    expect(useStore.getState().script.edges).toHaveLength(initialEdgeCount + 1);

    const addedEdge = useStore.getState().script.edges.find(
      e => e.from_node === startNode.id && e.to_node === endNode.id
    );
    if (addedEdge) {
      useStore.getState().deleteEdges([addedEdge.id]);
      expect(useStore.getState().script.edges).toHaveLength(initialEdgeCount);
    }
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
});
