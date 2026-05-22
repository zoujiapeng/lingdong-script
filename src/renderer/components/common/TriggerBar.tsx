import React, { useEffect, useCallback, useState } from 'react';
import { useStore } from '../../store';
import type { TriggerUIState } from '../../types';
import { Zap } from 'lucide-react';

const CATEGORY_ICONS: Record<string, string> = {
  timer: '⏱',
  window: '▣',
  process: '⚙',
  filesystem: '📁',
  clipboard: '📋',
  network: '🌐',
};

function getCategoryIcon(specName: string): string {
  if (specName.includes('timer')) return CATEGORY_ICONS.timer;
  if (specName.includes('window')) return CATEGORY_ICONS.window;
  if (specName.includes('process')) return CATEGORY_ICONS.process;
  if (specName.includes('file')) return CATEGORY_ICONS.filesystem;
  if (specName.includes('clipboard')) return CATEGORY_ICONS.clipboard;
  if (specName.includes('network')) return CATEGORY_ICONS.network;
  return '●';
}

function getStatusColor(status: string): string {
  switch (status) {
    case 'running': return '#22c55e';
    case 'paused': return '#f59e0b';
    case 'fired': return '#3b82f6';
    case 'stopped': return '#6b7280';
    default: return '#6b7280';
  }
}

const SCRIPT_TRIGGER_ICONS: Record<string, string> = {
  interval: '⏱',
  cron: '⏰',
  startup: '🚀',
  event: '📡',
};

function TriggerPill({ trigger, onStop }: { trigger: TriggerUIState; onStop: (id: string) => void }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="trigger-pill" onClick={() => setExpanded(!expanded)}>
      <span className="trigger-pill-icon" style={{ color: getStatusColor(trigger.status) }}>
        {getCategoryIcon(trigger.spec_name)}
      </span>
      <span className="trigger-pill-name">{trigger.spec_name}</span>
      {trigger.fired_count > 0 && (
        <span className="trigger-pill-count">({trigger.fired_count})</span>
      )}
      {expanded && (
        <div className="trigger-pill-detail">
          <div className="trigger-detail-row">
            <span>状态</span>
            <span style={{ color: getStatusColor(trigger.status) }}>{trigger.status}</span>
          </div>
          {trigger.last_fired && (
            <div className="trigger-detail-row">
              <span>上次触发</span>
              <span>{new Date(trigger.last_fired * 1000).toLocaleTimeString()}</span>
            </div>
          )}
          <div className="trigger-detail-row">
            <span>触发次数</span>
            <span>{trigger.fired_count}</span>
          </div>
          {Object.entries(trigger.config || {}).map(([k, v]) => (
            <div className="trigger-detail-row" key={k}>
              <span>{k}</span>
              <span>{String(v)}</span>
            </div>
          ))}
          <button
            className="trigger-detail-stop"
            onClick={(e) => { e.stopPropagation(); onStop(trigger.id); }}
          >
            停止
          </button>
        </div>
      )}
    </div>
  );
}

function ScriptTriggerBadge({ trigger, index }: { trigger: any; index: number }) {
  const updateScriptTrigger = useStore(s => s.updateScriptTrigger);
  const [editing, setEditing] = useState(false);

  const typeLabel: Record<string, string> = {
    interval: `每${trigger.interval_seconds || 60}秒`,
    cron: trigger.cron_expr || '',
    startup: '启动时',
    event: trigger.event_type || '',
  };

  return (
    <div
      className="trigger-pill script-trigger-badge"
      onClick={() => setEditing(!editing)}
      title="点击修改触发配置"
    >
      <span className="trigger-pill-icon">{SCRIPT_TRIGGER_ICONS[trigger.type] || '⚡'}</span>
      <span className="trigger-pill-name">{typeLabel[trigger.type] || trigger.type}</span>
      {editing && (
        <div className="trigger-pill-detail" onClick={(e) => e.stopPropagation()}>
          <div className="trigger-detail-row">
            <span>触发方式</span>
            <span>{trigger.type}</span>
          </div>
          <div className="trigger-detail-row">
            <span>配置</span>
            <span>{typeLabel[trigger.type]}</span>
          </div>
          <button
            className="trigger-detail-stop"
            onClick={() => useStore.getState().removeScriptTrigger(index)}
          >
            移除
          </button>
        </div>
      )}
    </div>
  );
}

export function TriggerBar() {
  const triggers = useStore(s => s.triggers);
  const scriptTriggers = useStore(s => s.scriptTriggers);
  const panelsVisible = useStore(s => s.ui.panels_visible);
  const stopTrigger = useStore(s => s.stopTrigger);
  const stopAllTriggers = useStore(s => s.stopAllTriggers);

  // Hooks must be called unconditionally — before any early return
  const handleStop = useCallback((id: string) => {
    stopTrigger(id);
    useStore.setState((s: any) => {
      s.triggers = s.triggers.filter((t: TriggerUIState) => t.id !== id);
    });
  }, [stopTrigger]);

  const handleStopAll = useCallback(() => {
    stopAllTriggers();
    useStore.setState((s: any) => { s.triggers = []; });
  }, [stopAllTriggers]);

  // Poll triggers periodically when visible
  useEffect(() => {
    if (!panelsVisible || triggers.length === 0) return;
    const api = (window as any).electronAPI;
    if (!api?.callFeature) return;
    const interval = setInterval(async () => {
      try {
        const result = await api.callFeature('trigger_list', {});
        if (Array.isArray(result)) {
          useStore.setState((s: any) => { s.triggers = result; });
        }
      } catch { /* ignore */ }
    }, 3000);
    return () => clearInterval(interval);
  }, [panelsVisible, triggers.length]);

  if (!panelsVisible) return null;

  const hasRuntimeTriggers = triggers.length > 0;
  const hasScriptTriggers = scriptTriggers.length > 0;

  return (
    <div className="trigger-bar panels-visible">
      <div className="trigger-bar-left">
        {/* 运行时活跃触发器 */}
        {triggers.map(t => (
          <TriggerPill key={t.id} trigger={t} onStop={handleStop} />
        ))}
        {/* 脚本级触发配置 */}
        {scriptTriggers.map((t, i) => (
          <ScriptTriggerBadge key={i} trigger={t} index={i} />
        ))}
        {/* 无触发器时的提示 */}
        {!hasRuntimeTriggers && !hasScriptTriggers && (
          <span className="trigger-bar-empty">
            <Zap size={12} style={{ marginRight: 4 }} />
            无触发配置 — 点击 ⚡ 按钮添加定时/事件触发
          </span>
        )}
      </div>
      {hasRuntimeTriggers && (
        <button className="trigger-bar-stop-all" onClick={handleStopAll} title="停止全部">
          停止全部
        </button>
      )}
    </div>
  );
}
