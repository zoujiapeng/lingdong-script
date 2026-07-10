from types import SimpleNamespace


def _status(sequence: int = 3) -> dict:
    return {
        "sequence": sequence,
        "running": True,
        "last_changed": True,
        "last_ratio": 0.02,
        "changed_regions": [
            {"left": 10, "top": 20, "width": 30, "height": 40},
        ],
        "samples": 5,
        "capture_latency_ms": 4,
        "diff_latency_ms": 1,
    }


def test_world_model_screen_bulletin_advisory_is_edge_triggered():
    from winpilot.perception.world import WorldModel

    world = WorldModel()
    world.record_screen_bulletin(_status())

    state = world.state()
    assert state["screen_watch"]["sequence"] == 3
    assert state["screen_watch"]["last_changed"] is True

    first = world.screen_advisory()
    second = world.screen_advisory()

    assert "ratio=0.0200" in first
    assert "roi=(10,20,30x40)" in first
    assert second == ""


def test_tool_session_records_fast_screen_bulletin_after_dispatch():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession

    class Watcher:
        def status(self):
            return _status(sequence=7)

    session = ToolSession(task="demo")
    session._fast_gui_controller = SimpleNamespace(watcher=Watcher())
    session._t_ping = lambda args: ToolResult("ok")

    result = session.dispatch("ping", {})

    assert result.text == "ok"
    assert session.world.state()["screen_watch"]["sequence"] == 7


def test_tool_session_attaches_screen_delta_to_visible_action():
    import json

    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession
    from winpilot.fast.types import ScreenDelta
    from winpilot.utils.screenshot import Region

    class Watcher:
        def __init__(self):
            self.samples = 0

        def sample_once(self):
            self.samples += 1
            if self.samples == 1:
                return ScreenDelta(False, 0.0, [], 1)
            return ScreenDelta(True, 0.0123, [Region(10, 20, 30, 40)], 2)

        def status(self):
            return _status(sequence=self.samples)

    watcher = Watcher()
    session = ToolSession(task="demo")
    session._fast_gui_controller = SimpleNamespace(watcher=watcher)
    session._t_click = lambda args: ToolResult('{"ok": true, "verified": false}')

    result = session.dispatch("click", {"x": 1, "y": 2})
    data = json.loads(result.text)

    assert watcher.samples == 2
    assert data["screen_delta"]["changed"] is True
    assert data["screen_delta"]["ratio"] == 0.0123
    assert data["screen_delta"]["regions"][0]["left"] == 10
    assert "action_target" in data
    assert session.world.state()["screen_watch"]["sequence"] == 2


def test_tool_session_can_skip_screen_delta_for_visible_action():
    import json

    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession

    class Watcher:
        def sample_once(self):
            raise AssertionError("screen delta should be skipped")

        def status(self):
            return {}

    session = ToolSession(task="demo")
    session._fast_gui_controller = SimpleNamespace(watcher=Watcher())
    session._t_click = lambda args: ToolResult('{"ok": true}')

    result = session.dispatch("click", {"x": 1, "y": 2, "skip_screen_delta": True})

    assert "screen_delta" not in json.loads(result.text)


def test_tool_session_action_delta_uses_independent_before_after_frames():
    import json

    import numpy as np

    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession
    from winpilot.utils.screenshot import Region

    frames = [
        np.zeros((40, 50, 3), dtype=np.uint8),
        np.zeros((40, 50, 3), dtype=np.uint8),
    ]
    frames[1][10:22, 20:36] = 255
    captures = []

    def fake_capture(region):
        captures.append(region)
        return frames[min(len(captures) - 1, 1)]

    class Watcher:
        pixel_thresh = 12

        def __init__(self):
            self.action_regions = []

        def status(self):
            return {
                "sequence": len(self.action_regions),
                "running": True,
                "action_changed_regions": [
                    {"left": r.left, "top": r.top, "width": r.width, "height": r.height}
                    for r in self.action_regions
                ],
            }

        def record_action_delta(self, delta, region, capture_ms=0, diff_ms=None):
            self.action_regions = list(delta.regions)

    class Controller:
        def __init__(self):
            self.watcher = Watcher()
            self.capture_fn = fake_capture

        def _target_region(self):
            return Region(100, 200, 50, 40)

    session = ToolSession(task="demo")
    session._fast_gui_controller = Controller()
    session._t_click = lambda args: ToolResult('{"ok": true, "verified": false}')

    result = session.dispatch("click", {"x": 1, "y": 2})
    data = json.loads(result.text)

    assert len(captures) == 2
    assert data["screen_delta"]["changed"] is True
    region = data["screen_delta"]["regions"][0]
    assert region["left"] <= 120
    assert region["left"] + region["width"] >= 136
    action_region = data["screen_watch"]["action_changed_regions"][0]
    assert action_region["left"] <= 120
    assert action_region["left"] + action_region["width"] >= 136


