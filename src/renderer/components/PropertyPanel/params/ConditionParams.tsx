import type { ConditionNodeParams, ConditionType } from '../../../types';

interface Props {
  params: ConditionNodeParams;
  onChange: (params: Partial<ConditionNodeParams>) => void;
}

const CONDITION_OPTIONS: Array<{ value: ConditionType; label: string }> = [
  { value: 'variable_equals', label: '变量等于' },
  { value: 'variable_greater', label: '变量大于' },
  { value: 'variable_less', label: '变量小于' },
  { value: 'always_true', label: '始终为真' },
];

export function ConditionParams({ params, onChange }: Props) {
  const supported = CONDITION_OPTIONS.some((option) => option.value === params.condition_type);
  const variableCondition = [
    'variable_equals',
    'variable_greater',
    'variable_less',
  ].includes(params.condition_type);

  return (
    <div>
      <div className="form-group">
        <label className="form-label">条件类型</label>
        <select
          className="form-input form-select"
          value={supported ? params.condition_type : ''}
          onChange={(event) => onChange({ condition_type: event.target.value as ConditionType })}
        >
          {!supported && <option value="" disabled>历史条件不受 ARP 支持</option>}
          {CONDITION_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
        </select>
      </div>

      {!supported && (
        <div className="arp-inline-error">请选择一个可执行条件以迁移该历史节点。</div>
      )}

      {variableCondition && (
        <>
          <div className="form-group">
            <label className="form-label">变量名</label>
            <input
              className="form-input"
              value={params.variable_name || ''}
              onChange={(event) => onChange({ variable_name: event.target.value })}
              placeholder="变量名"
            />
          </div>
          <div className="form-group">
            <label className="form-label">比较值</label>
            <input
              className="form-input"
              value={params.variable_value || ''}
              onChange={(event) => onChange({ variable_value: event.target.value })}
              placeholder="比较值"
            />
          </div>
        </>
      )}

      <div className="form-group">
        <label className="form-label">描述</label>
        <input
          className="form-input"
          value={params.description || ''}
          onChange={(event) => onChange({ description: event.target.value })}
          placeholder="条件说明"
        />
      </div>
    </div>
  );
}
