"""Low-cost screen watcher and change bulletin board."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from ..utils.screenshot import Region, capture
from ..utils.log import BUS, logger
from .types import ScreenDelta
from .vision import changed_regions


CaptureFn = Callable[[Region | None], np.ndarray]
RegionProvider = Callable[[], Region | None]
EventContextProvider = Callable[[], dict[str, Any]]
ChangeListener = Callable[
    [ScreenDelta, list[tuple[np.ndarray, Region]], dict[str, Any]],
    None,
]


@dataclass
class ScreenBulletin:
    """Latest cheap perception state for the world-model bulletin board."""

    sequence: int = 0
    running: bool = False
    region: Region | None = None
    last_changed: bool = False
    last_ratio: float = 0.0
    changed_regions: list[Region] = field(default_factory=list)
    recent_changed_regions: list[Region] = field(default_factory=list)
    action_changed_regions: list[Region] = field(default_factory=list)
    capture_latency_ms: int = 0
    diff_latency_ms: int = 0
    last_sample_ms: int = 0
    last_change_ms: int = 0
    last_action_change_ms: int = 0
    action_evidence_transient: bool = False
    last_change_ratio: float = 0.0
    samples: int = 0

    def to_dict(self) -> dict:
        now = _now_ms()
        return {
            "sequence": self.sequence,
            "running": self.running,
            "region": (
                None if self.region is None else {
                    "left": self.region.left,
                    "top": self.region.top,
                    "width": self.region.width,
                    "height": self.region.height,
                }
            ),
            "last_changed": self.last_changed,
            "last_ratio": round(float(self.last_ratio), 6),
            "changed_regions": [
                {"left": r.left, "top": r.top, "width": r.width, "height": r.height}
                for r in self.changed_regions
            ],
            "recent_changed_regions": [
                {"left": r.left, "top": r.top, "width": r.width, "height": r.height}
                for r in self.recent_changed_regions
            ],
            "action_changed_regions": [
                {"left": r.left, "top": r.top, "width": r.width, "height": r.height}
                for r in self.action_changed_regions
            ],
            "capture_latency_ms": self.capture_latency_ms,
            "diff_latency_ms": self.diff_latency_ms,
            "last_sample_ms": self.last_sample_ms,
            "last_change_ms": self.last_change_ms,
            "last_change_age_ms": max(0, now - self.last_change_ms) if self.last_change_ms else 0,
            "last_action_change_ms": self.last_action_change_ms,
            "action_evidence_transient": self.action_evidence_transient,
            "last_action_change_age_ms": (
                max(0, now - self.last_action_change_ms)
                if self.last_action_change_ms else 0
            ),
            "last_change_ratio": round(float(self.last_change_ratio), 6),
            "samples": self.samples,
        }


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


class ScreenWatcher:
    """Continuously watches cheap frame deltas without semantic interpretation."""

    def __init__(
        self,
        region_provider: RegionProvider | None = None,
        capture_fn: CaptureFn = capture,
        fps: float = 3.0,
        pixel_thresh: int = 12,
        event_context_provider: EventContextProvider | None = None,
    ) -> None:
        self.region_provider = region_provider or (lambda: None)
        self.capture_fn = capture_fn
        self.fps = max(1.0, float(fps))
        self.pixel_thresh = int(pixel_thresh)
        self.event_context_provider = event_context_provider or (lambda: {})
        self._lock = threading.Lock()
        self._sample_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._prev: np.ndarray | None = None
        self._prev_region: Region | None = None
        self._bulletin = ScreenBulletin()
        self._last_event_ms = 0
        self._action_evidence: list[tuple[np.ndarray, Region]] = []
        self._recent_evidence: list[tuple[np.ndarray, Region]] = []
        self._recent_evidence_ms = 0
        self._change_listeners: list[ChangeListener] = []

    def add_change_listener(self, listener: ChangeListener) -> None:
        with self._lock:
            if listener not in self._change_listeners:
                self._change_listeners.append(listener)

    def remove_change_listener(self, listener: ChangeListener) -> None:
        with self._lock:
            if listener in self._change_listeners:
                self._change_listeners.remove(listener)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="screen-watcher")
        self._thread.start()
        with self._lock:
            self._bulletin.running = True

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        with self._lock:
            self._bulletin.running = False

    def status(self) -> dict:
        with self._lock:
            return self._bulletin.to_dict()

    def sample_once(self) -> ScreenDelta:
        with self._sample_lock:
            region = self.region_provider()
            capture_started = _now_ms()
            frame = self.capture_fn(region)
            capture_ms = max(0, _now_ms() - capture_started)
            diff_started = _now_ms()
            prev = self._prev
            prev_region = self._prev_region
            if prev is None or prev_region != region:
                delta = ScreenDelta(False, 0.0, [], 0)
            else:
                delta = changed_regions(prev, frame, root_region=region, pixel_thresh=self.pixel_thresh)
            diff_ms = max(0, _now_ms() - diff_started)
            self._prev = frame
            self._prev_region = region
        self._update_bulletin(delta, region, capture_ms, diff_ms)
        if delta.changed:
            crops = self._crop_evidence(frame, delta, region)
            with self._lock:
                self._recent_evidence = crops
                self._recent_evidence_ms = _now_ms()
            self._notify_change(delta, crops, action_delta=False, transient=False)
        return delta

    def record_action_delta(
        self,
        delta: ScreenDelta,
        region: Region | None,
        capture_ms: int = 0,
        diff_ms: int | None = None,
        evidence_frame: np.ndarray | None = None,
        transient: bool = False,
    ) -> None:
        """Publish an independently measured action delta.

        The continuous watcher owns ``_prev`` and may sample between an action's
        before/after frames. Action verification therefore computes its own
        delta pair and records it here so changed-only perception can prefer
        the freshest action-caused ROI instead of unrelated background churn.
        """
        self._update_bulletin(
            delta,
            region,
            int(capture_ms),
            int(delta.elapsed_ms if diff_ms is None else diff_ms),
            action_delta=True,
        )
        crops: list[tuple[np.ndarray, Region]] = []
        if evidence_frame is not None and delta.changed:
            crops = self._crop_evidence(evidence_frame, delta, region)
            with self._lock:
                self._action_evidence = crops
                self._bulletin.action_evidence_transient = bool(transient)
        if delta.changed:
            self._notify_change(
                delta,
                crops,
                action_delta=True,
                transient=bool(transient),
            )

    def _notify_change(
        self,
        delta: ScreenDelta,
        crops: list[tuple[np.ndarray, Region]],
        *,
        action_delta: bool,
        transient: bool,
    ) -> None:
        with self._lock:
            listeners = list(self._change_listeners)
            sequence = int(self._bulletin.sequence)
        if not listeners:
            return
        try:
            context = dict(self.event_context_provider() or {})
        except Exception:
            context = {}
        metadata = {
            **context,
            "sequence": sequence,
            "action_delta": bool(action_delta),
            "transient": bool(transient),
            "created_ms": _now_ms(),
        }
        for listener in listeners:
            try:
                listener(delta, list(crops), dict(metadata))
            except Exception:
                logger.debug("screen change listener failed", exc_info=True)

    def action_evidence(self, max_age_ms: int = 5000) -> tuple[list[tuple[np.ndarray, Region]], bool]:
        with self._lock:
            age = _now_ms() - self._bulletin.last_action_change_ms if self._bulletin.last_action_change_ms else 10**9
            if age > max(0, int(max_age_ms)):
                return [], False
            return list(self._action_evidence), bool(self._bulletin.action_evidence_transient)

    def recent_evidence(self, max_age_ms: int = 1500) -> list[tuple[np.ndarray, Region]]:
        """Return the newest watcher-triggered ROI crops for async perception."""
        with self._lock:
            age = _now_ms() - self._recent_evidence_ms if self._recent_evidence_ms else 10**9
            if age > max(0, int(max_age_ms)):
                return []
            return list(self._recent_evidence)

    def latest_frame(self, max_age_ms: int = 500) -> tuple[np.ndarray, Region | None] | None:
        """Return a cheap copy of the latest Level-0 frame for tracing/debug."""
        with self._sample_lock:
            frame = self._prev
            region = self._prev_region
            with self._lock:
                sampled_at = self._bulletin.last_sample_ms
            if frame is None or not sampled_at or _now_ms() - sampled_at > max(0, int(max_age_ms)):
                return None
            return frame.copy(), region

    @staticmethod
    def _crop_evidence(
        frame: np.ndarray,
        delta: ScreenDelta,
        region: Region | None,
    ) -> list[tuple[np.ndarray, Region]]:
        root_left = region.left if region is not None else 0
        root_top = region.top if region is not None else 0
        frame_h, frame_w = frame.shape[:2]
        crops: list[tuple[np.ndarray, Region]] = []
        for changed in delta.regions[:8]:
            pad = 24
            left = max(root_left, changed.left - pad)
            top = max(root_top, changed.top - pad)
            right = min(root_left + frame_w, changed.right + pad)
            bottom = min(root_top + frame_h, changed.bottom + pad)
            if right <= left or bottom <= top:
                continue
            crop = frame[
                top - root_top:bottom - root_top,
                left - root_left:right - root_left,
            ].copy()
            crops.append((crop, Region(left, top, right - left, bottom - top)))
        return crops

    def wait_for_change(self, timeout_s: float = 3.0, min_ratio: float = 0.0005) -> ScreenDelta:
        deadline = time.monotonic() + float(timeout_s)
        last = self.sample_once()
        while time.monotonic() < deadline:
            time.sleep(1.0 / self.fps)
            last = self.sample_once()
            if last.changed and last.ratio >= float(min_ratio):
                return last
        return last

    def wait_for_stable(
        self,
        stable_ms: int = 500,
        timeout_s: float = 5.0,
        max_ratio: float = 0.0008,
    ) -> dict:
        deadline = time.monotonic() + float(timeout_s)
        stable_since: float | None = None
        last = self.sample_once()
        while time.monotonic() < deadline:
            time.sleep(1.0 / self.fps)
            last = self.sample_once()
            if last.ratio <= float(max_ratio):
                stable_since = stable_since or time.monotonic()
                if (time.monotonic() - stable_since) * 1000 >= int(stable_ms):
                    return {"stable": True, "delta": last.to_dict()}
            else:
                stable_since = None
        return {"stable": False, "delta": last.to_dict()}

    def _loop(self) -> None:
        interval = 1.0 / self.fps
        while not self._stop.is_set():
            self.sample_once()
            time.sleep(interval)
        with self._lock:
            self._bulletin.running = False

    def _update_bulletin(
        self,
        delta: ScreenDelta,
        region: Region | None,
        capture_ms: int,
        diff_ms: int,
        action_delta: bool = False,
    ) -> None:
        now = _now_ms()
        publish_event = False
        sequence = 0
        with self._lock:
            self._bulletin.sequence += 1
            self._bulletin.region = region
            self._bulletin.last_changed = delta.changed
            self._bulletin.last_ratio = delta.ratio
            self._bulletin.changed_regions = list(delta.regions)
            self._bulletin.capture_latency_ms = capture_ms
            self._bulletin.diff_latency_ms = diff_ms
            self._bulletin.last_sample_ms = now
            self._bulletin.samples += 1
            sequence = self._bulletin.sequence
            if delta.changed:
                self._bulletin.last_change_ms = now
                self._bulletin.last_change_ratio = delta.ratio
                self._bulletin.recent_changed_regions = list(delta.regions)
                if action_delta:
                    self._bulletin.last_action_change_ms = now
                    self._bulletin.action_changed_regions = list(delta.regions)
                if not action_delta and now - self._last_event_ms >= 80:
                    self._last_event_ms = now
                    publish_event = True
        if publish_event:
            try:
                context = self.event_context_provider() or {}
            except Exception:
                context = {}
            BUS.publish(
                "screen_watch",
                **context,
                sequence=sequence,
                changed=True,
                ratio=round(float(delta.ratio), 6),
                regions=[
                    {"left": r.left, "top": r.top, "width": r.width, "height": r.height}
                    for r in delta.regions
                ],
                capture_latency_ms=int(capture_ms),
                diff_latency_ms=int(diff_ms),
            )
