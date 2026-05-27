// Web Speech API types
declare var SpeechRecognition: {
  new (): SpeechRecognition;
  prototype: SpeechRecognition;
};
declare var webkitSpeechRecognition: {
  new (): SpeechRecognition;
  prototype: SpeechRecognition;
};
interface SpeechRecognition extends EventTarget {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onstart: (() => void) | null;
  onerror: ((event: any) => void) | null;
  onend: (() => void) | null;
  onresult: ((event: any) => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}

export type VoiceState = 'idle' | 'listening' | 'thinking' | 'speaking';

export interface VoiceMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  timestamp: number;
}

export interface VoiceConversationCallbacks {
  onStateChange: (state: VoiceState) => void;
  onTranscript: (text: string) => void;
  onResponse: (text: string) => void;
  onError: (msg: string) => void;
  onBackendChange?: (backend: STTBackend, wsConnected: boolean) => void;
}

export type STTBackend = 'whisper' | 'browser' | 'none';

function getApiKey(): string {
  return (
    (window as any).__DEEPSEEK_KEY__ ||
    localStorage.getItem('lingdong-deepseek-key') ||
    ''
  );
}

function getElevenlabsKey(): string {
  try { return localStorage.getItem('lingdong-elevenlabs-key') || ''; } catch { return ''; }
}
function getElevenlabsVoiceId(): string {
  try { return localStorage.getItem('lingdong-elevenlabs-voice') || ''; } catch { return ''; }
}

function getSttEngine(): string {
  try { return localStorage.getItem('lingdong-stt-engine') || 'whisper-small'; } catch { return 'whisper-small'; }
}

function engineLabel(engine: string): string {
  const labels: Record<string, string> = {
    'whisper-tiny': 'Whisper Tiny',
    'whisper-base': 'Whisper Base',
    'whisper-small': 'Whisper Small',
    'whisper-large-v3-turbo': 'Whisper Large V3 Turbo',
    'faster-whisper': 'Faster Whisper',
    'paraformer': 'Paraformer',
  };
  return labels[engine] || engine;
}

function getApiBase(): string {
  return (
    (window as any).__LLM_API_BASE__ ||
    localStorage.getItem('lingdong-llm-base') ||
    ''
  ).replace(/\/+$/, '');
}

function getModel(): string {
  return (
    (window as any).__LLM_MODEL__ ||
    localStorage.getItem('lingdong-llm-model') ||
    ''
  );
}

const MAX_RECONNECT_ATTEMPTS = 3;
const RECONNECT_DELAY_MS = 1000;

export class VoiceConversation {
  private recognition: SpeechRecognition | null = null;
  private state: VoiceState = 'idle';
  private callbacks: VoiceConversationCallbacks;
  private continuousMode = true;
  private ttsEnabled = true;

  private ws: WebSocket | null = null;
  private wsUrl: string | null = null;
  private reconnectAttempts = 0;
  private audioCtx: AudioContext | null = null;
  private audioQueue: ArrayBuffer[] = [];
  private isPlaying = false;
  private ttsFinished = false;
  private currentSource: AudioBufferSourceNode | null = null;
  private wsConnected = false;
  private pendingReconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private scheduledUntil = 0; // AudioContext time when current scheduled audio ends
  private backend: STTBackend = 'none';
  private wsConnecting = false;
  private starting = false;         // mutex guard for startListening re-entrance
  private continuousTimer: ReturnType<typeof setTimeout> | null = null; // single continuous-listen timer
  private onEngineChanged: ((e: Event) => void) | null = null;

  constructor(callbacks: VoiceConversationCallbacks) {
    this.callbacks = callbacks;
    // Auto-reconnect when STT engine changes in settings
    this.onEngineChanged = () => {
      if (this.ws) {
        this.wsConnected = false;
        try { this.ws.close(); } catch {}
        this.ws = null;
      }
    };
    window.addEventListener('stt-engine-changed', this.onEngineChanged);
  }

  getState() { return this.state; }
  isContinuous() { return this.continuousMode; }
  isTtsEnabled() { return this.ttsEnabled; }
  getBackend() { return this.backend; }
  getWsConnected() { return this.wsConnected; }
  getWsConnecting() { return this.wsConnecting; }

  setContinuousMode(enabled: boolean) {
    this.continuousMode = enabled;
  }

  setTtsEnabled(enabled: boolean) {
    this.ttsEnabled = enabled;
    if (!enabled) {
      this.cancelSpeaking();
    }
  }

