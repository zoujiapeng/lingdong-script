import React, { useState } from 'react';
import { useStore } from '../store';
import { Settings, Moon, Sun, Grid3x3, Magnet, Eye, Key, Gauge, AlertTriangle, RefreshCw, FileText, CheckCircle, Loader2 } from 'lucide-react';

const KEY_STORAGE = 'lingdong-deepseek-key';
const BASE_STORAGE = 'lingdong-llm-base';
const MODEL_STORAGE = 'lingdong-llm-model';

function getStoredKey(): string {
  try { return localStorage.getItem(KEY_STORAGE) || ''; } catch { return ''; }
}
function getStoredBase(): string {
  try { return localStorage.getItem(BASE_STORAGE) || 'https://api.deepseek.com/v1'; } catch { return 'https://api.deepseek.com/v1'; }
}
function getStoredModel(): string {
  try { return localStorage.getItem(MODEL_STORAGE) || 'deepseek-chat'; } catch { return 'deepseek-chat'; }
}

function storeKey(key: string) {
  try { localStorage.setItem(KEY_STORAGE, key); } catch {}
}
function storeBase(base: string) {
  try { localStorage.setItem(BASE_STORAGE, base); } catch {}
}
function storeModel(model: string) {
  try { localStorage.setItem(MODEL_STORAGE, model); } catch {}
}

