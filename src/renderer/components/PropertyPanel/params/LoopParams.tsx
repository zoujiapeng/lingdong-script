import { LoopNodeParams, LoopType } from '../../../types';

interface Props {
  params: LoopNodeParams;
  onChange: (p: Partial<LoopNodeParams>) => void;
}

export function LoopParams({ params, onChange }: Props) {
  return (
    <div>
      <div className="form-group">
        <label className="form-label">循环类型</label>
        <select className="form-input form-select" value={params.loop_type} onChange={(e) => onChange({ loop_type: e.target.value as LoopType })}>
          <option value="count">次数循环</option>
          <option value="condition">条件循环</option>
          <option value="infinite">无限循环</option>
        </select>
      </div>

      {params.loop_type === 'count' && (
        <div className="form-group">
          <label className="form-label">循环次数</label>
          <input className="form-input" type="number" min="1" max="999999" value={params.count || 10} onChange={(e) => onChange({ count: Math.max(1, +e.target.value) })} />
        </div>
      )}

      {params.loop_type === 'condition' && (
        <div style={{ padding: '8px 12px', background: 'rgba(148,163,184,0.06)', borderRadius: 'var(--radius-md)', fontSize: 12, color: 'var(--text-muted)' }}>
          条件循环参数将在高级功能中支持
        </div>
      )}

      {params.loop_type === 'infinite' && (
        <div style={{ padding: '8px 12px', background: 'rgba(245,158,11,0.08)', borderRadius: 'var(--radius-md)', fontSize: 12, color: 'var(--warning)' }}>
          注意：无限循环将永远运行直到手动停止
        </div>
      )}

      <div className="form-group">
        <label className="form-label">描述</label>
        <input className="form-input" value={params.description || ''} onChange={(e) => onChange({ description: e.target.value })} placeholder="循环说明" />
      </div>
    </div>
  );
}
