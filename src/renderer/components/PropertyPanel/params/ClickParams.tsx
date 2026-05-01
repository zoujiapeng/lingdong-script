import React from 'react';
import { ClickNodeParams, ClickMethod } from '../../../types';

interface Props {
  params: ClickNodeParams;
  onChange: (p: Partial<ClickNodeParams>) => void;
}

export function ClickParams({ params, onChange }: Props) {
  return (
    <div>
      <div className="form-row">
        <div className="form-group">
          <label className="form-label">X 坐标</label>
          <input className="form-input" type="number" value={params.x} onChange={(e) => onChange({ x: +e.target.value })} />
        </div>
        <div className="form-group">
          <label className="form-label">Y 坐标</label>
          <input className="form-input" type="number" value={params.y} onChange={(e) => onChange({ y: +e.target.value })} />
        </div>
      </div>
      <div className="form-row">
        <div className="form-group">
          <label className="form-label">偏移 X</label>
          <input className="form-input" type="number" value={params.offsetX || 0} onChange={(e) => onChange({ offsetX: +e.target.value })} />
        </div>
        <div className="form-group">
          <label className="form-label">偏移 Y</label>
          <input className="form-input" type="number" value={params.offsetY || 0} onChange={(e) => onChange({ offsetY: +e.target.value })} />
        </div>
      </div>
      <div className="form-group">
        <label className="form-label">点击方式</label>
        <select className="form-input form-select" value={params.method} onChange={(e) => onChange({ method: e.target.value as ClickMethod })}>
          <option value="single">单击</option>
          <option value="double">双击</option>
          <option value="right">右键</option>
          <option value="middle">中键</option>
        </select>
      </div>
      <div className="form-group">
        <label className="form-label">点击后延时 (秒)</label>
        <div className="slider-group">
          <input type="range" min="0" max="5" step="0.1" value={params.delay_after} onChange={(e) => onChange({ delay_after: +e.target.value })} />
          <span className="slider-value">{params.delay_after.toFixed(1)}</span>
        </div>
      </div>
      <div className="form-group">
        <label className="form-label">描述 (可选)</label>
        <input className="form-input" value={params.description || ''} onChange={(e) => onChange({ description: e.target.value })} placeholder="例如: 点击登录按钮" />
      </div>
    </div>
  );
}
