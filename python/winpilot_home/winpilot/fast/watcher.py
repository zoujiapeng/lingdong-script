"""Low-cost screen watcher and change bulletin board."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from ..utils.screenshot import Region, capture
from .types import ScreenDelta
from .vision import changed_regions


CaptureFn = Callable[[Region | None], np.ndarray]
RegionProvider = Callable[[], Region | None]


@dataclass
class ScreenBulletin:
    """Latest cheap perception state for the world-model bulletin board."""

    sequence: int = 0
    running: bool = False
    region: Region | None = None
    last_changed: bool = False
    last_ratio: float = 0.0
    changed_regions: list[Region] = field(default_factory=list)
    capture_latency_ms: int = 0
    diff_latency_ms: int = 0
    last_sample_ms: int = 0
    last_change_ms: int = 0
    samples: int = 0

    def to_dict(self) -> dict:
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
            "capture_latency_ms": self.capture_latency_ms,
            "diff_latency_ms": self.diff_latency_ms,
            "last_sample_ms": self.last_sample_ms,
            "last_change_ms": self.last_change_ms,
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
        fps: float = 8.0,
        pixel_thresh: int = 12,
    ) -> None:
        self.region_provider = region_provider or (lambda: None)
        self.capture_fn = capture_fn
        self.fps = max(1.0, float(fps))
        self.pixel_thresh = int(pixel_thresh)
        self._lock = threading.Lock()
        self._sample_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._prev: np.ndarray | None = None
        self._prev_region: Region | None = None
        self._bulletin = ScreenBulletin()

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
        return delta

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
    ) -> None:
        now = _now_ms()
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
            if delta.changed:
                self._bulletin.last_change_ms = now
