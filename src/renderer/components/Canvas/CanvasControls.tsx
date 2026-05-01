import React, { useCallback } from 'react';
import {
  ZoomIn, ZoomOut, Maximize2, LayoutGrid, Grid, Magnet,
  Copy, Clipboard, Scissors, Trash2, Lock, Unlock
} from 'lucide-react';
import { useStore } from '../../store';

// ============================================================
// CANVAS CONTROLS
// ============================================================
export function CanvasControls() {
  const { ui, setUI, zoomIn, zoomOut, resetZoom, fitToScreen } = useStore();
  const { canvas_transform, show_grid, snap_to_grid } = ui;

  return (
    <div className="canvas-controls">
      <div className="canvas-controls-group">
        <button className="canvas-btn" onClick={zoomIn} title="放大">
          <ZoomIn size={14} />
        </button>
        <div className="canvas-zoom-label">{Math.round(canvas_transform.scale * 100)}%</div>
        <button className="canvas-btn" onClick={zoomOut} title="缩小">
          <ZoomOut size={14} />
        </button>
      </div>

      <div className="canvas-controls-group">
        <button className="canvas-btn" onClick={fitToScreen} title="适应屏幕">
          <Maximize2 size={14} />
        </button>
        <button
          className="canvas-btn"
          onClick={() => setUI({ show_grid: !show_grid })}
          title="显示/隐藏网格"
          style={{ color: show_grid ? 'var(--accent)' : undefined }}
        >
          <Grid size={14} />
        </button>
        <button
          className="canvas-btn"
          onClick={() => setUI({ snap_to_grid: !snap_to_grid })}
          title="网格吸附"
          style={{ color: snap_to_grid ? 'var(--accent)' : undefined }}
        >
          <Magnet size={14} />
        </button>
      </div>
    </div>
  );
}

// ============================================================
// MINI MAP
// ============================================================
export function MiniMap() {
  const { script, ui, setCanvasTransform } = useStore();
  const { nodes } = script;
  const { canvas_transform } = ui;

  if (nodes.length === 0) return null;

  const MINIMAP_W = 160;
  const MINIMAP_H = 100;

  // Compute bounding box of all nodes
  const minX = Math.min(...nodes.map((n) => n.x));
  const minY = Math.min(...nodes.map((n) => n.y));
  const maxX = Math.max(...nodes.map((n) => n.x + 220));
  const maxY = Math.max(...nodes.map((n) => n.y + 80));
  const bW = maxX - minX || 1;
  const bH = maxY - minY || 1;

  const scaleX = MINIMAP_W / bW;
  const scaleY = MINIMAP_H / bH;
  const scale = Math.min(scaleX, scaleY) * 0.85;

  const offsetX = (MINIMAP_W - bW * scale) / 2;
  const offsetY = (MINIMAP_H - bH * scale) / 2;

  const toMini = (x: number, y: number) => ({
    x: (x - minX) * scale + offsetX,
    y: (y - minY) * scale + offsetY,
  });

  const COLORS: Record<string, string> = {
    start: '#6366f1', end: '#ef4444', click: '#3b82f6',
    image_click: '#8b5cf6', wait: '#0ea5e9', input_text: '#10b981',
    keyboard: '#f59e0b', swipe: '#06b6d4', condition: '#f97316',
    loop: '#22c55e', sub_script: '#a855f7', default: '#94a3b8',
  };

  return (
    <div className="minimap">
      <svg width={MINIMAP_W} height={MINIMAP_H}>
        {nodes.map((n) => {
          const isStartEnd = n.type === 'start' || n.type === 'end';
          const nw = (isStartEnd ? 160 : 220) * scale;
          const nh = (isStartEnd ? 56 : 80) * scale;
          const pos = toMini(n.x, n.y);
          const color = COLORS[n.type] || COLORS.default;
          return (
            <rect
              key={n.id}
              x={pos.x}
              y={pos.y}
              width={Math.max(nw, 4)}
              height={Math.max(nh, 3)}
              rx={isStartEnd ? 8 : 3}
              fill={color}
              opacity={0.7}
            />
          );
        })}
        {/* Viewport indicator */}
        <rect
          x={0}
          y={0}
          width={MINIMAP_W}
          height={MINIMAP_H}
          fill="none"
          stroke="rgba(99,102,241,0.4)"
          strokeWidth="1"
          rx="4"
        />
      </svg>
    </div>
  );
}

