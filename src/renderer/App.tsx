import { useCallback, useEffect } from 'react';
import { PlatformWorkbench } from './components/platform/PlatformWorkbench';
import { AppErrorBoundary } from './components/common/AppErrorBoundary';
import { confirmDocumentReplacement, useStore } from './store';
import { automationDocumentToFlowScript } from './utils/automationIR';
import { commitEditorDrafts } from './utils/editorDrafts';
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
    const script = automationDocumentToFlowScript(JSON.parse(loaded.content));
    if (!confirmDocumentReplacement()) return;
    useStore.getState().loadScript(script);
  }, []);

  const reportFileError = useCallback((error: unknown) => {
    const message = error instanceof Error ? error.message : String(error);
    console.error(message);
    void window.electronAPI?.showNotification?.('流程文件操作失败', message);
  }, []);

  const saveAutomation = useCallback(async () => {
    const result = await useStore.getState().saveScript();
    if (result.status === 'error') reportFileError(new Error(result.error));
  }, [reportFileError]);

  const newAutomation = useCallback(() => {
    if (!confirmDocumentReplacement()) return;
    useStore.getState().newScript();
  }, []);

  const onKeyDown = useCallback((event: KeyboardEvent) => {
    const store = useStore.getState();
    const target = event.target as HTMLElement | null;
    const typing = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || Boolean(target?.isContentEditable);
    if (event.ctrlKey || event.metaKey) {
      if (event.key.toLowerCase() === 's') { event.preventDefault(); void saveAutomation().catch(reportFileError); }
      if (event.key.toLowerCase() === 'o') { event.preventDefault(); void openAutomation().catch(reportFileError); }
      if (event.key.toLowerCase() === 'n') { event.preventDefault(); newAutomation(); }
    }
    if ((event.ctrlKey || event.metaKey) && !typing) {
      if (event.key.toLowerCase() === 'z') { event.preventDefault(); event.shiftKey ? store.redo() : store.undo(); }
      if (event.key.toLowerCase() === 'y') { event.preventDefault(); store.redo(); }
      if (event.key.toLowerCase() === 'c') { event.preventDefault(); store.copySelected(); }
      if (event.key.toLowerCase() === 'v') { event.preventDefault(); store.paste(); }
    }
    if (!typing && (event.key === 'Delete' || event.key === 'Backspace')) {
      const selected = store.ui.selection;
      if (selected.nodes.length) store.deleteNodes(selected.nodes);
      if (selected.edges.length) store.deleteEdges(selected.edges);
    }
    if (!typing && event.key === 'Escape') store.clearSelection();
  }, [newAutomation, openAutomation, reportFileError, saveAutomation]);

  useEffect(() => {
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onKeyDown]);

  useEffect(() => window.electronAPI?.onMenuAction?.((action) => {
    if (action === 'new') newAutomation();
    if (action === 'open') void openAutomation().catch(reportFileError);
    if (action === 'save') void saveAutomation().catch(reportFileError);
  }), [newAutomation, openAutomation, reportFileError, saveAutomation]);

  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      const draftError = commitEditorDrafts();
      if (!draftError && !useStore.getState().isDirty) return;
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', beforeUnload);
    return () => window.removeEventListener('beforeunload', beforeUnload);
  }, []);

  return <AppErrorBoundary><PlatformWorkbench onOpenAutomation={openAutomation} /></AppErrorBoundary>;
}
