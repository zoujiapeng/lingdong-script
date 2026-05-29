import React, { useState } from 'react';
import { useStore } from '../../store';
import type { PlanNodeState } from '../../types';

const STATUS_ICONS: Record<string, string> = {
  pending: '○',
  running: '⟳',
  completed: '✓',
  failed: '✗',
  skipped: '—',
};

const STATUS_COLORS: Record<string, string> = {
  pending: 'var(--text-muted)',
  running: '#2196F3',
  completed: '#4CAF50',
  failed: '#F44336',
  skipped: '#9E9E9E',
};

const PATH_LABELS: Record<number, string> = {
  0: '直接 API',
  1: '浏览器 CDP',
  2: 'Windows UIA',
  3: 'OCR 视觉',
  4: 'SoM 标号',
  5: '宏录制',
  6: 'DSL 脚本',
};

function groupByPath(nodes: PlanNodeState[]): Map<number, PlanNodeState[]> {
  const groups = new Map<number, PlanNodeState[]>();
  for (const n of nodes) {
    const level = n.path_level ?? 3;
    if (!groups.has(level)) groups.set(level, []);
    groups.get(level)!.push(n);
  }
  return groups;
}

function PhaseProgress({ nodes }: { nodes: PlanNodeState[] }) {
  const total = nodes.length;
  const done = nodes.filter(n => n.status === 'completed' || n.status === 'skipped').length;
  const failed = nodes.filter(n => n.status === 'failed').length;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  return (
    <span style={{ fontSize: 10, color: 'var(--text-muted)', marginLeft: 8 }}>
      {done}/{total} · {pct}%
      {failed > 0 && <span style={{ color: '#F44336', marginLeft: 4 }}>({failed} ✗)</span>}
    </span>
  );
}

function truncate(v: unknown, maxLen = 60): string {
  if (v === undefined || v === null) return '';
  const s = typeof v === 'object' ? JSON.stringify(v) : String(v);
  return s.length > maxLen ? s.slice(0, maxLen) + '…' : s;
}

function safeParamKeys(params: Record<string, unknown> | undefined): string[] {
  if (!params) return [];
  return Object.entries(params)
    .filter(([_, v]) => {
      if (v === undefined || v === null) return false;
      if (typeof v === 'string' && v === '') return false;
      if (Array.isArray(v) && v.length === 0) return false;
      return true;
    })
    .map(([k]) => k);
}

function NodeResult({ result }: { result: unknown }) {
  if (result === undefined || result === null) return null;
  if (typeof result === 'string') {
    return <div style={{ fontSize: 10, color: 'var(--text-secondary)', wordBreak: 'break-all' }}>{result.slice(0, 200)}</div>;
  }
  if (typeof result === 'object' && !Array.isArray(result)) {
    const r = result as Record<string, unknown>;
    const data = (r.data as Record<string, unknown>) ?? r;
    const entries = Object.entries(data).filter(([_, v]) => v !== undefined && v !== null).slice(0, 6);
    if (entries.length === 0) return <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>✓ (无详情)</div>;
    return (
      <div style={{ marginTop: 4 }}>
        {entries.map(([k, v]) => (
          <div key={k} style={{ fontSize: 10, color: 'var(--text-secondary)', padding: '1px 0', display: 'flex', gap: 4 }}>
            <span style={{ color: 'var(--text-muted)', flexShrink: 0 }}>{k}:</span>
            <span style={{ wordBreak: 'break-all', fontFamily: 'monospace', fontSize: 9 }}>{truncate(v, 40)}</span>
          </div>
        ))}
      </div>
    );
  }
  return <div style={{ fontSize: 10, color: 'var(--text-secondary)' }}>{truncate(result, 100)}</div>;
}

function ExpandedDetails({ node }: { node: PlanNodeState }) {
  const paramKeys = safeParamKeys(node.params);
  const hasResult = node.result !== undefined || node.error !== undefined;
  return (
    <div style={{
      marginTop: 6, padding: '6px 8px', borderRadius: 4,
      background: 'var(--bg-secondary)',
      border: '1px solid var(--border-primary)',
      fontSize: 10,
    }}>
      {paramKeys.length > 0 && (
        <div>
          <div style={{ fontWeight: 600, fontSize: 10, color: 'var(--text-muted)', marginBottom: 2 }}>参数</div>
          {paramKeys.map(k => (
            <div key={k} style={{ fontSize: 10, color: 'var(--text-secondary)', padding: '1px 0', display: 'flex', gap: 4 }}>
              <span style={{ color: 'var(--text-muted)', flexShrink: 0 }}>{k}:</span>
              <span style={{ wordBreak: 'break-all', fontFamily: 'monospace', fontSize: 9 }}>{truncate(node.params![k], 40)}</span>
            </div>
          ))}
        </div>
      )}
      {(node.error || hasResult) && (
        <div style={{ marginTop: paramKeys.length > 0 ? 6 : 0 }}>
          <div style={{ fontWeight: 600, fontSize: 10, color: node.error ? '#F44336' : 'var(--text-muted)', marginBottom: 2 }}>
            {node.error ? '错误' : '结果'}
          </div>
          {node.error ? (
            <div style={{ fontSize: 10, color: '#F44336', wordBreak: 'break-all' }}>{node.error}</div>
          ) : (
            <NodeResult result={node.result} />
          )}
        </div>
      )}
      {node.fallback_tools && node.fallback_tools.length > 0 && (
        <div style={{ marginTop: 6, fontSize: 10, color: '#FF9800' }}>
          降级路径: {node.fallback_tools.join(' → ')}
        </div>
      )}
      {node.depends_on && node.depends_on.length > 0 && (
        <div style={{ marginTop: 2, fontSize: 9, color: 'var(--text-muted)' }}>
          依赖: {node.depends_on.join(', ')}
        </div>
      )}
      {node.critical && (
        <div style={{ marginTop: 2, fontSize: 9, color: '#F44336' }}>关键步骤</div>
      )}
    </div>
  );
}

