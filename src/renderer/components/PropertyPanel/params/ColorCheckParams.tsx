import React from 'react';
import { ColorCheckNodeParams } from '../../../types';

interface Props {
  params: ColorCheckNodeParams;
  onChange: (p: Partial<ColorCheckNodeParams>) => void;
}

export function ColorCheckParams({ params, onChange }: Props) {
  return (
    <div>
      <div className="form-group">
        <label className="form-label">检测区域</label>
        <div className="form-row">
          <div className="form-group" style={{ flex: 1 }}>
            <label className="form-label" style={{ fontSize: 11 }}>X</label>
            <input className="form-input" type="number" value={params.x} onChange={(e) => onChange({ x: +e.target.value })} />
          </div>
          <div className="form-group" style={{ flex: 1 }}>
            <label className="form-label" style={{ fontSize: 11 }}>Y</label>
            <input className="form-input" type="number" value={params.y} onChange={(e) => onChange({ y: +e.target.value })} />
          </div>
          <div className="form-group" style={{ flex: 1 }}>
            <label className="form-label" style={{ fontSize: 11 }}>宽度</label>
            <input className="form-input" type="number" value={params.w} onChange={(e) => onChange({ w: +e.target.value })} />
          </div>
          <div className="form-group" style={{ flex: 1 }}>
            <label className="form-label" style={{ fontSize: 11 }}>高度</label>
            <input className="form-input" type="number" value={params.h} onChange={(e) => onChange({ h: +e.target.value })} />
          </div>
        </div>
      </div>
      <div className="form-group">
        <label className="form-label">期望颜色 (HSV格式: H,S,V)</label>
        <input className="form-input" value={params.expected_color} onChange={(e) => onChange({ expected_color: e.target.value })} placeholder="例如: 30,200,200" />
        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
          用逗号分隔的 HSV 值: H(0-180), S(0-255), V(0-255)
        </div>
      </div>
      <div className="form-group">
        <label className="form-label">容差 ({params.tolerance})</label>
        <div className="slider-group">
          <input type="range" min="0" max="100" value={params.tolerance} onChange={(e) => onChange({ tolerance: +e.target.value })} />
          <span className="slider-value">{params.tolerance}</span>
        </div>
      </div>
      <div className="form-group">
        <label className="form-label">描述</label>
        <input className="form-input" value={params.description || ''} onChange={(e) => onChange({ description: e.target.value })} placeholder="例如: 检测红色按钮" />
      </div>
    </div>
  );
}
