import React, { useState, useCallback } from 'react';
import { useStore } from '../store';
import { parsePseudoToFlow } from '../utils/pseudoParser';
import { Sparkles, Play, AlertTriangle, CheckCircle, Loader2, Eye } from 'lucide-react';

export function AIAssistant() {
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<{ explanation: string; pseudocode: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [execStatus, setExecStatus] = useState<string | null>(null);

  const handleGenerate = useCallback(async () => {
    if (!input.trim()) return;

    const apiKey = (window as any).__DEEPSEEK_KEY__;
    if (!apiKey) {
      setError('请先在设置页面配置 DeepSeek API 密钥');
      return;
    }

    setLoading(true);
    setError(null);
    setResult(null);
    setExecStatus(null);

    try {
      const response = await fetch('https://api.deepseek.com/v1/chat/completions', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${apiKey}`,
        },
        body: JSON.stringify({
          model: 'deepseek-chat',
          messages: [
            {
              role: 'system',
              content: `你是一个桌面自动化脚本生成器。
将用户需求转换为灵动脚本伪代码，返回JSON格式：{"pseudocode":"...","explanation":"..."}

命令：click (x,y)、click image "路径"、type "文本"、press 键、wait 秒、swipe (x1,y1) to (x2,y2)、screenshot、$变量="值"
控制流：if 条件: ... else: ...、loop N: ...、loop infinite: ...、break
条件：true、image_exists、window_exists、var_eq、var_gt、var_lt

示例：{"pseudocode":"press win+r\\nwait 0.5\\ntype \\"notepad\\"\\npress enter\\nwait 1\\ntype \\"hello\\"","explanation":"打开运行→启动记事本→输入文本"}`,
            },
            { role: 'user', content: input },
          ],
          response_format: { type: 'json_object' },
          temperature: 0.1,
          max_tokens: 2048,
        }),
      });

      if (!response.ok) {
        const text = await response.text();
        throw new Error(`API 错误 (${response.status}): ${text}`);
      }

      const data = await response.json();
      const content = data.choices?.[0]?.message?.content;
      if (!content) throw new Error('API 返回为空');

      const parsed = JSON.parse(content);
      if (!parsed.pseudocode) throw new Error('未能生成伪代码');

      setResult({
        explanation: parsed.explanation || '',
        pseudocode: parsed.pseudocode,
      });
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [input]);

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
      </div>

      <div className="ai-assistant-body">
        {/* Input */}
        <div className="ai-input-area">
          <textarea
            className="ai-textarea"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="用自然语言描述你要自动化的操作，例如：&#10;打开记事本，输入 hello world，然后截图保存"
            rows={4}
          />
          <button
            className="btn btn-primary"
            onClick={handleGenerate}
            disabled={loading || !input.trim()}
            style={{ gap: 6, alignSelf: 'flex-end' }}
          >
            {loading ? (
              <Loader2 size={15} className="spin" />
            ) : (
              <Sparkles size={15} />
            )}
            {loading ? '生成中...' : '生成伪代码'}
          </button>
        </div>

        {/* Error */}
        {error && (
          <div className="ai-error">
            <AlertTriangle size={14} />
            <span>{error}</span>
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
              <button className="btn btn-ghost" onClick={handleViewCode} style={{ gap: 6 }}>
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
    </div>
  );
}
