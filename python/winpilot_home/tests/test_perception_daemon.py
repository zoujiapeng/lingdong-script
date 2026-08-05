from __future__ import annotations

import time

import numpy as np
import pytest

from winpilot.config import CONFIG


@pytest.fixture(autouse=True)
def _pin_daemon_config(monkeypatch: pytest.MonkeyPatch):
    """Pin the daemon's own toggle so tests never depend on the machine config.

    The default product config keeps ambient auto-perception off (vision and
    shell first); these unit tests exercise the daemon schedule path itself.
    """
    monkeypatch.setattr(
        CONFIG,
        "_data",
        {**CONFIG._data, "fusion": {
            **dict(CONFIG._data.get("fusion") or {}),
            "incremental_perception_enabled": True,
            "incremental_perception_max_crops": 4,
            "incremental_perception_min_ratio": 0.00015,
            "incremental_perception_max_ratio": 0.35,
            "incremental_perception_debounce_ms": 0,
        }},
    )
    yield


def test_perception_daemon_reuses_watcher_and_cache_per_hwnd(monkeypatch):
    from winpilot.fast import perception_daemon as daemon_mod
    from winpilot.utils.screenshot import Region

    class FakeWatcher:
        def __init__(self, region_provider=None, event_context_provider=None):
            self.region_provider = region_provider
            self.event_context_provider = event_context_provider
            self.stopped = False

        def status(self):
            return {"running": not self.stopped}

        def stop(self):
            self.stopped = True

    monkeypatch.setattr(daemon_mod, "ScreenWatcher", FakeWatcher)
    monkeypatch.setattr(daemon_mod, "window_region", lambda hwnd: Region(10, 20, 300, 200))
    daemon = daemon_mod.PerceptionDaemon(idle_ttl_s=60)

    first = daemon.acquire(101, run_id="run-a")
    second = daemon.acquire(101, run_id="run-b")

    assert first.watcher is second.watcher
    assert first.cache is second.cache
    assert second.watcher.region_provider() == Region(10, 20, 300, 200)
    assert second.watcher.event_context_provider() == {"run_id": "run-b"}
    assert daemon.status()["active_leases"] == 2

    daemon.release(first)
    daemon.release(second)
    assert daemon.prune(force=True) == 1
    assert first.watcher.stopped is True


def test_perception_daemon_does_not_analyze_released_window_changes(monkeypatch):
    from winpilot.fast import perception_daemon as daemon_mod
    from winpilot.fast.types import ScreenDelta
    from winpilot.utils.screenshot import Region

    calls = []
    monkeypatch.setattr(daemon_mod, "ScreenWatcher", _EmittingWatcher)
    monkeypatch.setattr(daemon_mod, "window_region", lambda hwnd: Region(0, 0, 800, 600))
    daemon = daemon_mod.PerceptionDaemon(
        idle_ttl_s=60,
        analyze_fn=lambda *args: calls.append(args) or _snapshot_for_change(*args),
        debounce_ms=0,
    )
    lease = daemon.acquire(109, run_id="released-run")
    daemon.release(lease)
    region = Region(20, 30, 80, 40)
    lease.watcher.emit(
        ScreenDelta(True, 0.02, [region], 1),
        [(np.zeros((40, 80, 3), dtype=np.uint8), region)],
        action_delta=True,
    )
    time.sleep(0.05)

    incremental = daemon.status()["targets"][0]["incremental"]
    assert calls == []
    assert incremental["scheduled"] == 0
    assert incremental["skipped_inactive"] == 1
    daemon.shutdown()


def test_perception_daemon_discards_inflight_result_after_final_release(monkeypatch):
    import threading

    from winpilot.fast import perception_daemon as daemon_mod
    from winpilot.fast.types import ScreenDelta
    from winpilot.utils.screenshot import Region

    started = threading.Event()
    finish = threading.Event()

    def analyze(hwnd, crops, metadata):
        started.set()
        assert finish.wait(timeout=1.0)
        return _snapshot_for_change(hwnd, crops, metadata)

    monkeypatch.setattr(daemon_mod, "ScreenWatcher", _EmittingWatcher)
    monkeypatch.setattr(daemon_mod, "window_region", lambda hwnd: Region(0, 0, 800, 600))
    daemon = daemon_mod.PerceptionDaemon(idle_ttl_s=60, analyze_fn=analyze, debounce_ms=0)
    lease = daemon.acquire(119, run_id="cancel-inflight")
    region = Region(20, 30, 80, 40)
    lease.watcher.emit(
        ScreenDelta(True, 0.02, [region], 1),
        [(np.zeros((40, 80, 3), dtype=np.uint8), region)],
        action_delta=True,
    )
    assert started.wait(timeout=1.0)
    daemon.release(lease)
    finish.set()
    assert _wait_until(
        lambda: daemon.status()["targets"][0]["incremental"]["running"] is False
    )

    incremental = daemon.status()["targets"][0]["incremental"]
    assert incremental["history"] == 0
    assert incremental["superseded"] >= 1
    daemon.shutdown()


