import React, { useState } from 'react';
import { KeyboardNodeParams, KeyModifier } from '../../../types';

interface Props {
  params: KeyboardNodeParams;
  onChange: (p: Partial<KeyboardNodeParams>) => void;
}

const COMMON_KEYS = [
  'enter', 'tab', 'escape', 'space', 'backspace', 'delete',
  'up', 'down', 'left', 'right',
  'home', 'end', 'pageup', 'pagedown',
  'f1', 'f2', 'f3', 'f4', 'f5', 'f6', 'f7', 'f8', 'f9', 'f10', 'f11', 'f12',
  'a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm',
  'n', 'o', 'p', 'q', 'r', 's', 't', 'u', 'v', 'w', 'x', 'y', 'z',
  '0', '1', '2', '3', '4', '5', '6', '7', '8', '9',
];

export function KeyboardParams({ params, onChange }: Props) {
  const [customKey, setCustomKey] = useState('');

  const addKey = (key: string) => {
    if (!params.keys.includes(key)) {
      onChange({ keys: [...params.keys, key] });
    }
  };

  const removeKey = (key: string) => {
    onChange({ keys: params.keys.filter((k) => k !== key) });
  };

  const toggleModifier = (mod: KeyModifier) => {
    if (params.modifiers.includes(mod)) {
      onChange({ modifiers: params.modifiers.filter((m) => m !== mod) });
    } else {
      onChange({ modifiers: [...params.modifiers, mod] });
    }
  };

  const handleCustomKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && customKey) {
      addKey(customKey.toLowerCase());
      setCustomKey('');
    }
  };

  return (
    <div>
      <div className="form-group">
        <label className="form-label">修饰键</label>
        <div style={{ display: 'flex', gap: 4 }}>
          {(['ctrl', 'alt', 'shift', 'win'] as KeyModifier[]).map((mod) => (
            <button
              key={mod}
              className={`btn btn-ghost`}
              style={{
                height: 28, padding: '0 10px', fontSize: 12,
                background: params.modifiers.includes(mod) ? 'var(--accent-light)' : undefined,
                color: params.modifiers.includes(mod) ? 'var(--accent)' : undefined,
                borderColor: params.modifiers.includes(mod) ? 'var(--accent)' : undefined,
              }}
              onClick={() => toggleModifier(mod)}
            >
              {mod === 'win' ? 'Win' : mod.charAt(0).toUpperCase() + mod.slice(1)}
            </button>
          ))}
        </div>
      </div>

      <div className="form-group">
        <label className="form-label">按键序列</label>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 8 }}>
          {params.keys.map((key) => (
            <span
              key={key}
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 4,
                padding: '2px 8px', background: 'var(--accent-light)',
                color: 'var(--accent)', borderRadius: 'var(--radius-sm)',
                fontSize: 12, fontWeight: 500, border: '1px solid var(--border-hover)',
              }}
            >
              {key}
              <span
                style={{ cursor: 'pointer', opacity: 0.6, fontSize: 14, lineHeight: 1 }}
                onClick={() => removeKey(key)}
              >
                &times;
              </span>
            </span>
          ))}
        </div>

        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3, maxHeight: 120, overflowY: 'auto', marginBottom: 8 }}>
          {COMMON_KEYS.filter((k) => !params.keys.includes(k)).slice(0, 20).map((key) => (
            <button
              key={key}
              style={{
                padding: '2px 8px', fontSize: 11, background: 'transparent',
                border: '1px solid var(--border-primary)', borderRadius: 'var(--radius-sm)',
                cursor: 'pointer', color: 'var(--text-secondary)',
              }}
              onClick={() => addKey(key)}
            >
              {key}
            </button>
          ))}
        </div>

        <input
          className="form-input"
          value={customKey}
          onChange={(e) => setCustomKey(e.target.value)}
          onKeyDown={handleCustomKey}
          placeholder="输入自定义键名后按 Enter 添加"
        />
      </div>

      <div className="form-group">
        <label className="form-label">操作后延时 (秒)</label>
        <div className="slider-group">
          <input type="range" min="0" max="3" step="0.1" value={params.delay_after} onChange={(e) => onChange({ delay_after: +e.target.value })} />
          <span className="slider-value">{params.delay_after.toFixed(1)}</span>
        </div>
      </div>

      <div className="form-group">
        <label className="form-label">描述</label>
        <input className="form-input" value={params.description || ''} onChange={(e) => onChange({ description: e.target.value })} placeholder="例如: 全选后复制" />
      </div>
    </div>
  );
}
