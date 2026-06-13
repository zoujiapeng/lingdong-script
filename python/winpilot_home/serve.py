"""WinPilot server launcher for the embedded Electron Agent panel.

Run from this directory so `winpilot` is importable and config/traces/etc.
resolve to winpilot_home/. Usage: python serve.py [PORT]
"""
from __future__ import annotations

import sys

import uvicorn


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
    uvicorn.run("winpilot.server:app", host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
