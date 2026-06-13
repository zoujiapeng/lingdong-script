import React, { useState, useCallback, useEffect, useRef } from 'react';
import { useStore } from '../store';

// ── Native WinPilot event log entries ──
type LogEntry =
  | { type: 'system'; text: string; _id?: number }
  | { type: 'step'; step: number; reasoning: string; content: string; _id?: number }
  | { type: 'tool'; tool: string; args: unknown; preview: string; ms?: number; ok: boolean; terminal?: string; _id?: number }
  | { type: 'plan'; steps: string[]; _id?: number }
  | { type: 'chat'; role: string; text: string; interject?: boolean; _id?: number }
  | { type: 'check'; passed: boolean; reason: string; _id?: number }
  | { type: 'recovery'; reason: string; _id?: number }
  | { type: 'playbook'; name: string; _id?: number }
  | { type: 'done'; status: string; result: string; steps: number; _id?: number }
  | { type: 'fail'; reason: string; _id?: number };

interface Usage {
  prompt_tokens?: number;
  completion_tokens?: number;
  cache_hit_tokens?: number;
  cost_cny?: number;
}

let _logIdCounter = 0;
let _persistedLog: LogEntry[] = [];

const _STORAGE_KEY = 'lingdong-agent-goal';
const KEY_STORAGE = 'lingdong-deepseek-key';
const BASE_STORAGE = 'lingdong-llm-base';
const MODEL_STORAGE = 'lingdong-llm-model';
const VERIFY_STORAGE = 'lingdong-verify-enabled';
const MULTIMODAL_STORAGE = 'lingdong-agent-multimodal';
const MAXSTEPS_STORAGE = 'lingdong-agent-max-steps';

// ── Helpers ──
function lsGet(k: string, d = '') { try { return localStorage.getItem(k) || d; } catch { return d; } }
function lsSet(k: string, v: string) { try { localStorage.setItem(k, v); } catch {} }
function lsBool(k: string, d = false) { try { const v = localStorage.getItem(k); return v !== null ? v === 'true' : d; } catch { return d; } }
function lsNum(k: string, d: number) { try { const v = localStorage.getItem(k); return v !== null ? parseFloat(v) : d; } catch { return d; } }
function maskKey(key: string): string {
  if (!key) return '未配置';
  if (key.length <= 12) return key.slice(0, 4) + '...' + key.slice(-4);
  return key.slice(0, 6) + '...' + key.slice(-4);
}

// Resolve the LLM connection. Secrets come ONLY from runtime config
// (build-time global, localStorage) — never hardcoded in source.
function getRuntimeLLMConfig(preset: typeof PRESETS[number]) {
  const w = window as any;
  return {
    apiKey: (w.__DEEPSEEK_KEY__ || lsGet(KEY_STORAGE) || '').trim(),
    apiBase: (w.__LLM_API_BASE__ || lsGet(BASE_STORAGE) || preset.base).trim(),
    model: (w.__LLM_MODEL__ || lsGet(MODEL_STORAGE) || preset.model).trim(),
  };
}

// ── Presets (endpoint + model defaults only; NO keys in source) ──
const PRESETS = [
  { label: 'DeepSeek', model: 'deepseek-chat', base: 'https://api.deepseek.com/v1', multimodal: false },
  { label: 'MIMO', model: 'mimo-v2-omni', base: 'https://api.xiaomimimo.com/v1', multimodal: true },
];

// ── Inline controls ──
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

function summarizeArgs(args: unknown): string {
  if (args == null) return '';
  try {
    const s = typeof args === 'string' ? args : JSON.stringify(args);
    return s.length > 120 ? s.slice(0, 120) + '…' : s;
  } catch {
    return '';
  }
}

// ====================================================================
//  Main component — WinPilot agent panel
// ====================================================================

