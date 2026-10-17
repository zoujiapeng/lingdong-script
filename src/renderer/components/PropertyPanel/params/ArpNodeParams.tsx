import React, { useCallback, useEffect, useState } from 'react';
import type { NodeType } from '../../../types';
import { registerEditorDraftCommitter } from '../../../utils/editorDrafts';

interface Props {
  type: Extract<NodeType, 'action_batch' | 'replay_step' | 'run_command' | 'skill_replay' | 'evidence_gate' | 'human_gate'>;
  params: Record<string, any>;
  onChange: (params: Record<string, any>) => void;
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="form-group">
      <label className="form-label">{label}</label>
      {children}
      {hint && <div className="arp-field-hint">{hint}</div>}
    </div>
  );
}

export function ArpNodeParams({ type, params, onChange }: Props) {
  const [actionsText, setActionsText] = useState(() => JSON.stringify(params.actions || [], null, 2));
  const [stepText, setStepText] = useState(() => JSON.stringify(params.step || {}, null, 2));
  const [referenceImage, setReferenceImage] = useState('');
  const [requiredText, setRequiredText] = useState(() => JSON.stringify(params.required || [], null, 2));
  const [targetText, setTargetText] = useState(() => (
    typeof params.target_window === 'string'
      ? params.target_window
      : JSON.stringify(params.target_window || '', null, 2)
  ));
  const [jsonError, setJsonError] = useState('');

  useEffect(() => {
    if (type === 'action_batch') setActionsText(JSON.stringify(params.actions || [], null, 2));
    if (type === 'replay_step') {
      setStepText(JSON.stringify(params.step || {}, null, 2));
      setTargetText(typeof params.target_window === 'string'
        ? params.target_window
        : JSON.stringify(params.target_window || '', null, 2));
    }
    if (type === 'evidence_gate') setRequiredText(JSON.stringify(params.required || [], null, 2));
  }, [type, params.actions, params.required, params.step, params.target_window]);

  const referenceFrame = type === 'replay_step' && params.step?.reference_frame;
  useEffect(() => {
    let active = true;
    const path = String(referenceFrame?.path || '');
    const readImage = window.electronAPI?.readImage;
    setReferenceImage('');
    if (!path || !readImage) return () => { active = false; };
    void readImage(path).then((result) => {
      if (active && result.dataURL) setReferenceImage(result.dataURL);
    });
    return () => { active = false; };
  }, [referenceFrame?.path]);

  const patch = (values: Record<string, unknown>) => onChange({ ...params, ...values });
  const commitDraft = useCallback((): string | undefined => {
    try {
      if (type === 'action_batch') {
        const actions = JSON.parse(actionsText);
        if (!Array.isArray(actions)) throw new Error('actions 必须是 JSON 数组。');
        if (JSON.stringify(actions) !== JSON.stringify(params.actions || [])) {
          onChange({ ...params, actions });
        }
      }
      if (type === 'replay_step') {
        const step = JSON.parse(stepText);
        if (!step || Array.isArray(step) || typeof step !== 'object') throw new Error('step 必须是 JSON 对象。');
        if (!String(step.tool || '').trim()) throw new Error('step 必须包含 tool 字段。');
        const rawTarget = targetText.trim();
        const parsedTarget = !rawTarget
          ? undefined
          : rawTarget.startsWith('{') || rawTarget.startsWith('"')
            ? JSON.parse(rawTarget)
            : rawTarget;
        const targetWindow = parsedTarget === '' ? undefined : parsedTarget;
        if (typeof targetWindow !== 'string' && targetWindow !== undefined
          && (!targetWindow || Array.isArray(targetWindow) || typeof targetWindow !== 'object')) {
          throw new Error('target_window 必须是窗口标题或 JSON 对象。');
        }
        if (JSON.stringify(step) !== JSON.stringify(params.step || {})
          || JSON.stringify(targetWindow) !== JSON.stringify(params.target_window)) {
          onChange({ ...params, step, target_window: targetWindow });
        }
      }
      if (type === 'evidence_gate') {
        const required = JSON.parse(requiredText);
        if (!Array.isArray(required)) throw new Error('required 必须是 JSON 数组。');
        if (JSON.stringify(required) !== JSON.stringify(params.required || [])) {
          onChange({ ...params, required });
        }
      }
      setJsonError('');
      return undefined;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setJsonError(message);
      return message;
    }
  }, [actionsText, onChange, params, requiredText, stepText, targetText, type]);

  useEffect(() => {
    if (type !== 'action_batch' && type !== 'replay_step' && type !== 'evidence_gate') return undefined;
    return registerEditorDraftCommitter(commitDraft);
  }, [commitDraft, type]);

  return (
    <div className="arp-param-editor">
      {type === 'action_batch' && (
        <>
          <Field label="子目标" hint="填写一段连贯、可快速本地验证的交互序列。">
            <input className="form-input" value={params.subgoal || ''} onChange={(event) => patch({ subgoal: event.target.value })} />
          </Field>
          <Field label="操作列表" hint={'使用语义操作，例如 {"action":"click_element","label":"导出","expected_region":"top-right"}。'}>
            <textarea className="form-input arp-code-editor" spellCheck={false} value={actionsText} onChange={(event) => setActionsText(event.target.value)} onBlur={commitDraft} />
            {jsonError && <div className="arp-field-error">{jsonError}</div>}
          </Field>
          <label className="form-checkbox-label">
            <input type="checkbox" checked={params.stop_on_error !== false} onChange={(event) => patch({ stop_on_error: event.target.checked })} />
            首个操作失败时停止整批操作
          </label>
        </>
      )}

      {type === 'skill_replay' && (
        <>
          <Field label="技能文件" hint="使用经过精确测试的 .wps.json 技能；留空则按目标检索。">
            <input className="form-input" value={params.script || ''} onChange={(event) => patch({ script: event.target.value })} placeholder="示例.wps.json" />
          </Field>
          <Field label="按目标查找" hint="调用智能代理兜底前，先在本地查找最可靠且已验证的技能。">
            <input className="form-input" value={params.task || ''} onChange={(event) => patch({ task: event.target.value })} placeholder="导出当前报告" />
          </Field>
          <Field label="记忆 ID" hint="可选：填写准确的记忆图谱节点 ID。">
            <input className="form-input" value={params.memory_id || ''} onChange={(event) => patch({ memory_id: event.target.value })} placeholder="skill-..." />
          </Field>
        </>
      )}

      {type === 'replay_step' && (
        <>
          {referenceFrame && (
            <Field
              label="Agent 当时看到的画面"
              hint={Array.isArray(referenceFrame.target)
                ? `图中目标坐标：${referenceFrame.target.join(', ')}`
                : '复制到剪贴板或重放时使用的原始画面。'}
            >
              {referenceImage ? (
                <div style={{ position: 'relative' }}>
                  <img
                    src={referenceImage}
                    alt="Agent 当时看到的画面"
                    style={{ display: 'block', width: '100%', borderRadius: 6 }}
                  />
                  {Array.isArray(referenceFrame.target) && Array.isArray(referenceFrame.image_size) && (
                    <span style={{
                      position: 'absolute',
                      left: `${(Number(referenceFrame.target[0]) / Number(referenceFrame.image_size[0])) * 100}%`,
                      top: `${(Number(referenceFrame.target[1]) / Number(referenceFrame.image_size[1])) * 100}%`,
                      width: 12,
                      height: 12,
                      border: '2px solid #ff3b30',
                      borderRadius: '50%',
                      transform: 'translate(-50%, -50%)',
                      pointerEvents: 'none',
                    }} />
                  )}
                </div>
              ) : (
                <div className="arp-field-hint">{referenceFrame.available === false ? '临时画面已过期' : referenceFrame.path}</div>
              )}
            </Field>
          )}
          <Field label="WPS 步骤" hint="本地重放器执行的精确操作或控制步骤，不消耗模型 token。">
            <textarea className="form-input arp-code-editor" spellCheck={false} value={stepText} onChange={(event) => setStepText(event.target.value)} onBlur={commitDraft} />
          </Field>
          <Field label="目标窗口" hint="填写窗口标题，或类似 {&quot;title&quot;:&quot;Notepad&quot;,&quot;process&quot;:&quot;notepad.exe&quot;} 的 JSON。">
            <textarea className="form-input arp-textarea arp-textarea-compact" spellCheck={false} value={targetText} onChange={(event) => setTargetText(event.target.value)} onBlur={commitDraft} />
          </Field>
          <div className="arp-policy-grid">
            <Field label="会话">
              <input className="form-input" value={params.session || 'default'} onChange={(event) => patch({ session: event.target.value })} />
            </Field>
            <Field label="执行间隔（毫秒）">
              <input className="form-input" type="number" min={0} max={60000} value={params.pace_ms ?? 0} onChange={(event) => patch({ pace_ms: Math.max(0, Number(event.target.value) || 0) })} />
            </Field>
          </div>
          {jsonError && <div className="arp-field-error">{jsonError}</div>}
        </>
      )}

      {type === 'run_command' && (
        <>
          <Field label="命令" hint="可使用 ${变量名} 引用前序步骤写入的流程变量；命令在本机运行，不经过模型。">
            <textarea
              className="form-input arp-code-editor"
              spellCheck={false}
              value={params.command || ''}
              onChange={(event) => patch({ command: event.target.value })}
              placeholder="例如：ffmpeg -i ${input} -y ${output}"
            />
          </Field>
          <div className="arp-policy-grid">
            <Field label="Shell">
              <select className="form-input" value={params.shell || 'powershell'} onChange={(event) => patch({ shell: event.target.value })}>
                <option value="powershell">PowerShell</option>
                <option value="cmd">CMD</option>
              </select>
            </Field>
            <Field label="超时（秒）">
              <input className="form-input" type="number" min={1} max={3600} value={params.timeout_s ?? 30} onChange={(event) => patch({ timeout_s: Math.max(1, Math.min(3600, Number(event.target.value) || 30)) })} />
            </Field>
          </div>
          <Field label="工作目录" hint="留空时使用运行时默认工作目录。">
            <input className="form-input" value={params.cwd || ''} onChange={(event) => patch({ cwd: event.target.value })} placeholder="可选" />
          </Field>
          <Field label="输出变量" hint="可选；把标准输出保存到流程变量，供后续条件或命令使用。">
            <input className="form-input" value={params.into || ''} onChange={(event) => patch({ into: event.target.value })} placeholder="例如：result" />
          </Field>
        </>
      )}

      {type === 'evidence_gate' && (
        <Field label="必需证据" hint="JSON 数组；可保留 predicate、source、equals 和 max_age_ms 等结构化条件。">
          <textarea
            className="form-input arp-code-editor"
            spellCheck={false}
            value={requiredText}
            onChange={(event) => setRequiredText(event.target.value)}
            onBlur={commitDraft}
          />
          {jsonError && <div className="arp-field-error">{jsonError}</div>}
        </Field>
      )}

      {type === 'human_gate' && (
        <>
          <Field label="确认提示">
            <textarea className="form-input arp-textarea arp-textarea-compact" value={params.message || ''} onChange={(event) => patch({ message: event.target.value })} />
          </Field>
          <Field label="超时（秒）">
            <input className="form-input" type="number" min={1} max={86400} value={params.timeout_s ?? 3600} onChange={(event) => patch({ timeout_s: Number(event.target.value) || 3600 })} />
          </Field>
        </>
      )}

      <div className="arp-policy-block">
        <div className="arp-policy-title">失败策略</div>
        <div className="arp-policy-grid">
          <Field label="尝试次数">
            <input className="form-input" type="number" min={1} max={10} value={params.retry_max_attempts ?? 1} onChange={(event) => patch({ retry_max_attempts: Number(event.target.value) || 1 })} />
          </Field>
          <Field label="延迟（毫秒）">
            <input className="form-input" type="number" min={0} max={60000} step={100} value={params.retry_delay_ms ?? 0} onChange={(event) => patch({ retry_delay_ms: Number(event.target.value) || 0 })} />
          </Field>
        </div>
      </div>
    </div>
  );
}
