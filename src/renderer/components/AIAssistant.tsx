import React, { useState, useCallback, useRef, useEffect } from 'react';
import { useStore } from '../store';
import { parsePseudoToFlow } from '../utils/pseudoParser';
import { ScoredResult } from '../utils/templateRegistry';
import { matchSemantic, SemanticMatchResult } from '../utils/semanticMatcher';
import { htnPlanner } from '../utils/htnPlanner';
import { htnRegistry } from '../utils/htnRegistry';
import { promptBuilder } from '../utils/promptBuilder';
import { TemplateSelector } from './common/TemplateSelector';
import { Sparkles, Play, AlertTriangle, CheckCircle, Loader2, Eye, Settings, Clock } from 'lucide-react';
import { isScheduleIntent, parseQuickSchedule } from '../utils/quickScheduleParser';

export function AIAssistant() {
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<{ explanation: string; pseudocode: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [execStatus, setExecStatus] = useState<string | null>(null);
  const [showTemplateSelector, setShowTemplateSelector] = useState(false);
  const [templateCandidates, setTemplateCandidates] = useState<SemanticMatchResult | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-focus textarea
  useEffect(() => {
    textareaRef.current?.focus();
  }, []);

  const doGenerate = useCallback(async (text: string, skipOffline?: boolean) => {
    if (!text.trim()) return;

    setLoading(true);
    setError(null);
    setResult(null);
    setExecStatus(null);
    setShowTemplateSelector(false);
    setTemplateCandidates(null);

    // ── Phase 0: Quick Schedule (offline, no LLM) ──
    if (isScheduleIntent(text)) {
      const parsed = parseQuickSchedule(text);
      if (parsed.confidence >= 70 && parsed.triggers.length > 0) {
        try {
          const entry: any = {
            name: parsed.name,
            triggers: parsed.triggers,
            template_id: parsed.template_id || '__quick_schedule__',
            template_name: '',
            enabled: true,
          };
          if (parsed.notify_message) entry.notify_message = parsed.notify_message;
          if (parsed.launch_target) entry.launch_target = parsed.launch_target;

          await (window as any).electronAPI?.scheduleCreate(entry);

          setResult({
            explanation: `已创建定时任务「${parsed.name}」`,
            pseudocode: `定时: ${parsed.triggers.map((t: any) => t.cron_expr || `每${t.interval_seconds}s`).join(', ')}` +
              (parsed.notify_message ? `\n提醒: ${parsed.notify_message}` : '') +
              (parsed.launch_target ? `\n打开: ${parsed.launch_target}` : ''),
          });
          setExecStatus(`✅ 已创建定时任务: ${parsed.name}`);
          setLoading(false);
          return;
        } catch (e: any) {
          setError(`创建调度失败: ${e.message}`);
          setLoading(false);
          return;
        }
      }
      if (parsed.confidence >= 50 && parsed.triggers.length > 0) {
        // 低置信度 — 仍然创建但提示用户确认
        try {
          const entry: any = {
            name: parsed.name,
            triggers: parsed.triggers,
            template_id: parsed.template_id || '__quick_schedule__',
            template_name: '', enabled: true,
          };
          if (parsed.notify_message) entry.notify_message = parsed.notify_message;
          if (parsed.launch_target) entry.launch_target = parsed.launch_target;
          await (window as any).electronAPI?.scheduleCreate(entry);
        } catch {}
        setResult({
          explanation: `已创建定时，建议到设置里检查时间是否正确（置信度 ${parsed.confidence}%）`,
          pseudocode: `定时: ${parsed.triggers.map((t: any) => t.cron_expr || `每${t.interval_seconds}s`).join(', ')}` +
            (parsed.notify_message ? `\n提醒: ${parsed.notify_message}` : '') +
            (parsed.launch_target ? `\n打开: ${parsed.launch_target}` : ''),
        });
        setExecStatus(`⚠️ 已创建: ${parsed.name}（请在设置中确认时间）`);
        setLoading(false);
        return;
      }
      // 置信度过低，回退正常流程
    }

    // ── Phase 1: HTN Planner (offline, structured decomposition) ──
    if (!skipOffline) {
      const planResult = htnPlanner.plan(text);
      if (planResult && planResult.score >= 90) {
      try {
        const script = parsePseudoToFlow(planResult.pseudocode);
        if (script && script.nodes && script.nodes.length > 0) {
          script.meta = {
            ...script.meta,
            id: useStore.getState().script.meta.id,
            name: planResult.rootName,
          };
          useStore.getState().loadScript(script);
          setExecStatus(`已匹配模板: ${planResult.rootName}（离线匹配）`);
        }
      } catch {}
      setResult({
        explanation: planResult.explanation,
        pseudocode: planResult.pseudocode,
      });
      setLoading(false);
      return;
    }

    if (planResult && planResult.score >= 50) {
      try {
        const script = parsePseudoToFlow(planResult.pseudocode);
        if (script && script.nodes && script.nodes.length > 0) {
          script.meta = {
            ...script.meta,
            id: useStore.getState().script.meta.id,
            name: planResult.rootName,
          };
          useStore.getState().loadScript(script);
          setExecStatus(`推荐模板: ${planResult.rootName}（匹配度 ${Math.round(planResult.score)}%）`);
        }
      } catch {}
      setResult({
        explanation: planResult.explanation,
        pseudocode: planResult.pseudocode,
      });
      setLoading(false);
      return;
    }
    } // end Phase 1 skipOffline

    // ── Phase 2: Template Registry Matching (offline, flat) ──
    if (!skipOffline) {
      const matchResult = matchSemantic(text);
      if (matchResult.autoSelect && matchResult.best) {
      const entry = matchResult.best.template;
      if (entry.flowScript) {
        useStore.getState().loadScript(entry.flowScript);
        setExecStatus(`已自动匹配模板: ${entry.name}`);
      }
      setResult({
        explanation: `匹配到模板: ${entry.name}`,
        pseudocode: `调用模板: ${entry.name}`,
      });
      setLoading(false);
      return;
    }

    if (matchResult.candidates.length > 0) {
      setTemplateCandidates(matchResult);
      setShowTemplateSelector(true);
      setLoading(false);
      return;
    }
    } // end Phase 2 skipOffline

    // ── Phase 3: AI Generation (online) ──
    let apiKey = (window as any).__DEEPSEEK_KEY__;
    if (!apiKey) {
      try { apiKey = localStorage.getItem('lingdong-deepseek-key') || ''; } catch {}
    }
    if (!apiKey) {
      setError('请先在设置页面配置 DeepSeek API 密钥');
      setLoading(false);
      return;
    }

    const apiBase = ((window as any).__LLM_API_BASE__ || localStorage.getItem('lingdong-llm-base') || 'https://api.deepseek.com/v1').replace(/\/+$/, '');
    const model = (window as any).__LLM_MODEL__ || localStorage.getItem('lingdong-llm-model') || 'deepseek-chat';
    const multimodal = (window as any).__LLM_MULTIMODAL__ !== undefined
      ? (window as any).__LLM_MULTIMODAL__ : localStorage.getItem('lingdong-llm-multimodal') === 'true';

    // Warn if multimodal is enabled with a likely text-only model
    if (multimodal) {
      const textOnlyPatterns = [/^deepseek/i, /^gpt-3\.5/i, /^gpt-4$/i, /^llama/i, /^mistral/i, /^mixtral/i, /^phi-/i];
      const isLikelyTextOnly = textOnlyPatterns.some(pat => pat.test(model.trim()));
      if (isLikelyTextOnly) {
        setExecStatus(`⚠️ 当前模型「${model}」可能不支持多模态（视觉识别）。如调用出错，请到设置中关闭多模态开关，或更换为支持多模态的模型（GPT-4o、Claude 3.5+、Qwen-VL 等）。`);
      }
    }

    const systemPrompt = promptBuilder.build({
      language: 'chinese',
      query: text,
      maxTemplates: 8,
      maxExamples: 3,
    });
    const messages = [
      { role: 'system' as const, content: systemPrompt },
      { role: 'user' as const, content: text },
    ];

    try {
      const api = (window as any).electronAPI;
      let data: any;

      if (api?.aiGenerate) {
        data = await api.aiGenerate({ apiKey, messages, apiBase, model });
        if (data.error) throw new Error(data.error);
      } else {
        const response = await fetch(`${apiBase}/chat/completions`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${apiKey}`,
          },
          body: JSON.stringify({
            model,
            messages,
            response_format: { type: 'json_object' },
            temperature: 0.1,
            max_tokens: 2048,
          }),
        });
        if (!response.ok) {
          const text = await response.text();
          throw new Error(`API 错误 (${response.status}): ${text}`);
        }
        data = await response.json();
      }

      const content = data.choices?.[0]?.message?.content;
      if (!content) throw new Error('API 返回为空');

      const parsed = JSON.parse(content);
      if (!parsed.pseudocode) throw new Error('未能生成伪代码');

      setResult({
        explanation: parsed.explanation || '',
        pseudocode: parsed.pseudocode,
      });

      // Auto-load into flowchart
      try {
        const script = parsePseudoToFlow(parsed.pseudocode);
        script.meta = {
          ...script.meta,
          id: useStore.getState().script.meta.id,
          name: useStore.getState().script.meta.name + ' (AI)',
        };
        useStore.getState().loadScript(script);
      } catch {}

      // Post-generation template check
      const postMatch = matchSemantic(text);
      if (postMatch.autoSelect && postMatch.best) {
        const entry = postMatch.best.template;
        if (entry.flowScript) {
          useStore.getState().loadScript(entry.flowScript);
          setExecStatus(`已自动匹配模板: ${entry.name}`);
        }
      } else if (postMatch.candidates.length > 0 && !postMatch.autoSelect) {
        setTemplateCandidates(postMatch);
        setShowTemplateSelector(true);
      } else {
        // ── Auto-register new pattern ──
        // AI generated a flow that no existing template covers
        try {
          const script = parsePseudoToFlow(parsed.pseudocode);
          if (script?.nodes) {
            const nodeId = htnRegistry.registerFromFlowNodes(text, script.nodes);
            if (nodeId) {
              setExecStatus(`已记录新流程模式（可在类似需求时自动匹配）`);
            }
          }
        } catch {}
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  const handleTemplateSelect = useCallback((result: ScoredResult) => {
    setShowTemplateSelector(false);
    setTemplateCandidates(null);
    const entry = result.template;
    if (entry.flowScript) {
      useStore.getState().loadScript(entry.flowScript);
      setExecStatus(`已加载模板: ${entry.name}`);
      setTimeout(() => {
        useStore.getState().startExecution();
        setExecStatus(`正在执行: ${entry.name}`);
      }, 300);
    }
  }, []);

  const handleGenerateNew = useCallback(() => {
    setShowTemplateSelector(false);
    setTemplateCandidates(null);
    // Skip offline matching, go directly to AI generation
    if (input.trim()) {
      doGenerate(input, true);
    }
  }, [input, doGenerate]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      doGenerate(input);
    }
  }, [input, doGenerate]);

  const handleExecute = useCallback(() => {
    if (!result?.pseudocode) return;

    try {
      const script = parsePseudoToFlow(result.pseudocode);
      script.meta = {
        ...script.meta,
        id: useStore.getState().script.meta.id,
        name: useStore.getState().script.meta.name + ' (AI)',
      };
      useStore.getState().loadScript(script);
      setExecStatus('已加载到画布');
      setTimeout(() => {
        useStore.getState().startExecution();
        setExecStatus('已开始执行');
      }, 300);
    } catch (e: any) {
      setError(`执行失败: ${e.message}`);
    }
  }, [result]);

  const handleViewCode = useCallback(() => {
    if (!result?.pseudocode) return;
    try {
      const script = parsePseudoToFlow(result.pseudocode);
      script.meta = {
        ...script.meta,
        id: useStore.getState().script.meta.id,
        name: useStore.getState().script.meta.name + ' (AI)',
      };
      useStore.getState().loadScript(script);
      useStore.getState().setViewMode('canvas');
    } catch {}
  }, [result]);

  return (
    <div className="ai-assistant">
      <div className="ai-assistant-header">
        <Sparkles size={18} />
        <span>AI 助手</span>
        <span className="ai-assistant-subtitle">用自然语言描述操作，AI自动生成脚本</span>
      </div>

      <div className="ai-assistant-body">
        {/* Input section - takes full space when no results, half when results */}
        <div className={`ai-input-section${result ? ' ai-input-section-split' : ''}`}>
          <textarea
            ref={textareaRef}
            className="ai-textarea"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="输入需求后按 Enter 生成脚本&#10;Shift+Enter 换行&#10;例如：打开记事本，输入 hello world，然后截图保存"
          />
          <div className="ai-input-actions">
            <button
              className="btn btn-primary ai-generate-btn"
              onClick={() => doGenerate(input)}
              disabled={loading || !input.trim()}
            >
              {loading ? (
                <Loader2 size={16} className="spin" />
              ) : (
                <Sparkles size={16} />
              )}
              {loading ? '生成中...' : '生成脚本'}
            </button>
          </div>
        </div>

        {/* Error */}
        {error && (
          <div className="ai-error">
            <AlertTriangle size={14} />
            <span style={{ flex: 1 }}>{error}</span>
            {error.includes('API 密钥') && (
              <button
                className="btn btn-ghost"
                style={{ padding: '3px 8px', fontSize: 12, flexShrink: 0 }}
                onClick={() => useStore.getState().setViewMode('settings')}
              >
                <Settings size={12} />
                去设置
              </button>
            )}
          </div>
        )}

        {/* Result */}
        {result && (
          <div className="ai-result">
            {/* Explanation */}
            {result.explanation && (
              <div className="ai-explanation">
                <div className="ai-explanation-label">计划</div>
                <p>{result.explanation}</p>
              </div>
            )}

            {/* Pseudocode */}
            <div className="ai-pseudocode-area">
              <div className="ai-pseudocode-label">伪代码</div>
              <pre className="ai-pseudocode">{result.pseudocode}</pre>
            </div>

            {/* Actions */}
            <div className="ai-actions">
              <button className="btn btn-success" onClick={handleExecute} style={{ gap: 6 }}>
                <Play size={14} fill="white" />
                执行
              </button>
              <button className="btn btn-ghost" onClick={() => useStore.getState().setViewMode('canvas')} style={{ gap: 6 }}>
                <Eye size={14} />
                查看流程图
              </button>
            </div>

            {execStatus && (
              <div className="ai-exec-status">
                <CheckCircle size={14} />
                <span>{execStatus}</span>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Template Selector Dialog */}
      {showTemplateSelector && templateCandidates && (
        <TemplateSelector
          query={templateCandidates.query}
          candidates={templateCandidates.candidates}
          loading={false}
          onSelect={handleTemplateSelect}
          onGenerate={handleGenerateNew}
          onCancel={() => setShowTemplateSelector(false)}
        />
      )}
    </div>
  );
}
