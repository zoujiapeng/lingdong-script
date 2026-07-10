import React, { useCallback, useEffect } from 'react';
import { PlatformWorkbench } from './components/platform/PlatformWorkbench';
import { useStore } from './store';
import './styles/globals.css';
import './styles/platform.css';

export default function App() {
  const themeMode = useStore((state) => state.ui.themeMode);

  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const apply = () => {
      const dark = themeMode === 'dark' || (themeMode === 'system' && media.matches);
      document.documentElement.classList.toggle('dark', dark);
      if (useStore.getState().ui.dark_mode !== dark) useStore.getState().setUI({ dark_mode: dark });
    };
    apply();
    media.addEventListener('change', apply);
    return () => media.removeEventListener('change', apply);
  }, [themeMode]);

  useEffect(() => {
    try { localStorage.setItem('lingdong-theme-mode', themeMode); } catch { /* storage can be disabled */ }
  }, [themeMode]);

  const onKeyDown = useCallback((event: KeyboardEvent) => {
    const store = useStore.getState();
    const typing = event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement;
    if ((event.ctrlKey || event.metaKey) && !typing) {
      if (event.key.toLowerCase() === 's') { event.preventDefault(); store.saveScript(); }
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
  }, []);

  useEffect(() => {
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onKeyDown]);

  return <PlatformWorkbench />;
}
