import React, { useMemo, useState, useCallback, useEffect, useRef } from 'react';
import { useStore } from '../../store';
import { generateDSL } from '../../utils/dslGenerator';
import { Copy, Download, Edit3, AlertTriangle, CheckCircle, RefreshCw } from 'lucide-react';

export function DSLPanel() {
  const { script } = useStore();
  const [displayCode, setDisplayCode] = useState('');
  const [draftCode, setDraftCode] = useState('');
  const [isEditing, setIsEditing] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [initialized, setInitialized] = useState(false);
  const [syncMode, setSyncMode] = useState<'auto' | 'manual'>('auto');
  const editRef = useRef<HTMLTextAreaElement>(null);

  // Generate DSL from current script
  const generatedCode = useMemo(() => {
    try {
      return generateDSL(script);
    } catch (e: any) {
      return `# DSL 生成失败: ${e.message}`;
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
    if (initialized && !isEditing && syncMode === 'auto') {
      setDisplayCode(generatedCode);
      setErrorMessage(null);
    }
  }, [generatedCode, initialized, isEditing, syncMode]);

  // Start editing
  const handleEdit = useCallback(() => {
    setDraftCode(displayCode);
    setIsEditing(true);
    setErrorMessage(null);
    setTimeout(() => {
      editRef.current?.focus();
      editRef.current?.select();
    }, 50);
  }, [displayCode]);

  // Cancel editing (revert)
  const handleCancel = useCallback(() => {
    setDraftCode('');
    setIsEditing(false);
    setErrorMessage(null);
  }, []);

  // Sync DSL edits back to canvas
  const handleApply = useCallback(async () => {
    if (!draftCode.trim()) return;
    setIsEditing(false);

    const api = (window as any).electronAPI;
    if (api?.dslToFlow) {
      try {
        const result = await api.dslToFlow(draftCode);
        if (result.error) {
          setErrorMessage('DSL 解析失败: ' + result.error);
          return;
        }
        const flowData = result.data || result;
        if (flowData.nodes && flowData.nodes.length > 0) {
          useStore.getState().loadScript(flowData);
          setDisplayCode(draftCode);
          setErrorMessage(null);
          return;
        }
      } catch (e: any) {
        setErrorMessage('DSL 同步失败: ' + (e.message || '未知错误'));
        return;
      }
    }
    setErrorMessage('DSL→流程图 同步需要 Electron IPC (dslToFlow)');
  }, [draftCode]);

  // Copy to clipboard
  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(displayCode).catch(() => {});
  }, [displayCode]);

  // Download
  const handleDownload = useCallback(() => {
    const blob = new Blob([displayCode], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${script.meta.name}.dsl`;
    a.click();
    URL.revokeObjectURL(url);
  }, [displayCode, script.meta.name]);

  // Refresh / regenerate
  const handleRefresh = useCallback(() => {
    setDisplayCode(generatedCode);
    setErrorMessage(null);
  }, [generatedCode]);

  return (
    <div className="code-panel" style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Toolbar */}
      <div className="code-panel-toolbar">
        <span style={{ fontWeight: 600, fontSize: 13, color: 'var(--text-primary)' }}>
          DSL 视图
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          {isEditing ? (
            <>
              <button className="btn btn-ghost" onClick={handleCancel} style={{ fontSize: 12 }}>
                取消
              </button>
              <button className="btn btn-primary" onClick={handleApply} style={{ fontSize: 12 }}>
                应用到画布
              </button>
            </>
          ) : (
            <>
              <span style={{ fontSize: 11, color: 'var(--text-muted)', marginRight: 4 }}>
                {syncMode === 'auto' ? '自动同步' : '手动'}
              </span>
              <button
                className="btn btn-ghost btn-icon"
                onClick={() => setSyncMode(m => m === 'auto' ? 'manual' : 'auto')}
                title={syncMode === 'auto' ? '切换到手动模式' : '切换到自动模式'}
                style={{ opacity: syncMode === 'auto' ? 1 : 0.5 }}
              >
                <RefreshCw size={13} />
              </button>
              <button className="btn btn-ghost btn-icon" onClick={handleEdit} title="编辑 DSL">
                <Edit3 size={13} />
              </button>
              <button className="btn btn-ghost btn-icon" onClick={handleRefresh} title="重新生成">
                <RefreshCw size={13} />
              </button>
              <button className="btn btn-ghost btn-icon" onClick={handleCopy} title="复制">
                <Copy size={13} />
              </button>
              <button className="btn btn-ghost btn-icon" onClick={handleDownload} title="下载">
                <Download size={13} />
              </button>
            </>
          )}
        </div>
      </div>

      {/* DSL text area */}
      <div style={{ flex: 1, overflow: 'auto', padding: 0, position: 'relative' }}>
        {isEditing ? (
          <textarea
            ref={editRef}
            value={draftCode}
            onChange={(e) => setDraftCode(e.target.value)}
            style={{
              width: '100%',
              height: '100%',
              border: 'none',
              outline: 'none',
              resize: 'none',
              padding: 16,
              fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
              fontSize: 13,
              lineHeight: 1.6,
              background: 'var(--bg-primary)',
              color: 'var(--text-primary)',
              tabSize: 2,
            }}
            spellCheck={false}
          />
        ) : (
          <pre
            style={{
              margin: 0,
              padding: 16,
              fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
              fontSize: 13,
              lineHeight: 1.6,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              color: 'var(--text-primary)',
              minHeight: '100%',
            }}
          >
            {displayCode}
          </pre>
        )}
      </div>

      {/* Error message */}
      {errorMessage && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px', fontSize: 12, color: '#ef4444', background: 'rgba(239,68,68,0.06)', borderTop: '1px solid rgba(239,68,68,0.2)' }}>
          <AlertTriangle size={14} />
          {errorMessage}
        </div>
      )}

      {/* Status bar */}
      <div className="code-panel-status" style={{ padding: '6px 12px', borderTop: '1px solid var(--border-primary)', justifyContent: 'space-between', display: 'flex' }}>
        <span>行数: {displayCode.split('\n').length}</span>
        <span>节点数: {script.nodes.length}</span>
        <span>边数: {script.edges.length}</span>
      </div>
    </div>
  );
}
