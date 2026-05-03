import React from 'react';
import { useStore } from '../../store';
import { PanelRightClose, PanelRightOpen, PanelBottomClose, PanelBottomOpen, PanelLeftClose, PanelLeftOpen, Minimize2 } from 'lucide-react';

interface StatusBarProps {
  onHidePanels?: () => void;
}

export function StatusBar({ onHidePanels }: StatusBarProps) {
  const { script, execution, ui, setUI } = useStore();

  const statusDot = {
    idle: 'idle',
    running: 'running',
    paused: 'paused',
    stopped: 'idle',
    completed: 'idle',
    error: 'error',
  }[execution.status] || 'idle';

  const statusLabel = {
    idle: '就绪',
    running: '运行中',
    paused: '已暂停',
    stopped: '已停止',
    completed: '已完成',
    error: '出错',
  }[execution.status] || '就绪';

  return (
    <div className="status-bar">
      <div className="status-item">
        <div className={`status-dot ${statusDot}`} />
        {statusLabel}
      </div>
      <div className="status-item">
        节点: {script.nodes.length}
      </div>
      <div className="status-item">
        连接: {script.edges.length}
      </div>
      <div className="status-spacer" />
      <div className="status-item">
        缩放: {Math.round(ui.canvas_transform.scale * 100)}%
      </div>
      <div className="status-item">
        {ui.snap_to_grid ? '吸附: ' + ui.grid_size + 'px' : '吸附: 关'}
      </div>

      {/* Panel toggle buttons */}
      <div className="status-bar-divider" />
      <button
        className="status-bar-btn"
        onClick={() => setUI({ show_node_library: !ui.show_node_library })}
        title={ui.show_node_library ? '隐藏左侧面板' : '显示左侧面板'}
      >
        {ui.show_node_library ? <PanelLeftClose size={13} /> : <PanelLeftOpen size={13} />}
      </button>
      <button
        className="status-bar-btn"
        onClick={() => setUI({ show_property_panel: !ui.show_property_panel })}
        title={ui.show_property_panel ? '隐藏右侧面板' : '显示右侧面板'}
      >
        {ui.show_property_panel ? <PanelRightClose size={13} /> : <PanelRightOpen size={13} />}
      </button>
      <button
        className="status-bar-btn"
        onClick={() => setUI({ show_log_panel: !ui.show_log_panel })}
        title={ui.show_log_panel ? '隐藏日志面板' : '显示日志面板'}
      >
        {ui.show_log_panel ? <PanelBottomClose size={13} /> : <PanelBottomOpen size={13} />}
      </button>
      <div className="status-bar-divider" />
      <button
        className="status-bar-btn"
        onClick={onHidePanels}
        title="收起所有面板"
      >
        <Minimize2 size={13} />
      </button>
    </div>
  );
}
