import React, { useState, useEffect } from 'react';
import { useStore } from '../store';
import { Settings, Moon, Sun, Grid3x3, Magnet, Eye, Key, Gauge, AlertTriangle, RefreshCw, FileText, CheckCircle, Loader2, Clock, Plus, Trash2, Shield, Brain, Volume2, Sun as SunIcon, Wifi, Package, Monitor } from 'lucide-react';
import { ScheduleDialog } from './common/ScheduleDialog';

const KEY_STORAGE = 'lingdong-deepseek-key';
const BASE_STORAGE = 'lingdong-llm-base';
const MODEL_STORAGE = 'lingdong-llm-model';
const MULTIMODAL_STORAGE = 'lingdong-llm-multimodal';
const VISION_BACKEND_STORAGE = 'lingdong-vision-backend';

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
function getStoredMultimodal(): boolean {
  try { return localStorage.getItem(MULTIMODAL_STORAGE) === 'true'; } catch { return false; }
}
function storeMultimodal(val: boolean) {
  try { localStorage.setItem(MULTIMODAL_STORAGE, val ? 'true' : 'false'); } catch {}
}
function getStoredVisionBackend(): string {
  try { return localStorage.getItem(VISION_BACKEND_STORAGE) || 'gpt-4o-mini'; } catch { return 'gpt-4o-mini'; }
}
function storeVisionBackend(val: string) {
  try { localStorage.setItem(VISION_BACKEND_STORAGE, val); } catch {}
}

const VERIFY_STORAGE = 'lingdong-verify-enabled';
function getStoredVerify(): boolean {
  try { return localStorage.getItem(VERIFY_STORAGE) === 'true'; } catch { return false; }
}
function storeVerify(val: boolean) {
  try { localStorage.setItem(VERIFY_STORAGE, val ? 'true' : 'false'); } catch {}
}

const AUTO_LEARN_STORAGE = 'lingdong-auto-learn';
function getStoredAutoLearn(): boolean {
  try { return localStorage.getItem(AUTO_LEARN_STORAGE) === 'true'; } catch { return false; }
}
function storeAutoLearn(val: boolean) {
  try { localStorage.setItem(AUTO_LEARN_STORAGE, val ? 'true' : 'false'); } catch {}
}

const OMNIPARSER_STORAGE = 'lingdong-omniparser';
function getStoredOmniparser(): boolean {
  try { return localStorage.getItem(OMNIPARSER_STORAGE) === 'true'; } catch { return false; }
}
function storeOmniparser(val: boolean) {
  try { localStorage.setItem(OMNIPARSER_STORAGE, val ? 'true' : 'false'); } catch {}
}

// ── Agent 模式选择存储 ──
const AGENT_PROTOCOL_STORAGE = 'lingdong-agent-protocol';
const AGENT_VISION_STORAGE = 'lingdong-agent-vision';
const AGENT_PLANNING_STORAGE = 'lingdong-agent-planning';
const AGENT_ONERROR_STORAGE = 'lingdong-agent-on-error';
const AGENT_MULTIMODAL_STORAGE = 'lingdong-agent-multimodal';
const AGENT_FASTMODE_STORAGE = 'lingdong-agent-fast-mode';

function getStoredAgentMode(key: string, def: string): string {
  try { return localStorage.getItem(key) || def; } catch { return def; }
}
function storeAgentMode(key: string, val: string) {
  try { localStorage.setItem(key, val); } catch {}
}
function getStoredAgentBool(key: string, def: boolean): boolean {
  try { const v = localStorage.getItem(key); return v !== null ? v === 'true' : def; } catch { return def; }
}
function storeAgentBool(key: string, val: boolean) {
  try { localStorage.setItem(key, val ? 'true' : 'false'); } catch {}
}
// 判断模型是否支持多模态（前端简化版，镜像 Python agent.agent._is_multimodal_model）
function _isMultimodalModelFrontend(model: string): boolean {
  const m = model.toLowerCase().trim();
  // 多模态模型关键词
  const mmPatterns = [/^gpt-4o/, /^claude/, /^gemini/, /^qwen-vl/, /^qwen2\.5-vl/,
    /^glm-4v/, /^step-1v/, /^kimi/, /^moonshot-v/, /^reka/, /^llava/,
    /^cogvlm/, /^internvl/, /^yi-vl/, /mimo/i, /vision/i, /multimodal/i];
  if (mmPatterns.some(p => p.test(m))) return true;
  // 明确纯文本模型
  const textPatterns = [/^deepseek/, /^gpt-3/, /^gpt-4$/, /^gpt-4-0/, /^llama/, /^mistral/, /^mixtral/, /^phi-/];
  if (textPatterns.some(p => p.test(m))) return false;
  return false; // 默认保守，视为纯文本
}
function _getResolvedAgentLabel(field: string, model: string, multimodalSetting: string): string {
  const mm = multimodalSetting === 'true' || (multimodalSetting === 'auto' && _isMultimodalModelFrontend(model));
  const map: Record<string, string> = {
    llm_protocol: mm ? 'text 文本协议' : 'function_calling 函数调用',
    vision_mode: mm ? 'direct 直接视觉' : 'som 标号定位',
    planning: mm ? 'per_step 逐步决策' : 'pre_plan 预先规划',
    screen_context: mm ? 'minimal 简洁' : 'full 完整',
    on_error: 'retry 重试',
  };
  return map[field] || field;
}

