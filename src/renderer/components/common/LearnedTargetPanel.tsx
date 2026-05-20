import React, { useEffect } from 'react';
import { useStore } from '../../store';
import { Trash2, Target, Brain, Eye, MousePointer, X } from 'lucide-react';

export function LearnedTargetPanel() {
  const { learnedTargets, showLearnedPanel, toggleLearnedPanel, fetchLearnedTargets, deleteLearnedTarget } = useStore();

  useEffect(() => {
    if (showLearnedPanel) fetchLearnedTargets();
  }, [showLearnedPanel, fetchLearnedTargets]);

  const strategyIcon = (t: any) => {
    const ctx = t.context || {};
    if (t.uia?.name) return <Eye size={11} style={{ marginRight: 3 }} />;
    if (t.ocr?.text) return <Brain size={11} style={{ marginRight: 3 }} />;
    if (t.template_path) return <Target size={11} style={{ marginRight: 3 }} />;
    return <MousePointer size={11} style={{ marginRight: 3 }} />;
  };

  const strategyLabel = (t: any) => {
    if (t.uia?.name) return 'UIA';
    if (t.ocr?.text) return 'OCR';
    if (t.template_path) return '模板';
    return '坐标';
  };

  const totalHits = (t: any) => (t.success_count || 0) + (t.failure_count || 0);
  const accuracy = (t: any) => {
    const total = totalHits(t);
    return total === 0 ? '-' : `${Math.round((t.success_count || 0) / total * 100)}%`;
  };

  if (!showLearnedPanel) return null;

  return (
    <div className="learned-target-panel" style={{
      position: 'fixed',
      right: 0,
      top: 70,
      width: 320,
      maxHeight: 'calc(100vh - 140px)',
      background: 'var(--bg-primary)',
      border: '1px solid var(--border-primary)',
      borderRadius: '8px 0 0 8px',
      boxShadow: '-4px 0 20px rgba(0,0,0,0.15)',
      zIndex: 1000,
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden',
    }}>
      <div style={{
        padding: '12px 16px',
        borderBottom: '1px solid var(--border-primary)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        fontWeight: 600,
        fontSize: 13,
      }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <Brain size={14} />
          已学习目标
        </span>
        <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>
          {learnedTargets.length} 个
        </span>
      </div>

      <div style={{
        padding: '8px 16px',
        borderBottom: '1px solid var(--border-primary)',
        fontSize: 11,
        color: 'var(--text-muted)',
        lineHeight: 1.5,
      }}>
        每次执行成功后自动记录目标位置。下次融合感知优先走快速路径，跳过感知层。
      </div>

      {/* Stats bar */}
      <div style={{
        padding: '8px 12px',
        display: 'flex',
        gap: 8,
        fontSize: 11,
        borderBottom: '1px solid var(--border-primary)',
        background: 'var(--bg-secondary)',
      }}>
        <span style={{ color: '#22c55e' }}>✅ {learnedTargets.reduce((s, t) => s + (t.success_count || 0), 0)} 成功</span>
        <span style={{ color: '#ef4444' }}>❌ {learnedTargets.reduce((s, t) => s + (t.failure_count || 0), 0)} 失败</span>
        <span style={{ color: 'var(--text-muted)' }}>
          📊 {learnedTargets.length > 0
            ? Math.round(learnedTargets.reduce((s, t) => {
                const total = (t.success_count || 0) + (t.failure_count || 0);
                return s + (total > 0 ? (t.success_count || 0) / total : 0);
              }, 0) / learnedTargets.length * 100)
            : 0}% 平均准确率
        </span>
      </div>

      {/* Target list */}
      <div style={{ flex: 1, overflowY: 'auto', padding: 8 }}>
        {learnedTargets.length === 0 ? (
          <div style={{
            color: 'rgba(148,163,184,0.4)',
            fontSize: 11,
            textAlign: 'center',
            padding: 20,
          }}>
            暂无已学习目标
            <div style={{ marginTop: 8, fontSize: 10 }}>
              运行脚本后，点击操作将被自动记录在此
            </div>
          </div>
        ) : (
          learnedTargets.map((t) => (
            <div
              key={t.target_id}
              style={{
                padding: '10px 12px',
                marginBottom: 6,
                borderRadius: 6,
                background: 'var(--bg-secondary)',
                border: '1px solid var(--border-primary)',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                <span style={{ fontWeight: 600, fontSize: 12 }}>{t.target_id}</span>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{
                    fontSize: 10, padding: '1px 5px', borderRadius: 3,
                    background: t.uia?.name ? 'rgba(59,130,246,0.15)' : t.ocr?.text ? 'rgba(139,92,246,0.15)' : 'rgba(148,163,184,0.15)',
                    color: t.uia?.name ? '#3b82f6' : t.ocr?.text ? '#8b5cf6' : 'var(--text-muted)',
                    display: 'flex', alignItems: 'center',
                  }}>
                    {strategyIcon(t)}
                    {strategyLabel(t)}
                  </span>
                  <span style={{
                    fontSize: 10, padding: '1px 5px', borderRadius: 3,
                    background: 'rgba(34,197,94,0.1)', color: '#22c55e',
                  }}>
                    {accuracy(t)}
                  </span>
                </div>
              </div>
              <div style={{ fontSize: 10, color: 'var(--text-muted)', display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {t.app?.title && <span>📱 {t.app.title}</span>}
                {t.action && <span>🎯 {t.action}</span>}
                <span>🔄 {totalHits(t)} 次</span>
              </div>
              {/* Delete button on hover */}
              <div style={{ marginTop: 4, textAlign: 'right' }}>
                <button
                  className="btn btn-ghost"
                  style={{ padding: '2px 6px', fontSize: 10, color: 'var(--text-muted)', opacity: 0.6 }}
                  onClick={() => deleteLearnedTarget(t.target_id)}
                  title="删除此目标"
                >
                  <Trash2 size={10} />
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
