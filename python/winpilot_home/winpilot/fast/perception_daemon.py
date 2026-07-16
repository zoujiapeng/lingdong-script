"""Shared change-triggered perception service keyed by target hwnd.

The watcher continuously performs only capture and image differencing. When a
meaningful ROI changes, this daemon coalesces noisy updates and precomputes a
small OCR/Win32 snapshot. Full fusion and VLM remain request/fallback paths.
"""
from __future__ import annotations

import copy
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..config import CONFIG
from ..perception.fusion import Snapshot
from ..perception.incremental import (
    EvidenceCrop,
    analyze_changed_regions,
    dedupe_elements,
    element_region,
    regions_intersect,
)
from ..perception.model import UIElement
from ..utils.log import BUS, logger
from ..utils.screenshot import Region, window_region
from .element_cache import ElementCache
from .types import ScreenDelta
from .watcher import ChangeListener, ScreenWatcher

AnalyzeFn = Callable[[int, list[EvidenceCrop], dict[str, Any]], Snapshot]


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


@dataclass
class _PendingChange:
    generation: int
    crops: list[EvidenceCrop]
    regions: list[Region]
    action_delta: bool
    transient: bool
    metadata: dict[str, Any]
    cancel_event: threading.Event | None = None
    created_ms: int = field(default_factory=_now_ms)


@dataclass
class _IncrementalRecord:
    generation: int
    snapshot: Snapshot
    regions: list[Region]
    action_delta: bool
    transient: bool
    elapsed_ms: int
    created_ms: int = field(default_factory=_now_ms)


@dataclass
class _Entry:
    hwnd: int
    watcher: ScreenWatcher
    cache: ElementCache
    context: dict[str, Any] = field(default_factory=dict)
    references: int = 0
    last_used: float = field(default_factory=time.monotonic)
    last_region: Region | None = None
    listener: ChangeListener | None = None
    generation: int = 0
    pending: deque[_PendingChange] = field(default_factory=deque)
    history: deque[_IncrementalRecord] = field(default_factory=lambda: deque(maxlen=8))
    analysis_event: threading.Event = field(default_factory=threading.Event)
    analysis_stop: threading.Event = field(default_factory=threading.Event)
    lease_cancel: threading.Event = field(default_factory=threading.Event)
    analysis_thread: threading.Thread | None = None
    analysis_running: bool = False
    scheduled: int = 0
    completed: int = 0
    coalesced: int = 0
    superseded: int = 0
    skipped_noise: int = 0
    skipped_inactive: int = 0
    failed: int = 0
    last_analysis_ms: int = 0
    last_error: str = ""


@dataclass(frozen=True)
class PerceptionLease:
    hwnd: int
    watcher: ScreenWatcher
    cache: ElementCache
    run_id: str = ""


