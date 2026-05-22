import React from 'react';
import { useStore } from '../../store';
import type { TriggerDef, TriggerType } from '../../types';
import { X, Plus, Zap } from 'lucide-react';

const TRIGGER_TYPE_LABELS: Record<TriggerType, string> = {
  interval: '定时循环',
  cron: 'Cron 表达式',
  startup: '启动时触发',
  event: '事件触发',
};

const TRIGGER_TYPE_OPTIONS: { value: TriggerType; desc: string }[] = [
  { value: 'interval', desc: '每 N 秒循环执行' },
  { value: 'cron', desc: '按 cron 表达式调度' },
  { value: 'startup', desc: '应用启动时自动执行' },
  { value: 'event', desc: '系统事件触发（窗口/文件等）' },
];

function TriggerEditor({ trigger, onChange, onRemove }: {
  trigger: TriggerDef;
  onChange: (t: TriggerDef) => void;
  onRemove: () => void;
}) {
  return (
    <div className="trigger-editor">
      <div className="trigger-editor-header">
        <Zap size={12} />
        <select
          value={trigger.type}
          onChange={(e) => onChange({ ...trigger, type: e.target.value as TriggerType })}
          className="trigger-editor-select"
        >
          {TRIGGER_TYPE_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>{opt.desc}</option>
          ))}
        </select>
        <button className="btn btn-ghost btn-icon" onClick={onRemove} style={{ marginLeft: 'auto' }} title="移除">
          <X size={12} />
        </button>
      </div>
      <div className="trigger-editor-body">
        {trigger.type === 'interval' && (
          <div className="trigger-editor-field">
            <label>间隔（秒）</label>
            <input
              type="number"
              min={1}
              value={trigger.interval_seconds || 60}
              onChange={(e) => onChange({ ...trigger, interval_seconds: parseInt(e.target.value) || 60 })}
            />
          </div>
        )}
        {trigger.type === 'cron' && (
          <div className="trigger-editor-field">
            <label>Cron 表达式</label>
            <input
              type="text"
              value={trigger.cron_expr || '*/5 * * * *'}
              onChange={(e) => onChange({ ...trigger, cron_expr: e.target.value })}
              placeholder="*/5 * * * *"
            />
            <span className="trigger-editor-hint">分 时 日 月 周（例如 */5 * * * * = 每5分钟）</span>
          </div>
        )}
        {trigger.type === 'event' && (
          <>
            <div className="trigger-editor-field">
              <label>事件类型</label>
              <select
                value={trigger.event_type || 'window_open'}
                onChange={(e) => onChange({ ...trigger, event_type: e.target.value })}
              >
                <option value="window_open">窗口打开</option>
                <option value="window_close">窗口关闭</option>
                <option value="process_start">进程启动</option>
                <option value="process_exit">进程退出</option>
                <option value="file_created">文件创建</option>
                <option value="file_deleted">文件删除</option>
                <option value="clipboard_change">剪贴板变化</option>
                <option value="network_available">网络可用</option>
              </select>
            </div>
            {trigger.event_type === 'window_open' && (
              <div className="trigger-editor-field">
                <label>窗口标题</label>
                <input
                  type="text"
                  value={trigger.event_config?.window_title || ''}
                  onChange={(e) => onChange({ ...trigger, event_config: { ...trigger.event_config, window_title: e.target.value } })}
                  placeholder="例如: 记事本"
                />
              </div>
            )}
            {trigger.event_type === 'file_created' && (
              <div className="trigger-editor-field">
                <label>监控路径</label>
                <input
                  type="text"
                  value={trigger.event_config?.folder || ''}
                  onChange={(e) => onChange({ ...trigger, event_config: { ...trigger.event_config, folder: e.target.value } })}
                  placeholder="例如: C:\Downloads"
                />
              </div>
            )}
            {trigger.event_type === 'file_deleted' && (
              <div className="trigger-editor-field">
                <label>监控路径</label>
                <input
                  type="text"
                  value={trigger.event_config?.folder || ''}
                  onChange={(e) => onChange({ ...trigger, event_config: { ...trigger.event_config, folder: e.target.value } })}
                  placeholder="例如: C:\Downloads"
                />
              </div>
            )}
            {trigger.event_type === 'process_exit' && (
              <div className="trigger-editor-field">
                <label>进程名</label>
                <input
                  type="text"
                  value={trigger.event_config?.process_name || ''}
                  onChange={(e) => onChange({ ...trigger, event_config: { ...trigger.event_config, process_name: e.target.value } })}
                  placeholder="例如: notepad.exe"
                />
              </div>
            )}
            {trigger.event_type === 'process_start' && (
              <div className="trigger-editor-field">
                <label>进程名</label>
                <input
                  type="text"
                  value={trigger.event_config?.process_name || ''}
                  onChange={(e) => onChange({ ...trigger, event_config: { ...trigger.event_config, process_name: e.target.value } })}
                  placeholder="例如: chrome.exe"
                />
              </div>
            )}
          </>
        )}
        {trigger.type === 'startup' && (
          <div className="trigger-editor-hint" style={{ padding: '4px 0' }}>
            此脚本将在应用启动时自动执行
          </div>
        )}
      </div>
    </div>
  );
}

export function TriggerConfigPanel() {
  const scriptTriggers = useStore(s => s.scriptTriggers);
  const addScriptTrigger = useStore(s => s.addScriptTrigger);
  const removeScriptTrigger = useStore(s => s.removeScriptTrigger);
  const updateScriptTrigger = useStore(s => s.updateScriptTrigger);
  const showTriggerConfig = useStore(s => s.ui.show_trigger_config);
  const setUI = useStore(s => s.setUI);

  if (!showTriggerConfig) return null;

  const handleAddTimer = () => {
    addScriptTrigger({ type: 'interval', interval_seconds: 60 });
  };

  return (
    <div className="trigger-config-overlay">
      <div className="trigger-config-panel">
        <div className="trigger-config-title">
          <Zap size={14} />
          定时/触发配置 — 当前脚本
          <button className="btn btn-ghost btn-icon" onClick={() => setUI({ show_trigger_config: false })} style={{ marginLeft: 'auto' }}>
            <X size={14} />
          </button>
        </div>
        <div className="trigger-config-body">
          {scriptTriggers.length === 0 ? (
            <div className="trigger-config-empty">
              此脚本尚未配置触发条件。添加一个触发器使脚本可以定时或按事件自动执行。
            </div>
          ) : (
            scriptTriggers.map((t, i) => (
              <TriggerEditor
                key={i}
                trigger={t}
                onChange={(updated) => updateScriptTrigger(i, updated)}
                onRemove={() => removeScriptTrigger(i)}
              />
            ))
          )}
        </div>
        <div className="trigger-config-footer">
          <button className="btn btn-ghost" onClick={handleAddTimer} style={{ gap: 4 }}>
            <Plus size={14} />
            添加定时触发器
          </button>
        </div>
      </div>
    </div>
  );
}
