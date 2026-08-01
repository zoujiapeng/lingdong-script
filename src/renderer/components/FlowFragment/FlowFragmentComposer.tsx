import { useEffect, useRef, useState } from 'react';
import { Check, Loader2, Sparkles, X } from 'lucide-react';
import { useStore } from '../../store';
import { commitEditorDrafts } from '../../utils/editorDrafts';
import {
  buildFlowFragmentRequestContext,
  flowDocumentFingerprint,
  normalizeFlowFragmentProposal,
  validateFlowFragmentProposal,
  type FlowFragmentProposal,
  type FlowFragmentRequestContext,
} from '../../utils/flowFragmentProposal';

export function FlowFragmentComposer({
  propose,
  disabled,
  disabledReason,
}: {
  propose: (goal: string, context: FlowFragmentRequestContext, signal?: AbortSignal) => Promise<FlowFragmentProposal>;
  disabled?: boolean;
  disabledReason?: string;
}) {
  const [open, setOpen] = useState(false);
  const [goal, setGoal] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [proposal, setProposal] = useState<FlowFragmentProposal | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  const generate = async () => {
    const request = goal.trim();
    if (!request || busy) return;
    const draftError = commitEditorDrafts();
    if (draftError) {
      setError(`请先修正编辑器内容：${draftError}`);
      return;
    }
    setBusy(true);
    setError('');
    setProposal(null);
    const aborter = new AbortController();
    abortRef.current = aborter;
    try {
      const context = buildFlowFragmentRequestContext(useStore.getState().script);
      const raw = await propose(request, context, aborter.signal);
      if (aborter.signal.aborted) return;
      const current = useStore.getState().script;
      const normalized = normalizeFlowFragmentProposal(raw, flowDocumentFingerprint(current));
      const localErrors = validateFlowFragmentProposal(normalized, current);
      if (localErrors.length) throw new Error(`片段未通过本地结构检查：${localErrors.join(' · ')}`);
      setProposal(normalized);
    } catch (generateError) {
      if (!aborter.signal.aborted) {
        setError(generateError instanceof Error ? generateError.message : String(generateError));
      }
    } finally {
      if (abortRef.current === aborter) {
        abortRef.current = null;
        setBusy(false);
      }
    }
  };

  const apply = () => {
    if (!proposal) return;
    const draftError = commitEditorDrafts();
    if (draftError) {
      setError(`请先修正编辑器内容：${draftError}`);
      return;
    }
    const state = useStore.getState();
    if (flowDocumentFingerprint(state.script) !== proposal.base_fingerprint) {
      setProposal(null);
      setError('当前流程已变化，请重新生成片段。');
      return;
    }
    const localErrors = validateFlowFragmentProposal(proposal, state.script);
    if (localErrors.length) {
      setError(`片段未通过本地结构检查：${localErrors.join(' · ')}`);
      return;
    }
    const inserted = state.applyFlowFragment(proposal);
    const scale = state.ui.canvas_transform.scale;
    const canvas = document.querySelector<HTMLElement>('.arp-studio-shell .flow-canvas-wrapper');
    const bounds = canvas?.getBoundingClientRect();
    state.setCanvasTransform({
      x: (bounds?.width ?? window.innerWidth) * 0.5 - inserted.center.x * scale,
      y: (bounds?.height ?? window.innerHeight) * 0.5 - inserted.center.y * scale,
    });
    setOpen(false);
    setGoal('');
    setProposal(null);
    setError('');
  };

  const close = () => {
    abortRef.current?.abort();
    setOpen(false);
    setProposal(null);
    setError('');
  };

  return (
    <>
      <button
        className="arp-button arp-studio-command"
        title={disabled ? disabledReason : '一句话生成可编辑流程片段'}
        aria-label="一句话生成流程片段"
        onClick={() => setOpen(true)}
        disabled={disabled}
      >
        <Sparkles size={14} /><span>生成片段</span>
      </button>
      {open && (
        <div className="ld-command-overlay flow-fragment-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) close(); }}>
          <section
            className="flow-fragment-dialog"
            role="dialog"
            aria-modal="true"
            aria-label="一句话生成流程片段"
            onKeyDown={(event) => {
              if (event.key !== 'Escape') return;
              event.stopPropagation();
              close();
            }}
          >
            <header>
              <div><Sparkles size={16} /><span><strong>生成流程片段</strong><small>生成草稿 · 不观察桌面 · 不自动执行</small></span></div>
              <button type="button" onClick={close} aria-label="关闭"><X size={16} /></button>
            </header>
            <label className="flow-fragment-prompt">
              <span>描述要插入的机械流程</span>
              <textarea
                autoFocus
                value={goal}
                onChange={(event) => setGoal(event.target.value)}
                onKeyDown={(event) => {
                  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') void generate();
                }}
                placeholder="例如：点击 420,300，等待 1 秒，再输入 hello"
                disabled={busy}
              />
            </label>
            <p className="flow-fragment-note">生成只使用本地可编译的确定性节点；结果先校验并预览，应用后仍需手工拼接到主流程。</p>
            {error && <div className="ld-inline-alert" role="alert"><X size={13} />{error}</div>}
            {proposal && (
              <section className="flow-fragment-preview" aria-label="流程片段预览">
                <div><strong>{proposal.summary || '流程片段'}</strong><small>{proposal.nodes.length} 个节点 · {proposal.edges.length} 条内部连线</small></div>
                <ol>
                  {proposal.nodes.map((node) => (
                    <li key={node.local_id}>
                      <span>{node.label || node.type}</span>
                      <code>{node.type} {JSON.stringify(node.params)}</code>
                    </li>
                  ))}
                </ol>
              </section>
            )}
            <footer>
              <button className="arp-button" type="button" onClick={close}>{proposal ? '丢弃' : (busy ? '取消生成' : '取消')}</button>
              {!proposal ? (
                <button className="arp-button arp-button-primary" type="button" onClick={() => void generate()} disabled={busy || !goal.trim()}>
                  {busy ? <Loader2 size={14} className="arp-spin" /> : <Sparkles size={14} />}{busy ? '生成中' : '生成预览'}
                </button>
              ) : (
                <button className="arp-button arp-button-primary" type="button" onClick={apply}><Check size={14} />应用到空白处</button>
              )}
            </footer>
          </section>
        </div>
      )}
    </>
  );
}