  clearHistory() {
    // Reset server-side history by reconnecting
    if (this.ws) {
      this.wsConnected = false;
      try { this.ws.close(); } catch {}
      this.ws = null;
    }
  }

  redownloadModel() {
    if (this.ws && this.wsConnected) {
      this.ws.send(JSON.stringify({ type: 'redownload_model' }));
    } else {
      // Set flag so next connection triggers it
      try { localStorage.setItem('lingdong-stt-redownload', 'true'); } catch {}
    }
  }

  private async ensureWs(): Promise<boolean> {
    if (this.wsConnected && this.ws) return true;

    // Close any stale connection
    if (this.ws) {
      this.wsConnected = false;
      try { this.ws.close(); } catch {}
      this.ws = null;
    }

    // Get WebSocket URL from the main process
    try {
      const api = window.electronAPI;
      if (api?.getVoiceServerUrl) {
        const result = await api.getVoiceServerUrl();
        if (result?.url) {
          this.wsUrl = result.url;
        }
      }
    } catch {
      // Fallback: use default port
      this.wsUrl = 'ws://127.0.0.1:8765';
    }

    if (!this.wsUrl) {
      this.wsUrl = 'ws://127.0.0.1:8765';
    }

    return this.connectWs();
  }

  private setBackend(b: STTBackend) {
    if (this.backend !== b) {
      this.backend = b;
      this.callbacks.onBackendChange?.(b, this.wsConnected);
    }
  }

  private connectWs(): Promise<boolean> {
    this.wsConnecting = true;
    this.setBackend('none');
    return new Promise((resolve) => {
      try {
        const ws = new WebSocket(this.wsUrl!);
        ws.binaryType = 'arraybuffer';

        ws.onopen = () => {
          this.ws = ws;
          this.wsConnected = true;
          this.wsConnecting = false;
          this.reconnectAttempts = 0;

          // Send init message with API keys and STT engine setting
          const apiKey = getApiKey();
          const elevenlabsKey = getElevenlabsKey();
          const elevenlabsVoiceId = getElevenlabsVoiceId();
          const sttEngine = getSttEngine();
          ws.send(JSON.stringify({ apiKey, elevenlabsKey, elevenlabsVoiceId, sttEngine }));

          // Check redownload flag
          try {
            if (localStorage.getItem('lingdong-stt-redownload') === 'true') {
              ws.send(JSON.stringify({ type: 'redownload_model' }));
            }
          } catch {}

          resolve(true);
        };

        ws.onmessage = (event) => {
          if (event.data instanceof ArrayBuffer) {
            this.onAudioData(event.data);
          } else {
            this.onWsMessage(event.data);
          }
        };

        ws.onclose = () => {
          this.wsConnected = false;
          this.wsConnecting = false;
          if (this.ws === ws) this.ws = null;
          this.setBackend('none');
          this.attemptReconnect();
        };

        ws.onerror = () => {
          // onclose will fire after this
        };

        // Timeout if connection takes too long
        setTimeout(() => {
          if (ws.readyState !== WebSocket.OPEN) {
            ws.close();
            this.wsConnecting = false;
            resolve(false);
          }
        }, 3000);
      } catch {
        this.wsConnecting = false;
        resolve(false);
      }
    });
  }

  private attemptReconnect() {
    if (!this.continuousMode) return;
    this.reconnectAttempts++;
    // Exponential backoff: 1s, 2s, 4s, 8s... capped at 30s
    const delay = Math.min(RECONNECT_DELAY_MS * Math.pow(2, this.reconnectAttempts - 1), 30000);
    this.pendingReconnectTimer = setTimeout(() => {
      this.connectWs().then((ok) => {
        if (!ok && this.continuousMode) this.attemptReconnect();
      });
    }, delay);
  }