export function SettingsPanel() {
  const { script, ui, setUI } = useStore();
  const [apiKey, setApiKey] = useState(() => getStoredKey());
  const [apiBase, setApiBase] = useState(() => getStoredBase());
  const [model, setModel] = useState(() => getStoredModel());
  const [apiKeySaved, setApiKeySaved] = useState(false);
  const [testStatus, setTestStatus] = useState<'idle' | 'testing' | 'success' | 'error'>('idle');
  const [testMessage, setTestMessage] = useState('');

  const handleSaveApiKey = () => {
    storeKey(apiKey);
    storeBase(apiBase);
    storeModel(model);
    (window as any).__DEEPSEEK_KEY__ = apiKey;
    (window as any).__LLM_API_BASE__ = apiBase;
    (window as any).__LLM_MODEL__ = model;
    setApiKeySaved(true);
    setTimeout(() => setApiKeySaved(false), 2000);
  };

  const handleTestKey = async () => {
    if (!apiKey.trim()) return;
    setTestStatus('testing');
    setTestMessage('');
    try {
      const api = (window as any).electronAPI;
      let data: any;
      if (api?.aiGenerate) {
        data = await api.aiGenerate({
          apiKey: apiKey.trim(),
          apiBase: apiBase.trim(),
          model: model.trim(),
          messages: [
            { role: 'system', content: '你是一个助手。请用JSON回答。' },
            { role: 'user', content: '回复一句"连接成功"' },
          ],
        });
        if (data.error) throw new Error(data.error);
      } else {
        const base = apiBase.trim().replace(/\/+$/, '');
        const res = await fetch(`${base}/chat/completions`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${apiKey.trim()}`,
          },
          body: JSON.stringify({
            model: model.trim(),
            messages: [{ role: 'user', content: '回复"连接成功"' }],
            max_tokens: 20,
          }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        data = await res.json();
      }
      setTestStatus('success');
      setTestMessage(data.choices?.[0]?.message?.content?.slice(0, 50) || '连接成功');
    } catch (e: any) {
      setTestStatus('error');
      setTestMessage(e.message);
    }
  };

  return (
    <div className="settings-panel">
      <div className="settings-header">
        <Settings size={18} />
        <span>设置</span>
      </div>

      <div className="settings-body">
        {/* 外观 */}
        <section className="settings-section">
          <h3 className="settings-section-title">外观</h3>

          <div className="settings-row">
            <div className="settings-row-label">
              <Moon size={15} />
              <span>深色模式</span>
            </div>
            <button
              className={`toggle ${ui.dark_mode ? 'on' : ''}`}
              onClick={() => setUI({ dark_mode: !ui.dark_mode })}
            />
          </div>
        </section>

        {/* 网格 */}
        <section className="settings-section">
          <h3 className="settings-section-title">画布网格</h3>

          <div className="settings-row">
            <div className="settings-row-label">
              <Eye size={15} />
              <span>显示网格</span>
            </div>
            <button
              className={`toggle ${ui.show_grid ? 'on' : ''}`}
              onClick={() => setUI({ show_grid: !ui.show_grid })}
            />
          </div>

          <div className="settings-row">
            <div className="settings-row-label">
              <Magnet size={15} />
              <span>吸附到网格</span>
            </div>
            <button
              className={`toggle ${ui.snap_to_grid ? 'on' : ''}`}
              onClick={() => setUI({ snap_to_grid: !ui.snap_to_grid })}
            />
          </div>

          <div className="settings-row">
            <div className="settings-row-label">
              <Grid3x3 size={15} />
              <span>网格大小</span>
            </div>
            <div className="settings-row-input">
              <input
                type="range"
                min="10"
                max="50"
                value={ui.grid_size}
                onChange={(e) => setUI({ grid_size: parseInt(e.target.value) })}
                style={{ width: 120 }}
              />
              <span className="settings-value">{ui.grid_size}px</span>
            </div>
          </div>
        </section>

        {/* LLM API 配置 */}
        <section className="settings-section">
          <h3 className="settings-section-title">
            <Key size={15} style={{ marginRight: 6, verticalAlign: 'middle' }} />
            LLM API 配置
          </h3>
          <div className="settings-row" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 8 }}>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              API 地址（支持 DeepSeek / 硅基流动 / 任意 OpenAI 兼容 API）
            </label>
            <input
              className="settings-input"
              value={apiBase}
              onChange={(e) => setApiBase(e.target.value)}
              placeholder="https://api.deepseek.com/v1"
              style={{ width: '100%' }}
            />
          </div>
          <div className="settings-row" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 8 }}>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              模型名（硅基流动用 deepseek-ai/DeepSeek-V3 等）
            </label>
            <input
              className="settings-input"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="deepseek-chat"
              style={{ width: '100%' }}
            />
          </div>
          <div className="settings-row" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 8 }}>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              API 密钥
            </label>
            <div style={{ display: 'flex', gap: 8 }}>
              <input
                type="password"
                className="settings-input"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="sk-..."
                style={{ flex: 1 }}
              />
              <button className="btn btn-primary" onClick={handleSaveApiKey} style={{ padding: '6px 14px', fontSize: 12 }}>
                {apiKeySaved ? '已保存' : '保存'}
              </button>
              <button className="btn btn-ghost" onClick={handleTestKey} disabled={testStatus === 'testing' || !apiKey.trim()} style={{ padding: '6px 14px', fontSize: 12 }}>
                {testStatus === 'testing' ? <Loader2 size={12} className="spin" style={{ marginRight: 4 }} /> : null}
                测试连接
              </button>
            </div>
            {testStatus === 'success' && (
              <div style={{ fontSize: 12, color: 'var(--success)', display: 'flex', alignItems: 'center', gap: 4 }}>
                <CheckCircle size={12} /> {testMessage}
              </div>
            )}
            {testStatus === 'error' && (
              <div style={{ fontSize: 12, color: 'var(--error)', display: 'flex', alignItems: 'center', gap: 4 }}>
                <AlertTriangle size={12} /> 连接失败: {testMessage}
              </div>
            )}
          </div>
        </section>

        {/* 默认设置 */}
        <section className="settings-section">
          <h3 className="settings-section-title">脚本默认值</h3>

          <div className="settings-row">
            <div className="settings-row-label">
              <Gauge size={15} />
              <span>默认延迟（秒）</span>
            </div>
            <input
              type="number"
              className="settings-input"
              style={{ width: 80 }}
              min="0"
              max="30"
              step="0.1"
              value={script.settings.default_delay}
              onChange={(e) => {
                const v = parseFloat(e.target.value);
                if (!isNaN(v)) useStore.getState().updateNodeParams('__settings__', { default_delay: v });
              }}
            />
          </div>

          <div className="settings-row">
            <div className="settings-row-label">
              <AlertTriangle size={15} />
              <span>错误处理</span>
            </div>
            <select
              className="settings-select"
              value={script.settings.error_handling}
              onChange={(e) => {
                const s = useStore.getState().script;
                (s.settings as any).error_handling = e.target.value;
                useStore.setState({ script: { ...s } });
              }}
            >
              <option value="stop">停止</option>
              <option value="continue">继续</option>
              <option value="retry">重试</option>
            </select>
          </div>

          <div className="settings-row">
            <div className="settings-row-label">
              <RefreshCw size={15} />
              <span>最大重试次数</span>
            </div>
            <input
              type="number"
              className="settings-input"
              style={{ width: 80 }}
              min="0"
              max="20"
              value={script.settings.max_retries}
              onChange={(e) => {
                const v = parseInt(e.target.value);
                if (!isNaN(v)) {
                  const s = useStore.getState().script;
                  (s.settings as any).max_retries = v;
                  useStore.setState({ script: { ...s } });
                }
              }}
            />
          </div>

          <div className="settings-row">
            <div className="settings-row-label">
              <Gauge size={15} />
              <span>重试间隔（秒）</span>
            </div>
            <input
              type="number"
              className="settings-input"
              style={{ width: 80 }}
              min="0"
              max="30"
              step="0.5"
              value={script.settings.retry_delay}
              onChange={(e) => {
                const v = parseFloat(e.target.value);
                if (!isNaN(v)) {
                  const s = useStore.getState().script;
                  (s.settings as any).retry_delay = v;
                  useStore.setState({ script: { ...s } });
                }
              }}
            />
          </div>

          <div className="settings-row">
            <div className="settings-row-label">
              <FileText size={15} />
              <span>日志级别</span>
            </div>
            <select
              className="settings-select"
              value={script.settings.log_level}
              onChange={(e) => {
                const s = useStore.getState().script;
                (s.settings as any).log_level = e.target.value;
                useStore.setState({ script: { ...s } });
              }}
            >
              <option value="debug">调试</option>
              <option value="info">信息</option>
              <option value="warn">警告</option>
              <option value="error">错误</option>
            </select>
          </div>
        </section>
      </div>
    </div>
  );
}
