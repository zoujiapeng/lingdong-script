"""WinPilot server launcher — the ONLY supported way to start WinPilot.

Always launch via:  python serve.py [PORT] [HOST]

Do NOT run:  uvicorn winpilot.server:app  (blocked by a guard in server.py)

The Electron main process (src/main/winpilotServer.ts) calls this script with a
dynamically chosen free port. Running uvicorn directly would bypass config
resolution and the startup guard, so it is intentionally blocked.
"""
from __future__ import annotations

import os
import sys

# Ensure the directory containing this file (winpilot_home/) is on sys.path
# so `winpilot` is importable regardless of cwd or how uvicorn resolves imports.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# Mark that this process was started via serve.py so server.py can verify it.
os.environ["WINPILOT_LAUNCHED_VIA_SERVE"] = "1"

import uvicorn


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
    uvicorn.run("winpilot.server:app", host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
