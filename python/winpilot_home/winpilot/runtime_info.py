"""Runtime fingerprint helpers for debugging the live WinPilot service."""
from __future__ import annotations

import importlib.util
import hashlib
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


def _source_stamp() -> str:
    """Fingerprint Python sources that are already loaded by this service."""
    roots = [ROOT_DIR / "winpilot", ROOT_DIR / "serve.py"]
    records: list[str] = []
    for root in roots:
        paths = root.rglob("*.py") if root.is_dir() else [root]
        for path in paths:
            try:
                stat = path.stat()
                relative = path.relative_to(ROOT_DIR)
            except (OSError, ValueError):
                continue
            records.append(f"{relative.as_posix()}\0{stat.st_size}\0{stat.st_mtime_ns}")
    payload = "\n".join(sorted(records)).encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()[:16]


_LOADED_COMMIT = _git(["rev-parse", "--short=12", "HEAD"])
_LOADED_SOURCE_STAMP = _source_stamp()


def runtime_source_drifted() -> bool:
    """Return True when files changed after this Python process imported them."""
    return _source_stamp() != _LOADED_SOURCE_STAMP


def runtime_fingerprint() -> dict[str, Any]:
    """Return non-secret identity data for the currently running service."""
    current_commit = _git(["rev-parse", "--short=12", "HEAD"])
    current_source_stamp = _source_stamp()
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
            # ``commit`` remains the process-loaded revision for compatibility.
            # Previously it was recomputed and could falsely make a stale
            # long-lived service look current after a checkout or update.
            "commit": _LOADED_COMMIT,
            "loaded_commit": _LOADED_COMMIT,
            "current_commit": current_commit,
            "dirty": _git_dirty(),
        },
        "source": {
            "loaded_stamp": _LOADED_SOURCE_STAMP,
            "current_stamp": current_source_stamp,
            "drifted": current_source_stamp != _LOADED_SOURCE_STAMP,
        },
        "capabilities": {
            "runtime_profile": _has_module("winpilot.agent.runtime_profile"),
            "automation_ir": _has_module("winpilot.automation_ir"),
            "replay_jobs": True,
            "scheduler": _has_module("winpilot.scheduler"),
            "desktop_action_queue": _has_module("winpilot.action.desktop_queue"),
        },
    }
