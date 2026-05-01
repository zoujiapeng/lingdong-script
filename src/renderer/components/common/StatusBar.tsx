import React from 'react';
import { useStore } from '../../store';

export function StatusBar() {
  const { script, execution, ui } = useStore();

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
      <div className="status-item" style={{ marginLeft: 'auto' }}>
        缩放: {Math.round(ui.canvas_transform.scale * 100)}%
      </div>
      <div className="status-item">
        {ui.snap_to_grid ? '吸附: ' + ui.grid_size + 'px' : '吸附: 关'}
      </div>
    </div>
  );
}
