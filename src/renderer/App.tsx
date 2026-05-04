import React, { useEffect, useCallback, useState, useRef } from 'react';
import { NodeLibrary } from './components/NodeLibrary/NodeLibrary';
import { FlowCanvas } from './components/Canvas/FlowCanvas';
import { PropertyPanel } from './components/PropertyPanel/PropertyPanel';
import { TopBar } from './components/common/TopBar';
import { StatusBar } from './components/common/StatusBar';
import { LogPanel } from './components/common/LogPanel';
import { ToolCallPanel } from './components/common/ToolCallPanel';
import { PseudoPanel } from './components/PseudoPanel';
import { AIAssistantDialog } from './components/AIAssistantDialog';
import { SettingsPanel } from './components/SettingsPanel';
import { useStore } from './store';
import './styles/globals.css';

export default function App() {
  const { ui, setUI, setViewMode } = useStore();
  const { viewMode, dark_mode } = ui;
  const [proximity, setProximity] = useState({ top: false, bottom: false, left: false, right: false });
  const hoveredRef = useRef({ top: false, bottom: false, left: false, right: false });
  const [showAIDialog, setShowAIDialog] = useState(false);

  // Mouse proximity tracking — shows panels when mouse nears viewport edges
  // Once shown, panels stay visible until manually hidden via status bar ("收起所有面板")
  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    const { clientX: x, clientY: y } = e;
    const { innerWidth: w, innerHeight: h } = window;
    const hov = hoveredRef.current;
    const inCenterTop = y < 36 && x > 260 && x < w - 320;
    const inTopBarHover = y < 90 && hov.top;

    // Manage hover state
    if (inCenterTop || inTopBarHover) hov.top = true;
    if (!inCenterTop && y > 90) hov.top = false;

    // Latching: only set proximity to true, never false.
    // Panels stay visible once triggered until manually dismissed.
    const newProx: typeof proximity = { top: false, bottom: false, left: false, right: false };
    if (inCenterTop || inTopBarHover) newProx.top = true;
    if (y > h - 36 || hov.bottom) newProx.bottom = true;
    if (x < 36 || hov.left) newProx.left = true;
    if (x > w - 100 || hov.right) newProx.right = true;

    if (newProx.top || newProx.bottom || newProx.left || newProx.right) {
      setProximity(prev => ({ ...prev, ...newProx }));
    }
  }, []);

  // Panel hover tracking — keeps panel visible while mouse is over it
  const handlePanelEnter = useCallback((dir: 'top' | 'bottom' | 'left' | 'right') => {
    hoveredRef.current[dir] = true;
    setProximity(prev => ({ ...prev, [dir]: true }));
  }, []);

  const handlePanelLeave = useCallback((dir: 'top' | 'bottom' | 'left' | 'right') => {
    hoveredRef.current[dir] = false;
  }, []);

  // Manual hide all panels — called from status bar
  const hideAllPanels = useCallback(() => {
    setProximity({ top: false, bottom: false, left: false, right: false });
    hoveredRef.current = { top: false, bottom: false, left: false, right: false };
    setUI({ show_node_library: false, show_property_panel: false, show_log_panel: false });
  }, [setUI]);

  // Apply dark mode class
  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark_mode);
  }, [dark_mode]);

  // Center flowchart on initial mount
  useEffect(() => {
    const { script, fitToScreen } = useStore.getState();
    if (script.nodes.length > 0) {
      requestAnimationFrame(() => fitToScreen());
    }
  }, []);

  // Load feature registry on mount
  useEffect(() => {
    useStore.getState().fetchFeatures();
  }, []);

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
    <div
      className={`app-root${viewMode === 'canvas' ? ' canvas-view' : ''}${proximity.top ? ' proximity-top' : ''}${proximity.bottom ? ' proximity-bottom' : ''}${proximity.left ? ' proximity-left' : ''}${proximity.right ? ' proximity-right' : ''}`}
      onMouseMove={viewMode === 'canvas' ? handleMouseMove : undefined}
    >
      <div className="top-wrapper">
        <TopBar />
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
          className={`view-tab ${showAIDialog ? 'active' : ''}`}
          onClick={() => setShowAIDialog(prev => !prev)}
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
      </div>

      <div className={`app-body${viewMode === 'canvas' ? ' canvas-view-layout' : ''}`}>
        {viewMode === 'settings' ? (
          <SettingsPanel />
        ) : viewMode === 'pseudocode' ? (
          <div className="canvas-area">
            <PseudoPanel />
          </div>
        ) : viewMode === 'dsl' ? (
          <div className="canvas-area">
            <DSLPanel />
          </div>
        ) : (
          <>
            <div onMouseEnter={() => handlePanelEnter('left')} onMouseLeave={() => handlePanelLeave('left')}>
              <NodeLibrary forcedVisible={ui.show_node_library} />
            </div>
            <div className="canvas-area">
              <FlowCanvas />
            </div>
            <div onMouseEnter={() => handlePanelEnter('right')} onMouseLeave={() => handlePanelLeave('right')}>
              <PropertyPanel forcedVisible={ui.show_property_panel} />
            </div>
            {showAIDialog && (
              <div className="ai-dialog-backdrop" onClick={() => setShowAIDialog(false)}>
                <div className="ai-dialog" onClick={e => e.stopPropagation()}>
                  <AIAssistantDialog onClose={() => setShowAIDialog(false)} />
                </div>
              </div>
            )}
          </>
        )}
      </div>
      <div className="bottom-wrapper" onMouseEnter={() => handlePanelEnter('bottom')} onMouseLeave={() => handlePanelLeave('bottom')}>
        <StatusBar onHidePanels={hideAllPanels} />
        {ui.show_tool_call_panel && <ToolCallPanel />}
      </div>
    </div>
  );
}
