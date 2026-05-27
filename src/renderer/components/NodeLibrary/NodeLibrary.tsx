import React, { useState, useMemo, useCallback, useEffect, useRef } from 'react';
import { Search, Zap, Image, GitBranch, Monitor, Database, Settings, Wrench, X, Check, Loader2, Sparkles, FileType } from 'lucide-react';
import { NodeType } from '../../types';
import { NODE_DEFINITIONS, NODE_CATEGORIES } from '../../utils/nodeDefinitions';
import { useStore } from '../../store';
import { getLibraryFeatures, FeatureNodeItem, getFeatureCategoryLabel } from '../../utils/featureRegistry';
import { unifiedSearch, UnifiedSearchItem, UnifiedSearchResult } from '../../utils/unifiedSearch';
import { llmEnhanceSearch, hasLLMApiKey } from '../../utils/llmSearcher';
import { itemMarker } from '../../utils/itemMarker';
import * as LucideIcons from 'lucide-react';

const CategoryIcons: Record<string, React.FC<any>> = {
  basic: Zap,
  image: Image,
  control: GitBranch,
  device: Monitor,
  data: Database,
  system: Settings,
  tools: Wrench,
};

function NodeIcon({ name, size = 14 }: { name: string; size?: number }) {
  const Icon = (LucideIcons as any)[name];
  if (!Icon) return <Zap size={size} />;
  return <Icon size={size} />;
}

/** Merge node categories with feature-only categories */
function getAllCategories() {
  const cats = [...NODE_CATEGORIES];
  // Add tools category if not present
  if (!cats.find((c) => c.id === 'tools')) {
    cats.push({ id: 'tools', label: '工具', icon: 'Wrench' });
  }
  return cats;
}

const ALL_CATEGORIES = getAllCategories();

