/** Electron preload API 类型声明 */
export interface ElectronAPI {
  // File dialogs
  openFileDialog: (filters?: { name: string; extensions: string[] }[]) => Promise<string | null>;
  saveFileDialog: (defaultName?: string) => Promise<string | null>;

  // File read/write
  readFile: (filePath: string) => Promise<string>;
  writeFile: (filePath: string, content: string) => Promise<void>;

  // Engine control
  engineStart: (scriptJson: string) => Promise<void>;
  engineStop: () => Promise<void>;
  enginePause: () => Promise<void>;
  engineResume: () => Promise<void>;
  engineStatus: () => Promise<string>;

  // Engine messages
  onEngineMessage: (callback: (msg: any) => void) => () => void;
  onEngineLog: (callback: (msg: any) => void) => () => void;

  // Screenshot capture
  captureScreenshot: () => Promise<string>;
  selectRegion: () => Promise<{ x: number; y: number; w: number; h: number; dataURL: string } | null>;
  saveBase64Image: (filename: string, dataURL: string) => Promise<{ success: boolean; path?: string; error?: string }>;
  ocrRecognizeRegion: (region: { x: number; y: number; w: number; h: number }) => Promise<string>;

  // AI generation
  aiGenerate: (params: {
    apiKey: string;
    messages: { role: string; content: string }[];
    apiBase?: string;
    model?: string;
  }) => Promise<any>;

  // DSL conversion
  dslToFlow: (dslText: string) => Promise<any>;

  // Feature registry (MCP tools)
  featuresList: () => Promise<any[]>;
  callFeature: (name: string, params: Record<string, any>) => Promise<any>;

  // Wheel selector (tkinter overlay)
  wheelSelect: (params: {
    options: string[];
    values?: string[];
    title?: string;
    message?: string;
  }) => Promise<string | null>;

  // Menu actions
  onMenuAction: (callback: (action: string) => void) => () => void;

  // API commands
  onApiCommand: (callback: (action: string, payload: any) => void) => () => void;

  // Renderer state
  pushState: (state: any) => void;

  // Macro recording
  macroStart: (params?: { name?: string; include_mouse?: boolean; include_keys?: boolean }) => Promise<void>;
  macroStop: () => Promise<{ success: boolean; result?: any; error?: string }>;
  macroList: () => Promise<any[]>;
  macroPlay: (name: string) => Promise<any>;
  macroDelete: (name: string) => Promise<{ success: boolean; error?: string }>;
  macroGetEvents: (name: string) => Promise<{ success: boolean; result?: any; error?: string }>;
  macroRecordingStatus: () => Promise<{ recording: boolean; name?: string }>;
  onMacroMessage: (callback: (msg: any) => void) => () => void;

  // Auto-start
  getAutoStart: () => Promise<boolean>;
  setAutoStart: (enable: boolean) => Promise<void>;

  // Schedule CRUD
  scheduleList: () => Promise<any[]>;
  scheduleCreate: (entry: any) => Promise<any>;
  scheduleUpdate: (id: string, updates: any) => Promise<any>;
  scheduleDelete: (id: string) => Promise<boolean>;

  // Notifications
  showNotification: (title: string, body: string) => Promise<void>;

  // Agent control
  agentStart: (goal: string, config?: Record<string, any>, supervise?: boolean, steps?: any[]) => Promise<any>;
  agentStop: () => Promise<any>;
  agentGuidance: (text: string) => Promise<any>;
  agentChat: (text: string) => Promise<any>;
  agentStatus: () => Promise<any>;

  // WinPilot embedded server URL (Agent panel iframe target)
  getWinpilotUrl: () => Promise<string | null>;

  // Agent messages
  onAgentMessage: (callback: (msg: any) => void) => () => void;
  onAgentLog: (callback: (msg: any) => void) => () => void;

}

declare global {
  interface Window {
    electronAPI?: ElectronAPI;
    /** DeepSeek API key injected at build-time */
    __DEEPSEEK_KEY__?: string;
    /** Custom LLM API base URL */
    __LLM_API_BASE__?: string;
    /** Custom LLM model name */
    __LLM_MODEL__?: string;
    /** Multimodal (vision) support toggle */
    __LLM_MULTIMODAL__?: boolean;
    /** Template search function exposed by App.tsx */
    __searchTemplates?: (query: string, maxResults?: number) => string;
    /** Bridge state getter exposed by store */
    __bridge_getState?: () => any;
    /** Template registry (set by TemplateRegistry) */
    __templateRegistry?: {
      getById: (id: string) => { dsl_content?: string } | null;
      getAll: () => any[];
    };
  }
}