def test_tool_session_preserves_pressed_state_when_after_frame_reverts():
    import json

    import numpy as np

    from winpilot.action import executor
    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession
    from winpilot.utils.screenshot import Region

    before = np.zeros((60, 80, 3), dtype=np.uint8)
    pressed = before.copy()
    pressed[20:38, 28:58] = 255
    after = before.copy()
    frames = [before, pressed, after]
    captures = 0

    def fake_capture(_region):
        nonlocal captures
        frame = frames[min(captures, len(frames) - 1)]
        captures += 1
        return frame

    class Watcher:
        pixel_thresh = 12

        def __init__(self):
            self.delta = None

        def status(self):
            return {"sequence": 1, "running": True}

        def record_action_delta(self, delta, region, capture_ms=0, diff_ms=None):
            self.delta = delta

    class Controller:
        def __init__(self):
            self.watcher = Watcher()
            self.capture_fn = fake_capture

        def _target_region(self):
            return Region(100, 200, 80, 60)

    session = ToolSession(task="observe the pressed state")
    session.run_id = "transient-run"
    session._fast_gui_controller = Controller()

    def click_with_transient_probe(_args):
        assert executor._sample_pressed_state() is True
        return ToolResult('{"ok": true, "verified": false}')

    session._t_click = click_with_transient_probe
    result = session.dispatch("click", {"x": 1, "y": 2})
    data = json.loads(result.text)

    assert captures == 3
    assert data["screen_delta"]["changed"] is True
    assert data["screen_delta"]["delta_source"] == "press"
    assert data["screen_delta"]["transient_observed"] is True
    assert data["screen_delta"]["press_sample_count"] == 1
    assert session._fast_gui_controller.watcher.delta.changed is True


def test_tool_session_does_not_claim_transient_when_press_frame_is_unchanged():
    import json

    import numpy as np

    from winpilot.action import executor
    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession
    from winpilot.utils.screenshot import Region

    frame = np.zeros((40, 60, 3), dtype=np.uint8)

    class Watcher:
        pixel_thresh = 12

        def status(self):
            return {"sequence": 1, "running": True}

        def record_action_delta(self, delta, region, capture_ms=0, diff_ms=None):
            self.delta = delta

    class Controller:
        def __init__(self):
            self.watcher = Watcher()
            self.capture_fn = lambda _region: frame.copy()

        def _target_region(self):
            return Region(0, 0, 60, 40)

    session = ToolSession(task="no visual press change")
    session._fast_gui_controller = Controller()

    def click_with_probe(_args):
        assert executor._sample_pressed_state() is True
        return ToolResult('{"ok": true}')

    session._t_click = click_with_probe
    data = json.loads(session.dispatch("click", {"x": 1, "y": 2}).text)

    assert data["screen_delta"]["changed"] is False
    assert data["screen_delta"]["delta_source"] == "press"
    assert data["screen_delta"]["transient_observed"] is False


def test_screen_watcher_emits_change_event_with_run_context():
    import queue

    from winpilot.fast.types import ScreenDelta
    from winpilot.fast.watcher import ScreenWatcher
    from winpilot.utils.log import BUS
    from winpilot.utils.screenshot import Region

    events = BUS.subscribe()
    try:
        watcher = ScreenWatcher(event_context_provider=lambda: {"run_id": "watch-run"})
        watcher._update_bulletin(
            ScreenDelta(True, 0.04, [Region(10, 20, 30, 40)], 2),
            Region(0, 0, 100, 100),
            capture_ms=5,
            diff_ms=2,
        )
        event = events.get(timeout=1)
    finally:
        BUS.unsubscribe(events)

    assert event["type"] == "screen_watch"
    assert event["run_id"] == "watch-run"
    assert event["regions"][0]["left"] == 10


