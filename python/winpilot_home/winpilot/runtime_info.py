"""Runtime fingerprint helpers for debugging the live WinPilot service."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import ROOT_DIR


def repo_root() -> Path:
    return ROOT_DIR.parent.parent


def _git(args: list[str], timeout_s: float = 1.5) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_root(),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except Exception:
        return ""
    if completed.returncode != 0:
        return ""
    return (completed.stdout or "").strip()


def _git_dirty() -> bool | None:
    status = _git(["status", "--porcelain"], timeout_s=2.0)
    if status:
        return True
    inside = _git(["rev-parse", "--is-inside-work-tree"], timeout_s=1.0)
    if inside == "true":
        return False
    return None


def _has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def runtime_fingerprint() -> dict[str, Any]:
    """Return non-secret identity data for the currently running service."""
    commit = _git(["rev-parse", "--short=12", "HEAD"])
    branch = _git(["branch", "--show-current"])
    if not branch:
        branch = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    return {
        "pid": os.getpid(),
        "cwd": os.getcwd(),
        "argv": list(sys.argv),
        "winpilot_home": str(ROOT_DIR),
        "repo_root": str(repo_root()),
        "launcher": "serve.py" if os.environ.get("WINPILOT_LAUNCHED_VIA_SERVE") else "unknown",
        "host": os.environ.get("WINPILOT_SERVE_HOST") or "",
        "port": os.environ.get("WINPILOT_SERVE_PORT") or "",
        "git": {
            "branch": branch,
            "commit": commit,
            "dirty": _git_dirty(),
        },
        "capabilities": {
            "runtime_profile": _has_module("winpilot.agent.runtime_profile"),
            "automation_ir": _has_module("winpilot.automation_ir"),
            "replay_jobs": True,
            "scheduler": _has_module("winpilot.scheduler"),
            "desktop_action_queue": _has_module("winpilot.action.desktop_queue"),
        },
    }
