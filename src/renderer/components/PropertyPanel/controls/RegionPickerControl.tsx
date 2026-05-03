import React, { useState } from 'react';
import { Crosshair } from 'lucide-react';

export interface RegionValue {
  x: number;
  y: number;
  w: number;
  h: number;
}

interface Props {
  /** 当前区域值 */
  value: RegionValue;
  /** 区域值变更回调 */
  onChange: (region: RegionValue) => void;
  /** 输入框标签 */
  label?: string;
  /** 是否只显示坐标(x,y)不显示宽高 */
  coordOnly?: boolean;
}

/** 区域拾取控件：输入框 + 框选按钮，调用 electronAPI.selectRegion() */
export function RegionPickerControl({ value, onChange, label = '区域', coordOnly = false }: Props) {
  const [selecting, setSelecting] = useState(false);

  const handleSelectRegion = async () => {
    try {
      setSelecting(true);
      const api = (window as any).electronAPI;
      if (api?.selectRegion) {
        const result = await api.selectRegion();
        if (result && typeof result.x === 'number') {
          onChange({ x: result.x, y: result.y, w: result.w ?? value.w, h: result.h ?? value.h });
        }
      }
    } catch (err: any) {
      console.error('选区失败:', err);
    } finally {
      setSelecting(false);
    }
  };

  const set = (patch: Partial<RegionValue>) => onChange({ ...value, ...patch });

  return (
    <div className="form-group">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <label className="form-label" style={{ margin: 0 }}>{label}</label>
        <button
          className="btn btn-ghost btn-icon"
          onClick={handleSelectRegion}
          disabled={selecting}
          title="框选区域"
          style={{ width: 28, height: 28 }}
        >
          <Crosshair size={14} />
        </button>
      </div>
      <div className="form-row" style={{ marginTop: 4 }}>
        <div className="form-group" style={{ flex: 1 }}>
          <label className="form-label" style={{ fontSize: 11 }}>X</label>
          <input className="form-input" type="number" value={value.x} onChange={(e) => set({ x: +e.target.value })} />
        </div>
        <div className="form-group" style={{ flex: 1 }}>
          <label className="form-label" style={{ fontSize: 11 }}>Y</label>
          <input className="form-input" type="number" value={value.y} onChange={(e) => set({ y: +e.target.value })} />
        </div>
        {!coordOnly && (
          <>
            <div className="form-group" style={{ flex: 1 }}>
              <label className="form-label" style={{ fontSize: 11 }}>宽度</label>
              <input className="form-input" type="number" value={value.w} onChange={(e) => set({ w: +e.target.value })} />
            </div>
            <div className="form-group" style={{ flex: 1 }}>
              <label className="form-label" style={{ fontSize: 11 }}>高度</label>
              <input className="form-input" type="number" value={value.h} onChange={(e) => set({ h: +e.target.value })} />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
