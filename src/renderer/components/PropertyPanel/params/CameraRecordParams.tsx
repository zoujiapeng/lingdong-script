import React from 'react';

interface CameraRecordParams {
  camera_id: number;
  output_path: string;
  fps?: number;
  duration?: number;
  width?: number;
  height?: number;
  description?: string;
}

interface Props {
  params: CameraRecordParams;
  onChange: (p: Partial<CameraRecordParams>) => void;
}

export function CameraRecordParams({ params, onChange }: Props) {
  return (
    <div>
      <div className="form-group">
        <label className="form-label">摄像头编号</label>
        <input
          className="form-input"
          type="number"
          min={0}
          value={params.camera_id ?? 0}
          onChange={(e) => onChange({ camera_id: parseInt(e.target.value) || 0 })}
        />
      </div>
      <div className="form-group">
        <label className="form-label">输出路径</label>
        <input
          className="form-input"
          value={params.output_path || ''}
          onChange={(e) => onChange({ output_path: e.target.value })}
          placeholder="例如: C:\录制\cam_video.avi"
        />
      </div>
      <div className="form-row">
        <div className="form-group">
          <label className="form-label">帧率(FPS)</label>
          <input
            className="form-input"
            type="number"
            min={1}
            max={60}
            value={params.fps ?? 15}
            onChange={(e) => onChange({ fps: parseInt(e.target.value) || 15 })}
          />
        </div>
        <div className="form-group">
          <label className="form-label">时长（秒）</label>
          <input
            className="form-input"
            type="number"
            min={0}
            value={params.duration ?? 0}
            onChange={(e) => onChange({ duration: parseInt(e.target.value) || 0 })}
          />
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>0 = 不限制（直到手动停止）</div>
        </div>
      </div>
      <div className="form-row">
        <div className="form-group">
          <label className="form-label">宽度</label>
          <input
            className="form-input"
            type="number"
            min={0}
            value={params.width || ''}
            onChange={(e) => onChange({ width: e.target.value ? parseInt(e.target.value) : undefined })}
            placeholder="0=默认"
          />
        </div>
        <div className="form-group">
          <label className="form-label">高度</label>
          <input
            className="form-input"
            type="number"
            min={0}
            value={params.height || ''}
            onChange={(e) => onChange({ height: e.target.value ? parseInt(e.target.value) : undefined })}
            placeholder="0=默认"
          />
        </div>
      </div>
      <div className="form-group">
        <label className="form-label">描述</label>
        <input className="form-input" value={params.description || ''} onChange={(e) => onChange({ description: e.target.value })} placeholder="例如: 录制摄像头监控" />
      </div>
    </div>
  );
}
