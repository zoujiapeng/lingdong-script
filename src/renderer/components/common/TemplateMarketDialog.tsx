import React, { useState } from 'react';
import { Search, Download, X, ExternalLink } from 'lucide-react';

const OVERLAY: React.CSSProperties = {
  position: 'fixed', inset: 0, zIndex: 1100,
  background: 'rgba(0,0,0,0.4)',
  display: 'flex', alignItems: 'center', justifyContent: 'center',
};
const DIALOG: React.CSSProperties = {
  background: 'var(--bg-primary)', borderRadius: 12,
  boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
  width: 440, maxHeight: '70vh', display: 'flex', flexDirection: 'column',
};

export function TemplateMarketDialog({ onClose }: { onClose: () => void }) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<any[]>([]);
  const [searched, setSearched] = useState(false);
  const [searching, setSearching] = useState(false);

  const handleSearch = async () => {
    if (!query.trim()) return;
    setSearching(true);
    setSearched(true);
    try {
      const api = (window as any).electronAPI;
      const r = await api?.callFeature('search_templates', { query: query.trim(), limit: 20 });
      const list = Array.isArray(r) ? r : r?.result || [];
      setResults(list);
    } catch {
      setResults([]);
    }
    setSearching(false);
  };

  return (
    <div style={OVERLAY} onClick={onClose}>
      <div style={DIALOG} onClick={e => e.stopPropagation()}>
        <div style={{
          padding: '14px 16px', borderBottom: '1px solid var(--border-primary)',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          fontSize: 15, fontWeight: 600,
        }}>
          <span>模板市场</span>
          <button className="btn btn-ghost btn-icon" onClick={onClose}><X size={14} /></button>
        </div>

        <div style={{ padding: 16, display: 'flex', gap: 8 }}>
          <input className="settings-input" value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSearch()}
            placeholder="搜索本地模板库..."
            style={{ flex: 1, fontSize: 13 }} />
          <button className="btn btn-primary" onClick={handleSearch} disabled={searching || !query.trim()}
            style={{ padding: '6px 14px', fontSize: 12 }}>
            <Search size={12} style={{ marginRight: 4 }} />
            搜索
          </button>
        </div>

        <div style={{ flex: 1, overflow: 'auto', padding: '0 16px 16px' }}>
          {!searched && (
            <div style={{ fontSize: 12, color: 'var(--text-muted)', textAlign: 'center', padding: 20, lineHeight: 1.6 }}>
              输入关键字搜索模板<br />
              <span style={{ fontSize: 11 }}>远程社区模板请使用 CLI: <code>python tools/template_market.py search &lt;query&gt;</code></span>
            </div>
          )}
          {searched && !searching && results.length === 0 && (
            <div style={{ fontSize: 12, color: 'var(--text-muted)', textAlign: 'center', padding: 20 }}>
              未找到匹配模板
            </div>
          )}
          {results.map((tpl: any, i: number) => (
            <div key={tpl.id || i} style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '8px 10px', marginBottom: 6,
              borderRadius: 6, border: '1px solid var(--border-primary)',
              background: 'var(--bg-secondary)',
            }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600, fontSize: 12 }}>{tpl.name || tpl.id}</div>
                <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{tpl.description || ''}</div>
              </div>
              <button className="btn btn-ghost" onClick={() => {
                const api = (window as any).electronAPI;
                api?.loadTemplate?.(tpl.id || tpl.name);
                onClose();
              }} style={{ padding: '4px 10px', fontSize: 11, whiteSpace: 'nowrap' }}>
                <Download size={11} style={{ marginRight: 3 }} />
                加载
              </button>
            </div>
          ))}
        </div>

        <div style={{
          padding: '8px 16px', borderTop: '1px solid var(--border-primary)',
          fontSize: 11, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 6,
        }}>
          <ExternalLink size={11} />
          远程模板: <code>python tools/template_market.py search {query || '&lt;keyword&gt;'}</code>
        </div>
      </div>
    </div>
  );
}
