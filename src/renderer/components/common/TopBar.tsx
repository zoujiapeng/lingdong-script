import React, { useState, useRef, useEffect } from 'react';
import {
  Play, Pause, Square, Undo2, Redo2, Save, Upload, Download,
  Zap, LayoutGrid, GitBranch, ChevronDown, Moon, Sun, Settings, FileText, Plus, Circle
} from 'lucide-react';
import { useStore, loadCustomTemplates, saveCustomTemplates } from '../../store';
import { MacroPanel } from './MacroPanel';

// Expose preset templates list for the dropdown
const PRESET_LIST = [
  { id: 'desktop-monitor', name: '桌面图标监控', desc: '循环检测桌面目标图片' },
  { id: 'login-automation', name: '登录自动化', desc: '自动输入账号密码登录' },
  { id: 'form-filler', name: '表单填写器', desc: '循环填写表单数据' },
  { id: 'app-launcher', name: '应用启动器', desc: '启动应用并执行操作' },
  { id: 'screenshot-logger', name: '定时截图记录', desc: '每隔30秒自动截图' },
  { id: 'filedialog-save', name: '保存文件对话框', desc: 'Ctrl+S → UIA自动填写路径保存' },
  { id: 'filedialog-open', name: '打开文件对话框', desc: 'Ctrl+O → UIA自动填写路径打开' },
  // 复杂自动化模板
  { id: 'complex-image-pipeline', name: '多目标智能检测流水线', desc: '多目标图片检测 + OCR验证 + 失败重试' },
  { id: 'complex-web-scraper', name: '网页数据采集与监控', desc: '自动滚动→截图→翻页→循环采集多页' },
  { id: 'complex-form-submit', name: '智能表单提交流程', desc: '多字段表单→OCR验证→颜色检测→重试' },
  { id: 'complex-batch-process', name: '批量文件处理监控', desc: '批量循环→弹窗关闭→进度等待→截图' },
];

