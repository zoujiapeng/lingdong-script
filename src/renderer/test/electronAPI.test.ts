import { describe, it, expect, beforeEach, afterEach } from 'vitest';

describe('electronAPI', () => {
  // Since Vite caches ES modules, each test file gets its own module instance.
  // We use a fresh dynamic import for each test to avoid cross-test pollution
  // from the module-level _cached variable in electronAPI.ts.

  beforeEach(() => {
    if (typeof globalThis.window !== 'undefined') {
      delete (globalThis.window as any).electronAPI;
    }
  });

  it('returns null when window.electronAPI is not set', async () => {
    const { getElectronAPI } = await import('../utils/electronAPI');
    expect(getElectronAPI()).toBeNull();
  });

  it('returns false from hasElectronAPI when not available', async () => {
    const { hasElectronAPI } = await import('../utils/electronAPI');
    expect(hasElectronAPI()).toBe(false);
  });

  it('returns the electronAPI object when set', async () => {
    const mockAPI = { aiGenerate: async () => ({}), showMessage: () => {} };
    (globalThis.window as any).electronAPI = mockAPI;
    const { getElectronAPI } = await import('../utils/electronAPI');
    expect(getElectronAPI()).toBe(mockAPI);
  });

  it('returns true from hasElectronAPI when available', async () => {
    (globalThis.window as any).electronAPI = { aiGenerate: async () => ({}) };
    const { hasElectronAPI } = await import('../utils/electronAPI');
    expect(hasElectronAPI()).toBe(true);
  });
});
