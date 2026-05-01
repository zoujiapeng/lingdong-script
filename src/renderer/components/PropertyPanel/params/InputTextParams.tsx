import React from 'react';
import { InputTextNodeParams } from '../../../types';

interface Props {
  params: InputTextNodeParams;
  onChange: (p: Partial<InputTextNodeParams>) => void;
}

export function InputTextParams({ params, onChange }: Props) {
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
        <div className="form-group">
          <label className="form-label">X 坐标 (可选)</label>
          <input className="form-input" type="number" value={params.x ?? ''} onChange={(e) => onChange({ x: e.target.value ? +e.target.value : undefined })} placeholder="选填" />
        </div>
        <div className="form-group">
          <label className="form-label">Y 坐标 (可选)</label>
          <input className="form-input" type="number" value={params.y ?? ''} onChange={(e) => onChange({ y: e.target.value ? +e.target.value : undefined })} placeholder="选填" />
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
