import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AgentView } from './components/AgentView';
import { ParamPromptDialog } from './components/common/ParamPromptDialog';
import { useStore, loadTemplateShortcuts } from './store';
import { PRESET_TEMPLATE_META } from './store/defaults';
import type { FlowEdge, FlowNode, FlowScript, NodeDefinition, NodeType } from './types';
import { NODE_DEFINITIONS } from './utils/nodeDefinitions';
import { generateText, type TextMode } from './utils/textGenerator';
import { htnRegistry } from './utils/htnRegistry';
import { ALL_HTN_DEFS } from './utils/htnTemplateDefs';
import { unifiedSearch } from './utils/unifiedSearch';
import { llmEnhanceSearch } from './utils/llmSearcher';
import { wpsToFlowScript, type WpsScript } from './utils/wpsToFlowScript';
import './styles/globals.css';

type FocusZone = 'flow' | 'agent' | 'replay' | 'code' | 'settings';
type ThemeMode = 'light' | 'dark' | 'system';

const NODE_CARD_WIDTH = 236;
const NODE_CARD_HEIGHT = 92;
const FOCUS_TO_VIEW: Record<FocusZone, 'canvas' | 'pseudocode' | 'settings' | 'agent' | 'replay'> = {
  flow: 'canvas',
  agent: 'agent',
  replay: 'replay',
  code: 'pseudocode',
  settings: 'settings',
};
const VIEW_TO_FOCUS: Record<'canvas' | 'pseudocode' | 'settings' | 'agent' | 'replay', FocusZone> = {
  canvas: 'flow',
  agent: 'agent',
  replay: 'replay',
  pseudocode: 'code',
  settings: 'settings',
};

const CATEGORY_LABELS: Record<NodeDefinition['category'], string> = {
  basic: '基础动作',
  image: '视觉感知',
  control: '控制结构',
  device: '设备输入',
  data: '数据变量',
  system: '系统能力',
  tools: '工具桥接',
};

function nodeTitle(node: FlowNode) {
  return node.label || (node.params as any)?.label || NODE_DEFINITIONS[node.type]?.label || node.type;
}

function nodeDescription(node: FlowNode) {
  return (node.params as any)?.description || NODE_DEFINITIONS[node.type]?.description || 'FlowScript 节点';
}

function stringifyParam(value: unknown) {
  if (typeof value === 'string') return value;
  if (value === undefined || value === null) return '';
  if (typeof value === 'object') return JSON.stringify(value, null, 2);
  return String(value);
}

function parseMaybeJson(text: string, fallback: unknown) {
  try {
    return JSON.parse(text);
  } catch {
    return fallback;
  }
}

function statusLabel(status?: string) {
  const map: Record<string, string> = {
    idle: '待命',
    running: '运行中',
    paused: '暂停',
    stopped: '已停止',
    completed: '完成',
    error: '错误',
    success: '成功',
    skipped: '跳过',
  };
  return map[status || 'idle'] || status || '待命';
}

