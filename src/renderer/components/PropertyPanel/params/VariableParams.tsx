import React from 'react';
import { VariableSetNodeParams } from '../../../types';

interface Props {
  params: VariableSetNodeParams;
  onChange: (p: Partial<VariableSetNodeParams>) => void;
  mode: 'set' | 'get';
}

export function VariableParams({ params, onChange, mode }: Props) {
  return (
    <div>
      <div className="form-group">
        <label className="form-label">变量名</label>
        <input
          className="form-input"
          value={params.name || ''}
          onChange={(e) => onChange({ name: e.target.value })}
          placeholder="myVariable"
          style={{ fontFamily: "'JetBrains Mono', monospace" }}
        />
      </div>

      {mode === 'set' && (
        <div className="form-group">
          <label className="form-label">变量值</label>
          <input
            className="form-input"
            value={params.value || ''}
            onChange={(e) => onChange({ value: e.target.value })}
            placeholder="变量值"
          />
        </div>
      )}

      {mode === 'get' && (
        <div style={{
          padding: '8px 12px', background: 'rgba(148,163,184,0.06)',
          borderRadius: 'var(--radius-md)', fontSize: 12, color: 'var(--text-muted)',
        }}>
          读取变量 <code style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>{params.name || '?'}</code> 的值
        </div>
      )}

      <div className="form-group">
        <label className="form-label">描述</label>
        <input className="form-input" value={params.description || ''} onChange={(e) => onChange({ description: e.target.value })} placeholder="变量用途说明" />
      </div>
    </div>
  );
}
