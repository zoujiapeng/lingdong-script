import React, { useEffect, useState } from 'react';
import type { NodeType } from '../../../types';

interface Props {
  type: Extract<NodeType, 'agent_goal' | 'action_batch' | 'skill_replay' | 'evidence_gate' | 'human_gate'>;
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
  const [jsonError, setJsonError] = useState('');

  useEffect(() => {
    if (type === 'action_batch') setActionsText(JSON.stringify(params.actions || [], null, 2));
  }, [type]);

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
        <Field label="Skill file" hint="A tested .wps.json skill from the reusable asset library.">
          <input className="form-input" value={params.script || ''} onChange={(event) => patch({ script: event.target.value })} placeholder="example.wps.json" />
        </Field>
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
        <div className="arp-policy-title">Failure policy</div>
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
