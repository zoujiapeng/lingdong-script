import React, { useState } from 'react';
import * as LucideIcons from 'lucide-react';
import { MousePointer2, Settings, Info } from 'lucide-react';
import { useStore } from '../../store';
import { NODE_DEFINITIONS } from '../../utils/nodeDefinitions';
import { FlowNode } from '../../types';
import { ClickParams } from './params/ClickParams';
import { ImageClickParams } from './params/ImageClickParams';
import { ClickTextParams } from './params/ClickTextParams';
import { WaitParams } from './params/WaitParams';
import { InputTextParams } from './params/InputTextParams';
import { KeyboardParams } from './params/KeyboardParams';
import { SwipeParams } from './params/SwipeParams';
import { ConditionParams } from './params/ConditionParams';
import { LoopParams } from './params/LoopParams';
import { SubScriptParams } from './params/SubScriptParams';
import { VariableParams } from './params/VariableParams';
import { PopupCloseParams } from './params/PopupCloseParams';
import { ColorCheckParams } from './params/ColorCheckParams';
import { ProgressWaitParams } from './params/ProgressWaitParams';
import { RegionWaitParams } from './params/RegionWaitParams';
import { SceneConditionParams } from './params/SceneConditionParams';
import { ScreenshotParams } from './params/ScreenshotParams';
import { ToolCallParams } from './params/ToolCallParams';
import { WebBrowserParams } from './params/WebBrowserParams';
import { CommonNodeSettings } from './params/CommonNodeSettings';

function NodeIcon({ name, size = 16 }: { name: string; size?: number }) {
  const Icon = (LucideIcons as any)[name];
  if (!Icon) return null;
  return <Icon size={size} />;
}

export function PropertyPanel() {
  const { script, ui, updateNode, updateNodeParams } = useStore();
  const [activeTab, setActiveTab] = useState<'params' | 'advanced'>('params');

  const selectedNodeId = ui.selection.nodes[0];
  const node = selectedNodeId ? script.nodes.find((n) => n.id === selectedNodeId) : null;
  const forcedVisible = ui.show_property_panel || ui.panels_visible;

  const panelClass = `property-panel${forcedVisible ? ' forced-visible' : ''}`;

  if (!node) {
    return (
      <div className={panelClass}>
        <div className="property-empty" style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
          <div className="property-empty-icon">
            <MousePointer2 size={22} />
          </div>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)', marginTop: 12 }}>
            未选择节点
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 6, textAlign: 'center', lineHeight: 1.6 }}>
            点击画布中的节点<br />来编辑其参数
          </div>
        </div>
      </div>
    );
  }

  const def = NODE_DEFINITIONS[node.type];

  return (
    <div className={panelClass}>
      {/* Header */}
      <div className="property-panel-header">
        <div className="property-panel-icon" style={{ background: def.color }}>
          <NodeIcon name={def.icon} size={16} />
        </div>
        <div className="property-panel-title">
          <div className="property-panel-type">{def.label}</div>
          <input
            className="property-panel-name"
            style={{ background: 'transparent', border: 'none', outline: 'none', fontFamily: 'Outfit, sans-serif', fontWeight: 600, fontSize: 14, color: 'var(--text-primary)', width: '100%' }}
            value={node.label || def.label}
            onChange={(e) => updateNode(node.id, { label: e.target.value })}
            placeholder={def.label}
          />
        </div>
      </div>

      {/* Tabs */}
      <div className="property-tabs">
        <button
          className={`property-tab ${activeTab === 'params' ? 'active' : ''}`}
          onClick={() => setActiveTab('params')}
        >
          参数设置
        </button>
        <button
          className={`property-tab ${activeTab === 'advanced' ? 'active' : ''}`}
          onClick={() => setActiveTab('advanced')}
        >
          高级设置
        </button>
      </div>

      {/* Body */}
      <div className="property-body">
        {activeTab === 'params' ? (
          <NodeParamEditor node={node} updateParams={(p) => updateNodeParams(node.id, p)} />
        ) : (
          <CommonNodeSettings node={node} updateNode={(u) => updateNode(node.id, u)} />
        )}

        {/* Usage hint */}
        <div className="usage-hint" style={{ marginTop: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4, fontWeight: 600 }}>
            <Info size={12} />
            使用说明
          </div>
          {def.description}
        </div>
      </div>
    </div>
  );
}