export default function App() {
  const viewMode = useStore(s => s.ui.viewMode);
  const themeMode = useStore(s => s.ui.themeMode);
  const panelsVisible = useStore(s => s.ui.panels_visible);
  const executionStatus = useStore(s => s.execution.status);
  const isDirty = useStore(s => s.isDirty);
  const script = useStore(s => s.script);
  const setUI = useStore(s => s.setUI);
  const setViewMode = useStore(s => s.setViewMode);
  const fetchFeatures = useStore(s => s.fetchFeatures);
  const fitToScreen = useStore(s => s.fitToScreen);
  const saveScript = useStore(s => s.saveScript);
  const newScript = useStore(s => s.newScript);
  const startExecution = useStore(s => s.startExecution);
  const stopExecution = useStore(s => s.stopExecution);
  const undo = useStore(s => s.undo);
  const redo = useStore(s => s.redo);
  const copySelected = useStore(s => s.copySelected);
  const paste = useStore(s => s.paste);
  const cutSelected = useStore(s => s.cutSelected);
  const selectAll = useStore(s => s.selectAll);
  const duplicateNodes = useStore(s => s.duplicateNodes);
  const deleteNodes = useStore(s => s.deleteNodes);
  const deleteEdges = useStore(s => s.deleteEdges);
  const clearSelection = useStore(s => s.clearSelection);

  const [focus, setFocus] = useState<FocusZone>(VIEW_TO_FOCUS[viewMode]);
  const [spotlight, setSpotlight] = useState(true);

  const chooseFocus = useCallback((next: FocusZone) => {
    setFocus(next);
    setViewMode(FOCUS_TO_VIEW[next]);
  }, [setViewMode]);

  useEffect(() => {
    setFocus(VIEW_TO_FOCUS[viewMode]);
  }, [viewMode]);

  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const update = () => {
      const effectiveDark = themeMode === 'system' ? mq.matches : themeMode === 'dark';
      document.documentElement.classList.toggle('dark', effectiveDark);
      const current = useStore.getState().ui.dark_mode;
      if (current !== effectiveDark) {
        useStore.getState().setUI({ dark_mode: effectiveDark });
      }
    };
    update();
    mq.addEventListener('change', update);
    return () => mq.removeEventListener('change', update);
  }, [themeMode]);

  useEffect(() => {
    try {
      localStorage.setItem('lingdong-theme-mode', themeMode);
    } catch {
      // ignore storage failures in sandboxed/web previews
    }
  }, [themeMode]);

  useEffect(() => {
    fetchFeatures();
    if (useStore.getState().script.nodes.length > 0) {
      requestAnimationFrame(() => fitToScreen());
    }
  }, [fetchFeatures, fitToScreen]);

  useEffect(() => {
    for (const def of ALL_HTN_DEFS) {
      htnRegistry.registerFromYaml(def);
    }
    htnRegistry.autoRegisterFromTemplates();

    (window as any).__searchTemplates = async (query: string, maxResults?: number) => {
      try {
        const store = useStore.getState();
        unifiedSearch.rebuildIndex(store.featureList.length > 0 ? store.featureList as any : undefined);

        const limit = maxResults || 5;
        const unified = unifiedSearch.search(query, { maxResults: limit * 2 });
        const htnResults = htnRegistry.search(query);

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

    try {
      const key = localStorage.getItem('lingdong-llm-api-key');
      if (key) (window as any).__llmApiKey = key;
    } catch {
      // ignore
    }
  }, []);

  const cycleFocus = useCallback(() => {
    const order: FocusZone[] = ['flow', 'agent', 'replay', 'code', 'settings'];
    const idx = order.indexOf(focus);
    chooseFocus(order[(idx + 1) % order.length]);
  }, [chooseFocus, focus]);

  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement) return;
    const store = useStore.getState();

    if (e.key === 'Tab') {
      e.preventDefault();
      cycleFocus();
      return;
    }

    if (e.key === '1') { chooseFocus('flow'); return; }
    if (e.key === '2') { chooseFocus('agent'); return; }
    if (e.key === '3') { chooseFocus('replay'); return; }
    if (e.key === '4') { chooseFocus('code'); return; }
    if (e.key === '5') { chooseFocus('settings'); return; }

    if (e.ctrlKey || e.metaKey) {
      switch (e.key.toLowerCase()) {
        case 'z': e.preventDefault(); e.shiftKey ? redo() : undo(); break;
        case 'y': e.preventDefault(); redo(); break;
        case 'c': e.preventDefault(); copySelected(); break;
        case 'x': e.preventDefault(); cutSelected(); break;
        case 'v': e.preventDefault(); paste(); break;
        case 'a': e.preventDefault(); selectAll(); break;
        case 's': e.preventDefault(); saveScript(); break;
        case 'd': e.preventDefault(); duplicateNodes(store.ui.selection.nodes); break;
        case 'k': e.preventDefault(); setSpotlight(v => !v); break;
      }
    }

    if (e.key === 'F5') {
      e.preventDefault();
      if (store.execution.status === 'idle' || store.execution.status === 'stopped' || store.execution.status === 'completed') {
        startExecution();
      } else {
        stopExecution();
      }
      return;
    }

    if (e.key === 'F10') {
      e.preventDefault();
      store.stepOver();
      return;
    }

    const shortcuts = loadTemplateShortcuts();
    const parts: string[] = [];
    if (e.ctrlKey || e.metaKey) parts.push('Ctrl');
    if (e.shiftKey) parts.push('Shift');
    if (e.altKey) parts.push('Alt');
    if (!['Control', 'Shift', 'Alt', 'Meta'].includes(e.key)) {
      parts.push(e.key.length === 1 ? e.key.toUpperCase() : e.key);
      const combo = parts.join('+');
      for (const [tplId, sc] of Object.entries(shortcuts)) {
        if (sc === combo) {
          e.preventDefault();
          store.loadTemplate(tplId);
          chooseFocus('flow');
          return;
        }
      }
    }

    if (e.key === 'Delete' || e.key === 'Backspace') {
      const { nodes, edges } = store.ui.selection;
      if (nodes.length > 0) {
        deleteNodes(nodes.filter(id => {
          const n = store.script.nodes.find(node => node.id === id);
          return n && n.type !== 'start' && n.type !== 'end';
        }));
      }
      if (edges.length > 0) deleteEdges(edges);
    }

    if (e.key === 'Escape') clearSelection();
  }, [
    chooseFocus,
    clearSelection,
    copySelected,
    cutSelected,
    cycleFocus,
    deleteEdges,
    deleteNodes,
    duplicateNodes,
    paste,
    redo,
    saveScript,
    selectAll,
    startExecution,
    stopExecution,
    undo,
  ]);

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);

  const running = executionStatus === 'running' || executionStatus === 'paused';

  return (
    <div className={`ld-shell focus-${focus}${panelsVisible ? ' panels-visible' : ''}${spotlight ? ' spotlight-on' : ' spotlight-off'}`}>
      <div className="ambient-orb orb-one" />
      <div className="ambient-orb orb-two" />
      <CommandSpine
        focus={focus}
        chooseFocus={chooseFocus}
        dirty={isDirty}
        running={running}
        onNew={newScript}
        onSave={saveScript}
        onRun={() => running ? stopExecution() : startExecution()}
      />
      <main className="ld-workspace" aria-label="灵动脚本连续工作空间">
        <section className="hero-plate">
          <div>
            <p className="eyebrow">Lingdong Script / WinPilot</p>
            <h1>{script.meta.name}</h1>
            <p>{script.meta.description || '把 Agent、录制、流程图、伪代码和设置压缩在同一个可变形工作台。'}</p>
          </div>
          <div className="hero-actions">
            <button className="neo-button ghost" onClick={() => setUI({ panels_visible: !panelsVisible })}>
              {panelsVisible ? '收起浮层' : '释放浮层'}
            </button>
            <button className="neo-button primary" onClick={() => running ? stopExecution() : startExecution()}>
              {running ? '停止运行' : '运行 F5'}
            </button>
          </div>
        </section>

        <FlowDeck focus={focus} chooseFocus={chooseFocus} />
        <AgentCapsule focused={focus === 'agent'} onFocus={() => chooseFocus('agent')} />
        <ReplayDock focused={focus === 'replay'} setFocus={chooseFocus} />
        <CodeLens focused={focus === 'code'} />
        <SettingsDock focused={focus === 'settings'} />
        <InspectorOrb focused={focus === 'flow'} />
        <MetricsShelf />
      </main>
      <ParamPromptDialog />
    </div>
  );
}

