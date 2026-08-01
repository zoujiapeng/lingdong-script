import { useState } from 'react';
import { ClickTextNodeParams, ClickMethod } from '../../../types';
import { Crosshair, X } from 'lucide-react';

interface Props {
  params: ClickTextNodeParams;
  onChange: (p: Partial<ClickTextNodeParams>) => void;
}

export function ClickTextParams({ params, onChange }: Props) {
  const [selecting, setSelecting] = useState(false);
  const [ocrResults, setOcrResults] = useState<{ text: string; confidence: number; box: number[] }[] | null>(null);

  const handlePickText = async () => {
    try {
      setSelecting(true);
      setOcrResults(null);
      const api = (window as any).electronAPI;

      // Step 1: Select region on screen
      let region: any;
      if (api?.selectRegion) {
        region = await api.selectRegion();
      }
      if (!region || typeof region.x !== 'number') {
        return;
      }

      // Step 2: OCR the region
      if (api?.ocrRecognizeRegion) {
        const results = await api.ocrRecognizeRegion({
          x: region.x, y: region.y, w: region.w, h: region.h,
        });
        if (Array.isArray(results) && results.length > 0) {
          setOcrResults(results);
        } else {
          setOcrResults([{ text: '(未检测到文字)', confidence: 0, box: [] }]);
        }
      }
    } catch (err: any) {
      console.error('文字选取失败:', err);
    } finally {
      setSelecting(false);
    }
  };

  const handleSelectText = (text: string) => {
    onChange({ text });
    setOcrResults(null);
  };

  return (
    <div>
      <div className="form-group">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <label className="form-label" style={{ margin: 0 }}>目标文本</label>
          <button
            className="btn btn-ghost btn-icon"
            onClick={handlePickText}
            disabled={selecting}
            title="框选屏幕区域并识别文字"
            style={{ width: 28, height: 28 }}
          >
            <Crosshair size={14} />
          </button>
        </div>
        <input
          className="form-input"
          value={params.text}
          onChange={(e) => onChange({ text: e.target.value })}
          placeholder="例如: 领取奖励"
          style={{ marginTop: 4 }}
        />
      </div>

      {/* OCR results picker */}
      {ocrResults && ocrResults.length > 0 && (
        <div className="form-group" style={{
          background: 'var(--bg-secondary)',
          borderRadius: 'var(--radius-md)',
          border: '1px solid var(--border-primary)',
          padding: 8,
          maxHeight: 200,
          overflowY: 'auto',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>识别结果 (点击选取)</span>
            <button className="btn btn-ghost btn-icon" onClick={() => setOcrResults(null)} style={{ width: 20, height: 20 }}>
              <X size={12} />
            </button>
          </div>
          {ocrResults.map((r, i) => (
            <div
              key={i}
              onClick={() => r.text !== '(未检测到文字)' && handleSelectText(r.text)}
              style={{
                padding: '6px 8px',
                cursor: r.text !== '(未检测到文字)' ? 'pointer' : 'default',
                borderRadius: 4,
                fontSize: 13,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                background: i % 2 === 0 ? 'var(--bg-tertiary)' : 'transparent',
              }}
              onMouseEnter={(e) => { if (r.text !== '(未检测到文字)') (e.target as HTMLElement).style.background = 'var(--accent)'; }}
              onMouseLeave={(e) => { (e.target as HTMLElement).style.background = i % 2 === 0 ? 'var(--bg-tertiary)' : 'transparent'; }}
            >
              <span>{r.text}</span>
              <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                {(r.confidence * 100).toFixed(0)}%
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="form-group">
        <label className="form-label">点击方式</label>
        <select className="form-input form-select" value={params.method} onChange={(e) => onChange({ method: e.target.value as ClickMethod })}>
          <option value="single">单击</option>
          <option value="double">双击</option>
          <option value="right">右键</option>
        </select>
      </div>
      <div className="form-group">
        <label className="form-label">点击后延时 (秒)</label>
        <div className="slider-group">
          <input type="range" min="0" max="5" step="0.1" value={params.delay_after} onChange={(e) => onChange({ delay_after: +e.target.value })} />
          <span className="slider-value">{(params.delay_after ?? 0.5).toFixed(1)}</span>
        </div>
      </div>
      <div className="form-group">
        <label className="form-label">描述</label>
        <input className="form-input" value={params.description || ''} onChange={(e) => onChange({ description: e.target.value })} placeholder="例如: 点击领取按钮" />
      </div>
    </div>
  );
}
