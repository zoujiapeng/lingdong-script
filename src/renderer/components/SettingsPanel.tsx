import React, { useState } from 'react';
import { useStore } from '../store';
import { Settings, Moon, Sun, Grid3x3, Magnet, Eye, Key, Gauge, AlertTriangle, RefreshCw, FileText } from 'lucide-react';

export function SettingsPanel() {
  const { script, ui, setUI } = useStore();
  const [apiKey, setApiKey] = useState(() => {
    // Try to get from env (Electron) or stored value
    return (window as any).__DEEPSEEK_KEY__ || '';
  });
  const [apiKeySaved, setApiKeySaved] = useState(false);

  const handleSaveApiKey = () => {
    (window as any).__DEEPSEEK_KEY__ = apiKey;
    setApiKeySaved(true);
    setTimeout(() => setApiKeySaved(false), 2000);
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

        {/* DeepSeek API */}
        <section className="settings-section">
          <h3 className="settings-section-title">
            <Key size={15} style={{ marginRight: 6, verticalAlign: 'middle' }} />
            DeepSeek API
          </h3>
          <div className="settings-row" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 8 }}>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              API 密钥（用于 AI 助手功能）
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
            </div>
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