interface CommandSpineProps {
  focus: FocusZone;
  chooseFocus: (zone: FocusZone) => void;
  dirty: boolean;
  running: boolean;
  onNew: () => void;
  onSave: () => void;
  onRun: () => void;
}

function CommandSpine({ focus, chooseFocus, dirty, running, onNew, onSave, onRun }: CommandSpineProps) {
  const items: Array<{ id: FocusZone; label: string; icon: string; hint: string }> = [
    { id: 'flow', label: '流程', icon: '⌁', hint: '1' },
    { id: 'agent', label: 'Agent', icon: '◉', hint: '2' },
    { id: 'replay', label: '录制', icon: '◆', hint: '3' },
    { id: 'code', label: '伪码', icon: '⌘', hint: '4' },
    { id: 'settings', label: '设置', icon: '⚙', hint: '5' },
  ];

  return (
    <aside className="command-spine">
      <div className="brand-glyph">
        <span>灵</span>
        {dirty && <i />}
      </div>
      <nav>
        {items.map(item => (
          <button
            key={item.id}
            className={`spine-item${focus === item.id ? ' active' : ''}`}
            onClick={() => chooseFocus(item.id)}
            title={`${item.label} · ${item.hint}`}
          >
            <b>{item.icon}</b>
            <span>{item.label}</span>
            <em>{item.hint}</em>
          </button>
        ))}
      </nav>
      <div className="spine-actions">
        <button onClick={onNew} title="新建脚本">＋</button>
        <button onClick={onSave} title="保存脚本">⇩</button>
        <button className={running ? 'danger' : 'run'} onClick={onRun} title="运行 / 停止">
          {running ? '■' : '▶'}
        </button>
      </div>
    </aside>
  );
}

