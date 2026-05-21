import React, { useEffect, useRef, useState, useMemo, useCallback } from 'react';
import { useStore } from '../../store';
import { Trash2, Eye, Search, Copy, Download, Filter } from 'lucide-react';

type LogLevel = 'debug' | 'info' | 'warn' | 'error';

const LEVEL_ORDER: LogLevel[] = ['error', 'warn', 'info', 'debug'];

const LEVEL_LABELS: Record<LogLevel, string> = {
  debug: 'DEBUG',
  info: 'INFO',
  warn: 'WARN',
  error: 'ERROR',
};

export const LogPanel = React.memo(function LogPanel() {
  const logEntries = useStore(s => s.execution.log);
  const clearLog = useStore(s => s.clearLog);
  const variables = useStore(s => s.execution.variables);
  const nodes = useStore(s => s.script.nodes);
  const [showVars, setShowVars] = useState(false);
  const [activeLevels, setActiveLevels] = useState<Set<LogLevel>>(new Set(LEVEL_ORDER));
  const [searchText, setSearchText] = useState('');
  const [autoScroll, setAutoScroll] = useState(true);
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);
  const [prevTimestamp, setPrevTimestamp] = useState(0);
  const logEndRef = useRef<HTMLDivElement>(null);

  // Build a node lookup map: id → node
  const nodeMap = useMemo(() => {
    const map = new Map<string, { type: string; label: string }>();
    for (const n of nodes) {
      map.set(n.id, { type: n.type, label: n.data?.label || n.type });
    }
    return map;
  }, [nodes]);

  // Scroll to bottom when new logs arrive
  useEffect(() => {
    if (autoScroll) {
      logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logEntries.length, autoScroll]);

  // Toggle a level filter
  const toggleLevel = useCallback((level: LogLevel) => {
    setActiveLevels(prev => {
      const next = new Set(prev);
      if (next.has(level)) {
        // Don't allow removing all filters
        if (next.size === 1) return prev;
        next.delete(level);
      } else {
        next.add(level);
      }
      return next;
    });
  }, []);

  // Count per level
  const levelCounts = useMemo(() => {
    const counts: Record<string, number> = { debug: 0, info: 0, warn: 0, error: 0 };
    for (const entry of logEntries) {
      counts[entry.level] = (counts[entry.level] || 0) + 1;
    }
    return counts;
  }, [logEntries]);

  // Filtered entries
  const filteredEntries = useMemo(() => {
    return logEntries.filter(entry => {
      if (!activeLevels.has(entry.level as LogLevel)) return false;
      if (searchText) {
        const q = searchText.toLowerCase();
        const msgMatch = entry.message.toLowerCase().includes(q);
        const nodeMatch = entry.node_id?.toLowerCase().includes(q);
        const levelMatch = entry.level.toLowerCase().includes(q);
        if (!msgMatch && !nodeMatch && !levelMatch) return false;
      }
      return true;
    });
  }, [logEntries, activeLevels, searchText]);

  const copyAll = useCallback(() => {
    const text = filteredEntries.map(e =>
      `[${new Date(e.timestamp).toLocaleTimeString('zh-CN', { hour12: false })}] [${e.level.toUpperCase()}]${e.node_id ? ` [${e.node_id}]` : ''} ${e.message}`
    ).join('\n');
    navigator.clipboard.writeText(text);
  }, [filteredEntries]);

  const exportLog = useCallback(() => {
    const text = filteredEntries.map(e =>
      `[${e.timestamp}] [${e.level.toUpperCase()}]${e.node_id ? ` [${e.node_id}]` : ''} ${e.message}`
    ).join('\n');
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `execution-log-${Date.now()}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  }, [filteredEntries]);

  // Format time delta since last log
  const formatDelta = (ts: string, index: number) => {
    const t = new Date(ts).getTime();
    if (index === 0) { setPrevTimestamp(t); return ''; }
    const delta = t - prevTimestamp;
    setPrevTimestamp(t);
    if (delta < 1000) return `${delta}ms`;
    return `${(delta / 1000).toFixed(1)}s`;
  };

  return (
    <div className="log-panel">
      {/* Header */}
      <div className="log-panel-header">
        <span className="log-panel-title">
          <span>执行日志</span>
          {filteredEntries.length < logEntries.length && (
            <span style={{ fontSize: 10, color: 'rgba(148,163,184,0.4)', marginLeft: 6 }}>
              ({filteredEntries.length}/{logEntries.length})
            </span>
          )}
        </span>
        <div className="log-panel-header-actions">
          <button
            className={`log-btn ${autoScroll ? 'active' : ''}`}
            onClick={() => setAutoScroll(!autoScroll)}
            title="自动滚动"
          >
            <span style={{ fontSize: 10 }}>↓</span>
          </button>
          <button
            className="log-btn"
            onClick={copyAll}
            title="复制全部"
          >
            <Copy size={11} />
          </button>
          <button
            className="log-btn"
            onClick={exportLog}
            title="导出日志"
          >
            <Download size={11} />
          </button>
          <button
            className={`log-btn ${showVars ? 'active' : ''}`}
            onClick={() => setShowVars(!showVars)}
            title="变量监视"
          >
            <Eye size={11} />
          </button>
          {logEntries.length > 0 && (
            <button className="log-btn" onClick={clearLog} title="清空日志">
              <Trash2 size={11} />
            </button>
          )}
          <span className="log-count-badge">{logEntries.length}</span>
        </div>
      </div>

      {/* Variable watcher */}
      {showVars && (
        <div className="log-vars">
          <div className="log-vars-title">运行时变量</div>
          {Object.keys(variables).length === 0 ? (
            <div className="log-vars-empty">无变量</div>
          ) : (
            Object.entries(variables).map(([k, v]) => (
              <div key={k} className="log-var-row">
                <span className="log-var-name">{k}</span>
                <span className="log-var-eq">=</span>
                <span className="log-var-val">{v}</span>
              </div>
            ))
          )}
        </div>
      )}

      {/* Filter bar */}
      <div className="log-filter-bar">
        <div className="log-filter-levels">
          {LEVEL_ORDER.map(level => (
            <button
              key={level}
              className={`log-filter-chip ${level} ${activeLevels.has(level) ? 'on' : ''}`}
              onClick={() => toggleLevel(level)}
            >
              {LEVEL_LABELS[level]}
              <span className="log-filter-count">{levelCounts[level] || 0}</span>
            </button>
          ))}
        </div>
        <div className="log-filter-search">
          <Search size={11} style={{ position: 'absolute', left: 6, top: '50%', transform: 'translateY(-50%)', color: 'rgba(148,163,184,0.4)', pointerEvents: 'none' }} />
          <input
            className="log-search-input"
            type="text"
            placeholder="搜索..."
            value={searchText}
            onChange={e => setSearchText(e.target.value)}
          />
          {searchText && (
            <button
              className="log-search-clear"
              onClick={() => setSearchText('')}
            >
              ×
            </button>
          )}
        </div>
      </div>

      {/* Log entries */}
      <div className="log-entries" onScroll={e => {
        const el = e.currentTarget;
        const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        if (atBottom !== autoScroll) setAutoScroll(atBottom);
      }}>
        {filteredEntries.length === 0 && (
          <div className="log-empty">
            {searchText || activeLevels.size < 4
              ? '没有匹配的日志条目'
              : '暂无日志，运行脚本后将在此显示'}
          </div>
        )}
        {filteredEntries.map((entry, i) => {
          const nodeInfo = entry.node_id ? nodeMap.get(entry.node_id) : undefined;
          const isExpanded = expandedIdx === i;
          const longMsg = entry.message.length > 120;

          return (
            <div
              key={`${entry.timestamp}-${i}`}
              className={`log-entry ${isExpanded ? 'expanded' : ''}`}
              onClick={() => longMsg && setExpandedIdx(isExpanded ? null : i)}
            >
              <span className="log-time">{new Date(entry.timestamp).toLocaleTimeString('zh-CN', { hour12: false })}</span>
              {i < filteredEntries.length - 1 && (
                <span className="log-delta" title="距上一条间隔">{(() => {
                  const t = new Date(entry.timestamp).getTime();
                  const prev = new Date(filteredEntries[i + 1].timestamp).getTime();
                  const d = Math.abs(t - prev);
                  return d < 1000 ? `${d}ms` : `${(d / 1000).toFixed(1)}s`;
                })()}</span>
              )}
              <span className={`log-level ${entry.level}`}>{entry.level.toUpperCase()}</span>
              {nodeInfo && (
                <span className="log-node-badge" title={entry.node_id}>
                  {nodeInfo.type}
                </span>
              )}
              <span className={`log-msg ${isExpanded ? '' : (longMsg ? 'truncated' : '')}`}>
                {entry.message}
              </span>
              {longMsg && (
                <span className="log-expand-icon">{isExpanded ? '↑' : '↓'}</span>
              )}
            </div>
          );
        })}
        <div ref={logEndRef} />
      </div>
    </div>
  );
});
