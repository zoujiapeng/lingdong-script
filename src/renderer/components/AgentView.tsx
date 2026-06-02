import React, { useState, useCallback, useEffect, useRef } from 'react';
import { useStore } from '../store';
import {
  List, Wifi, WifiOff, Clock, RefreshCw,
  Eye, EyeOff, GitBranch, Zap,
  MessageSquare, Monitor, Brain,
  Shield, Settings, Sliders, ToggleLeft,
  ChevronDown, ChevronRight,
} from 'lucide-react';
import { PlanVisualizer } from './common/PlanVisualizer';
import type { AgentRunRecord } from '../types';

// ── Types ──
interface StepData {
  step_num: number; tool: string; thought: string;
  step_label?: string; step_type?: string; error?: string;
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
const KEY_STORAGE = 'lingdong-deepseek-key';
const BASE_STORAGE = 'lingdong-llm-base';
const MODEL_STORAGE = 'lingdong-llm-model';

// ── Helpers ──
function lsGet(k: string, d = '') { try { return localStorage.getItem(k) || d; } catch { return d; } }
function lsSet(k: string, v: string) { try { localStorage.setItem(k, v); } catch {} }
function lsBool(k: string, d = false) { try { const v = localStorage.getItem(k); return v !== null ? v === 'true' : d; } catch { return d; } }
function lsNum(k: string, d: number) { try { const v = localStorage.getItem(k); return v !== null ? parseFloat(v) : d; } catch { return d; } }
function maskKey(key: string): string {
  if (key.length <= 12) return key.slice(0, 4) + '...' + key.slice(-4);
  return key.slice(0, 6) + '...' + key.slice(-4);
}

// ── Presets ──
const PRESETS = [
  { label: '🤖 DeepSeek', model: 'deepseek-chat', base: 'https://api.deepseek.com/v1',
    key: 'REDACTED_DEEPSEEK_KEY', mode: 'text' as const },
  { label: '🖼️ MIMO', model: 'mimo-v2-omni', base: 'https://api.xiaomimimo.com/v1',
    key: 'REDACTED_SILICONFLOW_KEY_1', mode: 'multimodal' as const },
];

// ── Inline toggle ──
function Toggle({ label, on, onChange, desc }: { label: string; on: boolean; onChange: (v: boolean) => void; desc?: string }) {
  return (
    <label style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0', cursor: 'pointer' }}>
      <input type="checkbox" checked={on} onChange={e => onChange(e.target.checked)}
        style={{ accentColor: '#4f8cff', width: 16, height: 16 }} />
      <div>
        <div style={{ fontSize: 13, fontWeight: 500 }}>{label}</div>
        {desc && <div style={{ fontSize: 11, color: '#888' }}>{desc}</div>}
      </div>
    </label>
  );
}

function Slider({ label, value, min, max, step, onChange }: {
  label: string; value: number; min: number; max: number; step?: number;
  onChange: (v: number) => void;
}) {
  return (
    <div style={{ padding: '6px 0' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, marginBottom: 4 }}>
        <span>{label}</span><span style={{ color: '#4f8cff' }}>{value}</span>
      </div>
      <input type="range" min={min} max={max} step={step || 1} value={value}
        onChange={e => onChange(Number(e.target.value))}
        style={{ width: '100%', accentColor: '#4f8cff' }} />
    </div>
  );
}

function Select({ label, value, options, onChange }: {
  label: string; value: string; options: { value: string; label: string }[];
  onChange: (v: string) => void;
}) {
  return (
    <div style={{ padding: '6px 0' }}>
      <div style={{ fontSize: 13, marginBottom: 4 }}>{label}</div>
      <select value={value} onChange={e => onChange(e.target.value)}
        style={{ width: '100%', padding: '6px 8px', background: '#333', color: '#ccc', border: '1px solid #333', borderRadius: 4, fontSize: 13 }}>
        {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </div>
  );
}

// ====================================================================
//  Main component
// ====================================================================

export function AgentView() {
  const [goal, setGoal] = useState(() => sessionStorage.getItem(_STORAGE_KEY) || '');
  const [running, setRunning] = useState(false);
  const [offline, setOffline] = useState(false);
  const [offlineRunning, setOfflineRunning] = useState(false);
  const [log, setLog] = useState<LogEntry[]>(_persistedLog);
  const [lastSummary, setLastSummary] = useState<{ status: string; steps: number; goal: string } | null>(null);
  const [generatedScript, setGeneratedScript] = useState<{ pseudocode: string; explanation: string } | null>(null);
  const [lastLearning, setLastLearning] = useState<{ insights: string[]; mistakes: string[]; rulesCount: number; prefsCount: number } | null>(null);
  const [memoryStats, setMemoryStats] = useState<{ preferences: number; rules: number; episodes: number } | null>(null);

  // ── Preset & mode ──
  const [activePreset, setActivePreset] = useState(PRESETS[0].label);
  const [executionMode, setExecutionMode] = useState<'flow_chart' | 'multimodal' | 'text' | 'offline'>('text');
  const [showKey, setShowKey] = useState(false);
  const goalRef = useRef(goal);
  const msgEndRef = useRef<HTMLDivElement>(null);

  // ── Mode config states ──
  // Flow chart
  const [supervise, setSupervise] = useState(true);
  // Multimodal
  const [mmScreenshotQuality, setMmScreenshotQuality] = useState(() => lsNum('lingdong-agent-screenshot-quality', 60));
  // Text vision
  const [tvUia, setTvUia] = useState(false);
  const [tvOcr, setTvOcr] = useState(true);
  const [tvYolo, setTvYolo] = useState(false);
  const [tvSom, setTvSom] = useState(true);
  const [tvWindowOcr, setTvWindowOcr] = useState(true);
  const [tvLocalVision, setTvLocalVision] = useState(false);
  const [tvVisualStrategy, setTvVisualStrategy] = useState('som');
  // Global
  const [memoryOn, setMemoryOn] = useState(false);
  const [maxSteps, setMaxSteps] = useState(() => lsNum('lingdong-agent-max-steps', 20));

  // ── Chat & guidance ──
  const [guidance, setGuidance] = useState('');
  const [chatMessages, setChatMessages] = useState<{ role: 'user' | 'agent'; text: string }[]>([]);
  const preset = PRESETS.find(p => p.label === activePreset) || PRESETS[0];

  // ── Apply preset when selected ──
  const applyPreset = useCallback((label: string) => {
    const p = PRESETS.find(x => x.label === label);
    if (!p) return;
    setActivePreset(label);
    setExecutionMode(p.mode);
    // Save to localStorage so Python agent can find it
    lsSet(KEY_STORAGE, p.key);
    lsSet(BASE_STORAGE, p.base);
    lsSet(MODEL_STORAGE, p.model);
  }, []);

  // ── buildAgentConfig ──
  const buildAgentConfig = useCallback((): Record<string, any> => {
    const cfg: Record<string, any> = {
      execution_mode: executionMode,
      memory_enabled: memoryOn,
      max_steps: maxSteps,
      temperature: 0.1,
      max_tokens: 4096,
      screenshot_quality: 60,
      on_error: 'retry',
    };
    if (executionMode === 'flow_chart') {
      cfg.supervise = supervise;
    } else if (executionMode === 'multimodal') {
      cfg.screenshot_quality = mmScreenshotQuality;
    } else if (executionMode === 'text') {
      cfg.uia_enabled = tvUia;
      cfg.ocr_enabled = tvOcr;
      cfg.yolo_enabled = tvYolo;
      cfg.som_enabled = tvSom;
      cfg.window_aware_ocr = tvWindowOcr;
      cfg.local_vision = tvLocalVision;
      cfg.visual_strategy = tvVisualStrategy;
      cfg.llm_protocol = 'text';
      cfg.planning = 'per_step';
    }
    return cfg;
  }, [executionMode, memoryOn, maxSteps, supervise, mmScreenshotQuality,
      tvUia, tvOcr, tvYolo, tvSom, tvWindowOcr, tvLocalVision, tvVisualStrategy]);

  // ── Run agent ──
  const runAgent = useCallback(async (withSupervise: boolean) => {
    if (!goal.trim()) return;
    _hasActiveRun = false;
    if (withSupervise) { _persistedLog = []; setLog([]); }
    addEntry({ type: 'system', text: withSupervise ? '正在启动执行...' : '正在生成流程图...' });

    const apiKey_ = preset.key.trim();
    const apiBase_ = preset.base.trim();
    const model_ = preset.model.trim();

    if (!apiKey_) {
      addEntry({ type: 'system', text: '⚠️ 未配置 API 密钥' });
      setRunning(false); return;
    }
    try {
      const stepsArg = withSupervise ? _cachedSteps : undefined;
      await window.electronAPI!.agentStart(goal.trim(), {
        api_key: apiKey_, api_base: apiBase_, model: model_,
        multimodal: preset.mode === 'multimodal' ? 'true' : 'false',
        verify_enabled: 'true',
        auto_learn: 'false',
        agent_config: buildAgentConfig(),
      }, withSupervise, stepsArg ?? undefined);
    } catch (err: any) {
      addEntry({ type: 'fail', reason: `启动失败: ${err.message}` });
    }
  }, [goal, preset, buildAgentConfig]);

  const stopAgent = useCallback(async () => {
    try {
      if (offline) { await window.electronAPI!.engineStop(); setOfflineRunning(false); }
      else { await window.electronAPI!.agentStop(); }
      addEntry({ type: 'system', text: '已停止' });
      setRunning(false); _hasActiveRun = false;
    } catch { }
  }, [offline]);

  const loadScriptToCanvas = useCallback((script: any) => {
    try { fetch('http://127.0.0.1:7880/api/load-script', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ script: typeof script === 'string' ? { pseudocode: script } : script }),
    }); } catch {}
  }, []);

  const runOffline = useCallback(async () => {
    if (!goal.trim()) return;
    _persistedLog = []; setLog([]); setGeneratedScript(null);
    addEntry({ type: 'system', text: '正在离线执行流程图...' });
    setOfflineRunning(true); setRunning(true);
    try { await window.electronAPI!.engineStart(JSON.stringify(useStore.getState().script)); }
    catch { addEntry({ type: 'fail', reason: '离线执行失败' }); setRunning(false); setOfflineRunning(false); _hasActiveRun = false; }
  }, [goal]);

  const addEntry = useCallback((entry: LogEntry) => {
    const e = { ...entry, _id: ++_logIdCounter } as any;
    _persistedLog = [..._persistedLog, e];
    setLog(_persistedLog);
  }, []);

  useEffect(() => { msgEndRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [log]);
  useEffect(() => { goalRef.current = goal; }, [goal]);

  // ── IPC listeners ──
  useEffect(() => {
    const api = window.electronAPI;
    if (!api) return;
    const unsubMsg = api.onAgentMessage?.((msg: any) => {
      switch (msg.type) {
        case 'agent_start':
          _hasActiveRun = true; _cachedSteps = null;
          addEntry({ type: 'system', text: `正在处理：${msg.goal}` });
          setRunning(true); setLastSummary(null);
          break;
        case 'plan_state':
          if (msg.plan?.nodes?.length > 0 && !msg.final)
            addEntry({ type: 'system', text: `计划已生成：${msg.plan.nodes.length} 个步骤` });
          break;
        case 'step_complete':
          addEntry({ type: 'step', data: msg });
          if (msg.tool === 'generate_flow_script' && msg.result?.success) {
            const data = msg.result.data || {};
            const pseudo = data.pseudocode || data.result?.pseudocode;
            if (pseudo) {
              addEntry({ type: 'script', pseudocode: pseudo, explanation: data.explanation || '' });
              setGeneratedScript({ pseudocode: pseudo, explanation: data.explanation || '' });
              const steps = data.structured_steps || data.result?.structured_steps || [];
              if (Array.isArray(steps) && steps.length > 0) _cachedSteps = steps;
              loadScriptToCanvas(pseudo);
            }
          }
          break;
        case 'agent_done':
          if (!_hasActiveRun) return;
          if (msg.status === 'completed' && msg.total_steps > 0)
            addEntry({ type: 'complete', steps: msg.total_steps });
          else
            addEntry({ type: 'fail', reason: msg.fail_reason || '执行失败' });
          setRunning(false); _hasActiveRun = false;
          setLastSummary({ status: msg.status, steps: msg.total_steps || 0, goal: goalRef.current.slice(0, 80) });
          break;
        case 'log':
          if (msg.level === 'error') addEntry({ type: 'system', text: `[错误] ${msg.message}` });
          else if (msg.level === 'warn') addEntry({ type: 'system', text: `[警告] ${msg.message}` });
          else addEntry({ type: 'system', text: msg.message });
          break;
        case 'error':
          addEntry({ type: 'system', text: `[错误] ${msg.message}` });
          break;
        case 'agent_learned':
          setLastLearning({ insights: msg.insights || [], mistakes: msg.mistakes || [],
            rulesCount: msg.rules_count || 0, prefsCount: msg.prefs_count || 0 });
          break;
        case 'memory_snapshot':
          setMemoryStats({ preferences: msg.preferences || 0, rules: msg.rules || 0, episodes: msg.episodes || 0 });
          break;
      }
    });
    const unsubLog = api.onAgentLog?.((msg: any) => {
      if (msg.level === 'error') addEntry({ type: 'system', text: `[进程] ${msg.message}` });
    });
    return () => { unsubMsg?.(); unsubLog?.(); };
  }, [addEntry]);

  // ── Tabs ──
  const TABS = [
    { id: 'flow_chart' as const, label: '📋 流程图', desc: '生成流程图，用户决定执行' },
    { id: 'multimodal' as const, label: '🖼️ 多模态', desc: 'MIMO 直接看截图操作' },
    { id: 'text' as const, label: '🤖 非多模态', desc: 'DeepSeek + SoM+OCR 纯视觉' },
    { id: 'offline' as const, label: '💻 离线', desc: '直接执行当前流程图' },
  ];

  const renderModePanel = () => {
    if (executionMode === 'flow_chart') {
      return (
        <div>
          <Toggle label="监督执行" on={supervise} onChange={setSupervise}
            desc="执行时实时监督并验证每一步" />
        </div>
      );
    }
    if (executionMode === 'multimodal') {
      return (
        <div>
          <Slider label="截图质量" value={mmScreenshotQuality} min={10} max={95}
            onChange={setMmScreenshotQuality} />
          <div style={{ fontSize: 11, color: '#888', marginTop: 4 }}>多模态模型直接从截图看到界面，无需其他感知参数。</div>
        </div>
      );
    }
    if (executionMode === 'text') {
      return (
        <div>
          <div style={{ fontSize: 12, color: '#888', marginBottom: 8 }}>感知参数 — 自定义纯文本模型如何"看"屏幕</div>
          <Toggle label="UIA 控件树" on={tvUia} onChange={setTvUia}
            desc="Windows 无障碍接口，精确定位控件（默认关）" />
          <Toggle label="OCR 文字检测" on={tvOcr} onChange={setTvOcr}
            desc="识别屏幕上的文字（默认开）" />
          <Toggle label="YOLO 图标检测" on={tvYolo} onChange={setTvYolo}
            desc="检测图标/按钮位置（默认关）" />
          <Toggle label="SoM 标号" on={tvSom} onChange={setTvSom}
            desc="给屏幕元素编号，LLM 用 #N 点击（默认开）" />
          <Toggle label="窗口限定" on={tvWindowOcr} onChange={setTvWindowOcr}
            desc="只扫描活动窗口内的元素（默认开）" />
          <Toggle label="本地视觉" on={tvLocalVision} onChange={setTvLocalVision}
            desc="YOLO ONNX 本地模型（默认关）" />
          <Select label="视觉策略" value={tvVisualStrategy}
            options={[
              { value: 'som', label: 'SoM 编号 — OCR 文字→#N 点击' },
              { value: 'ocr_llm', label: 'OCR→LLM→坐标 — 文字列表让 LLM 选' },
              { value: 'blind_guess', label: '盲猜坐标 — LLM 直接猜像素位置' },
            ]}
            onChange={setTvVisualStrategy} />
        </div>
      );
    }
    // offline
    return (
      <div style={{ padding: 20, textAlign: 'center', color: '#666', fontSize: 14 }}>
        ⏳ 离线模式 — 待开发<br />
        <span style={{ fontSize: 12 }}>将支持不依赖 LLM 的本地执行</span>
      </div>
    );
  };

  // ── Render ──
  return (
    <div className="agent-view-panel">
      {/* ── Header: goal + preset ── */}
      <div className="agent-view-section" style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <input className="agent-input" value={goal} onChange={e => { setGoal(e.target.value); sessionStorage.setItem(_STORAGE_KEY, e.target.value); }}
            placeholder="输入目标，如「打开计算器，计算 7÷99」"
            style={{ flex: 1, padding: '8px 10px', borderRadius: 6, border: '1px solid #333', background: '#202020', color: '#ccc', fontSize: 14 }}
            onKeyDown={e => { if (e.key === 'Enter' && !running) { if (offline) runOffline(); else runAgent(executionMode !== 'flow_chart'); } }} />
          <select value={activePreset} onChange={e => applyPreset(e.target.value)}
            style={{ padding: '8px 10px', borderRadius: 6, border: '1px solid #333', background: '#202020', color: '#ccc', fontSize: 13, cursor: 'pointer' }}>
            {PRESETS.map(p => <option key={p.label} value={p.label}>{p.label}</option>)}
          </select>
          <button className="agent-btn agent-btn-primary" onClick={() => { if (offline) runOffline(); else runAgent(executionMode !== 'flow_chart'); }}
            disabled={running} style={{ whiteSpace: 'nowrap', padding: '8px 16px' }}>
            {offline ? '离线执行' : '运行'}
          </button>
          {running && <button className="agent-btn" onClick={stopAgent} style={{ padding: '8px 12px' }}>停止</button>}
        </div>

        {/* Key display (collapsible) */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: '#888' }}>
          <span>{preset.label}</span>
          <span>|</span>
          <span>{preset.model}</span>
          <span>|</span>
          {showKey ? (
            <input value={preset.key} onChange={e => {}}
              style={{ width: 240, padding: '2px 6px', background: '#333', border: '1px solid #333', borderRadius: 3, color: '#888', fontSize: 11 }}
              onBlur={() => setShowKey(false)} />
          ) : (
            <span style={{ cursor: 'pointer' }} onClick={() => setShowKey(true)}>
              {maskKey(preset.key)} ✏️
            </span>
          )}
          <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
            <ToggleLeft size={14} />
            <label style={{ fontSize: 12, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }}>
              <input type="checkbox" checked={offline} onChange={e => setOffline(e.target.checked)}
                style={{ accentColor: '#4f8cff' }} />
              离线
            </label>
            <label style={{ fontSize: 12, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }}>
              <input type="checkbox" checked={memoryOn} onChange={e => setMemoryOn(e.target.checked)}
                style={{ accentColor: '#4f8cff' }} />
              记忆
            </label>
          </span>
        </div>
      </div>

      {/* ── Mode Tabs ── */}
      <div style={{ display: 'flex', gap: 0, borderBottom: '1px solid #222' }}>
        {TABS.map(tab => (
          <button key={tab.id} onClick={() => setExecutionMode(tab.id)}
            style={{
              flex: 1, padding: '10px 8px', cursor: 'pointer', border: 'none', borderBottom: executionMode === tab.id ? '2px solid #4f8cff' : '2px solid transparent',
              background: executionMode === tab.id ? '#2d2d2d' : 'transparent', color: executionMode === tab.id ? '#fff' : '#666',
              fontSize: 13, fontWeight: executionMode === tab.id ? 600 : 400, transition: 'all 0.15s',
            }}>
            <div>{tab.label}</div>
            <div style={{ fontSize: 10, color: '#888', marginTop: 2 }}>{tab.desc}</div>
          </button>
        ))}
      </div>

      {/* ── Mode config panel ── */}
      <div className="agent-view-section" style={{ padding: 12, borderBottom: '1px solid #444' }}>
        {renderModePanel()}
      </div>

      {/* ── Plan visualizer ── */}
      {generatedScript && !running && !lastSummary && (
        <div className="agent-view-section" style={{ padding: 12 }}>
          <PlanVisualizer />
          <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
            <button className="agent-btn agent-btn-primary" onClick={() => runAgent(true)}>▶ 执行计划</button>
            <button className="agent-btn" onClick={() => runAgent(false)}>🔄 重新生成</button>
          </div>
        </div>
      )}

      {/* ── Log / Execution ── */}
      {(log.length > 0 || running) && (
        <div className="agent-view-section" style={{ flex: 1, overflow: 'auto', padding: 8 }}>
          {log.map((entry, i) => {
            if (entry.type === 'step' && 'data' in entry) {
              const d = (entry as any).data;
              return (
                <div key={entry._id || i} style={{ padding: '6px 8px', fontSize: 12, borderBottom: '1px solid #444' }}>
                  <span style={{ color: '#4f8cff' }}>#{d.step_num}</span>
                  {' '}<span style={{ color: '#e8c84a' }}>{d.tool}</span>
                  {d.args && <span style={{ color: '#888' }}> {JSON.stringify(d.args).slice(0, 100)}</span>}
                  {d.status === 'completed' && <span style={{ color: '#4caf50', marginLeft: 6 }}>✓</span>}
                  {d.status === 'failed' && <span style={{ color: '#f44336', marginLeft: 6 }}>✗</span>}
                  {d.result?.step_ms && <span style={{ color: '#888', marginLeft: 6 }}>{d.result.step_ms}ms</span>}
                </div>
              );
            }
            if (entry.type === 'complete') {
              return <div key={entry._id || i} style={{ padding: '8px', fontSize: 13, color: '#4caf50', textAlign: 'center' }}>✅ 执行完成，共 {(entry as any).steps} 步</div>;
            }
            if (entry.type === 'fail') {
              return <div key={entry._id || i} style={{ padding: '8px', fontSize: 13, color: '#f44336', textAlign: 'center' }}>❌ {(entry as any).reason || '执行失败'}</div>;
            }
            const text = 'text' in entry ? (entry as any).text : '';
            const prefix = entry.type === 'system' ? '' : entry.type === 'user' ? '💬 ' : entry.type === 'guidance' ? '📌 ' : '';
            const color = entry.type === 'system' ? '#aaa' : entry.type === 'user' ? '#8cf' : '#ccc';
            return (
              <div key={entry._id || i} style={{ padding: '3px 8px', fontSize: 12, color }}>
                {prefix}{text}
              </div>
            );
          })}
          <div ref={msgEndRef} />
        </div>
      )}

      {/* ── Summary ── */}
      {lastSummary && !running && (
        <div className="agent-view-section" style={{ padding: 12, textAlign: 'center', fontSize: 13, color: '#888' }}>
          {lastSummary.status === 'completed'
            ? `✅ 已完成 (${lastSummary.steps} 步)`
            : `❌ 执行失败: ${lastSummary.goal}`}
        </div>
      )}
    </div>
  );
}
