import React from 'react';
import { WaitNodeParams, WaitType } from '../../../types';

interface Props {
  params: WaitNodeParams;
  onChange: (p: Partial<WaitNodeParams>) => void;
}

export function WaitParams({ params, onChange }: Props) {
  return (
    <div>
      <div className="form-group">
        <label className="form-label">等待类型</label>
        <select className="form-input form-select" value={params.wait_type} onChange={(e) => onChange({ wait_type: e.target.value as WaitType })}>
          <option value="time">等待时间</option>
          <option value="image">等待图片出现</option>
          <option value="window">等待窗口出现</option>
        </select>
      </div>

      {params.wait_type === 'time' && (
        <div className="form-group">
          <label className="form-label">等待时长 (秒)</label>
          <div className="slider-group">
            <input type="range" min="0.1" max="60" step="0.1" value={params.duration || 1} onChange={(e) => onChange({ duration: +e.target.value })} />
            <span className="slider-value">{(params.duration || 1).toFixed(1)}s</span>
          </div>
        </div>
      )}

      {params.wait_type === 'image' && (
        <>
          <div className="form-group">
            <label className="form-label">目标图片</label>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', padding: '12px', border: '1px dashed var(--border-primary)', borderRadius: 'var(--radius-md)', textAlign: 'center' }}>
              暂未实现文件选择器
            </div>
          </div>
          <div className="form-group">
            <label className="form-label">相似度 ({params.similarity || 90}%)</label>
            <div className="slider-group">
              <input type="range" min="50" max="100" value={params.similarity || 90} onChange={(e) => onChange({ similarity: +e.target.value })} />
              <span className="slider-value">{params.similarity || 90}%</span>
            </div>
          </div>
          <div className="form-group">
            <label className="form-label">超时 (秒)</label>
            <input className="form-input" type="number" value={params.timeout || 10} onChange={(e) => onChange({ timeout: +e.target.value })} />
          </div>
        </>
      )}

      {params.wait_type === 'window' && (
        <div className="form-group">
          <label className="form-label">窗口标题</label>
          <input className="form-input" value={params.window_title || ''} onChange={(e) => onChange({ window_title: e.target.value })} placeholder="例如: 记事本" />
        </div>
      )}

      <div className="form-group">
        <label className="form-label">描述</label>
        <input className="form-input" value={params.description || ''} onChange={(e) => onChange({ description: e.target.value })} placeholder="等待说明" />
      </div>
    </div>
  );
}
