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

  if (!base) {
    return <div style={centered}><span style={muted}>等待 WinPilot 服务就绪…（Agent 面板需先启动一次）</span></div>;
  }

  return (
    <div style={{ height: '100%', width: '100%', overflowY: 'auto', padding: 16, maxWidth: 720, margin: '0 auto' }}>
      <button
        className={recording ? 'agent-btn agent-btn-primary' : 'agent-btn'}
        style={{ width: '100%', marginBottom: 14, padding: '8px 0' }}
        onClick={toggleRecord}
        title="录制你的真实操作（自动抓取元素文字选择器），生成可编辑的流程图脚本"
      >
        {recording ? '⏹ 停止录制（演示）→ 导入到流程图' : '🔴 录制演示 → 生成流程图'}
      </button>
      <p style={muted}>导入后会转换成原版流程图节点、加载到画布，可像普通流程图一样自由编辑、与指令库节点混用、按 F5 运行。</p>

      <div style={head}>Agent 录制 (trace)</div>
      {traces.length === 0 && <div style={muted}>（暂无 trace）</div>}
      {traces.map((t) => (
        <div key={t.run_id} style={row}>
          <span style={ellipsis} title={t.task}>{t.task || t.run_id}
            <span style={muted}> · {t.status} {t.steps}步</span></span>
          <button className="agent-btn agent-btn-primary" onClick={() => importTrace(t.run_id)} disabled={busy}>导入到流程图</button>
        </div>
      ))}

      <div style={{ ...head, marginTop: 16 }}>已保存脚本 (.wps.json)</div>
      {scripts.length === 0 && <div style={muted}>（暂无脚本）</div>}
      {scripts.map((sc) => (
        <div key={sc.file} style={row}>
          <span style={ellipsis} title={sc.task || sc.file}>{sc.name || sc.file}
            <span style={muted}> · {sc.steps}步</span></span>
          <button className="agent-btn agent-btn-primary" onClick={() => importScript(sc.file)} disabled={busy}>导入到流程图</button>
          <button className="agent-btn" onClick={() => replayOriginal(sc.file)} title="不编辑，直接用 WinPilot 离线重放原始脚本（0 token）">原样重放</button>
        </div>
      ))}

      <button className="agent-btn" style={{ marginTop: 14 }} onClick={refreshLists}>刷新</button>
      {error && <div style={{ color: 'var(--err, #d33)', fontSize: 12, marginTop: 8 }}>{error}</div>}
    </div>
  );
}

const centered: React.CSSProperties = { height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' };
const muted: React.CSSProperties = { color: 'var(--text-muted, #888)', fontSize: 12, margin: '4px 0' };
const head: React.CSSProperties = { fontSize: 13, fontWeight: 600, color: 'var(--accent, #6366f1)', margin: '6px 0 8px' };
const row: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 6, padding: '5px 4px', fontSize: 12, borderBottom: '1px solid var(--border, #eee)' };
const ellipsis: React.CSSProperties = { flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' };
