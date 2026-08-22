/** Electron preload API 类型声明 */
export interface ElectronAPI {
  // File dialogs
  openFileDialog: (filters?: { name: string; extensions: string[] }[]) => Promise<{ canceled: boolean; filePaths: string[] }>;
  saveFileDialog: (defaultName?: string) => Promise<{ canceled: boolean; filePath?: string }>;

  // File read/write
  readFile: (filePath: string) => Promise<{ content?: string; error?: string }>;
  readImage: (filePath: string) => Promise<{ dataURL?: string; error?: string }>;
  writeFile: (filePath: string, content: string) => Promise<{ success: boolean; error?: string }>;

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
  captureScreenshot: () => Promise<{ dataURL?: string; error?: string } | null>;
  selectRegion: (options?: { includePreview?: boolean }) => Promise<{
    x: number;
    y: number;
    w: number;
    h: number;
    dataURL?: string;
    display_id: string;
    scale_factor: number;
    display_bounds_dip: { x: number; y: number; width: number; height: number };
    display_bounds_physical: { x: number; y: number; width: number; height: number };
    coordinate_space: 'screen_physical_px';
  } | { error: string } | null>;
  saveBase64Image: (filename: string, dataURL: string) => Promise<{ success: boolean; path?: string; error?: string }>;
  ocrRecognizeRegion: (region: { x: number; y: number; w: number; h: number }) => Promise<string>;

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

  // Global Agent control hotkeys (F7 pause/resume, F8 stop)
  onAgentControl: (callback: (action: string) => void) => () => void;

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
  notifyTaskbar: (
    title: string,
    body: string,
    kind?: 'success' | 'error' | 'info',
    runId?: string,
  ) => Promise<boolean>;

  // WinPilot authenticated control-plane connection
  getWinpilotUrl: () => Promise<string | null>;
  getWinpilotConnection: () => Promise<{ url: string; token: string } | null>;
  // Ensure the WinPilot server is (re)starting; returns native workbench status
  ensureWinpilot: () => Promise<{ url: string | null; token?: string; starting: boolean; error: string | null }>;
  restartWinpilot: () => Promise<{ url: string | null; token?: string; starting: boolean; error: string | null }>;
  setTitleBarTheme: (theme: 'light' | 'dark') => Promise<boolean>;
  minimizeWindow: () => Promise<boolean>;
  toggleMaximizeWindow: () => Promise<boolean>;
  closeWindow: () => Promise<boolean>;

}

declare global {
  interface Window {
    electronAPI?: ElectronAPI;
    /** Custom LLM API base URL */
    __LLM_API_BASE__?: string;
    /** Custom LLM model name */
    __LLM_MODEL__?: string;
    /** Multimodal (vision) support toggle */
    __LLM_MULTIMODAL__?: boolean;
    /** Bridge state getter exposed by store */
    __bridge_getState?: () => any;
    /** Template registry (set by TemplateRegistry) */
    __templateRegistry?: {
      getById: (id: string) => { dsl_content?: string } | null;
      getAll: () => any[];
    };
  }
}
