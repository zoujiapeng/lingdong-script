"""Persistent scheduled WinPilot Agent tasks.

This is the WinPilot-Agent-side scheduler, separate from the legacy Electron
template scheduler. It stores natural-language agent tasks and lets the server
start them later through the same AgentRunner path as /api/chat.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import ROOT_DIR

SCHEDULES_PATH = ROOT_DIR / "agent_schedules.json"
_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.astimezone(timezone.utc)


def _read_unlocked() -> list[dict[str, Any]]:
    if not SCHEDULES_PATH.exists():
        return []
    try:
        data = json.loads(SCHEDULES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def _write_unlocked(items: list[dict[str, Any]]) -> None:
    SCHEDULES_PATH.write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def list_tasks(enabled_only: bool = False) -> list[dict[str, Any]]:
    with _lock:
        items = _read_unlocked()
    if enabled_only:
        return [item for item in items if item.get("enabled", True)]
    return items


def create_task(
    task: str,
    *,
    name: str = "",
    run_at: str = "",
    delay_seconds: float | int | None = None,
    interval_seconds: float | int | None = None,
    enabled: bool = True,
    max_steps: int | None = None,
) -> dict[str, Any]:
    task = str(task or "").strip()
    if not task:
        raise ValueError("task is required")
    due = _parse_time(run_at)
    if due is None:
        try:
            delay = float(delay_seconds) if delay_seconds is not None else 0.0
        except (TypeError, ValueError):
            delay = 0.0
        due = _now() + timedelta(seconds=max(0.0, delay))
    interval = None
    if interval_seconds is not None:
        try:
            interval = max(1.0, float(interval_seconds))
        except (TypeError, ValueError):
            interval = None
    entry: dict[str, Any] = {
        "id": f"agent_sched_{uuid.uuid4().hex[:12]}",
        "name": str(name or "").strip() or task[:60],
        "task": task,
        "enabled": bool(enabled),
        "next_run_at": _iso(due),
        "interval_seconds": interval,
        "created_at": _iso(_now()),
        "last_run_at": None,
        "last_run_id": None,
        "last_status": None,
        "run_count": 0,
        "running": False,
    }
    if max_steps and int(max_steps) > 0:
        entry["max_steps"] = int(max_steps)
    with _lock:
        items = _read_unlocked()
        items.append(entry)
        _write_unlocked(items)
    return entry


def cancel_task(schedule_id: str) -> bool:
    schedule_id = str(schedule_id or "")
    with _lock:
        items = _read_unlocked()
        changed = False
        for item in items:
            if item.get("id") == schedule_id:
                item["enabled"] = False
                item["running"] = False
                item["cancelled_at"] = _iso(_now())
                changed = True
                break
        if changed:
            _write_unlocked(items)
    return changed


def due_tasks(limit: int = 4) -> list[dict[str, Any]]:
    now = _now()
    due: list[dict[str, Any]] = []
    with _lock:
        for item in _read_unlocked():
            if not item.get("enabled", True) or item.get("running"):
                continue
            next_run = _parse_time(item.get("next_run_at"))
            if next_run is not None and next_run <= now:
                due.append(dict(item))
                if len(due) >= limit:
                    break
    return due


def mark_started(schedule_id: str, run_id: str) -> None:
    with _lock:
        items = _read_unlocked()
        for item in items:
            if item.get("id") == schedule_id:
                item["running"] = True
                item["last_run_id"] = run_id
                item["last_started_at"] = _iso(_now())
                break
        _write_unlocked(items)


def mark_skipped(schedule_id: str, reason: str, *, defer_seconds: float = 30.0) -> None:
    with _lock:
        items = _read_unlocked()
        for item in items:
            if item.get("id") == schedule_id:
                item["last_status"] = f"skipped:{reason}"
                item["next_run_at"] = _iso(_now() + timedelta(seconds=max(1.0, defer_seconds)))
                item["running"] = False
                break
        _write_unlocked(items)


def mark_finished(schedule_id: str, run_id: str, status: str) -> None:
    now = _now()
    with _lock:
        items = _read_unlocked()
        for item in items:
            if item.get("id") != schedule_id:
                continue
            item["running"] = False
            item["last_run_at"] = _iso(now)
            item["last_run_id"] = run_id
            item["last_status"] = str(status or "")
            item["run_count"] = int(item.get("run_count") or 0) + 1
            interval = item.get("interval_seconds")
            if isinstance(interval, (int, float)) and interval > 0:
                item["next_run_at"] = _iso(now + timedelta(seconds=float(interval)))
            else:
                item["enabled"] = False
                item["next_run_at"] = None
            break
        _write_unlocked(items)


def reset_running_flags(max_age_s: float = 3600.0) -> None:
    cutoff = time.time() - max_age_s
    changed = False
    with _lock:
        items = _read_unlocked()
        for item in items:
            if not item.get("running"):
                continue
            started = _parse_time(item.get("last_started_at"))
            if started is None or started.timestamp() < cutoff:
                item["running"] = False
                item["last_status"] = "recovered_stale_running_flag"
                changed = True
        if changed:
            _write_unlocked(items)
