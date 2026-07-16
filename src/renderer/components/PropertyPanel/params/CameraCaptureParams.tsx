
interface CameraCaptureParams {
  camera_id: number;
  output_path: string;
  width?: number;
  height?: number;
  format?: 'png' | 'jpg';
  wait_stable?: number;
  description?: string;
}

interface Props {
  params: CameraCaptureParams;
  onChange: (p: Partial<CameraCaptureParams>) => void;
}

export function CameraCaptureParams({ params, onChange }: Props) {
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
        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>0 = 默认摄像头，可通过「列出摄像头」工具查看可用设备</div>
      </div>
      <div className="form-group">
        <label className="form-label">保存路径</label>
        <input
          className="form-input"
          value={params.output_path || ''}
          onChange={(e) => onChange({ output_path: e.target.value })}
          placeholder="例如: C:\摄像头\snapshot.jpg"
        />
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
        <label className="form-label">图片格式</label>
        <select
          className="form-input form-select"
          value={params.format || 'jpg'}
          onChange={(e) => onChange({ format: e.target.value as 'png' | 'jpg' })}
        >
          <option value="jpg">JPG（压缩，速度快）</option>
          <option value="png">PNG（无损，文件大）</option>
        </select>
      </div>
      <div className="form-group">
        <label className="form-label">稳定等待（秒）</label>
        <input
          className="form-input"
          type="number"
          min={0}
          step={0.1}
          value={params.wait_stable ?? 0.5}
          onChange={(e) => onChange({ wait_stable: parseFloat(e.target.value) || 0 })}
        />
        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>等待自动曝光/白平衡稳定后再抓拍</div>
      </div>
      <div className="form-group">
        <label className="form-label">描述</label>
        <input className="form-input" value={params.description || ''} onChange={(e) => onChange({ description: e.target.value })} placeholder="例如: 抓拍当前场景" />
      </div>
    </div>
  );
}