function FlowDeck({ focus, chooseFocus }: { focus: FocusZone; chooseFocus: (zone: FocusZone) => void }) {
  const script = useStore(s => s.script);
  const transform = useStore(s => s.ui.canvas_transform);
  const selection = useStore(s => s.ui.selection);
  const execution = useStore(s => s.execution);
  const setCanvasTransform = useStore(s => s.setCanvasTransform);
  const fitToScreen = useStore(s => s.fitToScreen);
  const zoomIn = useStore(s => s.zoomIn);
  const zoomOut = useStore(s => s.zoomOut);
  const clearSelection = useStore(s => s.clearSelection);
  const selectNodes = useStore(s => s.selectNodes);
  const selectEdges = useStore(s => s.selectEdges);
  const moveNode = useStore(s => s.moveNode);

  const dragRef = useRef<{ id: string; startX: number; startY: number; nodeX: number; nodeY: number; scale: number } | null>(null);
  const panRef = useRef<{ startX: number; startY: number; x: number; y: number } | null>(null);

  const selectedId = selection.nodes[0];
  const currentNode = execution.current_node_id;

  const nodeMap = useMemo(() => {
    const map = new Map<string, FlowNode>();
    script.nodes.forEach(node => map.set(node.id, node));
    return map;
  }, [script.nodes]);

  useEffect(() => {
    const onMove = (event: PointerEvent) => {
      const drag = dragRef.current;
      if (drag) {
        moveNode(
          drag.id,
          drag.nodeX + (event.clientX - drag.startX) / drag.scale,
          drag.nodeY + (event.clientY - drag.startY) / drag.scale,
        );
        return;
      }
      const pan = panRef.current;
      if (pan) {
        setCanvasTransform({
          x: pan.x + event.clientX - pan.startX,
          y: pan.y + event.clientY - pan.startY,
        });
      }
    };
    const onUp = () => {
      dragRef.current = null;
      panRef.current = null;
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
  }, [moveNode, setCanvasTransform]);

  const startDrag = useCallback((node: FlowNode, event: React.PointerEvent) => {
    event.stopPropagation();
    dragRef.current = {
      id: node.id,
      startX: event.clientX,
      startY: event.clientY,
      nodeX: node.x,
      nodeY: node.y,
      scale: transform.scale || 1,
    };
  }, [transform.scale]);

  const startPan = useCallback((event: React.PointerEvent) => {
    if ((event.target as HTMLElement).closest('.flow-card, .edge-path, button, input, textarea, select')) return;
    if (event.altKey || event.button === 1) {
      panRef.current = { startX: event.clientX, startY: event.clientY, x: transform.x, y: transform.y };
      event.preventDefault();
      return;
    }
    clearSelection();
  }, [clearSelection, transform.x, transform.y]);

  const onWheel = useCallback((event: React.WheelEvent) => {
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    const next = Math.max(0.35, Math.min(1.9, transform.scale - event.deltaY * 0.001));
    setCanvasTransform({ scale: Number(next.toFixed(2)) });
  }, [setCanvasTransform, transform.scale]);

  return (
    <section className={`zone-card flow-deck${focus === 'flow' ? ' is-focus' : ''}`} onMouseDown={() => chooseFocus('flow')}>
      <header className="zone-header">
        <div>
          <p className="eyebrow">Flow Matter</p>
          <h2>可编排流程物质</h2>
        </div>
        <div className="zone-tools">
          <button className="pill-button" onClick={(e) => { e.stopPropagation(); zoomOut(); }}>－</button>
          <button className="pill-button" onClick={(e) => { e.stopPropagation(); fitToScreen(); }}>适配</button>
          <button className="pill-button" onClick={(e) => { e.stopPropagation(); zoomIn(); }}>＋</button>
        </div>
      </header>

      <div className="flow-body">
        <NodePalette />
        <div className="canvas-viewport" onPointerDown={startPan} onWheel={onWheel}>
          <div
            className="flow-stage"
            style={{ transform: `translate(${transform.x}px, ${transform.y}px) scale(${transform.scale})` }}
          >
            <EdgeLayer
              edges={script.edges}
              nodeMap={nodeMap}
              selectedEdges={selection.edges}
              onSelectEdge={(id, append) => selectEdges([id], append)}
            />
            {script.nodes.map(node => (
              <FlowNodeCard
                key={node.id}
                node={node}
                selected={selection.nodes.includes(node.id)}
                active={currentNode === node.id}
                onPointerDown={(event) => startDrag(node, event)}
                onSelect={(append) => selectNodes([node.id], append)}
              />
            ))}
          </div>
        </div>
      </div>

      <div className="flow-hud">
        <span>节点 {script.nodes.length}</span>
        <span>连线 {script.edges.length}</span>
        <span>缩放 {Math.round(transform.scale * 100)}%</span>
        <span>{selectedId ? `选中 ${nodeTitle(nodeMap.get(selectedId)!)}` : '空白处拖拽平移：Alt + 鼠标'}</span>
      </div>
    </section>
  );
}

function NodePalette() {
  const [query, setQuery] = useState('');
  const [collapsed, setCollapsed] = useState(false);
  const addNode = useStore(s => s.addNode);
  const addEdge = useStore(s => s.addEdge);
  const transform = useStore(s => s.ui.canvas_transform);

  const defs = useMemo(() => Object.values(NODE_DEFINITIONS), []);
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return defs.filter(def => {
      if (!q) return true;
      return [
        def.label,
        def.type,
        def.description,
        def.category,
      ].join(' ').toLowerCase().includes(q);
    });
  }, [defs, query]);

  const grouped = useMemo(() => {
    const groups = new Map<NodeDefinition['category'], NodeDefinition[]>();
    filtered.forEach(def => {
      const arr = groups.get(def.category) || [];
      arr.push(def);
      groups.set(def.category, arr);
    });
    return Array.from(groups.entries());
  }, [filtered]);

  const addSmartNode = (type: NodeType) => {
    const store = useStore.getState();
    const sourceId = store.ui.selection.nodes[0];
    const source = sourceId ? store.script.nodes.find(node => node.id === sourceId) : undefined;
    const x = source ? source.x + 330 : (-transform.x + 520) / Math.max(transform.scale, 0.1);
    const y = source ? source.y + 20 : (-transform.y + 260) / Math.max(transform.scale, 0.1);
    const newId = addNode(type, x, y);
    if (source) {
      const out = NODE_DEFINITIONS[source.type]?.ports.outputs[0]?.id || 'output';
      addEdge({ from_node: source.id, from_port: out, to_node: newId, to_port: 'input', label: out });
    }
  };

  return (
    <aside className={`node-palette${collapsed ? ' collapsed' : ''}`}>
      <button className="palette-collapse" onClick={() => setCollapsed(v => !v)}>{collapsed ? '展开指令' : '折叠'}</button>
      {!collapsed && (
        <>
          <input
            className="liquid-input"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索 54 类节点能力..."
          />
          <div className="palette-scroll">
            {grouped.map(([category, list]) => (
              <div className="palette-group" key={category}>
                <div className="palette-heading">
                  <span>{CATEGORY_LABELS[category]}</span>
                  <b>{list.length}</b>
                </div>
                {list.map(def => (
                  <button key={def.type} className="palette-node" onClick={() => addSmartNode(def.type)}>
                    <i style={{ background: def.color }} />
                    <span>{def.label}</span>
                    <em>{def.type}</em>
                  </button>
                ))}
              </div>
            ))}
          </div>
        </>
      )}
    </aside>
  );
}

function EdgeLayer({
  edges,
  nodeMap,
  selectedEdges,
  onSelectEdge,
}: {
  edges: FlowEdge[];
  nodeMap: Map<string, FlowNode>;
  selectedEdges: string[];
  onSelectEdge: (id: string, append: boolean) => void;
}) {
  const paths = useMemo(() => edges.map(edge => {
    const from = nodeMap.get(edge.from_node);
    const to = nodeMap.get(edge.to_node);
    if (!from || !to) return null;
    const x1 = from.x + NODE_CARD_WIDTH;
    const y1 = from.y + NODE_CARD_HEIGHT / 2;
    const x2 = to.x;
    const y2 = to.y + NODE_CARD_HEIGHT / 2;
    const tension = Math.max(90, Math.abs(x2 - x1) * 0.48);
    return {
      edge,
      d: `M ${x1} ${y1} C ${x1 + tension} ${y1}, ${x2 - tension} ${y2}, ${x2} ${y2}`,
      labelX: (x1 + x2) / 2,
      labelY: (y1 + y2) / 2,
    };
  }).filter(Boolean) as Array<{ edge: FlowEdge; d: string; labelX: number; labelY: number }>, [edges, nodeMap]);

  return (
    <svg className="edge-layer" width="4200" height="3200" viewBox="-900 -600 4200 3200">
      <defs>
        <marker id="edgeArrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
          <path d="M0,0 L0,6 L9,3 z" />
        </marker>
      </defs>
      {paths.map(({ edge, d, labelX, labelY }) => (
        <g key={edge.id}>
          <path
            className={`edge-path${selectedEdges.includes(edge.id) ? ' selected' : ''}${edge.animated ? ' animated' : ''}`}
            d={d}
            onClick={(event) => {
              event.stopPropagation();
              onSelectEdge(edge.id, event.shiftKey);
            }}
          />
          {edge.label && (
            <text className="edge-label" x={labelX} y={labelY - 8}>{edge.label}</text>
          )}
        </g>
      ))}
    </svg>
  );
}