const ELEVENLABS_KEY_STORAGE = 'lingdong-elevenlabs-key';
const ELEVENLABS_VOICE_STORAGE = 'lingdong-elevenlabs-voice';
function getStoredElevenlabsKey(): string {
  try { return localStorage.getItem(ELEVENLABS_KEY_STORAGE) || ''; } catch { return ''; }
}
function getStoredElevenlabsVoice(): string {
  try { return localStorage.getItem(ELEVENLABS_VOICE_STORAGE) || ''; } catch { return ''; }
}
function storeElevenlabsKey(key: string) {
  try { localStorage.setItem(ELEVENLABS_KEY_STORAGE, key); } catch {}
}
function storeElevenlabsVoice(id: string) {
  try { localStorage.setItem(ELEVENLABS_VOICE_STORAGE, id); } catch {}
}

const STT_ENGINE_STORAGE = 'lingdong-stt-engine';
function getStoredSttEngine(): string {
  try { return localStorage.getItem(STT_ENGINE_STORAGE) || 'whisper-small'; } catch { return 'whisper-small'; }
}
function storeSttEngine(engine: string) {
  try { localStorage.setItem(STT_ENGINE_STORAGE, engine); } catch {}
}

const STT_ENGINES: { value: string; label: string; desc: string }[] = [
  { value: 'whisper-tiny', label: 'Whisper Tiny', desc: '~75MB，快速，准确率一般（中文欠佳）' },
  { value: 'whisper-base', label: 'Whisper Base', desc: '~139MB，较准确（中文有少量错误）' },
  { value: 'whisper-small', label: 'Whisper Small', desc: '~461MB，准确率高（推荐）' },
  { value: 'whisper-large-v3-turbo', label: 'Whisper Large V3 Turbo', desc: '~1.5GB，准确率最高（首次需下载）' },
  { value: 'faster-whisper', label: 'Faster Whisper', desc: 'CTranslate2 加速，兼容性更好（需联网下载）' },
  { value: 'paraformer', label: 'Paraformer', desc: '阿里达摩院中文ASR，CPU快速（~40MB）' },
];

