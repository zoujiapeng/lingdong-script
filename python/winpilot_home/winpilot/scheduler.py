"""Persistent scheduled WinPilot Agent tasks.

This is the WinPilot-Agent-side scheduler, separate from the legacy Electron
template scheduler. It stores natural-language agent tasks and lets the server
start them later through the same AgentRunner path as /api/chat.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import ROOT_DIR

SCHEDULES_PATH = Path(
    os.environ.get("WINPILOT_SCHEDULES_PATH")
    or ROOT_DIR / "agent_schedules.json"
)
_lock = threading.Lock()
_CREATE_DEDUPE_WINDOW_S = 30.0


def _task_key(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def reminder_message(task: str) -> str:
    """Return a literal message for a reminder-only scheduled task.

    Reminder delivery is deterministic runtime work, not an open-ended Agent
    planning problem. Mixed tasks (open/calculate/send/etc.) deliberately do
    not match and continue through AgentRunner.
    """
    text = str(task or "").strip()
    folded = text.casefold()
    if not ("提醒" in text or re.search(r"\bremind\b", folded)):
        return ""
    if any(marker in folded for marker in (
        "打开", "启动", "运行", "计算", "发送", "搜索", "保存", "写入",
        "open ", "launch", "start ", "calculate", "send ", "search ", "save ",
    )):
        return ""
    quoted = re.findall(r'["“「]([^"”」]{1,240})["”」]', text)
    if quoted:
        return quoted[-1].strip()
    chinese = re.sub(r"^.*?提醒(?:我|用户)?", "", text, count=1).strip(" ，,：:。")
    english = re.sub(r"^.*?\bremind(?:\s+me)?\b", "", text, count=1, flags=re.IGNORECASE).strip(" ,:.")
    message = chinese if chinese and chinese != text else english
    return message[:240] or "提醒时间到了"


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
    SCHEDULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = SCHEDULES_PATH.with_suffix(SCHEDULES_PATH.suffix + ".tmp")
    temporary.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, SCHEDULES_PATH)


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
    run_at_text = str(run_at or "").strip()
    due = _parse_time(run_at_text)
    if run_at_text and due is None:
        raise ValueError("run_at must be an ISO-8601 datetime")
    if due is None:
        try:
            delay = float(delay_seconds) if delay_seconds is not None else 0.0
        except (TypeError, ValueError):
            raise ValueError("delay_seconds must be a number") from None
        due = _now() + timedelta(seconds=max(0.0, delay))
    interval = None
    if interval_seconds is not None:
        try:
            interval = max(1.0, float(interval_seconds))
        except (TypeError, ValueError):
            raise ValueError("interval_seconds must be a number") from None
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
        now = _now()
        for existing in items:
            existing_due = _parse_time(existing.get("next_run_at"))
            existing_created = _parse_time(existing.get("created_at"))
            if (
                existing.get("enabled", True)
                and _task_key(existing.get("task")) == _task_key(task)
                and existing.get("interval_seconds") == interval
                and existing_due is not None
                and abs((existing_due - due).total_seconds()) <= _CREATE_DEDUPE_WINDOW_S
                and existing_created is not None
                and abs((now - existing_created).total_seconds()) <= _CREATE_DEDUPE_WINDOW_S
            ):
                duplicate = dict(existing)
                duplicate["deduplicated"] = True
                return duplicate
        items.append(entry)
        _write_unlocked(items)
    return entry


def update_task(schedule_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
    """Update a persisted Agent schedule using the same validation as create."""
    schedule_id = str(schedule_id or "").strip()
    allowed = {
        "name", "task", "enabled", "run_at", "delay_seconds",
        "interval_seconds", "max_steps",
    }
    updates = {key: value for key, value in dict(changes or {}).items() if key in allowed}
    with _lock:
        items = _read_unlocked()
        entry = next((item for item in items if item.get("id") == schedule_id), None)
        if entry is None:
            return None
        if "task" in updates:
            task = str(updates["task"] or "").strip()
            if not task:
                raise ValueError("task is required")
            entry["task"] = task
        if "name" in updates:
            entry["name"] = str(updates["name"] or "").strip() or str(entry.get("task") or "")[:60]
        if "enabled" in updates:
            entry["enabled"] = bool(updates["enabled"])
            if entry["enabled"]:
                entry.pop("cancelled_at", None)
        if "interval_seconds" in updates:
            raw_interval = updates["interval_seconds"]
            if raw_interval in (None, ""):
                entry["interval_seconds"] = None
            else:
                try:
                    entry["interval_seconds"] = max(1.0, float(raw_interval))
                except (TypeError, ValueError):
                    raise ValueError("interval_seconds must be a number") from None
        if "max_steps" in updates:
            raw_steps = updates["max_steps"]
            if raw_steps in (None, ""):
                entry.pop("max_steps", None)
            else:
                try:
                    max_steps = int(raw_steps)
                except (TypeError, ValueError):
                    raise ValueError("max_steps must be a positive integer") from None
                if max_steps <= 0:
                    raise ValueError("max_steps must be a positive integer")
                entry["max_steps"] = max_steps
        if "run_at" in updates or "delay_seconds" in updates:
            run_at = str(updates.get("run_at") or "").strip()
            due = _parse_time(run_at)
            if run_at and due is None:
                raise ValueError("run_at must be an ISO-8601 datetime")
            if due is None:
                try:
                    delay = float(updates.get("delay_seconds") or 0.0)
                except (TypeError, ValueError):
                    raise ValueError("delay_seconds must be a number") from None
                due = _now() + timedelta(seconds=max(0.0, delay))
            entry["next_run_at"] = _iso(due)
        elif entry.get("enabled") and not entry.get("next_run_at"):
            entry["next_run_at"] = _iso(_now())
        entry["updated_at"] = _iso(_now())
        _write_unlocked(items)
        return dict(entry)


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
