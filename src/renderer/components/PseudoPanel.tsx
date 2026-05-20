import React, { useMemo, useState, useCallback, useEffect, useRef } from 'react';
import { useStore } from '../store';
import { generateText, TextMode } from '../utils/textGenerator';
import { Copy, Download, Edit3, AlertTriangle, CheckCircle, XCircle, Languages } from 'lucide-react';

export function PseudoPanel() {
  const script = useStore(s => s.script);
  const featureList = useStore(s => s.featureList);
  const [displayCode, setDisplayCode] = useState('');
  const [draftCode, setDraftCode] = useState('');
  const [isEditing, setIsEditing] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [initialized, setInitialized] = useState(false);
  const [textMode, setTextMode] = useState<TextMode>('pseudocode');
  const syncTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const editRef = useRef<HTMLTextAreaElement>(null);

  // Sync feature keyword map to textGenerator for DSL generation
  useEffect(() => {
    if (featureList.length > 0) {
      const map: Record<string, string> = {};
      for (const f of featureList) {
        if (f.dsl_keyword) {
          map[f.name] = f.dsl_keyword;
        }
      }
      import('../utils/textGenerator').then(m => m._setFeatureKeywordMap(map));
    }
  }, [featureList]);

  // Generate text from current script (both modes)
  const generatedCode = useMemo(() => {
    try {
      return generateText(script, textMode);
    } catch (e: any) {
      return `# 生成失败: ${e.message}`;
    }
  }, [script, textMode]);

  // Initialize display
  useEffect(() => {
    if (!initialized) {
      setDisplayCode(generatedCode);
      setInitialized(true);
    }
  }, [generatedCode, initialized]);

  // Sync from store to display when not editing
  useEffect(() => {
    if (initialized && !isEditing) {
      setDisplayCode(generatedCode);
      setErrorMessage(null);
    }
  }, [generatedCode, initialized, isEditing]);

  // Toggle mode
  const handleToggleMode = useCallback(() => {
    setTextMode((prev) => (prev === 'pseudocode' ? 'dsl' : 'pseudocode'));
  }, []);

  // Start editing
  const handleStartEditing = useCallback(() => {
    setDraftCode(displayCode);
    setIsEditing(true);
    setErrorMessage(null);
  }, [displayCode]);

  // Cancel editing
  const handleCancelEdit = useCallback(() => {
    setIsEditing(false);
    setErrorMessage(null);
    setDraftCode('');
    if (syncTimerRef.current) clearTimeout(syncTimerRef.current);
  }, []);

  // Auto-sync from textarea edits (debounced) — only for pseudocode mode
  const handleAutoSync = useCallback((code: string) => {
    if (!code.trim()) return;
    if (textMode !== 'pseudocode') return;
    const trimmed = code.trim();
    if (trimmed.startsWith('#') || trimmed === '') return;

    try {
      // Dynamic import to avoid circular deps
      import('../utils/pseudoParser').then(({ parsePseudoToFlow }) => {
        const newScript = parsePseudoToFlow(code);
        newScript.meta = {
          ...newScript.meta,
          id: script.meta.id,
          name: script.meta.name,
          description: script.meta.description,
        };
        useStore.getState().loadScript(newScript);
        setDisplayCode(code);
        setErrorMessage(null);
      }).catch(() => {});
    } catch (e: any) {
      setErrorMessage(e.message);
    }
  }, [script.meta.id, script.meta.name, script.meta.description, textMode]);

  const debouncedSync = useCallback((code: string) => {
    if (syncTimerRef.current) clearTimeout(syncTimerRef.current);
    syncTimerRef.current = setTimeout(() => handleAutoSync(code), 300);
  }, [handleAutoSync]);

  useEffect(() => {
    return () => { if (syncTimerRef.current) clearTimeout(syncTimerRef.current); };
  }, []);

  const handleCodeChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const newCode = e.target.value;
    setDraftCode(newCode);
    debouncedSync(newCode);
  }, [debouncedSync]);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(displayCode);
  }, [displayCode]);

  const handleDownload = useCallback(() => {
    const blob = new Blob([displayCode], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${script.meta.name}.${textMode === 'dsl' ? 'dsl' : 'flow'}`;
    a.click();
    URL.revokeObjectURL(url);
  }, [displayCode, script.meta.name, textMode]);

  const handleExportJSON = useCallback(() => {
    const json = JSON.stringify(script, null, 2);
    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${script.meta.name}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }, [script]);

  // Syntax highlighting — dynamic from registry keywords
  const dslKeywords = useMemo(() => {
    const kws: string[] = [];
    for (const f of featureList) {
      if (f.dsl_keyword) kws.push(f.dsl_keyword);
    }
    // Add control flow keywords not in registry
    kws.push('IF', 'ELSE', 'END', 'LOOP', 'BREAK', 'RETURN', 'CALL', 'IMPORT', 'PARALLEL', 'WHILE', 'UNTIL', 'ELIF', 'ELSEIF');
    return [...new Set(kws)];
  }, [featureList]);

  const highlighted = useMemo(() => {
    return highlightText(displayCode, textMode, dslKeywords);
  }, [displayCode, textMode, dslKeywords]);

  return (
    <div className="code-panel" style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Toolbar */}
      <div className="code-panel-toolbar">
        <div className="code-panel-toolbar-left">
          <button className="btn btn-ghost" onClick={handleCopy} style={{ gap: 4 }}>
            <Copy size={13} /> 复制
          </button>
          <button className="btn btn-ghost" onClick={handleDownload} style={{ gap: 4 }}>
            <Download size={13} /> 下载
          </button>
          <button className="btn btn-ghost" onClick={handleExportJSON} style={{ gap: 4 }}>
            <Download size={13} /> 导出 JSON
          </button>
          {!isEditing ? (
            <button className="btn btn-primary" onClick={handleStartEditing} style={{ gap: 4, fontSize: 12 }}>
              <Edit3 size={13} /> 编辑
            </button>
          ) : (
            <button className="btn btn-ghost" onClick={handleCancelEdit} style={{ gap: 4 }}>
              <XCircle size={13} /> 取消
            </button>
          )}
        </div>
        <div className="code-panel-toolbar-right">
          {/* Mode toggle */}
          <button
            className="btn btn-ghost"
            onClick={handleToggleMode}
            style={{ gap: 4, fontSize: 12, fontWeight: 600 }}
            title={textMode === 'pseudocode' ? '切换到 DSL (英文关键词)' : '切换到伪代码 (中文关键词)'}
          >
            <Languages size={13} />
            {textMode === 'pseudocode' ? '伪代码' : 'DSL'}
          </button>
          <span className="code-panel-status" style={{ marginLeft: 8 }}>
            {isEditing ? (
              <span style={{ color: '#818cf6' }}>
                <Edit3 size={12} style={{ verticalAlign: 'middle', marginRight: 4 }} /> 编辑模式
              </span>
            ) : errorMessage ? (
              <span style={{ color: '#ef4444' }}>
                <AlertTriangle size={12} style={{ verticalAlign: 'middle', marginRight: 4 }} /> 同步错误
              </span>
            ) : (
              <span style={{ color: '#34d399' }}>
                <CheckCircle size={12} style={{ verticalAlign: 'middle', marginRight: 4 }} /> 已同步
              </span>
            )}
          </span>
        </div>
      </div>

      {/* Error banner */}
      {errorMessage && isEditing && (
        <div style={{ background: '#fbbf24', color: '#1a1b26', padding: '8px 16px', fontSize: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
          <AlertTriangle size={14} />
          <span>解析错误: {errorMessage}</span>
        </div>
      )}

      {/* Code area */}
      <div className="code-panel-editor" style={{ flex: 1 }}>
        {isEditing ? (
          <textarea
            ref={editRef}
            className="code-textarea"
            value={draftCode}
            onChange={handleCodeChange}
            spellCheck={false}
            wrap="off"
          />
        ) : (
          <div
            className="code-display"
            dangerouslySetInnerHTML={{ __html: highlighted }}
          />
        )}
      </div>

      {/* Status bar */}
      <div className="code-panel-status" style={{ padding: '6px 12px', borderTop: '1px solid var(--border-primary)', justifyContent: 'space-between', display: 'flex' }}>
        <span>{textMode === 'dsl' ? 'DSL' : '伪代码'} · {displayCode.split('\n').length} 行</span>
        <span>{script.nodes.length} 节点 · {script.edges.length} 边</span>
      </div>
    </div>
  );
}

// ─── Syntax highlighting ───

function highlightText(code: string, mode: TextMode, dslKeywords: string[] = []): string {
  const escaped = code.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const lines = escaped.split('\n');
  const dslKwPattern = dslKeywords.length > 0
    ? dslKeywords.sort((a, b) => b.length - a.length).join('|')
    : '';

  return lines.map((line) => {
    if (/^\s*#/.test(line)) return `<span class="hl-comment">${line}</span>`;
    let h = line;
    h = h.replace(/("(?:[^"\\]|\\.)*")/g, '<span class="hl-string">$1</span>');
    h = h.replace(/\b(\d+\.?\d*)\b/g, '<span class="hl-number">$1</span>');

    if (mode === 'dsl') {
      // Dynamic DSL keywords from registry + control flow
      if (dslKwPattern) {
        h = h.replace(
          new RegExp(`\\b(${dslKwPattern})\\b`, 'g'),
          '<span class="hl-keyword">$1</span>'
        );
      }
    } else {
      // Pseudocode keywords
      h = h.replace(
        /\b(如果|否则|循环|无限循环|结束|跳出|真|假|click|image|type|press|wait|swipe|screenshot|run)\b/g,
        '<span class="hl-keyword">$1</span>'
      );
      h = h.replace(
        /\b(点击图片|点击|输入|按键|等待图片|等待窗口|等待|滑动|截图|运行|关闭弹窗|检测颜色|等待进度|等待区域|场景判断|检测|调用)\b/g,
        '<span class="hl-module">$1</span>'
      );
    }

    h = h.replace(/(\$[\w.]+)/g, '<span class="hl-annotation">$1</span>');
    return h;
  }).join('\n');
}