export function SettingsPanel() {
  const store = useStore();
  const script = store.script;
  const ui = store.ui;
  const setUI = store.setUI;
  // Guard against missing settings (should not happen with store fixes, but be defensive)
  const settings = script.settings || { default_delay: 0.5, error_handling: 'stop' as const, max_retries: 3, retry_delay: 1, log_level: 'info' as const };
  const [apiKey, setApiKey] = useState(() => getStoredKey());
  const [apiBase, setApiBase] = useState(() => getStoredBase());
  const [model, setModel] = useState(() => getStoredModel());
  const [multimodal, setMultimodal] = useState(() => getStoredMultimodal());
  const [visionBackend, setVisionBackend] = useState(() => getStoredVisionBackend());
  const [verifyEnabled, setVerifyEnabled] = useState(() => getStoredVerify());
  const [autoLearn, setAutoLearn] = useState(() => getStoredAutoLearn());
  const [omniparserEnabled, setOmniparserEnabled] = useState(() => getStoredOmniparser());
  // Agent 模式状态
  const [agentProtocol, setAgentProtocol] = useState(() => getStoredAgentMode(AGENT_PROTOCOL_STORAGE, 'auto'));
  const [agentVision, setAgentVision] = useState(() => getStoredAgentMode(AGENT_VISION_STORAGE, 'auto'));
  const [agentPlanning, setAgentPlanning] = useState(() => getStoredAgentMode(AGENT_PLANNING_STORAGE, 'auto'));
  const [agentOnError, setAgentOnError] = useState(() => getStoredAgentMode(AGENT_ONERROR_STORAGE, 'retry'));
  const [agentMultimodal, setAgentMultimodal] = useState(() => getStoredAgentMode(AGENT_MULTIMODAL_STORAGE, 'auto'));
  const [agentFastMode, setAgentFastMode] = useState(() => getStoredAgentBool(AGENT_FASTMODE_STORAGE, false));
  const [elevenlabsKey, setElevenlabsKey] = useState(() => getStoredElevenlabsKey());
  const [elevenlabsVoiceId, setElevenlabsVoiceId] = useState(() => getStoredElevenlabsVoice());
  const [elevenlabsSaved, setElevenlabsSaved] = useState(false);
  const [apiKeySaved, setApiKeySaved] = useState(false);
  const [sttEngine, setSttEngine] = useState(() => getStoredSttEngine());
  const [sttSaved, setSttSaved] = useState(false);
  const [testStatus, setTestStatus] = useState<'idle' | 'testing' | 'success' | 'error'>('idle');
  const [testMessage, setTestMessage] = useState('');

  const [autoStart, setAutoStart] = useState(false);
  const [schedules, setSchedules] = useState<any[]>([]);
  const [showScheduleDialog, setShowScheduleDialog] = useState(false);
  const [scheduleLoading, setScheduleLoading] = useState(true);
  const [schedulesError, setSchedulesError] = useState('');

  // ── 保险库 ──
  const [vaultItems, setVaultItems] = useState<{key: string; created: string}[]>([]);
  const [vaultNewKey, setVaultNewKey] = useState('');
  const [vaultNewVal, setVaultNewVal] = useState('');
  const [vaultShowAdd, setVaultShowAdd] = useState(false);
  const [vaultLoading, setVaultLoading] = useState(false);

  // ── 系统控制 ──
  const [volume, setVolume] = useState(100);
  const [brightness, setBrightness] = useState(100);
  const [wifiNetworks, setWifiNetworks] = useState<any[]>([]);
  const [wifiScanning, setWifiScanning] = useState(false);

  // ── 插件 ──
  const [plugins, setPlugins] = useState<any[]>([]);
  const [pluginLoading, setPluginLoading] = useState(false);

  // ── 高级功能折叠 ──
  const [showAdvanced, setShowAdvanced] = useState(false);

  useEffect(() => {
    (window as any).electronAPI?.getAutoStart().then(setAutoStart).catch(() => {});
    loadSchedules();
  }, []);

  const toggleAutoStart = () => {
    const next = !autoStart;
    setAutoStart(next);
    (window as any).electronAPI?.setAutoStart(next).catch(() => setAutoStart(!next));
  };

  const loadSchedules = async () => {
    setScheduleLoading(true);
    setSchedulesError('');
    try {
      const list = await (window as any).electronAPI?.scheduleList() || [];
      setSchedules(list);
    } catch (e: any) {
      setSchedulesError(e.message || '加载失败');
    } finally {
      setScheduleLoading(false);
    }
  };

  const loadVault = async () => {
    setVaultLoading(true);
    try {
      const api = (window as any).electronAPI;
      if (!api?.callFeature) return;
      const r = await api.callFeature('exec_vault_list', {});
      setVaultItems(Array.isArray(r) ? r : (Array.isArray(r?.result) ? r.result : []));
    } catch {}
    setVaultLoading(false);
  };

  const saveVaultItem = async () => {
    if (!vaultNewKey.trim() || !vaultNewVal.trim()) return;
    try {
      const api = (window as any).electronAPI;
      await api?.callFeature('exec_vault_set', { key: vaultNewKey.trim(), value: vaultNewVal.trim() });
      setVaultNewKey(''); setVaultNewVal(''); setVaultShowAdd(false);
      loadVault();
    } catch {}
  };

  const deleteVaultItem = async (key: string) => {
    try {
      await (window as any).electronAPI?.callFeature('exec_vault_set', { key, value: '' });
      loadVault();
    } catch {}
  };

  const toggleWifiScan = async () => {
    setWifiScanning(true);
    try {
      const r = await (window as any).electronAPI?.callFeature('wifi_list', {});
      setWifiNetworks(Array.isArray(r) ? r : r?.result || []);
    } catch {}
    setWifiScanning(false);
  };

  const loadPlugins = async () => {
    setPluginLoading(true);
    try {
      const r = await (window as any).electronAPI?.callFeature('plugin_list', {});
      setPlugins(Array.isArray(r) ? r : (Array.isArray(r?.result) ? r.result : []));
    } catch {}
    setPluginLoading(false);
  };

  const toggleSchedule = async (id: string) => {
    const entry = schedules.find(s => s.id === id);
    if (!entry) return;
    const updated = await (window as any).electronAPI?.scheduleUpdate(id, { enabled: !entry.enabled });
    if (updated) {
      setSchedules(schedules.map(s => s.id === id ? { ...s, enabled: !s.enabled } : s));
    }
  };

  const deleteSchedule = async (id: string) => {
    const ok = await (window as any).electronAPI?.scheduleDelete(id);
    if (ok) setSchedules(schedules.filter(s => s.id !== id));
  };

  const handleSaveApiKey = () => {
    storeKey(apiKey);
    storeBase(apiBase);
    storeModel(model);
    storeMultimodal(multimodal);
    storeVisionBackend(visionBackend);
    (window as any).__DEEPSEEK_KEY__ = apiKey;
    (window as any).__LLM_API_BASE__ = apiBase;
    (window as any).__LLM_MODEL__ = model;
    (window as any).__LLM_MULTIMODAL__ = multimodal;
    (window as any).__VLM_BACKEND__ = visionBackend;
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

  const handleSaveElevenlabs = () => {
    storeElevenlabsKey(elevenlabsKey);
    storeElevenlabsVoice(elevenlabsVoiceId);
    setElevenlabsSaved(true);
    setTimeout(() => setElevenlabsSaved(false), 2000);
  };

  const handleSaveStt = () => {
    storeSttEngine(sttEngine);
    (window as any).__STT_ENGINE__ = sttEngine;
    setSttSaved(true);
    setTimeout(() => setSttSaved(false), 2000);
    // Notify voice panel to reconnect with new engine
    window.dispatchEvent(new CustomEvent('stt-engine-changed'));
  };

  const handleRedownloadModel = () => {
    if (!confirm(`确定重新下载 ${sttEngine} 模型？这将删除已缓存的模型文件并重新下载。`)) return;
    localStorage.setItem('lingdong-stt-redownload', 'true');
    alert('已标记重新下载。请刷新页面并重新连接语音对话后自动生效。');
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
              <Monitor size={15} />
              <span>主题模式</span>
            </div>
            <div style={{ display: 'flex', gap: 4, background: 'var(--bg-secondary)', borderRadius: 6, padding: 2 }}>
              {(['light', 'dark', 'system'] as const).map(mode => (
                <button key={mode} className={`toggle-segment ${ui.themeMode === mode ? 'active' : ''}`}
                  onClick={() => setUI({ themeMode: mode })}
                  style={{ padding: '4px 12px', fontSize: 12, borderRadius: 4, border: 'none', cursor: 'pointer',
                    background: ui.themeMode === mode ? 'var(--accent)' : 'transparent',
                    color: ui.themeMode === mode ? '#fff' : 'var(--text-primary)' }}>
                  {mode === 'light' ? '亮色' : mode === 'dark' ? '深色' : '跟随系统'}
                </button>
              ))}
            </div>
          </div>

          <div className="settings-row">
            <div className="settings-row-label">
              <Settings size={15} />
              <span>开机自启</span>
            </div>
            <button
              className={`toggle ${autoStart ? 'on' : ''}`}
              onClick={toggleAutoStart}
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

          <div className="settings-row">
            <div className="settings-row-label">
              <Eye size={15} />
              <span>多模态（视觉能力）</span>
            </div>
            <button
              className={`toggle ${multimodal ? 'on' : ''}`}
              onClick={() => setMultimodal(!multimodal)}
            />
          </div>
          {multimodal && (
            <div style={{ fontSize: 11, color: 'var(--text-secondary)', padding: '0 0 4px 28px', lineHeight: 1.5 }}>
              启用后 AI 助手和 Agent 可使用截图分析、图像识别等视觉能力。
              需要模型支持多模态（如 GPT-4o、Claude 3.5+、Qwen-VL）。
              DeepSeek 等纯文本模型不支持多模态，开启后调用会出错。
            </div>
          )}
          {multimodal && (
            <div className="settings-row" style={{ paddingLeft: 28 }}>
              <div className="settings-row-label">
                <span style={{ fontSize: 13 }}>VLM 后端</span>
              </div>
              <select
                value={visionBackend}
                onChange={e => setVisionBackend(e.target.value)}
                style={{
                  fontSize: 12, padding: '2px 6px', borderRadius: 4,
                  border: '1px solid var(--border-color)',
                  background: 'var(--bg-secondary)', color: 'var(--text-primary)',
                  maxWidth: 160,
                }}
              >
                <option value="gpt-4o-mini">GPT-4o-mini（默认）</option>
                <option value="local">本地模型 (llama-server)</option>
              </select>
            </div>
          )}

          <div className="settings-row">
            <div className="settings-row-label">
              <Shield size={15} />
              <span>执行验证（Agent 模式）</span>
            </div>
            <button
              className={`toggle ${verifyEnabled ? 'on' : ''}`}
              onClick={() => { const v = !verifyEnabled; setVerifyEnabled(v); storeVerify(v); }}
            />
          </div>
          {!verifyEnabled && (
            <div style={{ fontSize: 11, color: 'var(--text-secondary)', padding: '0 0 4px 28px', lineHeight: 1.5 }}>
              关闭后 Agent 执行步骤后跳过验证，直接继续下一步，执行更快但不检测结果是否正确。
            </div>
          )}

          <div className="settings-row">
            <div className="settings-row-label">
              <Brain size={15} />
              <span>自动学习（Agent 模式）</span>
            </div>
            <button
              className={`toggle ${autoLearn ? 'on' : ''}`}
              onClick={() => { const v = !autoLearn; setAutoLearn(v); storeAutoLearn(v); }}
            />
          </div>
          {autoLearn && (
            <div style={{ fontSize: 11, color: 'var(--text-secondary)', padding: '0 0 4px 28px', lineHeight: 1.5 }}>
              开启后 Agent 每次执行成功自动记录屏幕状态和目标信息，后续可快速复用相似任务。
            </div>
          )}

          <div className="settings-row">
            <div className="settings-row-label">
              <Monitor size={15} />
              <span>OmniParser（增强 SoM 标注）</span>
            </div>
            <button
              className={`toggle ${omniparserEnabled ? 'on' : ''}`}
              onClick={() => { const v = !omniparserEnabled; setOmniparserEnabled(v); storeOmniparser(v); }}
            />
          </div>
          {!omniparserEnabled && (
            <div style={{ fontSize: 11, color: 'var(--text-secondary)', padding: '0 0 4px 28px', lineHeight: 1.5 }}>
              OmniParser 使用 YOLOv8+Florence-2 进行图标检测和描述（已兼容 transformers 5.x）。
              默认关闭，使用 UIA+OCR 已满足大多数场景。
            </div>
          )}
          {omniparserEnabled && (
            <div style={{ fontSize: 11, color: 'var(--warning)', padding: '0 0 4px 28px', lineHeight: 1.5 }}>
              首次启用时 Agent 会自动启动 OmniParser 服务器（~60s 模型加载）。CPU 解析每帧约 25s，GPU 大幅加速。
            </div>
          )}
        </section>

        {/* Agent 执行模式 — 现已整合到 AgentView 设置面板 */}
        <section className="settings-section">
          <h3 className="settings-section-title">
            <Brain size={15} style={{ marginRight: 6, verticalAlign: 'middle' }} />
            Agent 配置
          </h3>
          <div style={{
            padding: '14px 16px',
            background: 'rgba(99, 102, 241, 0.04)',
            border: '1px solid rgba(99, 102, 241, 0.12)',
            borderRadius: 8,
            fontSize: 12,
            lineHeight: 1.6,
            color: 'var(--text-secondary)',
          }}>
            <p style={{ marginBottom: 8, fontWeight: 500, color: 'var(--text-primary)' }}>
              所有 Agent 配置已整合到 Agent 面板
            </p>
            <p style={{ marginBottom: 8 }}>
              Agent 的全部 26+ 项配置参数（LLM 协议、视觉模式、规划策略、安全限制、截图质量、温度等）
              现已在 <strong>AI Agent 面板 → 「全部配置」</strong> 中统一管理。
            </p>
            <p style={{ marginBottom: 0 }}>
              切换到 Agent 视图即可展开配置面板，所有修改自动保存。
            </p>
            <div style={{
              marginTop: 10, paddingTop: 10,
              borderTop: '1px solid rgba(99, 102, 241, 0.12)',
              display: 'flex', flexWrap: 'wrap', gap: '4px 8px',
              fontSize: 11, color: 'var(--text-muted)',
            }}>
              <span>协议: {agentProtocol === 'auto' ? _getResolvedAgentLabel('llm_protocol', model, agentMultimodal) : agentProtocol}</span>
              <span>·</span>
              <span>视觉: {agentVision === 'auto' ? _getResolvedAgentLabel('vision_mode', model, agentMultimodal) : agentVision}</span>
              <span>·</span>
              <span>规划: {agentPlanning === 'auto' ? _getResolvedAgentLabel('planning', model, agentMultimodal) : agentPlanning}</span>
              <span>·</span>
              <span>错误: {agentOnError}</span>
              <span>·</span>
              <span>快速: {agentFastMode ? '开' : '关'}</span>
            </div>
          </div>
        </section>

        {/* TTS 语音 */}
        <section className="settings-section">
          <h3 className="settings-section-title">
            <Volume2 size={15} style={{ marginRight: 6, verticalAlign: 'middle' }} />
            TTS 语音（语音对话朗读）
          </h3>
          <div className="settings-row" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 8 }}>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              ElevenLabs API 密钥（留空则使用 edge-tts）
            </label>
            <input
              type="password"
              className="settings-input"
              value={elevenlabsKey}
              onChange={(e) => setElevenlabsKey(e.target.value)}
              placeholder="sk_..."
              style={{ width: '100%' }}
            />
          </div>
          <div className="settings-row" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 8 }}>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              ElevenLabs Voice ID（在 elevenlabs.io 创建音色后获取）
            </label>
            <div style={{ display: 'flex', gap: 8 }}>
              <input
                className="settings-input"
                value={elevenlabsVoiceId}
                onChange={(e) => setElevenlabsVoiceId(e.target.value)}
                placeholder="21m00Tcm4TlvDq8ikWAM"
                style={{ flex: 1, fontSize: 12 }}
              />
              <button className="btn btn-primary" onClick={handleSaveElevenlabs} style={{ padding: '6px 14px', fontSize: 12 }}>
                {elevenlabsSaved ? '已保存' : '保存'}
              </button>
            </div>
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-secondary)', padding: '4px 0', lineHeight: 1.5 }}>
            配置后在语音对话面板中，AI 回复将使用你定制的 ElevenLabs 音色朗读。语音对话服务器启动时会读取此配置。
          </div>
        </section>

        <section className="settings-section">
          <h3>语音识别引擎（语音对话输入）</h3>
          <div className="settings-row" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 12 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <select
                className="settings-input"
                value={sttEngine}
                onChange={(e) => setSttEngine(e.target.value)}
                style={{ flex: 1, minWidth: 200, fontSize: 12 }}
              >
                {STT_ENGINES.map((e) => (
                  <option key={e.value} value={e.value}>{e.label} — {e.desc}</option>
                ))}
              </select>
              <button className="btn btn-primary" onClick={handleSaveStt} style={{ padding: '6px 14px', fontSize: 12 }}>
                {sttSaved ? '已保存' : '保存'}
              </button>
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <button className="btn btn-ghost" onClick={handleRedownloadModel} style={{ padding: '6px 12px', fontSize: 11 }}>
                重新下载模型
              </button>
              <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                当前引擎切换后需刷新页面或重新连接语音对话生效
              </span>
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
              value={settings.default_delay}
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
              value={settings.error_handling}
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
              value={settings.max_retries}
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
              value={settings.retry_delay}
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
              value={settings.log_level}
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

        {/* 定时任务 */}
        <section className="settings-section">
          <h3 className="settings-section-title">
            <Clock size={15} style={{ marginRight: 6, verticalAlign: 'middle' }} />
            定时任务
          </h3>

          {scheduleLoading && (
            <div className="settings-row" style={{ justifyContent: 'center', color: 'var(--text-muted)', fontSize: 12 }}>
              加载中...
            </div>
          )}

          {schedulesError && (
            <div className="settings-row" style={{ justifyContent: 'center', color: 'var(--error)', fontSize: 12 }}>
              {schedulesError}
            </div>
          )}

          {!scheduleLoading && !schedulesError && schedules.length === 0 && (
            <div className="settings-row" style={{ justifyContent: 'center', color: 'var(--text-muted)', fontSize: 12 }}>
              暂无定时任务
            </div>
          )}

          {schedules.map((s: any) => {
            const t = s.triggers?.[0] || {};
            const triggerLabel = t.type === 'startup' ? '启动时执行' :
              t.type === 'cron' ? `Cron: ${t.cron_expr}` :
              t.type === 'interval' ? `每 ${t.interval_seconds}s` :
              t.type === 'event' ? `${t.event_type}` : '';
            const condCount = s.conditions?.rules?.length || 0;
            return (
            <div className="settings-row" key={s.id}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2, flex: 1 }}>
                <span style={{ fontSize: 13, fontWeight: 500 }}>{s.name}</span>
                <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                  {triggerLabel}
                  {condCount > 0 ? ` + ${condCount}个条件` : ''}
                  {s.template_name ? ` · ${s.template_name}` : ''}
                </span>
              </div>
              <button
                className={`toggle ${s.enabled ? 'on' : ''}`}
                onClick={() => toggleSchedule(s.id)}
              />
              <button
                className="btn btn-ghost"
                onClick={() => deleteSchedule(s.id)}
                style={{ padding: '4px 8px', fontSize: 12, color: 'var(--text-muted)' }}
                title="删除"
              >
                <Trash2 size={14} />
              </button>
            </div>
            );
          })}

          <div className="settings-row" style={{ justifyContent: 'center' }}>
            <button
              className="btn btn-primary"
              onClick={() => setShowScheduleDialog(true)}
              style={{ padding: '6px 16px', fontSize: 12 }}
            >
              <Plus size={14} style={{ marginRight: 4 }} />
              新增定时任务
            </button>
          </div>
        </section>

        {/* ════════════════════════════ 高级功能 ════════════════════════════ */}
        <section className="settings-section" style={{ marginTop: 12 }}>
          <h3 className="settings-section-title" onClick={() => {
            setShowAdvanced(!showAdvanced);
            if (!showAdvanced) { loadVault(); loadPlugins(); }
          }} style={{ cursor: 'pointer', userSelect: 'none' }}>
            <Package size={14} style={{ marginRight: 6, verticalAlign: 'middle' }} />
            高级功能（保险库 · 系统控制 · 插件）
            <span style={{ float: 'right', fontSize: 11 }}>{showAdvanced ? '▲' : '▼'}</span>
          </h3>
          <div style={{ display: showAdvanced ? '' : 'none' }}>

          {/* ── 保险库 ── */}
          <section className="settings-section">
            <h3 className="settings-section-title">
              <Key size={14} style={{ marginRight: 4, verticalAlign: 'middle' }} />
              凭据保险库（加密存储）
            </h3>
            {vaultLoading ? (
              <div style={{ fontSize: 12, color: 'var(--text-muted)', padding: 4 }}>加载中...</div>
            ) : vaultItems.length === 0 ? (
              <div style={{ fontSize: 12, color: 'var(--text-muted)', padding: 4 }}>暂无存储的凭据</div>
            ) : (
              vaultItems.map((item: any) => (
                <div key={item.key} className="settings-row" style={{ padding: '4px 0' }}>
                  <span style={{ fontSize: 13, flex: 1 }}>{item.key}</span>
                  <span style={{ fontSize: 10, color: 'var(--text-muted)', marginRight: 8 }}>
                    {item.created ? new Date(item.created).toLocaleDateString() : ''}
                  </span>
                  <button className="btn btn-ghost" onClick={() => deleteVaultItem(item.key)}
                    style={{ padding: '2px 6px', fontSize: 11, color: 'var(--text-muted)' }}>
                    <Trash2 size={11} />
                  </button>
                </div>
              ))
            )}
            {vaultShowAdd ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4, padding: '4px 0' }}>
                <input className="settings-input" value={vaultNewKey} onChange={e => setVaultNewKey(e.target.value)}
                  placeholder="名称 (如: github_token)" style={{ width: '100%', fontSize: 12 }} />
                <input className="settings-input" type="password" value={vaultNewVal} onChange={e => setVaultNewVal(e.target.value)}
                  placeholder="值" style={{ width: '100%', fontSize: 12 }} />
                <div style={{ display: 'flex', gap: 4 }}>
                  <button className="btn btn-primary" onClick={saveVaultItem}
                    style={{ padding: '4px 12px', fontSize: 11 }}>保存</button>
                  <button className="btn btn-ghost" onClick={() => setVaultShowAdd(false)}
                    style={{ padding: '4px 12px', fontSize: 11 }}>取消</button>
                </div>
              </div>
            ) : (
              <button className="btn btn-ghost" onClick={() => setVaultShowAdd(true)}
                style={{ padding: '4px 12px', fontSize: 11, marginTop: 4 }}>
                <Plus size={12} style={{ marginRight: 4 }} />新增
              </button>
            )}
          </section>

          {/* ── 系统控制 ── */}
          <section className="settings-section">
            <h3 className="settings-section-title">
              <SunIcon size={14} style={{ marginRight: 4, verticalAlign: 'middle' }} />
              系统控制
            </h3>
            <div className="settings-row">
              <div className="settings-row-label"><Volume2 size={14} /><span>系统音量</span></div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <input type="range" min={0} max={100} value={volume}
                  onChange={async e => {
                    const v = parseInt(e.target.value); setVolume(v);
                    await (window as any).electronAPI?.callFeature('volume_set', { level: v / 100 });
                  }} style={{ width: 100 }} />
                <span className="settings-value">{volume}%</span>
              </div>
            </div>
            <div className="settings-row">
              <div className="settings-row-label"><SunIcon size={14} /><span>屏幕亮度</span></div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <input type="range" min={0} max={100} value={brightness}
                  onChange={async e => {
                    const v = parseInt(e.target.value); setBrightness(v);
                    await (window as any).electronAPI?.callFeature('display_brightness', { level: v / 100 });
                  }} style={{ width: 100 }} />
                <span className="settings-value">{brightness}%</span>
              </div>
            </div>
            <div className="settings-row">
              <div className="settings-row-label"><Wifi size={14} /><span>Wi-Fi 网络</span></div>
              <button className="btn btn-ghost" onClick={toggleWifiScan} disabled={wifiScanning}
                style={{ padding: '4px 10px', fontSize: 11 }}>
                {wifiScanning ? '扫描中...' : '扫描'}
              </button>
            </div>
            {wifiNetworks.length > 0 && (
              <div style={{ maxHeight: 120, overflow: 'auto', fontSize: 11, marginTop: 4 }}>
                {wifiNetworks.slice(0, 10).map((w: any) => (
                  <div key={w.ssid || w.bssid} style={{ display: 'flex', justifyContent: 'space-between', padding: '2px 4px' }}>
                    <span>{w.ssid || '(隐藏)'}</span>
                    <span style={{ color: 'var(--text-muted)' }}>
                      {w.signal_level !== undefined ? `${Math.round(w.signal_level * 100)}%` : ''}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* ── 插件 ── */}
          <section className="settings-section">
            <h3 className="settings-section-title">
              <Package size={14} style={{ marginRight: 4, verticalAlign: 'middle' }} />
              插件
            </h3>
            {pluginLoading ? (
              <div style={{ fontSize: 12, color: 'var(--text-muted)', padding: 4 }}>加载中...</div>
            ) : plugins.length === 0 ? (
              <div style={{ fontSize: 12, color: 'var(--text-muted)', padding: 4 }}>无已加载插件</div>
            ) : (
              plugins.map((p: any) => (
                <div key={p.name || p.path} className="settings-row" style={{ padding: '4px 0' }}>
                  <span style={{ fontSize: 13, flex: 1 }}>{p.name || p.path}</span>
                  <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                    {p.status || (p.loaded ? '已加载' : '')}
                  </span>
                </div>
              ))
            )}
          </section>
        </div>
        </section>
      </div>

      {showScheduleDialog && (
        <ScheduleDialog
          onClose={() => setShowScheduleDialog(false)}
          onCreated={loadSchedules}
        />
      )}
    </div>
  );
}
