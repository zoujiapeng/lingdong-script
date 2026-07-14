import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useStore } from '../../store';
import { unifiedSearch, UnifiedSearchItem, UnifiedSearchResult } from '../../utils/unifiedSearch';
import { llmEnhanceSearch, hasLLMApiKey } from '../../utils/llmSearcher';
import { itemMarker } from '../../utils/itemMarker';
import { Search, X, Check, RotateCw, Sparkles, Loader2, Tag, Database, FileType, Star } from 'lucide-react';
import { displaySearchReason, displaySearchToken } from '../../utils/displayLabels';

type FilterTab = 'all' | 'template' | 'feature' | 'tested';

interface UnifiedSearchDialogProps {
  onClose: () => void;
  onSelectTemplate?: (entry: { id: string; name: string }) => void;
  onCallFeature?: (featureName: string, params?: Record<string, any>) => void;
  initialQuery?: string;
}

const OVERLAY_STYLE: React.CSSProperties = {
  position: 'fixed', inset: 0, zIndex: 1000,
  background: 'rgba(0,0,0,0.45)',
  display: 'flex', alignItems: 'flex-start', justifyContent: 'center',
  paddingTop: '8vh',
};

const DIALOG_STYLE: React.CSSProperties = {
  background: 'var(--bg-primary)', borderRadius: 12,
  boxShadow: '0 12px 48px rgba(0,0,0,0.25)',
  width: 680, maxHeight: '80vh', display: 'flex', flexDirection: 'column',
  overflow: 'hidden',
};

const FILTER_TABS: { key: FilterTab; label: string; icon: React.ReactNode }[] = [
  { key: 'all', label: '全部', icon: null },
  { key: 'template', label: '模板', icon: <FileType size={13} /> },
  { key: 'feature', label: '功能', icon: <Database size={13} /> },
  { key: 'tested', label: '已验证', icon: <Check size={13} /> },
];

const EXAMPLE_QUERIES = ['截图', '登录', '点击', '浏览器', '文件', '循环'];