function FlowNodeCard({
  node,
  selected,
  active,
  onPointerDown,
  onSelect,
}: {
  node: FlowNode;
  selected: boolean;
  active: boolean;
  onPointerDown: (event: React.PointerEvent) => void;
  onSelect: (append: boolean) => void;
}) {
  const def = NODE_DEFINITIONS[node.type];
  const [flipped, setFlipped] = useState(false);
  const brief = Object.entries(node.params as Record<string, unknown>)
    .filter(([key]) => key !== 'description')
    .slice(0, 3)
    .map(([key, value]) => `${key}: ${Array.isArray(value) ? `[${value.length}]` : stringifyParam(value)}`)
    .join(' · ');

  return (
    <article
      className={`flow-card status-${node.status || 'idle'}${selected ? ' selected' : ''}${active ? ' active' : ''}${node.disabled ? ' disabled' : ''}`}
      style={{ left: node.x, top: node.y, ['--node-color' as string]: def?.color || '#6366f1' }}
      onPointerDown={onPointerDown}
      onClick={(event) => {
        event.stopPropagation();
        onSelect(event.shiftKey || event.metaKey || event.ctrlKey);
      }}
      onDoubleClick={(event) => {
        event.stopPropagation();
        setFlipped(v => !v);
      }}
    >
      <div className={`flow-card-inner${flipped ? ' flipped' : ''}`}>
        <div className="flow-card-face front">
          <div className="node-port input" />
          <div className="node-port output" />
          <div className="node-topline">
            <i />
            <span>{def?.category || 'node'}</span>
            <b>{statusLabel(node.status)}</b>
          </div>
          <h3>{nodeTitle(node)}</h3>
          <p>{nodeDescription(node)}</p>
          <div className="node-brief">{brief || '双击翻面查看参数'}</div>
        </div>
        <div className="flow-card-face back">
          <b>{node.type}</b>
          <code>{JSON.stringify(node.params, null, 2)}</code>
        </div>
      </div>
    </article>
  );
}

function InspectorOrb({ focused }: { focused: boolean }) {
  const script = useStore(s => s.script);
  const selection = useStore(s => s.ui.selection);
  const addEdge = useStore(s => s.addEdge);
  const deleteNodes = useStore(s => s.deleteNodes);
  const deleteEdges = useStore(s => s.deleteEdges);
  const updateNode = useStore(s => s.updateNode);
  const updateNodeParams = useStore(s => s.updateNodeParams);
  const selectNodes = useStore(s => s.selectNodes);
  const [flipped, setFlipped] = useState(false);

  const selectedNode = useMemo(() => {
    const id = selection.nodes[0];
    return id ? script.nodes.find(node => node.id === id) : undefined;
  }, [script.nodes, selection.nodes]);

  const selectedEdges = selection.edges;

  const connectSelection = () => {
    const [fromId, toId] = selection.nodes;
    if (!fromId || !toId || fromId === toId) return;
    const from = script.nodes.find(node => node.id === fromId);
    if (!from) return;
    const out = NODE_DEFINITIONS[from.type]?.ports.outputs[0]?.id || 'output';
    addEdge({ from_node: fromId, from_port: out, to_node: toId, to_port: 'input', label: out });
  };

  return (
    <aside className={`zone-card inspector-orb${focused ? ' is-flow-focus' : ''}`}>
      <header className="zone-header small">
        <div>
          <p className="eyebrow">Inspector Orb</p>
          <h2>浮出属性层</h2>
        </div>
        <button className="pill-button" onClick={() => setFlipped(v => !v)}>3D 翻面</button>
      </header>
      <div className={`flip-shell${flipped ? ' flipped' : ''}`}>
        <div className="flip-face front">
          {selectedNode ? (
            <PropertyEditor
              node={selectedNode}
              updateNode={updateNode}
              updateNodeParams={updateNodeParams}
              deleteNode={() => deleteNodes([selectedNode.id])}
            />
          ) : selectedEdges.length > 0 ? (
            <div className="empty-inspector">
              <h3>已选择 {selectedEdges.length} 条连线</h3>
              <p>连线是 FlowScript 控制流，删除后不会影响节点参数。</p>
              <button className="neo-button danger" onClick={() => deleteEdges(selectedEdges)}>删除连线</button>
            </div>
          ) : (
            <div className="empty-inspector">
              <h3>没有固定面板</h3>
              <p>点选节点，属性层会像抽屉一样浮出；双击节点可在画布内翻到 JSON 背面。</p>
              <button className="neo-button ghost" onClick={() => {
                const first = script.nodes[0];
                if (first) selectNodes([first.id]);
              }}>选中第一个节点</button>
            </div>
          )}
          {selection.nodes.length >= 2 && (
            <button className="neo-button primary wide" onClick={connectSelection}>连接前两个选中节点</button>
          )}
        </div>
        <div className="flip-face back">
          <RunLog />
        </div>
      </div>
    </aside>
  );
}

