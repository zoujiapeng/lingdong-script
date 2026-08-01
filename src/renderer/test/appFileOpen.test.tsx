import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const harness = vi.hoisted(() => ({
  openAutomation: null as null | (() => Promise<void>),
  confirm: vi.fn(() => true),
  state: {
    isDirty: false,
    ui: {
      themeMode: 'light',
      dark_mode: false,
      selection: { nodes: [], edges: [] },
    },
    setUI: vi.fn(),
    loadScript: vi.fn(),
    saveScript: vi.fn(async () => ({ status: 'canceled' as const })),
    newScript: vi.fn(),
    undo: vi.fn(),
    redo: vi.fn(),
    copySelected: vi.fn(),
    paste: vi.fn(),
    deleteNodes: vi.fn(),
    deleteEdges: vi.fn(),
    clearSelection: vi.fn(),
  },
}));

vi.mock('../store', () => ({
  confirmDocumentReplacement: harness.confirm,
  useStore: Object.assign(
    (selector: (state: typeof harness.state) => unknown) => selector(harness.state),
    { getState: () => harness.state },
  ),
}));

vi.mock('../components/platform/PlatformWorkbench', () => ({
  PlatformWorkbench: (props: { onOpenAutomation?: () => Promise<void> }) => {
    harness.openAutomation = props.onOpenAutomation || null;
    return null;
  },
}));

import App from '../App';
import { registerEditorDraftCommitter } from '../utils/editorDrafts';

const validFlow = JSON.stringify({
  meta: { id: 'loaded', name: 'Loaded', created_at: '', updated_at: '', version: '1.0.0' },
  nodes: [
    { id: 'start', type: 'start', x: 0, y: 0, params: {} },
    { id: 'end', type: 'end', x: 200, y: 0, params: {} },
  ],
  edges: [
    { id: 'edge', from_node: 'start', from_port: 'output', to_node: 'end', to_port: 'input' },
  ],
  variables: {},
  settings: {
    default_delay: 0,
    error_handling: 'stop',
    max_retries: 0,
    retry_delay: 0,
    log_level: 'info',
  },
});

function electronApi() {
  return {
    openFileDialog: vi.fn(),
    readFile: vi.fn(),
    setTitleBarTheme: vi.fn(),
    onMenuAction: vi.fn(() => () => undefined),
    showNotification: vi.fn(),
  };
}

beforeEach(() => {
  harness.openAutomation = null;
  harness.state.isDirty = false;
  harness.confirm.mockReset().mockReturnValue(true);
  for (const value of Object.values(harness.state)) {
    if (typeof value === 'function' && 'mockClear' in value) value.mockClear();
  }
  vi.stubGlobal('matchMedia', vi.fn(() => ({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete (window as any).electronAPI;
});

describe('App file-open replacement boundary', () => {
  it('prevents window unload while an editor draft cannot be committed', () => {
    render(<App />);
    const unregister = registerEditorDraftCommitter(() => 'step 必须是有效 JSON。');
    const blocked = new Event('beforeunload', { cancelable: true });

    expect(window.dispatchEvent(blocked)).toBe(false);
    expect(blocked.defaultPrevented).toBe(true);

    unregister();
    const allowed = new Event('beforeunload', { cancelable: true });
    expect(window.dispatchEvent(allowed)).toBe(true);
    expect(allowed.defaultPrevented).toBe(false);
  });

  it('prevents window unload while the canonical document is dirty', () => {
    harness.state.isDirty = true;
    render(<App />);
    const blocked = new Event('beforeunload', { cancelable: true });

    expect(window.dispatchEvent(blocked)).toBe(false);
    expect(blocked.defaultPrevented).toBe(true);
    harness.state.isDirty = false;
  });

  it('does not ask to discard edits when the file picker is canceled', async () => {
    const api = electronApi();
    api.openFileDialog.mockResolvedValue({ canceled: true, filePaths: [] });
    (window as any).electronAPI = api;
    render(<App />);

    await act(async () => { await harness.openAutomation?.(); });

    expect(harness.confirm).not.toHaveBeenCalled();
    expect(api.readFile).not.toHaveBeenCalled();
    expect(harness.state.loadScript).not.toHaveBeenCalled();
  });

  it('does not ask to discard edits when the selected document cannot be parsed', async () => {
    const api = electronApi();
    api.openFileDialog.mockResolvedValue({ canceled: false, filePaths: ['broken.json'] });
    api.readFile.mockResolvedValue({ content: '{not-json' });
    (window as any).electronAPI = api;
    render(<App />);

    await expect(harness.openAutomation?.()).rejects.toBeInstanceOf(SyntaxError);

    expect(harness.confirm).not.toHaveBeenCalled();
    expect(harness.state.loadScript).not.toHaveBeenCalled();
  });

  it('asks only after a valid document is ready and immediately before replacing the store', async () => {
    const api = electronApi();
    api.openFileDialog.mockResolvedValue({ canceled: false, filePaths: ['valid.json'] });
    api.readFile.mockResolvedValue({ content: validFlow });
    harness.confirm.mockReturnValueOnce(false).mockReturnValueOnce(true);
    (window as any).electronAPI = api;
    render(<App />);

    await act(async () => { await harness.openAutomation?.(); });
    expect(api.readFile.mock.invocationCallOrder[0]).toBeLessThan(
      harness.confirm.mock.invocationCallOrder[0],
    );
    expect(harness.state.loadScript).not.toHaveBeenCalled();

    await act(async () => { await harness.openAutomation?.(); });
    expect(harness.state.loadScript).toHaveBeenCalledOnce();
    expect(harness.confirm.mock.invocationCallOrder[1]).toBeLessThan(
      harness.state.loadScript.mock.invocationCallOrder[0],
    );
  });
});
