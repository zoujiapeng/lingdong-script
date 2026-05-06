import React from 'react';
import { useStore } from '../../store';
import { PanelRightClose, PanelRightOpen, PanelBottomClose, PanelBottomOpen, PanelLeftClose, PanelLeftOpen, Minimize2 } from 'lucide-react';

export const StatusBar = React.memo(function StatusBar() {
  const nodeCount = useStore(s => s.script.nodes.length);
  const edgeCount = useStore(s => s.script.edges.length);
  const executionStatus = useStore(s => s.execution.status);
  const scale = useStore(s => s.ui.canvas_transform.scale);
  const snapToGrid = useStore(s => s.ui.snap_to_grid);
  const gridSize = useStore(s => s.ui.grid_size);
  const showNodeLibrary = useStore(s => s.ui.show_node_library);
  const showPropertyPanel = useStore(s => s.ui.show_property_panel);
  const showLogPanel = useStore(s => s.ui.show_log_panel);
  const showToolCallPanel = useStore(s => s.ui.show_tool_call_panel);
  const panelsVisible = useStore(s => s.ui.panels_visible);
  const setUI = useStore(s => s.setUI);

  const statusDot = {
    idle: 'idle',
    running: 'running',
    paused: 'paused',
    stopped: 'idle',
    completed: 'idle',
    error: 'error',
  }[executionStatus] || 'idle';

  const statusLabel = {
    idle: '就绪',
    running: '运行中',
    paused: '已暂停',
    stopped: '已停止',
    completed: '已完成',
    error: '出错',
  }[executionStatus] || '就绪';

  return (
    <div className="status-bar">
      <div className="status-item">
        <div className={`status-dot ${statusDot}`} />
        {statusLabel}
      </div>
      <div className="status-item">
        节点: {nodeCount}
      </div>
      <div className="status-item">
        连接: {edgeCount}
      </div>
      <div className="status-spacer" />
      <div className="status-item">
        缩放: {Math.round(scale * 100)}%
      </div>
      <div className="status-item">
        {snapToGrid ? '吸附: ' + gridSize + 'px' : '吸附: 关'}
      </div>

      {/* Panel toggle buttons */}
      <div className="status-bar-divider" />
      <button
        className="status-bar-btn"
        onClick={() => setUI({ show_node_library: !showNodeLibrary })}
        title={showNodeLibrary ? '隐藏左侧面板' : '显示左侧面板'}
      >
        {showNodeLibrary ? <PanelLeftClose size={13} /> : <PanelLeftOpen size={13} />}
      </button>
      <button
        className="status-bar-btn"
        onClick={() => setUI({ show_property_panel: !showPropertyPanel })}
        title={showPropertyPanel ? '隐藏右侧面板' : '显示右侧面板'}
      >
        {showPropertyPanel ? <PanelRightClose size={13} /> : <PanelRightOpen size={13} />}
      </button>
      <button
        className="status-bar-btn"
        onClick={() => setUI({ show_log_panel: !showLogPanel })}
        title={showLogPanel ? '隐藏日志面板' : '显示日志面板'}
      >
        {showLogPanel ? <PanelBottomClose size={13} /> : <PanelBottomOpen size={13} />}
      </button>
      <button
        className="status-bar-btn"
        onClick={() => setUI({ show_tool_call_panel: !showToolCallPanel })}
        title={showToolCallPanel ? '隐藏工具调用面板' : '显示工具调用面板'}
      >
        {React.createElement(
          showToolCallPanel ? PanelBottomClose : PanelBottomOpen,
          { size: 13 }
        )}
      </button>
      <div className="status-bar-divider" />
      <button
        className="status-bar-btn"
        onClick={() => setUI({ panels_visible: !panelsVisible })}
        title="切换所有面板"
      >
        <Minimize2 size={13} />
      </button>
    </div>
  );
});
