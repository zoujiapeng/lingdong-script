import React from 'react';
import { FlowNode } from '../../../types';

interface Props {
  node: FlowNode;
  updateNode: (updates: Partial<FlowNode>) => void;
}

export function CommonNodeSettings({ node, updateNode }: Props) {
  return (
    <div>
      <div className="form-group">
        <label className="form-label">节点标签</label>
        <input
          className="form-input"
          value={node.label || ''}
          onChange={(e) => updateNode({ label: e.target.value })}
          placeholder="自定义标签（可选）"
        />
      </div>

      <div className="toggle-group">
        <span className="toggle-label">禁用此节点</span>
        <button
          className={`toggle ${node.disabled ? 'on' : ''}`}
          onClick={() => updateNode({ disabled: !node.disabled })}
        />
      </div>

      <div className="toggle-group">
        <span className="toggle-label">折叠显示</span>
        <button
          className={`toggle ${node.collapsed ? 'on' : ''}`}
          onClick={() => updateNode({ collapsed: !node.collapsed })}
        />
      </div>

      <hr className="section-divider" />

      <div style={{ fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.6 }}>
        <div><strong>节点 ID:</strong> <code style={{ fontSize: 10 }}>{node.id}</code></div>
        <div><strong>类型:</strong> {node.type}</div>
        <div style={{ marginTop: 4 }}>
          <strong>位置:</strong> ({Math.round(node.x)}, {Math.round(node.y)})
        </div>
      </div>
    </div>
  );
}