export function PlanVisualizer() {
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(new Set());
  const plan = useStore((s) => s.execution.plan);
  const execStatus = useStore((s) => s.execution.status);

  const toggleNode = (id: string) => {
    setExpandedNodes(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  if (!plan || !plan.nodes || plan.nodes.length === 0) {
    return null;
  }

  const { nodes, summary } = plan;
  const total = summary?.total ?? nodes.length;
  const done = (summary?.completed ?? 0) + (summary?.skipped ?? 0);
  const failed = summary?.failed ?? 0;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;

  const groups = groupByPath(nodes);
  const sortedLevels = [...groups.keys()].sort((a, b) => a - b);

  return (
    <div style={{
      border: '1px solid var(--border-primary)',
      borderRadius: 8,
      overflow: 'hidden',
      marginBottom: 8,
      background: 'var(--bg-primary)',
    }}>
      {/* Header */}
      <div style={{
        padding: '10px 12px',
        borderBottom: '1px solid var(--border-primary)',
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        fontSize: 12,
        fontWeight: 600,
      }}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <rect x="3" y="3" width="18" height="18" rx="2" />
          <path d="M3 9h18" /><path d="M9 3v18" />
        </svg>
        <span style={{ flex: 1 }}>执行计划</span>
        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
          {done}/{total} · {pct}%
        </span>
        <span style={{
          fontSize: 10, padding: '1px 6px', borderRadius: 4,
          background: execStatus === 'running' ? '#2196F320' : execStatus === 'completed' ? '#4CAF5020' : '#F4433620',
          color: STATUS_COLORS[execStatus === 'idle' ? 'pending' : execStatus] ?? 'var(--text-muted)',
        }}>
          {execStatus === 'running' ? '运行中' : execStatus === 'completed' ? '已完成' : execStatus === 'error' ? '失败' : '等待中'}
        </span>
      </div>

      {/* Progress bar */}
      <div style={{ height: 3, background: 'var(--bg-secondary)', position: 'relative' }}>
        <div style={{
          height: '100%',
          width: `${pct}%`,
          background: failed > 0 ? '#F44336' : execStatus === 'running' ? '#2196F3' : '#4CAF50',
          transition: 'width 0.3s ease',
          borderRadius: '0 2px 2px 0',
        }} />
      </div>

      {/* Phase-grouped node list */}
      <div style={{ maxHeight: 400, overflow: 'auto' }}>
        {sortedLevels.map((level) => {
          const phaseNodes = groups.get(level)!;
          const phaseRunning = phaseNodes.some(n => n.status === 'running');
          const phaseFailed = phaseNodes.some(n => n.status === 'failed');
          const phaseDone = phaseNodes.every(n =>
            n.status === 'completed' || n.status === 'skipped' || n.status === 'failed'
          );

          return (
            <div key={level} style={{
              borderBottom: level !== sortedLevels[sortedLevels.length - 1] ? '1px solid var(--border-primary)' : 'none',
            }}>
              {/* Phase header */}
              <div style={{
                display: 'flex', alignItems: 'center', gap: 6,
                padding: '6px 12px',
                background: phaseRunning ? 'rgba(33,150,243,0.06)' : phaseDone ? 'rgba(76,175,80,0.04)' : 'transparent',
                borderBottom: '1px solid var(--border-primary)',
                fontSize: 10, fontWeight: 600, color: 'var(--text-secondary)',
                position: 'sticky', top: 0, zIndex: 1,
                backdropFilter: 'blur(4px)',
              }}>
                <span style={{
                  width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
                  background: phaseFailed ? '#F44336' : phaseRunning ? '#2196F3' : phaseDone ? '#4CAF50' : 'var(--border-primary)',
                }} />
                {PATH_LABELS[level] ?? `路径 L${level}`}
                <PhaseProgress nodes={phaseNodes} />
              </div>

              {/* Phase nodes */}
              {phaseNodes.map((node, idx) => {
                const isRunning = node.status === 'running';
                const icon = STATUS_ICONS[node.status] ?? '○';
                const color = STATUS_COLORS[node.status] ?? 'var(--text-muted)';
                const toolShort = (node.tool?.split('_').slice(0, 2).join('_')) || node.tool || '';
                const isExpanded = expandedNodes.has(node.id);
                const hasDetails = safeParamKeys(node.params).length > 0 ||
                  node.result !== undefined ||
                  node.error !== undefined ||
                  (node.fallback_tools && node.fallback_tools.length > 0) ||
                  (node.depends_on && node.depends_on.length > 0);

                return (
                  <div key={node.id}>
                    <div
                      onClick={() => hasDetails && toggleNode(node.id)}
                      style={{
                        display: 'flex', alignItems: 'flex-start', gap: 8,
                        padding: '7px 12px',
                        paddingLeft: 24,
                        borderBottom: idx < phaseNodes.length - 1 ? '1px solid var(--border-primary)' : 'none',
                        background: isRunning ? 'rgba(33,150,243,0.05)' : isExpanded ? 'rgba(255,255,255,0.02)' : 'transparent',
                        opacity: node.status === 'pending' ? 0.5 : 1,
                        cursor: hasDetails ? 'pointer' : 'default',
                        transition: 'background 0.15s',
                      }}
                      title={hasDetails ? '点击查看详情' : ''}
                    >
                      {/* Status icon */}
                      <div style={{
                        width: 20, height: 20, borderRadius: '50%',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        fontSize: 11, fontWeight: 700, flexShrink: 0,
                        background: isRunning ? '#2196F320' : node.status === 'completed' ? '#4CAF5020' : node.status === 'failed' ? '#F4433620' : 'var(--bg-secondary)',
                        color: color,
                        animation: isRunning ? 'agent-spin 0.7s linear infinite' : 'none',
                      }}>
                        {icon}
                      </div>

                      {/* Content */}
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                          <span style={{ fontSize: 11, fontWeight: 500, color: 'var(--text-primary)' }}>
                            {node.description || node.tool || node.id}
                          </span>
                          <span style={{
                            fontSize: 9, padding: '0 4px', borderRadius: 3,
                            background: 'var(--bg-secondary)',
                            color: 'var(--text-muted)',
                          }}>
                            {toolShort}
                          </span>
                          {hasDetails && (
                            <span style={{
                              fontSize: 9, color: 'var(--text-muted)',
                              marginLeft: 'auto',
                              transition: 'transform 0.15s',
                              display: 'inline-block',
                              transform: isExpanded ? 'rotate(90deg)' : 'none',
                            }}>
                              ▶
                            </span>
                          )}
                        </div>

                        {/* Collapsed summary of params */}
                        {!isExpanded && (() => {
                          const keys = safeParamKeys(node.params).slice(0, 3);
                          if (keys.length === 0) return null;
                          const preview = keys.map(k => `${k}=${truncate(node.params![k], 16)}`).join(', ');
                          const remaining = safeParamKeys(node.params).length - 3;
                          return (
                            <div style={{ fontSize: 9, color: 'var(--text-muted)', marginTop: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                              {preview}{remaining > 0 ? ` +${remaining}` : ''}
                            </div>
                          );
                        })()}

                        {/* Attempts / retries info */}
                        {node.attempts > 1 && (
                          <div style={{ fontSize: 10, color: '#FF9800', marginTop: 2 }}>
                            重试 {node.attempts - 1} 次
                          </div>
                        )}

                        {/* Error message (collapsed) */}
                        {!isExpanded && node.error && (
                          <div style={{ fontSize: 10, color: '#F44336', marginTop: 2, wordBreak: 'break-all' }}>
                            {node.error.length > 80 ? node.error.slice(0, 80) + '…' : node.error}
                          </div>
                        )}
                      </div>

                      {/* Status text */}
                      <div style={{
                        fontSize: 10, color, flexShrink: 0, whiteSpace: 'nowrap',
                      }}>
                        {node.status === 'pending' ? '等待' : node.status === 'running' ? '执行中' : node.status === 'completed' ? '完成' : node.status === 'failed' ? '失败' : '跳过'}
                      </div>
                    </div>

                    {/* Expanded details */}
                    {isExpanded && (
                      <div style={{ padding: '0 12px 8px 44px' }}>
                        <ExpandedDetails node={node} />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>

      {/* Footer summary */}
      {summary && (
        <div style={{
          padding: '6px 12px',
          borderTop: '1px solid var(--border-primary)',
          fontSize: 11,
          color: 'var(--text-muted)',
          display: 'flex',
          gap: 12,
          background: 'var(--bg-secondary)',
        }}>
          <span style={{ color: '#4CAF50' }}>✓ {summary.completed ?? 0} 完成</span>
          {failed > 0 && <span style={{ color: '#F44336' }}>✗ {failed} 失败</span>}
          {summary.skipped != null && summary.skipped > 0 && <span style={{ color: '#9E9E9E' }}>— {summary.skipped} 跳过</span>}
          <span>○ {summary.pending ?? 0} 待处理</span>
        </div>
      )}
    </div>
  );
}
