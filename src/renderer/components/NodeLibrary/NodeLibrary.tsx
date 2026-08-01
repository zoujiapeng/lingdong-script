import React, { useState, useMemo, useCallback, useEffect, useRef } from 'react';
import { Search, X, Check } from 'lucide-react';
import { NodeDefinition, NodeType } from '../../types';
import { NODE_DEFINITIONS, NODE_CATEGORIES } from '../../utils/nodeDefinitions';
import { confirmDocumentReplacement, useStore } from '../../store';
import { unifiedSearch, UnifiedSearchResult } from '../../utils/unifiedSearch';
import { itemMarker } from '../../utils/itemMarker';
import { getNodeIcon } from '../../utils/nodeIconMap';
import { displaySearchReason, displaySearchToken } from '../../utils/displayLabels';
import { isRunnableFlowNodeType } from '../../platform/flowToArpGraph';
import type { CapabilitySpec } from '../../platform/contracts';

const RECENT_NODE_TYPES: NodeType[] = ['mouse_operation', 'wait', 'condition'];

function NodeIcon({ name, size = 14 }: { name: string; size?: number }) {
  const Icon = getNodeIcon(name);
  return <Icon size={size} />;
}

export const NodeLibrary = React.memo(function NodeLibrary({ capabilities = [] }: { capabilities?: CapabilitySpec[] }) {
  const [search, setSearch] = useState('');
  const [activeCategory, setActiveCategory] = useState<string>('all');
  const [testedRefresh, setTestedRefresh] = useState(0);
  const addNode = useStore(s => s.addNode);
  const showNodeLibrary = useStore(s => s.ui.show_node_library);
  const panelsVisible = useStore(s => s.ui.panels_visible);
  const canvas_transform = useStore(s => s.ui.canvas_transform);
  const showUnifiedSearch = useStore(s => s.ui.show_unified_search);
  const unifiedSearchQuery = useStore(s => s.ui.unified_search_query);
  const setUI = useStore(s => s.setUI);
  const loadTemplate = useStore(s => s.loadTemplate);
  const forcedVisible = showNodeLibrary || panelsVisible;

  // ── Unified search mode state ──
  const [usQuery, setUsQuery] = useState('');
  const [usResults, setUsResults] = useState<UnifiedSearchResult[]>([]);
  const [usFilter, setUsFilter] = useState<'all' | 'node' | 'template'>('all');
  const [usTestedFilter, setUsTestedFilter] = useState(false);
  const [usExpanded, setUsExpanded] = useState<string | null>(null);
  const usTimer = useRef<ReturnType<typeof setTimeout>>();
  const usInputRef = useRef<HTMLInputElement>(null);
  const backendFlowTypes = useMemo(
    () => new Set(capabilities.flatMap((capability) => capability.flow_node_types || [])),
    [capabilities],
  );
  const runtimeCatalogAvailable = capabilities.length > 0;
  const modelBackedFlowTypes = useMemo(
    () => new Set(capabilities
      .filter((capability) => capability.requires_llm)
      .flatMap((capability) => capability.flow_node_types || [])),
    [capabilities],
  );
  const runtimeState = useCallback((type: string) => {
    if (!runtimeCatalogAvailable) return 'unchecked' as const;
    return backendFlowTypes.has(type) ? 'available' as const : 'unavailable' as const;
  }, [backendFlowTypes, runtimeCatalogAvailable]);
  const runtimeLabel = useCallback((type: string) => {
    const state = runtimeState(type);
    if (state === 'available') return modelBackedFlowTypes.has(type) ? '当前可执行 · 需要模型' : '当前可执行';
    if (state === 'unavailable') return '当前运行时未开放';
    return '运行时尚未核验';
  }, [modelBackedFlowTypes, runtimeState]);

  // Rebuild index + focus input when entering search mode
  useEffect(() => {
    if (showUnifiedSearch) {
      unifiedSearch.rebuildIndex();
      if (usQuery.trim()) {
        // Re-run search if the query was supplied before the panel opened.
        doUsSearch(usQuery);
      }
    }
  }, [showUnifiedSearch]);

  // Watch unified_search_query from store (set by TopBar search input)
  useEffect(() => {
    if (showUnifiedSearch && unifiedSearchQuery.trim()) {
      setUsQuery(unifiedSearchQuery);
      doUsSearch(unifiedSearchQuery);
    } else if (!unifiedSearchQuery.trim()) {
      setUsQuery('');
      setUsResults([]);
    }
  }, [unifiedSearchQuery, showUnifiedSearch]);

  const doUsSearch = useCallback(async (q: string) => {
    if (!q.trim()) { setUsResults([]); return; }
    unifiedSearch.refreshTestedStatus();
    let results = unifiedSearch.search(q, { maxResults: 30 });
    results = results.filter(({ item }) => (
      item.type === 'template'
        ? Boolean(item.hasScript)
        : item.type === 'node' && isRunnableFlowNodeType(item.nodeType || item.id)
    ));
    // Apply type filter
    if (usFilter !== 'all') results = results.filter(r => r.item.type === usFilter);
    if (usTestedFilter) results = results.filter(r => r.item.tested);
    setUsResults(results);
  }, [usFilter, usTestedFilter]);

  // Re-filter when filter changes
  useEffect(() => {
    if (showUnifiedSearch && usQuery.trim()) doUsSearch(usQuery);
  }, [usFilter, usTestedFilter, showUnifiedSearch]);

  const handleUsQuery = (v: string) => {
    setUsQuery(v);
    clearTimeout(usTimer.current);
    usTimer.current = setTimeout(() => doUsSearch(v), 250);
  };

  const handleUsTested = (id: string, type: 'template' | 'node') => {
    itemMarker.toggleTested(id, type);
    unifiedSearch.refreshTestedStatus();
    if (usQuery.trim()) doUsSearch(usQuery);
  };

  const handleUsClose = () => {
    setUI({ show_unified_search: false });
  };

  const handleUsUseTemplate = (id: string) => {
    if (!confirmDocumentReplacement()) return;
    loadTemplate(id);
    handleUsClose();
  };

  const handleUsAddNode = (nodeType: string) => {
    if (!isRunnableFlowNodeType(nodeType)) return;
    const canvasEl = document.querySelector('.flow-canvas-wrapper') as HTMLElement;
    if (!canvasEl) return;
    const rect = canvasEl.getBoundingClientRect();
    const x = (rect.width / 2 - canvas_transform.x) / canvas_transform.scale - 110;
    const y = (rect.height / 2 - canvas_transform.y) / canvas_transform.scale - 40;
    addNode(nodeType as NodeType, Math.max(0, x), Math.max(0, y));
    handleUsClose();
  };

  // ── Normal library state ──

  // Editing follows the local compiler boundary. Runtime catalog exposure is
  // shown separately and is enforced again when the graph is validated/run.
  const allItems = useMemo(() => {
    return Object.values(NODE_DEFINITIONS).filter((def) => isRunnableFlowNodeType(def.type));
  }, []);

  const runtimeAvailableCount = useMemo(
    () => allItems.filter((item) => runtimeState(item.type) === 'available').length,
    [allItems, runtimeState],
  );

  const availableCategories = useMemo(() => {
    const ids = new Set(allItems.map((def) => def.category));
    return NODE_CATEGORIES.filter((category) => ids.has(category.id));
  }, [allItems]);

  const recentItems = useMemo(() => RECENT_NODE_TYPES
    .map((type) => allItems.find((item) => item.type === type))
    .filter((item): item is NodeDefinition => Boolean(item)), [allItems]);

  const filtered = useMemo(() => {
    return allItems.filter((item) => {
      const matchSearch =
        !search ||
        item.label.includes(search) ||
        item.description.includes(search) ||
        item.type.includes(search.toLowerCase());
      const matchCat = activeCategory === 'all' || item.category === activeCategory;
      return matchSearch && matchCat;
    });
  }, [allItems, search, activeCategory, testedRefresh]);

  const handleDragStart = (e: React.DragEvent, item: NodeDefinition) => {
    e.dataTransfer.setData('node-type', item.type);
    e.dataTransfer.effectAllowed = 'copy';
  };

  const handleDoubleClick = (item: NodeDefinition) => {
    const canvasEl = document.querySelector('.flow-canvas-wrapper') as HTMLElement;
    if (!canvasEl) return;
    const rect = canvasEl.getBoundingClientRect();
    const x = (rect.width / 2 - canvas_transform.x) / canvas_transform.scale - 110;
    const y = (rect.height / 2 - canvas_transform.y) / canvas_transform.scale - 40;

    addNode(item.type, Math.max(0, x), Math.max(0, y));
  };

  return (
    <div className={`node-library${forcedVisible ? ' forced-visible' : ''}`}>
      {showUnifiedSearch ? (
        /* ── Unified Search Mode ── */
        <>
          {/* Header */}
          <div className="node-library-header">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
              <div>
                <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', letterSpacing: '0.1em', textTransform: 'uppercase' }}>
                  指令库搜索
                </div>
              </div>
              <button className="btn btn-ghost btn-icon" onClick={handleUsClose}
                style={{ width: 24, height: 24 }} title="关闭搜索">
                <X size={14} />
              </button>
            </div>
            <div className="search-box">
              <Search size={14} color="var(--text-muted)" />
              <input
                ref={usInputRef}
                placeholder="搜索指令库 — 输入关键词查找模板或功能..."
                value={usQuery}
                onChange={(e) => handleUsQuery(e.target.value)}
              />
              {usQuery && (
                <button onClick={(e) => { e.stopPropagation(); setUsQuery(''); setUsResults([]); setUI({ show_unified_search: false, unified_search_query: '' }); }}
                  style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', padding: 2, display: 'flex', flexShrink: 0 }}>
                  <X size={14} />
                </button>
              )}
            </div>
          </div>

          {/* Filter tabs */}
          <div className="node-library-tabs">
            <button className={`tab-btn ${usFilter === 'all' && !usTestedFilter ? 'active' : ''}`}
              onClick={() => { setUsFilter('all'); setUsTestedFilter(false); }}>
              全部
            </button>
            <button className={`tab-btn ${usFilter === 'template' ? 'active' : ''}`}
              onClick={() => { setUsFilter('template'); setUsTestedFilter(false); }}>
              模板
            </button>
            <button className={`tab-btn ${usFilter === 'node' ? 'active' : ''}`}
              onClick={() => { setUsFilter('node'); setUsTestedFilter(false); }}>
              节点
            </button>
            <button className={`tab-btn ${usTestedFilter ? 'active' : ''}`}
              onClick={() => { setUsTestedFilter(!usTestedFilter); setUsFilter('all'); }}>
              已标记
            </button>
          </div>

          {/* Results */}
          <div className="node-library-body">
            {!usQuery.trim() && (
              <div style={{ textAlign: 'center', padding: '32px 16px', color: 'var(--text-muted)', fontSize: 13 }}>
                输入关键词搜索模板和可执行节点
                <div style={{ marginTop: 12, display: 'flex', flexWrap: 'wrap', gap: 6, justifyContent: 'center' }}>
                  {['截图', '点击', '等待', '输入', '条件', '变量'].map(eq => (
                    <button key={eq} className="btn btn-ghost" style={{ fontSize: 11, padding: '2px 8px' }}
                      onClick={() => handleUsQuery(eq)}>
                      {eq}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {usQuery.trim() && usResults.length === 0 && (
              <div style={{ textAlign: 'center', padding: '32px 16px', color: 'var(--text-muted)', fontSize: 13 }}>
                未找到匹配的指令或模板
              </div>
            )}
            {usResults.map((r) => {
              const item = r.item;
              const isTemplate = item.type === 'template';
              const isNode = item.type === 'node';
              const isExpanded = usExpanded === item.id;
              const tested = item.tested;

              return (
                <div key={`${item.type}:${item.id}`} className="node-item"
                  style={{ flexDirection: 'column', alignItems: 'stretch', cursor: 'pointer' }}
                  draggable
                  onDragStart={(e) => {
                    if (isTemplate) {
                      e.dataTransfer.setData('node-type', '__template');
                      e.dataTransfer.setData('template-name', item.name);
                      e.dataTransfer.setData('template-id', item.id);
                    } else if (isNode) {
                      e.dataTransfer.setData('node-type', (item as any).nodeType || item.id);
                    }
                    e.dataTransfer.effectAllowed = 'copy';
                  }}
                  onClick={() => setUsExpanded(isExpanded ? null : item.id)}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%' }}>
                    {/* Type badge */}
                    <div style={{
                      fontSize: 9, fontWeight: 700, padding: '1px 5px', borderRadius: 4, flexShrink: 0,
                      background: isTemplate ? '#8b5cf6' : '#22c55e',
                      color: '#fff', letterSpacing: '0.05em',
                    }}>
                      {isTemplate ? '模板' : '节点'}
                    </div>
                    {/* Name */}
                    <div style={{ flex: 1, fontSize: 12, fontWeight: 600, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {item.name}
                    </div>
                    {/* Score bar */}
                    <div style={{ width: 40, height: 4, background: 'var(--bg-secondary)', borderRadius: 2, flexShrink: 0, overflow: 'hidden' }}>
                      <div style={{ width: `${r.score}%`, height: '100%', background: r.score >= 80 ? '#22c55e' : r.score >= 50 ? '#eab308' : '#ef4444', borderRadius: 2 }} />
                    </div>
                    {/* Tested badge */}
                    <div title={tested ? '已测试 — 点击取消标记' : '未测试 — 点击标记为已测试'}
                      onClick={(e) => { e.stopPropagation(); handleUsTested(item.id, item.type); }}
                      style={{ width: 18, height: 18, borderRadius: 4, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', flexShrink: 0,
                        background: tested ? 'var(--accent)' : 'var(--bg-secondary)', color: tested ? '#fff' : 'var(--text-muted)' }}>
                      <Check size={12} />
                    </div>
                  </div>
                  {/* Description */}
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', marginTop: 2 }}>
                    {item.description || '暂无描述'}
                  </div>
                  {isNode && (
                    <div style={{ marginTop: 3, fontSize: 10, color: runtimeState((item as any).nodeType || item.id) === 'available' ? '#16a34a' : 'var(--text-muted)' }}>
                      {runtimeLabel((item as any).nodeType || item.id)}
                    </div>
                  )}
                  {/* Expanded details */}
                  {isExpanded && (
                    <div style={{ marginTop: 6, padding: 8, background: 'var(--bg-secondary)', borderRadius: 6, fontSize: 11 }}>
                      {item.keywords && item.keywords.length > 0 && (
                        <div style={{ marginBottom: 4 }}>关键词: {item.keywords.map(displaySearchToken).join('、')}</div>
                      )}
                      {item.tags && item.tags.length > 0 && (
                        <div style={{ marginBottom: 4 }}>标签: {item.tags.map(displaySearchToken).join('、')}</div>
                      )}
                      <div style={{ color: 'var(--text-muted)', fontSize: 10 }}>相关性: {r.score}/100</div>
                      {r.reasons && r.reasons.length > 0 && (
                        <div style={{ marginTop: 4 }}>匹配理由: {r.reasons.map(displaySearchReason).join('；')}</div>
                      )}
                      <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
                        {isTemplate ? (
                          <button className="btn btn-primary" style={{ fontSize: 11, padding: '3px 10px' }}
                            onClick={(e) => { e.stopPropagation(); handleUsUseTemplate(item.id); }}>
                            使用模板
                          </button>
                        ) : (
                          <button className="btn btn-primary" style={{ fontSize: 11, padding: '3px 10px' }}
                            onClick={(e) => { e.stopPropagation(); handleUsAddNode((item as any).nodeType || item.id); }}>
                            添加到画布
                          </button>
                        )}
                        <button className="btn btn-ghost" style={{ fontSize: 11, padding: '3px 10px' }}
                          onClick={(e) => { e.stopPropagation(); handleUsTested(item.id, item.type); }}>
                          {tested ? '取消标记' : '标记已测试'}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </>
      ) : (
        /* ── Normal Library Mode ── */
        <>
          {/* Header */}
          <div className="node-library-header">
            <div style={{ marginBottom: 10 }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', letterSpacing: '0.1em', marginBottom: 8, textTransform: 'uppercase' }}>
                指令库
              </div>
              <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 2 }}>
                {runtimeCatalogAvailable
                  ? `${allItems.length} 个可编辑节点 · ${runtimeAvailableCount} 个当前可执行`
                  : `${allItems.length} 个可编辑节点 · 运行时尚未核验`}
              </div>
            </div>
            <div className="search-box">
              <Search size={14} color="var(--text-muted)" />
              <input
                placeholder="搜索指令…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
          </div>

          {/* Category Tabs */}
          <div className="node-library-tabs">
            <button
              className={`tab-btn ${activeCategory === 'all' ? 'active' : ''}`}
              onClick={() => setActiveCategory('all')}
            >
              全部
            </button>
            {availableCategories.map((cat) => (
              <button
                key={cat.id}
                className={`tab-btn ${activeCategory === cat.id ? 'active' : ''}`}
                onClick={() => setActiveCategory(cat.id)}
              >
                {cat.label}
              </button>
            ))}
          </div>

          {/* Item List */}
          <div className="node-library-body">
            {filtered.length === 0 && (
              <div style={{ textAlign: 'center', padding: '32px 16px', color: 'var(--text-muted)', fontSize: 13 }}>
                未找到匹配的指令
              </div>
            )}
            {filtered.map((item) => {
              const tested = itemMarker.isTested(item.type, 'node');

              return (
                <div
                  key={`node-${item.type}`}
                  className="node-item"
                  draggable
                  onDragStart={(e) => handleDragStart(e, item)}
                  onDoubleClick={() => handleDoubleClick(item)}
                  title={`拖拽到画布 或 双击添加\n${item.description}\n${runtimeLabel(item.type)}`}
                  style={{ opacity: runtimeState(item.type) === 'unavailable' ? 0.62 : tested ? 1 : 0.85 }}
                >
                  <div
                    className="node-item-icon"
                    style={{ background: item.color }}
                  >
                    <NodeIcon name={item.icon} size={15} />
                  </div>
                  <div className="node-item-info">
                    <div className="node-item-name">{item.label}</div>
                    <div className="node-item-desc">{item.description.slice(0, 60)}</div>
                    <div style={{ marginTop: 2, fontSize: 10, color: runtimeState(item.type) === 'available' ? '#16a34a' : 'var(--text-muted)' }}>
                      {runtimeLabel(item.type)}
                    </div>
                  </div>
                  <div title={tested ? '已测试 — 点击取消标记' : '未测试 — 点击标记为已测试'}
                    onClick={(e) => { e.stopPropagation(); itemMarker.toggleTested(item.type, 'node'); setTestedRefresh(k => k + 1); }}
                    style={{ width: 18, height: 18, borderRadius: 4, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', flexShrink: 0, marginLeft: 4,
                      background: tested ? 'var(--accent)' : 'var(--bg-secondary)', color: tested ? '#fff' : 'var(--text-muted)' }}>
                    <Check size={12} />
                  </div>
                </div>
              );
            })}
          </div>

          {/* Footer - recent / feature count */}
          <div style={{
            padding: '10px 12px',
            borderTop: '1px solid var(--border-primary)',
            flexShrink: 0,
          }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', letterSpacing: '0.1em', marginBottom: 8, textTransform: 'uppercase' }}>
              最近使用
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              {recentItems.map((def) => {
                return (
                  <div
                    key={def.type}
                    className="node-item"
                    style={{ padding: '5px 8px' }}
                    draggable
                    onDragStart={(e) => {
                      e.dataTransfer.setData('node-type', def.type);
                      e.dataTransfer.effectAllowed = 'copy';
                    }}
                    onDoubleClick={() => handleDoubleClick(def)}
                    title={runtimeLabel(def.type)}
                  >
                    <div className="node-item-icon" style={{ background: def.color, width: 24, height: 24 }}>
                      <NodeIcon name={def.icon} size={12} />
                    </div>
                    <div className="node-item-info">
                      <div className="node-item-name" style={{ fontSize: 12 }}>{def.label}</div>
                      <div className="node-item-desc">{runtimeLabel(def.type)}</div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </>
      )}
    </div>
  );
});
