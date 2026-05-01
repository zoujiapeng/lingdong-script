import React, { useEffect, useCallback } from 'react';
import { NodeLibrary } from './components/NodeLibrary/NodeLibrary';
import { FlowCanvas } from './components/Canvas/FlowCanvas';
import { PropertyPanel } from './components/PropertyPanel/PropertyPanel';
import { TopBar } from './components/common/TopBar';
import { StatusBar } from './components/common/StatusBar';
import { LogPanel } from './components/common/LogPanel';
import { PseudoPanel } from './components/PseudoPanel';
import { AIAssistant } from './components/AIAssistant';
import { SettingsPanel } from './components/SettingsPanel';
import { useStore } from './store';
import './styles/globals.css';

export default function App() {
  const { ui, setUI, setViewMode } = useStore();
  const { viewMode, dark_mode } = ui;

  // Apply dark mode class
  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark_mode);
  }, [dark_mode]);

  // Keyboard shortcuts
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      const store = useStore.getState();
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;

      if (e.ctrlKey || e.metaKey) {
        switch (e.key.toLowerCase()) {
          case 'z': e.preventDefault(); e.shiftKey ? store.redo() : store.undo(); break;
          case 'y': e.preventDefault(); store.redo(); break;
          case 'c': e.preventDefault(); store.copySelected(); break;
          case 'x': e.preventDefault(); store.cutSelected(); break;
          case 'v': e.preventDefault(); store.paste(); break;
          case 'a': e.preventDefault(); store.selectAll(); break;
          case 's': e.preventDefault(); store.saveScript(); break;
          case 'd': e.preventDefault(); store.duplicateNodes(store.ui.selection.nodes); break;
        }
      }
      if (e.key === 'Delete' || e.key === 'Backspace') {
        const { nodes, edges } = store.ui.selection;
        if (nodes.length > 0) store.deleteNodes(nodes.filter(id => {
          const n = store.script.nodes.find(n => n.id === id);
          return n && n.type !== 'start' && n.type !== 'end';
        }));
        if (edges.length > 0) store.deleteEdges(edges);
      }
      if (e.key === 'Escape') store.clearSelection();
    },
    []
  );

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);

  return (
    <div className="app-root">
      <TopBar />

      {/* View mode tabs */}
      <div className="view-tabs">
        <button
          className={`view-tab ${viewMode === 'canvas' ? 'active' : ''}`}
          onClick={() => setViewMode('canvas')}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ marginRight: 6 }}>
            <rect x="3" y="3" width="7" height="7" rx="1" />
            <rect x="14" y="3" width="7" height="7" rx="1" />
            <rect x="3" y="14" width="7" height="7" rx="1" />
            <rect x="14" y="14" width="7" height="7" rx="1" />
          </svg>
          流程图
        </button>
        <button
          className={`view-tab ${viewMode === 'pseudocode' ? 'active' : ''}`}
          onClick={() => setViewMode('pseudocode')}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ marginRight: 6 }}>
            <polyline points="16 18 22 12 16 6" />
            <polyline points="8 6 2 12 8 18" />
          </svg>
          伪代码
        </button>
        <button
          className={`view-tab ${viewMode === 'ai' ? 'active' : ''}`}
          onClick={() => setViewMode('ai')}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ marginRight: 6 }}>
            <path d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z" />
            <path d="M18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 00-2.455 2.456z" />
          </svg>
          AI 助手
        </button>
        <div className="view-tabs-spacer" />
        <button
          className={`view-tab ${viewMode === 'settings' ? 'active' : ''} view-tab-settings`}
          onClick={() => setViewMode(viewMode === 'settings' ? 'canvas' : 'settings')}
          title="设置"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ marginRight: 6 }}>
            <circle cx="12" cy="12" r="3" />
            <path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" />
          </svg>
          设置
        </button>
      </div>

      <div className="app-body">
        {viewMode === 'settings' ? (
          <SettingsPanel />
        ) : viewMode === 'ai' ? (
          <div className="canvas-area">
            <AIAssistant />
            <LogPanel />
          </div>
        ) : (
          <>
            <NodeLibrary />
            <div className="canvas-area">
              {viewMode === 'canvas' ? (
                <FlowCanvas />
              ) : (
                <PseudoPanel />
              )}
              <LogPanel />
            </div>
            <PropertyPanel />
          </>
        )}
      </div>
      <StatusBar />
    </div>
  );
}
