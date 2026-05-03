import React, { useState } from 'react';
import { ConditionNodeParams, ConditionType } from '../../../types';
import { Crosshair } from 'lucide-react';

interface Props {
  params: ConditionNodeParams;
  onChange: (p: Partial<ConditionNodeParams>) => void;
}

const CONDITION_OPTIONS: { value: ConditionType; label: string }[] = [
  { value: 'image_exists', label: '图片存在' },
  { value: 'window_exists', label: '窗口存在' },
  { value: 'variable_equals', label: '变量等于' },
  { value: 'variable_greater', label: '变量大于' },
  { value: 'variable_less', label: '变量小于' },
  { value: 'color_match', label: '颜色匹配' },
  { value: 'scene_match', label: '场景匹配' },
  { value: 'always_true', label: '始终为真' },
];

export function ConditionParams({ params, onChange }: Props) {
  const [selecting, setSelecting] = useState(false);

  const handleSelectRegion = async () => {
    try {
      setSelecting(true);
      const api = (window as any).electronAPI;
      if (api?.selectRegion) {
        const result = await api.selectRegion();
        if (result && typeof result.x === 'number') {
          onChange({ x: result.x, y: result.y, w: result.w, h: result.h });
        }
      }
    } catch (err: any) {
      console.error('选区失败:', err);
    } finally {
      setSelecting(false);
    }
  };

  return (
    <div>
      <div className="form-group">
        <label className="form-label">条件类型</label>
        <select className="form-input form-select" value={params.condition_type} onChange={(e) => onChange({ condition_type: e.target.value as ConditionType })}>
          {CONDITION_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
      </div>

      {(params.condition_type === 'image_exists') && (
        <>
          <div className="form-group">
            <label className="form-label">目标图片</label>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', padding: '12px', border: '1px dashed var(--border-primary)', borderRadius: 'var(--radius-md)', textAlign: 'center' }}>
              暂未实现文件选择器
            </div>
          </div>
          <div className="form-group">
            <label className="form-label">相似度 ({params.similarity || 85}%)</label>
            <div className="slider-group">
              <input type="range" min="50" max="100" value={params.similarity || 85} onChange={(e) => onChange({ similarity: +e.target.value })} />
              <span className="slider-value">{params.similarity || 85}%</span>
            </div>
          </div>
        </>
      )}

      {params.condition_type === 'window_exists' && (
        <div className="form-group">
          <label className="form-label">窗口标题</label>
          <input className="form-input" value={params.window_title || ''} onChange={(e) => onChange({ window_title: e.target.value })} placeholder="例如: 计算器" />
        </div>
      )}

      {(params.condition_type === 'variable_equals' || params.condition_type === 'variable_greater' || params.condition_type === 'variable_less') && (
        <>
          <div className="form-group">
            <label className="form-label">变量名</label>
            <input className="form-input" value={params.variable_name || ''} onChange={(e) => onChange({ variable_name: e.target.value })} placeholder="变量名" />
          </div>
          <div className="form-group">
            <label className="form-label">比较值</label>
            <input className="form-input" value={params.variable_value || ''} onChange={(e) => onChange({ variable_value: e.target.value })} placeholder="比较值" />
          </div>
        </>
      )}

      {params.condition_type === 'color_match' && (
        <>
          <div className="form-group">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <label className="form-label" style={{ margin: 0 }}>检测区域</label>
              <button className="btn btn-ghost btn-icon" onClick={handleSelectRegion} disabled={selecting} title="框选检测区域" style={{ width: 28, height: 28 }}>
                <Crosshair size={14} />
              </button>
            </div>
            <div className="form-row">
              <div className="form-group" style={{ flex: 1 }}>
                <label className="form-label" style={{ fontSize: 11 }}>X</label>
                <input className="form-input" type="number" value={(params as any).x || 0} onChange={(e) => onChange({ x: +e.target.value })} />
              </div>
              <div className="form-group" style={{ flex: 1 }}>
                <label className="form-label" style={{ fontSize: 11 }}>Y</label>
                <input className="form-input" type="number" value={(params as any).y || 0} onChange={(e) => onChange({ y: +e.target.value })} />
              </div>
              <div className="form-group" style={{ flex: 1 }}>
                <label className="form-label" style={{ fontSize: 11 }}>宽度</label>
                <input className="form-input" type="number" value={(params as any).w || 100} onChange={(e) => onChange({ w: +e.target.value })} />
              </div>
              <div className="form-group" style={{ flex: 1 }}>
                <label className="form-label" style={{ fontSize: 11 }}>高度</label>
                <input className="form-input" type="number" value={(params as any).h || 100} onChange={(e) => onChange({ h: +e.target.value })} />
              </div>
            </div>
          </div>
          <div className="form-group">
            <label className="form-label">期望颜色 (HSV)</label>
            <input className="form-input" value={(params as any).expected_color || ''} onChange={(e) => onChange({ expected_color: e.target.value })} placeholder="H,S,V 例如: 30,200,200" />
          </div>
          <div className="form-group">
            <label className="form-label">容差</label>
            <div className="slider-group">
              <input type="range" min="0" max="100" value={(params as any).tolerance || 30} onChange={(e) => onChange({ tolerance: +e.target.value })} />
              <span className="slider-value">{(params as any).tolerance || 30}</span>
            </div>
          </div>
        </>
      )}

      {params.condition_type === 'scene_match' && (
        <div className="form-group">
          <label className="form-label">场景名称</label>
          <input className="form-input" value={(params as any).scene_name || ''} onChange={(e) => onChange({ scene_name: e.target.value })} placeholder="例如: 主界面, 战斗" />
        </div>
      )}

      <div className="form-group">
        <label className="form-label">描述</label>
        <input className="form-input" value={params.description || ''} onChange={(e) => onChange({ description: e.target.value })} placeholder="条件说明" />
      </div>
    </div>
  );
}
