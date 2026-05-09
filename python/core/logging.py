"""
Structured logging and replay system.

Provides:
- StructuredLogger: captures every step and event for later analysis
- Replay: step-by-step replay of a previously recorded execution

All logging is thread-safe and JSON-serializable for export.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, is_dataclass
from typing import Any, Generator

from .events import Event
from .interfaces import StepRecord


def _serialize(obj: Any) -> Any:
    """Convert dataclasses and types to JSON-safe values."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, Exception):
        return {"error": str(obj), "type": type(obj).__name__}
    return str(obj)


class StructuredLogger:
    """Captures execution history for logging, analysis, and replay.

    Thread-safe. Each execution session appends to a shared timeline.
    """

    def __init__(self, max_history: int = 1000) -> None:
        self._lock = threading.Lock()
        self._max_history = max_history
        self._steps: list[StepRecord] = []
        self._events: list[Event] = []

    # ── Recording ──────────────────────────────────────────────

    def log_step(self, record: StepRecord) -> None:
        """Record a completed step in the execution history."""
        with self._lock:
            self._steps.append(record)
            if len(self._steps) > self._max_history:
                self._steps.pop(0)

    def log_event(self, event: Event) -> None:
        """Record a non-step event (log message, warning, etc.)."""
        with self._lock:
            self._events.append(event)
            if len(self._events) > self._max_history:
                self._events.pop(0)

    # ── Query ──────────────────────────────────────────────────

    def get_history(self) -> list[StepRecord]:
        """Return all step records (copy for thread safety)."""
        with self._lock:
            return list(self._steps)

    def get_events(self, event_type: str | None = None) -> list[Event]:
        """Return events, optionally filtered by type."""
        with self._lock:
            if event_type:
                return [e for e in self._events if e.type == event_type]
            return list(self._events)

    def get_summary(self) -> dict:
        """Return a brief execution summary."""
        with self._lock:
            successes = sum(1 for s in self._steps if s.result.success)
            failures = sum(1 for s in self._steps if not s.result.success)
            return {
                "total_steps": len(self._steps),
                "successful": successes,
                "failed": failures,
                "total_events": len(self._events),
                "last_step": self._steps[-1].step if self._steps else None,
            }

    def clear(self) -> None:
        """Clear all recorded history."""
        with self._lock:
            self._steps.clear()
            self._events.clear()

    # ── Export / Replay ─────────────────────────────────────────

    def export_replay(self) -> dict:
        """Export the full execution log as a JSON-serializable dict."""
        with self._lock:
            return {
                "version": 1,
                "exported_at": time.time(),
                "steps": [_serialize(s) for s in self._steps],
                "events": [_serialize(e) for e in self._events],
            }

    def export_json(self, path: str) -> None:
        """Export replay data to a JSON file."""
        data = self.export_replay()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @staticmethod
    def replay(replay_data: dict, speed: float = 1.0) -> Generator[StepRecord, None, None]:
        """Replay a previously exported execution step by step.

        Yields StepRecords in order with realistic timing gaps.
        """
        steps_data = replay_data.get("steps", [])
        prev_ts = None
        for s in steps_data:
            # Restore StepRecord from serialized dict
            if isinstance(s, dict):
                record = StepRecord(
                    step=s.get("step", 0),
                    tool=s.get("tool", ""),
                    params=s.get("params", {}),
                    result=s.get("result", {}),
                    timestamp=s.get("timestamp", 0),
                )
            else:
                record = s

            # Emit with realistic timing gap
            if prev_ts is not None and record.timestamp:
                gap = (record.timestamp - prev_ts) / speed
                if gap > 0:
                    time.sleep(min(gap, 5.0))  # cap at 5s real time
            prev_ts = record.timestamp or time.time()

            yield record
