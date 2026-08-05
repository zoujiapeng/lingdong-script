#!/usr/bin/env python3
"""Probe the running Electron client and managed runtime state."""

from __future__ import annotations

import json
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
for import_root in (WORKSPACE / "python" / "winpilot_home", SCRIPT_DIR):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from winpilot.utils.dpi import ensure_process_dpi_awareness

ensure_process_dpi_awareness()

import client_e2e_run as client_harness  # noqa: E402


def main() -> int:
    driver = client_harness.ElectronClientDriver(Path("output/probe"))
    status = driver.wait_for_runtime_ready()
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
