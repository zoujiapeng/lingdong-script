import React, { useEffect, useRef, useState } from 'react';

/**
 * AgentView — embeds WinPilot's native web UI (served by the bundled WinPilot
 * FastAPI server) in an iframe. This gives the Agent panel the exact WinPilot
 * experience: chat, full execution log (every BUS event), settings drawer,
 * traces, scripts and memory — all driven by WinPilot's own HTTP/WebSocket API.
 *
 * The Electron main process launches the server on app start; here we poll
 * `getWinpilotUrl` until it is reachable, then point the iframe at it.
 */
export function AgentView() {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let alive = true;
    let attempts = 0;
    const poll = async () => {
      attempts += 1;
      try {
        const u = await window.electronAPI?.getWinpilotUrl?.();
        if (alive && u) { setUrl(u); setFailed(false); return; }
      } catch { /* ignore */ }
      if (!alive) return;
      if (attempts > 90) { setFailed(true); return; } // ~72s
      timer.current = setTimeout(poll, 800);
    };
    poll();
    return () => { alive = false; if (timer.current) clearTimeout(timer.current); };
  }, [reloadKey]);

  const retry = () => { setUrl(null); setFailed(false); setReloadKey(k => k + 1); };

  if (failed) {
    return (
      <div className="agent-view-panel" style={centered}>
        <div style={{ color: 'var(--text-muted)', fontSize: 13, textAlign: 'center' }}>
          WinPilot 服务启动失败或超时。<br />
          请确认 Python 依赖已安装（fastapi / uvicorn）。
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

  return (
    <div className="agent-view-panel" style={{ padding: 0, height: '100%', width: '100%', maxWidth: 'none', margin: 0, overflow: 'hidden' }}>
      <iframe
        key={reloadKey}
        src={url}
        title="WinPilot"
        style={{ width: '100%', height: '100%', border: 'none', display: 'block', background: '#0d0f14' }}
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