function PropertyEditor({
  node,
  updateNode,
  updateNodeParams,
  deleteNode,
}: {
  node: FlowNode;
  updateNode: (id: string, updates: Partial<FlowNode>) => void;
  updateNodeParams: (id: string, params: any) => void;
  deleteNode: () => void;
}) {
  const def = NODE_DEFINITIONS[node.type];
  const params = node.params as Record<string, unknown>;

  return (
    <div className="property-editor">
      <div className="node-identity">
        <i style={{ background: def?.color || '#6366f1' }} />
        <div>
          <span>{def?.label || node.type}</span>
          <b>{node.type}</b>
        </div>
      </div>

      <label className="form-field">
        <span>节点名称</span>
        <input
          className="form-input"
          value={node.label || ''}
          placeholder={def?.label || node.type}
          onChange={(event) => updateNode(node.id, { label: event.target.value })}
        />
      </label>

      <div className="flag-row">
        <button className={`toggle-chip${node.disabled ? ' active' : ''}`} onClick={() => updateNode(node.id, { disabled: !node.disabled })}>
          {node.disabled ? '已禁用' : '启用中'}
        </button>
        <button className={`toggle-chip${node.collapsed ? ' active' : ''}`} onClick={() => updateNode(node.id, { collapsed: !node.collapsed })}>
          {node.collapsed ? '已折叠' : '展开态'}
        </button>
      </div>

      <div className="param-list">
        {Object.entries(params).map(([key, value]) => (
          <label className="form-field" key={key}>
            <span>{key}</span>
            {typeof value === 'boolean' ? (
              <input
                type="checkbox"
                checked={value}
                onChange={(event) => updateNodeParams(node.id, { [key]: event.target.checked })}
              />
            ) : typeof value === 'number' ? (
              <input
                className="form-input"
                type="number"
                value={Number.isFinite(value) ? value : 0}
                onChange={(event) => updateNodeParams(node.id, { [key]: Number(event.target.value) })}
              />
            ) : typeof value === 'object' && value !== null ? (
              <textarea
                className="form-input code-input"
                defaultValue={stringifyParam(value)}
                onBlur={(event) => updateNodeParams(node.id, { [key]: parseMaybeJson(event.target.value, value) })}
              />
            ) : (
              <input
                className="form-input"
                value={stringifyParam(value)}
                onChange={(event) => updateNodeParams(node.id, { [key]: event.target.value })}
              />
            )}
          </label>
        ))}
      </div>

      {node.type !== 'start' && node.type !== 'end' && (
        <button className="neo-button danger wide" onClick={deleteNode}>删除节点</button>
      )}
    </div>
  );
}

function AgentCapsule({ focused, onFocus }: { focused: boolean; onFocus: () => void }) {
  return (
    <section className={`zone-card agent-card${focused ? ' is-focus' : ''}`} onMouseDown={onFocus}>
      <header className="zone-header small">
        <div>
          <p className="eyebrow">Live Agent Core</p>
          <h2>WinPilot 原生核</h2>
        </div>
        <span className="breathing-dot" />
      </header>
      <div className="agent-frame">
        <AgentView />
      </div>
    </section>
  );
}

interface TraceItem { run_id: string; task: string; status: string; steps: number }
interface ScriptItem { file: string; name: string; task: string; steps: number }

async function jsonOrThrow<T = any>(response: Response, label: string): Promise<T> {
  const text = await response.text();
  let data: any = {};
  if (text) {
    try { data = JSON.parse(text); } catch { data = { detail: text }; }
  }
  if (!response.ok) {
    throw new Error(data?.detail || data?.error || `${label} failed (${response.status})`);
  }
  return data as T;
}

