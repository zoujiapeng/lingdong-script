import React, { useEffect, useRef } from 'react';
import { useStore } from '../../store';
import { Trash2, Eye } from 'lucide-react';

export const LogPanel = React.memo(function LogPanel() {
  const logEntries = useStore(s => s.execution.log);
  const clearLog = useStore(s => s.clearLog);
  const variables = useStore(s => s.execution.variables);
  const [showVars, setShowVars] = React.useState(false);
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
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <button
            onClick={() => setShowVars(!showVars)}
            style={{
              background: 'none', border: 'none', cursor: 'pointer',
              color: showVars ? '#8b5cf6' : 'rgba(148,163,184,0.6)', padding: 2, display: 'flex',
            }}
            title="变量监视"
          >
            <Eye size={12} />
          </button>
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
      </div>
      {/* Variable watcher */}
      {showVars && (
        <div style={{ padding: '4px 12px', borderBottom: '1px solid var(--border-primary)', marginBottom: 4 }}>
          <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 4, fontWeight: 600 }}>运行时变量</div>
          {Object.keys(variables).length === 0 ? (
            <div style={{ fontSize: 10, color: 'rgba(148,163,184,0.4)', padding: '2px 0' }}>无变量</div>
          ) : (
            Object.entries(variables).map(([k, v]) => (
              <div key={k} style={{ fontSize: 10, display: 'flex', gap: 8, padding: '1px 0', fontFamily: "'JetBrains Mono', monospace" }}>
                <span style={{ color: '#8b5cf6', fontWeight: 600 }}>{k}</span>
                <span style={{ color: '#22c55e' }}>=</span>
                <span style={{ color: '#e2e8f0', wordBreak: 'break-all' }}>{v}</span>
              </div>
            ))
          )}
        </div>
      )}
      <div className="log-entries">
        {logEntries.length === 0 && (
          <div style={{ color: 'rgba(148,163,184,0.3)', fontSize: 11, padding: 8, textAlign: 'center' }}>
            暂无日志，运行脚本后将在此显示
          </div>
        )}
        {logEntries.map((entry, i) => (
          <div key={`${entry.timestamp}-${i}`} className="log-entry">
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
