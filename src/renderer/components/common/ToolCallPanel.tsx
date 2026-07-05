import React, { useEffect, useRef } from 'react';
import { useStore } from '../../store';
import { Trash2, ChevronDown, ChevronRight, X } from 'lucide-react';

export const ToolCallPanel = React.memo(function ToolCallPanel() {
  const toolCallLog = useStore(s => s.toolCallLog);
  const clearToolCallLog = useStore(s => s.clearToolCallLog);
  const setUI = useStore(s => s.setUI);
  const logEndRef = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = React.useState<Record<string, boolean>>({});

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [toolCallLog.length]);

  const toggleExpand = (id: string) => {
    setExpanded((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  return (
    <div className="log-panel tool-call-panel">
      <div className="log-panel-header">
        <span className="log-panel-title">工具调用记录</span>
        {toolCallLog.length > 0 && (
          <button
            onClick={clearToolCallLog}
            style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'rgba(148,163,184,0.6)', padding: 2, display: 'flex' }}
            title="清空记录">
            <Trash2 size={12} />
          </button>
        )}
        <span style={{ fontSize: 10, color: 'rgba(148,163,184,0.4)' }}>{toolCallLog.length} 条</span>
        <button
          onClick={() => setUI({ show_tool_call_panel: false })}
          style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'rgba(148,163,184,0.65)', padding: 2, display: 'flex' }}
          title="关闭工具记录"
          aria-label="关闭工具记录">
          <X size={12} />
        </button>
      </div>
      <div className="log-entries">
        {toolCallLog.length === 0 && (
          <div style={{ color: 'rgba(148,163,184,0.3)', fontSize: 11, padding: 8, textAlign: 'center' }}>
            暂无调用记录，执行工具后将在此显示
          </div>
        )}
        {toolCallLog.map((entry) => (
          <div key={entry.id} className="log-entry" style={{ flexDirection: 'column', alignItems: 'stretch', cursor: 'pointer' }}
               onClick={() => toggleExpand(entry.id)}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, width: '100%' }}>
              <span style={{ fontSize: 10, color: 'rgba(148,163,184,0.5)', minWidth: 36, fontFamily: 'monospace' }}>
                {new Date(entry.timestamp).toLocaleTimeString('zh-CN', { hour12: false })}
              </span>
              <span style={{
                fontSize: 9, fontWeight: 600, padding: '1px 4px', borderRadius: 2, fontFamily: 'monospace', minWidth: 24, textAlign: 'center',
                background: entry.success ? 'rgba(34,197,94,0.15)' : 'rgba(239,68,68,0.15)',
                color: entry.success ? 'rgb(34,197,94)' : 'rgb(239,68,68)',
              }}>
                {entry.success ? 'OK' : 'FAIL'}
              </span>
              <span className="log-msg" style={{ fontWeight: 500, flex: 1 }}>{entry.display_name || entry.feature_name}</span>
              <span style={{ fontSize: 10, color: 'rgba(148,163,184,0.4)', fontFamily: 'monospace' }}>
                {entry.elapsed_ms}ms
              </span>
              {entry.source === 'direct' && (
                <span style={{ fontSize: 9, padding: '1px 4px', borderRadius: 2, background: 'rgba(99,102,241,0.12)', color: 'rgba(99,102,241,0.7)' }}>直调</span>
              )}
              {expanded[entry.id] ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
            </div>
            {expanded[entry.id] && (
              <div style={{ marginTop: 4, fontSize: 10, color: 'var(--text-muted)', paddingLeft: 42 }}>
                <div style={{ marginBottom: 2 }}>参数: {Object.keys(entry.params).length ? JSON.stringify(entry.params) : '(无)'}</div>
                <div style={{ marginBottom: 2 }}>结果: {typeof entry.result === 'object' ? JSON.stringify(entry.result).slice(0, 300) : String(entry.result ?? '').slice(0, 300)}</div>
              </div>
            )}
          </div>
        ))}
        <div ref={logEndRef} />
      </div>
    </div>
  );
});
