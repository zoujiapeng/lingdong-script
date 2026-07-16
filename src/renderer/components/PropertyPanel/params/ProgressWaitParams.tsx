import { useState } from 'react';
import { ProgressWaitNodeParams } from '../../../types';
import { Crosshair } from 'lucide-react';

interface Props {
  params: ProgressWaitNodeParams;
  onChange: (p: Partial<ProgressWaitNodeParams>) => void;
}

export function ProgressWaitParams({ params, onChange }: Props) {
  const [selecting, setSelecting] = useState(false);

  const handleSelectRegion = async () => {
    try {
      setSelecting(true);
      const api = (window as any).electronAPI;
      if (api?.selectRegion) {
        const result = await api.selectRegion();
        if (result && typeof result.x === 'number') {
          onChange({ region: { x: result.x, y: result.y, w: result.w, h: result.h } });
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
        <label className="form-label">等待类型</label>
        <select className="form-input form-select" value={params.wait_type} onChange={(e) => onChange({ wait_type: e.target.value as 'fill' | 'empty' | 'change' })}>
          <option value="fill">进度条填满</option>
          <option value="empty">进度条清空</option>
          <option value="change">进度条变化</option>
        </select>
      </div>
      <div className="form-group">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <label className="form-label" style={{ margin: 0 }}>进度条区域</label>
          <button className="btn btn-ghost btn-icon" onClick={handleSelectRegion} disabled={selecting} title="框选监控区域" style={{ width: 28, height: 28 }}>
            <Crosshair size={14} />
          </button>
        </div>
        <div className="form-row">
          <div className="form-group" style={{ flex: 1 }}>
            <label className="form-label" style={{ fontSize: 11 }}>X</label>
            <input className="form-input" type="number" value={params.region?.x ?? 0} onChange={(e) => onChange({ region: { ...params.region, x: +e.target.value } })} />
          </div>
          <div className="form-group" style={{ flex: 1 }}>
            <label className="form-label" style={{ fontSize: 11 }}>Y</label>
            <input className="form-input" type="number" value={params.region?.y ?? 0} onChange={(e) => onChange({ region: { ...params.region, y: +e.target.value } })} />
          </div>
          <div className="form-group" style={{ flex: 1 }}>
            <label className="form-label" style={{ fontSize: 11 }}>宽度</label>
            <input className="form-input" type="number" value={params.region?.w ?? 200} onChange={(e) => onChange({ region: { ...params.region, w: +e.target.value } })} />
          </div>
          <div className="form-group" style={{ flex: 1 }}>
            <label className="form-label" style={{ fontSize: 11 }}>高度</label>
            <input className="form-input" type="number" value={params.region?.h ?? 20} onChange={(e) => onChange({ region: { ...params.region, h: +e.target.value } })} />
          </div>
        </div>
      </div>
      <div className="form-group">
        <label className="form-label">超时 (秒)</label>
        <div className="slider-group">
          <input type="range" min="1" max="120" step="1" value={params.timeout} onChange={(e) => onChange({ timeout: +e.target.value })} />
          <span className="slider-value">{params.timeout}s</span>
        </div>
      </div>
      <div className="form-group">
        <label className="form-label">轮询间隔 (秒)</label>
        <div className="slider-group">
          <input type="range" min="0.1" max="3" step="0.1" value={params.poll_interval} onChange={(e) => onChange({ poll_interval: +e.target.value })} />
          <span className="slider-value">{(params.poll_interval ?? 0.5).toFixed(1)}s</span>
        </div>
      </div>
      <div className="form-group">
        <label className="form-label">描述</label>
        <input className="form-input" value={params.description || ''} onChange={(e) => onChange({ description: e.target.value })} placeholder="等待加载完成" />
      </div>
    </div>
  );
}
