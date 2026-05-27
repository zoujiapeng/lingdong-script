import React, { useCallback, useRef, useMemo } from 'react';
import * as LucideIcons from 'lucide-react';
import { Zap, AlertCircle, CheckCircle, Loader } from 'lucide-react';
import { FlowNode as FlowNodeType } from '../../types';
import { NODE_DEFINITIONS } from '../../utils/nodeDefinitions';
import { getNodeDimensions } from '../../utils/constants';
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
    case 'click_action': {
      if (p.mode === 'image') {
        const targets = p.targets || [];
        if (!targets.length) return '图片点击: 未设目标';
        const names = targets.slice(0, 2).map((t: any) => (t.path||'').split(/[\\/]/).pop()).filter(Boolean);
        return `图片点击: ${names.join(',')}${targets.length>2?'…':''} ${p.similarity}%`;
      }
      if (p.mode === 'text') return `文本点击: 「${(p.text||'').substring(0,12)}」`;
      const methodL = p.method === 'double' ? '双击' : p.method === 'right' ? '右键' : '单击';
      return `坐标(${p.x},${p.y}) ${methodL}`;
    }
    case 'mouse_operation': {
      const sub = p.sub_operation || 'click';
      if (sub === 'scroll') return `滚轮: ${p.scroll_direction || 'down'} ${p.scroll_amount || 3}格`;
      if (sub === 'hover') return `悬停 (${p.hover_x},${p.hover_y}) ${p.hover_duration || 0.5}s`;
      if (sub === 'move') return `移动至 (${p.move_x},${p.move_y})`;
      // click
      if (p.mode === 'image') {
        const targets = p.targets || [];
        if (!targets.length) return '点击: 未设目标';
        return `点击图片: ${targets.map((t: any)=>t.path||'').join(',')}`;
      }
      if (p.mode === 'text') return `点击文字: 「${(p.text||'').substring(0,12)}」`;
      const mLabel: Record<string,string> = {single:'单击',double:'双击',right:'右击',middle:'中键',triple:'三击'};
      return `点击 (${p.x},${p.y}) ${mLabel[p.method] || p.method}`;
    }
    case 'screenshot':
      return `保存截图`;
    case 'camera_capture':
      return `摄像头抓拍 (摄像头 ${p.camera_id ?? 0})`;
    case 'camera_record':
      return `摄像头录像 (摄像头 ${p.camera_id ?? 0}, ${p.duration ? p.duration + '秒' : '手动停止'})`;
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
    case 'lock_workstation':
      return '锁定工作站';
    case 'volume_set':
      return `音量: ${p.level ?? 50}%`;
    case 'volume_mute':
      return p.mute === null ? '切换静音' : p.mute ? '静音' : '取消静音';
    case 'process_action': {
      const labels: Record<string, string> = { launch:'启动', kill:'终止', wait_start:'等待启动', wait_exit:'等待退出', focus:'激活窗口' };
      const a = p.action || 'launch';
      if (a === 'launch') return `启动: ${p.target || '未设置'}`;
      if (a === 'kill') return `终止: ${p.name || '未设置'}${p.force ? '(强制)' : ''}`;
      if (a === 'focus') return `激活窗口: ${p.title || '未设置'}`;
      if (a === 'wait_start') return `等待启动: ${p.name || '未设置'} (超时${p.timeout??30}s)`;
      if (a === 'wait_exit') return `等待退出: ${p.name || '未设置'} (超时${p.timeout??30}s)`;
      return `${labels[a]||a}: ${p.name||p.target||''}`;
    }
    case 'launch_program':
      return `启动: ${p.target || '未设置'}`;
    case 'kill_process':
      return `终止: ${p.name || '未设置'}${p.force ? ' (强制)' : ''}`;
    case 'wait_process':
      return `等待${p.wait_for === 'exit' ? '退出' : '启动'}: ${p.name || '未设置'} (超时${p.timeout ?? 30}s)`;
    case 'focus_window':
      return `激活窗口: ${p.title || '未设置'}`;
    case 'set_wallpaper':
      return `壁纸: ${p.path || '未设置'}`;
    case 'display_off':
      return '关闭显示器';
    case 'system_sleep':
      return '睡眠';
    case 'show_desktop':
      return '显示桌面';
    case 'battery_status':
      return '获取电池状态';
    case 'empty_recycle_bin':
      return '清空回收站';
    case 'display_brightness':
      return `亮度: ${p.level ?? 80}%`;
    case 'clipboard_set':
      return `剪贴板写入: "${(p.text || '').substring(0, 20)}${p.text?.length > 20 ? '…' : ''}"`;
    case 'clipboard_get':
      return '读取剪贴板文本';
    case 'window_control': {
      const opLabels: Record<string, string> = { minimize: '最小化', maximize: '最大化', close: '关闭', resize: '调整大小', restore: '还原', snap: '贴靠' };
      const op = p.operation || 'minimize';
      if (op === 'snap') return `窗口贴靠${p.snap_position||'左'}: ${p.title||'当前窗口'}`;
      return `窗口${opLabels[op] || op}${p.title ? ': ' + p.title : ''}`;
    }
    case 'system_power': {
      const al: Record<string, string> = { shutdown:'关机', reboot:'重启', logoff:'注销', hibernate:'休眠', lock_workstation:'锁定', system_sleep:'睡眠', display_off:'关闭显示器' };
      return `系统电源: ${al[p.action] || p.action}${p.delay_seconds ? ' (延迟'+p.delay_seconds+'s)' : ''}`;
    }
    case 'audio_action':
      return p.action === 'volume_mute'
        ? (p.mute === null ? '切换静音' : p.mute ? '静音' : '取消静音')
        : `音量: ${p.level ?? 50}%`;
    case 'desktop_action': {
      const al: Record<string, string> = { show_desktop:'显示桌面', set_wallpaper:'设置壁纸', empty_recycle_bin:'清空回收站' };
      return p.action === 'set_wallpaper' ? `${al[p.action]||'壁纸'}: ${p.path||'未设置'}` : (al[p.action] || p.action);
    }
    case 'clipboard_action': {
      const ca: Record<string, string> = { set:'剪贴板写入', get:'读取剪贴板文本', get_image:'获取剪贴板图片', get_files:'获取剪贴板文件', clear:'清空剪贴板' };
      const sub = p.action || 'get';
      if (sub === 'set') return `剪贴板写入: "${(p.text||'').substring(0,20)}${p.text?.length>20?'…':''}"`;
      if (sub === 'get_image') return `获取剪贴板图片${p.format_==='file'?' → '+p.save_path:''}`;
      return ca[sub] || '剪贴板操作';
    }
    case 'dialog_action': {
      const da: Record<string, string> = { handle:'处理对话框', list:'列出对话框', take_action:'操作对话框', fill_save:'填写保存对话框' };
      const sub = da[p.action] || p.action || '处理对话框';
      return p.action === 'fill_save' ? `${sub}: ${p.path||''}` : sub;
    }
    case 'a11y_action': {
      const aa: Record<string, string> = { get_tree:'获取无障碍树', find_element:'查找元素', click_element:'点击元素' };
      return p.action === 'get_tree' ? `无障碍树 (深度${p.max_depth||5})` : `${aa[p.action]||p.action}: ${p.name||''}`;
    }
    case 'wifi_action': {
      const wa: Record<string, string> = { list:'列出网络', connect:'连接WiFi', disconnect:'断开WiFi' };
      return p.action === 'connect' ? `连接: ${p.ssid||''}` : (wa[p.action] || 'WiFi操作');
    }
    case 'web_browser': {
      const actionLabels: Record<string, string> = { navigate: '导航', click: '点击', input_text: '输入', screenshot: '截图', wait: '等待', extract: '提取', scroll: '滚动' };
      const action = actionLabels[(p as any).action] || p.action || '未知';
      const target = p.action === 'navigate' ? ((p as any).url || '').substring(0, 30) : (p as any).selector || '';
      return `🌐 ${action}: ${target}`;
    }
    case 'tool_call': {
      return p.feature_name ? `${p.feature_name}` : '(调用工具)';
    }
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

