import React, { useState, useCallback } from 'react';
import { useStore } from '../store';
import { parsePseudoToFlow } from '../utils/pseudoParser';
import { Sparkles, Play, AlertTriangle, CheckCircle, Loader2, Eye, Settings } from 'lucide-react';

const SYSTEM_PROMPT = `你是一个桌面自动化脚本生成器。
将用户需求转换为灵动脚本伪代码，返回JSON格式：{"pseudocode":"...","explanation":"..."}

## 关键词（必须使用中文关键词）
点击 (x,y) [双击|右击|中键] — 鼠标点击
点击图片 "路径" [相似度=N] [超时=N] — 图片识别点击
输入 "文本" [位置 (x,y)] — 输入文本
按键 键1+键2 — 键盘操作
等待 秒数 — 等待时间
等待图片 "路径" [超时=N] — 等待图片出现
等待窗口 "标题" [超时=N] — 等待窗口出现
滑动 (x1,y1) 到 (x2,y2) — 滑动操作
截图 — 全屏截图
$变量名="值" — 设置变量
运行 "脚本名" — 调用子脚本

## 控制流
如果 条件: ... 否则: ... — 条件分支
循环 N: — 计数循环
无限循环: — 无限循环
跳出 — 跳出循环

## 条件
真 — 始终为真
图片存在 "路径" [相似度=N] — 检测图片
窗口存在 "标题" — 检测窗口
变量等于 变量名 "值" — 变量比较

## 示例
{"pseudocode":"按键 win+r\\n等待 0.5\\n输入 \\"notepad\\"\\n按键 enter\\n等待 1\\n输入 \\"hello\\"","explanation":"打开运行→启动记事本→输入文本"}`;

export function AIAssistant() {
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<{ explanation: string; pseudocode: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [execStatus, setExecStatus] = useState<string | null>(null);

  const handleGenerate = useCallback(async () => {
    if (!input.trim()) return;

    let apiKey = (window as any).__DEEPSEEK_KEY__;
    if (!apiKey) {
      try { apiKey = localStorage.getItem('lingdong-deepseek-key') || ''; } catch {}
    }
    if (!apiKey) {
      setError('请先在设置页面配置 DeepSeek API 密钥');
      return;
    }

    setLoading(true);
    setError(null);
    setResult(null);
    setExecStatus(null);

    const messages = [
      { role: 'system' as const, content: SYSTEM_PROMPT },
      { role: 'user' as const, content: input },
    ];

    try {
      const api = (window as any).electronAPI;
      let data: any;

      if (api?.aiGenerate) {
        // Use Electron IPC (main process fetch — no CORS issues)
        data = await api.aiGenerate({ apiKey, messages });
        if (data.error) throw new Error(data.error);
      } else {
        // Browser fallback (direct fetch — may have CORS issues)
        const response = await fetch('https://api.deepseek.com/v1/chat/completions', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${apiKey}`,
          },
          body: JSON.stringify({
            model: 'deepseek-chat',
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
        <span className="ai-assistant-subtitle">用自然语言描述操作，AI自动生成脚本</span>
      </div>

      <div className="ai-assistant-body">
        {/* Input section - takes full space when no results, half when results */}
        <div className={`ai-input-section${result ? ' ai-input-section-split' : ''}`}>
          <textarea
            className="ai-textarea"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="用自然语言描述你要自动化的操作，例如：&#10;打开记事本，输入 hello world，然后截图保存"
          />
          <div className="ai-input-actions">
            <button
              className="btn btn-primary ai-generate-btn"
              onClick={handleGenerate}
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
              <button className="btn btn-ghost" onClick={handleViewCode} style={{ gap: 6 }}>
                <Eye size={14} />
                查看流程图
              </button>
              <button className="btn btn-ghost" onClick={() => { setInput(result.pseudocode); setResult(null); }} style={{ gap: 6 }}>
                <Settings size={14} />
                继续编辑
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
