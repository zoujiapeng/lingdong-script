import { useState } from 'react';
import { Crosshair, Monitor } from 'lucide-react';
import type { ScreenshotNodeParams } from '../../../types';

interface Props {
  params: ScreenshotNodeParams;
  onChange: (patch: Partial<ScreenshotNodeParams>) => void;
  onCommit?: () => void;
}

function isRegion(value: unknown): value is NonNullable<ScreenshotNodeParams['region']> {
  if (!value || typeof value !== 'object') return false;
  const region = value as Record<string, unknown>;
  return ['x', 'y', 'w', 'h'].every((key) => (
    typeof region[key] === 'number' && Number.isSafeInteger(region[key])
  )) && Number(region.w) > 0 && Number(region.h) > 0;
}

export function ScreenshotParams({ params, onChange, onCommit }: Props) {
  const [selecting, setSelecting] = useState(false);
  const [error, setError] = useState('');
  const region = params.region;

  const selectRegion = async () => {
    setSelecting(true);
    setError('');
    try {
      const result = await window.electronAPI?.selectRegion?.({ includePreview: false });
      if (result == null) return;
      if ('error' in result) {
        setError(result.error);
        return;
      }
      if (result.coordinate_space !== 'screen_physical_px') {
        setError('选区坐标系不明确，原参数未改变');
        return;
      }
      const selected = { x: result.x, y: result.y, w: result.w, h: result.h };
      if (!isRegion(selected)) {
        setError('选区坐标无效，原参数未改变');
        return;
      }
      onChange({ region: selected });
      onCommit?.();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSelecting(false);
    }
  };

  const updateCoordinate = (key: 'x' | 'y' | 'w' | 'h', value: number) => {
    const current = region || { x: 0, y: 0, w: 1, h: 1 };
    onChange({ region: { ...current, [key]: value } });
  };

  const useFullScreen = () => {
    if (!region) return;
    onChange({ region: undefined });
    onCommit?.();
  };

  return (
    <div>
      <div className="form-group">
        <label className="form-label">截取范围</label>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            className={`btn ${region ? 'btn-ghost' : 'btn-primary'}`}
            type="button"
            onClick={useFullScreen}
            style={{ flex: 1 }}
          >
            <Monitor size={14} /> 全屏
          </button>
          <button
            className={`btn ${region ? 'btn-primary' : 'btn-ghost'}`}
            type="button"
            onClick={selectRegion}
            disabled={selecting}
            style={{ flex: 1 }}
          >
            <Crosshair size={14} /> {selecting ? '正在框选…' : '框选区域'}
          </button>
        </div>
      </div>

      {region && (
        <div className="form-group">
          <label className="form-label">物理屏幕坐标</label>
          <div className="form-row">
            {(['x', 'y', 'w', 'h'] as const).map((key) => (
              <div className="form-group" style={{ flex: 1 }} key={key}>
                <label className="form-label" style={{ fontSize: 11 }}>
                  {{ x: 'X', y: 'Y', w: '宽', h: '高' }[key]}
                </label>
                <input
                  aria-label={`截图区域${{ x: 'X', y: 'Y', w: '宽度', h: '高度' }[key]}`}
                  className="form-input"
                  type="number"
                  min={key === 'w' || key === 'h' ? 1 : undefined}
                  step={1}
                  value={region[key]}
                  onChange={(event) => updateCoordinate(key, Number(event.target.value))}
                />
              </div>
            ))}
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', lineHeight: 1.5 }}>
            坐标按物理像素保存；运行时会重新截取当前区域，不会重用编辑时预览。
          </div>
        </div>
      )}

      {error && <div role="alert" style={{ fontSize: 11, color: 'var(--danger)', lineHeight: 1.5 }}>{error}</div>}
      <div style={{ fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.6 }}>
        截图由当前运行统一保存为受管证据文件。
      </div>
    </div>
  );
}
