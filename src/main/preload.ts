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
    ipcRenderer.on('engine-message', (_, msg) => callback(msg));
  },
  onEngineLog: (callback: (msg: any) => void) => {
    ipcRenderer.on('engine-log', (_, msg) => callback(msg));
  },

  // Screenshot capture
  captureScreenshot: () => ipcRenderer.invoke('screenshot:capture'),
  selectRegion: () => ipcRenderer.invoke('screenshot:selectRegion'),
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
    ipcRenderer.on('menu-action', (_, action) => callback(action));
  },

  // API commands (from local HTTP bridge)
  onApiCommand: (callback: (action: string, payload: any) => void) => {
    ipcRenderer.on('api-command', (_, msg) => callback(msg.action, msg.payload));
  },
});
