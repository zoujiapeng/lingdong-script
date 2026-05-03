import React, { useState } from 'react';
import { SwipeNodeParams } from '../../../types';
import { Crosshair } from 'lucide-react';

interface Props {
  params: SwipeNodeParams;
  onChange: (p: Partial<SwipeNodeParams>) => void;
}

export function SwipeParams({ params, onChange }: Props) {
  const [selecting, setSelecting] = useState<'start' | 'end' | null>(null);

  const handleSelectStart = async () => {
    try {
      setSelecting('start');
      const api = (window as any).electronAPI;
      if (api?.selectRegion) {
        const result = await api.selectRegion();
        if (result && typeof result.x === 'number') {
          onChange({ start_x: result.x, start_y: result.y });
        }
      }
    } catch (err: any) {
      console.error('选区失败:', err);
    } finally {
      setSelecting(null);
    }
  };

  const handleSelectEnd = async () => {
    try {
      setSelecting('end');
      const api = (window as any).electronAPI;
      if (api?.selectRegion) {
        const result = await api.selectRegion();
        if (result && typeof result.x === 'number') {
          onChange({ end_x: result.x, end_y: result.y });
        }
      }
    } catch (err: any) {
      console.error('选区失败:', err);
    } finally {
      setSelecting(null);
    }
  };

  return (
    <div>
      <div className="form-group">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <label className="form-label" style={{ margin: 0 }}>起点坐标</label>
          <button className="btn btn-ghost btn-icon" onClick={handleSelectStart} disabled={selecting !== null} title="框选起点" style={{ width: 28, height: 28 }}>
            <Crosshair size={14} />
          </button>
        </div>
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
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <label className="form-label" style={{ margin: 0 }}>终点坐标</label>
          <button className="btn btn-ghost btn-icon" onClick={handleSelectEnd} disabled={selecting !== null} title="框选终点" style={{ width: 28, height: 28 }}>
            <Crosshair size={14} />
          </button>
        </div>
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
