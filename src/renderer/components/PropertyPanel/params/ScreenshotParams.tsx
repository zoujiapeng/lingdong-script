import React from 'react';
import { RegionPickerControl } from '../controls/RegionPickerControl';

interface ScreenshotNodeParams {
  region?: { x: number; y: number; w: number; h: number };
  save_path?: string;
  format?: 'png' | 'jpg';
  description?: string;
}

interface Props {
  params: ScreenshotNodeParams;
  onChange: (p: Partial<ScreenshotNodeParams>) => void;
}

export function ScreenshotParams({ params, onChange }: Props) {
  const region = params.region ?? { x: 0, y: 0, w: 0, h: 0 };

  return (
    <div>
      <RegionPickerControl
        value={region}
        onChange={(r) => onChange({ region: r })}
        label="截图区域（留空=全屏）"
      />
      <div className="form-group">
        <label className="form-label">保存路径（可选）</label>
        <input
          className="form-input"
          value={params.save_path || ''}
          onChange={(e) => onChange({ save_path: e.target.value })}
          placeholder="留空自动生成: screenshot_时间戳.png"
        />
      </div>
      <div className="form-group">
        <label className="form-label">图片格式</label>
        <select
          className="form-input form-select"
          value={params.format || 'png'}
          onChange={(e) => onChange({ format: e.target.value as 'png' | 'jpg' })}
        >
          <option value="png">PNG（无损）</option>
          <option value="jpg">JPG（压缩）</option>
        </select>
      </div>
      <div className="form-group">
        <label className="form-label">描述</label>
        <input className="form-input" value={params.description || ''} onChange={(e) => onChange({ description: e.target.value })} placeholder="例如: 截取当前页面" />
      </div>
    </div>
  );
}