export function UnifiedSearchDialog({
  onClose,
  onSelectTemplate,
  onCallFeature,
  initialQuery = '',
}: UnifiedSearchDialogProps) {
  const featureList = useStore(s => s.featureList);
  const addLog = useStore(s => s.addLog);
  const loadTemplate = useStore(s => s.loadTemplate);
  const callFeatureDirect = useStore(s => s.callFeatureDirect);

  const [query, setQuery] = useState(initialQuery);
  const [results, setResults] = useState<UnifiedSearchResult[]>([]);
  const [filter, setFilter] = useState<FilterTab>('all');
  const [loading, setLoading] = useState(false);
  const [llmLoading, setLlmLoading] = useState(false);
  const [llmEnhanced, setLlmEnhanced] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [selectedItem, setSelectedItem] = useState<UnifiedSearchItem | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const searchTimer = useRef<ReturnType<typeof setTimeout>>();

  // Rebuild index with latest features on mount
  useEffect(() => {
    unifiedSearch.rebuildIndex(featureList.length > 0 ? featureList as any : undefined);
  }, [featureList]);

  // Focus input on mount
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Debounced search
  const doSearch = useCallback(async (q: string) => {
    if (!q.trim()) {
      setResults([]);
      setLoading(false);
      setError(null);
      return;
    }

    setLoading(true);
    setError(null);
    setLlmEnhanced(false);

    try {
      // Update tested status
      unifiedSearch.refreshTestedStatus();

      const offline = unifiedSearch.search(q, { maxResults: 20 });
      let final = offline;
      let enhanced = false;

      // LLM enhancement if low confidence
      if (offline.length === 0 || offline[0].score < 60) {
        const apiKey = (window as any).__llmApiKey || localStorage.getItem('lingdong-llm-api-key') || '';
        if (apiKey) {
          setLlmLoading(true);
          const llmResult = await llmEnhanceSearch(q, offline, apiKey);
          setLlmLoading(false);
          if (llmResult.enhanced) {
            final = llmResult.results;
            enhanced = true;
          }
        }
      }

      setResults(final);
      setLlmEnhanced(enhanced);
    } catch (e: any) {
      setError(e?.message || '搜索出错');
      setResults([]);
    } finally {
      setLoading(false);
    }
  }, []);

  const handleInputChange = (value: string) => {
    setQuery(value);
    if (searchTimer.current) clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => doSearch(value), 300);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') onClose();
  };

  // Toggle tested mark
  const toggleTested = (id: string, type: 'feature' | 'template' | 'node') => {
    itemMarker.toggleTested(id, type);
    unifiedSearch.refreshTestedStatus();
    setRefreshKey(k => k + 1);
  };

  // Use a template (load into canvas)
  const handleUseTemplate = (item: UnifiedSearchItem) => {
    if (onSelectTemplate) {
      onSelectTemplate({ id: item.id, name: item.name });
    } else {
      loadTemplate(item.id);
    }
    onClose();
  };

  // Call a feature directly
  const handleCallFeature = async (item: UnifiedSearchItem) => {
    if (onCallFeature) {
      onCallFeature(item.featureName || item.id, {});
    } else {
      try {
        await callFeatureDirect(item.featureName || item.id, item.name, {});
        addLog({ level: 'info', message: `调用了功能: ${item.name}` });
      } catch (e: any) {
        addLog({ level: 'error', message: `调用功能失败: ${e?.message}` });
      }
    }
    onClose();
  };

  // Show feature detail
  const handleShowDetail = (item: UnifiedSearchItem) => {
    setSelectedItem(selectedItem?.id === item.id ? null : item);
  };

  // Filter results
  const filtered = results.filter(r => {
    if (filter === 'template') return r.item.type === 'template';
    if (filter === 'feature') return r.item.type === 'feature';
    if (filter === 'tested') return r.item.tested;
    return true;
  });

  return (
    <div style={OVERLAY_STYLE} onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      onKeyDown={handleKeyDown} tabIndex={-1}>
      <div style={DIALOG_STYLE} onClick={e => e.stopPropagation()}>

        {/* ── Search bar ── */}
        <div style={{
          padding: '16px 16px 12px', borderBottom: '1px solid var(--border-primary)',
          display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0,
        }}>
          <Search size={18} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
          <input ref={inputRef}
            value={query}
            onChange={e => handleInputChange(e.target.value)}
            placeholder="搜索指令库 — 输入关键词查找模板或功能..."
            style={{
              flex: 1, border: 'none', outline: 'none', fontSize: 15,
              background: 'transparent', color: 'var(--text-primary)',
              fontFamily: 'inherit',
            }}
          />
          {llmLoading && <Loader2 size={16} style={{ color: 'var(--accent)', animation: 'spin 1s linear infinite' }} />}
          {llmEnhanced && <span title="AI 增强搜索"><Sparkles size={16} style={{ color: 'var(--accent)' }} /></span>}
          <button onClick={onClose}
            style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', padding: 4, display: 'flex' }}
            title="关闭 (Esc)"><X size={18} /></button>
        </div>

        {/* ── Filter tabs ── */}
        <div style={{
          display: 'flex', gap: 4, padding: '8px 16px',
          borderBottom: '1px solid var(--border-primary)', flexShrink: 0,
        }}>
          {FILTER_TABS.map(tab => (
            <button key={tab.key}
              onClick={() => setFilter(tab.key)}
              style={{
                display: 'flex', alignItems: 'center', gap: 4,
                padding: '4px 10px', borderRadius: 6, fontSize: 12,
                border: `1px solid ${filter === tab.key ? 'var(--accent)' : 'var(--border-primary)'}`,
                background: filter === tab.key ? 'var(--accent-light)' : 'transparent',
                color: filter === tab.key ? 'var(--accent)' : 'var(--text-secondary)',
                cursor: 'pointer', fontWeight: filter === tab.key ? 600 : 400,
              }}>
              {tab.icon}
              {tab.label}
              {tab.key === 'all' && results.length > 0 && (
                <span style={{ fontSize: 10, opacity: 0.7 }}>{results.length}</span>
              )}
            </button>
          ))}
        </div>

        {/* ── Results area ── */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '4px 0' }}>

          {/* Loading state */}
          {loading && (
            <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>
              <Loader2 size={24} style={{ margin: '0 auto 8px', animation: 'spin 1s linear infinite' }} />
              <div style={{ fontSize: 13 }}>搜索中...</div>
            </div>
          )}

          {/* Error state */}
          {error && !loading && (
            <div style={{ padding: 40, textAlign: 'center' }}>
              <div style={{ fontSize: 13, color: 'var(--error)' }}>{error}</div>
              <button onClick={() => doSearch(query)}
                style={{ marginTop: 8, padding: '6px 14px', fontSize: 12, cursor: 'pointer' }}
                className="btn btn-ghost">重试</button>
            </div>
          )}

          {/* Empty state */}
          {!loading && !error && query.trim() === '' && (
            <div style={{ padding: 40, textAlign: 'center' }}>
              <Search size={32} style={{ color: 'var(--text-muted)', margin: '0 auto 12px', opacity: 0.3 }} />
              <div style={{ fontSize: 14, color: 'var(--text-secondary)', marginBottom: 12 }}>
                输入关键词搜索指令库
              </div>
              <div style={{ display: 'flex', gap: 6, justifyContent: 'center', flexWrap: 'wrap' }}>
                {EXAMPLE_QUERIES.map(eq => (
                  <button key={eq} onClick={() => { setQuery(eq); doSearch(eq); }}
                    style={{
                      padding: '4px 10px', fontSize: 12, borderRadius: 6,
                      border: '1px solid var(--border-primary)',
                      background: 'var(--bg-secondary)', cursor: 'pointer',
                      color: 'var(--text-secondary)',
                    }}>{eq}</button>
                ))}
              </div>
            </div>
          )}

          {/* No results state */}
          {!loading && !error && query.trim() !== '' && filtered.length === 0 && (
            <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>
              <div style={{ fontSize: 14, marginBottom: 4 }}>未找到匹配的指令或模板</div>
              <div style={{ fontSize: 12 }}>试试其他关键词，或使用 AI 搜索</div>
            </div>
          )}

          {/* Results list */}
          {!loading && !error && filtered.length > 0 && (
            <div>
              {llmEnhanced && (
                <div style={{
                  padding: '6px 16px', fontSize: 11, color: 'var(--accent)',
                  display: 'flex', alignItems: 'center', gap: 4,
                }}>
                  <Sparkles size={12} /> AI 增强搜索 — 离线分较低时由 AI 语义重排序
                </div>
              )}
              {filtered.map((r, i) => (
                <div key={`${r.item.type}:${r.item.id}`}
                  style={{
                    padding: '10px 16px', cursor: 'pointer',
                    borderLeft: `3px solid ${getScoreColor(r.score)}`,
                    background: i % 2 === 0 ? 'transparent' : 'var(--bg-secondary)',
                    opacity: r.item.tested ? 1 : 0.85,
                  }}
                  onClick={() => handleShowDetail(r.item)}>

                  {/* Row 1: Type badge + Name + Score + Tested */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
                    {/* Type badge */}
                    <span style={getTypeBadgeStyle(r.item.type)}>
                      {r.item.type === 'template' ? '模板' : '功能'}
                    </span>

                    {/* Name */}
                    <span style={{
                      fontSize: 13, fontWeight: 600, color: 'var(--text-primary)',
                      flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>
                      {r.item.name}
                    </span>

                    {/* Tested badge */}
                    <span onClick={(e) => { e.stopPropagation(); toggleTested(r.item.id, r.item.type); }}
                      style={{
                        fontSize: 11, padding: '1px 6px', borderRadius: 4,
                        cursor: 'pointer', userSelect: 'none',
                        background: r.item.tested ? '#16a34a20' : 'transparent',
                        color: r.item.tested ? '#16a34a' : 'var(--text-muted)',
                        border: `1px solid ${r.item.tested ? '#16a34a40' : 'var(--border-primary)'}`,
                        display: 'flex', alignItems: 'center', gap: 3,
                      }}
                      title={r.item.tested ? '点击取消标记' : '点击标记为已测试'}>
                      {r.item.tested ? <><Check size={10} /> 已验证</> : '未测试'}
                    </span>

                    {/* Score bar */}
                    <div style={{
                      width: 50, height: 4, borderRadius: 2,
                      background: 'var(--border-primary)', overflow: 'hidden', flexShrink: 0,
                    }}>
                      <div style={{
                        width: `${Math.min(r.score, 100)}%`, height: '100%',
                        background: getScoreColor(r.score),
                        borderRadius: 2, transition: 'width 0.2s',
                      }} />
                    </div>
                    <span style={{ fontSize: 10, color: 'var(--text-muted)', width: 24, textAlign: 'right' }}>
                      {r.score}
                    </span>
                  </div>

                  {/* Row 2: Description */}
                  <div style={{
                    fontSize: 12, color: 'var(--text-secondary)',
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    paddingLeft: 4, marginBottom: selectedItem?.id === r.item.id ? 4 : 0,
                  }}>
                    {r.item.description}
                  </div>

                  {/* Row 3: Tags */}
                  {r.item.tags.length > 0 && (
                    <div style={{ display: 'flex', gap: 4, paddingLeft: 4, marginTop: 3 }}>
                      {r.item.tags.slice(0, 3).map(tag => (
                        <span key={tag} style={{
                          fontSize: 10, padding: '1px 5px', borderRadius: 3,
                          background: 'var(--bg-secondary)', color: 'var(--text-muted)',
                          border: '1px solid var(--border-primary)',
                        }}>
                          <Tag size={8} style={{ marginRight: 2, verticalAlign: 'middle' }} />
                          {displaySearchToken(tag)}
                        </span>
                      ))}
                    </div>
                  )}

                  {/* Expanded detail */}
                  {selectedItem?.id === r.item.id && (
                    <div style={{
                      marginTop: 8, padding: '8px 10px', borderRadius: 6,
                      background: 'var(--bg-secondary)',
                      fontSize: 12, color: 'var(--text-secondary)',
                    }}>
                      {/* Match reasons */}
                      {r.reasons.length > 0 && (
                        <div style={{ marginBottom: 6 }}>
                          <span style={{ fontWeight: 600 }}>匹配原因: </span>
                          {r.reasons.map(displaySearchReason).join('；')}
                        </div>
                      )}

                      {/* Feature params */}
                      {r.item.type === 'feature' && r.item.params && r.item.params.length > 0 && (
                        <div style={{ marginBottom: 6 }}>
                          <span style={{ fontWeight: 600 }}>参数: </span>
                          {r.item.params.map(p => (
                            <span key={p.name} style={{ marginRight: 8 }}>
                              {p.name}
                              {p.required ? <span style={{ color: 'var(--error)' }}>*</span> : '?'}
                            </span>
                          ))}
                        </div>
                      )}

                      {/* Actions */}
                      <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                        {r.item.type === 'template' && r.item.hasScript && (
                          <button className="btn btn-primary"
                            onClick={(e) => { e.stopPropagation(); handleUseTemplate(r.item); }}
                            style={{ padding: '4px 10px', fontSize: 11 }}>
                            使用此模板
                          </button>
                        )}
                        {r.item.type === 'feature' && (
                          <button className="btn btn-primary"
                            onClick={(e) => { e.stopPropagation(); handleCallFeature(r.item); }}
                            style={{ padding: '4px 10px', fontSize: 11 }}>
                            调用功能
                          </button>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* ── Footer ── */}
        <div style={{
          padding: '8px 16px', borderTop: '1px solid var(--border-primary)',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          fontSize: 11, color: 'var(--text-muted)', flexShrink: 0,
        }}>
          <span>
            {unifiedSearch.count} 条索引
            {llmEnhanced && <span style={{ color: 'var(--accent)', marginLeft: 4 }}>· AI 增强已启用</span>}
          </span>
          <span>
            Esc 关闭 · 点击名称查看详情 · 标记「已验证」
          </span>
        </div>
      </div>
    </div>
  );
}

/* ── Helpers ── */

function getScoreColor(score: number): string {
  if (score >= 80) return '#16a34a';
  if (score >= 50) return '#f59e0b';
  return '#6b7280';
}

function getTypeBadgeStyle(type: string): React.CSSProperties {
  if (type === 'template') {
    return {
      fontSize: 10, fontWeight: 600, padding: '1px 5px', borderRadius: 3,
      background: '#3b82f620', color: '#3b82f6',
    };
  }
  return {
    fontSize: 10, fontWeight: 600, padding: '1px 5px', borderRadius: 3,
    background: '#8b5cf620', color: '#8b5cf6',
  };
}
