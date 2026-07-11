from __future__ import annotations


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
