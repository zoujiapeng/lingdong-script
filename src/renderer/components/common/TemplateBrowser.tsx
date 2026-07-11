import React, { useState, useMemo, useCallback, useEffect } from 'react';
import { X, Search, Download, Upload, Trash2, Edit3, FileType, Tag, Grid3X3, Layers, Loader2, Check, AlertTriangle, Plus, Save, ExternalLink } from 'lucide-react';
import { useStore, loadCustomTemplates, saveCustomTemplates, CustomTemplate } from '../../store';
import { templateRegistry, TemplateEntry } from '../../utils/templateRegistry';

// ── Types ──

interface FileTemplate {
  name: string;
  filename: string;
  filetype: 'dsl' | 'flow';
  description: string;
  category: string;
  tags: string[];
  aliases: string[];
  node_count: number;
  node_types?: Record<string, number>;
}

interface DisplayItem {
  id: string;
  name: string;
  description: string;
  category: string;
  tags: string[];
  source: 'preset' | 'custom' | 'dsl' | 'flow';
  node_count: number;
  node_types?: Record<string, number>;
  raw: TemplateEntry | CustomTemplate | FileTemplate;
}

const CATEGORIES = [
  { key: '', label: '全部', icon: Grid3X3 },
  { key: 'app', label: '应用操作', icon: Layers },
  { key: 'image', label: '图像识别', icon: FileType },
  { key: 'system', label: '系统工具', icon: Layers },
  { key: 'web', label: '网页', icon: ExternalLink },
  { key: 'custom', label: '自定义', icon: Tag },
];

const SOURCE_LABELS: Record<string, string> = {
  preset: '内置', custom: '自定义', dsl: 'DSL', flow: '流程图',
};

const SOURCE_COLORS: Record<string, string> = {
  preset: '#6366f1', custom: '#22c55e', dsl: '#f59e0b', flow: '#3b82f6',
};

const NODE_TYPE_LABELS: Record<string, string> = {
  click: '点击', input_text: '输入', keyboard: '按键', wait: '等待',
  screenshot: '截图', image_click: '图识', click_text: 'OCR',
  condition: '条件', loop: '循环', sub_script: '子流程',
  launch_program: '启动', tool_call: '工具', web_browser: '浏览器',
  popup_close: '弹窗', swipe: '滑动', camera_capture: '拍照',
};

const OVERLAY: React.CSSProperties = {
  position: 'fixed', inset: 0, zIndex: 1100,
  background: 'rgba(0,0,0,0.45)',
  display: 'flex', alignItems: 'center', justifyContent: 'center',
};

const DIALOG: React.CSSProperties = {
  background: 'var(--bg-primary)', borderRadius: 12,
  boxShadow: '0 8px 40px rgba(0,0,0,0.3)',
  width: 720, maxWidth: '90vw', height: 560, maxHeight: '80vh',
  display: 'flex', flexDirection: 'column',
  overflow: 'hidden',
};

// ── Template Card ──

