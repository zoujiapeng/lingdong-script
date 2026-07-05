import React, { useEffect, useRef, useState } from 'react';

/**
 * AgentView — embeds WinPilot's native web UI in an iframe. The redesigned
 * workbench uses a dark cockpit, so the embedded Agent UI is kept dark too.
 *
 * This component is kept mounted across tab switches (see App.tsx) so the iframe
 * — and the chat history + WebSocket it holds — survives. It pre-warms the
 * server in the background even when not visible.
 */
export function AgentView() {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [errorDetail, setErrorDetail] = useState<string>('');
  const [reloadKey, setReloadKey] = useState(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);

  useEffect(() => {
    let alive = true;
    let attempts = 0;
    const poll = async () => {
      attempts += 1;
      try {
        // ensureWinpilot triggers a (re)start if the server isn't up and
        // returns {url, starting, error}; fall back to the read-only url IPC.
        const ensure = window.electronAPI?.ensureWinpilot;
        if (ensure) {
          const st = await ensure();
          if (!alive) return;
          if (st?.url) { setUrl(st.url); setFailed(false); return; }
          if (st?.error && !st?.starting) {
            setErrorDetail(st.error); setFailed(true); return;
          }
        } else {
          const u = await window.electronAPI?.getWinpilotUrl?.();
          if (alive && u) { setUrl(u); setFailed(false); return; }
        }
      } catch { /* ignore, keep polling */ }
      if (!alive) return;
      if (attempts > 90) { setFailed(true); return; }
      timer.current = setTimeout(poll, 800);
    };
    poll();
    return () => { alive = false; if (timer.current) clearTimeout(timer.current); };
  }, [reloadKey]);

  // Push theme changes to the iframe in real-time (no reload needed).
  useEffect(() => {
    iframeRef.current?.contentWindow?.postMessage(
      { type: 'wp-theme', value: 'dark' },
      '*',
    );
  }, [url]);

  const retry = () => { setUrl(null); setFailed(false); setErrorDetail(''); setReloadKey(k => k + 1); };

  if (failed) {
    return (
      <div className="agent-view-panel" style={centered}>
        <div style={{ color: 'var(--text-muted)', fontSize: 13, textAlign: 'center', maxWidth: 460 }}>
          WinPilot 服务启动失败或超时。<br />
          请确认 Python 依赖已安装（fastapi / uvicorn / pywin32 / opencv-python / mss）。
          {errorDetail && (
            <pre style={{
              marginTop: 10, padding: 8, textAlign: 'left', fontSize: 11,
              whiteSpace: 'pre-wrap', background: 'var(--surface-2, rgba(0,0,0,.06))',
              borderRadius: 6, maxHeight: 160, overflow: 'auto',
            }}>{errorDetail}</pre>
          )}
          <div style={{ marginTop: 12 }}>
            <button className="agent-btn agent-btn-primary" onClick={retry} style={{ padding: '6px 16px' }}>重试</button>
          </div>
        </div>
      </div>
    );
  }

  if (!url) {
    return (
      <div className="agent-view-panel" style={centered}>
        <div style={{ color: 'var(--text-muted)', fontSize: 13 }}>正在启动 WinPilot 服务…</div>
      </div>
    );
  }

  const src = `${url}?theme=dark`;

  return (
    <div className="agent-view-panel" style={{ padding: 0, height: '100%', width: '100%', maxWidth: 'none', margin: 0, overflow: 'hidden' }}>
      <iframe
        key={reloadKey}
        ref={iframeRef}
        className="winpilot-frame"
        src={src}
        title="WinPilot"
        allow="clipboard-read; clipboard-write"
      />
    </div>
  );
}

const centered: React.CSSProperties = {
  height: '100%',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
};
