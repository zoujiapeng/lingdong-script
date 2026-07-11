"""Process-local perception leases shared across short Agent/Replay runs."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ..utils.screenshot import Region, window_region
from .element_cache import ElementCache
from .watcher import ScreenWatcher


@dataclass
class _Entry:
    hwnd: int
    watcher: ScreenWatcher
    cache: ElementCache
    context: dict[str, Any] = field(default_factory=dict)
    references: int = 0
    last_used: float = field(default_factory=time.monotonic)
    last_region: Region | None = None


@dataclass(frozen=True)
class PerceptionLease:
    hwnd: int
    watcher: ScreenWatcher
    cache: ElementCache


class PerceptionDaemon:
    def __init__(self, idle_ttl_s: float = 45.0) -> None:
        self.idle_ttl_s = max(5.0, float(idle_ttl_s))
        self._lock = threading.RLock()
        self._entries: dict[int, _Entry] = {}
        self._stop = threading.Event()
        self._reaper: threading.Thread | None = None

    def _ensure_reaper(self) -> None:
        if self._reaper and self._reaper.is_alive():
            return
        self._stop.clear()
        self._reaper = threading.Thread(
            target=self._reap_loop,
            name="WinPilotPerceptionDaemon",
            daemon=True,
        )
        self._reaper.start()

    def acquire(self, hwnd: int, *, run_id: str = "") -> PerceptionLease:
        target = int(hwnd)
        if target <= 0:
            raise ValueError("perception lease requires a real hwnd")
        with self._lock:
            entry = self._entries.get(target)
            if entry is None:
                holder: dict[str, _Entry] = {}

                def region_provider() -> Region | None:
                    current = holder.get("entry")
                    if current is None:
                        return None
                    try:
                        current.last_region = window_region(current.hwnd)
                    except Exception:
                        pass
                    return current.last_region

                def event_context() -> dict[str, Any]:
                    current = holder.get("entry")
                    return dict(current.context) if current else {}

                entry = _Entry(
                    hwnd=target,
                    watcher=ScreenWatcher(
                        region_provider=region_provider,
                        event_context_provider=event_context,
                    ),
                    cache=ElementCache(),
                )
                try:
                    entry.last_region = window_region(target)
                except Exception:
                    entry.last_region = None
                holder["entry"] = entry
                setattr(entry.watcher, "_perception_daemon_managed", True)
                self._entries[target] = entry
            entry.references += 1
            entry.last_used = time.monotonic()
            entry.context = {"run_id": run_id} if run_id else {}
            self._ensure_reaper()
            return PerceptionLease(target, entry.watcher, entry.cache)

    def release(self, lease: PerceptionLease | int) -> None:
        hwnd = int(lease.hwnd if isinstance(lease, PerceptionLease) else lease)
        with self._lock:
            entry = self._entries.get(hwnd)
            if entry is None:
                return
            entry.references = max(0, entry.references - 1)
            entry.last_used = time.monotonic()
            if entry.references == 0:
                entry.context = {}

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "entries": len(self._entries),
                "active_leases": sum(item.references for item in self._entries.values()),
                "targets": [
                    {
                        "hwnd": item.hwnd,
                        "references": item.references,
                        "idle_ms": int((time.monotonic() - item.last_used) * 1000),
                        "watcher": item.watcher.status(),
                        "element_cache": item.cache.stats(),
                    }
                    for item in self._entries.values()
                ],
            }

    def latest_frame(self, *, run_id: str = "", max_age_ms: int = 500):
        with self._lock:
            entries = sorted(self._entries.values(), key=lambda item: item.last_used, reverse=True)
            if run_id:
                entries = [
                    item for item in entries
                    if str(item.context.get("run_id") or "") == str(run_id)
                ]
            watchers = [item.watcher for item in entries]
        for watcher in watchers:
            latest = watcher.latest_frame(max_age_ms=max_age_ms)
            if latest is not None:
                return latest
        return None

    def prune(self, *, force: bool = False) -> int:
        now = time.monotonic()
        removed: list[_Entry] = []
        with self._lock:
            for hwnd, entry in list(self._entries.items()):
                if entry.references:
                    continue
                if not force and now - entry.last_used < self.idle_ttl_s:
                    continue
                removed.append(self._entries.pop(hwnd))
        for entry in removed:
            entry.watcher.stop()
        return len(removed)

    def shutdown(self) -> None:
        self._stop.set()
        self.prune(force=True)

    def _reap_loop(self) -> None:
        while not self._stop.wait(min(5.0, self.idle_ttl_s / 2.0)):
            self.prune()


PERCEPTION_DAEMON = PerceptionDaemon()
