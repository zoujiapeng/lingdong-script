import React, { useState, useCallback, useEffect, useRef } from 'react';
import { useStore } from '../store';

interface StepData {
  step_num: number;
  tool: string;
  thought: string;
  result: { success: boolean; confidence: number };
}

type LogEntry =
  | { type: 'system'; text: string }
  | { type: 'step'; data: StepData }
  | { type: 'user'; text: string }
  | { type: 'complete'; steps: number }
  | { type: 'fail'; reason: string }
  | { type: 'guidance'; text: string }
  | { type: 'script'; pseudocode: string; explanation: string };

export function AgentView() {
  const [goal, setGoal] = useState('');
  const [running, setRunning] = useState(false);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [guidance, setGuidance] = useState('');
  const msgEndRef = useRef<HTMLDivElement>(null);

  const addEntry = useCallback((entry: LogEntry) => {
    setLog(prev => [...prev, entry]);
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
          addEntry({ type: 'system', text: `正在处理：${msg.goal}` });
          setRunning(true);
          break;

        case 'step_complete': {
          addEntry({ type: 'step', data: msg });
          // If agent generated a flow script, auto-load it into the canvas
          if (msg.tool === 'generate_flow_script' && msg.result?.success) {
            const data = msg.result.data || {};
            // Handle both pre-step path (data = inner_data with pseudocode)
            // and ReAct loop path (data = registry wrapper with result.result.pseudocode)
            const pseudocode = data.pseudocode || data.result?.result?.pseudocode || data.result?.pseudocode;
            const explanation = data.explanation || data.result?.result?.explanation || data.result?.explanation || '';
            if (pseudocode) {
              addEntry({ type: 'script', pseudocode, explanation });
              // Auto-load the script into the flowchart canvas
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
          addEntry(msg.status === 'completed'
            ? { type: 'complete', steps: msg.total_steps }
            : { type: 'fail', reason: msg.fail_reason || '未知原因' });
          setRunning(false);
          break;

        case 'log':
          if (msg.level === 'error') addEntry({ type: 'system', text: `[错误] ${msg.message}` });
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

  const runAgent = useCallback(async () => {
    if (!goal.trim()) return;
    setLog([]);
    addEntry({ type: 'system', text: '正在启动...' });

    // Read API config from settings
    const apiKey = (window as any).__DEEPSEEK_KEY__ || localStorage.getItem('lingdong-deepseek-key') || '';
    const apiBase = ((window as any).__LLM_API_BASE__ || localStorage.getItem('lingdong-llm-base') || '').replace(/\/+$/, '');
    const model = (window as any).__LLM_MODEL__ || localStorage.getItem('lingdong-llm-model') || '';

    try {
      await window.electronAPI!.agentStart(goal.trim(), { api_key: apiKey, api_base: apiBase, model }, true);
    } catch (err: any) {
      addEntry({ type: 'fail', reason: `启动失败: ${err.message}` });
    }
  }, [goal, addEntry]);

  const stopAgent = useCallback(async () => {
    try {
      await window.electronAPI!.agentStop();
      addEntry({ type: 'system', text: '已停止' });
      setRunning(false);
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
            onChange={e => setGoal(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && !running && runAgent()}
            placeholder="输入目标，例如：打开记事本输入 Hello World，或：每天早上9点提醒我开会"
            disabled={running}
          />
          {running ? (
            <button className="agent-btn agent-btn-danger" onClick={stopAgent}>停止</button>
          ) : (
            <button className="agent-btn agent-btn-primary" onClick={runAgent} disabled={!goal.trim()}>
              运行
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
          {log.map((entry, i) => (
            <div key={i} className={`agent-msg agent-msg-${entry.type === 'script' ? 'system' : entry.type}`}>
              {entry.type === 'system' && <span>{entry.text}</span>}

              {entry.type === 'step' && (
                <>
                  <span className="agent-step-num">[{entry.data.step_num}]</span>{' '}
                  <span className="agent-action">{entry.data.tool}</span>{' '}
                  <span className="agent-conf">conf={entry.data.result.confidence.toFixed(2)}</span>
                  {entry.data.result.success ? ' ✓' : ' ✗'}
                  {entry.data.thought && <div className="agent-thought">{entry.data.thought.slice(0, 200)}</div>}
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
