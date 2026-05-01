import React, { useRef } from 'react';
import { ImageClickNodeParams, ClickMethod } from '../../../types';

interface Props {
  params: ImageClickNodeParams;
  onChange: (p: Partial<ImageClickNodeParams>) => void;
}

export function ImageClickParams({ params, onChange }: Props) {
  const fileRef = useRef<HTMLInputElement>(null);

  const handleFileSelect = () => fileRef.current?.click();

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      onChange({ template_base64: ev.target?.result as string, template_path: file.name });
    };
    reader.readAsDataURL(file);
  };

  return (
    <div>
      <div className="form-group">
        <label className="form-label">目标图片</label>
        <div className="image-upload-area" onClick={handleFileSelect}>
          {params.template_base64 ? (
            <div>
              <img src={params.template_base64} alt="template" className="image-upload-preview" />
              <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>{params.template_path}</div>
              <div className="image-upload-actions">
                <button className="btn btn-ghost" style={{ height: 26, fontSize: 11 }} onClick={(e) => { e.stopPropagation(); onChange({ template_base64: undefined, template_path: '' }); }}>
                  移除
                </button>
              </div>
            </div>
          ) : (
            <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
              点击选择截图或图片文件
            </div>
          )}
          <input ref={fileRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={handleFileChange} />
        </div>
      </div>

      <div className="form-group">
        <label className="form-label">相似度阈值 ({params.similarity}%)</label>
        <div className="slider-group">
          <input type="range" min="50" max="100" step="1" value={params.similarity} onChange={(e) => onChange({ similarity: +e.target.value })} />
          <span className="slider-value">{params.similarity}%</span>
        </div>
      </div>

      <div className="form-group">
        <label className="form-label">超时 (秒)</label>
        <input className="form-input" type="number" min="1" max="120" value={params.timeout} onChange={(e) => onChange({ timeout: +e.target.value })} />
      </div>

      <div className="form-group">
        <label className="form-label">点击方式</label>
        <select className="form-input form-select" value={params.method} onChange={(e) => onChange({ method: e.target.value as ClickMethod })}>
          <option value="single">单击</option>
          <option value="double">双击</option>
          <option value="right">右键</option>
          <option value="middle">中键</option>
        </select>
      </div>

      <div className="form-row">
        <div className="form-group">
          <label className="form-label">偏移 X</label>
          <input className="form-input" type="number" value={params.offset_x} onChange={(e) => onChange({ offset_x: +e.target.value })} />
        </div>
        <div className="form-group">
          <label className="form-label">偏移 Y</label>
          <input className="form-input" type="number" value={params.offset_y} onChange={(e) => onChange({ offset_y: +e.target.value })} />
        </div>
      </div>

      <div className="form-group">
        <label className="form-label">点击后延时 (秒)</label>
        <div className="slider-group">
          <input type="range" min="0" max="5" step="0.1" value={params.delay_after} onChange={(e) => onChange({ delay_after: +e.target.value })} />
          <span className="slider-value">{params.delay_after.toFixed(1)}</span>
        </div>
      </div>

      <div className="toggle-group">
        <span className="toggle-label">多尺度匹配</span>
        <button className={`toggle ${params.multi_scale ? 'on' : ''}`} onClick={() => onChange({ multi_scale: !params.multi_scale })} />
      </div>
      <div className="toggle-group">
        <span className="toggle-label">匹配全部目标</span>
        <button className={`toggle ${params.match_all ? 'on' : ''}`} onClick={() => onChange({ match_all: !params.match_all })} />
      </div>

      <hr className="section-divider" />
      <div className="form-group">
        <label className="form-label">区域限制 (可选)</label>
        <div className="form-row">
          <div className="form-group">
            <label className="form-label" style={{ fontSize: 11 }}>X</label>
            <input className="form-input" type="number" value={params.region?.x ?? 0} onChange={(e) => onChange({ region: { ...params.region || { x: 0, y: 0, w: 0, h: 0 }, x: +e.target.value } })} />
          </div>
          <div className="form-group">
            <label className="form-label" style={{ fontSize: 11 }}>Y</label>
            <input className="form-input" type="number" value={params.region?.y ?? 0} onChange={(e) => onChange({ region: { ...params.region || { x: 0, y: 0, w: 0, h: 0 }, y: +e.target.value } })} />
          </div>
        </div>
        <div className="form-row">
          <div className="form-group">
            <label className="form-label" style={{ fontSize: 11 }}>W</label>
            <input className="form-input" type="number" value={params.region?.w ?? 0} onChange={(e) => onChange({ region: { ...params.region || { x: 0, y: 0, w: 0, h: 0 }, w: +e.target.value } })} />
          </div>
          <div className="form-group">
            <label className="form-label" style={{ fontSize: 11 }}>H</label>
            <input className="form-input" type="number" value={params.region?.h ?? 0} onChange={(e) => onChange({ region: { ...params.region || { x: 0, y: 0, w: 0, h: 0 }, h: +e.target.value } })} />
          </div>
        </div>
      </div>

      <div className="form-group">
        <label className="form-label">描述</label>
        <input className="form-input" value={params.description || ''} onChange={(e) => onChange({ description: e.target.value })} placeholder="例如: 点击确认按钮" />
      </div>
    </div>
  );
}
