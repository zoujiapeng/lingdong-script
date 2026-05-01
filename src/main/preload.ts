import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('electronAPI', {
  // File dialogs
  openFileDialog: (filters?: { name: string; extensions: string[] }[]) =>
    ipcRenderer.invoke('dialog:openFile', filters),
  saveFileDialog: (defaultName?: string) =>
    ipcRenderer.invoke('dialog:saveFile', defaultName),

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

  // AI generation (via main process to avoid CORS)
  aiGenerate: (params: { apiKey: string; messages: { role: string; content: string }[] }) =>
    ipcRenderer.invoke('ai:generate', params),

  // Menu actions (from native menu bar)
  onMenuAction: (callback: (action: string) => void) => {
    ipcRenderer.on('menu-action', (_, action) => callback(action));
  },
});
