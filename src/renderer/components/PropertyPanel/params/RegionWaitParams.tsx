import React from 'react';
import { RegionWaitNodeParams } from '../../../types';

interface Props {
  params: RegionWaitNodeParams;
  onChange: (p: Partial<RegionWaitNodeParams>) => void;
}

export function RegionWaitParams({ params, onChange }: Props) {
  return (
    <div>
      <div className="form-group">
        <label className="form-label">等待类型</label>
        <select className="form-input form-select" value={params.region_type} onChange={(e) => onChange({ region_type: e.target.value as 'stable' | 'change' })}>
          <option value="stable">区域稳定（不再变化）</option>
          <option value="change">区域发生变化</option>
        </select>
      </div>
      <div className="form-group">
        <label className="form-label">监控区域</label>
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
        <label className="form-label">超时 (秒)</label>
        <div className="slider-group">
          <input type="range" min="1" max="60" step="0.5" value={params.timeout} onChange={(e) => onChange({ timeout: +e.target.value })} />
          <span className="slider-value">{params.timeout}s</span>
        </div>
      </div>
      <div className="form-group">
        <label className="form-label">轮询间隔 (秒)</label>
        <div className="slider-group">
          <input type="range" min="0.1" max="3" step="0.1" value={params.poll_interval} onChange={(e) => onChange({ poll_interval: +e.target.value })} />
          <span className="slider-value">{params.poll_interval.toFixed(1)}s</span>
        </div>
      </div>
      <div className="form-group">
        <label className="form-label">描述</label>
        <input className="form-input" value={params.description || ''} onChange={(e) => onChange({ description: e.target.value })} placeholder="等待加载完成" />
      </div>
    </div>
  );
}
