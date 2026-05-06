import React, { useEffect, useRef } from 'react';
import { useStore } from '../../store';
import { Trash2 } from 'lucide-react';

export const LogPanel = React.memo(function LogPanel() {
  const logEntries = useStore(s => s.execution.log);
  const clearLog = useStore(s => s.clearLog);
  const logEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logEntries.length]);

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
        {logEntries.length > 0 && (
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
          {logEntries.length} 条
        </span>
      </div>
      <div className="log-entries">
        {logEntries.length === 0 && (
          <div style={{ color: 'rgba(148,163,184,0.3)', fontSize: 11, padding: 8, textAlign: 'center' }}>
            暂无日志，运行脚本后将在此显示
          </div>
        )}
        {logEntries.map((entry, i) => (
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
});
