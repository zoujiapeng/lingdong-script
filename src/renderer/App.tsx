import React, { useEffect, useCallback } from 'react';
import { NodeLibrary } from './components/NodeLibrary/NodeLibrary';
import { FlowCanvas } from './components/Canvas/FlowCanvas';
import { PropertyPanel } from './components/PropertyPanel/PropertyPanel';
import { TopBar } from './components/common/TopBar';
import { StatusBar } from './components/common/StatusBar';
import { LogPanel } from './components/common/LogPanel';
import { PseudoPanel } from './components/PseudoPanel';
import { useStore } from './store';
import './styles/globals.css';

export default function App() {
  const { ui, setUI, setViewMode } = useStore();
  const { viewMode } = ui;

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
      </div>

      <div className="app-body">
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
      </div>
      <StatusBar />
    </div>
  );
}