function NodeParamEditor({ node, updateParams }: { node: FlowNode; updateParams: (p: any) => void }) {
  switch (node.type) {
    case 'click': return <ClickParams params={node.params as any} onChange={updateParams} />;
    case 'image_click': return <ImageClickParams params={node.params as any} onChange={updateParams} />;
    case 'click_text': return <ClickTextParams params={node.params as any} onChange={updateParams} />;
    case 'wait': return <WaitParams params={node.params as any} onChange={updateParams} />;
    case 'input_text': return <InputTextParams params={node.params as any} onChange={updateParams} />;
    case 'keyboard': return <KeyboardParams params={node.params as any} onChange={updateParams} />;
    case 'swipe': return <SwipeParams params={node.params as any} onChange={updateParams} />;
    case 'condition': return <ConditionParams params={node.params as any} onChange={updateParams} />;
    case 'loop': return <LoopParams params={node.params as any} onChange={updateParams} />;
    case 'sub_script': return <SubScriptParams params={node.params as any} onChange={updateParams} />;
    case 'variable_set': return <VariableParams params={node.params as any} onChange={updateParams} mode="set" />;
    case 'variable_get': return <VariableParams params={node.params as any} onChange={updateParams} mode="get" />;
    case 'popup_close': return <PopupCloseParams params={node.params as any} onChange={updateParams} />;
    case 'color_check': return <ColorCheckParams params={node.params as any} onChange={updateParams} />;
    case 'progress_wait': return <ProgressWaitParams params={node.params as any} onChange={updateParams} />;
    case 'region_wait': return <RegionWaitParams params={node.params as any} onChange={updateParams} />;
    case 'scene_condition': return <SceneConditionParams params={node.params as any} onChange={updateParams} />;
    case 'screenshot': return <ScreenshotParams params={node.params as any} onChange={updateParams} />;
    case 'web_browser': return <WebBrowserParams params={node.params as any} onChange={updateParams} />;
    case 'tool_call': return <ToolCallParams params={node.params as any} onChange={updateParams} />;
    case 'shutdown':
    case 'reboot':
    case 'logoff':
    case 'hibernate': {
      const p = node.params as any;
      const labels: Record<string, string> = {
        shutdown: '关机', reboot: '重启', logoff: '注销', hibernate: '休眠',
      };
      return (
        <div>
          <div className="form-group">
            <label className="form-label">{labels[node.type]}操作</label>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 8 }}>
              {node.type === 'shutdown' ? '关闭计算机' :
               node.type === 'reboot' ? '重新启动计算机' :
               node.type === 'logoff' ? '注销当前用户' : '使计算机进入休眠状态'}
            </div>
          </div>
          {node.type !== 'logoff' && node.type !== 'hibernate' && (
            <div className="form-group">
              <label className="form-label">延迟（秒）</label>
              <input
                className="form-input"
                type="number"
                min={0}
                value={p.delay_seconds ?? 0}
                onChange={(e) => updateParams({ ...p, delay_seconds: parseInt(e.target.value) || 0 })}
              />
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
                0 表示立即{labels[node.type]}
              </div>
            </div>
          )}
          <div className="form-group">
            <label className="form-checkbox-label">
              <input
                type="checkbox"
                checked={p.confirm !== false}
                onChange={(e) => updateParams({ ...p, confirm: e.target.checked })}
                style={{ marginRight: 6 }}
              />
              执行前确认
            </label>
          </div>
        </div>
      );
    }
    // ── 扩展系统指令参数面板 ──
    case 'lock_workstation': {
      const p = node.params as any;
      return (
        <div>
          <div className="form-group"><label className="form-label">锁定工作站</label>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 8 }}>锁定当前工作站 (Win+L)</div>
          </div>
          <div className="form-group"><label className="form-checkbox-label">
            <input type="checkbox" checked={p.confirm !== false} onChange={(e) => updateParams({ ...p, confirm: e.target.checked })} style={{ marginRight: 6 }} />执行前确认
          </label></div>
        </div>
      );
    }
    case 'volume_set': {
      const p = node.params as any;
      return (
        <div>
          <div className="form-group"><label className="form-label">音量 (%)</label>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <input className="form-input" type="range" min={0} max={100} value={p.level ?? 50}
                onChange={(e) => updateParams({ ...p, level: parseInt(e.target.value) })} style={{ flex: 1 }} />
              <span style={{ minWidth: 32, textAlign: 'right' }}>{p.level ?? 50}%</span>
            </div>
          </div>
        </div>
      );
    }
    case 'volume_mute': {
      const p = node.params as any;
      return (
        <div>
          <div className="form-group"><label className="form-label">静音操作</label>
            <select className="form-input" value={p.mute === null ? 'toggle' : p.mute ? 'mute' : 'unmute'}
              onChange={(e) => { const v = e.target.value; updateParams({ ...p, mute: v === 'toggle' ? null : v === 'mute' }); }}>
              <option value="toggle">切换静音</option>
              <option value="mute">静音</option>
              <option value="unmute">取消静音</option>
            </select>
          </div>
        </div>
      );
    }
    case 'launch_program': {
      const p = node.params as any;
      return (
        <div>
          <div className="form-group"><label className="form-label">程序路径/URL</label>
            <input className="form-input" type="text" value={p.target || ''} placeholder="notepad.exe"
              onChange={(e) => updateParams({ ...p, target: e.target.value })} />
          </div>
          <div className="form-group"><label className="form-label">启动参数</label>
            <input className="form-input" type="text" value={p.args || ''} placeholder="（可选）"
              onChange={(e) => updateParams({ ...p, args: e.target.value })} />
          </div>
          <div className="form-group"><label className="form-checkbox-label">
            <input type="checkbox" checked={p.wait || false} onChange={(e) => updateParams({ ...p, wait: e.target.checked })} style={{ marginRight: 6 }} />等待程序退出
          </label></div>
        </div>
      );
    }
    case 'kill_process': {
      const p = node.params as any;
      return (
        <div>
          <div className="form-group"><label className="form-label">进程名称</label>
            <input className="form-input" type="text" value={p.name || ''} placeholder="notepad.exe"
              onChange={(e) => updateParams({ ...p, name: e.target.value })} />
          </div>
          <div className="form-group"><label className="form-checkbox-label">
            <input type="checkbox" checked={p.force || false} onChange={(e) => updateParams({ ...p, force: e.target.checked })} style={{ marginRight: 6 }} />强制终止
          </label></div>
        </div>
      );
    }
    case 'wait_process': {
      const p = node.params as any;
      return (
        <div>
          <div className="form-group"><label className="form-label">进程名称</label>
            <input className="form-input" type="text" value={p.name || ''} placeholder="notepad.exe"
              onChange={(e) => updateParams({ ...p, name: e.target.value })} />
          </div>
          <div className="form-group"><label className="form-label">等待类型</label>
            <select className="form-input" value={p.wait_for || 'start'}
              onChange={(e) => updateParams({ ...p, wait_for: e.target.value })}>
              <option value="start">等待启动</option>
              <option value="exit">等待退出</option>
            </select>
          </div>
          <div className="form-group"><label className="form-label">超时 (秒)</label>
            <input className="form-input" type="number" min={1} value={p.timeout ?? 30}
              onChange={(e) => updateParams({ ...p, timeout: parseInt(e.target.value) || 30 })} />
          </div>
        </div>
      );
    }
    case 'focus_window': {
      const p = node.params as any;
      return (
        <div>
          <div className="form-group"><label className="form-label">窗口标题</label>
            <input className="form-input" type="text" value={p.title || ''} placeholder="记事本"
              onChange={(e) => updateParams({ ...p, title: e.target.value })} />
          </div>
          <div className="form-group"><label className="form-checkbox-label">
            <input type="checkbox" checked={p.exact || false} onChange={(e) => updateParams({ ...p, exact: e.target.checked })} style={{ marginRight: 6 }} />精确匹配
          </label></div>
        </div>
      );
    }
    case 'set_wallpaper': {
      const p = node.params as any;
      return (
        <div>
          <div className="form-group"><label className="form-label">图片路径</label>
            <input className="form-input" type="text" value={p.path || ''} placeholder="C:\\wallpaper.jpg"
              onChange={(e) => updateParams({ ...p, path: e.target.value })} />
          </div>
          <div className="form-group"><label className="form-label">填充方式</label>
            <select className="form-input" value={p.style || 'fill'}
              onChange={(e) => updateParams({ ...p, style: e.target.value })}>
              <option value="fill">填充</option>
              <option value="fit">适应</option>
              <option value="stretch">拉伸</option>
              <option value="tile">平铺</option>
              <option value="center">居中</option>
            </select>
          </div>
        </div>
      );
    }
    case 'display_off': {
      const p = node.params as any;
      return (
        <div>
          <div className="form-group"><label className="form-label">关闭显示器</label>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 8 }}>关闭所有显示器，移动鼠标或按任意键恢复</div>
          </div>
          <div className="form-group"><label className="form-checkbox-label">
            <input type="checkbox" checked={p.confirm !== false} onChange={(e) => updateParams({ ...p, confirm: e.target.checked })} style={{ marginRight: 6 }} />执行前确认
          </label></div>
        </div>
      );
    }
    case 'system_sleep': {
      const p = node.params as any;
      return (
        <div>
          <div className="form-group"><label className="form-label">睡眠</label>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 8 }}>使计算机进入睡眠状态</div>
          </div>
          <div className="form-group"><label className="form-checkbox-label">
            <input type="checkbox" checked={p.confirm !== false} onChange={(e) => updateParams({ ...p, confirm: e.target.checked })} style={{ marginRight: 6 }} />执行前确认
          </label></div>
        </div>
      );
    }
    case 'show_desktop':
      return <div><div className="form-group"><label className="form-label">显示桌面</label>
        <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>最小化所有窗口，显示桌面 (Win+D)</div></div></div>;
    case 'battery_status':
      return <div><div className="form-group"><label className="form-label">读取电池状态</label>
        <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>获取电池电量、充电状态等信息（无参数）</div></div></div>;
    case 'empty_recycle_bin': {
      const p = node.params as any;
      return (
        <div>
          <div className="form-group"><label className="form-label">清空回收站</label>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 8 }}>释放磁盘空间</div>
          </div>
          <div className="form-group"><label className="form-checkbox-label">
            <input type="checkbox" checked={p.confirm !== false} onChange={(e) => updateParams({ ...p, confirm: e.target.checked })} style={{ marginRight: 6 }} />执行前确认
          </label></div>
        </div>
      );
    }
    case 'display_brightness': {
      const p = node.params as any;
      return (
        <div>
          <div className="form-group"><label className="form-label">亮度 (%)</label>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <input className="form-input" type="range" min={0} max={100} value={p.level ?? 80}
                onChange={(e) => updateParams({ ...p, level: parseInt(e.target.value) })} style={{ flex: 1 }} />
              <span style={{ minWidth: 32, textAlign: 'right' }}>{p.level ?? 80}%</span>
            </div>
          </div>
        </div>
      );
    }
    case 'clipboard_set': {
      const p = node.params as any;
      return (
        <div>
          <div className="form-group"><label className="form-label">剪贴板文本</label>
            <textarea className="form-input" rows={3} value={p.text || ''} placeholder="要写入的文本"
              onChange={(e) => updateParams({ ...p, text: e.target.value })} style={{ resize: 'vertical' }} />
          </div>
        </div>
      );
    }
    case 'clipboard_get':
      return <div><div className="form-group"><label className="form-label">读取剪贴板</label>
        <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>读取系统剪贴板中的文本内容（无参数）</div></div></div>;
    case 'window_control': {
      const p = node.params as any;
      return (
        <div>
          <div className="form-group"><label className="form-label">操作类型</label>
            <select className="form-input" value={p.operation || 'minimize'}
              onChange={(e) => updateParams({ ...p, operation: e.target.value })}>
              <option value="minimize">最小化</option>
              <option value="maximize">最大化</option>
              <option value="close">关闭</option>
              <option value="resize">调整大小</option>
            </select>
          </div>
          <div className="form-group"><label className="form-label">窗口标题（留空=当前窗口）</label>
            <input className="form-input" type="text" value={p.title || ''} placeholder="留空则操作当前窗口"
              onChange={(e) => updateParams({ ...p, title: e.target.value })} />
          </div>
          {p.operation === 'resize' && (<>
            <div className="form-group"><label className="form-label">宽度 (px)</label>
              <input className="form-input" type="number" min={100} value={p.width ?? 800}
                onChange={(e) => updateParams({ ...p, width: parseInt(e.target.value) || 800 })} />
            </div>
            <div className="form-group"><label className="form-label">高度 (px)</label>
              <input className="form-input" type="number" min={100} value={p.height ?? 600}
                onChange={(e) => updateParams({ ...p, height: parseInt(e.target.value) || 600 })} />
            </div>
          </>)}
        </div>
      );
    }
    case 'start':
    case 'end': return (
      <div className="form-group">
        <label className="form-label">标签文本</label>
        <input
          className="form-input"
          value={(node.params as any).label || ''}
          onChange={(e) => updateParams({ label: e.target.value })}
        />
      </div>
    );
    default: return <div style={{ color: 'var(--text-muted)', fontSize: 13 }}>该节点类型暂无参数</div>;
  }
}

