import React, { useMemo, useState } from 'react';
import { useStore } from '../../../store';
import { ToolCallNodeParams, FeatureDefinition } from '../../../types';

interface Props {
  params: ToolCallNodeParams;
  onChange: (p: Partial<ToolCallNodeParams>) => void;
}

const CAT_LABELS: Record<string, string> = {
  action: '交互动作',
  perception: '感知识别',
  control: '控制流',
  macro: '宏指令',
  ai: 'AI 规划',
  system: '系统功能',
  debug: '调试诊断',
};

const CAT_ORDER = ['action', 'perception', 'control', 'macro', 'ai', 'system', 'debug'];

export function ToolCallParams({ params, onChange }: Props) {
  const { featureList } = useStore();
  const [testResult, setTestResult] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);

  const grouped = useMemo(() => {
    const groups: Record<string, FeatureDefinition[]> = {};
    for (const f of featureList) {
      if (f.deprecated) continue;
      (groups[f.category] ||= []).push(f);
    }
    return groups;
  }, [featureList]);

  const selectedFeature = useMemo(
    () => featureList.find((f) => f.name === params.feature_name),
    [featureList, params.feature_name]
  );

  const handleFeatureChange = (name: string) => {
    onChange({ feature_name: name, params: {} });
  };

  const handleParamChange = (paramName: string, value: any) => {
    onChange({
      ...params,
      params: { ...params.params, [paramName]: value },
    });
  };

  const handleTestExecute = async () => {
    setTesting(true);
    setTestResult(null);
    const store = useStore.getState();
    const result = await store.callFeatureDirect(params.feature_name, selectedFeature?.display_name || params.feature_name, params.params);
    setTestResult(JSON.stringify(result, null, 2));
    setTesting(false);
  };

  const renderParamField = (p: FeatureDefinition['params'][0]) => {
    const val = params.params[p.name];
    const type = p.type;

    if (type === 'boolean') {
      return (
        <div key={p.name} className="form-group" style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <label className="form-label" style={{ marginBottom: 0 }}>{p.name}</label>
          <input type="checkbox" checked={!!val} onChange={(e) => handleParamChange(p.name, e.target.checked)} />
          {!p.required && <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>(可选)</span>}
        </div>
      );
    }

    if (type === 'number' || type === 'float' || type === 'int') {
      return (
        <div key={p.name} className="form-group">
          <label className="form-label">
            {p.name}
            {p.required && <span style={{ color: 'var(--accent-red)', marginLeft: 2 }}>*</span>}
            {!p.required && <span style={{ fontSize: 10, color: 'var(--text-muted)', marginLeft: 4 }}>(可选)</span>}
          </label>
          <input className="form-input" type="number"
            value={val ?? p.default ?? ''}
            onChange={(e) => handleParamChange(p.name, e.target.value === '' ? '' : +e.target.value)}
            placeholder={p.default != null ? String(p.default) : ''} />
          {p.description && <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 2 }}>{p.description}</div>}
        </div>
      );
    }

    // Default: text/string
    return (
      <div key={p.name} className="form-group">
        <label className="form-label">
          {p.name}
          {p.required && <span style={{ color: 'var(--accent-red)', marginLeft: 2 }}>*</span>}
          {!p.required && <span style={{ fontSize: 10, color: 'var(--text-muted)', marginLeft: 4 }}>(可选)</span>}
        </label>
        <input className="form-input" type="text"
          value={val ?? p.default ?? ''}
          onChange={(e) => handleParamChange(p.name, e.target.value)}
          placeholder={(p.default != null ? String(p.default) : p.example) || ''} />
        {p.description && <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 2 }}>{p.description}</div>}
      </div>
    );
  };

  return (
    <div>
      <div className="form-group">
        <label className="form-label">选择功能</label>
        <select className="form-input form-select"
          value={params.feature_name}
          onChange={(e) => handleFeatureChange(e.target.value)}>
          <option value="">-- 请选择功能 --</option>
          {CAT_ORDER.map((cat) => {
            const items = grouped[cat];
            if (!items?.length) return null;
            return (
              <optgroup key={cat} label={CAT_LABELS[cat] || cat}>
                {items.map((f) => (
                  <option key={f.name} value={f.name}>{f.display_name}</option>
                ))}
              </optgroup>
            );
          })}
        </select>
      </div>

      {selectedFeature && (
        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 8, padding: '6px 8px', background: 'var(--bg-secondary)', borderRadius: 4 }}>
            {selectedFeature.description}
            <div style={{ marginTop: 2, fontSize: 10 }}>返回值: {selectedFeature.returns}</div>
          </div>

          {selectedFeature.params.length === 0 && (
            <div style={{ fontSize: 11, color: 'var(--text-muted)', textAlign: 'center', padding: 8 }}>
              此功能无需参数
            </div>
          )}

          {selectedFeature.params.map(renderParamField)}

          <div style={{ marginTop: 12, display: 'flex', gap: 8, alignItems: 'center' }}>
            <button className="topbar-btn"
              onClick={handleTestExecute}
              disabled={testing}
              style={{ padding: '4px 12px', fontSize: 12 }}>
              {testing ? '执行中...' : '执行测试'}
            </button>
          </div>

          {testResult && (
            <div style={{ marginTop: 8 }}>
              <div className="form-label">执行结果</div>
              <pre style={{
                fontSize: 10, padding: 6, borderRadius: 4,
                background: 'var(--bg-secondary)', color: 'var(--text-primary)',
                maxHeight: 200, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all',
              }}>{testResult}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
