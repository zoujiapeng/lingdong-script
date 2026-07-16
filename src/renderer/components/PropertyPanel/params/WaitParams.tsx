import type { WaitNodeParams, WaitType } from '../../../types';

interface Props {
  params: WaitNodeParams;
  onChange: (params: Partial<WaitNodeParams>) => void;
}

export function WaitParams({ params, onChange }: Props) {
  const supported = params.wait_type === 'time' || params.wait_type === 'window';
  return (
    <div>
      <div className="form-group">
        <label className="form-label">等待类型</label>
        <select
          className="form-input form-select"
          value={supported ? params.wait_type : ''}
          onChange={(event) => onChange({ wait_type: event.target.value as WaitType })}
        >
          {!supported && <option value="" disabled>历史等待类型不受 ARP 支持</option>}
          <option value="time">等待时间</option>
          <option value="window">等待窗口出现</option>
        </select>
      </div>

      {!supported && (
        <div className="arp-inline-error">请选择时间或窗口等待以迁移该历史节点。</div>
      )}

      {params.wait_type === 'time' && (
        <div className="form-group">
          <label className="form-label">等待时长（秒）</label>
          <div className="slider-group">
            <input
              type="range"
              min="0.1"
              max="60"
              step="0.1"
              value={params.duration || 1}
              onChange={(event) => onChange({ duration: Number(event.target.value) })}
            />
            <span className="slider-value">{(params.duration || 1).toFixed(1)}s</span>
          </div>
        </div>
      )}

      {params.wait_type === 'window' && (
        <div className="form-group">
          <label className="form-label">窗口标题</label>
          <input
            className="form-input"
            value={params.window_title || ''}
            onChange={(event) => onChange({ window_title: event.target.value })}
            placeholder="例如：记事本"
          />
        </div>
      )}

      <div className="form-group">
        <label className="form-label">描述</label>
        <input
          className="form-input"
          value={params.description || ''}
          onChange={(event) => onChange({ description: event.target.value })}
          placeholder="等待说明"
        />
      </div>
    </div>
  );
}
