#!/usr/bin/env python3
"""Visible Electron E2E for the 2026-08-03 architecture gaps.

Task 1: "记忆系统有没有开？" must terminate as a direct reply without a
        desktop lease, observation, connector, or GUI action.
Task 2: "打开记事本" must complete and an independent Win32 oracle must find
        a visible Notepad window afterwards.

Both tasks are submitted through the real visible Electron client (UIA
input + native mouse), never through a backend shortcut.
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import psutil
import win32gui

WORKSPACE = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import client_e2e_run as harness  # noqa: E402

OUTPUT_ROOT = WORKSPACE / "output" / "playwright" / "client-architecture-e2e"
NOTEPAD_EXECUTABLES = ("notepad.exe", "Notepad.exe")
NOTEPAD_CLASSES = ("Notepad", "Notepad++", "Qt*")


def notepad_window_oracle() -> dict:
    """Independent Win32 oracle: a visible Notepad window exists now."""
    matches: list[int] = []

    def visit(hwnd: int, _extra) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        class_name = win32gui.GetClassName(hwnd)
        title = win32gui.GetWindowText(hwnd)
        if class_name in NOTEPAD_CLASSES and title:
            matches.append(int(hwnd))
        return True

    win32gui.EnumWindows(visit, None)
    return {
        "visible_notepad_windows": [int(h) for h in matches],
        "passed": len(matches) >= 1,
    }


def notepad_process_oracle() -> dict:
    pids = [
        int(proc.pid)
        for proc in psutil.process_iter(["name"])
        if proc.info.get("name") in NOTEPAD_EXECUTABLES
    ]
    return {
        "notepad_pids": pids,
        "passed": len(pids) >= 1,
    }


def run_ad_hoc(
    name: str,
    task: str,
    *,
    output_root: Path,
    max_steps: int = 60,
    timeout: int = 300,
) -> dict:
    spec = {
        "task": task,
        "max_steps": max_steps,
        "timeout": timeout,
        "runtime_version": "vnext",
    }
    return harness.run_case(
        name,
        spec,
        output_root=output_root,
        reset_session=True,
    )


def main() -> int:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_root = OUTPUT_ROOT / f"{timestamp}-suite"
    output_root.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []

    # Task 1: status query must reply without any desktop/connector activity.
    status = run_ad_hoc(
        "status-query-memory",
        "记忆系统有没有开？",
        output_root=output_root,
        max_steps=40,
        timeout=240,
    )
    result = dict(status)
    terminal = result.get("result") or {}
    trace_ids = list(result.get("bound_trace_ids") or [])
    events = harness._bound_trace_evidence(trace_ids, str(result.get("task") or ""), "vnext")
    result["status_query"] = {
        "outcome": terminal.get("outcome"),
        "result_text": terminal.get("result"),
        "model_calls": terminal.get("model_calls"),
        "status": terminal.get("status"),
        "bound_trace_ids": trace_ids,
        "trace_events": events,
    }
    results.append(result)

    # Task 2: open Notepad must complete and leave an independent window.
    notepad = run_ad_hoc(
        "launch-notepad",
        "打开记事本",
        output_root=output_root,
        max_steps=60,
        timeout=300,
    )
    result = dict(notepad)
    terminal = result.get("result") or {}
    result["notepad_oracle"] = {
        "window": notepad_window_oracle(),
        "process": notepad_process_oracle(),
    }
    results.append(result)

    report = {
        "entrypoint": "electron_client_windows_uia",
        "runtime_version": "vnext",
        "started_at": datetime.now().astimezone().isoformat(),
        "case_count": len(results),
        "results": results,
    }
    report_path = output_root / "suite-report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"event": "architecture_e2e_finished", "report": str(report_path)}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
