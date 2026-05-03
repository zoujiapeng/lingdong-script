import React, { useCallback, useRef } from 'react';
import * as LucideIcons from 'lucide-react';
import { Zap, AlertCircle, CheckCircle, Loader } from 'lucide-react';
import { FlowNode as FlowNodeType } from '../../types';
import { NODE_DEFINITIONS } from '../../utils/nodeDefinitions';
import { useStore } from '../../store';

function NodeIcon({ name, size = 14 }: { name: string; size?: number }) {
  const Icon = (LucideIcons as any)[name];
  if (!Icon) return <Zap size={size} />;
  return <Icon size={size} />;
}

function getNodeSummary(node: FlowNodeType): string {
  const p = node.params as any;
  switch (node.type) {
    case 'click': return `坐标: (${p.x}, ${p.y})  ${p.method === 'double' ? '双击' : p.method === 'right' ? '右键' : '单击'}`;
    case 'image_click': {
      const targets = (p as any).targets || [];
      if (targets.length === 0) return '未设置目标图片';
      const names = targets.slice(0, 3).map((t: any) => t.path.split(/[\\/]/).pop()).filter(Boolean);
      const suffix = targets.length > 3 ? `…(+${targets.length - 3})` : '';
      return `目标(${targets.length}): ${names.join(', ')}${suffix}  匹配度: ${p.similarity}%`;
    }
    case 'wait': return p.wait_type === 'time' ? `等待 ${p.duration} 秒` : p.wait_type === 'image' ? `等待出现图片` : `等待窗口`;
    case 'input_text': return p.text ? `"${p.text.substring(0, 20)}${p.text.length > 20 ? '…' : ''}"` : '未设置文本';
    case 'keyboard': return `按键: ${[...p.modifiers, ...p.keys].join(' + ')}`;
    case 'swipe': return `(${p.start_x},${p.start_y}) → (${p.end_x},${p.end_y})`;
    case 'condition': return `判断: ${conditionLabel(p.condition_type)}`;
    case 'loop': return p.loop_type === 'count' ? `循环 ${p.count} 次` : p.loop_type === 'infinite' ? '无限循环' : '条件循环';
    case 'sub_script': return `脚本: ${p.script_name || '未选择'}`;
    case 'click_text': {
      const txt = p.text || '未设置';
      const methodLabel = p.method === 'double' ? '双击' : p.method === 'right' ? '右键' : '单击';
      return `OCR「${txt.substring(0, 16)}${txt.length > 16 ? '…' : ''}」 ${methodLabel}`;
    }
    case 'screenshot':
      return `保存截图`;
    case 'popup_close': {
      const s = (p.strategies || ['auto'])[0];
      const strategyLabel: Record<string, string> = { auto: '自动', esc: 'Esc', close_btn: '关闭按钮', click_away: '空白区域', ocr_close: 'OCR查找', all: '全部尝试' };
      return `弹窗关闭 (${strategyLabel[s] || s})`;
    }
    case 'color_check':
      return `颜色检测 (${p.x},${p.y},${p.w}x${p.h}) ${p.expected_color || '未设'} 容差=${p.tolerance}`;
    case 'progress_wait': {
      const typeLabel: Record<string, string> = { fill: '填满', empty: '清空', change: '变化' };
      const r = p.region || {};
      return `等待进度条${typeLabel[p.wait_type] || p.wait_type} (${r.x||0},${r.y||0},${r.w||200}x${r.h||20})`;
    }
    case 'region_wait': {
      const rLabel = p.region_type === 'stable' ? '稳定' : '变化';
      return `等待区域${rLabel} (${p.x},${p.y},${p.w}x${p.h})`;
    }
    case 'scene_condition':
      return `场景识别: 「${p.scene_name || '未设置'}」 (相似度≥${p.similarity}%)`;
    case 'variable_get':
      return `读取变量: ${p.name || '未设置'}`;
    default: return `(${node.type})`;
  }
}

function conditionLabel(type: string): string {
  const m: Record<string, string> = {
    image_exists: '图片存在', window_exists: '窗口存在',
    variable_equals: '变量等于', variable_greater: '变量大于',
    variable_less: '变量小于', always_true: '始终为真',
  };
  return m[type] || type;
}

const STATUS_COLORS = {
  idle: 'transparent',
  running: '#3b82f6',
  success: '#22c55e',
  error: '#ef4444',
  skipped: '#94a3b8',
};

interface Props {
  node: FlowNodeType;
  onPortMouseDown: (e: React.MouseEvent, nodeId: string, portId: string) => void;
  onPortMouseUp: (e: React.MouseEvent, nodeId: string, portId: string) => void;
}