def test_perception_daemon_reacquire_clears_prior_history_and_anchor_cache(monkeypatch):
    from winpilot.fast import perception_daemon as daemon_mod
    from winpilot.fast.types import ScreenDelta, VisualAnchor
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    monkeypatch.setattr(daemon_mod, "ScreenWatcher", _EmittingWatcher)
    monkeypatch.setattr(daemon_mod, "window_region", lambda hwnd: Region(0, 0, 800, 600))
    daemon = daemon_mod.PerceptionDaemon(
        idle_ttl_s=60,
        analyze_fn=_snapshot_for_change,
        debounce_ms=0,
    )
    first = daemon.acquire(129, run_id="old-owner")
    region = Region(20, 30, 80, 40)
    element = UIElement(
        source="win32",
        role="button",
        text="Continue",
        cx=60,
        cy=50,
        left=20,
        top=30,
        width=80,
        height=40,
    )
    first.cache.remember(
        Snapshot(
            hwnd=129,
            title="Lease Target",
            region=Region(0, 0, 800, 600),
            elements=[element],
        ),
        VisualAnchor(label="Continue", role="button"),
        element,
    )
    first.watcher.emit(
        ScreenDelta(True, 0.02, [region], 1),
        [(np.zeros((40, 80, 3), dtype=np.uint8), region)],
        action_delta=True,
    )
    assert _wait_until(
        lambda: daemon.status()["targets"][0]["incremental"]["completed"] >= 1
    )
    assert first.cache.stats()["entries"] == 1
    assert daemon.latest_snapshot(129) is not None

    daemon.release(first)
    second = daemon.acquire(129, run_id="new-owner")

    assert second.watcher is first.watcher
    assert second.cache is first.cache
    assert second.cache.stats() == {
        "entries": 0,
        "hits": 0,
        "misses": 0,
        "stale": 0,
    }
    assert daemon.latest_snapshot(129) is None
    incremental = daemon.status()["targets"][0]["incremental"]
    assert incremental["history"] == 0

    daemon.release(second)
    daemon.shutdown()


class _EmittingWatcher:
    def __init__(self, region_provider=None, event_context_provider=None):
        self.region_provider = region_provider
        self.event_context_provider = event_context_provider
        self.listeners = []
        self.stopped = False

    def add_change_listener(self, listener):
        self.listeners.append(listener)

    def remove_change_listener(self, listener):
        self.listeners.remove(listener)

    def emit(self, delta, crops, **metadata):
        for listener in list(self.listeners):
            listener(delta, crops, metadata)

    def status(self):
        return {"running": not self.stopped, "samples": 0}

    def stop(self):
        self.stopped = True