export const TopBar = React.memo(function TopBar() {
  const scriptName = useStore(s => s.script.meta.name);
  const isDirty = useStore(s => s.isDirty);
  const executionStatus = useStore(s => s.execution.status);
  const darkMode = useStore(s => s.ui.dark_mode);
  const recordingActive = useStore(s => s.recording.active);
  const setScriptName = useStore(s => s.setScriptName);
  const saveScript = useStore(s => s.saveScript);
  const loadScript = useStore(s => s.loadScript);
  const loadTemplate = useStore(s => s.loadTemplate);
  const saveAsTemplate = useStore(s => s.saveAsTemplate);
  const undo = useStore(s => s.undo);
  const redo = useStore(s => s.redo);
  const startExecution = useStore(s => s.startExecution);
  const pauseExecution = useStore(s => s.pauseExecution);
  const resumeExecution = useStore(s => s.resumeExecution);
  const stopExecution = useStore(s => s.stopExecution);
  const setUI = useStore(s => s.setUI);
  const autoLayout = useStore(s => s.autoLayout);
  const fitToScreen = useStore(s => s.fitToScreen);
  const newScript = useStore(s => s.newScript);
  const stopRecording = useStore(s => s.stopRecording);

  const [editingTitle, setEditingTitle] = useState(false);
  const [showTemplates, setShowTemplates] = useState(false);
  const [savingName, setSavingName] = useState(false);
  const [templateName, setTemplateName] = useState('');
  const [showMacroPanel, setShowMacroPanel] = useState(false);
  const titleRef = useRef<HTMLInputElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // Handle native menu actions (Ctrl+N/O/S from menu bar)
  useEffect(() => {
    const api = (window as any).electronAPI;
    if (!api?.onMenuAction) return;
    const cleanup = api.onMenuAction((action: string) => {
      switch (action) {
        case 'new': newScript(); break;
        case 'open': handleLoad(); break;
        case 'save': saveScript(); break;
      }
    });
    return cleanup;
  }, []);

  // Listen for macro status messages (F6 toggle from external process)
  useEffect(() => {
    const api = (window as any).electronAPI;
    if (!api?.onMacroMessage) return;
    const cleanup = api.onMacroMessage((msg: any) => {
      if (msg.type === 'status') {
        // Update recording state and refresh list when stopped
        useStore.setState({ recording: { active: msg.recording, macroName: msg.recording ? '录制中...' : '' } });
        if (!msg.recording) {
          // Recording stopped via F6 — refresh list
          setTimeout(() => useStore.getState().listMacros(), 200);
        }
      }
    });
    // Load macro list on mount
    useStore.getState().listMacros();
    return cleanup;
  }, []);

  const handleTitleClick = () => {
    setEditingTitle(true);
    setTimeout(() => titleRef.current?.select(), 50);
  };

  const handleTitleBlur = () => setEditingTitle(false);

  const handleTitleKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') titleRef.current?.blur();
  };

  const handleLoad = async () => {
    const api = (window as any).electronAPI;
    if (api?.openFileDialog) {
      const result = await api.openFileDialog();
      if (!result.canceled && result.filePaths?.[0]) {
        try {
          const resp = await api.readFile(result.filePaths[0]);
          if (resp.content) {
            loadScript(JSON.parse(resp.content));
            return;
          }
          if (resp.error) throw new Error(resp.error);
        } catch (e) {
          console.error('Load failed:', e);
        }
      }
    }
    // Browser fallback
    fileRef.current?.click();
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      try {
        const script = JSON.parse(ev.target?.result as string);
        loadScript(script);
      } catch { alert('无效的脚本文件格式'); }
    };
    reader.readAsText(file);
    e.target.value = '';
  };

  const handleNewFromTemplate = (templateId: string) => {
    loadTemplate(templateId);
    setShowTemplates(false);
  };

  const handleCustomTemplate = (tpl: { id: string; name: string; description: string; script: any }) => {
    loadScript(tpl.script);
    setShowTemplates(false);
  };

  const handleSaveAsTemplate = () => {
    const name = templateName.trim();
    if (!name) return;
    saveAsTemplate(name);
    setTemplateName('');
    setSavingName(false);
    setShowTemplates(false);
  };

  const handleDeleteCustomTemplate = (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    const templates = loadCustomTemplates().filter((t) => t.id !== id);
    saveCustomTemplates(templates);
    setShowTemplates(false);
    // Force re-render by toggling state
    setTimeout(() => setShowTemplates(true), 50);
  };

  const statusMap = {
    idle: { label: '就绪', color: '#94a3b8' },
    running: { label: '运行中', color: '#22c55e' },
    paused: { label: '已暂停', color: '#f59e0b' },
    stopped: { label: '已停止', color: '#94a3b8' },
    completed: { label: '已完成', color: '#22c55e' },
    error: { label: '出错', color: '#ef4444' },
  };

  const execStatus = statusMap[executionStatus] || statusMap.idle;

  return (
    <div className="topbar">
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
          value={scriptName}
          onChange={(e) => setScriptName(e.target.value)}
          onFocus={handleTitleClick}
          onBlur={handleTitleBlur}
          onKeyDown={handleTitleKey}
          style={{ width: Math.max(120, scriptName.length * 9 + 20) }}
        />
        <button className="btn btn-ghost btn-icon" style={{ width: 28, height: 28 }} onClick={handleTitleClick}>
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
            <path d="M9 1L11 3L3.5 10.5L1 11L1.5 8.5L9 1Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
          </svg>
        </button>
      </div>

      {/* Actions */}
      <div className="topbar-actions">
        {/* Templates */}
        <div className="topbar-template-dropdown">
          <button className="btn btn-ghost" onClick={() => setShowTemplates(!showTemplates)} style={{ gap: 4, position: 'relative' }}>
            <FileText size={14} />
            模板
            <ChevronDown size={12} />
          </button>
          {showTemplates && (
            <div className="dropdown-menu" style={{ maxHeight: 400, overflowY: 'auto' }}>
              <div style={{ padding: '4px 10px', fontSize: 11, color: 'var(--text-muted)', fontWeight: 600 }}>预设模板</div>
              {PRESET_LIST.map((tpl) => (
                <button key={tpl.id} className="dropdown-item" onClick={() => handleNewFromTemplate(tpl.id)}>
                  <div className="dropdown-item-title">{tpl.name}</div>
                  <div className="dropdown-item-desc">{tpl.desc}</div>
                </button>
              ))}

              {/* Custom templates */}
              {(() => {
                const customTpls = loadCustomTemplates();
                if (customTpls.length === 0) return null;
                return (
                  <>
                    <div style={{ height: 1, background: 'var(--border-primary)', margin: '4px 0' }} />
                    <div style={{ padding: '4px 10px', fontSize: 11, color: 'var(--text-muted)', fontWeight: 600 }}>我的模板</div>
                    {customTpls.map((tpl) => (
                      <button key={tpl.id} className="dropdown-item" onClick={() => handleCustomTemplate(tpl)} style={{ paddingRight: 28 }}>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}>
                          <div>
                            <div className="dropdown-item-title">{tpl.name}</div>
                            <div className="dropdown-item-desc">{tpl.description}</div>
                          </div>
                          <span
                            onClick={(e) => handleDeleteCustomTemplate(e, tpl.id)}
                            style={{
                              color: 'var(--text-muted)',
                              cursor: 'pointer',
                              padding: '2px 4px',
                              borderRadius: 4,
                              fontSize: 14,
                              lineHeight: 1,
                            }}
                            title="删除模板"
                          >
                            ×
                          </span>
                        </div>
                      </button>
                    ))}
                  </>
                );
              })()}

              {/* Save as template */}
              <div style={{ height: 1, background: 'var(--border-primary)', margin: '4px 0' }} />
              {savingName ? (
                <div style={{ padding: '6px 10px', display: 'flex', gap: 6, alignItems: 'center' }}>
                  <input
                    className="topbar-title-input"
                    value={templateName}
                    onChange={(e) => setTemplateName(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') handleSaveAsTemplate(); }}
                    placeholder="模板名称..."
                    autoFocus
                    style={{ flex: 1, fontSize: 12, padding: '4px 8px' }}
                  />
                  <button className="btn btn-primary" style={{ padding: '3px 10px', fontSize: 12 }} onClick={handleSaveAsTemplate}>
                    保存
                  </button>
                  <button className="btn btn-ghost" style={{ padding: '3px 8px', fontSize: 12 }} onClick={() => { setSavingName(false); setTemplateName(''); }}>
                    取消
                  </button>
                </div>
              ) : (
                <button className="dropdown-item" onClick={() => setSavingName(true)} style={{ color: 'var(--accent)' }}>
                  <Plus size={14} />
                  保存当前为模板
                </button>
              )}
            </div>
          )}
        </div>

        {/* Layout */}
        <button className="btn btn-ghost btn-icon" onClick={() => autoLayout()} title="自动布局">
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
        <input ref={fileRef} type="file" accept=".json" style={{ display: 'none' }} onChange={handleFileChange} />
        <button className="btn btn-ghost btn-icon" onClick={saveScript} title="保存 (Ctrl+S)">
          <Download size={15} />
        </button>

        <span style={{ width: 1, height: 20, background: 'var(--border-primary)' }} />

        {/* Execution controls */}
        {executionStatus === 'idle' || executionStatus === 'stopped' || executionStatus === 'completed' || executionStatus === 'error' ? (
          <button className="btn btn-success" onClick={startExecution} style={{ gap: 6 }}>
            <Play size={14} fill="white" />
            运行
          </button>
        ) : (
          <>
            {executionStatus === 'running' ? (
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

        <span style={{ width: 1, height: 20, background: 'var(--border-primary)' }} />

        {/* Macro Recording Button */}
        <button
          className={`btn btn-ghost btn-icon ${recordingActive ? 'recording-active' : ''}`}
          onClick={() => {
            setShowMacroPanel(!showMacroPanel);
            if (recordingActive) {
              stopRecording();
            }
          }}
          title={recordingActive ? '停止录制 (F6)' : '宏录制'}
          style={{ position: 'relative' }}
        >
          <Circle size={14} style={{ color: recordingActive ? '#ef4444' : 'var(--text-muted)' }} />
          {recordingActive && <span className="recording-dot-pulse" />}
        </button>
        <button
          className="btn btn-ghost btn-icon"
          onClick={() => setUI({ dark_mode: !darkMode })}
          title={darkMode ? '亮色模式' : '深色模式'}
        >
          {darkMode ? <Sun size={15} /> : <Moon size={15} />}
        </button>
      </div>

      {/* Macro Panel */}
      {(showMacroPanel || recordingActive) && <MacroPanel />}
    </div>
  );
});
