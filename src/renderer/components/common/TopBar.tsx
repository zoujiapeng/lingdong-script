import React, { useState, useRef } from 'react';
import {
  Play, Pause, Square, Undo2, Redo2, Save, Upload, Download,
  Zap, LayoutGrid, GitBranch, ChevronDown, Moon, Sun, Settings
} from 'lucide-react';
import { useStore } from '../../store';

export function TopBar() {
  const {
    script, isDirty, execution,
    setScriptName, saveScript, newScript,
    undo, redo,
    startExecution, pauseExecution, resumeExecution, stopExecution,
    ui, setUI, autoLayout, fitToScreen,
  } = useStore();

  const [editingTitle, setEditingTitle] = useState(false);
  const titleRef = useRef<HTMLInputElement>(null);

  const handleTitleClick = () => {
    setEditingTitle(true);
    setTimeout(() => titleRef.current?.select(), 50);
  };

  const handleTitleBlur = () => setEditingTitle(false);

  const handleTitleKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') titleRef.current?.blur();
  };

  const handleLoad = () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.onchange = (e) => {
      const file = (e.target as HTMLInputElement).files?.[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = (ev) => {
        try {
          const script = JSON.parse(ev.target?.result as string);
          useStore.getState().loadScript(script);
        } catch (err) {
          alert('无效的脚本文件格式');
        }
      };
      reader.readAsText(file);
    };
    input.click();
  };

  const statusMap = {
    idle: { label: '就绪', color: '#94a3b8' },
    running: { label: '运行中', color: '#22c55e' },
    paused: { label: '已暂停', color: '#f59e0b' },
    stopped: { label: '已停止', color: '#94a3b8' },
    completed: { label: '已完成', color: '#22c55e' },
    error: { label: '出错', color: '#ef4444' },
  };

  const execStatus = statusMap[execution.status] || statusMap.idle;

  return (
    <div className="topbar">
      {/* Logo */}
      <div className="topbar-logo">
        <div className="topbar-logo-icon">
          <Zap size={16} />
        </div>
        <span className="topbar-logo-text">灵动脚本</span>
        <span className="topbar-logo-badge">PRO</span>
      </div>

      {/* Title */}
      <div className="topbar-title-area">
        <button
          className="btn btn-ghost btn-icon"
          onClick={undo}
          title="撤销 (Ctrl+Z)"
          style={{ width: 30, height: 30 }}
        >
          <Undo2 size={14} />
        </button>
        <button
          className="btn btn-ghost btn-icon"
          onClick={redo}
          title="重做 (Ctrl+Y)"
          style={{ width: 30, height: 30 }}
        >
          <Redo2 size={14} />
        </button>

        <span style={{ width: 1, height: 20, background: 'var(--border-primary)', margin: '0 4px' }} />

        {isDirty && <div className="topbar-dirty-dot" title="未保存" />}
        <input
          ref={titleRef}
          className="topbar-title-input"
          value={script.meta.name}
          onChange={(e) => setScriptName(e.target.value)}
          onFocus={handleTitleClick}
          onBlur={handleTitleBlur}
          onKeyDown={handleTitleKey}
          style={{ width: Math.max(120, script.meta.name.length * 9 + 20) }}
        />
        <button className="btn btn-ghost btn-icon" style={{ width: 28, height: 28 }} onClick={handleTitleClick}>
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
            <path d="M9 1L11 3L3.5 10.5L1 11L1.5 8.5L9 1Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
          </svg>
        </button>
      </div>

      {/* Actions */}
      <div className="topbar-actions">
        {/* Layout */}
        <button className="btn btn-ghost btn-icon" onClick={autoLayout} title="自动布局">
          <LayoutGrid size={15} />
        </button>
        <button className="btn btn-ghost btn-icon" onClick={fitToScreen} title="适应屏幕">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3" />
          </svg>
        </button>

        <span style={{ width: 1, height: 20, background: 'var(--border-primary)' }} />

        {/* File */}
        <button className="btn btn-ghost btn-icon" onClick={handleLoad} title="打开">
          <Upload size={15} />
        </button>
        <button className="btn btn-ghost btn-icon" onClick={saveScript} title="保存 (Ctrl+S)">
          <Download size={15} />
        </button>

        <span style={{ width: 1, height: 20, background: 'var(--border-primary)' }} />

        {/* Execution controls */}
        {execution.status === 'idle' || execution.status === 'stopped' || execution.status === 'completed' || execution.status === 'error' ? (
          <button className="btn btn-success" onClick={startExecution} style={{ gap: 6 }}>
            <Play size={14} fill="white" />
            运行
          </button>
        ) : (
          <>
            {execution.status === 'running' ? (
              <button className="btn btn-ghost" onClick={pauseExecution} style={{ color: '#f59e0b', borderColor: 'rgba(245,158,11,0.3)' }}>
                <Pause size={14} />
                暂停
              </button>
            ) : (
              <button className="btn btn-ghost" onClick={resumeExecution} style={{ color: '#22c55e', borderColor: 'rgba(34,197,94,0.3)' }}>
                <Play size={14} />
                继续
              </button>
            )}
            <button className="btn btn-danger" onClick={stopExecution}>
              <Square size={12} fill="white" />
              停止
            </button>
          </>
        )}

        <button
          className="btn btn-primary"
          onClick={() => {}}
          style={{ gap: 6 }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="18" cy="18" r="3" />
            <circle cx="6" cy="6" r="3" />
            <path d="M13 6h3a2 2 0 0 1 2 2v7" />
            <path d="M11 18H8a2 2 0 0 1-2-2V9" />
          </svg>
          发布
        </button>

        <span style={{ width: 1, height: 20, background: 'var(--border-primary)' }} />

        <button
          className="btn btn-ghost btn-icon"
          onClick={() => setUI({ dark_mode: !ui.dark_mode })}
          title="切换主题"
        >
          {ui.dark_mode ? <Sun size={15} /> : <Moon size={15} />}
        </button>
      </div>
    </div>
  );
}
