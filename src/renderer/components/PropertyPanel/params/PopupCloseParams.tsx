import React from 'react';
import { PopupCloseNodeParams } from '../../../types';
import { WheelSelectControl } from '../controls/WheelSelectControl';

interface Props {
  params: PopupCloseNodeParams;
  onChange: (p: Partial<PopupCloseNodeParams>) => void;
}

const STRATEGY_OPTIONS = [
  { value: 'auto', label: '自动检测并关闭' },
  { value: 'close_btn', label: '点击关闭按钮 (X)' },
  { value: 'ocr_close', label: 'OCR 识别"关闭"并点击' },
  { value: 'esc', label: '按 ESC 键' },
  { value: 'click_away', label: '点击弹窗外部' },
];

export function PopupCloseParams({ params, onChange }: Props) {
  return (
    <div>
      <WheelSelectControl
        label="关闭策略"
        options={STRATEGY_OPTIONS}
        value={params.strategies?.[0] || 'auto'}
        onChange={(v) => onChange({ strategies: [v] })}
        title="选择关闭策略"
      />
      <div className="form-group">
        <label className="form-label">超时 (秒)</label>
        <div className="slider-group">
          <input type="range" min="1" max="20" step="0.5" value={params.timeout} onChange={(e) => onChange({ timeout: +e.target.value })} />
          <span className="slider-value">{params.timeout}s</span>
        </div>
      </div>
      <div className="form-group">
        <label className="form-label">关闭后延时 (秒)</label>
        <div className="slider-group">
          <input type="range" min="0" max="5" step="0.1" value={params.delay_after} onChange={(e) => onChange({ delay_after: +e.target.value })} />
          <span className="slider-value">{params.delay_after.toFixed(1)}</span>
        </div>
      </div>
      <div className="form-group">
        <label className="form-label">描述</label>
        <input className="form-input" value={params.description || ''} onChange={(e) => onChange({ description: e.target.value })} placeholder="关闭弹窗" />
      </div>
    </div>
  );
}
