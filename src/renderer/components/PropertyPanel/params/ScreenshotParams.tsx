import React from 'react';

interface ScreenshotNodeParams {
  quality?: number;
  description?: string;
}

interface Props {
  params: ScreenshotNodeParams;
  onChange: (p: Partial<ScreenshotNodeParams>) => void;
}

export function ScreenshotParams({ params, onChange }: Props) {
  return (
    <div>
      <div className="form-group">
        <label className="form-label">图像质量 ({params.quality ?? 85})</label>
        <input
          type="range"
          min="40"
          max="100"
          value={params.quality ?? 85}
          onChange={(event) => onChange({ quality: Number(event.target.value) })}
        />
      </div>
      <div className="form-group">
        <label className="form-label">描述</label>
        <input
          className="form-input"
          value={params.description || ''}
          onChange={(event) => onChange({ description: event.target.value })}
          placeholder="例如：记录当前界面证据"
        />
      </div>
    </div>
  );
}
