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
import { CommonNodeSettings } from './params/CommonNodeSettings';

function NodeIcon({ name, size = 16 }: { name: string; size?: number }) {
  const Icon = (LucideIcons as any)[name];
  if (!Icon) return null;
  return <Icon size={size} />;
}

export function PropertyPanel({ forcedVisible = false }: { forcedVisible?: boolean }) {
  const { script, ui, updateNode, updateNodeParams } = useStore();
  const [activeTab, setActiveTab] = useState<'params' | 'advanced'>('params');

  const selectedNodeId = ui.selection.nodes[0];
  const node = selectedNodeId ? script.nodes.find((n) => n.id === selectedNodeId) : null;

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

