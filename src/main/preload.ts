import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('electronAPI', {
  // File dialogs
  openFileDialog: (filters?: { name: string; extensions: string[] }[]) =>
    ipcRenderer.invoke('dialog:openFile', filters),
  saveFileDialog: (defaultName?: string) =>
    ipcRenderer.invoke('dialog:saveFile', defaultName),

  // File read/write (via main process, bypasses nodeIntegration restriction)
  readFile: (filePath: string) => ipcRenderer.invoke('file:read', filePath),
  writeFile: (filePath: string, content: string) => ipcRenderer.invoke('file:write', filePath, content),

  // Engine control
  engineStart: (scriptJson: string) => ipcRenderer.invoke('engine:start', scriptJson),
  engineStop: () => ipcRenderer.invoke('engine:stop'),
  enginePause: () => ipcRenderer.invoke('engine:pause'),
  engineResume: () => ipcRenderer.invoke('engine:resume'),
  engineStatus: () => ipcRenderer.invoke('engine:status'),

  // Engine messages (from main process)
  onEngineMessage: (callback: (msg: any) => void) => {
    const handler = (_: any, msg: any) => callback(msg);
    ipcRenderer.on('engine-message', handler);
    return () => ipcRenderer.removeListener('engine-message', handler);
  },
  onEngineLog: (callback: (msg: any) => void) => {
    const handler = (_: any, msg: any) => callback(msg);
    ipcRenderer.on('engine-log', handler);
    return () => ipcRenderer.removeListener('engine-log', handler);
  },

  // Agent control
  agentStart: (goal: string, config?: Record<string, any>, supervise?: boolean, steps?: any[]) =>
    ipcRenderer.invoke('agent:start', goal, config, supervise, steps),
  agentStop: () =>
    ipcRenderer.invoke('agent:stop'),
  agentGuidance: (text: string) =>
    ipcRenderer.invoke('agent:guidance', text),
  agentChat: (text: string) =>
    ipcRenderer.invoke('agent:chat', text),
  agentStatus: () =>
    ipcRenderer.invoke('agent:status'),

  // Agent messages (from main process)
  onAgentMessage: (callback: (msg: any) => void) => {
    const handler = (_: any, msg: any) => callback(msg);
    ipcRenderer.on('agent-message', handler);
    return () => ipcRenderer.removeListener('agent-message', handler);
  },
  onAgentLog: (callback: (msg: any) => void) => {
    const handler = (_: any, msg: any) => callback(msg);
    ipcRenderer.on('agent-log', handler);
    return () => ipcRenderer.removeListener('agent-log', handler);
  },

  // Screenshot capture
  captureScreenshot: () => ipcRenderer.invoke('screenshot:capture'),
  selectRegion: () => ipcRenderer.invoke('screenshot:selectRegion'),
  saveBase64Image: (filename: string, dataURL: string) => ipcRenderer.invoke('screenshot:saveBase64Image', filename, dataURL),
  ocrRecognizeRegion: (region: { x: number; y: number; w: number; h: number }) =>
    ipcRenderer.invoke('ocr:recognizeRegion', region),

  // AI generation (via main process to avoid CORS)
  aiGenerate: (params: { apiKey: string; messages: { role: string; content: string }[] }) =>
    ipcRenderer.invoke('ai:generate', params),

  // DSL conversion
  dslToFlow: (dslText: string) => ipcRenderer.invoke('dsl:toFlow', dslText),

  // Feature registry (MCP tools)
  featuresList: () => ipcRenderer.invoke('features:list'),
  callFeature: (name: string, params: Record<string, any>) =>
    ipcRenderer.invoke('feature:call', name, params),

  // Wheel selector (tkinter overlay)
  wheelSelect: (params: { options: string[]; values?: string[]; title?: string; message?: string }) =>
    ipcRenderer.invoke('interaction:wheelSelect', params),

  // Menu actions (from native menu bar)
  onMenuAction: (callback: (action: string) => void) => {
    const handler = (_: any, action: string) => callback(action);
    ipcRenderer.on('menu-action', handler);
    return () => ipcRenderer.removeListener('menu-action', handler);
  },

  // API commands (from local HTTP bridge)
  onApiCommand: (callback: (action: string, payload: any) => void) => {
    const handler = (_: any, msg: any) => callback(msg.action, msg.payload);
    ipcRenderer.on('api-command', handler);
    return () => ipcRenderer.removeListener('api-command', handler);
  },

  // Push renderer state to main process (for HTTP API /api/get-state)
  pushState: (state: any) => {
    ipcRenderer.send('state:response', state);
  },

  // Macro recording API
  macroStart: (params?: { name?: string; include_mouse?: boolean; include_keys?: boolean }) =>
    ipcRenderer.invoke('macro:start', params || {}),
  macroStop: () =>
    ipcRenderer.invoke('macro:stop'),
  macroList: () =>
    ipcRenderer.invoke('macro:list'),
  macroPlay: (name: string) =>
    ipcRenderer.invoke('macro:play', name),
  macroDelete: (name: string) =>
    ipcRenderer.invoke('macro:delete', name),
  macroGetEvents: (name: string) =>
    ipcRenderer.invoke('macro:get-events', name),
  macroRecordingStatus: () =>
    ipcRenderer.invoke('macro:recording-status'),

  // Macro messages (from main process)
  onMacroMessage: (callback: (msg: any) => void) => {
    const handler = (_: any, msg: any) => callback(msg);
    ipcRenderer.on('macro-message', handler);
    return () => ipcRenderer.removeListener('macro-message', handler);
  },

  // Auto-start
  getAutoStart: () => ipcRenderer.invoke('settings:get-auto-start'),
  setAutoStart: (enable: boolean) => ipcRenderer.invoke('settings:set-auto-start', enable),

  // Vision backend (VLM provider)
  setVisionBackend: (backend: string) => ipcRenderer.invoke('settings:set-vision-backend', backend),

  // Schedule CRUD
  scheduleList: () => ipcRenderer.invoke('schedule:list'),
  scheduleCreate: (entry: any) => ipcRenderer.invoke('schedule:create', entry),
  scheduleUpdate: (id: string, updates: any) => ipcRenderer.invoke('schedule:update', id, updates),
  scheduleDelete: (id: string) => ipcRenderer.invoke('schedule:delete', id),

  // Notice: built-in actions
  showNotification: (title: string, body: string) => ipcRenderer.invoke('notification:show', title, body),
});
