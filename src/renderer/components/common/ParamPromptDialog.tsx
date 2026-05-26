import React, { useState, useEffect } from 'react';
import { useStore } from '../../store';
import { Crosshair, FolderOpen, Check } from 'lucide-react';

const OVERLAY_STYLE: React.CSSProperties = {
  position: 'fixed', inset: 0, zIndex: 1000,
  background: 'rgba(0,0,0,0.4)',
  display: 'flex', alignItems: 'center', justifyContent: 'center',
};

const DIALOG_STYLE: React.CSSProperties = {
  background: 'var(--bg-primary)', borderRadius: 12,
  boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
  width: 520, maxHeight: '85vh', display: 'flex', flexDirection: 'column',
};

const NODE_TYPE_LABELS: Record<string, string> = {
  click: '点击', input_text: '输入文字', keyboard: '快捷键', swipe: '滑动',
  wait: '等待', condition: '条件分支', loop: '循环', screenshot: '截图',
  image_click: '图片点击', click_text: '文字点击', color_check: '颜色检测',
  region_wait: '区域等待', progress_wait: '进度条等待', scene_condition: '场景判断',
  variable_set: '变量设置', tool_call: '工具调用', sub_script: '子流程',
};

type FilledMap = Record<string, boolean>;

export function ParamPromptDialog() {
  const show = useStore((s) => s.showParamPrompt);
  const queue = useStore((s) => s.paramPromptQueue);
  const cancelParamPrompt = useStore((s) => s.cancelParamPrompt);
  const finishParamPrompt = useStore((s) => s.finishParamPrompt);
  const updateNodeParams = useStore((s) => s.updateNodeParams);
  const addLog = useStore((s) => s.addLog);

  const [filled, setFilled] = useState<FilledMap>({});
  const [regionValues, setRegionValues] = useState<Record<string, { x: number; y: number; w: number; h: number }>>({});
  const [textValues, setTextValues] = useState<Record<string, string>>({});
  const [selectingId, setSelectingId] = useState<string | null>(null);

  // Reset local state when queue changes
  useEffect(() => {
    setFilled({});
    setRegionValues({});
    setTextValues({});
    setSelectingId(null);
  }, [queue]);

  if (!show || queue.length === 0) return null;

  const total = queue.length;
  const filledCount = Object.keys(filled).length;
  const allFilled = filledCount >= total;

  const itemKey = (item: typeof queue[0]) => `${item.nodeId}_${item.params.join(',')}`;

  const markFilled = (key: string) => {
    setFilled(prev => ({ ...prev, [key]: true }));
  };

  const handleRegionSelect = async (item: typeof queue[0]) => {
    try {
      setSelectingId(item.nodeId);
      const api = (window as any).electronAPI;
      if (api?.selectRegion) {
        const result = await api.selectRegion();
        if (result && typeof result.x === 'number') {
          const vals = { x: result.x, y: result.y, w: result.w, h: result.h };
          const key = itemKey(item);

          // compound param (e.g. 'region') vs flat params (e.g. ['x','y'])
          if (item.params.length === 1) {
            updateNodeParams(item.nodeId, { [item.params[0]]: vals });
          } else {
            updateNodeParams(item.nodeId, vals);
          }

          setRegionValues(prev => ({ ...prev, [key]: vals }));
          markFilled(key);
          addLog({ level: 'info', message: `已框选区域: (${vals.x}, ${vals.y}) ${vals.w}×${vals.h}` });
        }
      }
    } catch (err: any) {
      console.error('选区失败:', err);
    } finally {
      setSelectingId(null);
    }
  };

  const handleFileSelect = async (item: typeof queue[0]) => {
    const api = (window as any).electronAPI;
    if (api?.openFileDialog) {
      const result = await api.openFileDialog([{ name: '图片', extensions: ['png', 'jpg', 'jpeg', 'bmp'] }]);
      if (result && !result.canceled && result.filePaths?.length > 0) {
        const path = result.filePaths[0];
        const key = itemKey(item);
        const vals: Record<string, any> = {};
        vals[item.params[0]] = [{ path }];
        updateNodeParams(item.nodeId, vals);
        setTextValues(prev => ({ ...prev, [key]: path }));
        markFilled(key);
      }
    }
  };

  const handleTextConfirm = (item: typeof queue[0]) => {
    const key = itemKey(item);
    const value = textValues[key]?.trim();
    if (!value) return;
    const vals: Record<string, any> = {};
    vals[item.params[0]] = value;
    updateNodeParams(item.nodeId, vals);
    markFilled(key);
  };

  const handleRun = () => {
    finishParamPrompt(); // clears state silently and starts execution
  };

  const handleCancel = () => {
    cancelParamPrompt();
  };

  return (
    <div style={OVERLAY_STYLE} onClick={(e) => { if (e.target === e.currentTarget) handleCancel(); }}>
      <div style={DIALOG_STYLE} onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div style={{
          padding: '14px 16px', borderBottom: '1px solid var(--border-primary)',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          flexShrink: 0,
        }}>
          <div style={{ fontSize: 15, fontWeight: 600 }}>
            参数补全 <span style={{ fontSize: 12, color: 'var(--text-secondary)', fontWeight: 400 }}>
              {filledCount}/{total} 已完成
            </span>
          </div>
          <button onClick={handleCancel}
            style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', fontSize: 18, padding: '0 4px' }}
            title="取消运行">×</button>
        </div>

        {/* Scrollable items list */}
        <div style={{ padding: '8px 16px', overflowY: 'auto', flex: 1 }}>
          {queue.map((item) => {
            const key = itemKey(item);
            const isFilled = !!filled[key];
            const typeLabel = NODE_TYPE_LABELS[item.nodeType] || item.nodeType;

            return (
              <div key={key} style={{
                padding: 12, borderRadius: 8, marginBottom: 8,
                background: 'var(--bg-secondary)',
                border: `1px solid ${isFilled ? 'var(--accent)' : 'var(--border-primary)'}`,
                opacity: isFilled ? 0.65 : 1,
                transition: 'opacity 0.2s',
              }}>
                {/* Item header: type · label + checkmark */}
                <div style={{
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                  marginBottom: 6,
                }}>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>
                    <span style={{ color: 'var(--text-secondary)' }}>{typeLabel}</span>
                    &nbsp;·&nbsp;{item.nodeLabel}
                  </div>
                  {isFilled && <Check size={14} color="var(--accent)" />}
                </div>

                <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 8 }}>
                  缺失: {item.labels.join(', ')}
                </div>

                {/* ── Region picker ── */}
                {item.groupType === 'region' && (
                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                      <button className="btn btn-ghost btn-icon" onClick={() => handleRegionSelect(item)}
                        disabled={selectingId === item.nodeId || isFilled}
                        title="框选区域"
                        style={{ width: 28, height: 28 }}>
                        <Crosshair size={14} />
                      </button>
                      {!isFilled && (
                        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>点此框选屏幕区域</span>
                      )}
                      {isFilled && regionValues[key] && (
                        <span style={{ fontSize: 11, color: 'var(--accent)' }}>
                          ({regionValues[key].x}, {regionValues[key].y}) {regionValues[key].w}×{regionValues[key].h}
                        </span>
                      )}
                    </div>
                    <div className="form-row" style={{ display: 'flex', gap: 6 }}>
                      {['x', 'y', 'w', 'h'].map((k) => (
                        <div key={k} style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 2 }}>
                          <label style={{ fontSize: 10, color: 'var(--text-muted)' }}>{k.toUpperCase()}</label>
                          <input className="form-input" type="number"
                            value={regionValues[key]?.[k as keyof typeof regionValues[string]] ?? 0}
                            onChange={(e) => {
                              const cur = regionValues[key] || { x: 0, y: 0, w: 100, h: 100 };
                              setRegionValues(prev => ({ ...prev, [key]: { ...cur, [k]: +e.target.value } }));
                            }}
                            style={{ padding: '4px 6px', fontSize: 12 }}
                          />
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* ── Text input ── */}
                {item.groupType === 'text' && (
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                    <input className="form-input" value={textValues[key] || ''}
                      onChange={(e) => setTextValues(prev => ({ ...prev, [key]: e.target.value }))}
                      placeholder={`请输入${item.labels[0]}`}
                      style={{ flex: 1, padding: '6px 8px', fontSize: 12 }}
                      onKeyDown={(e) => { if (e.key === 'Enter') handleTextConfirm(item); }}
                      disabled={isFilled}
                    />
                    {!isFilled && (
                      <button className="btn btn-primary" onClick={() => handleTextConfirm(item)}
                        style={{ padding: '6px 10px', fontSize: 12 }}
                        disabled={!textValues[key]?.trim()}>确定</button>
                    )}
                  </div>
                )}

                {/* ── File picker ── */}
                {item.groupType === 'file' && (
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                    <input className="form-input" value={textValues[key] || ''}
                      onChange={(e) => setTextValues(prev => ({ ...prev, [key]: e.target.value }))}
                      placeholder="选择或输入图片路径"
                      style={{ flex: 1, padding: '6px 8px', fontSize: 12 }}
                      disabled={isFilled}
                    />
                    {!isFilled && (
                      <>
                        <button className="btn btn-ghost" onClick={() => handleFileSelect(item)}
                          style={{ padding: '6px 10px', display: 'flex', alignItems: 'center', gap: 4, fontSize: 12 }}>
                          <FolderOpen size={14} /> 浏览
                        </button>
                        {textValues[key]?.trim() && (
                          <button className="btn btn-primary" onClick={() => handleTextConfirm(item)}
                            style={{ padding: '6px 10px', fontSize: 12 }}>确定</button>
                        )}
                      </>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* Footer */}
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          padding: '10px 16px', borderTop: '1px solid var(--border-primary)',
          flexShrink: 0,
        }}>
          <span style={{ fontSize: 12, color: allFilled ? 'var(--accent)' : 'var(--text-muted)' }}>
            {allFilled ? '所有参数已补全' : `${total - filledCount} 个参数待补全`}
          </span>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-ghost" onClick={handleCancel}
              style={{ padding: '6px 14px', fontSize: 12 }}>取消运行</button>
            <button className="btn btn-primary" onClick={handleRun}
              style={{ padding: '6px 14px', fontSize: 12 }}
              disabled={!allFilled}>
              补全并运行
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
