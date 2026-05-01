import React, { useEffect, useRef } from 'react';
import { useStore } from '../../store';
import { Trash2 } from 'lucide-react';

export function LogPanel() {
  const { execution, addLog, clearLog } = useStore();
  const logEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [execution.log.length]);

  const levelClass = (level: string) => {
    switch (level) {
      case 'info': return 'info';
      case 'warn': return 'warn';
      case 'error': return 'error';
      case 'debug': return 'debug';
      default: return 'info';
    }
  };

  const formatTime = (ts: string) => {
    const d = new Date(ts);
    return d.toLocaleTimeString('zh-CN', { hour12: false });
  };

  return (
    <div className="log-panel">
      <div className="log-panel-header">
        <span className="log-panel-title">执行日志</span>
        {execution.log.length > 0 && (
          <button
            onClick={clearLog}
            style={{
              background: 'none', border: 'none', cursor: 'pointer',
              color: 'rgba(148,163,184,0.6)', padding: 2, display: 'flex',
            }}
            title="清空日志"
          >
            <Trash2 size={12} />
          </button>
        )}
        <span style={{ fontSize: 10, color: 'rgba(148,163,184,0.4)' }}>
          {execution.log.length} 条
        </span>
      </div>
      <div className="log-entries">
        {execution.log.length === 0 && (
          <div style={{ color: 'rgba(148,163,184,0.3)', fontSize: 11, padding: 8, textAlign: 'center' }}>
            暂无日志，运行脚本后将在此显示
          </div>
        )}
        {execution.log.map((entry, i) => (
          <div key={i} className="log-entry">
            <span className="log-time">{formatTime(entry.timestamp)}</span>
            <span className={`log-level ${levelClass(entry.level)}`}>
              {entry.level.toUpperCase()}
            </span>
            <span className="log-msg">{entry.message}</span>
          </div>
        ))}
        <div ref={logEndRef} />
      </div>
    </div>
  );
}