function ReplayDock({ focused, setFocus }: { focused: boolean; setFocus: (zone: FocusZone) => void }) {
  const loadScript = useStore(s => s.loadScript);
  const setViewMode = useStore(s => s.setViewMode);
  const [base, setBase] = useState<string | null>(null);
  const [traces, setTraces] = useState<TraceItem[]>([]);
  const [scripts, setScripts] = useState<ScriptItem[]>([]);
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const apiBase = useCallback(async () => {
    if (base) return base;
    const ensured = await window.electronAPI?.ensureWinpilot?.();
    const url = ensured?.url || await window.electronAPI?.getWinpilotUrl?.();
    if (url) setBase(url);
    return url || null;
  }, [base]);

  const refreshLists = useCallback(async () => {
    const b = await apiBase();
    if (!b) return;
    try {
      const [traceRows, scriptRows] = await Promise.all([
        fetch(`${b}api/traces`).then(r => jsonOrThrow<TraceItem[]>(r, 'load traces')),
        fetch(`${b}api/scripts`).then(r => jsonOrThrow<ScriptItem[]>(r, 'load scripts')),
      ]);
      setTraces(Array.isArray(traceRows) ? traceRows : []);
      setScripts(Array.isArray(scriptRows) ? scriptRows : []);
      try {
        const st = await fetch(`${b}api/human/status`).then(r => jsonOrThrow(r, 'human recorder status'));
        setRecording(!!st?.recording);
      } catch {
        // human recorder may be unavailable while WinPilot is starting
      }
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [apiBase]);

  useEffect(() => {
    refreshLists();
  }, [refreshLists]);

  const importScript = useCallback(async (file: string) => {
    const b = await apiBase();
    if (!b) return;
    setBusy(true);
    try {
      const wps = await fetch(`${b}api/script/${encodeURIComponent(file)}`).then((r) => jsonOrThrow<WpsScript>(r, 'read script'));
      loadScript(wpsToFlowScript(wps));
      setFocus('flow');
      setViewMode('canvas');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [apiBase, loadScript, setFocus, setViewMode]);

  const importTrace = useCallback(async (runId: string) => {
    const b = await apiBase();
    if (!b) return;
    setBusy(true);
    try {
      const res = await fetch(`${b}api/export/${encodeURIComponent(runId)}`, { method: 'POST' }).then(r => jsonOrThrow(r, 'export trace'));
      if (res?.script) {
        await refreshLists();
        await importScript(res.script);
      } else {
        setError('该 trace 没有可重放步骤，无法导出');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [apiBase, importScript, refreshLists]);

  const replayOriginal = useCallback(async (file: string) => {
    const b = await apiBase();
    if (!b) return;
    setBusy(true);
    try {
      await fetch(`${b}api/replay`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ script: file }),
      }).then(r => jsonOrThrow(r, 'replay script'));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [apiBase]);

  const toggleRecord = useCallback(async () => {
    const b = await apiBase();
    if (!b) return;
    setBusy(true);
    try {
      if (recording) {
        const res = await fetch(`${b}api/human/stop`, { method: 'POST' }).then(r => jsonOrThrow(r, 'stop recording'));
        setRecording(false);
        await refreshLists();
        if (res?.file) await importScript(res.file);
      } else {
        await fetch(`${b}api/human/start`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        }).then(r => jsonOrThrow(r, 'start recording'));
        setRecording(true);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [apiBase, importScript, recording, refreshLists]);

  return (
    <section className={`zone-card replay-card${focused ? ' is-focus' : ''}`}>
      <header className="zone-header small">
        <div>
          <p className="eyebrow">Replay Forge</p>
          <h2>录制转流程</h2>
        </div>
        <button className="pill-button" onClick={refreshLists} disabled={busy}>刷新</button>
      </header>
      <button className={`record-button${recording ? ' recording' : ''}`} onClick={toggleRecord} disabled={busy}>
        {recording ? '停止录制并铸成流程图' : '录制真实操作'}
      </button>
      <p className="muted-line">trace → .wps.json → FlowScript，仍保留 WinPilot 原样 0-token 重放入口。</p>
      <div className="replay-scroll">
        <h3>Agent traces</h3>
        {traces.length === 0 && <p className="muted-line">暂无 trace</p>}
        {traces.slice(0, focused ? 20 : 3).map(trace => (
          <div className="replay-row" key={trace.run_id}>
            <span title={trace.task}>{trace.task || trace.run_id}<em>{trace.status} · {trace.steps}步</em></span>
            <button onClick={() => importTrace(trace.run_id)} disabled={busy}>导入</button>
          </div>
        ))}
        <h3>已保存脚本</h3>
        {scripts.length === 0 && <p className="muted-line">暂无 .wps.json</p>}
        {scripts.slice(0, focused ? 20 : 3).map(item => (
          <div className="replay-row" key={item.file}>
            <span title={item.task || item.file}>{item.name || item.file}<em>{item.steps}步</em></span>
            <button onClick={() => importScript(item.file)} disabled={busy}>编辑</button>
            <button onClick={() => replayOriginal(item.file)} disabled={busy}>原放</button>
          </div>
        ))}
      </div>
      {error && <p className="error-line">{error}</p>}
    </section>
  );
}

function CodeLens({ focused }: { focused: boolean }) {
  const script = useStore(s => s.script);
  const [mode, setMode] = useState<TextMode>('pseudocode');
  const text = useMemo(() => {
    try {
      return generateText(script, mode);
    } catch (err) {
      return `# 生成失败\n${err instanceof Error ? err.message : String(err)}`;
    }
  }, [mode, script]);

  const copyText = async () => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // clipboard may be blocked in browser previews
    }
  };

  return (
    <section className={`zone-card code-card${focused ? ' is-focus' : ''}`}>
      <header className="zone-header small">
        <div>
          <p className="eyebrow">Code Lens</p>
          <h2>{mode === 'pseudocode' ? '伪代码镜面' : 'DSL 镜面'}</h2>
        </div>
        <div className="zone-tools">
          <button className={`pill-button${mode === 'pseudocode' ? ' active' : ''}`} onClick={() => setMode('pseudocode')}>伪码</button>
          <button className={`pill-button${mode === 'dsl' ? ' active' : ''}`} onClick={() => setMode('dsl')}>DSL</button>
          <button className="pill-button" onClick={copyText}>复制</button>
        </div>
      </header>
      <pre className="code-lens"><code>{text}</code></pre>
    </section>
  );
}

function SettingsDock({ focused }: { focused: boolean }) {
  const script = useStore(s => s.script);
  const themeMode = useStore(s => s.ui.themeMode);
  const showGrid = useStore(s => s.ui.show_grid);
  const snapToGrid = useStore(s => s.ui.snap_to_grid);
  const gridSize = useStore(s => s.ui.grid_size);
  const autoLearn = useStore(s => s.ui.auto_learn);
  const featureList = useStore(s => s.featureList);
  const setUI = useStore(s => s.setUI);
  const setScriptName = useStore(s => s.setScriptName);
  const loadTemplate = useStore(s => s.loadTemplate);

  const setTheme = (mode: ThemeMode) => setUI({ themeMode: mode });

  const patchSetting = (key: keyof FlowScript['settings'], value: any) => {
    const current = useStore.getState().script;
    useStore.setState({
      script: {
        ...current,
        settings: {
          ...current.settings,
          [key]: value,
        },
      },
      isDirty: true,
    } as any);
  };

  return (
    <section className={`zone-card settings-card${focused ? ' is-focus' : ''}`}>
      <header className="zone-header small">
        <div>
          <p className="eyebrow">Studio Settings</p>
          <h2>参数折叠舱</h2>
        </div>
      </header>
      <div className="settings-scroll">
        <label className="form-field">
          <span>脚本名称</span>
          <input className="form-input" value={script.meta.name} onChange={(e) => setScriptName(e.target.value)} />
        </label>

        <div className="settings-block">
          <h3>主题</h3>
          <div className="segmented">
            {(['light', 'dark', 'system'] as ThemeMode[]).map(mode => (
              <button key={mode} className={themeMode === mode ? 'active' : ''} onClick={() => setTheme(mode)}>
                {mode === 'light' ? '浅色' : mode === 'dark' ? '深色' : '系统'}
              </button>
            ))}
          </div>
        </div>

        <div className="settings-block">
          <h3>画布物理</h3>
          <div className="flag-row">
            <button className={`toggle-chip${showGrid ? ' active' : ''}`} onClick={() => setUI({ show_grid: !showGrid })}>网格</button>
            <button className={`toggle-chip${snapToGrid ? ' active' : ''}`} onClick={() => setUI({ snap_to_grid: !snapToGrid })}>吸附</button>
            <input
              className="form-input mini"
              type="number"
              value={gridSize}
              min={4}
              max={80}
              onChange={(e) => setUI({ grid_size: Number(e.target.value) || 20 })}
            />
          </div>
        </div>

        <div className="settings-block">
          <h3>运行策略</h3>
          <label className="form-field">
            <span>错误处理</span>
            <select
              className="form-input"
              value={script.settings.error_handling}
              onChange={(e) => patchSetting('error_handling', e.target.value)}
            >
              <option value="stop">停止</option>
              <option value="continue">继续</option>
              <option value="retry">重试</option>
            </select>
          </label>
          <label className="form-field">
            <span>默认延迟</span>
            <input
              className="form-input"
              type="number"
              value={script.settings.default_delay}
              step="0.1"
              onChange={(e) => patchSetting('default_delay', Number(e.target.value))}
            />
          </label>
          <label className="form-field">
            <span>最大重试</span>
            <input
              className="form-input"
              type="number"
              value={script.settings.max_retries}
              onChange={(e) => patchSetting('max_retries', Number(e.target.value))}
            />
          </label>
        </div>

        <div className="settings-block">
          <h3>模板跃迁</h3>
          <div className="template-grid">
            {PRESET_TEMPLATE_META.slice(0, focused ? PRESET_TEMPLATE_META.length : 4).map(template => (
              <button key={template.id} onClick={() => loadTemplate(template.id)}>
                <b>{template.name}</b>
                <span>{template.category}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="settings-block">
          <h3>能力状态</h3>
          <div className="flag-row">
            <button className={`toggle-chip${autoLearn ? ' active' : ''}`} onClick={() => setUI({ auto_learn: !autoLearn })}>自动学习</button>
            <span className="mini-stat">{featureList.length} features</span>
          </div>
        </div>
      </div>
    </section>
  );
}

function MetricsShelf() {
  const script = useStore(s => s.script);
  const execution = useStore(s => s.execution);
  const featureList = useStore(s => s.featureList);
  const agentHistory = useStore(s => s.agentHistory);
  const latestRun = agentHistory[0];

  return (
    <aside className="metrics-shelf">
      <div><span>状态</span><b>{statusLabel(execution.status)}</b></div>
      <div><span>变量</span><b>{Object.keys(script.variables || {}).length}</b></div>
      <div><span>Feature</span><b>{featureList.length}</b></div>
      <div><span>Agent</span><b>{latestRun ? statusLabel(latestRun.status) : '未运行'}</b></div>
    </aside>
  );
}

function RunLog() {
  const log = useStore(s => s.execution.log);
  const toolCallLog = useStore(s => s.toolCallLog);

  return (
    <div className="run-log">
      <h3>运行与工具调用</h3>
      <div className="log-feed">
        {log.length === 0 && toolCallLog.length === 0 && <p>暂无运行日志。运行脚本后这里会像暗格一样翻出来。</p>}
        {log.slice(-12).reverse().map((entry, index) => (
          <div className={`log-row level-${entry.level}`} key={`${entry.timestamp}-${index}`}>
            <span>{entry.level}</span>
            <p>{entry.message}</p>
          </div>
        ))}
        {toolCallLog.slice(-6).reverse().map(entry => (
          <div className={`log-row level-${entry.success ? 'info' : 'error'}`} key={entry.id}>
            <span>{entry.success ? 'tool' : 'fail'}</span>
            <p>{entry.display_name || entry.feature_name} · {entry.elapsed_ms}ms</p>
          </div>
        ))}
      </div>
    </div>
  );
}
