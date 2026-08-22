"""Runtime fingerprint helpers for debugging the live WinPilot service."""
from __future__ import annotations

import importlib.util
import hashlib
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .config import ROOT_DIR


RUNTIME_CONTRACT_VERSION = 3

# Source drift and Git identity are diagnostics only. Keep source refreshes
# short enough that the status page remains useful without putting either fact
# on the task execution path.
_SOURCE_STAMP_TTL_S = 0.25
_GIT_STATE_TTL_S = 1.0
_monotonic = time.monotonic


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
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


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

_SOURCE_CACHE_LOCK = threading.Lock()
_GIT_CACHE_LOCK = threading.Lock()
_source_cache_stamp = _LOADED_SOURCE_STAMP
_source_cache_expires_at = _monotonic() + _SOURCE_STAMP_TTL_S
_source_drift_observed = False
_git_cache_state: tuple[str, str, bool | None] | None = None
_git_cache_expires_at = 0.0

_CAPABILITIES = {
    "automation_ir": _has_module("winpilot.automation_ir"),
    "replay_jobs": True,
    "scheduler": False,
    "desktop_action_queue": _has_module("winpilot.action.desktop_queue"),
}


def _cached_source_state() -> tuple[str, bool]:
    """Return the current source stamp and sticky drift state.

    The lock intentionally covers the filesystem scan. This avoids a request
    burst starting the same expensive scan in several server threads. Once
    drift has been observed it remains true for the life of the process: a
    stale interpreter must not appear compatible merely because files were
    changed back while it was running.
    """
    global _source_cache_stamp, _source_cache_expires_at, _source_drift_observed

    with _SOURCE_CACHE_LOCK:
        if _source_drift_observed:
            return _source_cache_stamp, True

        now = _monotonic()
        if now < _source_cache_expires_at:
            return _source_cache_stamp, False

        stamp = _source_stamp()
        _source_cache_stamp = stamp
        _source_cache_expires_at = _monotonic() + _SOURCE_STAMP_TTL_S
        if stamp != _LOADED_SOURCE_STAMP:
            _source_drift_observed = True
        return stamp, _source_drift_observed


def _read_git_state() -> tuple[str, str, bool | None]:
    current_commit = _git(["rev-parse", "--short=12", "HEAD"])
    branch = _git(["branch", "--show-current"])
    if not branch:
        branch = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    return current_commit, branch, _git_dirty()


def _cached_git_state() -> tuple[str, str, bool | None]:
    global _git_cache_state, _git_cache_expires_at

    with _GIT_CACHE_LOCK:
        now = _monotonic()
        if _git_cache_state is not None and now < _git_cache_expires_at:
            return _git_cache_state

        _git_cache_state = _read_git_state()
        _git_cache_expires_at = _monotonic() + _GIT_STATE_TTL_S
        return _git_cache_state


def _reset_runtime_caches_for_tests() -> None:
    """Reset process-local caches; intended only for deterministic tests."""
    global _source_cache_stamp, _source_cache_expires_at, _source_drift_observed
    global _git_cache_state, _git_cache_expires_at

    with _SOURCE_CACHE_LOCK:
        _source_cache_stamp = _LOADED_SOURCE_STAMP
        _source_cache_expires_at = 0.0
        _source_drift_observed = False
    with _GIT_CACHE_LOCK:
        _git_cache_state = None
        _git_cache_expires_at = 0.0


def runtime_source_drifted() -> bool:
    """Return True when files changed after this Python process imported them."""
    return _cached_source_state()[1]


def runtime_fingerprint() -> dict[str, Any]:
    """Return non-secret identity data for the currently running service."""
    current_source_stamp, source_drifted = _cached_source_state()
    current_commit, branch, dirty = _cached_git_state()
    return {
        "contract_version": RUNTIME_CONTRACT_VERSION,
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
            "dirty": dirty,
        },
        "source": {
            "loaded_stamp": _LOADED_SOURCE_STAMP,
            "current_stamp": current_source_stamp,
            "drifted": source_drifted,
        },
        "capabilities": dict(_CAPABILITIES),
    }
