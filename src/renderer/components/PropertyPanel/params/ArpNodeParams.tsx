import React, { useEffect, useState } from 'react';
import type { NodeType } from '../../../types';

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
  const commitActions = () => {
    try {
      const value = JSON.parse(actionsText);
      if (!Array.isArray(value)) throw new Error('Actions must be a JSON array.');
      patch({ actions: value });
      setJsonError('');
    } catch (error) {
      setJsonError(error instanceof Error ? error.message : String(error));
    }
  };
  const commitStep = () => {
    try {
      const value = JSON.parse(stepText);
      if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error('Step must be a JSON object.');
      if (!String(value.tool || '').trim()) throw new Error('Step requires a tool.');
      patch({ step: value });
      setJsonError('');
    } catch (error) {
      setJsonError(error instanceof Error ? error.message : String(error));
    }
  };
  const commitTarget = () => {
    const raw = targetText.trim();
    if (!raw) {
      patch({ target_window: undefined });
      setJsonError('');
      return;
    }
    try {
      const value = raw.startsWith('{') || raw.startsWith('"') ? JSON.parse(raw) : raw;
      if (typeof value !== 'string' && (!value || Array.isArray(value) || typeof value !== 'object')) {
        throw new Error('Target window must be a title or JSON object.');
      }
      patch({ target_window: value });
      setJsonError('');
    } catch (error) {
      setJsonError(error instanceof Error ? error.message : String(error));
    }
  };

  return (
    <div className="arp-param-editor">
      {type === 'agent_goal' && (
        <>
          <Field label="Goal" hint="Describe the outcome. The Agent decides what to do; the controller decides how to interact.">
            <textarea className="form-input arp-textarea" value={params.goal || ''} onChange={(event) => patch({ goal: event.target.value })} />
          </Field>
          <Field label="Success criteria" hint="Visible, testable evidence that proves completion.">
            <textarea className="form-input arp-textarea arp-textarea-compact" value={params.success_criteria || ''} onChange={(event) => patch({ success_criteria: event.target.value })} />
          </Field>
          <Field label="Maximum controller steps">
            <input className="form-input" type="number" min={1} max={200} value={params.max_steps ?? 30} onChange={(event) => patch({ max_steps: Number(event.target.value) || 30 })} />
          </Field>
        </>
      )}

      {type === 'action_batch' && (
        <>
          <Field label="Subgoal" hint="One coherent interaction sequence with fast local verification.">
            <input className="form-input" value={params.subgoal || ''} onChange={(event) => patch({ subgoal: event.target.value })} />
          </Field>
          <Field label="Actions" hint={'Use semantic actions such as {"action":"click_element","label":"Export","expected_region":"top-right"}.'}>
            <textarea className="form-input arp-code-editor" spellCheck={false} value={actionsText} onChange={(event) => setActionsText(event.target.value)} onBlur={commitActions} />
            {jsonError && <div className="arp-field-error">{jsonError}</div>}
          </Field>
          <label className="form-checkbox-label">
            <input type="checkbox" checked={params.stop_on_error !== false} onChange={(event) => patch({ stop_on_error: event.target.checked })} />
            Stop the batch on the first failed action
          </label>
        </>
      )}

      {type === 'skill_replay' && (
        <>
          <Field label="Skill file" hint="Use an exact tested .wps.json skill, or leave empty to retrieve by goal.">
            <input className="form-input" value={params.script || ''} onChange={(event) => patch({ script: event.target.value })} placeholder="example.wps.json" />
          </Field>
          <Field label="Goal lookup" hint="Find the strongest verified skill locally before invoking an Agent fallback.">
            <input className="form-input" value={params.task || ''} onChange={(event) => patch({ task: event.target.value })} placeholder="Export the current report" />
          </Field>
          <Field label="Memory ID" hint="Optional exact memory graph node ID.">
            <input className="form-input" value={params.memory_id || ''} onChange={(event) => patch({ memory_id: event.target.value })} placeholder="skill-..." />
          </Field>
        </>
      )}

      {type === 'replay_step' && (
        <>
          <Field label="WPS step" hint="The exact zero-token action or control step executed by the local Replayer.">
            <textarea className="form-input arp-code-editor" spellCheck={false} value={stepText} onChange={(event) => setStepText(event.target.value)} onBlur={commitStep} />
          </Field>
          <Field label="Target window" hint="A window title, or JSON such as {&quot;title&quot;:&quot;Notepad&quot;,&quot;process&quot;:&quot;notepad.exe&quot;}.">
            <textarea className="form-input arp-textarea arp-textarea-compact" spellCheck={false} value={targetText} onChange={(event) => setTargetText(event.target.value)} onBlur={commitTarget} />
          </Field>
          <div className="arp-policy-grid">
            <Field label="Session">
              <input className="form-input" value={params.session || 'default'} onChange={(event) => patch({ session: event.target.value })} />
            </Field>
            <Field label="Pace (ms)">
              <input className="form-input" type="number" min={0} max={60000} value={params.pace_ms ?? 0} onChange={(event) => patch({ pace_ms: Math.max(0, Number(event.target.value) || 0) })} />
            </Field>
          </div>
          <label className="form-checkbox-label">
            <input type="checkbox" checked={params.allow_shell === true} onChange={(event) => patch({ allow_shell: event.target.checked })} />
            Allow shell execution in this workflow
          </label>
          {jsonError && <div className="arp-field-error">{jsonError}</div>}
        </>
      )}

      {type === 'evidence_gate' && (
        <Field label="Required evidence" hint="One evidence token per line. Every token must exist in accumulated run evidence.">
          <textarea
            className="form-input arp-textarea"
            value={Array.isArray(params.required) ? params.required.join('\n') : ''}
            onChange={(event) => patch({ required: event.target.value.split('\n').map((item) => item.trim()).filter(Boolean) })}
          />
        </Field>
      )}

      {type === 'human_gate' && (
        <>
          <Field label="Approval message">
            <textarea className="form-input arp-textarea arp-textarea-compact" value={params.message || ''} onChange={(event) => patch({ message: event.target.value })} />
          </Field>
          <Field label="Timeout (seconds)">
            <input className="form-input" type="number" min={1} max={86400} value={params.timeout_s ?? 3600} onChange={(event) => patch({ timeout_s: Number(event.target.value) || 3600 })} />
          </Field>
        </>
      )}

      <div className="arp-policy-block">
        <div className="arp-policy-title">失败策略</div>
        <div className="arp-policy-grid">
          <Field label="Attempts">
            <input className="form-input" type="number" min={1} max={10} value={params.retry_max_attempts ?? 1} onChange={(event) => patch({ retry_max_attempts: Number(event.target.value) || 1 })} />
          </Field>
          <Field label="Delay (ms)">
            <input className="form-input" type="number" min={0} max={60000} step={100} value={params.retry_delay_ms ?? 0} onChange={(event) => patch({ retry_delay_ms: Number(event.target.value) || 0 })} />
          </Field>
        </div>
      </div>
    </div>
  );
}
