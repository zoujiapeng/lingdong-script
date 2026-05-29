import React, { useState, useCallback, useEffect, useRef } from 'react';
import { useStore } from '../store';
import {
  List, Wifi, WifiOff, Mic, MicOff, Clock,
  ChevronDown, ChevronRight, Settings, Eye,
  GitBranch, FileText, AlertTriangle, Zap,
  Camera, MessageSquare, Monitor, Brain, Volume2, Terminal,
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

// Agent config storage keys (moved from SettingsPanel)
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

function getStoredAgentProtocol(): string {
  try { return localStorage.getItem(AGENT_PROTOCOL_KEY) || 'auto'; } catch { return 'auto'; }
}
function getStoredAgentVision(): string {
  try { return localStorage.getItem(AGENT_VISION_KEY) || 'auto'; } catch { return 'auto'; }
}
function getStoredAgentPlanning(): string {
  try { return localStorage.getItem(AGENT_PLANNING_KEY) || 'auto'; } catch { return 'auto'; }
}
function getStoredAgentContext(): string {
  try { return localStorage.getItem(AGENT_CONTEXT_KEY) || 'auto'; } catch { return 'auto'; }
}
function getStoredAgentOnError(): string {
  try { return localStorage.getItem(AGENT_ONERROR_KEY) || 'retry'; } catch { return 'retry'; }
}
function getStoredAgentFastMode(): boolean {
  try { return localStorage.getItem(AGENT_FAST_MODE_KEY) === 'true'; } catch { return false; }
}
function getStoredAgentDedup(): boolean {
  try { return localStorage.getItem(AGENT_DEDUP_KEY) !== 'false'; } catch { return true; }
}
function getStoredAgentWindowAwareOcr(): boolean {
  try { return localStorage.getItem(AGENT_WINDOW_AWARE_OCR_KEY) !== 'false'; } catch { return true; }
}
function getStoredAgentUiaPrefocus(): boolean {
  try { return localStorage.getItem(AGENT_UIA_PREFOCUS_KEY) !== 'false'; } catch { return true; }
}
function getStoredAgentScriptedMode(): boolean {
  try { return localStorage.getItem(AGENT_SCRIPTED_MODE_KEY) === 'true'; } catch { return false; }
}

export function AgentView() {
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
  const [agentTools, setAgentTools] = useState<string[]>([]);
  const [toolsLoading, setToolsLoading] = useState(false);

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

  // ── Agent config (moved from SettingsPanel) ──
  const [configOpen, setConfigOpen] = useState(false);
  const [agentProtocol, setAgentProtocol] = useState(() => getStoredAgentProtocol());
  const [agentVision, setAgentVision] = useState(() => getStoredAgentVision());
  const [agentPlanning, setAgentPlanning] = useState(() => getStoredAgentPlanning());
  const [agentContext, setAgentContext] = useState(() => getStoredAgentContext());
  const [agentOnError, setAgentOnError] = useState(() => getStoredAgentOnError());
  const [agentFastMode, setAgentFastMode] = useState(() => getStoredAgentFastMode());
  const [agentDedup, setAgentDedup] = useState(() => getStoredAgentDedup());
  const [agentWindowAwareOcr, setAgentWindowAwareOcr] = useState(() => getStoredAgentWindowAwareOcr());
  const [agentUiaPrefocus, setAgentUiaPrefocus] = useState(() => getStoredAgentUiaPrefocus());
  const [agentScriptedMode, setAgentScriptedMode] = useState(() => getStoredAgentScriptedMode());

  // ── Tool explorer ──
  const loadAgentTools = async () => {
    setToolsLoading(true);
    try {
      const api = (window as any).electronAPI;
      const r = await api?.callFeature?.('list_features', { category: 'agent' }) || await api?.callFeature?.('list_features', {});
      const list = Array.isArray(r) ? r : r?.result || [];
      setAgentTools(list.map((f: any) => f.name || f));
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
                if (Array.isArray(steps) && steps.length > 0) {
                  _cachedSteps = steps;
                }
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
          if (msg.status === 'completed') {
            addEntry({ type: 'complete', steps: 0 });
          } else {
            addEntry({ type: 'fail', reason: msg.error || '执行失败' });
          }
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

  // ── Run agent (generate or execute) ──
  const runAgent = useCallback(async (supervise: boolean) => {
    if (!goal.trim()) return;
    _hasActiveRun = false;
    if (supervise) {
      _persistedLog = [];
      setLog([]);
    }
    addEntry({ type: 'system', text: supervise ? '正在启动执行...' : '正在生成流程图...' });

    const rawKey = (window as any).__DEEPSEEK_KEY__ || localStorage.getItem('lingdong-deepseek-key') || '';
    const rawBase = ((window as any).__LLM_API_BASE__ || localStorage.getItem('lingdong-llm-base') || '').replace(/\/+$/, '');
    const rawModel = (window as any).__LLM_MODEL__ || localStorage.getItem('lingdong-llm-model') || '';
    const multimodal = (window as any).__LLM_MULTIMODAL__ !== undefined
      ? (window as any).__LLM_MULTIMODAL__ : localStorage.getItem('lingdong-llm-multimodal') === 'true';
    const verifyEnabled = localStorage.getItem('lingdong-verify-enabled') === 'true';
    const autoLearn = localStorage.getItem('lingdong-auto-learn') === 'true';
    const omniparserEnabled = localStorage.getItem('lingdong-omniparser') === 'true';
    const apiKey = rawKey.trim();
    const apiBase = rawBase.trim();
    const model = rawModel.trim();

    if (!apiKey) {
      addEntry({ type: 'system', text: '⚠️ 请先在设置页面配置 API 密钥后重试' });
      setRunning(false);
      return;
    }

    try {
      const stepsArg = supervise ? _cachedSteps : undefined;
      const agentConfig = {
        llm_protocol: localStorage.getItem('lingdong-agent-protocol') ?? 'auto',
        vision_mode: localStorage.getItem('lingdong-agent-vision') ?? 'auto',
        planning: localStorage.getItem('lingdong-agent-planning') ?? 'auto',
        screen_context: localStorage.getItem('lingdong-agent-context') ?? 'auto',
        on_error: localStorage.getItem('lingdong-agent-on-error') ?? 'retry',
        fast_mode: localStorage.getItem('lingdong-agent-fast-mode') === 'true',
        image_dedup: localStorage.getItem('lingdong-agent-image-dedup') !== 'false',
        window_aware_ocr: localStorage.getItem('lingdong-agent-window-aware-ocr') !== 'false',
        uia_pre_focus: localStorage.getItem('lingdong-agent-uia-prefocus') !== 'false',
        scripted_mode: localStorage.getItem('lingdong-agent-scripted-mode') === 'true',
      };
      await window.electronAPI!.agentStart(goal.trim(), {
        api_key: apiKey,
        api_base: apiBase,
        model,
        multimodal: multimodal ? 'true' : 'false',
        verify_enabled: verifyEnabled ? 'true' : 'false',
        auto_learn: autoLearn ? 'true' : 'false',
        omniparser_enabled: omniparserEnabled ? 'true' : 'false',
        agent_config: agentConfig,
      }, supervise, stepsArg ?? undefined);
    } catch (err: any) {
      addEntry({ type: 'fail', reason: `启动失败: ${err.message}` });
    }
  }, [goal, addEntry]);

  const stopAgent = useCallback(async () => {
    try {
      if (offline) {
        await window.electronAPI!.engineStop();
        setOfflineRunning(false);
      } else {
        await window.electronAPI!.agentStop();
      }
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

  const runOffline = useCallback(async () => {
    if (!goal.trim()) return;
    _hasActiveRun = false;
    _persistedLog = [];
    setLog([]);
    setGeneratedScript(null);
    addEntry({ type: 'system', text: '正在离线执行流程图...' });
    setOfflineRunning(true);
    setRunning(true);
    try {
      const script = useStore.getState().script;
      window.electronAPI!.engineStart(JSON.stringify(script));
    } catch (err: any) {
      addEntry({ type: 'fail', reason: `离线执行失败: ${err.message}` });
      setRunning(false);
      setOfflineRunning(false);
      _hasActiveRun = false;
    }
  }, [goal, addEntry]);

  const startVoiceInput = useCallback(() => {
    if (listening) return;
    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SpeechRecognition) {
      addEntry({ type: 'system', text: '⚠️ 浏览器不支持语音识别' });
      return;
    }
    const recognition = new SpeechRecognition();
    recognition.lang = 'zh-CN';
    recognition.continuous = false;
    recognition.interimResults = true;

    recognition.onstart = () => setListening(true);
    recognition.onerror = (event: any) => {
      setListening(false);
      addEntry({ type: 'system', text: `⚠️ 语音识别失败: ${event.error === 'not-allowed' ? '请允许麦克风权限' : event.error}` });
    };
    recognition.onend = () => setListening(false);
    recognition.onresult = (event: any) => {
      let finalText = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        if (event.results[i].isFinal) {
          finalText += event.results[i][0].transcript;
        }
      }
      if (finalText) {
        const prev = goalRef.current;
        handleSetGoal(prev ? prev + '，' + finalText : finalText);
      }
    };
    try { recognition.start(); } catch { setListening(false); }
  }, [listening, handleSetGoal, addEntry]);

  const loadScriptToCanvas = useCallback(async (pseudocode: string) => {
    try {
      const { parsePseudoToFlow } = await import('../utils/pseudoParser');
      const script = parsePseudoToFlow(pseudocode);
      script.meta = {
        ...script.meta,
        id: useStore.getState().script.meta.id,
        name: useStore.getState().script.meta.name + ' (Agent)',
      };
      useStore.getState().loadScript(script);
      useStore.getState().setViewMode('canvas');
    } catch {}
  }, []);

  // ── Render helpers ──

  const FlowArrow = () => (
    <div className="agent-flow-arrow">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
        <path d="M12 5v14M7 14l5 5 5-5" />
      </svg>
    </div>
  );

  const renderLogEntries = () => (
    <>
      {log.length === 0 && !offline && (
        <div className="agent-empty">
          输入目标后点击「生成流程图」，Agent 会自动分析任务并生成可视化流程图。
        </div>
      )}
      {log.length === 0 && offline && (
        <div className="agent-empty">
          离线模式：在画布上编辑好流程图，输入描述后点击「离线执行」直接运行。
        </div>
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
                  onClick={() => loadScriptToCanvas(entry.pseudocode)}>
                  查看流程图
                </button>
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

  // ── Render ──

  if (voiceConversationMode) {
    return (
      <div className="agent-view">
        <VoiceConversationPanel
          visible={true}
          onClose={() => setVoiceConversationMode(false)}
          minimized={voicePanelMinimized}
          onToggleMinimize={() => setVoicePanelMinimized(!voicePanelMinimized)}
        />
        {/* Tool explorer modal */}
        {showTools && renderToolExplorer()}
      </div>
    );
  }

  return (
    <div className="agent-view">
      <div className="agent-flowchart">

        {/* ═══════ NODE 1: Goal ═══════ */}
        <div className="agent-flow-node goal-node">
          <div className="agent-flow-node-header">
            <Brain size={15} />
            <span>AI Agent</span>
            <span className={`agent-status ${offline ? 'offline' : 'online'}`}>
              ● {offline ? '离线' : running ? '运行中' : '就绪'}
            </span>
            <button className="btn btn-ghost btn-icon" onClick={() => { setShowTools(true); loadAgentTools(); }}
              style={{ width: 22, height: 22 }} title="可用 Agent 工具">
              <List size={12} />
            </button>
            <button className={`btn btn-ghost btn-icon ${offline ? 'offline' : ''}`}
              onClick={() => setOffline(!offline)}
              style={{ width: 22, height: 22 }} title={offline ? '切换为在线模式' : '切换为离线模式'}>
              {offline ? <WifiOff size={12} /> : <Wifi size={12} />}
            </button>
            {voiceSupported && (
              <button className={`btn btn-ghost btn-icon ${voiceConversationMode ? 'voice-mode-active' : ''}`}
                onClick={() => setVoiceConversationMode(true)}
                style={{ width: 22, height: 22 }} title="语音对话">
                <Volume2 size={12} />
              </button>
            )}
          </div>
          <div className="agent-flow-node-body">
            <div className="agent-goal-row">
              <input
                className="agent-input"
                value={goal}
                onChange={e => { handleSetGoal(e.target.value); setGeneratedScript(null); }}
                onKeyDown={e => e.key === 'Enter' && !running && (offline ? runOffline() : runAgent(false))}
                placeholder={offline ? '输入描述后按 Enter 直接执行当前流程图' : '输入需求后按 Enter 生成流程图'}
                disabled={running}
              />
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

        {/* ═══════ NODE 2: Agent Config ═══════ */}
        <div className="agent-flow-node config-node">
          <div className="agent-flow-node-header config-toggle" onClick={() => setConfigOpen(!configOpen)}>
            <Settings size={14} />
            <span>Agent 配置</span>
            {configOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </div>
          {configOpen && (
            <div className="agent-flow-node-body">
              <div className="agent-config-grid">
                <div className="agent-config-item">
                  <label><MessageSquare size={13} /> LLM 协议</label>
                  <select value={agentProtocol} onChange={e => { setAgentProtocol(e.target.value); localStorage.setItem(AGENT_PROTOCOL_KEY, e.target.value); }}>
                    <option value="auto">自动</option>
                    <option value="text">文本协议</option>
                    <option value="function_calling">函数调用</option>
                  </select>
                </div>
                <div className="agent-config-item">
                  <label><Eye size={13} /> 视觉模式</label>
                  <select value={agentVision} onChange={e => { setAgentVision(e.target.value); localStorage.setItem(AGENT_VISION_KEY, e.target.value); }}>
                    <option value="auto">自动</option>
                    <option value="direct">直接截图</option>
                    <option value="som">SoM 标号</option>
                    <option value="omniparser">OmniParser</option>
                  </select>
                </div>
                <div className="agent-config-item">
                  <label><GitBranch size={13} /> 规划策略</label>
                  <select value={agentPlanning} onChange={e => { setAgentPlanning(e.target.value); localStorage.setItem(AGENT_PLANNING_KEY, e.target.value); }}>
                    <option value="auto">自动</option>
                    <option value="per_step">逐步骤 (ReAct)</option>
                    <option value="pre_plan">预生成计划 (DAG)</option>
                  </select>
                </div>
                <div className="agent-config-item">
                  <label><FileText size={13} /> 屏幕上下文</label>
                  <select value={agentContext} onChange={e => { setAgentContext(e.target.value); localStorage.setItem(AGENT_CONTEXT_KEY, e.target.value); }}>
                    <option value="auto">自动</option>
                    <option value="minimal">精简</option>
                    <option value="full">完整</option>
                  </select>
                </div>
                <div className="agent-config-item">
                  <label><AlertTriangle size={13} /> 错误处理</label>
                  <select value={agentOnError} onChange={e => { setAgentOnError(e.target.value); localStorage.setItem(AGENT_ONERROR_KEY, e.target.value); }}>
                    <option value="retry">重试</option>
                    <option value="replan">重新规划</option>
                    <option value="skip">跳过</option>
                    <option value="stop">停止</option>
                  </select>
                </div>
                <div className="agent-config-item">
                  <label><Zap size={13} /> 快速模式</label>
                  <button className={`toggle ${agentFastMode ? 'on' : ''}`} onClick={() => { const v = !agentFastMode; setAgentFastMode(v); localStorage.setItem(AGENT_FAST_MODE_KEY, v.toString()); }} />
                </div>
                <div className="agent-config-item">
                  <label><Camera size={13} /> 去重截图</label>
                  <button className={`toggle ${agentDedup ? 'on' : ''}`} onClick={() => { const v = !agentDedup; setAgentDedup(v); localStorage.setItem(AGENT_DEDUP_KEY, v.toString()); }} />
                </div>
                <div className="agent-config-item">
                  <label><Eye size={13} /> 窗口感知 OCR</label>
                  <button className={`toggle ${agentWindowAwareOcr ? 'on' : ''}`} onClick={() => { const v = !agentWindowAwareOcr; setAgentWindowAwareOcr(v); localStorage.setItem(AGENT_WINDOW_AWARE_OCR_KEY, v.toString()); }} />
                </div>
                <div className="agent-config-item">
                  <label><Monitor size={13} /> 输入框预聚焦</label>
                  <button className={`toggle ${agentUiaPrefocus ? 'on' : ''}`} onClick={() => { const v = !agentUiaPrefocus; setAgentUiaPrefocus(v); localStorage.setItem(AGENT_UIA_PREFOCUS_KEY, v.toString()); }} />
                </div>
                <div className="agent-config-item">
                  <label><Terminal size={13} /> 脚本模式</label>
                  <button className={`toggle ${agentScriptedMode ? 'on' : ''}`} onClick={() => { const v = !agentScriptedMode; setAgentScriptedMode(v); localStorage.setItem(AGENT_SCRIPTED_MODE_KEY, v.toString()); }} />
                </div>
              </div>
            </div>
          )}
        </div>

        {/* ═══════ NODE 3: Plan ═══════ */}
        {showPlanNode && <FlowArrow />}
        {showPlanNode && (
          <div className="agent-flow-node plan-node">
            <div className="agent-flow-node-header">
              <GitBranch size={14} />
              <span>计划</span>
              {running && !lastSummary && <span className="agent-node-badge running">● 运行中</span>}
            </div>
            <div className="agent-flow-node-body">
              <PlanVisualizer />
              {generatedScript && !running && !lastSummary && (
                <div className="agent-plan-actions">
                  <button className="agent-btn agent-btn-primary" onClick={() => runAgent(true)} style={{ gap: 6 }}>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
                    执行
                  </button>
                  <button className="agent-btn" onClick={() => { setGeneratedScript(null); }} style={{ gap: 6 }}>
                    重新生成
                  </button>
                </div>
              )}
            </div>
          </div>
        )}

        {/* ═══════ NODE 4: Execution ═══════ */}
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
                <div className="agent-guidance-bar" style={{ marginTop: 8 }}>
                  <input
                    className="agent-input"
                    value={guidance}
                    onChange={e => setGuidance(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && sendGuidance()}
                    placeholder="给 agent 下达指令..."
                  />
                  <button className="agent-btn" onClick={sendGuidance} disabled={!guidance.trim()}>发送</button>
                </div>
              )}
            </div>
          </div>
        )}

        {/* ═══════ NODE 5: Result ═══════ */}
        {showResultNode && <FlowArrow />}
        {showResultNode && (
          <div className="agent-flow-node result-node">
            <div className="agent-flow-node-header">
              {lastSummary.status === 'completed' ? (
                <span style={{ color: '#4CAF50', fontSize: 16 }}>✓</span>
              ) : (
                <span style={{ color: '#F44336', fontSize: 16 }}>✗</span>
              )}
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
                        onClick={() => { handleSetGoal(h.goal); setShowHistory(false); }}
                        title="点击填入目标">
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

        {/* Bottom spacer */}
        <div style={{ height: 40, flexShrink: 0 }} />
      </div>

      {/* Tool Explorer Modal */}
      {showTools && renderToolExplorer()}
    </div>
  );

  function renderToolExplorer() {
    return (
      <div style={{
        position: 'fixed', inset: 0, zIndex: 1100,
        background: 'rgba(0,0,0,0.4)', display: 'flex',
        alignItems: 'center', justifyContent: 'center',
      }} onClick={() => setShowTools(false)}>
        <div onClick={e => e.stopPropagation()} style={{
          background: 'var(--bg-primary)', borderRadius: 12,
          boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
          width: 440, maxHeight: '70vh', display: 'flex', flexDirection: 'column',
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
          <div style={{ padding: '8px 16px', fontSize: 11, color: 'var(--text-muted)', borderBottom: '1px solid var(--border-primary)' }}>
            Agent 在执行任务时可调用以下后端工具
          </div>
          <div style={{ flex: 1, overflow: 'auto', padding: 8 }}>
            {toolsLoading ? (
              <div style={{ textAlign: 'center', padding: 20, fontSize: 12, color: 'var(--text-muted)' }}>加载中...</div>
            ) : agentTools.length === 0 ? (
              <div style={{ textAlign: 'center', padding: 20, fontSize: 12, color: 'var(--text-muted)' }}>
                暂无数据<br />
                <span style={{ fontSize: 11 }}>可通过 MCP: <code>list_features</code> 获取完整工具列表</span>
              </div>
            ) : (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3 }}>
                {agentTools.map((name: string) => (
                  <span key={name} style={{
                    fontSize: 11, padding: '2px 8px', borderRadius: 4,
                    background: 'var(--bg-secondary)', border: '1px solid var(--border-primary)',
                    color: 'var(--text-primary)',
                  }}>{name}</span>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }
}
