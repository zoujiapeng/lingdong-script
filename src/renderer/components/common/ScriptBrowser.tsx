import React, { useState, useMemo } from 'react';
import { Search, FileCode, X, AlertTriangle, Library } from 'lucide-react';
import { useStore } from '../../store';
import { templateRegistry, TemplateEntry } from '../../utils/templateRegistry';
import { unifiedSearch } from '../../utils/unifiedSearch';
import { UnifiedSearchDialog } from './UnifiedSearchDialog';

interface ScriptBrowserProps {
  onSelect: (entry: { id: string; name: string }) => void;
  onCancel: () => void;
}

export function ScriptBrowser({ onSelect, onCancel }: ScriptBrowserProps) {
  const [search, setSearch] = useState('');
  const [showUnifiedSearch, setShowUnifiedSearch] = useState(false);
  const scripts = useStore((s) => s.scripts);
  const activeScriptId = useStore((s) => s.activeScriptId);
  const featureList = useStore((s) => s.featureList);

  // Combine store scripts with registry templates
  const allScripts = useMemo(() => {
    const map = new Map<string, { id: string; name: string; description: string; tags: string[]; source: string; nodeCount: number }>();

    // From store
    for (const [id, script] of Object.entries(scripts)) {
      if (!map.has(id)) {
        map.set(id, {
          id,
          name: script.meta.name || '未命名',
          description: script.meta.description || '',
          tags: script.meta.tags || [],
          source: 'store',
          nodeCount: script.nodes.length,
        });
      }
    }

    // From registry (presets)
    for (const entry of templateRegistry.getAll()) {
      if (!map.has(entry.id) && entry.hasScript) {
        map.set(entry.id, {
          id: entry.id,
          name: entry.name,
          description: entry.description,
          tags: entry.tags,
          source: entry.source,
          nodeCount: entry.flowScript?.nodes?.length || 0,
        });
      }
    }

    return Array.from(map.values());
  }, [scripts]);

  const filtered = useMemo(() => {
    if (!search.trim()) return allScripts;
    // Use unified search for better matching
    unifiedSearch.rebuildIndex(featureList as any);
    const unified = unifiedSearch.search(search, { types: ['template'], maxResults: 20, minScore: 10 });
    const matchedIds = new Set(unified.map(r => r.item.id));
    return allScripts.filter(s => matchedIds.has(s.id));
  }, [allScripts, search, featureList]);

  const handleSelect = (entry: { id: string; name: string }) => {
    // Warn if selecting current script (circular reference)
    if (entry.id === activeScriptId) {
      if (!confirm('选择当前脚本可能导致循环引用。是否继续？')) return;
    }
    onSelect(entry);
  };

  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal-content script-browser" onClick={(e) => e.stopPropagation()}>
        <div className="script-browser-header">
          <FileCode size={18} />
          <span>选择脚本</span>
          <button className="btn btn-ghost modal-close" onClick={onCancel}>
            <X size={14} />
          </button>
        </div>

        <div className="script-browser-search">
          <Search size={14} />
          <input
            type="text"
            placeholder="搜索脚本名称、描述、标签..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            autoFocus
          />
          <button className="btn btn-ghost" onClick={() => setShowUnifiedSearch(true)}
            style={{ fontSize: 11, padding: '3px 8px', marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 4 }}>
            <Library size={12} /> 搜索全部指令库
          </button>
        </div>
        {showUnifiedSearch && <UnifiedSearchDialog
          onClose={() => setShowUnifiedSearch(false)}
          onSelectTemplate={(entry) => { onSelect(entry); setShowUnifiedSearch(false); }}
        />}

        <div className="script-browser-list">
          {filtered.length === 0 && (
            <div className="script-browser-empty">
              <AlertTriangle size={16} />
              <span>{search ? '未找到匹配的脚本' : '暂无可用脚本'}</span>
            </div>
          )}
          {filtered.map((entry) => (
            <div
              key={entry.id}
              className={`script-browser-item ${entry.id === activeScriptId ? 'script-browser-item-active' : ''}`}
              onClick={() => handleSelect(entry)}
            >
              <div className="script-browser-item-info">
                <div className="script-browser-item-name">
                  {entry.name}
                  {entry.id === activeScriptId && <span className="badge">当前</span>}
                </div>
                <div className="script-browser-item-desc">{entry.description}</div>
                <div className="script-browser-item-meta">
                  <span className="tag">{entry.source}</span>
                  <span className="tag">{entry.nodeCount} 节点</span>
                  {entry.tags.slice(0, 3).map((t) => (
                    <span key={t} className="tag">{t}</span>
                  ))}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
