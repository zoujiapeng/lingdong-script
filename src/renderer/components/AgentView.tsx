import React, { useState, useCallback, useEffect, useRef } from 'react';
import { promptBuilder } from '../utils/promptBuilder';
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
  | { type: 'guidance'; text: string };

export function AgentView() {
  const [goal, setGoal] = useState('');
  const [running, setRunning] = useState(false);
  const [connected, setConnected] = useState(true);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [guidance, setGuidance] = useState('');
  const [generating, setGenerating] = useState(false);
  const msgEndRef = useRef<HTMLDivElement>(null);

  const addEntry = useCallback((entry: LogEntry) => {
    setLog(prev => [...prev, entry]);
  }, []);

  // Scroll to bottom on new log entries
  useEffect(() => {
    msgEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [log]);

  // Listen for agent messages via IPC
  useEffect(() => {
    window.electronAPI?.onAgentMessage((msg: any) => {
      switch (msg.type) {
        case 'agent_start':
          addEntry({ type: 'system', text: `Agent 启动，目标：${msg.goal}` });
          setRunning(true);
          break;

        case 'step_complete':
          addEntry({ type: 'step', data: msg });
          break;

        case 'user_guidance':
          addEntry({ type: 'guidance', text: msg.text });
          break;

        case 'user_guidance_sent':
          addEntry({ type: 'user', text: msg.text });
          break;

        case 'agent_done':
          if (msg.status === 'completed') {
            addEntry({ type: 'complete', steps: msg.total_steps });
          } else {
            addEntry({ type: 'fail', reason: msg.fail_reason || '未知原因' });
          }
          setRunning(false);
          break;

        case 'log':
          if (msg.level === 'error') {
            addEntry({ type: 'system', text: `[错误] ${msg.message}` });
          }
          break;

        case 'error':
          addEntry({ type: 'system', text: `[错误] ${msg.message}` });
          break;
      }
    });

    window.electronAPI?.onAgentLog((msg: any) => {
      if (msg.level === 'error') {
        addEntry({ type: 'system', text: `[进程] ${msg.message}` });
      }
    });
  }, [addEntry]);

  const runAgent = useCallback(async () => {
    if (!goal.trim()) return;
    addEntry({ type: 'system', text: '正在启动 agent...' });
    try {
      await window.electronAPI!.agentStart(goal.trim());
    } catch (err: any) {
      addEntry({ type: 'fail', reason: `启动失败: ${err.message}` });
    }
  }, [goal, addEntry]);

  const stopAgent = useCallback(async () => {
    try {
      await window.electronAPI!.agentStop();
      addEntry({ type: 'system', text: 'Agent 已停止' });
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

  const generateFlow = useCallback(async () => {
    if (!goal.trim() || generating) return;
    setGenerating(true);
    addEntry({ type: 'system', text: '正在生成流程图...' });

    try {
      const apiKey = (window as any).__DEEPSEEK_KEY__ || localStorage.getItem('lingdong-deepseek-key') || '';
      if (!apiKey) throw new Error('请先在设置页面配置 API 密钥');

      const apiBase = ((window as any).__LLM_API_BASE__ || localStorage.getItem('lingdong-llm-base') || 'https://api.deepseek.com/v1').replace(/\/+$/, '');
      const model = (window as any).__LLM_MODEL__ || localStorage.getItem('lingdong-llm-model') || 'deepseek-chat';

      const systemPrompt = promptBuilder.build({
        language: 'chinese',
        query: goal.trim(),
        maxTemplates: 8,
        maxExamples: 3,
      });

      const data = await window.electronAPI!.aiGenerate({
        apiKey,
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: goal.trim() },
        ],
        apiBase,
        model,
      });

      if (data.error) throw new Error(data.error);

      const rawContent = data.choices?.[0]?.message?.content;
      if (!rawContent) throw new Error('AI 返回为空');

      let parsed: any;
      try {
        parsed = JSON.parse(rawContent);
      } catch {
        const jsonMatch = rawContent.match(/\{[\s\S]*\}/);
        if (jsonMatch) parsed = JSON.parse(jsonMatch[0]);
        else parsed = { pseudocode: rawContent };
      }

      const pseudocode = parsed.pseudocode || rawContent;

      const { parsePseudoToFlow } = await import('../utils/pseudoParser');
      const flowScript = parsePseudoToFlow(pseudocode);
      flowScript.meta = {
        ...flowScript.meta,
        name: goal.trim().slice(0, 50),
        description: `由 AI 从目标生成: ${goal.trim()}`,
      };

      useStore.getState().loadScript(flowScript);
      useStore.getState().setViewMode('pseudocode');
      addEntry({ type: 'system', text: '流程图已生成！切换到伪代码/画布视图，点击顶栏「运行」按钮执行。' });
    } catch (err: any) {
      addEntry({ type: 'system', text: `生成失败: ${err.message}` });
    } finally {
      setGenerating(false);
    }
  }, [goal, generating, addEntry]);

  return (
    <div className="agent-view">
      {/* Header */}
      <div className="agent-header">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M12 2a4 4 0 014 4c0 2-2 4-4 6-2-2-4-4-4-6a4 4 0 014-4z" />
          <path d="M12 14c-4 0-8 2-8 4v2h16v-2c0-2-4-4-8-4z" />
        </svg>
        <span>Agent 控制台</span>
        <span className={`agent-status ${connected ? 'online' : ''}`}>
          ● {connected ? '就绪' : '离线'}
        </span>
      </div>

      <div className="agent-body">
        {/* Goal input */}
        <div className="agent-goal-bar">
          <input
            className="agent-input"
            value={goal}
            onChange={e => setGoal(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && runAgent()}
            placeholder="输入目标，例如：打开记事本，输入 Hello World"
            disabled={running}
          />
          {running ? (
            <button className="agent-btn agent-btn-danger" onClick={stopAgent}>
              停止
            </button>
          ) : (
            <>
              <button className="agent-btn" onClick={generateFlow} disabled={!goal.trim() || generating}>
                {generating ? '生成中...' : '生成流程'}
              </button>
              <button className="agent-btn agent-btn-primary" onClick={runAgent} disabled={!goal.trim() || generating}>
                直接执行
              </button>
            </>
          )}
        </div>

        {/* Log area */}
        <div className="agent-log">
          {log.length === 0 && (
            <div className="agent-empty">输入目标后，可选择「生成流程」将其转为流程图编辑执行，或「直接执行」让 Agent 自主运行。</div>
          )}
          {log.map((entry, i) => (
            <div key={i} className={`agent-msg agent-msg-${entry.type}`}>
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
              {entry.type === 'complete' && <strong>任务完成！共 {entry.steps} 步</strong>}
              {entry.type === 'fail' && <strong>任务失败：{entry.reason}</strong>}
            </div>
          ))}
          <div ref={msgEndRef} />
        </div>

        {/* Guidance input */}
        <div className="agent-guidance-bar">
          <input
            className="agent-input"
            value={guidance}
            onChange={e => setGuidance(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && sendGuidance()}
            placeholder={running ? '给 agent 下达指令...' : '等待 agent 启动...'}
            disabled={!running}
          />
          <button className="agent-btn" onClick={sendGuidance} disabled={!running || !guidance.trim()}>
            发送
          </button>
        </div>
      </div>
    </div>
  );
}
