import { useState } from 'react';
import { ColorCheckNodeParams } from '../../../types';
import { Crosshair } from 'lucide-react';

interface Props {
  params: ColorCheckNodeParams;
  onChange: (p: Partial<ColorCheckNodeParams>) => void;
}

export function ColorCheckParams({ params, onChange }: Props) {
  const [selecting, setSelecting] = useState(false);

  const handleSelectRegion = async () => {
    try {
      setSelecting(true);
      const api = (window as any).electronAPI;
      if (api?.selectRegion) {
        const result = await api.selectRegion();
        if (result && typeof result.x === 'number') {
          onChange({ x: result.x, y: result.y, w: result.w, h: result.h });
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
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <label className="form-label" style={{ margin: 0 }}>检测区域</label>
          <button className="btn btn-ghost btn-icon" onClick={handleSelectRegion} disabled={selecting} title="框选检测区域" style={{ width: 28, height: 28 }}>
            <Crosshair size={14} />
          </button>
        </div>
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
