"""
State persistence — lightweight agent run history.

Saves last N run summaries to ~/.lingdong/agent_history.json.
Loaded on startup to provide context continuity across agent restarts.
"""

import json
import logging
import os
from dataclasses import dataclass, asdict
from typing import Optional

logger = logging.getLogger("agent.persistence")

LINGDONG_HOME = os.path.join(os.path.expanduser("~"), ".lingdong")
HISTORY_FILE = os.path.join(LINGDONG_HOME, "agent_history.json")
MAX_HISTORY = 50


@dataclass
class RunSummary:
    """Summary of a single agent run."""
    goal: str
    mode: str
    model: str
    success: bool
    total_steps: int
    elapsed_ms: int
    timestamp: str
    summary: str = ""
    error: str = ""


def _ensure_dir():
    os.makedirs(LINGDONG_HOME, exist_ok=True)


def _load_all() -> list[dict]:
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "runs" in data:
            return data["runs"]
        return []
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load agent history: %s", e)
        return []


def _save_all(entries: list[dict]):
    _ensure_dir()
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump({"runs": entries, "version": 2}, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning("Failed to save agent history: %s", e)


def save_run(summary: RunSummary):
    """Append a run summary, trimming to MAX_HISTORY."""
    entries = _load_all()
    entries.append(asdict(summary))
    if len(entries) > MAX_HISTORY:
        entries = entries[-MAX_HISTORY:]
    _save_all(entries)


def load_history(limit: int = 10) -> list[RunSummary]:
    """Load the most recent run summaries."""
    entries = _load_all()
    return [RunSummary(**e) for e in entries[-limit:]]


def get_stats() -> dict:
    """Aggregate statistics from history."""
    entries = _load_all()
    if not entries:
        return {"total_runs": 0, "success_rate": 0.0, "avg_steps": 0, "avg_elapsed_ms": 0}
    successes = sum(1 for e in entries if e.get("success", False))
    total = len(entries)
    avg_steps = sum(e.get("total_steps", 0) for e in entries) / total
    avg_elapsed = sum(e.get("elapsed_ms", 0) for e in entries) / total
    modes: dict[str, int] = {}
    for e in entries:
        m = e.get("mode", "unknown")
        modes[m] = modes.get(m, 0) + 1
    return {
        "total_runs": total,
        "success_rate": round(successes / total, 3),
        "avg_steps": round(avg_steps, 1),
        "avg_elapsed_ms": round(avg_elapsed, 0),
        "by_mode": modes,
    }


def clear():
    """Clear all history."""
    _ensure_dir()
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump({"runs": [], "version": 2}, f)
    except OSError:
        pass