export function FlowNode({ node, onPortMouseDown, onPortMouseUp }: Props) {
  const { ui, selectNodes, moveNode, moveNodes, updateNode, script } = useStore();
  const { selection, connecting } = ui;
  const isSelected = selection.nodes.includes(node.id);
  const def = NODE_DEFINITIONS[node.type];
  const dragStartRef = useRef<{ nx: number; ny: number; mx: number; my: number } | null>(null);
  const hasDragged = useRef(false);

  const isStartEnd = node.type === 'start' || node.type === 'end';
  const nodeW = isStartEnd ? 160 : 220;
  const nodeH = isStartEnd ? 56 : 80;

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (e.button !== 0) return;
      e.stopPropagation();

      if (connecting.active) return;

      if (!isSelected) {
        selectNodes([node.id], e.shiftKey || e.ctrlKey || e.metaKey);
      }

      dragStartRef.current = { nx: node.x, ny: node.y, mx: e.clientX, my: e.clientY };
      hasDragged.current = false;

      const { canvas_transform } = useStore.getState().ui;

      const onMove = (ev: MouseEvent) => {
        if (!dragStartRef.current) return;
        const dx = (ev.clientX - dragStartRef.current.mx) / canvas_transform.scale;
        const dy = (ev.clientY - dragStartRef.current.my) / canvas_transform.scale;
        if (Math.abs(dx) > 2 || Math.abs(dy) > 2) {
          hasDragged.current = true;
          const state = useStore.getState();
          const selectedIds = state.ui.selection.nodes;
          if (selectedIds.length > 1 && selectedIds.includes(node.id)) {
            // Move all selected
            selectedIds.forEach((id) => {
              const n = state.script.nodes.find((n) => n.id === id);
              if (n) {
                const snap = state.ui.snap_to_grid;
                const gs = state.ui.grid_size;
                const nx = dragStartRef.current!.nx + dx;
                const ny = dragStartRef.current!.ny + dy;
                state.moveNode(id, snap ? Math.round(nx / gs) * gs : nx, snap ? Math.round(ny / gs) * gs : ny);
              }
            });
          } else {
            const newX = dragStartRef.current.nx + dx;
            const newY = dragStartRef.current.ny + dy;
            moveNode(node.id, newX, newY);
          }
        }
      };

      const onUp = () => {
        dragStartRef.current = null;
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
      };

      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    },
    [node, isSelected, selectNodes, moveNode, connecting]
  );

  const handleClick = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      if (!hasDragged.current) {
        selectNodes([node.id], e.shiftKey || e.ctrlKey || e.metaKey);
      }
    },
    [node.id, selectNodes]
  );

  // Render ports
  const renderPorts = () => {
    if (isStartEnd) return null;
    const ports = def.ports;
    return (
      <>
        {/* Input port */}
        {ports.inputs.map((port) => (
          <div
            key={port.id}
            className="port input-port"
            style={{ left: '50%', top: -6, transform: 'translateX(-50%)' }}
            onMouseDown={(e) => { e.stopPropagation(); onPortMouseDown(e, node.id, port.id); }}
            onMouseUp={(e) => { e.stopPropagation(); onPortMouseUp(e, node.id, port.id); }}
            title="输入端口"
          />
        ))}

        {/* Output ports */}
        {ports.outputs.map((port, i) => {
          const total = ports.outputs.length;
          const leftPct = total === 1 ? 50 : total === 2 ? (i === 0 ? 28 : 72) : 20 + i * 30;
          return (
            <div key={port.id} style={{ position: 'absolute', bottom: -18, left: `${leftPct}%`, transform: 'translateX(-50%)', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
              <div
                className="port output-port"
                style={{ position: 'static', transform: 'none' }}
                onMouseDown={(e) => { e.stopPropagation(); onPortMouseDown(e, node.id, port.id); }}
                onMouseUp={(e) => { e.stopPropagation(); onPortMouseUp(e, node.id, port.id); }}
                title={`输出端口: ${port.label || port.id}`}
              />
              {port.label && (
                <span style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 4, whiteSpace: 'nowrap', pointerEvents: 'none' }}>
                  {port.label}
                </span>
              )}
            </div>
          );
        })}
      </>
    );
  };

  const statusColor = STATUS_COLORS[node.status || 'idle'];
  const summary = getNodeSummary(node);

  // Special render for start/end
  if (isStartEnd) {
    const isStart = node.type === 'start';
    return (
      <div
        style={{ position: 'absolute', left: node.x, top: node.y, width: nodeW, height: nodeH }}
        onMouseDown={handleMouseDown}
        onClick={handleClick}
      >
        <div
          className={`flow-node start-node ${isSelected ? 'selected' : ''} ${node.status || ''}`}
          style={{
            width: nodeW, height: nodeH,
            background: isStart ? 'linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%)' : 'linear-gradient(135deg, #ef4444 0%, #dc2626 100%)',
            border: `1.5px solid ${isSelected ? 'white' : isStart ? 'rgba(99,102,241,0.4)' : 'rgba(239,68,68,0.4)'}`,
            color: 'white', fontWeight: 700, fontSize: 14,
            boxShadow: isSelected
              ? `0 0 0 2px ${isStart ? '#6366f1' : '#ef4444'}, var(--shadow-lg)`
              : `var(--shadow-md), ${isStart ? '0 4px 20px rgba(99,102,241,0.3)' : '0 4px 20px rgba(239,68,68,0.3)'}`,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{
              width: 24, height: 24, borderRadius: '50%',
              background: 'rgba(255,255,255,0.25)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <NodeIcon name={def.icon} size={12} />
            </div>
            {def.label}
          </div>
        </div>

        {/* Start has output port, end has input port */}
        {isStart ? (
          <div
            className="port"
            style={{
              position: 'absolute', bottom: -6, left: '50%', transform: 'translateX(-50%)',
              background: 'white', borderColor: '#6366f1',
            }}
            onMouseDown={(e) => { e.stopPropagation(); onPortMouseDown(e, node.id, 'output'); }}
            onMouseUp={(e) => { e.stopPropagation(); onPortMouseUp(e, node.id, 'output'); }}
          />
        ) : (
          <div
            className="port"
            style={{
              position: 'absolute', top: -6, left: '50%', transform: 'translateX(-50%)',
              background: 'white', borderColor: '#ef4444',
            }}
            onMouseDown={(e) => { e.stopPropagation(); onPortMouseDown(e, node.id, 'input'); }}
            onMouseUp={(e) => { e.stopPropagation(); onPortMouseUp(e, node.id, 'input'); }}
          />
        )}
      </div>
    );
  }

  return (
    <div
      style={{ position: 'absolute', left: node.x, top: node.y, width: nodeW }}
      onMouseDown={handleMouseDown}
      onClick={handleClick}
    >
      <div
        className={`flow-node ${isSelected ? 'selected' : ''} ${node.status || 'idle'} ${node.disabled ? 'disabled' : ''}`}
        style={{ width: nodeW }}
      >
        {/* Header */}
        <div className="flow-node-header">
          <div className="flow-node-icon" style={{ background: def.color }}>
            <NodeIcon name={def.icon} size={14} />
          </div>
          <div className="flow-node-title">
            <div className="flow-node-type">{def.label}</div>
            <div className="flow-node-name">{node.label || def.label}</div>
          </div>
          {/* Status icon */}
          {node.status === 'running' && (
            <div style={{ color: '#3b82f6', animation: 'spin 1s linear infinite' }}>
              <Loader size={14} />
            </div>
          )}
          {node.status === 'success' && <CheckCircle size={14} color="#22c55e" />}
          {node.status === 'error' && <span title={node.error_msg}><AlertCircle size={14} color="#ef4444" /></span>}
        </div>

        {/* Body */}
        {summary && (
          <div className="flow-node-body">
            <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>{summary}</span>
          </div>
        )}

        {/* Error message */}
        {node.status === 'error' && node.error_msg && (
          <div style={{
            padding: '4px 10px 6px',
            fontSize: 11,
            color: '#ef4444',
            background: 'rgba(239,68,68,0.06)',
          }}>
            {node.error_msg}
          </div>
        )}

        {/* Loop iteration counter (shown during execution) */}
        {node.type === 'loop' && (() => {
          const li = useStore.getState().execution.loop_iteration;
          if (!li || li.node_id !== node.id) return null;
          const remaining = li.total > 0 ? li.total - li.current : 0;
          return (
            <div style={{
              padding: '4px 10px 6px',
              fontSize: 12,
              color: '#3b82f6',
              background: 'rgba(59,130,246,0.08)',
              display: 'flex',
              alignItems: 'center',
              gap: 6,
            }}>
              <span style={{ fontWeight: 600 }}>第 {li.current}/{li.total} 次</span>
              <span style={{ opacity: 0.6 }}>剩余 {remaining} 次</span>
            </div>
          );
        })()}

        {/* Status bar */}
        <div className="flow-node-status-bar" style={{ background: statusColor }} />
      </div>

      {/* Ports */}
      {renderPorts()}

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}
