import React, { useState, useMemo } from 'react';
import { Search, Zap, Image, GitBranch, Monitor, Database, Settings, Wrench } from 'lucide-react';
import { NodeType } from '../../types';
import { NODE_DEFINITIONS, NODE_CATEGORIES } from '../../utils/nodeDefinitions';
import { useStore } from '../../store';
import { getLibraryFeatures, FeatureNodeItem, getFeatureCategoryLabel } from '../../utils/featureRegistry';
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
  const addNode = useStore(s => s.addNode);
  const updateNodeParams = useStore(s => s.updateNodeParams);
  const featureList = useStore(s => s.featureList);
  const showNodeLibrary = useStore(s => s.ui.show_node_library);
  const panelsVisible = useStore(s => s.ui.panels_visible);
  const canvas_transform = useStore(s => s.ui.canvas_transform);
  const forcedVisible = showNodeLibrary || panelsVisible;

  // Build merged list: native nodes + feature-derived items
  const allItems = useMemo(() => {
    const items: Array<{ kind: 'node'; def: (typeof NODE_DEFINITIONS)[NodeType] } | { kind: 'feature'; item: FeatureNodeItem }> = [];

    // Native node definitions (exclude start/end from library)
    for (const def of Object.values(NODE_DEFINITIONS)) {
      if (def.type === 'start' || def.type === 'end') continue;
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
  }, [allItems, search, activeCategory]);

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

          return (
            <div
              key={key}
              className="node-item"
              draggable
              onDragStart={(e) => handleDragStart(e, item)}
              onDoubleClick={() => handleDoubleClick(item)}
              title={`拖拽到画布 或 双击添加\n${desc}`}
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
          {['image_click', 'wait', 'condition'].map((type) => {
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
    </div>
  );
});
