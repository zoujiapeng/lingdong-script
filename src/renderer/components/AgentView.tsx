import React, { useState, useCallback, useEffect, useRef } from 'react';
import { useStore } from '../store';
import {
  List, Wifi, WifiOff, Mic, MicOff, Clock, RefreshCw,
  Eye, EyeOff, GitBranch, Zap,
  MessageSquare, Monitor, Brain, Volume2,
  Shield,
} from 'lucide-react';
import { VoiceConversationPanel } from './common/VoiceConversationPanel';
import { PlanVisualizer } from './common/PlanVisualizer';
import type { AgentRunRecord } from '../types';

interface StepData {
  step_num: number;
  tool: string;
  thought: string;
  step_label?: string;
  step_type?: string;
  error?: string;
  result: { success: boolean; confidence: number };
}

type LogEntry =
  | { type: 'system'; text: string; _id?: number }
  | { type: 'step'; data: StepData; _id?: number }
  | { type: 'user'; text: string; _id?: number }
  | { type: 'complete'; steps: number; _id?: number }
  | { type: 'fail'; reason: string; _id?: number }
  | { type: 'guidance'; text: string; _id?: number }
  | { type: 'script'; pseudocode: string; explanation: string; _id?: number };

let _logIdCounter = 0;
let _hasActiveRun = false;
let _persistedLog: LogEntry[] = [];
let _cachedSteps: any[] | null = null;

const _STORAGE_KEY = 'lingdong-agent-goal';

// ── Agent config localStorage keys ──
const AGENT_PROTOCOL_KEY = 'lingdong-agent-protocol';
const AGENT_VISION_KEY = 'lingdong-agent-vision';
const AGENT_PLANNING_KEY = 'lingdong-agent-planning';
const AGENT_CONTEXT_KEY = 'lingdong-agent-context';
const AGENT_ONERROR_KEY = 'lingdong-agent-on-error';
const AGENT_FAST_MODE_KEY = 'lingdong-agent-fast-mode';
const AGENT_DEDUP_KEY = 'lingdong-agent-image-dedup';
const AGENT_WINDOW_AWARE_OCR_KEY = 'lingdong-agent-window-aware-ocr';
const AGENT_UIA_PREFOCUS_KEY = 'lingdong-agent-uia-prefocus';
const AGENT_SCRIPTED_MODE_KEY = 'lingdong-agent-scripted-mode';
const AGENT_MULTIMODAL_KEY = 'lingdong-agent-multimodal';
const AGENT_VERIFY_KEY = 'lingdong-agent-verify';
const AGENT_AUTO_LEARN_KEY = 'lingdong-agent-auto-learn';
const AGENT_OMNIPARSER_KEY = 'lingdong-agent-omniparser';
const AGENT_SOM_KEY = 'lingdong-agent-som';
const AGENT_LOCAL_VISION_KEY = 'lingdong-agent-local-vision';
const AGENT_FLOW_CHART_KEY = 'lingdong-agent-flow-chart';
const AGENT_SCREENSHOT_QUALITY_KEY = 'lingdong-agent-screenshot-quality';
const AGENT_MAX_STEPS_KEY = 'lingdong-agent-max-steps';
const AGENT_MAX_CONSECUTIVE_FAILS_KEY = 'lingdong-agent-max-consecutive-fails';
const AGENT_MIN_SUCCESS_RATE_KEY = 'lingdong-agent-min-success-rate';
const AGENT_TEMPERATURE_KEY = 'lingdong-agent-temperature';
const AGENT_MAX_TOKENS_KEY = 'lingdong-agent-max-tokens';
const AGENT_LLM_RETRIES_KEY = 'lingdong-agent-llm-retries';
const AGENT_SLIDING_WINDOW_KEY = 'lingdong-agent-sliding-window';
const AGENT_VISION_BACKEND_KEY = 'lingdong-agent-vision-backend';
const AGENT_CHAT_MODE_KEY = 'lingdong-agent-chat-mode';
const AGENT_VISUAL_STRATEGY_KEY = 'lingdong-agent-visual-strategy';

// Legacy storage keys (shared with SettingsPanel)
const KEY_STORAGE = 'lingdong-deepseek-key';
const PROFILE_HISTORY_STORAGE = 'lingdong-api-profiles';
const BASE_STORAGE = 'lingdong-llm-base';
const MODEL_STORAGE = 'lingdong-llm-model';
const LLM_MULTIMODAL_STORAGE = 'lingdong-llm-multimodal';

// ── Helper getters ──
function lsGet(key: string, def: string = ''): string {
  try { return localStorage.getItem(key) || def; } catch { return def; }
}
function lsSet(key: string, val: string) {
  try { localStorage.setItem(key, val); } catch {}
}
function lsGetBool(key: string, def: boolean = false): boolean {
  try { const v = localStorage.getItem(key); return v !== null ? v === 'true' : def; } catch { return def; }
}
function lsSetBool(key: string, val: boolean) {
  try { localStorage.setItem(key, val ? 'true' : 'false'); } catch {}
}
function lsGetNum(key: string, def: number): number {
  try { const v = localStorage.getItem(key); return v !== null ? parseFloat(v) : def; } catch { return def; }
}
function lsSetNum(key: string, val: number) {
  try { localStorage.setItem(key, String(val)); } catch {}
}

// ── Inline config control components ──