class PerceptionDaemon:
    """Keep Level-0 watchers and Level-1 ROI perception hot between runs."""

    def __init__(
        self,
        idle_ttl_s: float = 45.0,
        *,
        analyze_fn: AnalyzeFn | None = None,
        debounce_ms: int | None = None,
    ) -> None:
        self.idle_ttl_s = max(5.0, float(idle_ttl_s))
        configured_debounce = CONFIG.get(
            "fusion",
            "incremental_perception_debounce_ms",
            default=80,
        )
        self.debounce_ms = max(
            0,
            int(configured_debounce if debounce_ms is None else debounce_ms),
        )
        self._analyze_fn = analyze_fn or analyze_changed_regions
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

    def _ensure_worker(self, entry: _Entry) -> None:
        if entry.analysis_thread and entry.analysis_thread.is_alive():
            return
        entry.analysis_stop.clear()
        entry.analysis_thread = threading.Thread(
            target=self._analysis_loop,
            args=(entry.hwnd,),
            name=f"WinPilotPerception-{entry.hwnd}",
            daemon=True,
        )
        entry.analysis_thread.start()

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

                def listener(
                    delta: ScreenDelta,
                    crops: list[EvidenceCrop],
                    metadata: dict[str, Any],
                ) -> None:
                    self._schedule_change(target, delta, crops, metadata)

                entry.listener = listener
                add_listener = getattr(entry.watcher, "add_change_listener", None)
                if callable(add_listener):
                    add_listener(listener)
                setattr(entry.watcher, "_perception_daemon_managed", True)
                self._entries[target] = entry
            if entry.references == 0:
                # A reacquired hwnd starts a new lease. Reuse the expensive
                # watcher, but never expose semantic anchors or incremental
                # evidence collected for the prior owner. Results from an old
                # in-flight analysis are rejected by the new cancel token below.
                entry.history.clear()
                entry.cache.clear()
                entry.lease_cancel = threading.Event()
            entry.references += 1
            entry.last_used = time.monotonic()
            entry.context = {"run_id": run_id} if run_id else {}
            self._ensure_worker(entry)
            self._ensure_reaper()
            return PerceptionLease(target, entry.watcher, entry.cache, run_id=run_id)

    def release(self, lease: PerceptionLease | int) -> None:
        hwnd = int(lease.hwnd if isinstance(lease, PerceptionLease) else lease)
        with self._lock:
            entry = self._entries.get(hwnd)
            if entry is None:
                return
            entry.references = max(0, entry.references - 1)
            entry.last_used = time.monotonic()
            if entry.references == 0:
                entry.lease_cancel.set()
                entry.context = {}
                entry.coalesced += len(entry.pending)
                entry.pending.clear()
                entry.analysis_event.clear()

    def _schedule_change(
        self,
        hwnd: int,
        delta: ScreenDelta,
        crops: list[EvidenceCrop],
        metadata: dict[str, Any],
    ) -> None:
        if not bool(CONFIG.get("fusion", "incremental_perception_enabled", default=True)):
            return
        if not delta.changed or not crops:
            return
        max_crops = max(1, int(CONFIG.get(
            "fusion",
            "incremental_perception_max_crops",
            default=4,
        )))
        with self._lock:
            entry = self._entries.get(int(hwnd))
            if entry is None:
                return
            if entry.references <= 0:
                entry.skipped_inactive += 1
                return
            entry.generation += 1
            action_delta = bool(metadata.get("action_delta"))
            if not action_delta:
                min_ratio = float(CONFIG.get(
                    "fusion",
                    "incremental_perception_min_ratio",
                    default=0.00015,
                ))
                max_ratio = float(CONFIG.get(
                    "fusion",
                    "incremental_perception_max_ratio",
                    default=0.35,
                ))
                if float(delta.ratio) < min_ratio or float(delta.ratio) > max_ratio:
                    entry.skipped_noise += 1
                    return
            pending = _PendingChange(
                generation=entry.generation,
                crops=list(crops[:max_crops]),
                regions=list(delta.regions[:max_crops]),
                action_delta=action_delta,
                transient=bool(metadata.get("transient")),
                metadata={**entry.context, **dict(metadata)},
                cancel_event=entry.lease_cancel,
            )
            if not action_delta:
                for index in range(len(entry.pending) - 1, -1, -1):
                    if not entry.pending[index].action_delta:
                        entry.pending[index] = pending
                        entry.coalesced += 1
                        break
                else:
                    entry.pending.append(pending)
            else:
                entry.pending.append(pending)
            while len(entry.pending) > 4:
                normal_index = next((
                    index for index, item in enumerate(entry.pending)
                    if not item.action_delta
                ), None)
                if normal_index is None:
                    entry.pending.popleft()
                else:
                    del entry.pending[normal_index]
                entry.coalesced += 1
            entry.scheduled += 1
            entry.analysis_event.set()
            self._ensure_worker(entry)

    def _next_pending(self, entry: _Entry) -> _PendingChange | None:
        if not entry.pending:
            entry.analysis_event.clear()
            return None
        action_index = next((
            index for index, item in enumerate(entry.pending)
            if item.action_delta
        ), None)
        if action_index is not None:
            pending = entry.pending[action_index]
            del entry.pending[action_index]
        else:
            pending = entry.pending.pop()
            entry.coalesced += len(entry.pending)
            entry.pending.clear()
        if not entry.pending:
            entry.analysis_event.clear()
        return pending

    def _analysis_loop(self, hwnd: int) -> None:
        while True:
            with self._lock:
                entry = self._entries.get(hwnd)
                if entry is None:
                    return
                event = entry.analysis_event
                stop = entry.analysis_stop
            if stop.is_set():
                return
            if not event.wait(timeout=0.5):
                continue
            if stop.wait(self.debounce_ms / 1000.0):
                return
            with self._lock:
                entry = self._entries.get(hwnd)
                if entry is None or entry.analysis_stop.is_set():
                    return
                if entry.references <= 0:
                    entry.coalesced += len(entry.pending)
                    entry.pending.clear()
                    entry.analysis_event.clear()
                    continue
                pending = self._next_pending(entry)
                if pending is None:
                    continue
                if pending.cancel_event is not None and pending.cancel_event.is_set():
                    entry.coalesced += 1
                    continue
                entry.analysis_running = True
            started = time.monotonic()
            try:
                snapshot = self._analyze_fn(
                    hwnd,
                    pending.crops,
                    {
                        **pending.metadata,
                        "generation": pending.generation,
                        "regions": pending.regions,
                        "action_delta": pending.action_delta,
                        "transient": pending.transient,
                        "cancel_event": pending.cancel_event,
                    },
                )
                if not isinstance(snapshot, Snapshot):
                    raise TypeError("incremental analyzer must return Snapshot")
            except Exception as exc:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                with self._lock:
                    current = self._entries.get(hwnd)
                    if current is not None:
                        current.analysis_running = False
                        current.failed += 1
                        current.last_analysis_ms = elapsed_ms
                        current.last_error = str(exc)[:300]
                logger.debug("incremental perception failed", exc_info=True)
                continue

            elapsed_ms = int((time.monotonic() - started) * 1000)
            record = _IncrementalRecord(
                generation=pending.generation,
                snapshot=snapshot,
                regions=pending.regions,
                action_delta=pending.action_delta,
                transient=pending.transient,
                elapsed_ms=elapsed_ms,
            )
            with self._lock:
                current = self._entries.get(hwnd)
                if current is None:
                    return
                if (
                    pending.cancel_event is not None
                    and (
                        pending.cancel_event.is_set()
                        or current.lease_cancel is not pending.cancel_event
                        or current.references <= 0
                    )
                ):
                    current.analysis_running = False
                    current.superseded += 1
                    if current.pending:
                        current.analysis_event.set()
                    continue
                retained: list[_IncrementalRecord] = []
                for old in current.history:
                    overlaps = any(
                        regions_intersect(old_region, new_region)
                        for old_region in old.regions
                        for new_region in record.regions
                    )
                    preserve_transient = (
                        old.action_delta
                        and old.transient
                        and not record.action_delta
                    )
                    if not overlaps or preserve_transient:
                        retained.append(old)
                    else:
                        current.superseded += 1
                current.history = deque(retained, maxlen=8)
                current.history.append(record)
                current.analysis_running = False
                current.completed += 1
                current.last_analysis_ms = elapsed_ms
                current.last_error = ""
                if current.pending:
                    current.analysis_event.set()
            texts = list(dict.fromkeys(
                str(element.text or "").strip()
                for element in snapshot.elements
                if str(element.text or "").strip()
            ))[:12]
            reserved = {
                "type", "source", "dimension", "hwnd", "generation",
                "action_delta", "transient", "count", "elapsed_ms",
                "dims", "texts", "cancel_event",
            }
            event_context = {
                key: value for key, value in pending.metadata.items()
                if key not in reserved
            }
            try:
                BUS.publish(
                    "perception_prefetch",
                    **event_context,
                    source="perception_daemon",
                    dimension="incremental_ocr_win32",
                    hwnd=hwnd,
                    generation=pending.generation,
                    action_delta=pending.action_delta,
                    transient=pending.transient,
                    count=len(snapshot.elements),
                    elapsed_ms=elapsed_ms,
                    dims=snapshot.dim_stats,
                    texts=texts,
                )
            except Exception:
                logger.debug("incremental perception event publish failed", exc_info=True)

    def incremental_elements(
        self,
        hwnd: int,
        *,
        regions: list[Region] | None = None,
        max_age_ms: int = 30_000,
        wait_ms: int = 0,
    ) -> tuple[list[UIElement], dict[str, Any]]:
        """Return copied Level-1 elements intersecting the requested ROIs."""
        deadline = time.monotonic() + max(0, int(wait_ms)) / 1000.0
        while True:
            with self._lock:
                entry = self._entries.get(int(hwnd))
                if entry is None:
                    return [], {"hit": False, "source": "perception_daemon"}
                now = _now_ms()
                records = [
                    record for record in reversed(entry.history)
                    if now - record.created_ms <= max(0, int(max_age_ms))
                    and (
                        not regions
                        or any(
                            regions_intersect(record_region, query_region)
                            for record_region in record.regions
                            for query_region in regions
                        )
                    )
                ]
                pending = bool(entry.pending or entry.analysis_running)
                latest_generation = entry.generation
            if records or not pending or time.monotonic() >= deadline:
                break
            time.sleep(0.01)

        selected: list[UIElement] = []
        transient = False
        elapsed_ms = 0
        generations: list[int] = []
        for record in records:
            transient = transient or record.transient
            elapsed_ms = max(elapsed_ms, record.elapsed_ms)
            generations.append(record.generation)
            for element in record.snapshot.elements:
                if regions and not any(
                    regions_intersect(element_region(element), region)
                    for region in regions
                ):
                    continue
                selected.append(copy.deepcopy(element))
        selected = dedupe_elements(selected)
        return selected, {
            "hit": bool(selected),
            "source": "perception_daemon",
            "source_elapsed_ms": elapsed_ms,
            "transient": transient,
            "generations": generations[:8],
            "latest_generation": latest_generation,
            "inflight": pending,
            "record_count": len(records),
        }

    def latest_snapshot(self, hwnd: int, *, max_age_ms: int = 30_000) -> Snapshot | None:
        with self._lock:
            entry = self._entries.get(int(hwnd))
            if entry is None:
                return None
            now = _now_ms()
            record = next((
                item for item in reversed(entry.history)
                if now - item.created_ms <= max(0, int(max_age_ms))
            ), None)
            return copy.deepcopy(record.snapshot) if record is not None else None

    def status(self) -> dict[str, Any]:
        with self._lock:
            now = _now_ms()
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
                        "incremental": {
                            "generation": item.generation,
                            "pending": len(item.pending),
                            "running": item.analysis_running,
                            "history": len(item.history),
                            "scheduled": item.scheduled,
                            "completed": item.completed,
                            "coalesced": item.coalesced,
                            "superseded": item.superseded,
                            "skipped_noise": item.skipped_noise,
                            "skipped_inactive": item.skipped_inactive,
                            "failed": item.failed,
                            "last_analysis_ms": item.last_analysis_ms,
                            "last_error": item.last_error,
                            "latest_age_ms": (
                                max(0, now - item.history[-1].created_ms)
                                if item.history else None
                            ),
                            "latest_elements": (
                                len(item.history[-1].snapshot.elements)
                                if item.history else 0
                            ),
                        },
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
            remove_listener = getattr(entry.watcher, "remove_change_listener", None)
            if callable(remove_listener) and entry.listener is not None:
                remove_listener(entry.listener)
            entry.analysis_stop.set()
            entry.analysis_event.set()
            entry.watcher.stop()
            if entry.analysis_thread and entry.analysis_thread.is_alive():
                entry.analysis_thread.join(timeout=1.5)
        return len(removed)

    def shutdown(self) -> None:
        self._stop.set()
        self.prune(force=True)

    def _reap_loop(self) -> None:
        while not self._stop.wait(min(5.0, self.idle_ttl_s / 2.0)):
            self.prune()


PERCEPTION_DAEMON = PerceptionDaemon()
