import React, { useCallback, useEffect, useState } from 'react';
import { useStore } from '../store';
import { wpsToFlowScript, WpsScript } from '../utils/wpsToFlowScript';

/**
 * Agent import / record hub. Lists WinPilot traces + `.wps.json` scripts and
 * imports them INTO the existing flow editor (convert → loadScript → switch to
 * canvas), so the mature editor + engine handle them and they mix freely with
 * the instruction-library nodes. No separate renderer — the flow page is the
 * editor. Also hosts the human-demonstration recorder toggle.
 */

interface TraceItem { run_id: string; task: string; status: string; steps: number }
interface ScriptItem { file: string; name: string; task: string; steps: number }

async function jsonOrThrow<T = any>(response: Response, label: string): Promise<T> {
  const text = await response.text();
  let data: any = {};
  if (text) {
    try { data = JSON.parse(text); } catch { data = { detail: text }; }
  }
  if (!response.ok) {
    throw new Error(data?.detail || data?.error || `${label} failed (${response.status})`);
  }
  return data as T;
}

export function ReplayEditorView() {
  const loadScript = useStore((s) => s.loadScript);
  const setViewMode = useStore((s) => s.setViewMode);

  const [base, setBase] = useState<string | null>(null);
  const [traces, setTraces] = useState<TraceItem[]>([]);
  const [scripts, setScripts] = useState<ScriptItem[]>([]);
  const [recording, setRecording] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [traceFoldOpen, setTraceFoldOpen] = useState(false);
  const [scriptFoldOpen, setScriptFoldOpen] = useState(false);

  const apiBase = useCallback(async (): Promise<string | null> => {
    if (base) return base;
    const u = await window.electronAPI?.getWinpilotUrl?.();
    if (u) setBase(u);
    return u || null;
  }, [base]);

  const refreshLists = useCallback(async () => {
    const b = await apiBase();
    if (!b) return;
    try {
      const [tr, sc] = await Promise.all([
        fetch(`${b}api/traces`).then((r) => r.json()),
        fetch(`${b}api/scripts`).then((r) => r.json()),
      ]);
      setTraces(Array.isArray(tr) ? tr : []);
      setScripts(Array.isArray(sc) ? sc : []);
      setError('');
      try {
        const st = await fetch(`${b}api/human/status`).then((r) => r.json());
        setRecording(!!st?.recording);
      } catch { /* ignore */ }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [apiBase]);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      const b = await apiBase();
      if (!alive) return;
      if (b) refreshLists(); else setTimeout(tick, 800);
    };
    tick();
    return () => { alive = false; };
  }, [apiBase, refreshLists]);

  const importScript = useCallback(async (file: string) => {
    const b = await apiBase();
    if (!b) return;
    setBusy(true);
    try {
      const wps = (await fetch(`${b}api/script/${encodeURIComponent(file)}`).then((r) => {
        if (!r.ok) throw new Error(`读取脚本失败 (${r.status})`);
        return r.json();
      })) as WpsScript;
      loadScript(wpsToFlowScript(wps));
      setViewMode('canvas');           // drop into the real flow editor
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [apiBase, loadScript, setViewMode]);

  const importTrace = useCallback(async (runId: string) => {
    const b = await apiBase();
    if (!b) return;
    setBusy(true);
    try {
      const res = await fetch(`${b}api/export/${encodeURIComponent(runId)}`, { method: 'POST' })
        .then((r) => jsonOrThrow(r, 'export trace'));
      if (res?.script) {
        await refreshLists();
        await importScript(res.script);
      } else {
        setError('该 trace 没有可重放步骤，无法导出');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [apiBase, refreshLists, importScript]);

  const replayOriginal = useCallback(async (file: string) => {
    const b = await apiBase();
    if (!b) return;
    setBusy(true);
    try {
      await fetch(`${b}api/replay`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ script: file }),
      }).then((r) => jsonOrThrow(r, 'replay script'));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [apiBase]);

  const toggleRecord = useCallback(async () => {
    const b = await apiBase();
    if (!b) return;
    try {
      if (recording) {
        const res = await fetch(`${b}api/human/stop`, { method: 'POST' })
          .then((r) => jsonOrThrow(r, 'stop recording'));
        setRecording(false);
        await refreshLists();
        if (res?.file) await importScript(res.file);
      } else {
        const res = await fetch(`${b}api/human/start`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}),
        });
        await jsonOrThrow(res, 'start recording');
        setRecording(true);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [apiBase, recording, refreshLists, importScript]);

  const traceCount = traces.length;
  const scriptCount = scripts.length;

  useEffect(() => {
    if (traceCount > 0) setTraceFoldOpen(true);
  }, [traceCount]);

  useEffect(() => {
    if (scriptCount > 0) setScriptFoldOpen(true);
  }, [scriptCount]);

  return (
    <div className={`replay-orb${recording ? ' is-recording' : ''}${busy ? ' is-busy' : ''}`}>
      <section className="replay-orb-prime">
        <div className="replay-orb-status" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        <div className="replay-orb-copy">
          <span>{base ? 'Replay ready' : 'Waiting for WinPilot'}</span>
          <strong>{recording ? '正在录制真实操作' : '演示与 trace 复用'}</strong>
          <em>{base ? `${traceCount} trace · ${scriptCount} script` : 'connection pending'}</em>
        </div>
        <button
          className="replay-record-btn"
          onClick={toggleRecord}
          disabled={!base || busy}
          title="录制你的真实操作（自动抓取元素文字选择器），生成可编辑的流程图脚本"
        >
          <span />
          {recording ? '停止并导入' : '录制'}
        </button>
      </section>

      <div className="replay-orb-strip">
        <button type="button" onClick={refreshLists} disabled={!base || busy}>刷新</button>
        <span>FlowScript import</span>
      </div>

      {!base ? (
        <div className="replay-empty">
          <strong>等待 WinPilot</strong>
          <span>trace / scripts pending</span>
        </div>
      ) : (
        <div className="replay-folds">
          <details
            className="replay-fold"
            open={traceFoldOpen}
            onToggle={event => setTraceFoldOpen(event.currentTarget.open)}
          >
            <summary>
              <span>Trace</span>
              <strong>{traceCount}</strong>
            </summary>
            {traces.length === 0 ? (
              <div className="replay-empty compact">暂无 trace</div>
            ) : traces.map((t) => (
              <div key={t.run_id} className="replay-row">
                <span className="replay-row-main" title={t.task}>{t.task || t.run_id}</span>
                <span className="replay-row-meta">{t.status} · {t.steps}步</span>
                <button onClick={() => importTrace(t.run_id)} disabled={busy}>导入</button>
              </div>
            ))}
          </details>

          <details
            className="replay-fold"
            open={scriptFoldOpen}
            onToggle={event => setScriptFoldOpen(event.currentTarget.open)}
          >
            <summary>
              <span>WPS scripts</span>
              <strong>{scriptCount}</strong>
            </summary>
            {scripts.length === 0 ? (
              <div className="replay-empty compact">暂无脚本</div>
            ) : scripts.map((sc) => (
              <div key={sc.file} className="replay-row">
                <span className="replay-row-main" title={sc.task || sc.file}>{sc.name || sc.file}</span>
                <span className="replay-row-meta">{sc.steps}步</span>
                <button onClick={() => importScript(sc.file)} disabled={busy}>导入</button>
                <button onClick={() => replayOriginal(sc.file)} disabled={busy} title="不编辑，直接用 WinPilot 离线重放原始脚本（0 token）">重放</button>
              </div>
            ))}
          </details>
        </div>
      )}

      {error && <div className="replay-error">{error}</div>}
    </div>
  );
}