export const NodeLibrary = React.memo(function NodeLibrary() {
  const [search, setSearch] = useState('');
  const [activeCategory, setActiveCategory] = useState<string>('all');
  const [testedRefresh, setTestedRefresh] = useState(0);
  const addNode = useStore(s => s.addNode);
  const updateNodeParams = useStore(s => s.updateNodeParams);
  const featureList = useStore(s => s.featureList);
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
  const [usFilter, setUsFilter] = useState<'all' | 'feature' | 'template'>('all');
  const [usTestedFilter, setUsTestedFilter] = useState(false);
  const [usExpanded, setUsExpanded] = useState<string | null>(null);
  const [usLoading, setUsLoading] = useState(false);
  const usTimer = useRef<ReturnType<typeof setTimeout>>();
  const usInputRef = useRef<HTMLInputElement>(null);

  // Rebuild index + focus input when entering search mode
  useEffect(() => {
    if (showUnifiedSearch) {
      unifiedSearch.rebuildIndex(featureList as any);
      if (usQuery.trim()) {
        // Re-run search if we already have a query (e.g. features just loaded)
        doUsSearch(usQuery);
      }
    }
  }, [showUnifiedSearch, featureList]);

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
    if (!q.trim()) { setUsResults([]); setUsLoading(false); return; }
    unifiedSearch.refreshTestedStatus();
    let results = unifiedSearch.search(q, { maxResults: 30 });
    // Apply type filter
    if (usFilter !== 'all') results = results.filter(r => r.item.type === usFilter);
    if (usTestedFilter) results = results.filter(r => r.item.tested);
    setUsResults(results);
    // LLM enhancement if offline scores are low
    if (hasLLMApiKey() && results.length > 0 && results[0].score < 60) {
      setUsLoading(true);
      try {
        const apiKey = (window as any).__llmApiKey || localStorage.getItem('lingdong-llm-api-key') || '';
        const llmResult = await llmEnhanceSearch(q, results, apiKey);
        if (llmResult.enhanced) setUsResults(llmResult.results);
      } catch { /* keep offline results */ }
      setUsLoading(false);
    }
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

  const handleUsTested = (id: string, type: 'feature' | 'template' | 'node') => {
    itemMarker.toggleTested(id, type);
    unifiedSearch.refreshTestedStatus();
    if (usQuery.trim()) doUsSearch(usQuery);
  };

  const handleUsClose = () => {
    setUI({ show_unified_search: false });
  };

  const handleUsUseTemplate = (name: string) => {
    loadTemplate(name);
    handleUsClose();
  };

  const handleUsAddFeature = (featureName: string) => {
    const canvasEl = document.querySelector('.flow-canvas-wrapper') as HTMLElement;
    if (!canvasEl) return;
    const rect = canvasEl.getBoundingClientRect();
    const x = (rect.width / 2 - canvas_transform.x) / canvas_transform.scale - 110;
    const y = (rect.height / 2 - canvas_transform.y) / canvas_transform.scale - 40;
    const id = addNode('tool_call', Math.max(0, x), Math.max(0, y));
    updateNodeParams(id, { feature_name: featureName, params: {} });
    handleUsClose();
  };

  const handleUsAddNode = (nodeType: string) => {
    const canvasEl = document.querySelector('.flow-canvas-wrapper') as HTMLElement;
    if (!canvasEl) return;
    const rect = canvasEl.getBoundingClientRect();
    const x = (rect.width / 2 - canvas_transform.x) / canvas_transform.scale - 110;
    const y = (rect.height / 2 - canvas_transform.y) / canvas_transform.scale - 40;
    addNode(nodeType as NodeType, Math.max(0, x), Math.max(0, y));
    handleUsClose();
  };

  // ── Normal library state ──

  // Build merged list: native nodes + feature-derived items
  const allItems = useMemo(() => {
    const items: Array<{ kind: 'node'; def: (typeof NODE_DEFINITIONS)[NodeType] } | { kind: 'feature'; item: FeatureNodeItem }> = [];

    // Native node definitions (exclude start/end from library)
    const OBSOLETE_TYPES = new Set(['click','image_click','click_text','click_action','shutdown','reboot','logoff','hibernate','lock_workstation','system_sleep','display_off',
      'volume_set','volume_mute','show_desktop','set_wallpaper','empty_recycle_bin',
      'clipboard_set','clipboard_get','launch_program','kill_process','wait_process','focus_window']);
    for (const def of Object.values(NODE_DEFINITIONS)) {
      if (def.type === 'start' || def.type === 'end') continue;
      if (OBSOLETE_TYPES.has(def.type)) continue;
      items.push({ kind: 'node', def });
    }

    // Feature-derived items
    const features = getLibraryFeatures(featureList);
    for (const f of features) {
      items.push({ kind: 'feature', item: f });
    }

    return items;
  }, [featureList]);

  const filtered = useMemo(() => {
    return allItems.filter((item) => {
      const label = item.kind === 'node' ? item.def.label : item.item.label;
      const desc = item.kind === 'node' ? item.def.description : item.item.description;
      const typeName = item.kind === 'node' ? item.def.type : item.item.feature_name;
      const cat = item.kind === 'node' ? item.def.category : item.item.category;

      const matchSearch =
        !search ||
        label.includes(search) ||
        desc.includes(search) ||
        typeName.includes(search.toLowerCase());
      const matchCat = activeCategory === 'all' || cat === activeCategory;
      return matchSearch && matchCat;
    });
  }, [allItems, search, activeCategory, testedRefresh]);

  const handleDragStart = (e: React.DragEvent, item: typeof allItems[0]) => {
    if (item.kind === 'node') {
      e.dataTransfer.setData('node-type', item.def.type);
    } else {
      e.dataTransfer.setData('node-type', 'tool_call');
      e.dataTransfer.setData('feature-name', item.item.feature_name);
    }
    e.dataTransfer.effectAllowed = 'copy';
  };

  const handleDoubleClick = (item: typeof allItems[0]) => {
    const canvasEl = document.querySelector('.flow-canvas-wrapper') as HTMLElement;
    if (!canvasEl) return;
    const rect = canvasEl.getBoundingClientRect();
    const x = (rect.width / 2 - canvas_transform.x) / canvas_transform.scale - 110;
    const y = (rect.height / 2 - canvas_transform.y) / canvas_transform.scale - 40;

    if (item.kind === 'node') {
      addNode(item.def.type, Math.max(0, x), Math.max(0, y));
    } else {
      const id = addNode('tool_call', Math.max(0, x), Math.max(0, y));
      updateNodeParams(id, { feature_name: item.item.feature_name, params: {} });
    }
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
              {usLoading && <Loader2 size={14} className="spinner" style={{ color: 'var(--text-muted)', flexShrink: 0 }} />}
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
            <button className={`tab-btn ${usFilter === 'feature' ? 'active' : ''}`}
              onClick={() => { setUsFilter('feature'); setUsTestedFilter(false); }}>
              功能
            </button>
            <button className={`tab-btn ${usTestedFilter ? 'active' : ''}`}
              onClick={() => { setUsTestedFilter(!usTestedFilter); setUsFilter('all'); }}>
              已验证
            </button>
          </div>

          {/* Results */}
          <div className="node-library-body">
            {!usQuery.trim() && (
              <div style={{ textAlign: 'center', padding: '32px 16px', color: 'var(--text-muted)', fontSize: 13 }}>
                输入关键词搜索模板和功能指令
                <div style={{ marginTop: 12, display: 'flex', flexWrap: 'wrap', gap: 6, justifyContent: 'center' }}>
                  {['截图', '登录', '点击', '浏览器', '文件', '循环'].map(eq => (
                    <button key={eq} className="btn btn-ghost" style={{ fontSize: 11, padding: '2px 8px' }}
                      onClick={() => handleUsQuery(eq)}>
                      {eq}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {usQuery.trim() && usResults.length === 0 && !usLoading && (
              <div style={{ textAlign: 'center', padding: '32px 16px', color: 'var(--text-muted)', fontSize: 13 }}>
                未找到匹配的指令或模板
              </div>
            )}
            {usResults.map((r) => {
              const item = r.item;
              const isTemplate = item.type === 'template';
              const isNode = item.type === 'node';
              const isFeature = item.type === 'feature';
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
                    } else {
                      e.dataTransfer.setData('node-type', 'tool_call');
                      e.dataTransfer.setData('feature-name', (item as any).featureName || item.name);
                    }
                    e.dataTransfer.effectAllowed = 'copy';
                  }}
                  onClick={() => setUsExpanded(isExpanded ? null : item.id)}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%' }}>
                    {/* Type badge */}
                    <div style={{
                      fontSize: 9, fontWeight: 700, padding: '1px 5px', borderRadius: 4, flexShrink: 0,
                      background: isTemplate ? '#8b5cf6' : isNode ? '#22c55e' : '#0891b2',
                      color: '#fff', letterSpacing: '0.05em',
                    }}>
                      {isTemplate ? '模板' : isNode ? '节点' : '功能'}
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
                  {/* Expanded details */}
                  {isExpanded && (
                    <div style={{ marginTop: 6, padding: 8, background: 'var(--bg-secondary)', borderRadius: 6, fontSize: 11 }}>
                      {item.keywords && item.keywords.length > 0 && (
                        <div style={{ marginBottom: 4 }}>关键词: {item.keywords.join(', ')}</div>
                      )}
                      {item.tags && item.tags.length > 0 && (
                        <div style={{ marginBottom: 4 }}>标签: {item.tags.join(', ')}</div>
                      )}
                      <div style={{ color: 'var(--text-muted)', fontSize: 10 }}>相关性: {r.score}/100</div>
                      {r.reasons && r.reasons.length > 0 && (
                        <div style={{ marginTop: 4 }}>匹配理由: {r.reasons.join('; ')}</div>
                      )}
                      <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
                        {isTemplate ? (
                          <button className="btn btn-primary" style={{ fontSize: 11, padding: '3px 10px' }}
                            onClick={(e) => { e.stopPropagation(); handleUsUseTemplate(item.name); }}>
                            使用模板
                          </button>
                        ) : isNode ? (
                          <button className="btn btn-primary" style={{ fontSize: 11, padding: '3px 10px' }}
                            onClick={(e) => { e.stopPropagation(); handleUsAddNode((item as any).nodeType || item.id); }}>
                            添加到画布
                          </button>
                        ) : (
                          <button className="btn btn-primary" style={{ fontSize: 11, padding: '3px 10px' }}
                            onClick={(e) => { e.stopPropagation(); handleUsAddFeature((item as any).featureName || item.name); }}>
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
                {featureList.length > 0 ? `${allItems.length} 个指令可用` : '加载中...'}
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
            {ALL_CATEGORIES.map((cat) => (
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
              const label = item.kind === 'node' ? item.def.label : item.item.label;
              const desc = item.kind === 'node' ? item.def.description : item.item.description;
              const icon = item.kind === 'node' ? item.def.icon : item.item.icon;
              const color = item.kind === 'node' ? item.def.color : item.item.color;
              const key = item.kind === 'node' ? `node-${item.def.type}` : `feature-${item.item.feature_name}`;
              const itemId = item.kind === 'node' ? item.def.type : item.item.feature_name;
              const itemType = item.kind === 'node' ? 'node' as const : 'feature' as const;
              const tested = itemMarker.isTested(itemId, itemType);

              return (
                <div
                  key={key}
                  className="node-item"
                  draggable
                  onDragStart={(e) => handleDragStart(e, item)}
                  onDoubleClick={() => handleDoubleClick(item)}
                  title={`拖拽到画布 或 双击添加\n${desc}`}
                  style={{ opacity: tested ? 1 : 0.85 }}
                >
                  <div
                    className="node-item-icon"
                    style={{ background: color }}
                  >
                    <NodeIcon name={icon} size={15} />
                  </div>
                  <div className="node-item-info">
                    <div className="node-item-name">{label}</div>
                    <div className="node-item-desc">{desc.slice(0, 60)}</div>
                  </div>
                  <div title={tested ? '已测试 — 点击取消标记' : '未测试 — 点击标记为已测试'}
                    onClick={(e) => { e.stopPropagation(); itemMarker.toggleTested(itemId, itemType); setTestedRefresh(k => k + 1); }}
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
              {['mouse_operation', 'wait', 'condition'].map((type) => {
                const def = NODE_DEFINITIONS[type as NodeType];
                if (!def) return null;
                return (
                  <div
                    key={type}
                    className="node-item"
                    style={{ padding: '5px 8px' }}
                    draggable
                    onDragStart={(e) => {
                      e.dataTransfer.setData('node-type', type);
                      e.dataTransfer.effectAllowed = 'copy';
                    }}
                    onDoubleClick={() => handleDoubleClick({ kind: 'node', def })}
                  >
                    <div className="node-item-icon" style={{ background: def.color, width: 24, height: 24 }}>
                      <NodeIcon name={def.icon} size={12} />
                    </div>
                    <div className="node-item-name" style={{ fontSize: 12 }}>{def.label}</div>
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