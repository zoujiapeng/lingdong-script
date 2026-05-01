import React from 'react';
import { ConditionNodeParams, ConditionType } from '../../../types';

interface Props {
  params: ConditionNodeParams;
  onChange: (p: Partial<ConditionNodeParams>) => void;
}

const CONDITION_OPTIONS: { value: ConditionType; label: string }[] = [
  { value: 'image_exists', label: '图片存在' },
  { value: 'window_exists', label: '窗口存在' },
  { value: 'variable_equals', label: '变量等于' },
  { value: 'variable_greater', label: '变量大于' },
  { value: 'variable_less', label: '变量小于' },
  { value: 'always_true', label: '始终为真' },
];

export function ConditionParams({ params, onChange }: Props) {
  return (
    <div>
      <div className="form-group">
        <label className="form-label">条件类型</label>
        <select className="form-input form-select" value={params.condition_type} onChange={(e) => onChange({ condition_type: e.target.value as ConditionType })}>
          {CONDITION_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
      </div>

      {(params.condition_type === 'image_exists') && (
        <>
          <div className="form-group">
            <label className="form-label">目标图片</label>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', padding: '12px', border: '1px dashed var(--border-primary)', borderRadius: 'var(--radius-md)', textAlign: 'center' }}>
              暂未实现文件选择器
            </div>
          </div>
          <div className="form-group">
            <label className="form-label">相似度 ({params.similarity || 85}%)</label>
            <div className="slider-group">
              <input type="range" min="50" max="100" value={params.similarity || 85} onChange={(e) => onChange({ similarity: +e.target.value })} />
              <span className="slider-value">{params.similarity || 85}%</span>
            </div>
          </div>
        </>
      )}

      {params.condition_type === 'window_exists' && (
        <div className="form-group">
          <label className="form-label">窗口标题</label>
          <input className="form-input" value={params.window_title || ''} onChange={(e) => onChange({ window_title: e.target.value })} placeholder="例如: 计算器" />
        </div>
      )}

      {(params.condition_type === 'variable_equals' || params.condition_type === 'variable_greater' || params.condition_type === 'variable_less') && (
        <>
          <div className="form-group">
            <label className="form-label">变量名</label>
            <input className="form-input" value={params.variable_name || ''} onChange={(e) => onChange({ variable_name: e.target.value })} placeholder="变量名" />
          </div>
          <div className="form-group">
            <label className="form-label">比较值</label>
            <input className="form-input" value={params.variable_value || ''} onChange={(e) => onChange({ variable_value: e.target.value })} placeholder="比较值" />
          </div>
        </>
      )}

      <div className="form-group">
        <label className="form-label">描述</label>
        <input className="form-input" value={params.description || ''} onChange={(e) => onChange({ description: e.target.value })} placeholder="条件说明" />
      </div>
    </div>
  );
}
