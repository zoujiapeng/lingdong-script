import React, { useState, useMemo } from 'react';
import { Search, Zap, Image, GitBranch, Monitor, Database, Settings, FileText, Clock } from 'lucide-react';
import { NodeType } from '../../types';
import { NODE_DEFINITIONS, NODE_CATEGORIES } from '../../utils/nodeDefinitions';
import { useStore } from '../../store';
import * as LucideIcons from 'lucide-react';

const CategoryIcons: Record<string, React.FC<any>> = {
  basic: Zap,
  image: Image,
  control: GitBranch,
  device: Monitor,
  data: Database,
  system: Settings,
};

function NodeIcon({ name, size = 14 }: { name: string; size?: number }) {
  const Icon = (LucideIcons as any)[name];
  if (!Icon) return <Zap size={size} />;
  return <Icon size={size} />;
}

export function NodeLibrary() {
  const [search, setSearch] = useState('');
  const [activeCategory, setActiveCategory] = useState<string>('all');
  const { addNode, ui } = useStore();

  const nodes = useMemo(() => {
    return Object.values(NODE_DEFINITIONS).filter((def) => {
      if (def.type === 'start' || def.type === 'end') return false;
      const matchSearch =
        !search ||
        def.label.includes(search) ||
        def.description.includes(search) ||
        def.type.includes(search.toLowerCase());
      const matchCat = activeCategory === 'all' || def.category === activeCategory;
      return matchSearch && matchCat;
    });
  }, [search, activeCategory]);

  const handleDragStart = (e: React.DragEvent, type: NodeType) => {
    e.dataTransfer.setData('node-type', type);
    e.dataTransfer.effectAllowed = 'copy';
  };

  const handleDoubleClick = (type: NodeType) => {
    // Add node to center of canvas
    const { canvas_transform } = ui;
    const canvasEl = document.querySelector('.flow-canvas-wrapper') as HTMLElement;
    if (!canvasEl) return;
    const rect = canvasEl.getBoundingClientRect();
    const x = (rect.width / 2 - canvas_transform.x) / canvas_transform.scale - 110;
    const y = (rect.height / 2 - canvas_transform.y) / canvas_transform.scale - 40;
    addNode(type, Math.max(0, x), Math.max(0, y));
  };

  return (
    <div className="node-library">
      {/* Header */}
      <div className="node-library-header">
        <div style={{ marginBottom: 10 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', letterSpacing: '0.1em', marginBottom: 8, textTransform: 'uppercase' }}>
            指令库
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
        {NODE_CATEGORIES.map((cat) => (
          <button
            key={cat.id}
            className={`tab-btn ${activeCategory === cat.id ? 'active' : ''}`}
            onClick={() => setActiveCategory(cat.id)}
          >
            {cat.label}
          </button>
        ))}
      </div>

      {/* Node List */}
      <div className="node-library-body">
        {nodes.length === 0 && (
          <div style={{ textAlign: 'center', padding: '32px 16px', color: 'var(--text-muted)', fontSize: 13 }}>
            未找到匹配的指令
          </div>
        )}
        {nodes.map((def) => (
          <div
            key={def.type}
            className="node-item"
            draggable
            onDragStart={(e) => handleDragStart(e, def.type)}
            onDoubleClick={() => handleDoubleClick(def.type)}
            title={`拖拽到画布 或 双击添加\n${def.description}`}
          >
            <div
              className="node-item-icon"
              style={{ background: def.color }}
            >
              <NodeIcon name={def.icon} size={15} />
            </div>
            <div className="node-item-info">
              <div className="node-item-name">{def.label}</div>
              <div className="node-item-desc">{def.description}</div>
            </div>
          </div>
        ))}
      </div>

      {/* Footer section - quick templates */}
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
            return (
              <div
                key={type}
                className="node-item"
                style={{ padding: '5px 8px' }}
                draggable
                onDragStart={(e) => handleDragStart(e, type as NodeType)}
                onDoubleClick={() => handleDoubleClick(type as NodeType)}
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
}
