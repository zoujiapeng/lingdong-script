import React, { useMemo, useState, useCallback, useEffect, useRef } from 'react';
import { useStore } from '../store';
import { generatePseudoCode } from '../utils/pseudoGenerator';
import { parsePseudoToFlow } from '../utils/pseudoParser';
import { Copy, Download, Edit3, AlertTriangle, CheckCircle, XCircle } from 'lucide-react';

export function PseudoPanel() {
  const { script } = useStore();
  const [displayCode, setDisplayCode] = useState('');
  const [draftCode, setDraftCode] = useState('');
  const [isEditing, setIsEditing] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [initialized, setInitialized] = useState(false);
  const syncTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hasPendingCanvasUpdate = useRef(false);
  const editRef = useRef<HTMLTextAreaElement>(null);

  // Generate pseudocode from current script
  const generatedCode = useMemo(() => {
    try {
      return generatePseudoCode(script);
    } catch (e: any) {
      return `# 伪代码生成失败: ${e.message}`;
    }
  }, [script]);

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
      if (hasPendingCanvasUpdate.current) {
        hasPendingCanvasUpdate.current = false;
      }
      setDisplayCode(generatedCode);
      setErrorMessage(null);
    }
  }, [generatedCode, initialized, isEditing]);

  // Auto-sync from textarea edits (debounced)
  const handleAutoSync = useCallback((code: string) => {
    if (!code.trim()) return;

    const trimmed = code.trim();
    // Skip if it looks like just comments or empty
    if (trimmed.startsWith('#') || trimmed === '') return;

    try {
      const newScript = parsePseudoToFlow(code);
      newScript.meta = {
        ...newScript.meta,
        id: script.meta.id,
        name: script.meta.name,
        description: script.meta.description,
      };
      // Only update if parse is successful
      useStore.getState().loadScript(newScript);
      setDisplayCode(code);
      setErrorMessage(null);
    } catch (e: any) {
      setErrorMessage(e.message);
    }
  }, [script.meta.id, script.meta.name, script.meta.description]);

  const debouncedSync = useCallback((code: string) => {
    if (syncTimerRef.current) {
      clearTimeout(syncTimerRef.current);
    }
    syncTimerRef.current = setTimeout(() => {
      handleAutoSync(code);
    }, 300);
  }, [handleAutoSync]);

  // Cleanup timer on unmount
  useEffect(() => {
    return () => {
      if (syncTimerRef.current) {
        clearTimeout(syncTimerRef.current);
      }
    };
  }, []);

  const handleStartEditing = useCallback(() => {
    setDraftCode(displayCode);
    setIsEditing(true);
    setErrorMessage(null);
    hasPendingCanvasUpdate.current = false;
  }, [displayCode]);

  const handleCodeChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const newCode = e.target.value;
    setDraftCode(newCode);
    debouncedSync(newCode);
  }, [debouncedSync]);

  const handleCancelEdit = useCallback(() => {
    setIsEditing(false);
    setErrorMessage(null);
    setDraftCode('');
    if (syncTimerRef.current) {
      clearTimeout(syncTimerRef.current);
    }
  }, []);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(displayCode);
  }, [displayCode]);

  const handleDownloadFlow = useCallback(() => {
    const blob = new Blob([displayCode], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${script.meta.name}.flow`;
    a.click();
    URL.revokeObjectURL(url);
  }, [displayCode, script.meta.name]);

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

  // Pseudocode syntax highlighting
  const highlighted = useMemo(() => {
    return highlightPseudo(displayCode);
  }, [displayCode]);

  return (
    <div className="code-panel">
      {/* Toolbar */}
      <div className="code-panel-toolbar">
        <div className="code-panel-toolbar-left">
          <button className="btn btn-ghost" onClick={handleCopy} style={{ gap: 4 }}>
            <Copy size={13} />
            复制
          </button>
          <button className="btn btn-ghost" onClick={handleDownloadFlow} style={{ gap: 4 }}>
            <Download size={13} />
            下载 .flow
          </button>
          <button className="btn btn-ghost" onClick={handleExportJSON} style={{ gap: 4 }}>
            <Download size={13} />
            导出 JSON
          </button>
          {!isEditing ? (
            <button className="btn btn-primary" onClick={handleStartEditing} style={{ gap: 4, fontSize: 12 }}>
              <Edit3 size={13} />
              编辑伪代码
            </button>
          ) : (
            <button className="btn btn-ghost" onClick={handleCancelEdit} style={{ gap: 4 }}>
              <XCircle size={13} />
              取消
            </button>
          )}
        </div>
        <div className="code-panel-toolbar-right">
          <span className="code-panel-status">
            {isEditing ? (
              <span style={{ color: '#818cf6' }}>
                <Edit3 size={12} style={{ verticalAlign: 'middle', marginRight: 4 }} />
                编辑模式
              </span>
            ) : errorMessage ? (
              <span style={{ color: '#ef4444' }}>
                <AlertTriangle size={12} style={{ verticalAlign: 'middle', marginRight: 4 }} />
                同步错误
              </span>
            ) : (
              <span style={{ color: '#34d399' }}>
                <CheckCircle size={12} style={{ verticalAlign: 'middle', marginRight: 4 }} />
                已同步
              </span>
            )}
          </span>
        </div>
      </div>

      {/* Error banner */}
      {errorMessage && isEditing && (
        <div style={{
          background: '#fbbf24', color: '#1a1b26', padding: '8px 16px',
          fontSize: 12, display: 'flex', alignItems: 'center', gap: 8,
        }}>
          <AlertTriangle size={14} />
          <span>解析错误: {errorMessage} — 流程图将保持上个有效状态</span>
        </div>
      )}

      {/* Code area */}
      <div className="code-panel-editor">
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
    </div>
  );
}

// ─── Pseudocode syntax highlighting ───

function highlightPseudo(code: string): string {
  const escaped = code
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

  const lines = escaped.split('\n');
  return lines
    .map((line) => {
      // Comments
      if (/^\s*#/.test(line)) {
        return `<span class="hl-comment">${line}</span>`;
      }

      let h = line;

      // Strings
      h = h.replace(
        /("(?:[^"\\]|\\.)*")/g,
        '<span class="hl-string">$1</span>'
      );

      // Numbers
      h = h.replace(
        /\b(\d+\.?\d*)\b/g,
        '<span class="hl-number">$1</span>'
      );

      // Keywords: if, else, loop, break, and/not/or
      h = h.replace(
        /\b(if|else|loop|infinite|while|break|and|or|not)\b/g,
        '<span class="hl-keyword">$1</span>'
      );

      // Operations: click, type, press, wait, swipe, screenshot, run
      h = h.replace(
        /\b(click|type|press|wait|swipe|screenshot|run)\b/g,
        '<span class="hl-module">$1</span>'
      );

      // Conditions: image_exists, window_exists, var_eq, var_gt, var_lt
      h = h.replace(
        /\b(image_exists|window_exists|var_eq|var_gt|var_lt)\b/g,
        '<span class="hl-annotation">$1</span>'
      );

      // Variables: $name
      h = h.replace(
        /(\$\w+)/g,
        '<span class="hl-annotation">$1</span>'
      );

      return h;
    })
    .join('\n');
}