export const FlowNode = React.memo(function FlowNode({ node, onPortMouseDown, onPortMouseUp }: Props) {
  const isSelected = useStore(s => s.ui.selection.nodes.includes(node.id));
  const isBreakpoint = useStore(s => s.execution.breakpoints.includes(node.id));
  const connectingActive = useStore(s => s.ui.connecting.active);
  const selectNodes = useStore(s => s.selectNodes);
  const moveNode = useStore(s => s.moveNode);
  const toggleBreakpoint = useStore(s => s.toggleBreakpoint);
  const def = NODE_DEFINITIONS[node.type];
  const dragStartRef = useRef<{ nx: number; ny: number; mx: number; my: number } | null>(null);
  const hasDragged = useRef(false);

  // Stable refs for callbacks — avoids recreating handleMouseDown on every node/connectingActive change
  const nodeRef = useRef(node);
  nodeRef.current = node;
  const connectingActiveRef = useRef(connectingActive);
  connectingActiveRef.current = connectingActive;

  const dims = getNodeDimensions(node.type);
  const nodeW = dims.w;
  const nodeH = dims.h;

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (e.button !== 0) return;
      e.stopPropagation();

      if (connectingActiveRef.current) return;

      const n = nodeRef.current;
      if (!isSelected) {
        selectNodes([n.id], e.shiftKey || e.ctrlKey || e.metaKey);
      }

      dragStartRef.current = { nx: n.x, ny: n.y, mx: e.clientX, my: e.clientY };
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
          if (selectedIds.length > 1 && selectedIds.includes(n.id)) {
            selectedIds.forEach((id) => {
              const target = state.script.nodes.find((nd) => nd.id === id);
              if (target) {
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
            moveNode(n.id, newX, newY);
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
    [isSelected, selectNodes, moveNode]
  );

  const handleClick = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      if (!hasDragged.current) {
        selectNodes([nodeRef.current.id], e.shiftKey || e.ctrlKey || e.metaKey);
      }
    },
    [selectNodes]
  );

  // Memoized ports — avoids re-creating DOM on every render
  const portsEl = useMemo(() => {
    if (node.type === 'start' || node.type === 'end') return null;
    const ports = def.ports;
    return (
      <>
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
  }, [node.type, node.id, def, onPortMouseDown, onPortMouseUp]);

  const statusColor = STATUS_COLORS[node.status || 'idle'];
  const summary = getNodeSummary(node);

  // Special render for start/end
  if (node.type === 'start' || node.type === 'end') {
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
        <div className="flow-node-header" onContextMenu={(e) => { e.preventDefault(); toggleBreakpoint(node.id); }}>
          {/* Breakpoint indicator */}
          {isBreakpoint && <div style={{ position: 'absolute', left: -6, top: -6, width: 14, height: 14, borderRadius: '50%', background: '#ef4444', border: '2px solid var(--bg-primary)', zIndex: 10, boxShadow: '0 0 6px rgba(239,68,68,0.6)' }} title="断点" />}
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
            {node.type === 'sub_script' && (node.params as any).script_id && (
              <span
                className="sub-script-zoom-btn"
                onClick={(e) => {
                  e.stopPropagation();
                  const sid = (node.params as any).script_id;
                  useStore.getState().navigateToSubFlow(sid, node.id);
                }}
                title="进入子流程"
              >
                ▶ 进入
              </span>
            )}
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
      {portsEl}
    </div>
  );
});
