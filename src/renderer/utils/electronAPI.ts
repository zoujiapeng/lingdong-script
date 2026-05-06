"use client";

/**
 * 类型安全的 electronAPI 访问
 *
 * 替代 (window as any).electronAPI?.xxx() 模式
 */
import type { ElectronAPI } from '../types/electron';

let _cached: ElectronAPI | null = null;

export function getElectronAPI(): ElectronAPI | null {
  if (_cached) return _cached;
  if (typeof window !== 'undefined' && window.electronAPI) {
    _cached = window.electronAPI;
  }
  return _cached;
}

export function hasElectronAPI(): boolean {
  return getElectronAPI() !== null;
}
