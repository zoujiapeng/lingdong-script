import React, { useState } from 'react';
import { FolderOpen, ZoomIn } from 'lucide-react';
import { SubScriptNodeParams } from '../../../types';
import { ScriptBrowser } from '../../common/ScriptBrowser';
import { useStore } from '../../../store';

interface Props {
  params: SubScriptNodeParams;
  onChange: (p: Partial<SubScriptNodeParams>) => void;
}

export function SubScriptParams({ params, onChange }: Props) {
  const [showBrowser, setShowBrowser] = useState(false);
  const hasSelection = params.script_name && params.script_name !== '未选择';

  const handleBrowseSelect = (entry: { id: string; name: string }) => {
    onChange({ script_id: entry.id, script_name: entry.name });
    setShowBrowser(false);
  };

  const handleZoomIn = () => {
    if (params.script_id) {
      useStore.getState().loadSubScript(params.script_id);
    }
  };

  return (
    <div>
      <div className="form-group">
        <label className="form-label">子脚本</label>
        <div className="form-row" style={{ gap: 8 }}>
          <button
            className="btn btn-ghost"
            style={{ flex: 1, justifyContent: 'center', gap: 6, padding: '8px 12px' }}
            onClick={() => setShowBrowser(true)}
          >
            <FolderOpen size={14} />
            {hasSelection ? params.script_name : '选择脚本...'}
          </button>
          {hasSelection && (
            <button className="btn btn-ghost" onClick={handleZoomIn} title="进入子流程" style={{ gap: 4 }}>
              <ZoomIn size={14} />
              进入
            </button>
          )}
        </div>
      </div>

      {hasSelection && (
        <div className="form-group">
          <label className="form-label">脚本 ID</label>
          <input className="form-input" value={params.script_id} onChange={(e) => onChange({ script_id: e.target.value })} />
        </div>
      )}

      <div className="form-group">
        <label className="form-label">描述</label>
        <input className="form-input" value={params.description || ''} onChange={(e) => onChange({ description: e.target.value })} placeholder="子脚本说明" />
      </div>

      {showBrowser && (
        <ScriptBrowser
          onSelect={handleBrowseSelect}
          onCancel={() => setShowBrowser(false)}
        />
      )}
    </div>
  );
}
