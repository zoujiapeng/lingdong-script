import { useCallback, useEffect } from 'react';
import { PlatformWorkbench } from './components/platform/PlatformWorkbench';
import { AppErrorBoundary } from './components/common/AppErrorBoundary';
import { useStore } from './store';
import { automationDocumentToFlowScript } from './utils/automationIR';
import './styles/globals.css';
import './styles/platform.css';
import './styles/floating.css';

document.documentElement.classList.toggle('electron-frame', Boolean(window.electronAPI));

export default function App() {
  const themeMode = useStore((state) => state.ui.themeMode);

  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const apply = () => {
      const dark = themeMode === 'dark' || (themeMode === 'system' && media.matches);
      document.documentElement.classList.toggle('dark', dark);
      void window.electronAPI?.setTitleBarTheme?.(dark ? 'dark' : 'light');
      if (useStore.getState().ui.dark_mode !== dark) useStore.getState().setUI({ dark_mode: dark });
    };
    apply();
    media.addEventListener('change', apply);
    return () => media.removeEventListener('change', apply);
  }, [themeMode]);

  useEffect(() => {
    try { localStorage.setItem('lingdong-theme-mode', themeMode); } catch { /* storage can be disabled */ }
  }, [themeMode]);

  const openAutomation = useCallback(async () => {
    const api = window.electronAPI;
    if (!api?.openFileDialog || !api.readFile) return;
    const selected = await api.openFileDialog([{ name: '自动化流程', extensions: ['json'] }]);
    if (selected.canceled || !selected.filePaths[0]) return;
    const loaded = await api.readFile(selected.filePaths[0]);
    if (loaded.error || !loaded.content) throw new Error(loaded.error || '自动化文件为空');
    useStore.getState().loadScript(automationDocumentToFlowScript(JSON.parse(loaded.content)));
  }, []);

  const onKeyDown = useCallback((event: KeyboardEvent) => {
    const store = useStore.getState();
    const typing = event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement;
    if ((event.ctrlKey || event.metaKey) && !typing) {
      if (event.key.toLowerCase() === 's') { event.preventDefault(); store.saveScript(); }
      if (event.key.toLowerCase() === 'o') { event.preventDefault(); void openAutomation().catch(console.error); }
      if (event.key.toLowerCase() === 'z') { event.preventDefault(); event.shiftKey ? store.redo() : store.undo(); }
      if (event.key.toLowerCase() === 'y') { event.preventDefault(); store.redo(); }
      if (event.key.toLowerCase() === 'c') { event.preventDefault(); store.copySelected(); }
      if (event.key.toLowerCase() === 'v') { event.preventDefault(); store.paste(); }
    }
    if (!typing && (event.key === 'Delete' || event.key === 'Backspace')) {
      const selected = store.ui.selection;
      const removable = selected.nodes.filter((id) => !['start', 'end'].includes(store.script.nodes.find((node) => node.id === id)?.type || ''));
      if (removable.length) store.deleteNodes(removable);
      if (selected.edges.length) store.deleteEdges(selected.edges);
    }
    if (!typing && event.key === 'Escape') store.clearSelection();
  }, [openAutomation]);

  useEffect(() => {
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onKeyDown]);

  useEffect(() => window.electronAPI?.onMenuAction?.((action) => {
    if (action === 'new') useStore.getState().newScript();
    if (action === 'open') void openAutomation().catch(console.error);
    if (action === 'save') void useStore.getState().saveScript();
  }), [openAutomation]);

  return <AppErrorBoundary><PlatformWorkbench /></AppErrorBoundary>;
}