// ============================================================
// CONTEXT MENU
// ============================================================
interface ContextMenuProps {
  x: number;
  y: number;
  canvasX: number;
  canvasY: number;
  onClose: () => void;
}

export function ContextMenu({ x, y, canvasX, canvasY, onClose }: ContextMenuProps) {
  const {
    ui, script, addNode,
    copySelected, cutSelected, paste,
    deleteNodes, duplicateNodes,
    selectAll, clearSelection,
    autoLayout,
  } = useStore();

  const hasSelection = ui.selection.nodes.length > 0;
  const hasClipboard = useStore.getState().clipboard !== null;

  const run = useCallback(
    (fn: () => void) => {
      fn();
      onClose();
    },
    [onClose]
  );

  // Adjust position to stay in viewport
  const adjustedX = Math.min(x, window.innerWidth - 200);
  const adjustedY = Math.min(y, window.innerHeight - 300);

  return (
    <div
      className="context-menu scale-in"
      style={{ left: adjustedX, top: adjustedY }}
      onClick={(e) => e.stopPropagation()}
    >
      {hasSelection ? (
        <>
          <button className="context-menu-item" onClick={() => run(copySelected)}>
            <Copy size={13} /> 复制
            <span className="context-menu-shortcut">Ctrl+C</span>
          </button>
          <button className="context-menu-item" onClick={() => run(cutSelected)}>
            <Scissors size={13} /> 剪切
            <span className="context-menu-shortcut">Ctrl+X</span>
          </button>
          <button
            className="context-menu-item"
            onClick={() => run(() => duplicateNodes(ui.selection.nodes))}
          >
            <Copy size={13} /> 复制节点
            <span className="context-menu-shortcut">Ctrl+D</span>
          </button>
          <div className="context-menu-divider" />
          <button
            className="context-menu-item danger"
            onClick={() => run(() => deleteNodes(
              ui.selection.nodes.filter(id => {
                const n = script.nodes.find(n => n.id === id);
                return n && n.type !== 'start' && n.type !== 'end';
              })
            ))}
          >
            <Trash2 size={13} /> 删除
            <span className="context-menu-shortcut">Del</span>
          </button>
        </>
      ) : (
        <>
          {hasClipboard && (
            <button className="context-menu-item" onClick={() => run(paste)}>
              <Clipboard size={13} /> 粘贴
              <span className="context-menu-shortcut">Ctrl+V</span>
            </button>
          )}
          <button className="context-menu-item" onClick={() => run(selectAll)}>
            <LayoutGrid size={13} /> 全选
            <span className="context-menu-shortcut">Ctrl+A</span>
          </button>
          <div className="context-menu-divider" />
          <div style={{ padding: '4px 8px', fontSize: 11, color: 'var(--text-muted)', fontWeight: 600 }}>添加节点</div>
          {['click', 'image_click', 'wait', 'condition', 'loop'].map((type) => (
            <button
              key={type}
              className="context-menu-item"
              onClick={() => run(() => addNode(type as any, canvasX, canvasY))}
            >
              + {{ click: '点击', image_click: '图像点击', wait: '等待', condition: '条件判断', loop: '循环' }[type]}
            </button>
          ))}
          <div className="context-menu-divider" />
          <button className="context-menu-item" onClick={() => run(autoLayout)}>
            <LayoutGrid size={13} /> 自动布局
          </button>
        </>
      )}
    </div>
  );
}
