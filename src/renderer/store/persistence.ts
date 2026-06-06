/** localStorage persistence helpers */

import type { AgentRunRecord } from '../types';
import { CUSTOM_TEMPLATES_KEY, AGENT_HISTORY_KEY, TEMPLATE_SHORTCUTS_KEY } from './defaults';

export interface CustomTemplate {
  id: string;
  name: string;
  description: string;
  script: any;
  shortcut?: string;
}

// ── Template Shortcuts ──

export function loadTemplateShortcuts(): Record<string, string> {
  try {
    const saved = localStorage.getItem(TEMPLATE_SHORTCUTS_KEY);
    return saved ? JSON.parse(saved) : {};
  } catch { return {}; }
}

export function saveTemplateShortcut(templateId: string, shortcut: string) {
  try {
    const all = loadTemplateShortcuts();
    if (shortcut) all[templateId] = shortcut;
    else delete all[templateId];
    localStorage.setItem(TEMPLATE_SHORTCUTS_KEY, JSON.stringify(all));
  } catch {}
}

export function deleteTemplateShortcut(templateId: string) {
  saveTemplateShortcut(templateId, '');
}

// ── Custom Templates ──

export function loadCustomTemplates(): CustomTemplate[] {
  try {
    const saved = localStorage.getItem(CUSTOM_TEMPLATES_KEY);
    return saved ? JSON.parse(saved) : [];
  } catch { return []; }
}

export function saveCustomTemplates(templates: CustomTemplate[]) {
  try {
    localStorage.setItem(CUSTOM_TEMPLATES_KEY, JSON.stringify(templates));
  } catch {}
}

// ── Agent History ──

export function loadAgentHistory(): AgentRunRecord[] {
  try {
    const saved = localStorage.getItem(AGENT_HISTORY_KEY);
    return saved ? JSON.parse(saved) : [];
  } catch { return []; }
}

export function saveAgentHistory(history: AgentRunRecord[]) {
  try {
    localStorage.setItem(AGENT_HISTORY_KEY, JSON.stringify(history.slice(0, 50)));
  } catch {}
}
