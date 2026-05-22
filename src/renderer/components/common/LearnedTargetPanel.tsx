import React, { useEffect } from 'react';
import { useStore } from '../../store';
import { Trash2, Target, Brain, Eye, MousePointer, X, Zap, FileText, Loader } from 'lucide-react';

export function LearnedTargetPanel() {
  const {
    learnedTargets, showLearnedPanel, toggleLearnedPanel,
    fetchLearnedTargets, deleteLearnedTarget,
    crystallizedTemplates, fetchCrystallizedTemplates,
    loadScript,
  } = useStore();

  useEffect(() => {
    if (showLearnedPanel) {
      fetchLearnedTargets();
      fetchCrystallizedTemplates();
    }
  }, [showLearnedPanel, fetchLearnedTargets, fetchCrystallizedTemplates]);

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

  const handleLoadCrystallized = async (tpl: any) => {
    try {
      const api = (window as any).electronAPI;
      const content = api?.readFile ? await api.readFile(tpl.path) : null;
      if (content) {
        const script = JSON.parse(content);
        loadScript(script);
        toggleLearnedPanel();
      }
    } catch (e) {
      console.error('[LearnedTargetPanel] 加载结晶模板失败:', e);
    }
  };

  if (!showLearnedPanel) return null;

  const hasLearnedTargets = learnedTargets.length > 0;
  const hasCrystallized = crystallizedTemplates.length > 0;

  return (
    <div className="learned-target-panel" style={{
      position: 'fixed',
      right: 0,
      top: 70,
      width: 340,
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
      {/* Header */}
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
          已学习技能
        </span>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>
            {learnedTargets.length + crystallizedTemplates.length} 项
          </span>
          <button className="btn btn-ghost btn-icon" onClick={toggleLearnedPanel} style={{ width: 20, height: 20 }}>
            <X size={12} />
          </button>
        </div>
      </div>

      <div style={{
        padding: '8px 16px',
        borderBottom: '1px solid var(--border-primary)',
        fontSize: 11,
        color: 'var(--text-muted)',
        lineHeight: 1.5,
      }}>
        系统从执行中自动学习两类技能：微观的元素定位（按钮/输入框位置）和宏观的完整工作流模板。
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: 8 }}>

        {/* ── ① 微观：已学习目标 ── */}
        <div style={{
          padding: '6px 4px 4px', fontSize: 11, fontWeight: 600,
          color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: 4,
        }}>
          <Target size={12} />
          已学习元素
          <span style={{ marginLeft: 4, fontSize: 10, fontWeight: 400, color: 'var(--text-muted)' }}>
            ({learnedTargets.length})
          </span>
        </div>

        {/* Stats bar */}
        {hasLearnedTargets && (
          <div style={{
            padding: '6px 10px', marginBottom: 6,
            display: 'flex', gap: 8, fontSize: 10,
            background: 'var(--bg-secondary)', borderRadius: 4,
          }}>
            <span style={{ color: '#22c55e' }}>✅ {learnedTargets.reduce((s, t) => s + (t.success_count || 0), 0)}</span>
            <span style={{ color: '#ef4444' }}>❌ {learnedTargets.reduce((s, t) => s + (t.failure_count || 0), 0)}</span>
            <span style={{ color: 'var(--text-muted)' }}>
              📊 {learnedTargets.length > 0
                ? Math.round(learnedTargets.reduce((s, t) => {
                    const total = (t.success_count || 0) + (t.failure_count || 0);
                    return s + (total > 0 ? (t.success_count || 0) / total : 0);
                  }, 0) / learnedTargets.length * 100)
                : 0}%
            </span>
          </div>
        )}

        {/* Target list */}
        {!hasLearnedTargets ? (
          <div style={{
            color: 'rgba(148,163,184,0.4)', fontSize: 11, textAlign: 'center', padding: '16px 8px',
          }}>
            暂无已学习元素
            <div style={{ marginTop: 4, fontSize: 10 }}>运行脚本后，点击操作将被自动记录在此</div>
          </div>
        ) : (
          learnedTargets.slice(0, 20).map((t) => (
            <div key={t.target_id} style={{
              padding: '8px 10px', marginBottom: 4,
              borderRadius: 4, background: 'var(--bg-secondary)',
              border: '1px solid var(--border-primary)',
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 2 }}>
                <span style={{ fontWeight: 600, fontSize: 11 }}>{t.target_id}</span>
                <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <span style={{
                    fontSize: 9, padding: '1px 4px', borderRadius: 2,
                    background: t.uia?.name ? 'rgba(59,130,246,0.15)' : t.ocr?.text ? 'rgba(139,92,246,0.15)' : 'rgba(148,163,184,0.15)',
                    color: t.uia?.name ? '#3b82f6' : t.ocr?.text ? '#8b5cf6' : 'var(--text-muted)',
                    display: 'flex', alignItems: 'center',
                  }}>
                    {strategyIcon(t)}
                    {strategyLabel(t)}
                  </span>
                  <span style={{
                    fontSize: 9, padding: '1px 4px', borderRadius: 2,
                    background: 'rgba(34,197,94,0.1)', color: '#22c55e',
                  }}>
                    {accuracy(t)}
                  </span>
                  <button className="btn btn-ghost" style={{ padding: '1px 3px', fontSize: 9, color: 'var(--text-muted)', opacity: 0.5 }}
                    onClick={() => deleteLearnedTarget(t.target_id)} title="删除">
                    <Trash2 size={8} />
                  </button>
                </div>
              </div>
              <div style={{ fontSize: 9, color: 'var(--text-muted)', display: 'flex', gap: 6 }}>
                {t.app?.title && <span>📱 {t.app.title}</span>}
                {t.action && <span>🎯 {t.action}</span>}
                <span>🔄 {totalHits(t)} 次</span>
              </div>
            </div>
          ))
        )}

        {/* ── ② 宏观：结晶模板 ── */}
        <div style={{
          padding: '10px 4px 4px', fontSize: 11, fontWeight: 600,
          color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: 4,
        }}>
          <Zap size={12} style={{ color: '#f59e0b' }} />
          结晶模板
          <span style={{ marginLeft: 4, fontSize: 10, fontWeight: 400, color: 'var(--text-muted)' }}>
            ({crystallizedTemplates.length}) — 执行成功后自动生成
          </span>
        </div>

        {!hasCrystallized ? (
          <div style={{
            color: 'rgba(148,163,184,0.4)', fontSize: 11, textAlign: 'center', padding: '16px 8px',
          }}>
            暂无结晶模板
            <div style={{ marginTop: 4, fontSize: 10 }}>执行脚本成功后，完整工作流将自动保存为可复用的模板</div>
          </div>
        ) : (
          crystallizedTemplates.slice(0, 8).map((tpl: any) => (
            <button key={tpl.id} onClick={() => handleLoadCrystallized(tpl)} style={{
              display: 'flex', alignItems: 'center', gap: 6,
              width: '100%', padding: '6px 10px', marginBottom: 3,
              borderRadius: 4, border: '1px solid var(--border-primary)',
              background: 'var(--bg-secondary)', cursor: 'pointer',
              fontSize: 11, textAlign: 'left', color: 'var(--text-primary)',
              transition: 'background var(--transition-fast)',
            }}
              onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--accent-light)')}
              onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--bg-secondary)')}
            >
              <FileText size={12} style={{ color: '#f59e0b', flexShrink: 0 }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600, fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {tpl.name}
                </div>
                <div style={{ fontSize: 9, color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {tpl.description || tpl.id}
                </div>
              </div>
              {tpl.created && (
                <span style={{ fontSize: 9, color: 'var(--text-muted)', flexShrink: 0 }}>
                  {new Date(tpl.created).toLocaleDateString()}
                </span>
              )}
            </button>
          ))
        )}

        {hasCrystallized && crystallizedTemplates.length > 8 && (
          <div style={{ fontSize: 10, color: 'var(--text-muted)', textAlign: 'center', padding: 4 }}>
            ...还有 {crystallizedTemplates.length - 8} 个模板（见顶部「模板」下拉菜单）
          </div>
        )}
      </div>
    </div>
  );
}