  private onWsMessage(raw: string) {
    try {
      const msg = JSON.parse(raw);
      switch (msg.type) {
        case 'recording_start':
          this.setState('listening');
          break;
        case 'stt_result':
          if (msg.text) {
            this.setState('thinking');
            this.callbacks.onTranscript(msg.text);
            // Server will auto-process LLM → TTS, no need to send transcript back
          } else {
            // No speech detected — don't stay stuck, restart listening in continuous mode
            this.setState('idle');
            if (this.continuousMode) {
              this.scheduleContinuousListen();
            }
          }
          break;
        case 'model_redownloaded':
          try { localStorage.removeItem('lingdong-stt-redownload'); } catch {}
          this.callbacks.onResponse('✅ 模型已重新下载并加载');
          break;
        case 'model_redownload_error':
          this.callbacks.onError(`模型下载失败: ${msg.message}`);
          break;
        case 'llm_start':
          this.setState('thinking');
          break;
        case 'llm_text':
          this.callbacks.onResponse(msg.text);
          break;
        case 'switch_engine':
          try { localStorage.setItem('lingdong-stt-engine', msg.engine); } catch {}
          (window as any).__STT_ENGINE__ = msg.engine;
          this.callbacks.onResponse(`🔄 已切换至 ${engineLabel(msg.engine)}`);
          this.setState('idle');
          break;
        case 'tts_end':
          this.onTtsEnd();
          break;
        case 'error':
          this.callbacks.onError(msg.message);
          this.setState('idle');
          break;
      }
    } catch {
      // Ignore parse errors
    }
  }

  private onAudioData(data: ArrayBuffer) {
    if (!this.ttsEnabled) return;
    this.audioQueue.push(data);
    if (!this.isPlaying) {
      this.playNextAudio();
    }
  }

  private async playNextAudio() {
    if (this.audioQueue.length === 0) {
      this.isPlaying = false;
      if (this.ttsFinished) {
        this.ttsFinished = false;
        this.scheduledUntil = 0;
        this.setState('idle');
        this.scheduleContinuousListen();
      }
      return;
    }

    this.isPlaying = true;
    this.setState('speaking');

    try {
      if (!this.audioCtx) {
        this.audioCtx = new AudioContext();
      }
      // Resume if suspended (autoplay policy)
      if (this.audioCtx.state === 'suspended') {
        await this.audioCtx.resume();
      }

      const chunk = this.audioQueue.shift()!;
      const audioBuffer = await this.audioCtx.decodeAudioData(chunk);

      const source = this.audioCtx.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(this.audioCtx.destination);
      this.currentSource = source;

      // Schedule with precise timing to avoid gaps between chunks
      const now = this.audioCtx.currentTime;
      if (this.scheduledUntil < now) {
        this.scheduledUntil = now;
      }
      source.start(this.scheduledUntil);
      this.scheduledUntil += audioBuffer.duration;

      source.onended = () => {
        this.currentSource = null;
        this.playNextAudio();
      };
    } catch {
      // Skip bad audio chunk and try next
      this.playNextAudio();
    }
  }

  private onTtsEnd() {
    this.ttsFinished = true;
    if (!this.isPlaying) {
      this.ttsFinished = false;
      this.setState('idle');
      this.scheduleContinuousListen();
    }
  }

  private scheduleContinuousListen(delay: number = 500) {
    // Cancel any existing timer — prevents stacking multiple schedules
    if (this.continuousTimer) {
      clearTimeout(this.continuousTimer);
      this.continuousTimer = null;
    }
    if (this.continuousMode && this.state === 'idle') {
      this.continuousTimer = setTimeout(() => {
        this.continuousTimer = null;
        if (this.continuousMode && this.state === 'idle') {
          this.startListening();
        }
      }, delay);
    }
  }

