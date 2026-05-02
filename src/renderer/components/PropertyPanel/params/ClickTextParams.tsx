import React from 'react';
import { ClickTextNodeParams, ClickMethod } from '../../../types';

interface Props {
  params: ClickTextNodeParams;
  onChange: (p: Partial<ClickTextNodeParams>) => void;
}

export function ClickTextParams({ params, onChange }: Props) {
  return (
    <div>
      <div className="form-group">
        <label className="form-label">目标文本</label>
        <input className="form-input" value={params.text} onChange={(e) => onChange({ text: e.target.value })} placeholder="例如: 领取奖励" />
      </div>
      <div className="form-group">
        <label className="form-label">识别相似度 ({params.similarity}%)</label>
        <div className="slider-group">
          <input type="range" min="50" max="100" value={params.similarity} onChange={(e) => onChange({ similarity: +e.target.value })} />
          <span className="slider-value">{params.similarity}%</span>
        </div>
      </div>
      <div className="form-group">
        <label className="form-label">点击方式</label>
        <select className="form-input form-select" value={params.method} onChange={(e) => onChange({ method: e.target.value as ClickMethod })}>
          <option value="single">单击</option>
          <option value="double">双击</option>
          <option value="right">右键</option>
        </select>
      </div>
      <div className="form-row">
        <div className="form-group">
          <label className="form-label">偏移 X</label>
          <input className="form-input" type="number" value={params.offset_x || 0} onChange={(e) => onChange({ offset_x: +e.target.value })} />
        </div>
        <div className="form-group">
          <label className="form-label">偏移 Y</label>
          <input className="form-input" type="number" value={params.offset_y || 0} onChange={(e) => onChange({ offset_y: +e.target.value })} />
        </div>
      </div>
      <div className="form-group">
        <label className="form-label">超时 (秒)</label>
        <div className="slider-group">
          <input type="range" min="1" max="30" step="0.5" value={params.timeout} onChange={(e) => onChange({ timeout: +e.target.value })} />
          <span className="slider-value">{params.timeout}s</span>
        </div>
      </div>
      <div className="form-group">
        <label className="form-label">点击后延时 (秒)</label>
        <div className="slider-group">
          <input type="range" min="0" max="5" step="0.1" value={params.delay_after} onChange={(e) => onChange({ delay_after: +e.target.value })} />
          <span className="slider-value">{params.delay_after.toFixed(1)}</span>
        </div>
      </div>
      <div className="form-group">
        <label className="form-label">描述</label>
        <input className="form-input" value={params.description || ''} onChange={(e) => onChange({ description: e.target.value })} placeholder="例如: 点击领取按钮" />
      </div>
    </div>
  );
}
