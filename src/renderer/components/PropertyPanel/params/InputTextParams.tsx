import React, { useState } from 'react';
import { InputTextNodeParams } from '../../../types';
import { Crosshair } from 'lucide-react';

interface Props {
  params: InputTextNodeParams;
  onChange: (p: Partial<InputTextNodeParams>) => void;
}

export function InputTextParams({ params, onChange }: Props) {
  const [selecting, setSelecting] = useState(false);

  const handleSelectRegion = async () => {
    try {
      setSelecting(true);
      const api = (window as any).electronAPI;
      if (api?.selectRegion) {
        const result = await api.selectRegion();
        if (result && typeof result.x === 'number') {
          onChange({ x: result.x, y: result.y });
        }
      }
    } catch (err: any) {
      console.error('选区失败:', err);
    } finally {
      setSelecting(false);
    }
  };

  return (
    <div>
      <div className="form-group">
        <label className="form-label">输入文本</label>
        <textarea
          className="form-input"
          value={params.text}
          onChange={(e) => onChange({ text: e.target.value })}
          placeholder="要输入的文本内容..."
          rows={3}
        />
      </div>

      <div className="form-row">
        <div className="form-group" style={{ flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <label className="form-label" style={{ margin: 0, fontSize: 11 }}>点击位置 (可选)</label>
            <button className="btn btn-ghost btn-icon" onClick={handleSelectRegion} disabled={selecting} title="框选点击位置" style={{ width: 24, height: 24 }}>
              <Crosshair size={12} />
            </button>
          </div>
          <div className="form-row" style={{ marginTop: 2 }}>
            <div className="form-group">
              <input className="form-input" type="number" value={params.x ?? ''} onChange={(e) => onChange({ x: e.target.value ? +e.target.value : undefined })} placeholder="X" />
            </div>
            <div className="form-group">
              <input className="form-input" type="number" value={params.y ?? ''} onChange={(e) => onChange({ y: e.target.value ? +e.target.value : undefined })} placeholder="Y" />
            </div>
          </div>
        </div>
      </div>

      <div className="toggle-group">
        <span className="toggle-label">点击后输入</span>
        <button className={`toggle ${params.click_before ? 'on' : ''}`} onClick={() => onChange({ click_before: !params.click_before })} />
      </div>
      <div className="toggle-group">
        <span className="toggle-label">清空后输入</span>
        <button className={`toggle ${params.clear_before ? 'on' : ''}`} onClick={() => onChange({ clear_before: !params.clear_before })} />
      </div>

      <div className="form-group">
        <label className="form-label">输入后延时 (秒)</label>
        <div className="slider-group">
          <input type="range" min="0" max="3" step="0.1" value={params.delay_after} onChange={(e) => onChange({ delay_after: +e.target.value })} />
          <span className="slider-value">{params.delay_after.toFixed(1)}</span>
        </div>
      </div>

      <div className="form-group">
        <label className="form-label">描述</label>
        <input className="form-input" value={params.description || ''} onChange={(e) => onChange({ description: e.target.value })} placeholder="例如: 输入用户名" />
      </div>
    </div>
  );
}