  async startListening() {
    // Guard: prevent re-entrance across async boundary
    if (this.starting || this.state === 'listening') return;
    this.starting = true;
    try {
      this.cancelSpeaking();

      // Prefer server-side STT via WebSocket (supports offline whisper backend)
      const wsOk = await this.ensureWs();
      if (wsOk && this.wsConnected) {
        this.setBackend('whisper');
        this.setState('listening');
        this.ws!.send(JSON.stringify({ type: 'start_recording' }));
        return;
      }

      // Fallback: browser Web Speech API STT (may fail in restricted networks)
      const SpeechRecognitionCtor =
        (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
      if (!SpeechRecognitionCtor) {
        return;
      }
      this.setBackend('browser');

      const recognition = new SpeechRecognitionCtor();
      recognition.lang = 'zh-CN';
      recognition.continuous = false;
      recognition.interimResults = false;

      recognition.onstart = () => {
        this.setState('listening');
      };

      recognition.onerror = (event: any) => {
        this.setState('idle');
        if (event.error === 'not-allowed') {
          this.callbacks.onError('请允许麦克风权限');
        } else if (event.error !== 'no-speech' && event.error !== 'aborted') {
          this.callbacks.onError(`语音识别错误(${event.error})`);
        }
      };

      recognition.onend = () => {
        if (this.state === 'listening') {
          this.setState('idle');
          if (this.continuousMode) {
            this.scheduleContinuousListen(300);
          }
        }
      };

      recognition.onresult = (event: any) => {
        let finalText = '';
        for (let i = event.resultIndex; i < event.results.length; i++) {
          if (event.results[i].isFinal) {
            finalText += event.results[i][0].transcript;
          }
        }
        if (finalText) {
          this.callbacks.onTranscript(finalText);
          this.processUserInput(finalText);
        }
      };

      try {
        recognition.start();
        this.recognition = recognition;
      } catch {
        this.setState('idle');
      }
    } finally {
      this.starting = false;
    }
  }

  stopListening() {
    if (this.recognition) {
      try { this.recognition.stop(); } catch {}
      this.recognition = null;
    }
    this.setState('idle');
  }

  cancelSpeaking() {
    // Cancel server-side processing too
    if (this.ws && this.wsConnected) {
      try { this.ws.send(JSON.stringify({ type: 'cancel' })); } catch {}
    }
    if (this.currentSource) {
      try { this.currentSource.stop(); } catch {}
      this.currentSource = null;
    }
    this.isPlaying = false;
    this.ttsFinished = false;
    this.audioQueue = [];
    this.setState('idle');
  }

  async processUserInput(text: string) {
    this.setState('thinking');

    try {
      const ws = this.ws;
      if (!ws || !this.wsConnected) {
        const ok = await this.ensureWs();
        if (!ok) {
          // Fallback: direct LLM call through electronAPI
          this.fallbackLlmCall(text);
          return;
        }
      }

      // Send transcript to voice server
      this.ws!.send(JSON.stringify({ type: 'transcript', text }));
    } catch (err: any) {
      this.callbacks.onError(`请求失败: ${err.message}`);
      this.setState('idle');
    }
  }

  private async fallbackLlmCall(text: string) {
    try {
      const apiKey = getApiKey();
      const apiBase = getApiBase();
      const model = getModel();

      if (!apiKey) {
        this.callbacks.onError('请先在设置页面配置 API 密钥');
        this.setState('idle');
        return;
      }

      const api = window.electronAPI;
      if (!api?.aiGenerate) {
        this.callbacks.onError('AI 服务不可用');
        this.setState('idle');
        return;
      }

      // Use a simple system prompt for fallback
      const messages = [
        { role: 'system' as const, content: '你是一个语音助手，请用中文口语化回答。回答要简洁自然。' },
        { role: 'user' as const, content: text },
      ];

      const data = await api.aiGenerate({
        apiKey,
        messages,
        apiBase: apiBase || undefined,
        model: model || undefined,
      });

      const reply =
        data?.choices?.[0]?.message?.content ||
        data?.result?.choices?.[0]?.message?.content ||
        data?.content ||
        data?.reply ||
        '抱歉，我没有理解您的意思。';

      this.callbacks.onResponse(reply);

      if (this.ttsEnabled) {
        // Use browser TTS as fallback
        await this.speakFallback(reply);
      }

      this.scheduleContinuousListen(500);
    } catch (err: any) {
      this.callbacks.onError(`请求失败: ${err.message}`);
      this.setState('idle');
    }
  }

  private speakFallback(text: string): Promise<void> {
    return new Promise((resolve) => {
      this.setState('speaking');
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = 'zh-CN';
      utterance.rate = 1.1;
      utterance.pitch = 1.0;
      utterance.volume = 1.0;

      utterance.onend = () => {
        this.setState('idle');
        resolve();
      };
      utterance.onerror = () => {
        this.setState('idle');
        resolve();
      };

      window.speechSynthesis.speak(utterance);
    });
  }

  private setState(state: VoiceState) {
    this.state = state;
    this.callbacks.onStateChange(state);
  }

  destroy() {
    if (this.onEngineChanged) {
      window.removeEventListener('stt-engine-changed', this.onEngineChanged);
      this.onEngineChanged = null;
    }
    this.stopListening();
    this.cancelSpeaking();
    if (this.continuousTimer) {
      clearTimeout(this.continuousTimer);
      this.continuousTimer = null;
    }
    if (this.pendingReconnectTimer) {
      clearTimeout(this.pendingReconnectTimer);
      this.pendingReconnectTimer = null;
    }
    if (this.ws) {
      this.wsConnected = false;
      try { this.ws.close(); } catch {}
      this.ws = null;
    }
    if (this.audioCtx) {
      this.audioCtx.close().catch(() => {});
      this.audioCtx = null;
    }
  }
}
