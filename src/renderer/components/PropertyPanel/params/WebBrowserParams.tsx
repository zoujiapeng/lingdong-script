import React from 'react';
import { WebBrowserNodeParams } from '../../../types';

interface Props {
  params: WebBrowserNodeParams;
  onChange: (params: any) => void;
}

const ACTIONS = [
  { value: 'navigate', label: '导航到 URL' },
  { value: 'click', label: '点击元素' },
  { value: 'input_text', label: '输入文本' },
  { value: 'screenshot', label: '截图' },
  { value: 'wait', label: '等待' },
  { value: 'extract', label: '提取内容' },
  { value: 'scroll', label: '滚动' },
];

export function WebBrowserParams({ params, onChange }: Props) {
  const set = (field: string, value: any) => onChange({ ...params, [field]: value });

  return (
    <div>
      <div className="form-group">
        <label className="form-label">操作类型</label>
        <select className="form-select" value={params.action} onChange={(e) => set('action', e.target.value)}>
          {ACTIONS.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
        </select>
      </div>

      {params.action === 'navigate' && (
        <div className="form-group">
          <label className="form-label">URL</label>
          <input className="form-input" type="url" value={params.url || ''} onChange={(e) => set('url', e.target.value)} placeholder="https://example.com" />
        </div>
      )}

      {(params.action === 'click' || params.action === 'input_text' || params.action === 'extract') && (
        <>
          <div className="form-group">
            <label className="form-label">CSS 选择器</label>
            <input className="form-input" value={params.selector || ''} onChange={(e) => set('selector', e.target.value)} placeholder=".btn-submit, #search-input" />
          </div>
          {params.action === 'input_text' && (
            <div className="form-group">
              <label className="form-label">输入文本</label>
              <input className="form-input" value={params.text || ''} onChange={(e) => set('text', e.target.value)} placeholder="要输入的文本" />
            </div>
          )}
          {params.action === 'extract' && (
            <div className="form-group">
              <label className="form-label">提取属性</label>
              <input className="form-input" value={params.extract_attr || ''} onChange={(e) => set('extract_attr', e.target.value)} placeholder="textContent, href, src, innerHTML" />
            </div>
          )}
        </>
      )}

      {params.action === 'scroll' && (
        <div className="form-group">
          <label className="form-label">滚动方向</label>
          <select className="form-select" value={params.scroll_direction || 'down'} onChange={(e) => set('scroll_direction', e.target.value)}>
            <option value="down">向下</option>
            <option value="up">向上</option>
          </select>
        </div>
      )}

      {params.action === 'wait' && (
        <div className="form-group">
          <label className="form-label">等待秒数</label>
          <input className="form-input" type="number" min={0.1} step={0.1} value={params.wait_seconds ?? 1} onChange={(e) => set('wait_seconds', parseFloat(e.target.value) || 1)} />
        </div>
      )}

      <div className="form-group">
        <label className="form-label">超时（秒）</label>
        <input className="form-input" type="number" min={1} step={1} value={params.timeout ?? 30} onChange={(e) => set('timeout', parseInt(e.target.value) || 30)} />
      </div>

      <div className="form-group" style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <input type="checkbox" id="web_headless" checked={params.headless || false} onChange={(e) => set('headless', e.target.checked)} />
        <label className="form-label" htmlFor="web_headless" style={{ marginBottom: 0 }}>无头模式（不可见浏览器）</label>
      </div>

      {(params.action === 'click' || params.action === 'input_text') && (
        <div style={{ padding: '8px 10px', background: 'rgba(34,197,94,0.08)', borderRadius: 6, fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
          此操作为桌面+网页混排，浏览器在任务栏可见，不影响桌面自动化操作。
        </div>
      )}
    </div>
  );
}