def test_predictive_ocr_prefetches_action_evidence(monkeypatch):
    import time

    import numpy as np

    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    session = ToolSession(task="read transient result")
    session.run_id = "prefetch-run"
    crop_region = Region(100, 120, 180, 60)
    watcher = type("Watcher", (), {
        "action_evidence": lambda self, max_age_ms=5000: ([(np.zeros((60, 180, 3), dtype=np.uint8), crop_region)], True),
        "status": lambda self: {"last_action_change_ms": 123},
    })()
    monkeypatch.setattr(tools.ocr, "recognize", lambda image, origin=(0, 0): [
        UIElement(source="ocr", role="text", text="PRESS PULSE ACTIVE 1", cx=180, cy=140, left=120, top=130, width=120, height=20)
    ])

    session._schedule_predictive_ocr(watcher)
    deadline = time.time() + 2
    while session._predictive_ocr_cache is None and time.time() < deadline:
        time.sleep(0.01)

    assert session._predictive_ocr_cache is not None
    element = session._predictive_ocr_cache["elements"][0]
    assert "predictive_ocr" in element.states
    assert "transient_action_evidence" in element.states


def test_tool_session_auto_starts_and_stops_screen_watch_for_target():
    from winpilot.agent.tools import ToolSession

    class Watcher:
        def __init__(self):
            self.running = False
            self.start_calls = 0
            self.stop_calls = 0

        def start(self):
            self.running = True
            self.start_calls += 1

        def stop(self):
            self.running = False
            self.stop_calls += 1

        def status(self):
            return {
                "sequence": self.start_calls + self.stop_calls,
                "running": self.running,
                "last_changed": False,
                "last_ratio": 0.0,
                "changed_regions": [],
                "samples": 0,
                "capture_latency_ms": 0,
                "diff_latency_ms": 0,
            }

    watcher = Watcher()
    session = ToolSession(task="demo")
    session.run_id = "run-1"
    session._window_alive = lambda hwnd: bool(hwnd)
    session._fast_gui_controller = SimpleNamespace(watcher=watcher)

    session._remember_target(123, "Target App")

    assert watcher.start_calls == 1
    assert session.world.state()["screen_watch"]["running"] is True

    session.close()

    assert watcher.stop_calls == 1
    assert watcher.running is False


def test_tool_session_auto_watch_requires_real_run_id():
    from winpilot.agent.tools import ToolSession

    class Watcher:
        def start(self):
            raise AssertionError("ad-hoc ToolSession tests should not autostart watcher")

        def status(self):
            return {"running": False}

    session = ToolSession(task="demo")
    session._window_alive = lambda hwnd: bool(hwnd)
    session._fast_gui_controller = SimpleNamespace(watcher=Watcher())

    session._remember_target(123, "Target App")


def test_tool_session_respects_screen_watch_autostart_toggle(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession

    class Watcher:
        def start(self):
            raise AssertionError("autostart should be disabled")

        def status(self):
            return {"running": False}

    def fake_get(section, key=None, default=None):
        if section == "fusion" and key == "screen_watch_autostart":
            return False
        return default

    monkeypatch.setattr(tools.CONFIG, "get", fake_get)

    session = ToolSession(task="demo")
    session._window_alive = lambda hwnd: bool(hwnd)
    session._fast_gui_controller = SimpleNamespace(watcher=Watcher())

    session._remember_target(123, "Target App")


def test_changed_only_observe_prefers_fresh_action_roi(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession
    from winpilot.utils.screenshot import Region

    status = {
        "action_changed_regions": [
            {"left": 100, "top": 120, "width": 30, "height": 40},
        ],
        "last_action_change_age_ms": 100,
        "recent_changed_regions": [
            {"left": 800, "top": 820, "width": 30, "height": 40},
        ],
        "last_change_age_ms": 100,
    }

    session = ToolSession(task="demo")
    session._fast_screen_status = lambda: status
    monkeypatch.setattr(tools, "window_region", lambda hwnd: Region(0, 0, 1000, 1000))

    regions = session._recent_changed_regions(1)

    assert regions
    assert regions[0].left <= 100
    assert regions[0].right >= 130
    assert regions[0].left < 800
