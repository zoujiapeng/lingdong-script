import React, { useEffect, useCallback } from 'react';
import { TriggerBar } from './components/common/TriggerBar';
import { TriggerConfigPanel } from './components/common/TriggerConfigPanel';
import { ToolCallPanel } from './components/common/ToolCallPanel';
import { ParamPromptDialog } from './components/common/ParamPromptDialog';
import {
  CommandDock,
  NebulaBackdrop,
  UnifiedWorkbenchView,
} from './components/workbench/WorkbenchPanels';
import { useStore } from './store';
import { htnRegistry } from './utils/htnRegistry';
import { ALL_HTN_DEFS } from './utils/htnTemplateDefs';
import { loadTemplateShortcuts } from './store';
import { matchSemantic } from './utils/semanticMatcher';
import { unifiedSearch } from './utils/unifiedSearch';
import { llmEnhanceSearch } from './utils/llmSearcher';
import './styles/globals.css';

export default function App() {
  const viewMode = useStore(s => s.ui.viewMode);
  const themeMode = useStore(s => s.ui.themeMode);
  const panelsVisible = useStore(s => s.ui.panels_visible);
  const showToolCallPanel = useStore(s => s.ui.show_tool_call_panel);
  const setUI = useStore(s => s.setUI);
  const setViewMode = useStore(s => s.setViewMode);

  // Apply dark mode based on themeMode + system preference
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const update = () => {
      let effectiveDark: boolean;
      if (themeMode === 'system') {
        effectiveDark = mq.matches;
      } else {
        effectiveDark = themeMode === 'dark';
      }
      document.documentElement.classList.toggle('dark', effectiveDark);
      // Sync dark_mode for backward compat (store auto-save, HTTP API)
      const current = useStore.getState().ui.dark_mode;
      if (current !== effectiveDark) {
        useStore.getState().setUI({ dark_mode: effectiveDark });
      }
    };
    update();
    mq.addEventListener('change', update);
    return () => mq.removeEventListener('change', update);
  }, [themeMode, setUI]);

  // Persist themeMode to localStorage
  useEffect(() => {
    try { localStorage.setItem('lingdong-theme-mode', themeMode); } catch {}
  }, [themeMode]);

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

  // Initialize HTN registry on mount
  useEffect(() => {
    // Register all base primitives and composite templates
    for (const def of ALL_HTN_DEFS) {
      htnRegistry.registerFromYaml(def);
    }
    // Auto-register from existing FlowScript templates (sub_script refs)
    htnRegistry.autoRegisterFromTemplates();

    // Expose search function for main process HTTP API access
    (window as any).__searchTemplates = async (query: string, maxResults?: number) => {
      try {
        // Build unified search index (includes features + templates)
        const store = useStore.getState();
        unifiedSearch.rebuildIndex(store.featureList.length > 0 ? store.featureList as any : undefined);

        // Offline unified search
        const limit = maxResults || 5;
        const unified = unifiedSearch.search(query, { maxResults: limit * 2 });

        // HTN search (kept for backward compatibility)
        const htnResults = htnRegistry.search(query);

        // Try LLM enhancement if confidence is low
        const apiKey = (window as any).__llmApiKey || '';
        let results = unified;
        let enhanced = false;
        if (apiKey && (unified.length === 0 || unified[0].score < 60)) {
          const llmResult = await llmEnhanceSearch(query, unified, apiKey);
          if (llmResult.enhanced) {
            results = llmResult.results;
            enhanced = true;
          }
        }

        const topResults = results.slice(0, limit);

        return JSON.stringify({
          htn: htnResults.slice(0, limit).map((r: any) => ({
            id: r.node.id,
            name: r.node.name,
            description: r.node.description,
            score: r.score,
            reasons: r.reasons,
            kind: r.node.kind,
            steps: (r.node.children || []).map((c: any) => ({
              childId: c.childId,
              description: c.description,
              optional: c.optional,
            })),
          })),
          flat: topResults
            .filter(r => r.item.type === 'template')
            .map((r: any) => ({
              id: r.item.id,
              name: r.item.name,
              description: r.item.description,
              score: r.score,
              reasons: r.reasons,
              hasScript: r.item.hasScript,
              tested: r.item.tested,
            })),
          features: topResults
            .filter(r => r.item.type === 'feature')
            .map((r: any) => ({
              id: r.item.id,
              name: r.item.name,
              featureName: r.item.featureName,
              description: r.item.description,
              score: r.score,
              reasons: r.reasons,
              tested: r.item.tested,
            })),
          autoSelect: topResults.length > 0 && topResults[0].score >= 90,
          enhanced,
        });
      } catch (e: any) {
        return JSON.stringify({ error: e.message });
      }
    };

    // Expose API key from settings for LLM search
    try {
      const key = localStorage.getItem('lingdong-llm-api-key');
      if (key) (window as any).__llmApiKey = key;
    } catch {};
  }, []);

  // Keyboard shortcuts
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      const store = useStore.getState();
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;

      if (e.key === 'Tab') {
        e.preventDefault();
        const next = !store.ui.panels_visible;
        if (!next) {
          // 隐藏时同时关闭所有独立面板
          store.setUI({ panels_visible: false, show_node_library: false, show_property_panel: false, show_log_panel: false, show_tool_call_panel: false, show_trigger_config: false });
        } else {
          store.setUI({ panels_visible: true });
        }
        return;
      }

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
          case 'k': e.preventDefault(); useStore.getState().setUI({ show_unified_search: true }); break;
        }
      }
      // F10: Step over (debugger)
      if (e.key === 'F10') {
        e.preventDefault();
        store.stepOver();
        return;
      }
      // F5: Run
      if (e.key === 'F5') {
        e.preventDefault();
        if (store.execution.status === 'idle' || store.execution.status === 'stopped' || store.execution.status === 'completed') {
          store.startExecution();
        } else {
          store.stopExecution();
        }
        return;
      }
      // Template shortcuts
      const shortcuts = loadTemplateShortcuts();
      const parts: string[] = [];
      if (e.ctrlKey || e.metaKey) parts.push('Ctrl');
      if (e.shiftKey) parts.push('Shift');
      if (e.altKey) parts.push('Alt');
      if (!['Control', 'Shift', 'Alt', 'Meta'].includes(e.key)) {
        parts.push(e.key.length === 1 ? e.key.toUpperCase() : e.key);
        const combo = parts.join('+');
        for (const [tplId, sc] of Object.entries(shortcuts)) {
          if (sc === combo && store.script.nodes.filter(n => n.type !== 'start' && n.type !== 'end').length === 0) {
            e.preventDefault();
            store.loadTemplate(tplId);
            store.setViewMode('canvas');
            return;
          }
        }
        // Also allow executing shortcut even if canvas has content (re-execute template check)
        for (const [tplId, sc] of Object.entries(shortcuts)) {
          if (sc === combo) {
            e.preventDefault();
            store.loadTemplate(tplId);
            store.setViewMode('canvas');
            return;
          }
        }
      }

      if (!e.ctrlKey && !e.metaKey && !e.altKey) {
        const lensShortcuts: Record<string, 'overview' | 'canvas' | 'replay' | 'settings'> = {
          '1': 'overview',
          '2': 'canvas',
          '3': 'replay',
          '4': 'settings',
        };
        const nextMode = lensShortcuts[e.key];
        if (nextMode) {
          e.preventDefault();
          store.setViewMode(nextMode);
          if (nextMode === 'overview') {
            store.setUI({ panels_visible: false, show_node_library: false, show_property_panel: false });
          }
          if (nextMode === 'canvas') {
            store.setUI({ panels_visible: true, show_node_library: false, show_property_panel: false });
            requestAnimationFrame(() => useStore.getState().fitToScreen());
          }
          return;
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
      if (e.key === 'Escape') {
        if (store.ui.show_tool_call_panel) {
          store.setUI({ show_tool_call_panel: false });
          return;
        }
        if (['canvas', 'pseudocode', 'replay', 'settings'].includes(store.ui.viewMode)) {
          store.setViewMode('overview');
          store.setUI({ panels_visible: false, show_node_library: false, show_property_panel: false });
        }
        store.clearSelection();
      }
    },
    []
  );

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);

  return (
    <div className={`app-root wb-root wb-root-unified${viewMode === 'canvas' ? ' canvas-view' : ''}${panelsVisible ? ' panels-visible' : ''}`}>
      <NebulaBackdrop />
      <div className="wb-main">
        <CommandDock />
        <TriggerBar />
        <TriggerConfigPanel />

        <div className={`app-body wb-content wb-unified-content${viewMode === 'canvas' ? ' canvas-view-layout' : ''}`}>
          <UnifiedWorkbenchView />
        </div>

        <div className="bottom-wrapper wb-bottom">
          {showToolCallPanel && <ToolCallPanel />}
        </div>
      </div>
      <ParamPromptDialog />
    </div>
  );
}
