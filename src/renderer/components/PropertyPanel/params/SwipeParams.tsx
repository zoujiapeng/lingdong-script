import React from 'react';
import { SwipeNodeParams } from '../../../types';

interface Props {
  params: SwipeNodeParams;
  onChange: (p: Partial<SwipeNodeParams>) => void;
}

export function SwipeParams({ params, onChange }: Props) {
  return (
    <div>
      <div className="form-group">
        <label className="form-label">起点坐标</label>
        <div className="form-row">
          <div>
            <label className="form-label" style={{ fontSize: 11, marginBottom: 2 }}>X</label>
            <input className="form-input" type="number" value={params.start_x} onChange={(e) => onChange({ start_x: +e.target.value })} />
          </div>
          <div>
            <label className="form-label" style={{ fontSize: 11, marginBottom: 2 }}>Y</label>
            <input className="form-input" type="number" value={params.start_y} onChange={(e) => onChange({ start_y: +e.target.value })} />
          </div>
        </div>
      </div>

      <div className="form-group">
        <label className="form-label">终点坐标</label>
        <div className="form-row">
          <div>
            <label className="form-label" style={{ fontSize: 11, marginBottom: 2 }}>X</label>
            <input className="form-input" type="number" value={params.end_x} onChange={(e) => onChange({ end_x: +e.target.value })} />
          </div>
          <div>
            <label className="form-label" style={{ fontSize: 11, marginBottom: 2 }}>Y</label>
            <input className="form-input" type="number" value={params.end_y} onChange={(e) => onChange({ end_y: +e.target.value })} />
          </div>
        </div>
      </div>

      <div className="form-group">
        <label className="form-label">滑动时长 (秒)</label>
        <div className="slider-group">
          <input type="range" min="0.1" max="5" step="0.1" value={params.duration} onChange={(e) => onChange({ duration: +e.target.value })} />
          <span className="slider-value">{params.duration.toFixed(1)}s</span>
        </div>
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
        <input className="form-input" value={params.description || ''} onChange={(e) => onChange({ description: e.target.value })} placeholder="例如: 向右滑动翻页" />
      </div>
    </div>
  );
}
