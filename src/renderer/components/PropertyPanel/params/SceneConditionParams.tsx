import { SceneConditionNodeParams } from '../../../types';

interface Props {
  params: SceneConditionNodeParams;
  onChange: (p: Partial<SceneConditionNodeParams>) => void;
}

export function SceneConditionParams({ params, onChange }: Props) {
  return (
    <div>
      <div className="form-group">
        <label className="form-label">场景名称</label>
        <input className="form-input" value={params.scene_name} onChange={(e) => onChange({ scene_name: e.target.value })} placeholder="例如: 主界面, 战斗, 结算" />
        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
          在此输入场景的名称，引擎将通过 AI 识别当前画面是否匹配
        </div>
      </div>
      <div className="form-group">
        <label className="form-label">匹配相似度 ({params.similarity}%)</label>
        <div className="slider-group">
          <input type="range" min="30" max="100" value={params.similarity} onChange={(e) => onChange({ similarity: +e.target.value })} />
          <span className="slider-value">{params.similarity}%</span>
        </div>
      </div>
      <div className="form-group">
        <label className="form-label">描述</label>
        <input className="form-input" value={params.description || ''} onChange={(e) => onChange({ description: e.target.value })} placeholder="场景判断" />
      </div>
    </div>
  );
}