function ConfigSelect({ label, value, onChange, options, desc, resolved }: {
  label: string; value: string; onChange: (v: string) => void;
  options: { value: string; label: string }[];
  desc?: string; resolved?: string;
}) {
  const isAuto = value === 'auto';
  return (
    <div className="agent-config-field">
      <div className="agent-config-field-label">{label}</div>
      <div className="agent-config-field-control">
        <select
          className="agent-config-select"
          value={value}
          onChange={e => onChange(e.target.value)}
        >
          {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        {isAuto && resolved && (
          <span className="agent-config-resolved">自动 → {resolved}</span>
        )}
      </div>
      {desc && <div className="agent-config-field-desc">{desc}</div>}
    </div>
  );
}

function ConfigToggle({ label, checked, onChange, desc, badge }: {
  label: string; checked: boolean; onChange: (v: boolean) => void;
  desc?: string; badge?: string;
}) {
  return (
    <div className="agent-config-field">
      <div className="agent-config-field-label-row">
        <span className="agent-config-field-label">{label}</span>
        {badge && <span className="agent-config-badge">{badge}</span>}
      </div>
      <div className="agent-config-field-control">
        <button className={`toggle ${checked ? 'on' : ''}`}
          onClick={() => onChange(!checked)} />
      </div>
      {desc && <div className="agent-config-field-desc">{desc}</div>}
    </div>
  );
}

function ConfigSlider({ label, value, min, max, step, onChange, unit, desc }: {
  label: string; value: number; min: number; max: number; step: number;
  onChange: (v: number) => void; unit?: string; desc?: string;
}) {
  return (
    <div className="agent-config-field">
      <div className="agent-config-field-label-row">
        <span className="agent-config-field-label">{label}</span>
        <span className="agent-config-slider-value">{value}{unit || ''}</span>
      </div>
      <div className="agent-config-field-control slider-control">
        <input type="range" min={min} max={max} step={step} value={value}
          onChange={e => onChange(parseFloat(e.target.value))}
          className="agent-config-slider" />
      </div>
      {desc && <div className="agent-config-field-desc">{desc}</div>}
    </div>
  );
}

function ConfigInput({ label, value, onChange, type, placeholder, secret, onTest }: {
  label: string; value: string; onChange: (v: string) => void;
  type?: string; placeholder?: string; secret?: boolean; onTest?: () => void;
}) {
  const [showSecret, setShowSecret] = useState(false);
  const inputType = secret && !showSecret ? 'password' : type || 'text';
  return (
    <div className="agent-config-field full-width">
      <div className="agent-config-field-label">{label}</div>
      <div className="agent-config-field-row">
        <input
          className="agent-config-input"
          type={inputType}
          value={value}
          onChange={e => onChange(e.target.value)}
          placeholder={placeholder || ''}
          spellCheck={false}
        />
        {secret && (
          <button className="agent-config-icon-btn" onClick={() => setShowSecret(!showSecret)}
            title={showSecret ? '隐藏' : '显示'}>
            {showSecret ? <Eye size={12} /> : <Eye size={12} />}
          </button>
        )}
        {onTest && (
          <button className="agent-config-btn" onClick={onTest}>测试</button>
        )}
      </div>
    </div>
  );
}

interface ApiProfile {
  key: string;
  base: string;
  model: string;
  label: string;
}

// 常用 LLM 服务商预设
const PROVIDER_PRESETS: ApiProfile[] = [
  { label: '🐋 DeepSeek',   key: '', base: 'https://api.deepseek.com/v1',        model: 'deepseek-chat' },
  { label: '🔮 MIMO',       key: '', base: 'https://api.xiaomimimo.com/v1',     model: 'mimo-v2-omni' },
  { label: '🟢 OpenAI',     key: '', base: 'https://api.openai.com/v1',         model: 'gpt-4o' },
  { label: '🔴 Claude',     key: '', base: 'https://api.anthropic.com/v1',      model: 'claude-sonnet-4-20250514' },
  { label: '🔵 Gemini',     key: '', base: 'https://generativelanguage.googleapis.com/v1beta', model: 'gemini-2.0-flash' },
  { label: '🟣 SiliconFlow', key: '', base: 'https://api.siliconflow.cn/v1',     model: 'deepseek-ai/DeepSeek-V3' },
];
function getProfiles(): ApiProfile[] {
  try { return JSON.parse(localStorage.getItem(PROFILE_HISTORY_STORAGE) || '[]').slice(0, 10); } catch { return []; }
}
function saveProfile(key: string, base: string, model: string) {
  if (!key || key.length < 8) return;
  const label = model || base.replace(/https?:\/\//, '').split('/')[0] || 'custom';
  const profiles = [{ key, base, model, label }, ...getProfiles().filter(p => p.key !== key)].slice(0, 10);
  localStorage.setItem(PROFILE_HISTORY_STORAGE, JSON.stringify(profiles));
}
function maskKey(key: string): string {
  if (key.length <= 12) return key.slice(0, 4) + '...' + key.slice(-4);
  return key.slice(0, 6) + '...' + key.slice(-4);
}

function ConfigApiKey({ label, value, onKeyChange, onSelectProfile, onPresetSelect }: {
  label: string; value: string;
  onKeyChange: (v: string) => void;
  onSelectProfile: (p: ApiProfile) => void;
  onPresetSelect: (base: string, model: string) => void;
}) {
  const [showSecret, setShowSecret] = useState(false);
  const profiles = getProfiles();
  const [showDropdown, setShowDropdown] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node) &&
          inputRef.current && !inputRef.current.contains(e.target as Node)) {
        setShowDropdown(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  // 找出当前 key 对应的 profile 信息
  const activeProfile = profiles.find(p => p.key === value);

  return (
    <div className="agent-config-field full-width" style={{ position: 'relative' }}>
      <div className="agent-config-field-label">{label}</div>
      <div className="agent-config-field-row">
        <input
          ref={inputRef}
          className="agent-config-input"
          type={showSecret ? 'text' : 'password'}
          value={value}
          onChange={e => onKeyChange(e.target.value)}
          placeholder="sk-..."
          spellCheck={false}
          onFocus={() => setShowDropdown(true)}
        />
        <button className="agent-config-icon-btn" onClick={() => setShowSecret(!showSecret)}
          title={showSecret ? '隐藏' : '显示'}>
          {showSecret ? <EyeOff size={12} /> : <Eye size={12} />}
        </button>
        {profiles.length > 0 && (
          <button className="agent-config-icon-btn" onClick={() => setShowDropdown(!showDropdown)}
            title="切换配置档案" style={{ fontSize: 10 }}>
            ▼
          </button>
        )}
      </div>
      {activeProfile && (
        <div style={{ fontSize: 10, color: '#888', marginTop: 2, paddingLeft: 2 }}>
          {activeProfile.label} · {maskKey(value)}
        </div>
      )}
      {showDropdown && (
        <div ref={dropdownRef} style={{
          position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 100,
          background: '#2a2a2a', border: '1px solid #444', borderRadius: 4,
          maxHeight: 300, overflowY: 'auto', marginTop: 2,
        }}>
          {/* ── 快捷预设 ── */}
          <div style={{ padding: '4px 10px', fontSize: 10, color: '#666', fontWeight: 600 }}>快捷选择</div>
          {PROVIDER_PRESETS.map(p => (
            <div key={p.label} onClick={() => {
              onKeyChange(p.key);
              onPresetSelect(p.base, p.model);
              setShowDropdown(false);
            }}
              style={{ padding: '7px 10px', cursor: 'pointer', fontSize: 12, borderBottom: '1px solid #333' }}
              onMouseEnter={e => (e.currentTarget.style.background = '#333')}
              onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
            >
              <div style={{ fontWeight: 600 }}>{p.label}</div>
              <div style={{ fontSize: 10, color: '#999' }}>{p.model}</div>
            </div>
          ))}
          {/* ── 历史配置 ── */}
          {profiles.length > 0 && (
            <>
              <div style={{ padding: '4px 10px', fontSize: 10, color: '#666', fontWeight: 600, borderTop: '1px solid #444' }}>
                历史配置
              </div>
              {profiles.map((p, i) => (
                <div key={p.key} onClick={() => { onSelectProfile(p); setShowDropdown(false); }}
                  style={{
                    padding: '7px 10px', cursor: 'pointer', fontSize: 12,
                    borderBottom: i < profiles.length - 1 ? '1px solid #333' : 'none',
                  }}
                  onMouseEnter={e => (e.currentTarget.style.background = '#333')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                >
                  <div style={{ fontWeight: 600, marginBottom: 1 }}>{p.label}</div>
                  <div style={{ fontSize: 10, color: '#999', fontFamily: 'monospace' }}>
                    {maskKey(p.key)} · {p.base.replace(/https?:\/\//, '').split('/')[0]}
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function ConfigTriState({ label, value, onChange, desc, options }: {
  label: string; value: string; onChange: (v: string) => void;
  desc?: string; options: { value: string; label: string }[];
}) {
  return (
    <div className="agent-config-field">
      <div className="agent-config-field-label">{label}</div>
      <div className="agent-config-field-control">
        <div className="agent-config-segment">
          {options.map(o => (
            <button key={o.value}
              className={`agent-config-segment-btn ${value === o.value ? 'active' : ''}`}
              onClick={() => onChange(o.value)}>
              {o.label}
            </button>
          ))}
        </div>
      </div>
      {desc && <div className="agent-config-field-desc">{desc}</div>}
    </div>
  );
}

// ── AgentConfig builder (sends all settings to the Python agent) ──
function buildAgentConfig(): Record<string, any> {
  return {
    llm_protocol: lsGet(AGENT_PROTOCOL_KEY, 'auto'),
    vision_mode: lsGet(AGENT_VISION_KEY, 'auto'),
    planning: lsGet(AGENT_PLANNING_KEY, 'auto'),
    screen_context: lsGet(AGENT_CONTEXT_KEY, 'auto'),
    on_error: lsGet(AGENT_ONERROR_KEY, 'retry'),
    multimodal: lsGet(AGENT_MULTIMODAL_KEY, 'auto'),
    verify: lsGetBool(AGENT_VERIFY_KEY, true),
    fast_mode: lsGetBool(AGENT_FAST_MODE_KEY, false),
    image_dedup: lsGetBool(AGENT_DEDUP_KEY, true),
    window_aware_ocr: lsGetBool(AGENT_WINDOW_AWARE_OCR_KEY, true),
    uia_pre_focus: lsGetBool(AGENT_UIA_PREFOCUS_KEY, true),
    scripted_mode: lsGetBool(AGENT_SCRIPTED_MODE_KEY, false),
    auto_learn: lsGetBool(AGENT_AUTO_LEARN_KEY, false),
    som_enabled: lsGetBool(AGENT_SOM_KEY, true),
    flow_chart_mode: lsGetBool(AGENT_FLOW_CHART_KEY, false),
    max_steps: lsGetNum(AGENT_MAX_STEPS_KEY, 20),
    max_consecutive_failures: lsGetNum(AGENT_MAX_CONSECUTIVE_FAILS_KEY, 5),
    min_success_rate: lsGetNum(AGENT_MIN_SUCCESS_RATE_KEY, 0.1),
    temperature: lsGetNum(AGENT_TEMPERATURE_KEY, 0.1),
    max_tokens: lsGetNum(AGENT_MAX_TOKENS_KEY, 4096),
    llm_retries: lsGetNum(AGENT_LLM_RETRIES_KEY, 2),
    screenshot_quality: lsGetNum(AGENT_SCREENSHOT_QUALITY_KEY, 60),
    sliding_window_size: lsGetNum(AGENT_SLIDING_WINDOW_KEY, 0),
    vision_backend: lsGet(AGENT_VISION_BACKEND_KEY, 'gpt-4o-mini'),
    chat_mode: lsGetBool(AGENT_CHAT_MODE_KEY, false),
    visual_strategy: lsGet(AGENT_VISUAL_STRATEGY_KEY, 'auto'),
    local_vision: lsGetBool(AGENT_LOCAL_VISION_KEY, false),
  };
}

// ── Multi-modal model detection (frontend mirror) ──
function _isMultimodalModel(model: string): boolean {
  const m = model.toLowerCase().trim();
  const mmPatterns = [/^gpt-4o/, /^claude/, /^gemini/, /^qwen-vl/, /^qwen2\.5-vl/,
    /^glm-4v/, /^step-1v/, /^kimi/, /^moonshot-v/, /^reka/, /^llava/,
    /^cogvlm/, /^internvl/, /^yi-vl/, /mimo/i, /vision/i, /multimodal/i];
  return mmPatterns.some(p => p.test(m));
}

// ── Resolve "auto" for display ──
function _resolveAuto(field: string, model: string, multimodalOverride: string): string {
  const isMM = multimodalOverride === 'true'
    ? true : multimodalOverride === 'false' ? false : _isMultimodalModel(model);
  const map: Record<string, Record<string, string>> = {
    llm_protocol: { true: '文本协议', false: '函数调用' },
    vision_mode: { true: '直接截图', false: 'SoM 标号' },
    planning: { true: '逐步骤 (ReAct)', false: '预生成计划 (DAG)' },
    screen_context: { true: '精简', false: '完整' },
    on_error: { true: '重试', false: '重试' },
  };
  return map[field]?.[String(isMM)] || '自动';
}

// ═══════════════════════════════════════════════════════════════════
//  Main Component
// ═══════════════════════════════════════════════════════════════════

export function AgentView() {
  // ── 迁移旧 key 到配置档案 ──
  useEffect(() => {
    const profiles = getProfiles();
    const oldKey = localStorage.getItem(KEY_STORAGE);
    if (oldKey && oldKey.length > 8 && !profiles.some(p => p.key === oldKey)) {
      const oldBase = localStorage.getItem(BASE_STORAGE) || 'https://api.deepseek.com/v1';
      const oldModel = localStorage.getItem(MODEL_STORAGE) || 'deepseek-chat';
      profiles.unshift({ key: oldKey, base: oldBase, model: oldModel, label: oldModel });
      localStorage.setItem(PROFILE_HISTORY_STORAGE, JSON.stringify(profiles.slice(0, 10)));
    }
  }, []);

  // ── Core state ──
  const [goal, setGoal] = useState(() => sessionStorage.getItem(_STORAGE_KEY) || '');
  const [running, setRunning] = useState(false);
  const [generatedScript, setGeneratedScript] = useState<{ pseudocode: string; explanation: string } | null>(null);
  const [log, setLog] = useState<LogEntry[]>(() => _persistedLog);
  const [guidance, setGuidance] = useState('');
  const msgEndRef = useRef<HTMLDivElement>(null);
  const goalRef = useRef(goal);
  goalRef.current = goal;

  // ── Agent tool explorer ──
  const [showTools, setShowTools] = useState(false);
  const [agentTools, setAgentTools] = useState<any[]>([]);
  const [toolsLoading, setToolsLoading] = useState(false);
  const [toolsViewMode, setToolsViewMode] = useState<'grouped' | 'all'>('grouped');

  // ── Mode state ──
  const [offline, setOffline] = useState(false);
  const [offlineRunning, setOfflineRunning] = useState(false);
  const [listening, setListening] = useState(false);
  const [voiceSupported] = useState(() => !!((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition));
  const [voiceConversationMode, setVoiceConversationMode] = useState(false);
  const [voicePanelMinimized, setVoicePanelMinimized] = useState(false);

  // ── Completion summary ──
  const [lastSummary, setLastSummary] = useState<{ status: string; steps: number; goal: string } | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const agentHistory = useStore((s) => s.agentHistory);

  // ── Self-evolving agent: reflection + learning ──
  const [lastLearning, setLastLearning] = useState<{
    insights: string[]; mistakes: string[]; rulesCount: number; prefsCount: number;
  } | null>(null);
  const [memoryStats, setMemoryStats] = useState<{
    preferences: number; rules: number; episodes: number;
  } | null>(null);

  // ── Node expand/collapse state ──
  const [nodeExpanded, setNodeExpanded] = useState<Record<string, boolean>>({
    llm: true, perceive: true, act: true, safety: true,
  });
  const toggleNode = (name: string) => setNodeExpanded(prev => ({ ...prev, [name]: !prev[name] }));

  // ── Config values ──
  const [apiKey, setApiKey] = useState(() => lsGet(KEY_STORAGE, ''));
  const [apiBase, setApiBase] = useState(() => lsGet(BASE_STORAGE, 'https://api.deepseek.com/v1'));
  const [model, setModel] = useState(() => lsGet(MODEL_STORAGE, 'deepseek-chat'));
  const [agentProtocol, setAgentProtocol] = useState(() => lsGet(AGENT_PROTOCOL_KEY, 'auto'));
  const [agentVision, setAgentVision] = useState(() => lsGet(AGENT_VISION_KEY, 'auto'));
  const [agentPlanning, setAgentPlanning] = useState(() => lsGet(AGENT_PLANNING_KEY, 'auto'));
  const [agentContext, setAgentContext] = useState(() => lsGet(AGENT_CONTEXT_KEY, 'auto'));
  const [agentOnError, setAgentOnError] = useState(() => lsGet(AGENT_ONERROR_KEY, 'retry'));
  const [agentMultimodal, setAgentMultimodal] = useState(() => lsGet(AGENT_MULTIMODAL_KEY, 'auto'));
  const [agentFastMode, setAgentFastMode] = useState(() => lsGetBool(AGENT_FAST_MODE_KEY, false));
  const [agentDedup, setAgentDedup] = useState(() => lsGetBool(AGENT_DEDUP_KEY, true));
  const [agentWindowAwareOcr, setAgentWindowAwareOcr] = useState(() => lsGetBool(AGENT_WINDOW_AWARE_OCR_KEY, true));
  const [agentUiaPrefocus, setAgentUiaPrefocus] = useState(() => lsGetBool(AGENT_UIA_PREFOCUS_KEY, true));
  const [agentScriptedMode, setAgentScriptedMode] = useState(() => lsGetBool(AGENT_SCRIPTED_MODE_KEY, false));
  const [agentVerify, setAgentVerify] = useState(() => lsGetBool(AGENT_VERIFY_KEY, true));
  const [agentAutoLearn, setAgentAutoLearn] = useState(() => lsGetBool(AGENT_AUTO_LEARN_KEY, false));
  const [agentOmniparser, setAgentOmniparser] = useState(() => lsGetBool(AGENT_OMNIPARSER_KEY, false));
  const [agentSom, setAgentSom] = useState(() => lsGetBool(AGENT_SOM_KEY, true));
  const [agentLocalVision, setAgentLocalVision] = useState(() => lsGetBool(AGENT_LOCAL_VISION_KEY, false));
  const [agentFlowChart, setAgentFlowChart] = useState(() => lsGetBool(AGENT_FLOW_CHART_KEY, false));
  const [agentScreenshotQuality, setAgentScreenshotQuality] = useState(() => lsGetNum(AGENT_SCREENSHOT_QUALITY_KEY, 60));
  const [agentMaxSteps, setAgentMaxSteps] = useState(() => lsGetNum(AGENT_MAX_STEPS_KEY, 20));
  const [agentMaxConsecutiveFails, setAgentMaxConsecutiveFails] = useState(() => lsGetNum(AGENT_MAX_CONSECUTIVE_FAILS_KEY, 5));
  const [agentMinSuccessRate, setAgentMinSuccessRate] = useState(() => lsGetNum(AGENT_MIN_SUCCESS_RATE_KEY, 0.1));
  const [agentTemperature, setAgentTemperature] = useState(() => lsGetNum(AGENT_TEMPERATURE_KEY, 0.1));
  const [agentMaxTokens, setAgentMaxTokens] = useState(() => lsGetNum(AGENT_MAX_TOKENS_KEY, 4096));
  const [agentLlmRetries, setAgentLlmRetries] = useState(() => lsGetNum(AGENT_LLM_RETRIES_KEY, 2));
  const [agentSlidingWindow, setAgentSlidingWindow] = useState(() => lsGetNum(AGENT_SLIDING_WINDOW_KEY, 0));

  // ── Chat mode & visual strategy ──
  const [agentChatMode, setAgentChatMode] = useState(() => lsGetBool(AGENT_CHAT_MODE_KEY, false));
  const [agentVisualStrategy, setAgentVisualStrategy] = useState(() => lsGet(AGENT_VISUAL_STRATEGY_KEY, 'auto'));
  const [chatMessages, setChatMessages] = useState<{ role: 'user' | 'agent'; text: string }[]>([]);

  // ── Tool explorer ──
  const loadAgentTools = async () => {
    setToolsLoading(true);
    try {
      const api = (window as any).electronAPI;
      const r = await api?.callFeature?.('list_features', {});
      const list = Array.isArray(r) ? r : r?.result || [];
      const filtered = list.filter((f: any) => !f.deprecated && !f.hidden);
      setAgentTools(filtered);
    } catch { setAgentTools([]); }
    setToolsLoading(false);
  };

  const handleSetGoal = useCallback((val: string) => {
    setGoal(val);
    sessionStorage.setItem(_STORAGE_KEY, val);
  }, []);

  const addEntry = useCallback((entry: LogEntry) => {
    const entryWithId = { ...entry, _id: ++_logIdCounter } as any;
    _persistedLog = [..._persistedLog, entryWithId];
    setLog(_persistedLog);
  }, []);

  useEffect(() => {
    msgEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [log]);

  // ── Agent IPC listener ──
  useEffect(() => {
    const api = window.electronAPI;
    if (!api) return;

    const unsubMessages = api.onAgentMessage?.((msg: any) => {
      switch (msg.type) {
        case 'agent_start':
          _hasActiveRun = true;
          _cachedSteps = null;
          addEntry({ type: 'system', text: `正在处理：${msg.goal}` });
          setRunning(true);
          setLastSummary(null);
          break;

        case 'plan_state':
          if (msg.plan?.nodes?.length > 0 && !msg.final) {
            addEntry({ type: 'system', text: `计划已生成：${msg.plan.nodes.length} 个步骤` });
          }
          break;

        case 'step_attempt':
          addEntry({ type: 'system', text: `[重试] ${msg.tool} (第 ${msg.attempt} 次)` });
          break;

        case 'step_complete': {
          addEntry({ type: 'step', data: msg });
          if (msg.tool === 'generate_flow_script') {
            if (msg.result?.success) {
              const data = msg.result.data || {};
              const pseudocode = data.pseudocode || data.result?.result?.pseudocode || data.result?.pseudocode;
              const explanation = data.explanation || data.result?.result?.explanation || data.result?.explanation || '';
              if (pseudocode) {
                addEntry({ type: 'script', pseudocode, explanation });
                setGeneratedScript({ pseudocode, explanation });
                const steps = data.structured_steps || data.result?.structured_steps || [];
                if (Array.isArray(steps) && steps.length > 0) _cachedSteps = steps;
                loadScriptToCanvas(pseudocode);
              } else {
                addEntry({ type: 'system', text: '⚠️ AI 返回了空脚本，请重试或修改描述' });
              }
            } else {
              const errMsg = msg.result?.error || msg.result?.data?.error || '未知错误';
              addEntry({ type: 'system', text: `⚠️ 生成流程图失败: ${errMsg}` });
            }
          }
          break;
        }

        case 'user_guidance':
          addEntry({ type: 'guidance', text: msg.text });
          break;

        case 'user_guidance_sent':
          addEntry({ type: 'user', text: msg.text });
          break;

        case 'agent_chat_response':
          setChatMessages(prev => [...prev, { role: 'agent', text: msg.text }]);
          addEntry({ type: 'system', text: `🤖 AI回复: ${msg.text.slice(0, 200)}` });
          break;

        case 'agent_reflection':
          addEntry({ type: 'system', text: `[反思] ${msg.insight?.slice(0, 200)}` });
          if (msg.rule) {
            addEntry({ type: 'system', text: `[学习] 规则: ${msg.rule.slice(0, 200)}` });
          }
          break;

        case 'agent_learned':
          setLastLearning({
            insights: Array.isArray(msg.insights) ? msg.insights : [],
            mistakes: Array.isArray(msg.mistakes) ? msg.mistakes : [],
            rulesCount: typeof msg.rules_count === 'number' ? msg.rules_count : 0,
            prefsCount: typeof msg.prefs_count === 'number' ? msg.prefs_count : 0,
          });
          if (Array.isArray(msg.insights) && msg.insights.length > 0) {
            addEntry({ type: 'system', text: `[学习] 提取了 ${msg.rules_count} 条规则, ${msg.prefs_count} 个偏好` });
          }
          break;

        case 'memory_snapshot':
          setMemoryStats({
            preferences: typeof msg.preferences === 'number' ? msg.preferences : 0,
            rules: typeof msg.rules === 'number' ? msg.rules : 0,
            episodes: typeof msg.episodes === 'number' ? msg.episodes : 0,
          });
          break;

        case 'agent_done':
          if (!_hasActiveRun) return;
          if (msg.status === 'completed' && msg.total_steps > 0) {
            addEntry({ type: 'complete', steps: msg.total_steps });
          } else {
            const reason = msg.fail_reason || (msg.status === 'failed' ? 'AI 处理失败，请检查 API 密钥是否有效' : '');
            addEntry({ type: 'fail', reason });
          }
          setRunning(false);
          _hasActiveRun = false;
          const planState = useStore.getState().execution.plan;
          setLastSummary({
            status: msg.status,
            steps: msg.total_steps || 0,
            goal: msg.goal || planState?.goal || goalRef.current.slice(0, 80),
          });
          break;

        case 'log':
          if (msg.level === 'error') addEntry({ type: 'system', text: `[错误] ${msg.message}` });
          else if (msg.level === 'warn') addEntry({ type: 'system', text: `[警告] ${msg.message}` });
          else addEntry({ type: 'system', text: msg.message });
          break;

        case 'error':
          addEntry({ type: 'system', text: `[错误] ${msg.message}` });
          break;
      }
    });

    const unsubLog = api.onAgentLog?.((msg: any) => {
      if (msg.level === 'error') addEntry({ type: 'system', text: `[进程] ${msg.message}` });
    });

    return () => { unsubMessages?.(); unsubLog?.(); };
  }, [addEntry]);

  // ── Engine IPC listener (offline mode) ──
  useEffect(() => {
    const api = window.electronAPI;
    if (!api) return;

    const unsubEngineMsg = api.onEngineMessage?.((msg: any) => {
      switch (msg.type) {
        case 'node_done': {
          const nodeLabel = msg.node_type || msg.node_id || '';
          addEntry({
            type: 'step',
            data: {
              step_num: msg.node_index || 0,
              tool: nodeLabel,
              thought: '',
              result: { success: !msg.error, confidence: msg.error ? 0 : 1 },
              error: msg.error,
            },
          });
          break;
        }
        case 'execution_done':
          if (msg.status === 'completed') addEntry({ type: 'complete', steps: 0 });
          else addEntry({ type: 'fail', reason: msg.error || '执行失败' });
          setOfflineRunning(false);
          setRunning(false);
          _hasActiveRun = false;
          setLastSummary({
            status: msg.status === 'completed' ? 'completed' : 'failed',
            steps: msg.total_nodes || 0,
            goal: goalRef.current.slice(0, 80),
          });
          break;
      }
    });
    const unsubEngineLog = api.onEngineLog?.((msg: any) => {
      if (msg.level === 'error') addEntry({ type: 'system', text: `[引擎] ${msg.message}` });
      else addEntry({ type: 'system', text: msg.message });
    });
    return () => { unsubEngineMsg?.(); unsubEngineLog?.(); };
  }, [addEntry]);

  // ── Run agent ──
  const runAgent = useCallback(async (supervise: boolean) => {
    if (!goal.trim()) return;
    _hasActiveRun = false;
    if (supervise) { _persistedLog = []; setLog([]); }
    addEntry({ type: 'system', text: supervise ? '正在启动执行...' : '正在生成流程图...' });

    const rawKey = (window as any).__DEEPSEEK_KEY__ || lsGet(KEY_STORAGE, '');
    const rawBase = ((window as any).__LLM_API_BASE__ || lsGet(BASE_STORAGE, '')).replace(/\/+$/, '');
    const rawModel = (window as any).__LLM_MODEL__ || lsGet(MODEL_STORAGE, '');
    const multimodalSetting = lsGet(LLM_MULTIMODAL_STORAGE, 'false');

    const apiKey_ = rawKey.trim();
    const apiBase_ = rawBase.trim();
    const model_ = rawModel.trim();

    if (!apiKey_) {
      addEntry({ type: 'system', text: '⚠️ 请先在 "LLM / API" 节点中配置 API 密钥后重试' });
      setRunning(false);
      return;
    }
    // Save full profile for dropdown reuse
    saveProfile(apiKey_, apiBase_, model_);

    try {
      const stepsArg = supervise ? _cachedSteps : undefined;
      const agentConfig = buildAgentConfig();
      await window.electronAPI!.agentStart(goal.trim(), {
        api_key: apiKey_,
        api_base: apiBase_,
        model: model_,
        multimodal: multimodalSetting,
        verify_enabled: agentVerify ? 'true' : 'false',
        auto_learn: agentAutoLearn ? 'true' : 'false',
        omniparser_enabled: agentOmniparser ? 'true' : 'false',
        agent_config: agentConfig,
      }, supervise, stepsArg ?? undefined);
    } catch (err: any) {
      addEntry({ type: 'fail', reason: `启动失败: ${err.message}` });
    }
  }, [goal, addEntry, agentVerify, agentAutoLearn, agentOmniparser]);

  const stopAgent = useCallback(async () => {
    try {
      if (offline) { await window.electronAPI!.engineStop(); setOfflineRunning(false); }
      else { await window.electronAPI!.agentStop(); }
      addEntry({ type: 'system', text: '已停止' });
      setRunning(false);
      _hasActiveRun = false;
      setLastSummary({ status: 'failed', steps: 0, goal: goalRef.current.slice(0, 80) });
    } catch (err: any) {
      addEntry({ type: 'system', text: `停止失败: ${err.message}` });
    }
  }, [addEntry, offline]);

  const sendGuidance = useCallback(async () => {
    if (!guidance.trim() || !running) return;
    try { await window.electronAPI!.agentGuidance(guidance.trim()); } catch {}
    setGuidance('');
  }, [guidance, running]);

  const sendChatMessage = useCallback(async () => {
    if (!guidance.trim() || !running) return;
    setChatMessages(prev => [...prev, { role: 'user', text: guidance.trim() }]);
    addEntry({ type: 'user', text: guidance.trim() });
    try { await window.electronAPI!.agentChat(guidance.trim()); } catch {}
    setGuidance('');
  }, [guidance, running, addEntry]);

  const runOffline = useCallback(async () => {
    if (!goal.trim()) return;
    _hasActiveRun = false; _persistedLog = []; setLog([]); setGeneratedScript(null);
    addEntry({ type: 'system', text: '正在离线执行流程图...' });
    setOfflineRunning(true); setRunning(true);
    try { window.electronAPI!.engineStart(JSON.stringify(useStore.getState().script)); }
    catch (err: any) { addEntry({ type: 'fail', reason: `离线执行失败: ${err.message}` }); setRunning(false); setOfflineRunning(false); _hasActiveRun = false; }
  }, [goal, addEntry]);

  const startVoiceInput = useCallback(() => {
    if (listening) return;
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) { addEntry({ type: 'system', text: '⚠️ 浏览器不支持语音识别' }); return; }
    const recognition = new SR();
    recognition.lang = 'zh-CN'; recognition.continuous = false; recognition.interimResults = true;
    recognition.onstart = () => setListening(true);
    recognition.onerror = (e: any) => { setListening(false); addEntry({ type: 'system', text: `⚠️ 语音识别失败: ${e.error === 'not-allowed' ? '请允许麦克风权限' : e.error}` }); };
    recognition.onend = () => setListening(false);
    recognition.onresult = (e: any) => {
      let final = '';
      for (let i = e.resultIndex; i < e.results.length; i++) { if (e.results[i].isFinal) final += e.results[i][0].transcript; }
      if (final) handleSetGoal(goalRef.current ? goalRef.current + '，' + final : final);
    };
    try { recognition.start(); } catch { setListening(false); }
  }, [listening, handleSetGoal, addEntry]);

  const loadScriptToCanvas = useCallback(async (pseudocode: string) => {
    try {
      const { parsePseudoToFlow } = await import('../utils/pseudoParser');
      const script = parsePseudoToFlow(pseudocode);
      script.meta = { ...script.meta, id: useStore.getState().script.meta.id, name: useStore.getState().script.meta.name + ' (Agent)' };
      useStore.getState().loadScript(script);
      useStore.getState().setViewMode('canvas');
    } catch {}
  }, []);

  // ── Save helpers ──
  const saveStr = (k: string, v: string, setter: (v: string) => void) => { setter(v); lsSet(k, v); };
  const saveBool = (k: string, v: boolean, setter: (v: boolean) => void) => { setter(v); lsSetBool(k, v); };
  const saveNum = (k: string, v: number, setter: (v: number) => void) => { setter(v); lsSetNum(k, v); };

  // ── Resolve display helpers ──
  const resolvedMM = agentMultimodal === 'true' ? true : agentMultimodal === 'false' ? false : _isMultimodalModel(lsGet(MODEL_STORAGE, ''));
  const hasApiKey = !!apiKey.trim();

  const modeLabel = (val: string, label: string): string => {
    const map: Record<string, string> = {
      auto: '⚡自动', text: '◉文本', function_calling: '◉函数', computer_use: '◉CU',
      direct: '◉截图', som: '◉SoM', omniparser: '◉Omni',
      pre_plan: '◉DAG', per_step: '◉ReAct',
      retry: '◉重试', replan: '◉重规划', skip: '◉跳过', stop: '◉停止',
    };
    return `${map[val] || val}`;
  };

  // ── Render helpers ──

  const FlowArrow = () => (
    <div className="agent-flow-arrow">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
        <path d="M12 5v14M7 14l5 5 5-5" />
      </svg>
    </div>
  );

  // (BranchSplit/BranchMerge/BranchLabel/BranchRow removed — replaced by execution loop)

  const renderLogEntries = () => (
    <>
      {log.length === 0 && !offline && (
        <div className="agent-empty">输入目标后点击「生成流程图」，Agent 会自动分析任务并生成可视化流程图。</div>
      )}
      {log.length === 0 && offline && (
        <div className="agent-empty">离线模式：在画布上编辑好流程图，输入描述后点击「离线执行」直接运行。</div>
      )}
      {log.map((entry) => (
        <div key={entry._id!} className={`agent-msg agent-msg-${entry.type === 'script' ? 'system' : entry.type}`}>
          {entry.type === 'system' && <span>{entry.text}</span>}
          {entry.type === 'step' && (
            <>
              <span className="agent-step-num">[{entry.data.step_num}]</span>{' '}
              <span className="agent-action">{entry.data.tool}</span>
              {(entry.data.step_label || entry.data.tool !== entry.data.step_type) && (
                <span className="agent-step-label"> — {entry.data.step_label || ''}</span>
              )}
              <span className="agent-conf"> conf={entry.data.result?.confidence?.toFixed(2) ?? 'N/A'}</span>
              {entry.data.result?.success ? ' ✓' : ' ✗'}
              {(entry.data.thought || entry.data.error) && (
                <div className="agent-thought">{(entry.data.error || entry.data.thought?.slice(0, 200)) || ''}</div>
              )}
            </>
          )}
          {entry.type === 'user' && <><span className="agent-label">你：</span>{entry.text}</>}
          {entry.type === 'guidance' && <><span className="agent-label">Agent 接收：</span>{entry.text}</>}
          {entry.type === 'complete' && <strong>完成！共 {entry.steps} 步</strong>}
          {entry.type === 'fail' && <strong>失败：{entry.reason}</strong>}
          {entry.type === 'script' && (
            <div className="agent-result">
              {entry.explanation && <p className="agent-result-explanation">{entry.explanation}</p>}
              <pre className="agent-result-pseudo">{entry.pseudocode}</pre>
              <div className="agent-result-actions">
                <button className="agent-btn" style={{ fontSize: 12, padding: '4px 12px' }}
                  onClick={() => loadScriptToCanvas(entry.pseudocode)}>查看流程图</button>
              </div>
            </div>
          )}
        </div>
      ))}
      {running && log.length > 0 && !log.some(e => e.type === 'script' || e.type === 'complete' || e.type === 'fail') && (
        <div className="agent-msg agent-msg-system" style={{ opacity: 0.6 }}>
          <span className="agent-spinner" /> {offlineRunning ? '正在执行...' : '正在等待 AI 响应...'}
        </div>
      )}
      <div ref={msgEndRef} />
    </>
  );

  const showPlanNode = !!(generatedScript || running || lastSummary) && !voiceConversationMode;
  const showExecNode = (log.length > 0 || running) && !voiceConversationMode;
  const showResultNode = !!lastSummary && !running && !voiceConversationMode;

  // ═══════════════════════════════════════════════════════════════════
  //  Voice Conversation Mode
  // ═══════════════════════════════════════════════════════════════════

  if (voiceConversationMode) {
    return (
      <div className="agent-view">
        <VoiceConversationPanel
          visible={true} onClose={() => setVoiceConversationMode(false)}
          minimized={voicePanelMinimized} onToggleMinimize={() => setVoicePanelMinimized(!voicePanelMinimized)}
        />
        {showTools && renderToolExplorer()}
      </div>
    );
  }

  // ═══════════════════════════════════════════════════════════════════
  //  Main Layout
  // ═══════════════════════════════════════════════════════════════════

  return (
    <div className="agent-view">
      <div className="agent-flowchart">

        {/* ═══════ NODE 1: Goal + API Status ═══════ */}
        <div className="agent-flow-node goal-node">
          <div className="agent-flow-node-header">
            <Brain size={15} />
            <span>AI Agent</span>
            <span className={`agent-status ${offline ? 'offline' : running ? 'online' : 'online'}`}>
              ● {offline ? '离线' : running ? '运行中' : '就绪'}
            </span>
            {!hasApiKey && (
              <span className="agent-config-badge danger" style={{ marginLeft: 4, fontSize: 9, padding: '1px 6px' }}>
                未配置密钥
              </span>
            )}
            <button className="btn btn-ghost btn-icon" onClick={() => { setShowTools(true); loadAgentTools(); }}
              style={{ width: 22, height: 22 }} title="可用 Agent 工具">
              <List size={12} />
            </button>
            <label className="agent-mini-toggle" title="流程图模式：Agent 可调用 generate_flow / execute_flow">
              <input type="checkbox" checked={agentFlowChart}
                onChange={e => saveBool(AGENT_FLOW_CHART_KEY, e.target.checked, setAgentFlowChart)} />
              <span>流程图</span>
            </label>
            <button className={`btn btn-ghost btn-icon ${offline ? 'offline' : ''}`}
              onClick={() => setOffline(!offline)} style={{ width: 22, height: 22 }}
              title={offline ? '切换为在线模式' : '切换为离线模式'}>
              {offline ? <WifiOff size={12} /> : <Wifi size={12} />}
            </button>
            {voiceSupported && (
              <button className={`btn btn-ghost btn-icon ${voiceConversationMode ? 'voice-mode-active' : ''}`}
                onClick={() => setVoiceConversationMode(true)} style={{ width: 22, height: 22 }} title="语音对话">
                <Volume2 size={12} />
              </button>
            )}
          </div>
          <div className="agent-flow-node-body">
            <div className="agent-goal-row">
              <input className="agent-input" value={goal}
                onChange={e => { handleSetGoal(e.target.value); setGeneratedScript(null); }}
                onKeyDown={e => e.key === 'Enter' && !running && (offline ? runOffline() : runAgent(false))}
                placeholder={offline ? '输入描述后按 Enter 直接执行当前流程图' : '输入需求后按 Enter 生成流程图'}
                disabled={running} />
              {voiceSupported && (
                <button className={`btn btn-ghost btn-icon ${listening ? 'listening' : ''}`}
                  onClick={startVoiceInput} disabled={running}
                  style={{ width: 28, height: 28, flexShrink: 0, position: 'relative' }}
                  title={listening ? '聆听中...' : '语音输入'}>
                  {listening ? <MicOff size={14} /> : <Mic size={14} />}
                  {listening && <span className="voice-pulse" />}
                </button>
              )}
              {running ? (
                <button className="agent-btn agent-btn-danger" onClick={stopAgent} style={{ gap: 6 }}>
                  <span className="agent-spinner" /> 停止
                </button>
              ) : offline ? (
                <button className="agent-btn agent-btn-primary" onClick={runOffline} disabled={!goal.trim()} style={{ gap: 6 }}>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 2a4 4 0 014 4c0 2-2 4-4 6-2-2-4-4-4-6a4 4 0 014-4z"/><path d="M12 14c-4 0-8 2-8 4v2h16v-2c0-2-4-4-8-4z"/></svg>
                  离线执行
                </button>
              ) : (
                <button className="agent-btn agent-btn-primary" onClick={() => runAgent(false)} disabled={!goal.trim()} style={{ gap: 6 }}>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 2a4 4 0 014 4c0 2-2 4-4 6-2-2-4-4-4-6a4 4 0 014-4z"/><path d="M12 14c-4 0-8 2-8 4v2h16v-2c0-2-4-4-8-4z"/></svg>
                  生成流程图
                </button>
              )}
            </div>
          </div>
        </div>

        <FlowArrow />

        {/* ═══════ NODE 2: LLM Brain ═══════ */}
        <div className={`agent-flow-node compact llm-node ${nodeExpanded.llm ? 'expanded' : ''}`}>
          <div className="agent-flow-node-header clickable" onClick={() => toggleNode('llm')}>
            <MessageSquare size={13} />
            <span>LLM Brain</span>
            <span className="agent-mode-status">{modeLabel(agentProtocol, '协议')}</span>
            {agentPlanning !== 'auto' && (
              <span className="agent-mode-status" style={{ marginLeft: 2 }}>{modeLabel(agentPlanning, '')}</span>
            )}
            <span className={`agent-expand-arrow ${nodeExpanded.llm ? 'open' : ''}`}>▶</span>
          </div>
          {nodeExpanded.llm && (
            <div className="agent-flow-node-body">
              <div className="agent-node-settings">
                <ConfigApiKey label="API Key" value={apiKey}
                  onKeyChange={v => {
                    saveStr(KEY_STORAGE, v, setApiKey);
                    // key 变化时自动保存配置档案
                    saveProfile(v, apiBase, model);
                  }}
                  onSelectProfile={p => {
                    saveStr(KEY_STORAGE, p.key, setApiKey);
                    saveStr(BASE_STORAGE, p.base, setApiBase);
                    saveStr(MODEL_STORAGE, p.model, setModel);
                  }}
                  onPresetSelect={(base, mdl) => {
                    saveStr(BASE_STORAGE, base, setApiBase);
                    saveStr(MODEL_STORAGE, mdl, setModel);
                    if (apiKey) saveProfile(apiKey, base, mdl);
                  }} />
                <ConfigInput label="API Base" value={apiBase} onChange={v => {
                    saveStr(BASE_STORAGE, v, setApiBase);
                    if (apiKey) saveProfile(apiKey, v, model);
                  }} placeholder="https://api.deepseek.com/v1" />
                <ConfigInput label="模型" value={model} onChange={v => {
                    saveStr(MODEL_STORAGE, v, setModel);
                    if (apiKey) saveProfile(apiKey, apiBase, v);
                  }} placeholder="deepseek-chat" />
                <ConfigSelect label="协议" value={agentProtocol} onChange={v => saveStr(AGENT_PROTOCOL_KEY, v, setAgentProtocol)}
                  options={[
                    { value: 'auto', label: '自动' },
                    { value: 'text', label: '文本协议' },
                    { value: 'function_calling', label: '函数调用' },
                    { value: 'computer_use', label: 'Computer Use' },
                  ]}
                  resolved={_resolveAuto('llm_protocol', model, agentMultimodal)} />
                <ConfigSelect label="规划策略" value={agentPlanning} onChange={v => saveStr(AGENT_PLANNING_KEY, v, setAgentPlanning)}
                  options={[
                    { value: 'auto', label: '自动' },
                    { value: 'pre_plan', label: '预生成 (DAG)' },
                    { value: 'per_step', label: '逐步骤 (ReAct)' },
                  ]}
                  resolved={_resolveAuto('planning', model, agentMultimodal)} />
                <ConfigTriState label="多模态" value={agentMultimodal}
                  onChange={v => saveStr(AGENT_MULTIMODAL_KEY, v, setAgentMultimodal)}
                  options={[
                    { value: 'auto', label: '自动' },
                    { value: 'true', label: '开启' },
                    { value: 'false', label: '关闭' },
                  ]} />
                <ConfigSlider label="温度" value={agentTemperature}
                  min={0} max={2} step={0.05}
                  onChange={v => saveNum(AGENT_TEMPERATURE_KEY, v, setAgentTemperature)} />
                <ConfigSlider label="Max Tokens" value={agentMaxTokens}
                  min={512} max={16384} step={512}
                  onChange={v => saveNum(AGENT_MAX_TOKENS_KEY, v, setAgentMaxTokens)} />
                <ConfigSlider label="LLM 重试" value={agentLlmRetries}
                  min={0} max={5} step={1}
                  onChange={v => saveNum(AGENT_LLM_RETRIES_KEY, v, setAgentLlmRetries)} />
                <ConfigToggle label="对话模式" checked={agentChatMode}
                  onChange={v => saveBool(AGENT_CHAT_MODE_KEY, v, setAgentChatMode)}
                  desc="开启后边执行边聊天，可实时反馈干预" badge="新" />
              </div>
            </div>
          )}
        </div>

        {/* PlanVisualizer (after LLM generates a plan) */}
        {showPlanNode && (
          <>
            <FlowArrow />
            <div className="agent-flow-node compact plan-node">
              <div className="agent-flow-node-header" style={{ background: 'linear-gradient(135deg, rgba(245,158,11,0.08), rgba(245,158,11,0.02))' }}>
                <GitBranch size={13} />
                <span>生成计划</span>
                {running && <span className="agent-node-badge running">● 运行中</span>}
              </div>
              <div className="agent-flow-node-body" style={{ padding: '8px 10px' }}>
                <PlanVisualizer />
                {generatedScript && !running && !lastSummary && (
                  <div className="agent-plan-actions" style={{ marginTop: 8 }}>
                    <button className="agent-btn agent-btn-primary" onClick={() => runAgent(true)} style={{ gap: 6 }}>
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
                      执行计划
                    </button>
                    <button className="agent-btn" onClick={() => { setGeneratedScript(null); }} style={{ gap: 6 }}>
                      重新生成
                    </button>
                  </div>
                )}
              </div>
            </div>
          </>
        )}

        <FlowArrow />

        {/* ═══════ EXECUTION LOOP ═══════ */}
        <div className="agent-loop-container">
          <div className="agent-loop-header">
            <RefreshCw size={12} />
            <span>执行循环</span>
            {running && <span className="agent-spinner" style={{ marginLeft: 'auto', fontSize: 10 }} />}
          </div>

          {/* ── Decision: 还有步骤? ── */}
          <div className="agent-loop-decision">
            <div className="agent-loop-decision-shape">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M12 2L22 12L12 22L2 12Z" />
              </svg>
            </div>
            <span className="agent-loop-decision-text">还有步骤?</span>
            <div className="agent-loop-decision-tags">
              <span className="agent-loop-branch-tag no">NO → 结束</span>
              <span className="agent-loop-branch-tag yes">YES ↓</span>
            </div>
          </div>

          <FlowArrow />

          {/* ── Perceive 感知 ── */}
          <div className={`agent-flow-node compact vision-node ${nodeExpanded.perceive ? 'expanded' : ''}`}>
            <div className="agent-flow-node-header clickable" onClick={() => toggleNode('perceive')}>
              <Eye size={13} />
              <span>Perceive 感知</span>
              <span className="agent-mode-status">{modeLabel(agentVision, '模式')}</span>
              <span className={`agent-expand-arrow ${nodeExpanded.perceive ? 'open' : ''}`}>▶</span>
            </div>
            {nodeExpanded.perceive && (
              <div className="agent-flow-node-body">
                <div className="agent-node-settings">
                  <ConfigSelect label="视觉模式" value={agentVision} onChange={v => saveStr(AGENT_VISION_KEY, v, setAgentVision)}
                    options={[
                      { value: 'auto', label: '自动' },
                      { value: 'direct', label: '直接截图' },
                      { value: 'som', label: 'SoM 标号' },
                      { value: 'omniparser', label: 'OmniParser' },
                    ]}
                    resolved={_resolveAuto('vision_mode', model, agentMultimodal)} />
                  <ConfigToggle label="SoM 标号" checked={agentSom}
                    onChange={v => saveBool(AGENT_SOM_KEY, v, setAgentSom)} />
                  <ConfigToggle label="OmniParser" checked={agentOmniparser}
                    onChange={v => saveBool(AGENT_OMNIPARSER_KEY, v, setAgentOmniparser)} />
                  <ConfigToggle label="本地视觉引擎(YOLO GPU)" checked={agentLocalVision}
                    onChange={v => {
                      if (v && _isMultimodalModel(model)) {
                        alert('本地视觉引擎只对纯文本模型(如 DeepSeek)生效。\n当前使用多模态模型时此项无效，视觉将走直接截图路径。');
                      }
                      saveBool(AGENT_LOCAL_VISION_KEY, v, setAgentLocalVision);
                    }}
                    desc="YOLO + MobileNet + RapidOCR，离线 GPU 加速" />
                  <ConfigSlider label="截图质量" value={agentScreenshotQuality}
                    min={30} max={95} step={5}
                    onChange={v => saveNum(AGENT_SCREENSHOT_QUALITY_KEY, v, setAgentScreenshotQuality)} unit="%" />
                  <ConfigSelect label="屏幕上下文" value={agentContext} onChange={v => saveStr(AGENT_CONTEXT_KEY, v, setAgentContext)}
                    options={[
                      { value: 'auto', label: '自动' },
                      { value: 'minimal', label: '精简' },
                      { value: 'full', label: '完整' },
                    ]}
                    resolved={_resolveAuto('screen_context', model, agentMultimodal)} />
                  <ConfigToggle label="截图去重" checked={agentDedup}
                    onChange={v => saveBool(AGENT_DEDUP_KEY, v, setAgentDedup)} />
                  <ConfigSelect label="视觉策略" value={agentVisualStrategy}
                    onChange={v => saveStr(AGENT_VISUAL_STRATEGY_KEY, v, setAgentVisualStrategy)}
                    options={[
                      { value: 'auto', label: '自动' },
                      { value: 'som', label: 'SoM 标号 (方案4)' },
                      { value: 'ocr_llm', label: 'OCR→LLM→点击 (方案3)' },
                      { value: 'blind_guess', label: '自主盲猜坐标 (方案2)' },
                      { value: 'cli_path', label: 'CLI/路径优先 (方案1)' },
                    ]}
                    desc="多模态操控方案" />
                </div>
              </div>
            )}
          </div>

          <FlowArrow />

          {/* ── Act 执行 ── */}
          <div className={`agent-flow-node compact supervisor-node ${nodeExpanded.act ? 'expanded' : ''}`}>
            <div className="agent-flow-node-header clickable" onClick={() => toggleNode('act')}>
              <Zap size={13} />
              <span>Act 执行</span>
              <span className="agent-mode-status">{agentScriptedMode ? '脚本模式' : '工具模式'}</span>
              <span className={`agent-expand-arrow ${nodeExpanded.act ? 'open' : ''}`}>▶</span>
            </div>
            {nodeExpanded.act && (
              <div className="agent-flow-node-body">
                <div className="agent-node-settings">
                  <ConfigToggle label="窗口感知 OCR" checked={agentWindowAwareOcr}
                    onChange={v => saveBool(AGENT_WINDOW_AWARE_OCR_KEY, v, setAgentWindowAwareOcr)} />
                  <ConfigToggle label="UIA 预聚焦" checked={agentUiaPrefocus}
                    onChange={v => saveBool(AGENT_UIA_PREFOCUS_KEY, v, setAgentUiaPrefocus)} />
                  <ConfigToggle label="脚本执行" checked={agentScriptedMode}
                    onChange={v => saveBool(AGENT_SCRIPTED_MODE_KEY, v, setAgentScriptedMode)} />
                  <ConfigToggle label="快速模式" checked={agentFastMode}
                    onChange={v => saveBool(AGENT_FAST_MODE_KEY, v, setAgentFastMode)} />
                  <ConfigToggle label="自动学习" checked={agentAutoLearn}
                    onChange={v => saveBool(AGENT_AUTO_LEARN_KEY, v, setAgentAutoLearn)} />
                </div>
              </div>
            )}
          </div>

          <FlowArrow />

          {/* ── Verify 验证 ── */}
          <div className="agent-flow-node compact verify-node">
            <div className="agent-flow-node-header">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
                <polyline points="22 4 12 14.01 9 11.01" />
              </svg>
              <span>Verify 验证</span>
              <label className="agent-mini-toggle" style={{ marginLeft: 'auto' }}>
                <input type="checkbox" checked={agentVerify}
                  onChange={e => saveBool(AGENT_VERIFY_KEY, e.target.checked, setAgentVerify)} />
                <span>{agentVerify ? '开启' : '关闭'}</span>
              </label>
            </div>
          </div>

          {/* ── Decision: 验证成功? ── */}
          <div className="agent-loop-decision">
            <div className="agent-loop-decision-shape">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M12 2L22 12L12 22L2 12Z" />
              </svg>
            </div>
            <span className="agent-loop-decision-text">验证成功?</span>
            <div className="agent-loop-decision-tags">
              <span className="agent-loop-branch-tag yes">YES ↑ 继续</span>
              <span className="agent-loop-branch-tag no">NO ↓ 错误处理</span>
            </div>
          </div>

          <FlowArrow />

          {/* ── Error Handler + Safety ── */}
          <div className="agent-error-section">
            <div className="agent-error-header">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
                <line x1="12" y1="9" x2="12" y2="13" />
                <line x1="12" y1="17" x2="12.01" y2="17" />
              </svg>
              <span>错误处理</span>
            </div>
            <div className="agent-error-branch-row">
              <div className={`agent-error-card ${agentOnError === 'retry' ? 'active' : ''}`}
                onClick={() => saveStr(AGENT_ONERROR_KEY, 'retry', setAgentOnError)}>
                <span className="agent-error-card-label">重试</span>
                <span className="agent-error-card-arrow">↻ Act</span>
              </div>
              <div className={`agent-error-card ${agentOnError === 'replan' ? 'active' : ''}`}
                onClick={() => saveStr(AGENT_ONERROR_KEY, 'replan', setAgentOnError)}>
                <span className="agent-error-card-label">重规划</span>
                <span className="agent-error-card-arrow">↰ LLM</span>
              </div>
              <div className={`agent-error-card ${agentOnError === 'skip' ? 'active' : ''}`}
                onClick={() => saveStr(AGENT_ONERROR_KEY, 'skip', setAgentOnError)}>
                <span className="agent-error-card-label">跳过</span>
                <span className="agent-error-card-arrow">→ 下一步</span>
              </div>
              <div className={`agent-error-card ${agentOnError === 'stop' ? 'active' : ''}`}
                onClick={() => saveStr(AGENT_ONERROR_KEY, 'stop', setAgentOnError)}>
                <span className="agent-error-card-label">停止</span>
                <span className="agent-error-card-arrow">■ 结束</span>
              </div>
            </div>

            {/* Safety limits inline */}
            <div className={`agent-flow-node compact safety-node ${nodeExpanded.safety ? 'expanded' : ''}`}
              style={{ maxWidth: '100%', border: 'none', borderTop: '1px solid var(--border-primary)', borderRadius: 0, boxShadow: 'none' }}>
              <div className="agent-flow-node-header clickable" onClick={() => toggleNode('safety')}
                style={{ background: 'transparent', padding: '6px 4px' }}>
                <Shield size={11} />
                <span style={{ fontSize: 11 }}>安全限制</span>
                <span className="agent-mode-status" style={{ fontSize: 8 }}>{agentMaxSteps}步/{agentMaxConsecutiveFails}次失败</span>
                <span className={`agent-expand-arrow ${nodeExpanded.safety ? 'open' : ''}`}>▶</span>
              </div>
              {nodeExpanded.safety && (
                <div className="agent-flow-node-body" style={{ padding: '6px 4px' }}>
                  <div className="agent-node-settings">
                    <ConfigSlider label="最大步数" value={agentMaxSteps}
                      min={3} max={100} step={1}
                      onChange={v => saveNum(AGENT_MAX_STEPS_KEY, v, setAgentMaxSteps)} />
                    <ConfigSlider label="连续失败上限" value={agentMaxConsecutiveFails}
                      min={1} max={20} step={1}
                      onChange={v => saveNum(AGENT_MAX_CONSECUTIVE_FAILS_KEY, v, setAgentMaxConsecutiveFails)} />
                    <ConfigSlider label="最低成功率" value={agentMinSuccessRate}
                      min={0.01} max={0.5} step={0.01}
                      onChange={v => saveNum(AGENT_MIN_SUCCESS_RATE_KEY, v, setAgentMinSuccessRate)} />
                    <ConfigSlider label="滑动窗口" value={agentSlidingWindow}
                      min={0} max={30} step={1}
                      onChange={v => saveNum(AGENT_SLIDING_WINDOW_KEY, v, setAgentSlidingWindow)}
                      unit={agentSlidingWindow === 0 ? ' (自动)' : ''} />
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* ═══════ NODE 7: Execution ═══════ */}
        {showExecNode && <FlowArrow />}
        {showExecNode && (
          <div className="agent-flow-node exec-node">
            <div className="agent-flow-node-header">
              <Monitor size={14} />
              <span>执行</span>
              {running && <span className="agent-spinner" style={{ marginLeft: 6 }} />}
              {!running && lastSummary && (
                <span className={`agent-node-badge ${lastSummary.status === 'completed' ? 'done' : 'failed'}`}>
                  {lastSummary.status === 'completed' ? '✓ 完成' : '✗ 失败'}
                </span>
              )}
            </div>
            <div className="agent-flow-node-body">
              <div className="agent-log" style={{ minHeight: 80, maxHeight: 300 }}>
                {renderLogEntries()}
              </div>
              {running && !offline && (
                <>
                  {/* ── 对话模式: 聊天消息列表 ── */}
                  {agentChatMode && chatMessages.length > 0 && (
                    <div className="agent-chat-box">
                      {chatMessages.map((m, i) => (
                        <div key={i} className={`agent-chat-msg agent-chat-msg-${m.role}`}>
                          <span className="agent-chat-role">
                            {m.role === 'user' ? '🧑' : '🤖'}:
                          </span>
                          <span className="agent-chat-text">{m.text}</span>
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="agent-guidance-bar" style={{ marginTop: 8 }}>
                    <input className="agent-input" value={guidance}
                      onChange={e => setGuidance(e.target.value)}
                      onKeyDown={e => {
                        if (e.key === 'Enter') {
                          if (agentChatMode) sendChatMessage();
                          else sendGuidance();
                        }
                      }}
                      placeholder={agentChatMode ? "输入消息与 AI 对话..." : "给 agent 下达指令..."} />
                    <button className="agent-btn" onClick={agentChatMode ? sendChatMessage : sendGuidance}
                      disabled={!guidance.trim()}>
                      {agentChatMode ? '对话' : '发送'}
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        )}

        {/* ═══════ NODE 8: Result ═══════ */}
        {showResultNode && <FlowArrow />}
        {showResultNode && (
          <div className="agent-flow-node result-node">
            <div className="agent-flow-node-header">
              {lastSummary.status === 'completed'
                ? <span style={{ color: '#4CAF50', fontSize: 16 }}>✓</span>
                : <span style={{ color: '#F44336', fontSize: 16 }}>✗</span>}
              <span>结果</span>
              <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '40%' }}>
                {lastSummary.goal}
              </span>
            </div>
            <div className="agent-flow-node-body">
              <div className="agent-summary-stats" style={{ padding: 0 }}>
                {(() => {
                  const plan = useStore.getState().execution.plan;
                  const sum = plan?.summary;
                  const failedCount = sum?.failed || 0;
                  const skippedCount = sum?.skipped || 0;
                  return (
                    <>
                      <div className="agent-summary-stat">
                        <span className="agent-summary-stat-value">{lastSummary.steps}</span>
                        <span className="agent-summary-stat-label">总步数</span>
                      </div>
                      <div className="agent-summary-stat">
                        <span className="agent-summary-stat-value" style={{ color: '#4CAF50' }}>{sum?.completed || lastSummary.status === 'completed' ? lastSummary.steps : 0}</span>
                        <span className="agent-summary-stat-label">成功</span>
                      </div>
                      {failedCount > 0 && (
                        <div className="agent-summary-stat">
                          <span className="agent-summary-stat-value" style={{ color: '#F44336' }}>{failedCount}</span>
                          <span className="agent-summary-stat-label">失败</span>
                        </div>
                      )}
                      {skippedCount > 0 && (
                        <div className="agent-summary-stat">
                          <span className="agent-summary-stat-value" style={{ color: '#9E9E9E' }}>{skippedCount}</span>
                          <span className="agent-summary-stat-label">跳过</span>
                        </div>
                      )}
                    </>
                  );
                })()}
              </div>

              {/* ── 本任务学到的 (learning section) ── */}
              {lastLearning && (lastLearning.insights.length > 0 || lastLearning.mistakes.length > 0) && (
                <div className="agent-learning-section">
                  <div className="agent-learning-header">
                    <span className="agent-learning-icon">🧠</span>
                    <span>本任务学到的</span>
                  </div>
                  {lastLearning.insights.length > 0 && (
                    <div className="agent-learning-list">
                      {lastLearning.insights.map((insight: string, i: number) => (
                        <div key={i} className="agent-learning-item insight">{insight}</div>
                      ))}
                    </div>
                  )}
                  {lastLearning.mistakes.length > 0 && (
                    <div className="agent-learning-list">
                      {lastLearning.mistakes.map((mistake: string, i: number) => (
                        <div key={i} className="agent-learning-item mistake">{mistake}</div>
                      ))}
                    </div>
                  )}
                  <div className="agent-learning-meta">
                    提取了 {lastLearning.rulesCount} 条规则 · 更新了 {lastLearning.prefsCount} 个偏好
                  </div>
                </div>
              )}

              <div className="agent-summary-footer" style={{ padding: '8px 0 0', borderTop: 'none', background: 'transparent' }}>
                <button className="agent-btn" style={{ fontSize: 11, padding: '4px 12px' }}
                  onClick={() => { setLastSummary(null); const g = goalRef.current; if (g.trim()) runAgent(true); }}>
                  重新执行
                </button>
                {lastSummary.status === 'completed' && (
                  <button className="agent-btn" style={{ fontSize: 11, padding: '4px 12px' }}
                    onClick={() => { setLastSummary(null); _persistedLog = []; setLog([]); }}>
                    清除日志
                  </button>
                )}
              </div>
            </div>
          </div>
        )}

        {/* ── History ── */}
        {!running && agentHistory.length > 0 && (
          <>
            <div className="agent-flow-arrow" style={{ opacity: 0.25 }}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                <path d="M12 5v14" />
              </svg>
            </div>
            <div className="agent-flow-node history-node">
              <div className="agent-flow-node-header" style={{ cursor: 'pointer' }} onClick={() => setShowHistory(!showHistory)}>
                <Clock size={13} />
                <span>执行历史 ({agentHistory.length})</span>
                <span style={{ marginLeft: 'auto', fontSize: 10, transition: 'transform 0.15s', display: 'inline-block', transform: showHistory ? 'rotate(90deg)' : 'none' }}>▶</span>
              </div>
              {showHistory && (
                <div className="agent-flow-node-body" style={{ padding: 0 }}>
                  <div className="agent-history-panel" style={{ border: 'none', borderRadius: 0, maxHeight: 200 }}>
                    {agentHistory.slice(0, 20).map((h: AgentRunRecord) => (
                      <div key={h.id} className="agent-history-item"
                        onClick={() => { handleSetGoal(h.goal); setShowHistory(false); }} title="点击填入目标">
                        <div className={`agent-history-status ${h.status}`} />
                        <span className="agent-history-goal" title={h.goal}>{h.goal || '(无目标)'}</span>
                        <span className="agent-history-meta">
                          {h.total_steps} 步 · {h.elapsed_seconds}s
                          {h.failed_steps > 0 && <span style={{ color: '#F44336', marginLeft: 4 }}>✗{h.failed_steps}</span>}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </>
        )}

        {/* ── Memory (长期记忆节点) ── */}
        {memoryStats && !running && (
          <>
            <div className="agent-flow-arrow" style={{ opacity: 0.25 }}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                <path d="M12 5v14" />
              </svg>
            </div>
            <div className="agent-flow-node memory-node">
              <div className="agent-flow-node-header">
                <span style={{ fontSize: 13 }}>🧠</span>
                <span>长期记忆</span>
              </div>
              <div className="agent-flow-node-body">
                <div className="agent-memory-stats">
                  <div className="agent-memory-stat">
                    <span className="agent-memory-stat-value">{memoryStats.preferences}</span>
                    <span className="agent-memory-stat-label">偏好</span>
                  </div>
                  <div className="agent-memory-stat">
                    <span className="agent-memory-stat-value">{memoryStats.rules}</span>
                    <span className="agent-memory-stat-label">规则</span>
                  </div>
                  <div className="agent-memory-stat">
                    <span className="agent-memory-stat-value">{memoryStats.episodes}</span>
                    <span className="agent-memory-stat-label">经验</span>
                  </div>
                </div>
              </div>
            </div>
          </>
        )}

        <div style={{ height: 40, flexShrink: 0 }} />
      </div>

      {showTools && renderToolExplorer()}
    </div>
  );

  // ═══════════════════════════════════════════════════════════════════
  //  Tool Explorer Modal
  // ═══════════════════════════════════════════════════════════════════

  function renderToolExplorer() {
    const CAT_LABELS: Record<string, string> = {
      action: '交互动作', perception: '感知识别', control: '控制流',
      ai: 'AI 规划', macro: '宏指令', system: '系统功能', debug: '调试诊断',
    };
    const CAT_ORDER = ['action', 'perception', 'control', 'ai', 'macro', 'system', 'debug'];

    const grouped: Record<string, any[]> = {};
    for (const t of agentTools) {
      const cat = t.category || 'other';
      if (!grouped[cat]) grouped[cat] = [];
      grouped[cat].push(t);
    }

    const metaActions = [
      { name: 'click', desc: '点击屏幕目标（文字/坐标/编号）' },
      { name: 'type_text', desc: '向输入框输入文字' },
      { name: 'press_keys', desc: '键盘快捷键组合' },
      { name: 'scroll', desc: '滚动鼠标滚轮' },
      { name: 'hover', desc: '悬停到目标位置' },
      { name: 'drag', desc: '拖拽元素' },
      { name: 'read_text', desc: 'OCR 识别屏幕文字' },
      { name: 'read_ui', desc: '读取 UIA 控件树' },
      { name: 'screenshot', desc: '截取屏幕' },
      { name: 'get_cursor', desc: '获取鼠标位置' },
      { name: 'manage_window', desc: '窗口管理（聚焦/移动/调整）' },
      { name: 'list_windows', desc: '列出所有打开窗口' },
      { name: 'manage_app', desc: '启动/停止应用' },
      { name: 'control_audio', desc: '音量控制' },
      { name: 'system_info', desc: '系统信息查询' },
      { name: 'set_brightness', desc: '设置屏幕亮度' },
      { name: 'lock_screen', desc: '锁定屏幕' },
      { name: 'notify', desc: '发送系统通知' },
      { name: 'dialog', desc: '处理对话框（保存/关闭/确认）' },
      { name: 'browser_do', desc: '浏览器自动化操作' },
      { name: 'network', desc: '网络/WiFi 管理' },
      { name: 'run_command', desc: '执行系统命令' },
      { name: 'file_ops', desc: '文件操作（读写/复制/删除）' },
      { name: 'wait', desc: '等待指定时间' },
      { name: 'generate_flow', desc: '用 LLM 生成流程图脚本（需开启流程图模式）' },
      { name: 'execute_flow', desc: '执行已生成的流程图（需开启流程图模式）' },
    ];

    return (
      <div style={{
        position: 'fixed', inset: 0, zIndex: 1100,
        background: 'rgba(0,0,0,0.4)', display: 'flex',
        alignItems: 'center', justifyContent: 'center',
      }} onClick={() => setShowTools(false)}>
        <div onClick={e => e.stopPropagation()} style={{
          background: 'var(--bg-primary)', borderRadius: 12,
          boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
          width: 520, maxHeight: '80vh', display: 'flex', flexDirection: 'column',
        }}>
          <div style={{
            padding: '14px 16px', borderBottom: '1px solid var(--border-primary)',
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            fontSize: 15, fontWeight: 600,
          }}>
            <span>Agent 可用工具</span>
            <button className="btn btn-ghost btn-icon" onClick={() => setShowTools(false)}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M18 6L6 18M6 6l12 12"/></svg>
            </button>
          </div>
          <div style={{
            padding: '8px 16px', fontSize: 11, color: 'var(--text-muted)',
            borderBottom: '1px solid var(--border-primary)',
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          }}>
            <span>Agent 在执行任务时可调用以下工具</span>
            <div style={{ display: 'flex', gap: 4 }}>
              <button className={`btn btn-ghost ${toolsViewMode === 'grouped' ? 'active' : ''}`}
                onClick={() => setToolsViewMode('grouped')} style={{ fontSize: 11, padding: '2px 8px' }}>分类</button>
              <button className={`btn btn-ghost ${toolsViewMode === 'all' ? 'active' : ''}`}
                onClick={() => setToolsViewMode('all')} style={{ fontSize: 11, padding: '2px 8px' }}>全部</button>
            </div>
          </div>
          <div style={{ flex: 1, overflow: 'auto', padding: 8 }}>
            {toolsLoading ? (
              <div style={{ textAlign: 'center', padding: 20, fontSize: 12, color: 'var(--text-muted)' }}>加载中...</div>
            ) : toolsViewMode === 'all' ? (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3 }}>
                {agentTools.map((t: any) => (
                  <span key={t.name} title={t.description || ''} style={{
                    fontSize: 11, padding: '2px 8px', borderRadius: 4,
                    background: 'var(--bg-secondary)', border: '1px solid var(--border-primary)',
                    color: 'var(--text-primary)',
                  }}>{t.name}</span>
                ))}
              </div>
            ) : (
              <>
                <div style={{ marginBottom: 12 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, padding: '4px 0', color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: 6 }}>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
                    元动作（常用，推荐优先使用）
                    <span style={{ fontSize: 10, color: 'var(--text-muted)', fontWeight: 400 }}>{metaActions.length} 个</span>
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3, paddingLeft: 4 }}>
                    {metaActions.map(m => {
                      const isFlowOnly = m.name === 'generate_flow' || m.name === 'execute_flow';
                      return (
                        <span key={m.name} title={m.desc} style={{
                          fontSize: 11, padding: '2px 8px', borderRadius: 4,
                          background: isFlowOnly ? '#fff3e0' : '#e8f5e9',
                          border: isFlowOnly ? '1px solid #ffe0b2' : '1px solid #c8e6c9',
                          color: isFlowOnly ? '#e65100' : '#2e7d32',
                          cursor: 'default',
                        }}>{m.name}{isFlowOnly ? ' 🔒' : ''}</span>
                      );
                    })}
                  </div>
                </div>
                {CAT_ORDER.map(cat => {
                  const tools = grouped[cat];
                  if (!tools || tools.length === 0) return null;
                  return (
                    <div key={cat} style={{ marginBottom: 10 }}>
                      <div style={{ fontSize: 12, fontWeight: 600, padding: '4px 0', color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: 6 }}>
                        {CAT_LABELS[cat] || cat}
                        <span style={{ fontSize: 10, color: 'var(--text-muted)', fontWeight: 400 }}>{tools.length} 个</span>
                      </div>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 2, paddingLeft: 4 }}>
                        {tools.sort((a: any, b: any) => a.name.localeCompare(b.name)).map((t: any) => (
                          <div key={t.name} style={{
                            fontSize: 11, padding: '3px 8px', borderRadius: 4,
                            background: 'var(--bg-secondary)', border: '1px solid var(--border-primary)',
                            color: 'var(--text-primary)',
                          }} title={t.description || ''}>
                            <span style={{ fontWeight: 500, fontFamily: 'monospace' }}>{t.name}</span>
                            {t.description && (
                              <span style={{ color: 'var(--text-muted)', marginLeft: 6, fontSize: 10 }}>
                                — {t.description.slice(0, 120)}
                              </span>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </>
            )}
          </div>
        </div>
      </div>
    );
  }
}
