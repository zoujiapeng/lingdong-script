import React, { useCallback, useEffect, useState } from 'react';
import type { NodeType } from '../../../types';
import { registerEditorDraftCommitter } from '../../../utils/editorDrafts';

interface Props {
  type: Extract<NodeType, 'agent_goal' | 'action_batch' | 'replay_step' | 'skill_replay' | 'evidence_gate' | 'human_gate'>;
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
  }, [type, params.actions, params.step, params.target_window]);

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
      setJsonError('');
      return undefined;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setJsonError(message);
      return message;
    }
  }, [actionsText, onChange, params, stepText, targetText, type]);

  useEffect(() => {
    if (type !== 'action_batch' && type !== 'replay_step') return undefined;
    return registerEditorDraftCommitter(commitDraft);
  }, [commitDraft, type]);

  return (
    <div className="arp-param-editor">
      {type === 'agent_goal' && (
        <>
          <Field label="目标" hint="描述期望结果。智能代理决定做什么，控制器决定如何交互。">
            <textarea className="form-input arp-textarea" value={params.goal || ''} onChange={(event) => patch({ goal: event.target.value })} />
          </Field>
          <Field label="成功标准" hint="填写可见、可测试且能证明任务完成的证据。">
            <textarea className="form-input arp-textarea arp-textarea-compact" value={params.success_criteria || ''} onChange={(event) => patch({ success_criteria: event.target.value })} />
          </Field>
          <Field label="控制器最大步数">
            <input className="form-input" type="number" min={1} max={200} value={params.max_steps ?? 30} onChange={(event) => patch({ max_steps: Number(event.target.value) || 30 })} />
          </Field>
        </>
      )}

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
          <label className="form-checkbox-label">
            <input type="checkbox" checked={params.allow_shell === true} onChange={(event) => patch({ allow_shell: event.target.checked })} />
            允许此工作流执行 shell 命令
          </label>
          {jsonError && <div className="arp-field-error">{jsonError}</div>}
        </>
      )}

      {type === 'evidence_gate' && (
        <Field label="必需证据" hint="每行填写一个证据标记；所有标记都必须出现在累计运行证据中。">
          <textarea
            className="form-input arp-textarea"
            value={Array.isArray(params.required) ? params.required.join('\n') : ''}
            onChange={(event) => patch({ required: event.target.value.split('\n').map((item) => item.trim()).filter(Boolean) })}
          />
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