def _wait_until(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _snapshot_for_change(hwnd, crops, metadata):
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement

    region = crops[0][1]
    generation = int(metadata["generation"])
    states = ["incremental_perception"]
    if metadata.get("transient"):
        states.append("transient_action_evidence")
    return Snapshot(
        hwnd=hwnd,
        title="Incremental Target",
        region=region,
        elements=[UIElement(
            source="ocr",
            role="text",
            text=f"GEN-{generation}",
            cx=region.left + region.width // 2,
            cy=region.top + region.height // 2,
            left=region.left,
            top=region.top,
            width=region.width,
            height=region.height,
            states=states,
        )],
        dim_stats={"ocr": {"count": 1, "ms": 1, "scope": "changed_roi_prefetch"}},
    )


def test_perception_daemon_coalesces_normal_churn_and_reuses_latest_roi(monkeypatch):
    from winpilot.fast import perception_daemon as daemon_mod
    from winpilot.fast.types import ScreenDelta
    from winpilot.utils.screenshot import Region

    calls = []

    def analyze(hwnd, crops, metadata):
        calls.append(metadata["generation"])
        return _snapshot_for_change(hwnd, crops, metadata)

    monkeypatch.setattr(daemon_mod, "ScreenWatcher", _EmittingWatcher)
    monkeypatch.setattr(daemon_mod, "window_region", lambda hwnd: Region(0, 0, 800, 600))
    daemon = daemon_mod.PerceptionDaemon(
        idle_ttl_s=60,
        analyze_fn=analyze,
        debounce_ms=60,
    )
    lease = daemon.acquire(101, run_id="run-incremental")
    try:
        for generation, left in enumerate((10, 100, 200), start=1):
            region = Region(left, 20, 40, 30)
            lease.watcher.emit(
                ScreenDelta(True, 0.02, [region], 1),
                [(np.zeros((30, 40, 3), dtype=np.uint8), region)],
                sequence=generation,
                action_delta=False,
            )

        assert _wait_until(
            lambda: daemon.status()["targets"][0]["incremental"]["completed"] >= 1
        )
        elements, meta = daemon.incremental_elements(
            101,
            regions=[Region(190, 10, 70, 60)],
        )

        assert calls == [3]
        assert [element.text for element in elements] == ["GEN-3"]
        assert meta["source"] == "perception_daemon"
        incremental = daemon.status()["targets"][0]["incremental"]
        assert incremental["scheduled"] == 3
        assert incremental["coalesced"] >= 2
    finally:
        daemon.release(lease)
        daemon.shutdown()


def test_perception_daemon_preserves_action_delta_before_normal_update(monkeypatch):
    from winpilot.fast import perception_daemon as daemon_mod
    from winpilot.fast.types import ScreenDelta
    from winpilot.utils.screenshot import Region

    call_order = []

    def analyze(hwnd, crops, metadata):
        call_order.append(bool(metadata.get("action_delta")))
        return _snapshot_for_change(hwnd, crops, metadata)

    monkeypatch.setattr(daemon_mod, "ScreenWatcher", _EmittingWatcher)
    monkeypatch.setattr(daemon_mod, "window_region", lambda hwnd: Region(0, 0, 800, 600))
    daemon = daemon_mod.PerceptionDaemon(
        idle_ttl_s=60,
        analyze_fn=analyze,
        debounce_ms=40,
    )
    lease = daemon.acquire(202, run_id="run-action")
    region = Region(100, 100, 120, 40)
    crop = (np.zeros((40, 120, 3), dtype=np.uint8), region)
    try:
        lease.watcher.emit(
            ScreenDelta(True, 0.03, [region], 1),
            [crop],
            sequence=1,
            action_delta=False,
        )
        lease.watcher.emit(
            ScreenDelta(True, 0.04, [region], 1),
            [crop],
            sequence=2,
            action_delta=True,
            transient=True,
        )

        assert _wait_until(
            lambda: daemon.status()["targets"][0]["incremental"]["completed"] >= 2
        )
        elements, meta = daemon.incremental_elements(202, regions=[region])

        assert call_order == [True, False]
        assert any("transient_action_evidence" in element.states for element in elements)
        assert meta["record_count"] == 2
        assert meta["transient"] is True
    finally:
        daemon.release(lease)
        daemon.shutdown()


def test_perception_daemon_supersedes_stale_normal_evidence_in_same_roi(monkeypatch):
    from winpilot.fast import perception_daemon as daemon_mod
    from winpilot.fast.types import ScreenDelta
    from winpilot.utils.screenshot import Region

    monkeypatch.setattr(daemon_mod, "ScreenWatcher", _EmittingWatcher)
    monkeypatch.setattr(daemon_mod, "window_region", lambda hwnd: Region(0, 0, 800, 600))
    daemon = daemon_mod.PerceptionDaemon(
        idle_ttl_s=60,
        analyze_fn=_snapshot_for_change,
        debounce_ms=0,
    )
    lease = daemon.acquire(250)
    region = Region(100, 100, 120, 40)
    crop = (np.zeros((40, 120, 3), dtype=np.uint8), region)
    try:
        lease.watcher.emit(
            ScreenDelta(True, 0.03, [region], 1),
            [crop],
            action_delta=False,
        )
        assert _wait_until(
            lambda: daemon.status()["targets"][0]["incremental"]["completed"] >= 1
        )
        lease.watcher.emit(
            ScreenDelta(True, 0.03, [region], 1),
            [crop],
            action_delta=False,
        )
        assert _wait_until(
            lambda: daemon.status()["targets"][0]["incremental"]["completed"] >= 2
        )

        elements, meta = daemon.incremental_elements(250, regions=[region])

        assert [element.text for element in elements] == ["GEN-2"]
        assert meta["record_count"] == 1
        assert daemon.status()["targets"][0]["incremental"]["superseded"] == 1
    finally:
        daemon.release(lease)
        daemon.shutdown()


def test_perception_daemon_skips_caret_noise_and_large_video_churn(monkeypatch):
    from winpilot.fast import perception_daemon as daemon_mod
    from winpilot.fast.types import ScreenDelta
    from winpilot.utils.screenshot import Region

    calls = []
    monkeypatch.setattr(daemon_mod, "ScreenWatcher", _EmittingWatcher)
    monkeypatch.setattr(daemon_mod, "window_region", lambda hwnd: Region(0, 0, 800, 600))
    daemon = daemon_mod.PerceptionDaemon(
        idle_ttl_s=60,
        analyze_fn=lambda *args: calls.append(args) or _snapshot_for_change(*args),
        debounce_ms=0,
    )
    lease = daemon.acquire(260)
    region = Region(100, 100, 120, 40)
    crop = (np.zeros((40, 120, 3), dtype=np.uint8), region)
    try:
        for ratio in (0.00001, 0.8):
            lease.watcher.emit(
                ScreenDelta(True, ratio, [region], 1),
                [crop],
                action_delta=False,
            )
        time.sleep(0.1)

        incremental = daemon.status()["targets"][0]["incremental"]
        assert calls == []
        assert incremental["skipped_noise"] == 2
    finally:
        daemon.release(lease)
        daemon.shutdown()


def test_perception_daemon_default_analyzer_uses_only_roi_ocr_and_win32(monkeypatch):
    from winpilot.perception import incremental
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    region = Region(100, 200, 180, 60)
    origins = []
    monkeypatch.setattr(
        incremental.CONFIG,
        "enabled_dimensions",
        lambda: ["win32", "ocr", "vlm"],
    )
    monkeypatch.setattr(
        incremental.CONFIG,
        "get",
        lambda section, key=None, default=None: default,
    )
    monkeypatch.setattr(incremental.win32, "_window_text", lambda hwnd: "Custom UI")
    monkeypatch.setattr(incremental.win32, "collect", lambda hwnd: [
        UIElement(
            source="win32", role="button", text="Inside",
            cx=120, cy=220, left=110, top=210, width=20, height=20,
        ),
        UIElement(
            source="win32", role="button", text="Outside",
            cx=600, cy=500, left=590, top=490, width=20, height=20,
        ),
    ])

    def recognize(_image, origin=(0, 0)):
        origins.append(origin)
        return [
            UIElement(
                source="ocr", role="text", text="READY",
                cx=150, cy=230, left=120, top=220, width=60, height=20,
            )
        ]

    monkeypatch.setattr(incremental.ocr, "recognize", recognize)

    snapshot = incremental.analyze_changed_regions(
        404,
        [(np.zeros((60, 180, 3), dtype=np.uint8), region)],
        {"transient": True},
    )

    assert origins == [(100, 200)]
    assert {element.text for element in snapshot.elements} == {"Inside", "READY"}
    ready = next(element for element in snapshot.elements if element.text == "READY")
    assert "predictive_ocr" in ready.states
    assert "transient_action_evidence" in ready.states
    assert "vlm" not in snapshot.dim_stats


def test_perception_daemon_prune_detaches_listener_and_stops_worker(monkeypatch):
    from winpilot.fast import perception_daemon as daemon_mod
    from winpilot.utils.screenshot import Region

    monkeypatch.setattr(daemon_mod, "ScreenWatcher", _EmittingWatcher)
    monkeypatch.setattr(daemon_mod, "window_region", lambda hwnd: Region(0, 0, 800, 600))
    daemon = daemon_mod.PerceptionDaemon(
        idle_ttl_s=60,
        analyze_fn=_snapshot_for_change,
        debounce_ms=0,
    )
    lease = daemon.acquire(303)
    watcher = lease.watcher

    daemon.release(lease)
    assert daemon.prune(force=True) == 1

    assert watcher.stopped is True
    assert watcher.listeners == []