export function AgentView() {
  const [goal, setGoal] = useState(() => sessionStorage.getItem(_STORAGE_KEY) || '');
  const [running, setRunning] = useState(false);
  const [offline, setOffline] = useState(false);
  const [log, setLog] = useState<LogEntry[]>(_persistedLog);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [lastStatus, setLastStatus] = useState<string | null>(null);

  const [activePreset, setActivePreset] = useState(PRESETS[0].label);
  const [showKey, setShowKey] = useState(false);
  const [keyInput, setKeyInput] = useState(() => lsGet(KEY_STORAGE));

  // ── WinPilot-relevant config ──
  const [maxSteps, setMaxSteps] = useState(() => lsNum(MAXSTEPS_STORAGE, 40));
  const [verifyEnabled, setVerifyEnabled] = useState(() => lsBool(VERIFY_STORAGE, true));
  const [multimodal, setMultimodal] = useState(() => lsBool(MULTIMODAL_STORAGE, false));

  const [guidance, setGuidance] = useState('');
  const goalRef = useRef(goal);
  const msgEndRef = useRef<HTMLDivElement>(null);
  const preset = PRESETS.find(p => p.label === activePreset) || PRESETS[0];

  const applyPreset = useCallback((label: string) => {
    const p = PRESETS.find(x => x.label === label);
    if (!p) return;
    setActivePreset(label);
    setMultimodal(p.multimodal);
    lsSet(MULTIMODAL_STORAGE, String(p.multimodal));
    lsSet(BASE_STORAGE, p.base);
    lsSet(MODEL_STORAGE, p.model);
  }, []);

  const addEntry = useCallback((entry: LogEntry) => {
    const e = { ...entry, _id: ++_logIdCounter } as LogEntry;
    _persistedLog = [..._persistedLog, e];
    setLog(_persistedLog);
  }, []);

  // ── Run / stop ──
  const runAgent = useCallback(async () => {
    if (!goal.trim() || running) return;
    _persistedLog = []; setLog([]); setUsage(null); setLastStatus(null);

    if (offline) {
      addEntry({ type: 'system', text: '正在离线执行当前流程图...' });
      setRunning(true);
      try {
        await window.electronAPI!.engineStart(JSON.stringify(useStore.getState().script));
      } catch {
        addEntry({ type: 'fail', reason: '离线执行失败' });
        setRunning(false);
      }
      return;
    }

    const { apiKey, apiBase, model } = getRuntimeLLMConfig(preset);
    if (!apiKey) {
      addEntry({ type: 'system', text: '⚠️ 未配置 API 密钥，请先在上方填入 Key' });
      return;
    }
    addEntry({ type: 'system', text: '正在启动 WinPilot...' });
    setRunning(true);
    lsSet(MAXSTEPS_STORAGE, String(maxSteps));
    lsSet(VERIFY_STORAGE, String(verifyEnabled));
    lsSet(MULTIMODAL_STORAGE, String(multimodal));
    try {
      await window.electronAPI!.agentStart(goal.trim(), {
        api_key: apiKey,
        api_base: apiBase,
        model,
        multimodal: multimodal ? 'true' : 'false',
        verify_enabled: verifyEnabled ? 'true' : 'false',
        agent_config: { max_steps: maxSteps, temperature: 0.3 },
      });
    } catch (err: any) {
      addEntry({ type: 'fail', reason: `启动失败: ${err?.message || err}` });
      setRunning(false);
    }
  }, [goal, running, offline, preset, maxSteps, verifyEnabled, multimodal, addEntry]);

  const stopAgent = useCallback(async () => {
    try {
      if (offline) await window.electronAPI!.engineStop();
      else await window.electronAPI!.agentStop();
      addEntry({ type: 'system', text: '已请求停止' });
      setRunning(false);
    } catch { /* ignore */ }
  }, [offline, addEntry]);

  const sendGuidance = useCallback(async () => {
    const text = guidance.trim();
    if (!text || !running) return;
    setGuidance('');
    try { await window.electronAPI!.agentGuidance(text); } catch { /* ignore */ }
  }, [guidance, running]);

  useEffect(() => { msgEndRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [log]);
  useEffect(() => { goalRef.current = goal; }, [goal]);

  // ── IPC listener: consume WinPilot native BUS events verbatim ──
  useEffect(() => {
    const api = window.electronAPI;
    if (!api) return;
    const unsubMsg = api.onAgentMessage?.((msg: any) => {
      switch (msg.type) {
        case 'agent_start':
          setRunning(true); setLastStatus(null);
          addEntry({ type: 'system', text: `正在处理：${msg.task ?? ''}` });
          break;
        case 'agent_plan': {
          const raw = msg.plan;
          const steps: string[] = Array.isArray(raw)
            ? raw.map((s: any) => typeof s === 'string' ? s : (s?.desc || s?.text || JSON.stringify(s)))
            : Array.isArray(raw?.steps)
              ? raw.steps.map((s: any) => typeof s === 'string' ? s : (s?.desc || s?.text || JSON.stringify(s)))
              : [];
          if (steps.length) addEntry({ type: 'plan', steps });
          break;
        }
        case 'agent_step': {
          const content = typeof msg.content === 'string' ? msg.content : '';
          const reasoning = typeof msg.reasoning === 'string' ? msg.reasoning : '';
          // A step with no tool calls and some content is a plain reply.
          if ((msg.n_tool_calls ?? 0) === 0 && content) {
            addEntry({ type: 'chat', role: 'assistant', text: content });
          } else if (reasoning || content) {
            addEntry({ type: 'step', step: msg.step ?? 0, reasoning, content });
          }
          break;
        }
        case 'agent_tool':
          addEntry({
            type: 'tool',
            tool: msg.tool ?? '?',
            args: msg.args,
            preview: typeof msg.result_preview === 'string' ? msg.result_preview : '',
            ms: typeof msg.elapsed_ms === 'number' ? msg.elapsed_ms : undefined,
            ok: !msg.error,
            terminal: msg.terminal || undefined,
          });
          break;
        case 'agent_usage':
          if (msg.total && typeof msg.total === 'object') setUsage(msg.total as Usage);
          break;
        case 'done_check':
          addEntry({ type: 'check', passed: !!msg.passed, reason: msg.reason || '' });
          break;
        case 'agent_recovery':
          addEntry({ type: 'recovery', reason: msg.reason || msg.kind || '重新规划' });
          break;
        case 'playbook':
          addEntry({ type: 'playbook', name: msg.name || '' });
          break;
        case 'chat':
          addEntry({ type: 'chat', role: msg.role || 'assistant', text: msg.content || '', interject: !!msg.interject });
          break;
        case 'agent_done': {
          const status = msg.status || 'error';
          const ok = status === 'done' || status === 'reply';
          if (ok) addEntry({ type: 'done', status, result: msg.result || '', steps: msg.steps ?? 0 });
          else addEntry({ type: 'fail', reason: msg.result || `执行${status === 'stopped' ? '已停止' : '失败'}` });
          setRunning(false);
          setLastStatus(status);
          break;
        }
        case 'agent_error':
          addEntry({ type: 'fail', reason: msg.error || '内部错误' });
          setRunning(false);
          break;
        case 'user_guidance_sent':
          addEntry({ type: 'chat', role: 'user', text: msg.text || '', interject: true });
          break;
        case 'log':
          if (msg.level === 'error') addEntry({ type: 'system', text: `[错误] ${msg.message}` });
          else if (msg.level === 'warn') addEntry({ type: 'system', text: `[警告] ${msg.message}` });
          else addEntry({ type: 'system', text: msg.message });
          break;
        // world / status_report intentionally not surfaced in the log.
      }
    });
    const unsubLog = api.onAgentLog?.((msg: any) => {
      if (msg.level === 'error' && msg.message) addEntry({ type: 'system', text: `[进程] ${msg.message}` });
    });
    return () => { unsubMsg?.(); unsubLog?.(); };
  }, [addEntry]);

  const llm = getRuntimeLLMConfig(preset);

  // ── Render ──
  return (
    <div className="agent-view-panel">
      {/* ── Header: goal + preset + run ── */}
      <div className="agent-view-section" style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <input className="agent-input" value={goal}
            onChange={e => { setGoal(e.target.value); sessionStorage.setItem(_STORAGE_KEY, e.target.value); }}
            placeholder="输入目标，如「打开计算器，计算 7÷99」"
            style={{ flex: 1, padding: '8px 10px', borderRadius: 6, border: '1px solid var(--border-primary)', background: 'var(--bg-primary)', color: 'var(--text-primary)', fontSize: 14 }}
            onKeyDown={e => { if (e.key === 'Enter' && !running) runAgent(); }} />
          <select value={activePreset} onChange={e => applyPreset(e.target.value)}
            style={{ padding: '8px 10px', borderRadius: 6, border: '1px solid var(--border-primary)', background: 'var(--bg-primary)', color: 'var(--text-primary)', fontSize: 13, cursor: 'pointer' }}>
            {PRESETS.map(p => <option key={p.label} value={p.label}>{p.label}</option>)}
          </select>
          <button className="agent-btn agent-btn-primary" onClick={runAgent}
            disabled={running} style={{ whiteSpace: 'nowrap', padding: '8px 16px' }}>
            {offline ? '离线执行' : '运行'}
          </button>
          {running && <button className="agent-btn" onClick={stopAgent} style={{ padding: '8px 12px' }}>停止</button>}
        </div>

        {/* Key + model row */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: '#888' }}>
          <span>{preset.label}</span><span>|</span><span>{llm.model}</span><span>|</span>
          {showKey ? (
            <input value={keyInput} autoFocus
              placeholder="粘贴 API Key"
              onChange={e => setKeyInput(e.target.value)}
              onBlur={() => { lsSet(KEY_STORAGE, keyInput.trim()); setShowKey(false); }}
              onKeyDown={e => { if (e.key === 'Enter') { lsSet(KEY_STORAGE, keyInput.trim()); setShowKey(false); } }}
              style={{ width: 260, padding: '2px 6px', background: 'var(--bg-secondary)', border: '1px solid var(--border-primary)', borderRadius: 3, color: 'var(--text-muted)', fontSize: 11 }} />
          ) : (
            <span style={{ cursor: 'pointer' }} onClick={() => { setKeyInput(lsGet(KEY_STORAGE)); setShowKey(true); }}>
              ●●● {maskKey(llm.apiKey)}（点击编辑）
            </span>
          )}
          <label style={{ marginLeft: 'auto', fontSize: 12, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }}>
            <input type="checkbox" checked={offline} onChange={e => setOffline(e.target.checked)}
              style={{ accentColor: '#4f8cff' }} />
            离线（直接跑流程图）
          </label>
        </div>
      </div>

      {/* ── WinPilot config ── */}
      {!offline && (
        <div className="agent-view-section" style={{ padding: 12, borderBottom: '1px solid var(--border-primary)' }}>
          <Slider label="最大步数" value={maxSteps} min={5} max={80} onChange={setMaxSteps} />
          <Toggle label="完成核验" on={verifyEnabled} onChange={setVerifyEnabled}
            desc="独立核验任务是否真正完成（done-gate）" />
          <Toggle label="多模态视觉 (VLM)" on={multimodal} onChange={setMultimodal}
            desc="启用视觉语言模型感知维度（需在 config 配置 VLM 端点）" />
        </div>
      )}

      {/* ── Usage ── */}
      {usage && (
        <div className="agent-view-section" style={{ padding: '6px 12px', fontSize: 12, color: '#888', display: 'flex', gap: 16, borderBottom: '1px solid var(--border-primary)' }}>
          <span>输入 {usage.prompt_tokens ?? 0}</span>
          <span>输出 {usage.completion_tokens ?? 0}</span>
          {usage.cache_hit_tokens ? <span>缓存 {usage.cache_hit_tokens}</span> : null}
          <span style={{ marginLeft: 'auto', color: '#4f8cff' }}>¥{(usage.cost_cny ?? 0).toFixed(4)}</span>
        </div>
      )}

      {/* ── Log ── */}
      {(log.length > 0 || running) && (
        <div className="agent-view-section" style={{ flex: 1, overflowY: 'auto', minHeight: 0, padding: 8 }}>
          {log.map((entry, i) => {
            const key = entry._id || i;
            if (entry.type === 'step') {
              return (
                <div key={key} style={{ padding: '6px 8px', fontSize: 12, borderBottom: '1px solid var(--border-primary)' }}>
                  <span style={{ color: '#4f8cff' }}>#{entry.step}</span>
                  {entry.reasoning && <span style={{ color: '#bbb', marginLeft: 6 }}>{entry.reasoning}</span>}
                  {entry.content && <div style={{ color: '#999', marginTop: 2 }}>{entry.content}</div>}
                </div>
              );
            }
            if (entry.type === 'tool') {
              return (
                <div key={key} style={{ padding: '5px 8px', fontSize: 12 }}>
                  <span style={{ color: '#e8c84a' }}>🔧 {entry.tool}</span>
                  {entry.args != null && <span style={{ color: '#888' }}> {summarizeArgs(entry.args)}</span>}
                  <span style={{ color: entry.ok ? '#4caf50' : '#f44336', marginLeft: 6 }}>{entry.ok ? '✓' : '✗'}</span>
                  {entry.terminal && <span style={{ color: '#4f8cff', marginLeft: 6 }}>[{entry.terminal}]</span>}
                  {entry.ms != null && <span style={{ color: '#888', marginLeft: 6 }}>{entry.ms}ms</span>}
                  {entry.preview && <div style={{ color: '#888', marginTop: 2 }}>{entry.preview}</div>}
                </div>
              );
            }
            if (entry.type === 'plan') {
              return (
                <div key={key} style={{ padding: '6px 8px', fontSize: 12, background: 'var(--bg-secondary)', borderRadius: 4, margin: '4px 0' }}>
                  <div style={{ color: '#4f8cff', marginBottom: 2 }}>📋 计划（{entry.steps.length} 步）</div>
                  {entry.steps.map((s, j) => <div key={j} style={{ color: '#aaa' }}>{j + 1}. {s}</div>)}
                </div>
              );
            }
            if (entry.type === 'chat') {
              return (
                <div key={key} style={{ padding: '4px 8px', fontSize: 12, color: entry.role === 'user' ? '#8cf' : '#cde' }}>
                  {entry.role === 'user' ? '💬 ' : '🤖 '}{entry.interject && entry.role === 'user' ? '（提示）' : ''}{entry.text}
                </div>
              );
            }
            if (entry.type === 'check') {
              return <div key={key} style={{ padding: '4px 8px', fontSize: 12, color: entry.passed ? '#4caf50' : '#e8a' }}>
                {entry.passed ? '✔ 完成核验通过' : '✘ 核验未通过'}{entry.reason ? `：${entry.reason}` : ''}
              </div>;
            }
            if (entry.type === 'recovery') {
              return <div key={key} style={{ padding: '4px 8px', fontSize: 12, color: '#e8a' }}>↻ 重新规划：{entry.reason}</div>;
            }
            if (entry.type === 'playbook') {
              return <div key={key} style={{ padding: '4px 8px', fontSize: 12, color: '#9c7' }}>📖 应用方法库：{entry.name}</div>;
            }
            if (entry.type === 'done') {
              return <div key={key} style={{ padding: 8, fontSize: 13, color: '#4caf50' }}>
                ✅ {entry.status === 'reply' ? '已回复' : `完成（${entry.steps} 步）`}{entry.result ? `：${entry.result}` : ''}
              </div>;
            }
            if (entry.type === 'fail') {
              return <div key={key} style={{ padding: 8, fontSize: 13, color: '#f44336' }}>❌ {entry.reason}</div>;
            }
            // system
            return <div key={key} style={{ padding: '3px 8px', fontSize: 12, color: '#aaa' }}>{entry.text}</div>;
          })}
          <div ref={msgEndRef} />
        </div>
      )}

      {/* ── Mid-run hint input ── */}
      {running && !offline && (
        <div className="agent-view-section" style={{ padding: 8, display: 'flex', gap: 8, borderTop: '1px solid var(--border-primary)' }}>
          <input value={guidance} onChange={e => setGuidance(e.target.value)}
            placeholder="给 Agent 一个提示（执行中可发送）..."
            onKeyDown={e => { if (e.key === 'Enter') sendGuidance(); }}
            style={{ flex: 1, padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border-primary)', background: 'var(--bg-primary)', color: 'var(--text-primary)', fontSize: 13 }} />
          <button className="agent-btn" onClick={sendGuidance} style={{ padding: '6px 12px' }}>发送</button>
        </div>
      )}

      {/* ── Summary ── */}
      {lastStatus && !running && (
        <div className="agent-view-section" style={{ padding: 12, textAlign: 'center', fontSize: 13, color: '#888' }}>
          {lastStatus === 'done' ? '✅ 已完成'
            : lastStatus === 'reply' ? '💬 已回复'
            : lastStatus === 'stopped' ? '⏹ 已停止'
            : '❌ 执行失败'}
        </div>
      )}
    </div>
  );
}
