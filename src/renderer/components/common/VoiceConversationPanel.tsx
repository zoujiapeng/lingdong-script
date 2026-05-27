import React, { useState, useEffect, useRef, useCallback } from 'react';
import { VoiceConversation, VoiceState, VoiceMessage, STTBackend } from '../../utils/voiceConversation';

interface Props {
  visible: boolean;
  onClose: () => void;
  minimized?: boolean;
  onToggleMinimize?: () => void;
}

const STT_ENGINES = [
  { value: 'whisper-tiny', label: 'Whisper Tiny' },
  { value: 'whisper-base', label: 'Whisper Base' },
  { value: 'whisper-small', label: 'Whisper Small' },
  { value: 'whisper-large-v3-turbo', label: 'Whisper Large V3 Turbo' },
  { value: 'paraformer', label: 'Paraformer' },
];

const ENGINE_LABELS: Record<string, string> = {};
for (const e of STT_ENGINES) ENGINE_LABELS[e.value] = e.label;

function getStoredEngine(): string {
  try { return localStorage.getItem('lingdong-stt-engine') || 'whisper-small'; } catch { return 'whisper-small'; }
}
function storeEngine(engine: string) {
  try { localStorage.setItem('lingdong-stt-engine', engine); } catch {}
}

export function VoiceConversationPanel({ visible, onClose, minimized, onToggleMinimize }: Props) {
  const [voiceState, setVoiceState] = useState<VoiceState>('idle');
  const [messages, setMessages] = useState<VoiceMessage[]>([]);
  const [continuous, setContinuous] = useState(true);
  const [ttsEnabled, setTtsEnabled] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [backend, setBackend] = useState<STTBackend>('none');
  const [wsConnected, setWsConnected] = useState(false);
  const [sttEngine, setSttEngine] = useState(() => getStoredEngine());
  const convRef = useRef<VoiceConversation | null>(null);
  const msgEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    msgEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    if (!visible) {
      convRef.current?.destroy();
      convRef.current = null;
      return;
    }

    const conv = new VoiceConversation({
      onStateChange: (s) => setVoiceState(s),
      onTranscript: (text) => {
        setMessages(prev => [...prev, {
          id: `u${Date.now()}`,
          role: 'user',
          text,
          timestamp: Date.now(),
        }]);
      },
      onResponse: (text) => {
        setMessages(prev => [...prev, {
          id: `a${Date.now()}`,
          role: 'assistant',
          text,
          timestamp: Date.now(),
        }]);
      },
      onError: (msg) => setError(msg),
      onBackendChange: (b, connected) => {
        setBackend(b);
        setWsConnected(connected);
      },
    });
    convRef.current = conv;
    conv.startListening();

    return () => {
      conv.destroy();
    };
  }, [visible]);

  const toggleMic = useCallback(() => {
    const conv = convRef.current;
    if (!conv) return;
    if (conv.getState() === 'listening') {
      conv.stopListening();
    } else {
      setError(null);
      conv.startListening();
    }
  }, []);

  const toggleContinuous = useCallback(() => {
    const conv = convRef.current;
    if (!conv) return;
    const next = !conv.isContinuous();
    conv.setContinuousMode(next);
    setContinuous(next);
  }, []);

  const toggleTts = useCallback(() => {
    const conv = convRef.current;
    if (!conv) return;
    const next = !conv.isTtsEnabled();
    conv.setTtsEnabled(next);
    setTtsEnabled(next);
  }, []);

  const switchEngine = useCallback((engine: string) => {
    storeEngine(engine);
    setSttEngine(engine);
    (window as any).__STT_ENGINE__ = engine;
    window.dispatchEvent(new CustomEvent('stt-engine-changed'));
    setMessages(prev => [...prev, {
      id: `sys${Date.now()}`,
      role: 'assistant',
      text: `🔄 引擎切换至 ${ENGINE_LABELS[engine] || engine}`,
      timestamp: Date.now(),
    }]);
  }, []);

  const clearChat = useCallback(() => {
    convRef.current?.clearHistory();
    setMessages([]);
    setError(null);
  }, []);

  if (!visible) return null;

  const statusLabels: Record<VoiceState, string> = {
    idle: '就绪',
    listening: '聆听中…',
    thinking: '思考中…',
    speaking: '回复中…',
  };

  return (
    <div className={`voice-panel ${minimized ? 'voice-panel-minimized' : ''}`}>
      {/* Header */}
      <div className="voice-panel-header">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
          <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
          <line x1="12" y1="19" x2="12" y2="23"/>
          <line x1="8" y1="23" x2="16" y2="23"/>
        </svg>
        <span>语音对话</span>
        <span className={`voice-status-indicator ${voiceState}`} title={`STT: ${backend}`}>
          {statusLabels[voiceState]}
        </span>
        <div className="voice-panel-header-actions">
          {onToggleMinimize && (
            <button className="btn btn-ghost btn-icon" onClick={onToggleMinimize} title={minimized ? '展开' : '折叠'}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                {minimized
                  ? <><polyline points="9 18 15 12 9 6"/></>
                  : <><polyline points="15 18 9 12 15 6"/></>
                }
              </svg>
            </button>
          )}
          <button className="btn btn-ghost btn-icon" onClick={onClose} title="关闭">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </div>
      </div>

      {!minimized && (
        <>
          {/* Messages */}
          <div className="voice-panel-messages">
            {messages.length === 0 && (
              <div className="voice-panel-empty">
                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="var(--text-muted)" strokeWidth="1.5">
                  <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
                  <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
                </svg>
                <p>点击麦克风开始语音对话</p>
                <p className="voice-panel-empty-hint">支持连续对话，AI 回复后自动开始聆听</p>
              </div>
            )}
            {messages.map((msg) => (
              <div key={msg.id} className={`voice-msg voice-msg-${msg.role}`}>
                <div className="voice-msg-avatar">
                  {msg.role === 'user' ? (
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
                      <circle cx="12" cy="7" r="4"/>
                    </svg>
                  ) : (
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                    </svg>
                  )}
                </div>
                <div className="voice-msg-content">
                  <div className="voice-msg-text">{msg.text}</div>
                  <div className="voice-msg-time">
                    {new Date(msg.timestamp).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}
                  </div>
                </div>
              </div>
            ))}
            {voiceState === 'listening' && (
              <div className="voice-msg voice-msg-user voice-msg-pending">
                <div className="voice-msg-avatar">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
                    <circle cx="12" cy="7" r="4"/>
                  </svg>
                </div>
                <div className="voice-msg-content">
                  <div className="voice-listening-animation">
                    <span className="voice-listening-dot" />
                    <span className="voice-listening-dot" />
                    <span className="voice-listening-dot" />
                  </div>
                </div>
              </div>
            )}
            {error && (
              <div className="voice-msg voice-msg-error">
                <div className="voice-msg-content">
                  <span style={{ color: 'var(--error)', fontSize: 12 }}>{error}</span>
                </div>
              </div>
            )}
            <div ref={msgEndRef} />
          </div>

          {/* Controls */}
          <div className="voice-panel-controls">
            <button
              className={`voice-mic-btn ${voiceState === 'listening' ? 'voice-mic-active' : ''} ${voiceState === 'speaking' ? 'voice-mic-speaking' : ''}`}
              onClick={toggleMic}
              disabled={voiceState === 'thinking' || voiceState === 'speaking'}
              title={voiceState === 'listening' ? '停止聆听' : '开始聆听'}
            >
              {voiceState === 'thinking' ? (
                <span className="voice-spinner" />
              ) : (
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
                  <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
                </svg>
              )}
            </button>
            <div className="voice-control-buttons">
              <button
                className={`btn btn-ghost btn-icon ${ttsEnabled ? 'voice-btn-active' : ''}`}
                onClick={toggleTts}
                title={ttsEnabled ? '关闭语音朗读' : '开启语音朗读'}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  {ttsEnabled ? (
                    <>
                      <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
                      <path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/>
                    </>
                  ) : (
                    <>
                      <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
                      <line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/>
                    </>
                  )}
                </svg>
              </button>
              <button
                className={`btn btn-ghost btn-icon ${continuous ? 'voice-btn-active' : ''}`}
                onClick={toggleContinuous}
                title={continuous ? '关闭连续对话' : '开启连续对话'}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
                  <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
                  <line x1="12" y1="19" x2="12" y2="23"/>
                  {continuous && <><path d="M8 2a4 4 0 0 1 8 0"/><path d="M8 21h8"/></>}
                </svg>
              </button>
              <button
                className="btn btn-ghost btn-icon"
                onClick={clearChat}
                disabled={messages.length === 0}
                title="清除对话"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <polyline points="3 6 5 6 21 6"/>
                  <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                </svg>
              </button>
            </div>
          </div>
          {/* Engine selector + Status */}
          <div className="voice-panel-statusbar">
            <span className={`voice-status-dot ${wsConnected ? 'status-connected' : backend === 'browser' ? 'status-warn' : 'status-disconnected'}`} />
            {wsConnected ? (
              <select
                className="voice-engine-select"
                value={sttEngine}
                onChange={(e) => switchEngine(e.target.value)}
                title="切换语音识别引擎"
              >
                {STT_ENGINES.map((eng) => (
                  <option key={eng.value} value={eng.value}>{eng.label}</option>
                ))}
              </select>
            ) : (
              <span className="voice-status-text">
                {backend === 'browser' ? '浏览器 STT' : '未连接'}
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}
