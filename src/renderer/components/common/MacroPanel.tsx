import React, { useEffect, useState } from 'react';
import { useStore } from '../../store';
import { Play, Trash2, X, RotateCcw, Eye, Loader2, CheckCircle } from 'lucide-react';

export function MacroPanel() {
  const {
    recording, macroList,
    listMacros, playMacro, deleteMacro, convertMacroToFlow,
    startRecording, stopRecording,
  } = useStore();

  // Visual macro
  const [visualRecording, setVisualRecording] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [extractMsg, setExtractMsg] = useState('');

  useEffect(() => {
    useStore.getState().listMacros();
    // Refresh list when recording state changes
    const unsub = useStore.subscribe((state, prevState) => {
      if (prevState.recording.active && !state.recording.active) {
        setTimeout(() => useStore.getState().listMacros(), 300);
      }
    });
    return () => unsub();
  }, []);

  return (
    <div className="macro-panel" style={{
      position: 'fixed',
      right: 0,
      top: 70,
      width: 300,
      maxHeight: 'calc(100vh - 140px)',
      background: 'var(--bg-primary)',
      border: '1px solid var(--border-primary)',
      borderRadius: '8px 0 0 8px',
      boxShadow: '-4px 0 20px rgba(0,0,0,0.15)',
      zIndex: 1000,
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden',
    }}>
      <div style={{
        padding: '12px 16px',
        borderBottom: '1px solid var(--border-primary)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        fontWeight: 600,
        fontSize: 13,
      }}>
        <span>宏录制</span>
        {recording.active && (
          <span style={{ color: '#ef4444', fontSize: 11, display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{
              width: 8, height: 8, borderRadius: '50%', background: '#ef4444',
              animation: 'pulse 1s infinite',
            }} />
            录制中...
          </span>
        )}
      </div>

      {/* Record controls */}
      <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-primary)' }}>
        {recording.active ? (
          <button className="btn btn-danger" onClick={stopRecording} style={{ width: '100%', gap: 6 }}>
            <RotateCcw size={14} />
            停止录制 (F6)
          </button>
        ) : (
          <button className="btn btn-success" onClick={() => startRecording()} style={{ width: '100%', gap: 6 }}>
            <span style={{ width: 10, height: 10, borderRadius: '50%', background: '#fff' }} />
            开始录制 (F6)
          </button>
        )}

        {/* Visual macro toggle */}
        <div style={{ marginTop: 8, display: 'flex', gap: 4, alignItems: 'center' }}>
          <button className={`btn btn-ghost ${visualRecording ? 'active' : ''}`}
            onClick={async () => {
              if (visualRecording) {
                await (window as any).electronAPI?.callFeature('macro_stop_visual_recording', {});
                setVisualRecording(false);
                setTimeout(() => listMacros?.(), 300);
              } else {
                await (window as any).electronAPI?.callFeature('macro_start_visual_recording', {});
                setVisualRecording(true);
              }
            }}
            style={{ flex: 1, padding: '4px 8px', fontSize: 11, gap: 4,
              borderColor: visualRecording ? 'var(--accent)' : undefined }}>
            <Eye size={12} />
            {visualRecording ? '停止视觉' : '视觉录制'}
          </button>
          <button className="btn btn-ghost"
            onClick={async () => {
              setExtracting(true); setExtractMsg('');
              try {
                const r = await (window as any).electronAPI?.callFeature('macro_extract_visual_templates', {});
                setExtractMsg(r?.success || r?.ok ? '提取完成' : '无新模板');
              } catch { setExtractMsg('失败'); }
              setExtracting(false);
            }}
            disabled={extracting}
            style={{ padding: '4px 8px', fontSize: 11, gap: 4 }}>
            {extracting ? <Loader2 size={11} className="spin" /> : <CheckCircle size={12} />}
            提取模板
          </button>
        </div>
        {extractMsg && (
          <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 4, textAlign: 'center' }}>
            {extractMsg}
          </div>
        )}
      </div>

      {/* Macro list */}
      <div style={{ flex: 1, overflowY: 'auto', padding: 8 }}>
        {macroList.length === 0 ? (
          <div style={{
            color: 'rgba(148,163,184,0.4)',
            fontSize: 11,
            textAlign: 'center',
            padding: 20,
          }}>
            暂无宏录制记录
          </div>
        ) : (
          macroList.map((macro) => (
            <div
              key={macro.file}
              style={{
                padding: '10px 12px',
                marginBottom: 6,
                borderRadius: 6,
                background: 'var(--bg-secondary)',
                border: '1px solid var(--border-primary)',
              }}
            >
              <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>{macro.name}</div>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 8 }}>
                {macro.events} 个事件 · {macro.duration} 秒
              </div>
              <div style={{ display: 'flex', gap: 4 }}>
                <button
                  className="btn btn-ghost"
                  style={{ padding: '3px 10px', fontSize: 11, flex: 1 }}
                  onClick={() => playMacro(macro.name)}
                  title="播放宏"
                >
                  <Play size={11} style={{ marginRight: 3 }} />
                  播放
                </button>
                <button
                  className="btn btn-ghost"
                  style={{ padding: '3px 10px', fontSize: 11, flex: 1, color: 'var(--accent)' }}
                  onClick={() => convertMacroToFlow(macro.file)}
                  title="转换为流程图"
                >
                  <RotateCcw size={11} style={{ marginRight: 3 }} />
                  转流程
                </button>
                <button
                  className="btn btn-ghost"
                  style={{ padding: '3px 8px', fontSize: 11, color: 'var(--text-muted)' }}
                  onClick={() => deleteMacro(macro.name)}
                  title="删除"
                >
                  <Trash2 size={11} />
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
