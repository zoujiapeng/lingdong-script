import React, { useState } from 'react';
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
import { CameraCaptureParams } from './params/CameraCaptureParams';
import { CameraRecordParams } from './params/CameraRecordParams';
import { CommonNodeSettings } from './params/CommonNodeSettings';
import { ArpNodeParams } from './params/ArpNodeParams';
import { getNodeIcon } from '../../utils/nodeIconMap';

function NodeIcon({ name, size = 16 }: { name: string; size?: number }) {
  const Icon = getNodeIcon(name);
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
    case 'agent_goal':
    case 'action_batch':
    case 'replay_step':
    case 'skill_replay':
    case 'evidence_gate':
    case 'human_gate':
      return <ArpNodeParams type={node.type} params={node.params as any} onChange={updateParams} />;
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
    case 'camera_capture': return <CameraCaptureParams params={node.params as any} onChange={updateParams} />;
    case 'camera_record': return <CameraRecordParams params={node.params as any} onChange={updateParams} />;
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
    // ── 聚合：进程操作 ──
    case 'process_action': {
      const p = node.params as any;
      return (
        <div>
          <div className="form-group"><label className="form-label">操作</label>
            <select className="form-input" value={p.action || 'launch'} onChange={(e) => updateParams({ ...p, action: e.target.value })}>
              <option value="launch">启动程序</option>
              <option value="kill">终止进程</option>
              <option value="wait_start">等待进程启动</option>
              <option value="wait_exit">等待进程退出</option>
              <option value="focus">激活窗口</option>
            </select>
          </div>
          {(p.action === 'launch') && (<>
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
          </>)}
          {(p.action === 'kill') && (<>
            <div className="form-group"><label className="form-label">进程名称</label>
              <input className="form-input" type="text" value={p.name || ''} placeholder="notepad.exe"
                onChange={(e) => updateParams({ ...p, name: e.target.value })} />
            </div>
            <div className="form-group"><label className="form-checkbox-label">
              <input type="checkbox" checked={p.force || false} onChange={(e) => updateParams({ ...p, force: e.target.checked })} style={{ marginRight: 6 }} />强制终止
            </label></div>
          </>)}
          {(p.action === 'wait_start' || p.action === 'wait_exit') && (<>
            <div className="form-group"><label className="form-label">进程名称</label>
              <input className="form-input" type="text" value={p.name || ''} placeholder="notepad.exe"
                onChange={(e) => updateParams({ ...p, name: e.target.value })} />
            </div>
            <div className="form-group"><label className="form-label">超时 (秒)</label>
              <input className="form-input" type="number" min={1} value={p.timeout ?? 30}
                onChange={(e) => updateParams({ ...p, timeout: parseInt(e.target.value) || 30 })} />
            </div>
          </>)}
          {(p.action === 'focus') && (<>
            <div className="form-group"><label className="form-label">窗口标题</label>
              <input className="form-input" type="text" value={p.title || ''} placeholder="记事本"
                onChange={(e) => updateParams({ ...p, title: e.target.value })} />
            </div>
          </>)}
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
    // ── 聚合类型参数面板 ──
    case 'system_power': {
      const p = node.params as any;
      const actions = [
        { value: 'shutdown', label: '关机' }, { value: 'reboot', label: '重启' },
        { value: 'logoff', label: '注销' }, { value: 'hibernate', label: '休眠' },
        { value: 'lock_workstation', label: '锁定' }, { value: 'system_sleep', label: '睡眠' },
        { value: 'display_off', label: '关闭显示器' },
      ];
      const descs: Record<string, string> = { shutdown:'关闭计算机', reboot:'重新启动', logoff:'注销当前用户', hibernate:'进入休眠', lock_workstation:'锁定工作站 (Win+L)', system_sleep:'进入睡眠状态', display_off:'关闭所有显示器' };
      return (
        <div>
          <div className="form-group"><label className="form-label">操作</label>
            <select className="form-input" value={p.action || 'shutdown'} onChange={(e) => updateParams({ ...p, action: e.target.value })}>
              {actions.map(a => <option key={a.value} value={a.value}>{a.label}</option>)}
            </select>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>{descs[p.action] || ''}</div>
          </div>
          {p.action !== 'logoff' && p.action !== 'hibernate' && p.action !== 'lock_workstation' && (
            <div className="form-group"><label className="form-label">延迟（秒）</label>
              <input className="form-input" type="number" min={0} value={p.delay_seconds ?? 0}
                onChange={(e) => updateParams({ ...p, delay_seconds: parseInt(e.target.value) || 0 })} />
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>0 表示立即执行</div>
            </div>
          )}
          {p.action !== 'display_off' && p.action !== 'system_sleep' && (
            <div className="form-group"><label className="form-checkbox-label">
              <input type="checkbox" checked={p.confirm !== false}
                onChange={(e) => updateParams({ ...p, confirm: e.target.checked })} style={{ marginRight: 6 }} />执行前确认
            </label></div>
          )}
        </div>
      );
    }
    case 'audio_action': {
      const p = node.params as any;
      return (
        <div>
          <div className="form-group"><label className="form-label">操作</label>
            <select className="form-input" value={p.action || 'volume_set'} onChange={(e) => updateParams({ ...p, action: e.target.value, level: 50, mute: undefined })}>
              <option value="volume_set">设置音量</option>
              <option value="volume_mute">静音控制</option>
            </select>
          </div>
          {p.action === 'volume_set' ? (
            <div className="form-group"><label className="form-label">音量 (%)</label>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <input className="form-input" type="range" min={0} max={100} value={p.level ?? 50}
                  onChange={(e) => updateParams({ ...p, level: parseInt(e.target.value) })} style={{ flex: 1 }} />
                <span style={{ minWidth: 32, textAlign: 'right' }}>{p.level ?? 50}%</span>
              </div>
            </div>
          ) : (
            <div className="form-group"><label className="form-label">静音操作</label>
              <select className="form-input" value={p.mute === null ? 'toggle' : p.mute ? 'mute' : 'unmute'}
                onChange={(e) => { const v = e.target.value; updateParams({ ...p, mute: v === 'toggle' ? null : v === 'mute' }); }}>
                <option value="toggle">切换静音</option>
                <option value="mute">静音</option>
                <option value="unmute">取消静音</option>
              </select>
            </div>
          )}
        </div>
      );
    }
    case 'desktop_action': {
      const p = node.params as any;
      return (
        <div>
          <div className="form-group"><label className="form-label">操作</label>
            <select className="form-input" value={p.action || 'show_desktop'} onChange={(e) => updateParams({ ...p, action: e.target.value, path: '' })}>
              <option value="show_desktop">显示桌面</option>
              <option value="set_wallpaper">设置壁纸</option>
              <option value="empty_recycle_bin">清空回收站</option>
            </select>
          </div>
          {p.action === 'show_desktop' && <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>最小化所有窗口，显示桌面 (Win+D)</div>}
          {p.action === 'set_wallpaper' && (
            <div className="form-group"><label className="form-label">图片路径</label>
              <input className="form-input" type="text" value={p.path || ''} placeholder="C:\\wallpaper.jpg"
                onChange={(e) => updateParams({ ...p, path: e.target.value })} />
            </div>
          )}
          {p.action === 'empty_recycle_bin' && (
            <div className="form-group"><label className="form-checkbox-label">
              <input type="checkbox" checked={p.confirm !== false}
                onChange={(e) => updateParams({ ...p, confirm: e.target.checked })} style={{ marginRight: 6 }} />执行前确认
            </label></div>
          )}
        </div>
      );
    }
    case 'clipboard_action': {
      const p = node.params as any;
      return (
        <div>
          <div className="form-group"><label className="form-label">操作</label>
            <select className="form-input" value={p.action || 'set'} onChange={(e) => updateParams({ ...p, action: e.target.value })}>
              <option value="set">写入剪贴板</option>
              <option value="get">读取剪贴板</option>
              <option value="get_image">获取剪贴板图片</option>
              <option value="get_files">获取剪贴板文件列表</option>
              <option value="clear">清空剪贴板</option>
            </select>
          </div>
          {p.action === 'set' ? (
            <div className="form-group"><label className="form-label">文本内容</label>
              <textarea className="form-input" rows={3} value={p.text || ''} placeholder="要写入的文本"
                onChange={(e) => updateParams({ ...p, text: e.target.value })} style={{ resize: 'vertical' }} />
            </div>
          ) : p.action === 'get_image' ? (<>
            <div className="form-group"><label className="form-label">输出格式</label>
              <select className="form-input" value={p.format_ || 'base64'} onChange={(e) => updateParams({ ...p, format_: e.target.value })}>
                <option value="base64">Base64 编码</option>
                <option value="file">保存到文件</option>
              </select>
            </div>
            {p.format_ === 'file' && (
              <div className="form-group"><label className="form-label">保存路径</label>
                <input className="form-input" type="text" value={p.save_path || ''} placeholder="C:\path\to\image.png"
                  onChange={(e) => updateParams({ ...p, save_path: e.target.value })} />
              </div>
            )}
          </>) : p.action === 'get_files' ? (
            <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>读取剪贴板中的文件路径列表（无参数）</div>
          ) : p.action === 'clear' ? (
            <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>清空系统剪贴板内容（无参数）</div>
          ) : (
            <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>读取系统剪贴板中的文本内容（无参数）</div>
          )}
        </div>
      );
    }
    case 'mouse_operation': {
      const p = node.params as any;
      const sub = p.sub_operation || 'click';
      return (
        <div>
          <div className="form-group"><label className="form-label">子操作</label>
            <select className="form-input" value={sub} onChange={(e) => updateParams({ ...p, sub_operation: e.target.value })}>
              <option value="click">点击</option>
              <option value="scroll">滚轮</option>
              <option value="hover">悬停</option>
              <option value="move">移动</option>
            </select>
          </div>
          {sub === 'click' && (<>
            <div className="form-group"><label className="form-label">点击模式</label>
              <select className="form-input" value={p.mode || 'coordinate'} onChange={(e) => updateParams({ ...p, mode: e.target.value })}>
                <option value="coordinate">坐标点击</option>
                <option value="text">文字识别</option>
              </select>
            </div>

            {/* === 坐标模式 === */}
            {p.mode === 'coordinate' && (<>
              <div style={{ display: 'flex', gap: 8 }}>
                <div className="form-group" style={{ flex: 1 }}><label className="form-label">X</label>
                  <input className="form-input" type="number" value={p.x ?? 0} onChange={(e) => updateParams({ ...p, x: parseInt(e.target.value) || 0 })} />
                </div>
                <div className="form-group" style={{ flex: 1 }}><label className="form-label">Y</label>
                  <input className="form-input" type="number" value={p.y ?? 0} onChange={(e) => updateParams({ ...p, y: parseInt(e.target.value) || 0 })} />
                </div>
              </div>
            </>)}

            {/* === 图片识别模式 === */}
            {p.mode === 'image' && (<>
              <div className="form-group"><label className="form-label">目标图片列表（优先级排序）</label>
                {(p.targets || []).map((t: any, i: number) => (
                  <div key={i} style={{ display: 'flex', gap: 4, marginBottom: 4, alignItems: 'center' }}>
                    <span style={{ cursor: 'grab', color: 'var(--text-muted)', fontSize: 16, userSelect: 'none' }}>⠿</span>
                    <span style={{ fontSize: 11, color: 'var(--text-muted)', minWidth: 20 }}>#{i + 1}</span>
                    <input className="form-input" type="text" value={t.path || ''} placeholder="图片路径"
                      onChange={(e) => { const targets = [...(p.targets || [])]; targets[i] = { ...targets[i], path: e.target.value }; updateParams({ ...p, targets }); }} style={{ flex: 1 }} />
                    <button onClick={() => { const targets = (p.targets || []).filter((_: any, j: number) => j !== i); updateParams({ ...p, targets }); }}
                      style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', fontSize: 16 }}>×</button>
                  </div>
                ))}
                <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
                  <button className="form-input" style={{ cursor: 'pointer', textAlign: 'center', flex: 1 }}
                    onClick={() => updateParams({ ...p, targets: [...(p.targets || []), { path: '' }] })}>+ 手动添加图片</button>
                  <button className="form-input" style={{ cursor: 'pointer', textAlign: 'center', flex: 1 }}
                    onClick={async () => {
                      try {
                        const win = window as any;
                        if (win.electronAPI?.selectRegion) {
                          const region = await win.electronAPI.selectRegion();
                          if (region && region.dataURL) {
                            const ts = new Date().toISOString().replace(/[:.]/g, '-');
                            const path = `框选_${ts}.png`;
                            updateParams({ ...p, targets: [...(p.targets || []), { path, base64: region.dataURL }] });
                            win.electronAPI.saveBase64Image?.(path, region.dataURL);
                          }
                        }
                      } catch (e) { /* 用户取消 */ }
                    }}>📷 框选截图添加</button>
                </div>
                <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
                  <button className="form-input" style={{ cursor: 'pointer', textAlign: 'center', flex: 1, fontSize: 12 }}
                    onClick={() => {
                      const targets = [...(p.targets || [])];
                      if (targets.length < 2) return;
                      // 上移选中的第一张
                      const idx = prompt('输入要上移的图片序号 (1-' + targets.length + '):');
                      if (idx) {
                        const i = parseInt(idx) - 1;
                        if (i > 0 && i < targets.length) {
                          [targets[i - 1], targets[i]] = [targets[i], targets[i - 1]];
                          updateParams({ ...p, targets });
                        }
                      }
                    }}>↑ 上移优先级</button>
                  <button className="form-input" style={{ cursor: 'pointer', textAlign: 'center', flex: 1, fontSize: 12 }}
                    onClick={() => {
                      const targets = [...(p.targets || [])];
                      if (targets.length < 2) return;
                      const idx = prompt('输入要下移的图片序号 (1-' + targets.length + '):');
                      if (idx) {
                        const i = parseInt(idx) - 1;
                        if (i >= 0 && i < targets.length - 1) {
                          [targets[i], targets[i + 1]] = [targets[i + 1], targets[i]];
                          updateParams({ ...p, targets });
                        }
                      }
                    }}>↓ 下移优先级</button>
                </div>
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <div className="form-group" style={{ flex: 1 }}><label className="form-label">相似度 %</label>
                  <input className="form-input" type="number" min={1} max={100} value={p.similarity ?? 90}
                    onChange={(e) => updateParams({ ...p, similarity: parseInt(e.target.value) || 90 })} />
                </div>
                <div className="form-group" style={{ flex: 1 }}><label className="form-label">超时（秒）</label>
                  <input className="form-input" type="number" min={1} max={120} value={p.timeout ?? 5}
                    onChange={(e) => updateParams({ ...p, timeout: parseInt(e.target.value) || 5 })} />
                </div>
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <div className="form-group" style={{ flex: 1 }}><label className="form-label">偏移 X</label>
                  <input className="form-input" type="number" value={p.offset_x ?? 0}
                    onChange={(e) => updateParams({ ...p, offset_x: parseInt(e.target.value) || 0 })} />
                </div>
                <div className="form-group" style={{ flex: 1 }}><label className="form-label">偏移 Y</label>
                  <input className="form-input" type="number" value={p.offset_y ?? 0}
                    onChange={(e) => updateParams({ ...p, offset_y: parseInt(e.target.value) || 0 })} />
                </div>
              </div>
              <div className="form-group"><label className="form-label">点击方式</label>
                <select className="form-input" value={p.method || 'single'} onChange={(e) => updateParams({ ...p, method: e.target.value })}>
                  <option value="single">单击</option>
                  <option value="double">双击</option>
                  <option value="right">右击</option>
                  <option value="middle">中键</option>
                  <option value="triple">三击</option>
                </select>
              </div>
              <div className="form-group" style={{ display: 'flex', gap: 16 }}>
                <label className="form-checkbox-label">
                  <input type="checkbox" checked={p.multi_scale !== false}
                    onChange={(e) => updateParams({ ...p, multi_scale: e.target.checked })} style={{ marginRight: 6 }} />多尺寸匹配
                </label>
                <label className="form-checkbox-label">
                  <input type="checkbox" checked={p.match_all === true}
                    onChange={(e) => updateParams({ ...p, match_all: e.target.checked })} style={{ marginRight: 6 }} />匹配全部
                </label>
              </div>
            </>)}

            {/* === 文字识别模式 === */}
            {p.mode === 'text' && (<>
              <div className="form-group"><label className="form-label">文字内容</label>
                <input className="form-input" type="text" value={p.text ?? ''} placeholder="要点击的文字"
                  onChange={(e) => updateParams({ ...p, text: e.target.value })} />
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <div className="form-group" style={{ flex: 1 }}><label className="form-label">相似度 %</label>
                  <input className="form-input" type="number" min={1} max={100} value={p.similarity ?? 80}
                    onChange={(e) => updateParams({ ...p, similarity: parseInt(e.target.value) || 80 })} />
                </div>
                <div className="form-group" style={{ flex: 1 }}><label className="form-label">超时（秒）</label>
                  <input className="form-input" type="number" min={1} max={120} value={p.timeout ?? 5}
                    onChange={(e) => updateParams({ ...p, timeout: parseInt(e.target.value) || 5 })} />
                </div>
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <div className="form-group" style={{ flex: 1 }}><label className="form-label">偏移 X</label>
                  <input className="form-input" type="number" value={p.offset_x ?? 0}
                    onChange={(e) => updateParams({ ...p, offset_x: parseInt(e.target.value) || 0 })} />
                </div>
                <div className="form-group" style={{ flex: 1 }}><label className="form-label">偏移 Y</label>
                  <input className="form-input" type="number" value={p.offset_y ?? 0}
                    onChange={(e) => updateParams({ ...p, offset_y: parseInt(e.target.value) || 0 })} />
                </div>
              </div>
              <div className="form-group"><label className="form-label">点击方式</label>
                <select className="form-input" value={p.method || 'single'} onChange={(e) => updateParams({ ...p, method: e.target.value })}>
                  <option value="single">单击</option>
                  <option value="double">双击</option>
                  <option value="right">右击</option>
                  <option value="middle">中键</option>
                  <option value="triple">三击</option>
                </select>
              </div>
            </>)}
          </>)}
          {sub === 'scroll' && (<>
            <div className="form-group"><label className="form-label">方向</label>
              <select className="form-input" value={p.scroll_direction || 'down'} onChange={(e) => updateParams({ ...p, scroll_direction: e.target.value })}>
                <option value="down">向下</option>
                <option value="up">向上</option>
                <option value="left">向左</option>
                <option value="right">向右</option>
              </select>
            </div>
            <div className="form-group"><label className="form-label">滚动格数</label>
              <input className="form-input" type="number" min={1} max={100} value={p.scroll_amount ?? 3}
                onChange={(e) => updateParams({ ...p, scroll_amount: parseInt(e.target.value) || 3 })} />
            </div>
          </>)}
          {sub === 'hover' && (<>
            <div style={{ display: 'flex', gap: 8 }}>
              <div className="form-group" style={{ flex: 1 }}><label className="form-label">X</label>
                <input className="form-input" type="number" value={p.hover_x ?? 0} onChange={(e) => updateParams({ ...p, hover_x: parseInt(e.target.value) || 0 })} />
              </div>
              <div className="form-group" style={{ flex: 1 }}><label className="form-label">Y</label>
                <input className="form-input" type="number" value={p.hover_y ?? 0} onChange={(e) => updateParams({ ...p, hover_y: parseInt(e.target.value) || 0 })} />
              </div>
            </div>
            <div className="form-group"><label className="form-label">悬停秒数</label>
              <input className="form-input" type="number" min={0} step={0.1} value={p.hover_duration ?? 0.5}
                onChange={(e) => updateParams({ ...p, hover_duration: parseFloat(e.target.value) || 0 })} />
            </div>
          </>)}
          {sub === 'move' && (<>
            <div style={{ display: 'flex', gap: 8 }}>
              <div className="form-group" style={{ flex: 1 }}><label className="form-label">X</label>
                <input className="form-input" type="number" value={p.move_x ?? 0} onChange={(e) => updateParams({ ...p, move_x: parseInt(e.target.value) || 0 })} />
              </div>
              <div className="form-group" style={{ flex: 1 }}><label className="form-label">Y</label>
                <input className="form-input" type="number" value={p.move_y ?? 0} onChange={(e) => updateParams({ ...p, move_y: parseInt(e.target.value) || 0 })} />
              </div>
            </div>
          </>)}
          <div className="form-group"><label className="form-label">操作后延迟（秒）</label>
            <input className="form-input" type="number" min={0} step={0.1} value={p.delay_after ?? 0.5}
              onChange={(e) => updateParams({ ...p, delay_after: parseFloat(e.target.value) || 0 })} />
          </div>
        </div>
      );
    }
    case 'dialog_action': {
      const p = node.params as any;
      const action = p.action || 'handle';
      return (
        <div>
          <div className="form-group"><label className="form-label">操作</label>
            <select className="form-input" value={action} onChange={(e) => updateParams({ ...p, action: e.target.value })}>
              <option value="handle">处理对话框</option>
              <option value="list">列出对话框</option>
              <option value="take_action">对话框操作（点击按钮）</option>
              <option value="fill_save">填写保存对话框</option>
            </select>
          </div>
          {action === 'handle' && (
            <div className="form-group"><label className="form-label">超时（秒）</label>
              <input className="form-input" type="number" min={0} value={p.timeout ?? 10}
                onChange={(e) => updateParams({ ...p, timeout: parseInt(e.target.value) || 10 })} />
            </div>
          )}
          {action === 'take_action' && (<>
            <div className="form-group"><label className="form-label">按钮文字</label>
              <input className="form-input" type="text" value={p.button || ''} placeholder="保存、确定、取消等"
                onChange={(e) => updateParams({ ...p, button: e.target.value })} />
            </div>
            <div className="form-group"><label className="form-label">超时（秒）</label>
              <input className="form-input" type="number" min={0} value={p.timeout ?? 10}
                onChange={(e) => updateParams({ ...p, timeout: parseInt(e.target.value) || 10 })} />
            </div>
          </>)}
          {action === 'fill_save' && (<>
            <div className="form-group"><label className="form-label">保存路径</label>
              <input className="form-input" type="text" value={p.path || ''} placeholder="C:\\path\\file.txt"
                onChange={(e) => updateParams({ ...p, path: e.target.value })} />
            </div>
            <div className="form-group"><label className="form-label">超时（秒）</label>
              <input className="form-input" type="number" min={0} value={p.timeout ?? 10}
                onChange={(e) => updateParams({ ...p, timeout: parseInt(e.target.value) || 10 })} />
            </div>
          </>)}
        </div>
      );
    }
    case 'a11y_action': {
      const p = node.params as any;
      const action = p.action || 'get_tree';
      return (
        <div>
          <div className="form-group"><label className="form-label">操作</label>
            <select className="form-input" value={action} onChange={(e) => updateParams({ ...p, action: e.target.value })}>
              <option value="get_tree">获取无障碍树</option>
              <option value="find_element">查找元素</option>
              <option value="click_element">点击元素</option>
            </select>
          </div>
          {action === 'get_tree' && (<>
            <div className="form-group"><label className="form-label">最大深度</label>
              <input className="form-input" type="number" min={1} max={10} value={p.max_depth ?? 5}
                onChange={(e) => updateParams({ ...p, max_depth: parseInt(e.target.value) || 5 })} />
            </div>
            <div className="form-group"><label className="form-checkbox-label">
              <input type="checkbox" checked={p.filter_visible !== false}
                onChange={(e) => updateParams({ ...p, filter_visible: e.target.checked })} style={{ marginRight: 6 }} />仅可见元素
            </label></div>
          </>)}
          {(action === 'find_element' || action === 'click_element') && (<>
            <div className="form-group"><label className="form-label">元素名称</label>
              <input className="form-input" type="text" value={p.name || ''} placeholder="确定、保存、搜索等"
                onChange={(e) => updateParams({ ...p, name: e.target.value })} />
            </div>
            <div className="form-group"><label className="form-label">角色（可选）</label>
              <input className="form-input" type="text" value={p.role || ''} placeholder="button, edit, list"
                onChange={(e) => updateParams({ ...p, role: e.target.value })} />
            </div>
            {action === 'find_element' && (
              <div className="form-group"><label className="form-label">控件类型（可选）</label>
                <input className="form-input" type="text" value={p.control_type || ''} placeholder="Edit, Button, ComboBox"
                  onChange={(e) => updateParams({ ...p, control_type: e.target.value })} />
              </div>
            )}
          </>)}
        </div>
      );
    }
    case 'wifi_action': {
      const p = node.params as any;
      const action = p.action || 'list';
      return (
        <div>
          <div className="form-group"><label className="form-label">操作</label>
            <select className="form-input" value={action} onChange={(e) => updateParams({ ...p, action: e.target.value })}>
              <option value="list">列出网络</option>
              <option value="connect">连接 WiFi</option>
              <option value="disconnect">断开 WiFi</option>
            </select>
          </div>
          {action === 'connect' && (<>
            <div className="form-group"><label className="form-label">WiFi 名称 (SSID)</label>
              <input className="form-input" type="text" value={p.ssid || ''} placeholder="MyWiFi"
                onChange={(e) => updateParams({ ...p, ssid: e.target.value })} />
            </div>
            <div className="form-group"><label className="form-label">密码（可选）</label>
              <input className="form-input" type="password" value={p.password || ''} placeholder="留空表示无需密码"
                onChange={(e) => updateParams({ ...p, password: e.target.value })} />
            </div>
          </>)}
        </div>
      );
    }
    case 'window_control': {
      const p = node.params as any;
      return (
        <div>
          <div className="form-group"><label className="form-label">操作类型</label>
            <select className="form-input" value={p.operation || 'minimize'}
              onChange={(e) => updateParams({ ...p, operation: e.target.value })}>
              <option value="minimize">最小化</option>
              <option value="maximize">最大化</option>
              <option value="restore">还原</option>
              <option value="close">关闭</option>
              <option value="resize">调整大小</option>
              <option value="snap">贴靠</option>
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
          {p.operation === 'snap' && (
            <div className="form-group"><label className="form-label">贴靠位置</label>
              <select className="form-input" value={p.snap_position || 'left'}
                onChange={(e) => updateParams({ ...p, snap_position: e.target.value })}>
                <option value="left">左半屏</option>
                <option value="right">右半屏</option>
                <option value="top">上半屏</option>
                <option value="bottom">下半屏</option>
                <option value="top_left">左上角</option>
                <option value="top_right">右上角</option>
                <option value="bottom_left">左下角</option>
                <option value="bottom_right">右下角</option>
              </select>
            </div>
          )}
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

