import React, { useState, useEffect } from 'react';

interface ScheduleDialogProps {
  onClose: () => void;
  onCreated: () => void;
}

interface ConditionRule {
  type: string;
  params: Record<string, any>;
}

const OVERLAY_STYLE: React.CSSProperties = {
  position: 'fixed', inset: 0, zIndex: 1000,
  background: 'rgba(0,0,0,0.4)',
  display: 'flex', alignItems: 'center', justifyContent: 'center',
};
const DIALOG_STYLE: React.CSSProperties = {
  background: 'var(--bg-primary)', borderRadius: 12,
  boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
  maxHeight: '80vh', overflow: 'auto', width: 480,
};
const HEADER_STYLE: React.CSSProperties = {
  padding: '14px 16px', borderBottom: '1px solid var(--border-primary)',
  fontSize: 15, fontWeight: 600,
};
const SECTION_TITLE: React.CSSProperties = {
  fontSize: 12, fontWeight: 600, color: 'var(--text-muted)',
  textTransform: 'uppercase', letterSpacing: '0.05em',
  marginBottom: 8,
};

const CONDITION_LABELS: Record<string, string> = {
  always_true: '无条件',
  window_exists: '窗口存在',
  process_running: '进程运行中',
  is_weekday: '工作日',
  is_weekend: '周末',
  day_of_week: '匹配星期',
  time_between: '时间段内',
  image_exists: '图片出现',
  text_exists: '文字出现(OCR)',
  color_match: '颜色匹配',
  scene_match: '场景匹配',
};

const CONDITION_PARAMS: Record<string, { name: string; label: string; type: string; default?: any }[]> = {
  window_exists: [{ name: 'title', label: '窗口标题', type: 'string' }],
  process_running: [{ name: 'name', label: '进程名', type: 'string' }],
  is_weekday: [],
  is_weekend: [],
  day_of_week: [{ name: 'days', label: '星期（0=周一 6=周日）', type: 'daypicker' }],
  time_between: [
    { name: 'start_hour', label: '开始小时', type: 'number', default: 9 },
    { name: 'end_hour', label: '结束小时', type: 'number', default: 18 },
  ],
  image_exists: [
    { name: 'path', label: '图片路径', type: 'string' },
    { name: 'similarity', label: '相似度', type: 'number', default: 90 },
  ],
  text_exists: [
    { name: 'text', label: '文字内容', type: 'string' },
    { name: 'similarity', label: '相似度', type: 'number', default: 80 },
  ],
  color_match: [
    { name: 'expected_color', label: '颜色 (#RRGGBB)', type: 'string' },
    { name: 'tolerance', label: '容差', type: 'number', default: 30 },
  ],
  scene_match: [
    { name: 'scene_name', label: '场景名', type: 'string' },
    { name: 'similarity', label: '相似度', type: 'number', default: 85 },
  ],
};

function getDefaultParams(type: string): Record<string, any> {
  const defs = CONDITION_PARAMS[type] || [];
  const params: Record<string, any> = {};
  for (const p of defs) {
    if (p.default !== undefined) params[p.name] = p.default;
    else if (p.type === 'number') params[p.name] = 0;
    else if (p.type === 'daypicker') params[p.name] = [0, 1, 2, 3, 4];
    else params[p.name] = '';
  }
  return params;
}

