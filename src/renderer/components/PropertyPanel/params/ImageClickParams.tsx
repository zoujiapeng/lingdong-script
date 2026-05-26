import React, { useRef, useState } from 'react';
import { ImageClickNodeParams, ClickMethod, ImageTarget } from '../../../types';
import { Camera, ChevronUp, ChevronDown, Trash2, Crosshair } from 'lucide-react';

interface Props {
  params: ImageClickNodeParams;
  onChange: (p: Partial<ImageClickNodeParams>) => void;
}

export function ImageClickParams({ params, onChange }: Props) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [capturing, setCapturing] = useState(false);
  const targets = params.targets || [];

  const handleFileSelect = () => fileRef.current?.click();

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length === 0) return;

    // Sort by filename
    files.sort((a, b) => a.name.localeCompare(b.name));

    const newTargets: ImageTarget[] = [];
    let loaded = 0;

    files.forEach((file) => {
      const reader = new FileReader();
      reader.onload = (ev) => {
        newTargets.push({
          path: file.name,
          base64: ev.target?.result as string,
        });
        loaded++;
        if (loaded === files.length) {
          onChange({ targets: [...targets, ...newTargets] });
        }
      };
      reader.readAsDataURL(file);
    });

    e.target.value = '';
  };

  const handleCapture = async () => {
    try {
      setCapturing(true);
      const api = (window as any).electronAPI;
      if (api?.captureScreenshot) {
        const result = await api.captureScreenshot();
        if (result.dataURL) {
          const ts = new Date().toISOString().replace(/[:.]/g, '-');
          const newTarget: ImageTarget = { path: `截图_${ts}.png`, base64: result.dataURL };
          onChange({ targets: [...targets, newTarget] });
        }
      }
    } catch (err: any) {
      console.error('截图失败:', err);
    } finally {
      setCapturing(false);
    }
  };

  const removeTarget = (idx: number) => {
    const next = targets.filter((_, i) => i !== idx);
    onChange({ targets: next });
  };

  const moveTarget = (idx: number, dir: -1 | 1) => {
    const next = [...targets];
    const swap = idx + dir;
    if (swap < 0 || swap >= next.length) return;
    [next[idx], next[swap]] = [next[swap], next[idx]];
    onChange({ targets: next });
  };

  const replaceTarget = (idx: number) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/*';
    input.onchange = (e: any) => {
      const file = e.target.files?.[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = (ev) => {
        const next = [...targets];
        next[idx] = { path: file.name, base64: ev.target?.result as string };
        onChange({ targets: next });
      };
      reader.readAsDataURL(file);
    };
    input.click();
  };

  const handleSelectRegion = async () => {
    try {
      setCapturing(true);
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
      setCapturing(false);
    }
  };

  return (
    <div>
      {/* Target images */}
      <div className="form-group">
        <label className="form-label">目标图片列表 ({targets.length}张)</label>
        <div
          className="image-upload-area"
          onClick={handleFileSelect}
          style={{ cursor: 'pointer', marginBottom: 8 }}
        >
          <Camera size={20} />
          <span>点击选择多张图片（按文件名排序）</span>
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            multiple
            style={{ display: 'none' }}
            onChange={handleFileChange}
          />
        </div>

        {/* Target list */}
        {targets.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {targets.map((t, i) => (
              <div
                key={t.path}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  padding: '4px 6px',
                  background: 'var(--bg-secondary)',
                  borderRadius: 6,
                  border: '1px solid var(--border-primary)',
                }}
              >
                {/* Thumbnail */}
                {t.base64 ? (
                  <img
                    src={t.base64}
                    alt=""
                    style={{ width: 36, height: 28, objectFit: 'cover', borderRadius: 4, cursor: 'pointer' }}
                    onClick={() => replaceTarget(i)}
                    title="点击替换图片"
                  />
                ) : (
                  <div
                    style={{ width: 36, height: 28, borderRadius: 4, background: 'var(--bg-tertiary)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, color: 'var(--text-muted)' }}
                  >
                    无图
                  </div>
                )}
                {/* Priority */}
                <span style={{ fontSize: 11, color: 'var(--text-muted)', minWidth: 16, textAlign: 'center' }}>#{i + 1}</span>
                {/* Filename */}
                <span style={{ flex: 1, fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {t.path.split(/[\\/]/).pop()}
                </span>
                {/* Reorder */}
                <button className="btn btn-ghost btn-icon" style={{ width: 22, height: 22 }} onClick={() => moveTarget(i, -1)} disabled={i === 0} title="上移">
                  <ChevronUp size={12} />
                </button>
                <button className="btn btn-ghost btn-icon" style={{ width: 22, height: 22 }} onClick={() => moveTarget(i, 1)} disabled={i === targets.length - 1} title="下移">
                  <ChevronDown size={12} />
                </button>
                {/* Delete */}
                <button className="btn btn-ghost btn-icon" style={{ width: 22, height: 22, color: '#ef4444' }} onClick={() => removeTarget(i)} title="移除">
                  <Trash2 size={12} />
                </button>
              </div>
            ))}
          </div>
        )}

        <button
          className="btn btn-secondary"
          onClick={handleCapture}
          disabled={capturing}
          style={{ width: '100%', marginTop: 8, justifyContent: 'center' }}
        >
          <Camera size={14} />
          {capturing ? '截图中...' : '截图添加'}
        </button>
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
          <span className="slider-value">{(params.delay_after ?? 0.5).toFixed(1)}</span>
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
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <label className="form-label" style={{ margin: 0 }}>区域限制 (可选)</label>
          <button className="btn btn-ghost btn-icon" onClick={handleSelectRegion} disabled={capturing} title="框选搜索区域" style={{ width: 28, height: 28 }}>
            <Crosshair size={14} />
          </button>
        </div>
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
        <input className="form-input" value={params.description || ''} onChange={(e) => onChange({ description: e.target.value })} placeholder="例如: 点击登录按钮" />
      </div>
    </div>
  );
}
