import React, { useState, useCallback, useEffect, useRef } from 'react';
import { useStore } from '../store';

interface StepData {
  step_num: number;
  tool: string;
  thought: string;
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

export function AgentView() {
  const [goal, setGoal] = useState('');
  const [running, setRunning] = useState(false);
  const [generatedScript, setGeneratedScript] = useState<{ pseudocode: string; explanation: string } | null>(null);
  const [log, setLog] = useState<LogEntry[]>(() => _persistedLog);
  const [guidance, setGuidance] = useState('');
  const msgEndRef = useRef<HTMLDivElement>(null);

  const addEntry = useCallback((entry: LogEntry) => {
    const entryWithId = { ...entry, _id: ++_logIdCounter } as any;
    _persistedLog = [..._persistedLog, entryWithId];
    setLog(_persistedLog);
  }, []);

  useEffect(() => {
    msgEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [log]);

  // Listen for agent messages via IPC
  useEffect(() => {
    const api = window.electronAPI;
    if (!api) return;

    const unsubMessages = api.onAgentMessage?.((msg: any) => {
      switch (msg.type) {
        case 'agent_start':
          _hasActiveRun = true;
          addEntry({ type: 'system', text: `正在处理：${msg.goal}` });
          setRunning(true);
          break;

        case 'step_complete': {
          addEntry({ type: 'step', data: msg });
          // If agent generated a flow script, auto-load it into the canvas
          if (msg.tool === 'generate_flow_script' && msg.result?.success) {
            const data = msg.result.data || {};
            const pseudocode = data.pseudocode || data.result?.result?.pseudocode || data.result?.pseudocode;
            const explanation = data.explanation || data.result?.result?.explanation || data.result?.explanation || '';
            if (pseudocode) {
              addEntry({ type: 'script', pseudocode, explanation });
              setGeneratedScript({ pseudocode, explanation });
              loadScriptToCanvas(pseudocode);
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
          addEntry(msg.status === 'completed'
            ? { type: 'complete', steps: msg.total_steps }
            : { type: 'fail', reason: msg.fail_reason || '未知原因' });
          setRunning(false);
          _hasActiveRun = false;
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

  const runAgent = useCallback(async (supervise: boolean) => {
    if (!goal.trim()) return;
    _hasActiveRun = false;
    if (supervise) {
      // Execute mode: clear logs for clean execution output
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
    const apiKey = rawKey.trim();
    const apiBase = rawBase.trim();
    const model = rawModel.trim();

    if (!apiKey) {
      addEntry({ type: 'system', text: '⚠️ 请先在设置页面配置 API 密钥后重试' });
      setRunning(false);
      return;
    }

    try {
      await window.electronAPI!.agentStart(goal.trim(), { api_key: apiKey, api_base: apiBase, model, multimodal: multimodal ? 'true' : 'false', verify_enabled: verifyEnabled ? 'true' : 'false' }, supervise);
    } catch (err: any) {
      addEntry({ type: 'fail', reason: `启动失败: ${err.message}` });
    }
  }, [goal, addEntry]);

  const stopAgent = useCallback(async () => {
    try {
      await window.electronAPI!.agentStop();
      addEntry({ type: 'system', text: '已停止' });
      setRunning(false);
      _hasActiveRun = false;
    } catch (err: any) {
      addEntry({ type: 'system', text: `停止失败: ${err.message}` });
    }
  }, [addEntry]);

  const sendGuidance = useCallback(async () => {
    if (!guidance.trim() || !running) return;
    try {
      await window.electronAPI!.agentGuidance(guidance.trim());
    } catch {}
    setGuidance('');
  }, [guidance, running]);

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

  return (
    <div className="agent-view">
      <div className="agent-header">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M12 2a4 4 0 014 4c0 2-2 4-4 6-2-2-4-4-4-6a4 4 0 014-4z" />
          <path d="M12 14c-4 0-8 2-8 4v2h16v-2c0-2-4-4-8-4z" />
        </svg>
        <span>AI Agent</span>
        <span className="agent-status online">● {running ? '运行中' : '就绪'}</span>
      </div>

      <div className="agent-body">
        {/* Single input bar */}
        <div className="agent-goal-bar">
          <input
            className="agent-input"
            value={goal}
            onChange={e => { setGoal(e.target.value); setGeneratedScript(null); }}
            onKeyDown={e => e.key === 'Enter' && !running && runAgent(false)}
            placeholder="输入需求后按 Enter 生成流程图，再按「执行」运行动作"
            disabled={running}
          />
          {running ? (
            <button className="agent-btn agent-btn-danger" onClick={stopAgent}>停止</button>
          ) : generatedScript ? (
            <>
              <button className="agent-btn agent-btn-primary" onClick={() => runAgent(true)} style={{ gap: 6 }}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
                执行
              </button>
              <button className="agent-btn" onClick={() => { setGeneratedScript(null); }} style={{ gap: 6 }}>
                重新生成
              </button>
            </>
          ) : (
            <button className="agent-btn agent-btn-primary" onClick={() => runAgent(false)} disabled={!goal.trim()} style={{ gap: 6 }}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 2a4 4 0 014 4c0 2-2 4-4 6-2-2-4-4-4-6a4 4 0 014-4z"/><path d="M12 14c-4 0-8 2-8 4v2h16v-2c0-2-4-4-8-4z"/></svg>
              生成流程图
            </button>
          )}
        </div>

        {/* Log area */}
        <div className="agent-log">
          {log.length === 0 && (
            <div className="agent-empty">
              输入目标后点击「运行」，Agent 会自动分析任务并选择最佳执行方式：
              简单操作直接执行，复杂任务生成可视化流程图。
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
                  <span className="agent-conf"> conf={entry.data.result.confidence.toFixed(2)}</span>
                  {entry.data.result.success ? ' ✓' : ' ✗'}
                  {(entry.data.thought || (entry.data as any).error) && (
                    <div className="agent-thought">{(entry.data as any).error || entry.data.thought?.slice(0, 200)}</div>
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
          <div ref={msgEndRef} />
        </div>

        {/* Guidance input (only when agent running) */}
        {running && (
          <div className="agent-guidance-bar">
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
  );
}
