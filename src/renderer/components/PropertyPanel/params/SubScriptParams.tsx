import React from 'react';
import { SubScriptNodeParams } from '../../../types';

interface Props {
  params: SubScriptNodeParams;
  onChange: (p: Partial<SubScriptNodeParams>) => void;
}

export function SubScriptParams({ params, onChange }: Props) {
  return (
    <div>
      <div className="form-group">
        <label className="form-label">子脚本文件</label>
        <div style={{
          padding: '16px', border: '2px dashed var(--border-primary)',
          borderRadius: 'var(--radius-md)', textAlign: 'center',
          cursor: 'pointer', transition: 'all var(--transition-fast)',
        }}
          onClick={() => {
            const input = document.createElement('input');
            input.type = 'file';
            input.accept = '.json';
            input.onchange = (e) => {
              const file = (e.target as HTMLInputElement).files?.[0];
              if (!file) return;
              const reader = new FileReader();
              reader.onload = (ev) => {
                try {
                  const data = JSON.parse(ev.target?.result as string);
                  onChange({ script_id: data.meta?.id || file.name, script_name: data.meta?.name || file.name });
                } catch {
                  alert('无效的脚本文件');
                }
              };
              reader.readAsText(file);
            };
            input.click();
          }}
        >
          <div style={{ fontSize: 24, marginBottom: 8, opacity: 0.4 }}>📂</div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            {params.script_name && params.script_name !== '未选择'
              ? `已选择: ${params.script_name}`
              : '点击选择脚本文件 (.json)'}
          </div>
        </div>
      </div>

      {(params.script_name && params.script_name !== '未选择') && (
        <div className="form-group">
          <label className="form-label">脚本 ID</label>
          <input className="form-input" value={params.script_id} onChange={(e) => onChange({ script_id: e.target.value })} />
        </div>
      )}

      <div className="form-group">
        <label className="form-label">描述</label>
        <input className="form-input" value={params.description || ''} onChange={(e) => onChange({ description: e.target.value })} placeholder="子脚本说明" />
      </div>
    </div>
  );
}