function TemplateCard({
  item, onLoad, onExport, onDelete, onEdit,
}: {
  item: DisplayItem;
  onLoad: (item: DisplayItem) => void;
  onExport?: (item: DisplayItem) => void;
  onDelete?: (item: DisplayItem) => void;
  onEdit?: (item: DisplayItem) => void;
}) {
  const types = item.node_types;
  const typeEntries = types ? Object.entries(types).filter(([k]) => k !== 'start' && k !== 'end').slice(0, 4) : [];

  return (
    <div style={{
      background: 'var(--bg-secondary)',
      border: '1px solid var(--border-primary)',
      borderRadius: 8, padding: '10px 12px',
      display: 'flex', flexDirection: 'column', gap: 6,
      cursor: 'pointer', transition: 'border-color 0.15s, box-shadow 0.15s',
      position: 'relative',
    }}
      onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--accent)'; e.currentTarget.style.boxShadow = '0 0 0 1px var(--accent)'; }}
      onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border-primary)'; e.currentTarget.style.boxShadow = 'none'; }}
      onClick={() => onLoad(item)}
    >
      {/* Source badge */}
      <span style={{
        position: 'absolute', top: 6, right: 6,
        fontSize: 9, fontWeight: 600, padding: '1px 6px',
        borderRadius: 4, color: '#fff',
        background: SOURCE_COLORS[item.source] || '#666',
      }}>
        {SOURCE_LABELS[item.source] || item.source}
      </span>

      {/* Name */}
      <div style={{ fontWeight: 600, fontSize: 13, paddingRight: 50, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {item.name}
      </div>

      {/* Description */}
      <div style={{ fontSize: 11, color: 'var(--text-muted)', lineHeight: 1.4, minHeight: 30, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' } as any}>
        {item.description || '暂无描述'}
      </div>

      {/* Tags */}
      {item.tags.length > 0 && (
        <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap' }}>
          {item.tags.slice(0, 4).map(t => (
            <span key={t} style={{ fontSize: 9, padding: '1px 5px', borderRadius: 3, background: 'var(--accent-light)', color: 'var(--accent)' }}>{t}</span>
          ))}
        </div>
      )}

      {/* Node types preview + count */}
      {(item.node_count > 0 || typeEntries.length > 0) && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>{item.node_count} 节点</span>
          {typeEntries.map(([type, count]) => (
            <span key={type} style={{ fontSize: 9, padding: '0 4px', borderRadius: 3, background: 'var(--border-primary)', color: 'var(--text-muted)' }}>
              {NODE_TYPE_LABELS[type] || type}{count > 1 ? `×${count}` : ''}
            </span>
          ))}
        </div>
      )}

      {/* Actions (shown on hover via buttons) */}
      <div style={{ display: 'flex', gap: 4, marginTop: 2, opacity: 0.6 }}
        onClick={e => e.stopPropagation()}
        onMouseEnter={e => { e.currentTarget.style.opacity = '1'; }}
        onMouseLeave={e => { e.currentTarget.style.opacity = '0.6'; }}>
        {item.source === 'flow' && onExport && (
          <button className="btn btn-ghost" onClick={() => onExport(item)} style={{ padding: '2px 8px', fontSize: 10, gap: 3 }}>
            <Download size={10} /> 导出
          </button>
        )}
        {item.source === 'custom' && onDelete && (
          <button className="btn btn-ghost" onClick={() => onDelete(item)} style={{ padding: '2px 8px', fontSize: 10, gap: 3, color: '#ef4444' }}>
            <Trash2 size={10} /> 删除
          </button>
        )}
        {item.source === 'custom' && onEdit && (
          <button className="btn btn-ghost" onClick={() => onEdit(item)} style={{ padding: '2px 8px', fontSize: 10, gap: 3 }}>
            <Edit3 size={10} /> 编辑
          </button>
        )}
      </div>
    </div>
  );
}

// ── Edit Metadata Dialog ──

function EditMetadataDialog({ item, onSave, onClose }: {
  item: DisplayItem;
  onSave: (name: string, description: string, tags: string[]) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(item.name);
  const [desc, setDesc] = useState(item.description);
  const [tags, setTags] = useState(item.tags.join(', '));

  return (
    <div style={OVERLAY} onClick={onClose}>
      <div style={{ ...DIALOG, width: 400, height: 'auto' }} onClick={e => e.stopPropagation()}>
        <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border-primary)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 15, fontWeight: 600 }}>
          <span>编辑模板元数据</span>
          <button className="btn btn-ghost btn-icon" onClick={onClose}><X size={14} /></button>
        </div>
        <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <label className="form-label">名称</label>
            <input className="form-input" value={name} onChange={e => setName(e.target.value)} placeholder="模板名称" />
          </div>
          <div>
            <label className="form-label">描述</label>
            <textarea className="form-input" value={desc} onChange={e => setDesc(e.target.value)} placeholder="模板描述" rows={3} style={{ resize: 'vertical' }} />
          </div>
          <div>
            <label className="form-label">标签（逗号分隔）</label>
            <input className="form-input" value={tags} onChange={e => setTags(e.target.value)} placeholder="标签1, 标签2" />
          </div>
          <button className="btn btn-primary" onClick={() => onSave(name, desc, tags.split(',').map(s => s.trim()).filter(Boolean))}
            style={{ alignSelf: 'flex-end', padding: '8px 20px' }}>
            <Check size={14} style={{ marginRight: 4 }} /> 保存
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Export Dialog ──

function ExportDialog({ item, onClose }: { item: DisplayItem; onClose: () => void }) {
  const [exporting, setExporting] = useState(false);
  const [done, setDone] = useState(false);
  const script = useStore(s => s.script);

  const handleExport = useCallback(async () => {
    setExporting(true);
    try {
      const electronAPI = (window as any).electronAPI;
      if (!electronAPI?.saveFileDialog || !electronAPI?.writeFile) {
        console.warn('No file dialog API available');
        setDone(true);
        setExporting(false);
        return;
      }
      const fileName = `${item.name.replace(/[/\\?%*:|"<>]/g, '_')}.flow`;
      const filepath = await electronAPI.saveFileDialog(fileName);
      if (!filepath) {
        setExporting(false);
        return; // user cancelled
      }
      const flowJson = JSON.stringify(script, null, 2);
      await electronAPI.writeFile(filepath, flowJson);
      console.log('Template exported to:', filepath);
      setDone(true);
    } catch (e: any) {
      console.error('Export failed:', e);
    }
    setExporting(false);
  }, [item, script]);

  return (
    <div style={OVERLAY} onClick={onClose}>
      <div style={{ ...DIALOG, width: 380, height: 'auto' }} onClick={e => e.stopPropagation()}>
        <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border-primary)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 15, fontWeight: 600 }}>
          <span>导出模板</span>
          <button className="btn btn-ghost btn-icon" onClick={onClose}><X size={14} /></button>
        </div>
        <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ fontSize: 13 }}>模板: <strong>{item.name}</strong></div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', lineHeight: 1.5 }}>
            将导出为 <code>.flow</code> 文件，包含当前画布中的完整流程和元数据。
            {item.source === 'flow' && <div style={{ marginTop: 4 }}>原文件保留不变。</div>}
          </div>
          {done ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: '#22c55e', fontSize: 13 }}>
              <Check size={16} /> 导出成功
            </div>
          ) : (
            <button className="btn btn-primary" onClick={handleExport} disabled={exporting}
              style={{ alignSelf: 'flex-end', padding: '8px 20px' }}>
              {exporting ? <><Loader2 size={14} className="spin" style={{ marginRight: 4 }} /> 导出中...</> : <><Download size={14} style={{ marginRight: 4 }} /> 导出</>}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Main TemplateBrowser ──

export function TemplateBrowser({ onClose }: { onClose: () => void }) {
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('');
  const [loading, setLoading] = useState(true);
  const [fileTemplates, setFileTemplates] = useState<FileTemplate[]>([]);
  const [editingItem, setEditingItem] = useState<DisplayItem | null>(null);
  const [exportingItem, setExportingItem] = useState<DisplayItem | null>(null);
  const [importing, setImporting] = useState(false);
  const [importDone, setImportDone] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const loadTemplate = useStore(s => s.loadTemplate);
  const saveAsTemplate = useStore(s => s.saveAsTemplate);

  // Fetch file-based templates from backend
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    (async () => {
      try {
        const api = (window as any).electronAPI;
        let list: FileTemplate[] = [];
        if (api?.callFeature) {
          const r = await api.callFeature('list_templates');
          list = Array.isArray(r) ? r : r?.result || [];
        }
        if (!cancelled) {
          setFileTemplates(list as FileTemplate[]);
        }
      } catch { /* ignore */ }
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
  }, [refreshKey]);

  // Gather all display items
  const allItems = useMemo<DisplayItem[]>(() => {
    const items: DisplayItem[] = [];

    // 1. Preset templates from registry
    for (const t of templateRegistry.getAll()) {
      if (t.source === 'custom') continue; // handled separately
      items.push({
        id: `preset-${t.id}`,
        name: t.name,
        description: t.description,
        category: t.category,
        tags: t.tags,
        source: 'preset',
        node_count: t.flowScript?.nodes?.length || 0,
        raw: t,
      });
    }

    // 2. Custom templates from localStorage
    const customs = loadCustomTemplates();
    for (const ct of customs) {
      items.push({
        id: `custom-${ct.id}`,
        name: ct.name,
        description: ct.description || '',
        category: ct.script?.meta?.category || 'custom',
        tags: ct.script?.meta?.tags || [],
        source: 'custom',
        node_count: ct.script?.nodes?.length || 0,
        raw: ct,
      });
    }

    // 3. DSL/Flow templates from backend
    for (const ft of fileTemplates) {
      items.push({
        id: `file-${ft.filename}`,
        name: ft.name,
        description: ft.description,
        category: ft.category || 'custom',
        tags: ft.tags || [],
        source: ft.filetype as 'dsl' | 'flow',
        node_count: ft.node_count || 0,
        node_types: ft.node_types,
        raw: ft,
      });
    }

    return items;
  }, [fileTemplates, refreshKey]);

  // Filter by search + category
  const filtered = useMemo(() => {
    let result = allItems;
    if (category) {
      result = result.filter(i => i.category === category);
    }
    if (query.trim()) {
      const q = query.toLowerCase().trim();
      result = result.filter(i =>
        i.name.toLowerCase().includes(q) ||
        i.description.toLowerCase().includes(q) ||
        i.tags.some(t => t.toLowerCase().includes(q))
      );
    }
    return result;
  }, [allItems, query, category]);

  // Actions
  const handleLoad = useCallback((item: DisplayItem) => {
    if (item.source === 'preset') {
      const entry = item.raw as TemplateEntry;
      loadTemplate(entry.id);
    } else if (item.source === 'custom') {
      const ct = item.raw as CustomTemplate;
      const store = useStore.getState();
      store.loadScript(ct.script);
    } else {
      // DSL/flow: try to load via electronAPI
      const ft = item.raw as FileTemplate;
      (async () => {
        try {
          const api = (window as any).electronAPI;
          const r = await api?.callFeature?.('load_template', { name: ft.name });
          if (r?.dsl_or_json && r?.filetype === 'flow') {
            const script = JSON.parse(r.dsl_or_json);
            useStore.getState().loadScript(script);
          } else if (r?.dsl_or_json && r?.filetype === 'dsl') {
            const script = await api?.dslToFlow?.(r.dsl_or_json);
            if (script && !script.error) {
              useStore.getState().loadScript(script);
            }
          }
        } catch { /* ignore */ }
      })();
    }
    onClose();
  }, [loadTemplate, onClose]);

  const handleDelete = useCallback((item: DisplayItem) => {
    if (item.source !== 'custom') return;
    const ct = item.raw as CustomTemplate;
    const templates = loadCustomTemplates().filter(t => t.id !== ct.id);
    saveCustomTemplates(templates);
    setRefreshKey(k => k + 1);
  }, []);

  const handleEditSave = useCallback((name: string, description: string, tags: string[]) => {
    if (!editingItem) return;
    const ct = editingItem.raw as CustomTemplate;
    const templates = loadCustomTemplates();
    const found = templates.find(t => t.id === ct.id);
    if (found) {
      found.name = name;
      found.description = description;
      if (!found.script.meta) found.script.meta = {};
      found.script.meta.name = name;
      found.script.meta.tags = tags;
      saveCustomTemplates(templates);
    }
    setEditingItem(null);
    setRefreshKey(k => k + 1);
  }, [editingItem]);

  const handleImport = useCallback(async () => {
    setImporting(true);
    setImportDone(null);
    try {
      const api = (window as any).electronAPI;
      if (!api?.openFileDialog || !api?.readFile) {
        setImportDone('此环境不支持文件操作');
        setImporting(false);
        return;
      }
      const filepath = await api.openFileDialog([{ name: 'Flow 模板', extensions: ['flow'] }]);
      if (!filepath) {
        setImportDone('未选择文件');
        setImporting(false);
        return;
      }
      // Read the file
      const content = await api.readFile(filepath);
      if (!content) {
        setImportDone('文件读取失败');
        setImporting(false);
        return;
      }
      const script = JSON.parse(content);
      const name = script?.meta?.name || filepath.split(/[\\/]/).pop()?.replace('.flow', '') || '未命名';
      const nodeCount = script?.nodes?.length || 0;
      // Load into editor
      useStore.getState().loadScript(script);
      // Also copy to templates/ via feature call
      if (api.callFeature) {
        api.callFeature('import_template_from_file', {
          filepath, copy_to_templates: true,
        }).catch(() => {});
      }
      setImportDone(`已导入: ${name} (${nodeCount} 节点)`);
      setRefreshKey(k => k + 1);
    } catch (e: any) {
      setImportDone(`导入出错: ${e.message}`);
    }
    setImporting(false);
  }, []);

  // Category counts
  const counts = useMemo(() => {
    const m: Record<string, number> = {};
    for (const item of allItems) {
      const cat = item.category || 'custom';
      m[cat] = (m[cat] || 0) + 1;
    }
    m[''] = allItems.length;
    return m;
  }, [allItems]);

  return (
    <div style={OVERLAY} onClick={onClose}>
      <div style={DIALOG} onClick={e => e.stopPropagation()}>
        {/* ── Header ── */}
        <div style={{
          padding: '12px 16px', borderBottom: '1px solid var(--border-primary)',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          flexShrink: 0,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Grid3X3 size={16} />
            <span style={{ fontSize: 15, fontWeight: 600 }}>模板库</span>
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{allItems.length} 个模板</span>
          </div>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <button className="btn btn-ghost" onClick={handleImport} disabled={importing} style={{ padding: '4px 12px', fontSize: 12, gap: 4 }}>
              {importing ? <Loader2 size={12} className="spin" /> : <Upload size={12} />}
              导入
            </button>
            {importDone && (
              <span style={{ fontSize: 11, color: importDone.includes('失败') || importDone.includes('出错') ? '#ef4444' : '#22c55e' }}>
                {importDone}
              </span>
            )}
            <button className="btn btn-ghost btn-icon" onClick={onClose}><X size={14} /></button>
          </div>
        </div>

        <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
          {/* ── Left: Category Sidebar ── */}
          <div style={{
            width: 130, flexShrink: 0, borderRight: '1px solid var(--border-primary)',
            padding: '8px 0', overflow: 'auto',
          }}>
            {CATEGORIES.map(cat => {
              const Icon = cat.icon;
              const cnt = counts[cat.key] || 0;
              return (
                <div key={cat.key} onClick={() => setCategory(cat.key)}
                  style={{
                    padding: '7px 12px', cursor: 'pointer', fontSize: 12, display: 'flex', alignItems: 'center', gap: 6,
                    background: category === cat.key ? 'var(--accent-light)' : 'transparent',
                    color: category === cat.key ? 'var(--accent)' : 'var(--text-primary)',
                    fontWeight: category === cat.key ? 600 : 400,
                    borderRight: category === cat.key ? '2px solid var(--accent)' : '2px solid transparent',
                  }}>
                  <Icon size={13} />
                  <span>{cat.label}</span>
                  <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--text-muted)' }}>{cnt}</span>
                </div>
              );
            })}
          </div>

          {/* ── Right: Search + Grid ── */}
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
            {/* Search bar */}
            <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border-primary)' }}>
              <div style={{ position: 'relative' }}>
                <Search size={13} style={{ position: 'absolute', left: 10, top: 7, color: 'var(--text-muted)' }} />
                <input className="form-input" value={query}
                  onChange={e => setQuery(e.target.value)}
                  placeholder="搜索模板名称、描述、标签..."
                  style={{ paddingLeft: 30, fontSize: 13, width: '100%' }} />
              </div>
            </div>

            {/* Template grid */}
            <div style={{ flex: 1, overflow: 'auto', padding: 12 }}>
              {loading ? (
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, padding: 40, color: 'var(--text-muted)', fontSize: 13 }}>
                  <Loader2 size={16} className="spin" /> 加载模板列表...
                </div>
              ) : filtered.length === 0 ? (
                <div style={{ textAlign: 'center', padding: 40, color: 'var(--text-muted)', fontSize: 13, lineHeight: 1.6 }}>
                  <Search size={24} style={{ opacity: 0.3, marginBottom: 8 }} />
                  {query ? '未找到匹配模板' : '此分类暂无模板'}
                </div>
              ) : (
                <div style={{
                  display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))',
                  gap: 10,
                }}>
                  {filtered.map(item => (
                    <TemplateCard key={item.id}
                      item={item}
                      onLoad={handleLoad}
                      onExport={item.source === 'flow' ? setExportingItem : undefined}
                      onDelete={item.source === 'custom' ? handleDelete : undefined}
                      onEdit={item.source === 'custom' ? setEditingItem : undefined}
                    />
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Sub-dialogs */}
      {editingItem && (
        <EditMetadataDialog
          item={editingItem}
          onSave={handleEditSave}
          onClose={() => setEditingItem(null)}
        />
      )}
      {exportingItem && (
        <ExportDialog
          item={exportingItem}
          onClose={() => setExportingItem(null)}
        />
      )}
    </div>
  );
}