export function ScheduleDialog({ onClose, onCreated }: ScheduleDialogProps) {
  const [name, setName] = useState('');
  const [templateId, setTemplateId] = useState('');
  const [templateName, setTemplateName] = useState('');
  const [triggerType, setTriggerType] = useState('cron');
  const [cronExpr, setCronExpr] = useState('0 * * * *');
  const [intervalSec, setIntervalSec] = useState(3600);
  const [conditions, setConditions] = useState<ConditionRule[]>([]);
  const [conditionOp, setConditionOp] = useState<'AND' | 'OR'>('AND');
  const [templates, setTemplates] = useState<any[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    try {
      const reg = (window as any).__templateRegistry;
      if (reg) setTemplates(reg.getAll() || []);
    } catch {}
  }, []);

  const canSave = name.trim() && templateId.trim() && !saving;

  const addCondition = (type: string) => {
    setConditions([...conditions, { type, params: getDefaultParams(type) }]);
  };

  const removeCondition = (idx: number) => {
    setConditions(conditions.filter((_, i) => i !== idx));
  };

  const updateConditionParam = (idx: number, param: string, value: any) => {
    const updated = conditions.map((c, i) => {
      if (i !== idx) return c;
      return { ...c, params: { ...c.params, [param]: value } };
    });
    setConditions(updated);
  };

  const changeConditionType = (idx: number, newType: string) => {
    const updated = conditions.map((c, i) => {
      if (i !== idx) return c;
      return { type: newType, params: getDefaultParams(newType) };
    });
    setConditions(updated);
  };

  const handleCreate = async () => {
    if (!canSave) return;
    setSaving(true);
    setError('');
    try {
      const trigger: any = { type: triggerType };
      if (triggerType === 'cron') trigger.cron_expr = cronExpr.trim();
      if (triggerType === 'interval') trigger.interval_seconds = intervalSec;

      const entry: any = {
        name: name.trim(),
        template_id: templateId.trim(),
        template_name: templateName.trim(),
        triggers: [trigger],
        enabled: true,
      };
      if (conditions.length > 0) {
        entry.conditions = { operator: conditionOp, rules: conditions };
      }

      await (window as any).electronAPI?.scheduleCreate(entry);
      onCreated();
      onClose();
    } catch (e: any) {
      setError(e.message || '创建失败');
    } finally {
      setSaving(false);
    }
  };

  const availableConditionTypes = Object.keys(CONDITION_LABELS).filter(k => k !== 'always_true');

  return (
    <div style={OVERLAY_STYLE} onClick={onClose}>
      <div style={DIALOG_STYLE} onClick={e => e.stopPropagation()}>
        <div style={HEADER_STYLE}>新增定时任务</div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, padding: 16 }}>
          {/* 名称 */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>任务名称</label>
            <input className="settings-input" value={name}
              onChange={e => setName(e.target.value)}
              placeholder="例如：上班自动打卡" style={{ width: '100%' }} />
          </div>

          {/* 模板选择 */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>选择模板</label>
            <select className="settings-select" value={templateId}
              onChange={e => {
                const t = templates.find(t => t.id === e.target.value);
                setTemplateId(e.target.value);
                setTemplateName(t?.name || t?.id || '');
              }} style={{ width: '100%' }}>
              <option value="">-- 请选择 --</option>
              {templates.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </div>

          {/* ── 触发器 ── */}
          <div>
            <div style={SECTION_TITLE}>触发器（何时检查）</div>
            <select className="settings-select" value={triggerType}
              onChange={e => setTriggerType(e.target.value)} style={{ width: '100%', marginBottom: 8 }}>
              <option value="cron">Cron 表达式（复杂定时）</option>
              <option value="interval">固定间隔（每 N 秒）</option>
              <option value="startup">启动时执行</option>
              <option value="event">系统事件（窗口/进程/文件）</option>
            </select>

            {triggerType === 'cron' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <input className="settings-input" value={cronExpr}
                  onChange={e => setCronExpr(e.target.value)}
                  placeholder="0 * * * *" style={{ width: '100%', fontFamily: 'monospace' }} />
                <div style={{ fontSize: 11, color: 'var(--text-muted)', lineHeight: 1.5 }}>
                  格式: 分 时 日 月 周 &nbsp;
                  <code>0 * * * *</code> 每小时、<code>*/30 * * * *</code> 每30分钟、
                  <code>0 9 * * 1-5</code> 工作日早9点
                </div>
              </div>
            )}

            {triggerType === 'interval' && (
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <span style={{ fontSize: 12 }}>每</span>
                <input type="number" className="settings-input"
                  value={intervalSec} min={1}
                  onChange={e => setIntervalSec(parseInt(e.target.value) || 0)}
                  style={{ width: 100 }} />
                <span style={{ fontSize: 12 }}>秒</span>
              </div>
            )}
          </div>

          {/* ── 条件 ── */}
          <div>
            <div style={{ ...SECTION_TITLE, display: 'flex', alignItems: 'center', gap: 8 }}>
              <span>守卫条件（何时执行）</span>
              {conditions.length >= 2 && (
                <select className="settings-select" value={conditionOp}
                  onChange={e => setConditionOp(e.target.value as 'AND' | 'OR')}
                  style={{ width: 70, fontSize: 11, padding: '2px 4px' }}>
                  <option value="AND">全部(AND)</option>
                  <option value="OR">任一(OR)</option>
                </select>
              )}
            </div>

            {conditions.length === 0 && (
              <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 8 }}>
                无额外条件（触发即执行）
              </div>
            )}

            {conditions.map((c, idx) => (
              <div key={idx} style={{
                display: 'flex', flexDirection: 'column', gap: 4,
                padding: 8, marginBottom: 8,
                background: 'var(--bg-secondary)', borderRadius: 8,
                border: '1px solid var(--border-primary)',
              }}>
                <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                  <select className="settings-select" value={c.type}
                    onChange={e => changeConditionType(idx, e.target.value)}
                    style={{ flex: 1, fontSize: 12, padding: '4px 6px' }}>
                    {availableConditionTypes.map(ct => (
                      <option key={ct} value={ct}>{CONDITION_LABELS[ct] || ct}</option>
                    ))}
                  </select>
                  <button onClick={() => removeCondition(idx)}
                    style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', fontSize: 16, padding: '0 4px' }}
                    title="删除条件">×</button>
                </div>
                {CONDITION_PARAMS[c.type]?.map(p => (
                  p.type === 'daypicker' ? (
                    <div key={p.name} style={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
                      {[0, 1, 2, 3, 4, 5, 6].map(d => {
                        const days = Array.isArray(c.params?.days) ? c.params.days : [];
                        const selected = days.includes(d);
                        const labels = ['一', '二', '三', '四', '五', '六', '日'];
                        return (
                          <button key={d} onClick={() => {
                            const next = selected ? days.filter((x: number) => x !== d) : [...days, d];
                            updateConditionParam(idx, 'days', next.sort());
                          }} style={{
                            width: 32, height: 28, border: '1px solid var(--border-primary)',
                            borderRadius: 4, cursor: 'pointer', fontSize: 12,
                            background: selected ? 'var(--accent)' : 'var(--bg-primary)',
                            color: selected ? '#fff' : 'var(--text-primary)',
                          }}>{labels[d]}</button>
                        );
                      })}
                    </div>
                  ) : (
                    <input key={p.name} className="settings-input"
                      type={p.type === 'number' ? 'number' : 'text'}
                      value={c.params?.[p.name] ?? ''}
                      onChange={e => updateConditionParam(idx, p.name,
                        p.type === 'number' ? (parseInt(e.target.value) || 0) : e.target.value)}
                      placeholder={p.label} style={{ width: '100%', fontSize: 12 }} />
                  )
                ))}
              </div>
            ))}

            <button onClick={() => addCondition('window_exists')}
              style={{
                background: 'none', border: '1px dashed var(--border-primary)', borderRadius: 6,
                padding: '6px 12px', fontSize: 12, color: 'var(--text-muted)', cursor: 'pointer',
                width: '100%',
              }}>
              + 添加条件
            </button>
          </div>

          {error && <div style={{ fontSize: 12, color: 'var(--error)' }}>{error}</div>}
        </div>

        <div style={{
          display: 'flex', justifyContent: 'flex-end', gap: 8,
          padding: '8px 16px', borderTop: '1px solid var(--border-primary)',
        }}>
          <button className="btn btn-ghost" onClick={onClose}
            style={{ padding: '6px 14px', fontSize: 12 }}>取消</button>
          <button className="btn btn-primary" onClick={handleCreate} disabled={!canSave}
            style={{ padding: '6px 14px', fontSize: 12 }}>
            {saving ? '创建中...' : '创建'}
          </button>
        </div>
      </div>
    </div>
  );
}
