import json
from types import SimpleNamespace

import numpy as np


def test_changed_regions_are_screen_space():
    from winpilot.fast.vision import changed_regions, roi_point_to_screen, screen_point_to_roi
    from winpilot.utils.screenshot import Region

    before = np.zeros((40, 50, 3), dtype=np.uint8)
    after = before.copy()
    after[10:20, 30:40] = 255
    root = Region(100, 200, 50, 40)

    out = changed_regions(before, after, root_region=root, min_area=4, merge_pad=0)

    assert out.changed
    assert out.regions[0].left >= 130
    assert out.regions[0].top >= 210
    assert roi_point_to_screen(root, 30, 10) == (130, 210)
    assert screen_point_to_roi(root, 130, 210) == (30, 10)


def test_element_cache_locates_visual_anchor_in_region():
    from winpilot.fast.element_cache import ElementCache
    from winpilot.fast.types import VisualAnchor
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    snapshot = Snapshot(
        hwnd=1,
        title="Demo",
        region=Region(0, 0, 1000, 600),
        elements=[
            UIElement(elem_id=1, role="button", text="Cancel", cx=900, cy=560),
            UIElement(elem_id=2, role="button", text="Export", cx=920, cy=50),
        ],
    )
    cache = ElementCache()

    element = cache.locate(
        snapshot,
        VisualAnchor(label="Export", expected_region="top-right", role="button"),
    )
    again = cache.locate(
        snapshot,
        VisualAnchor(label="Export", expected_region="top-right", role="button"),
    )

    assert element is not None
    assert element.elem_id == 2
    assert again is not None and again.elem_id == 2
    assert cache.stats()["hits"] == 1


def test_fast_controller_click_element_dispatches_click():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []

    class Session:
        snapshot = Snapshot(
            hwnd=1,
            title="Demo",
            region=Region(0, 0, 1000, 600),
            elements=[UIElement(elem_id=5, role="button", text="Export", cx=920, cy=50)],
        )

        def dispatch(self, name, args):
            calls.append((name, args))
            return ToolResult('{"ok": true, "verified": true}')

    out = FastGuiController(Session()).execute([
        {"action": "click_element", "label": "Export", "expected_region": "top-right", "role": "button"}
    ])

    assert out["ok"]
    assert calls == [("click", {"element_id": 5, "button": "left", "double": False})]
    assert out["metrics"]["controller_round_trips_saved"] == 0


def test_fast_controller_accepts_single_key_shorthand():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController

    calls = []

    class Session:
        snapshot = None

        def dispatch(self, name, args):
            calls.append((name, args))
            return ToolResult('{"ok": true}')

    out = FastGuiController(Session()).execute([
        {"click": {"element_id": 1}},
        {"hotkey": {"keys": ["enter"]}},
    ])

    assert out["ok"]
    assert calls == [
        ("click", {"element_id": 1}),
        ("hotkey", {"keys": ["enter"]}),
    ]
    assert out["metrics"]["controller_round_trips_saved"] == 1


def test_fast_controller_wait_shorthand_is_pause_not_condition_wait():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController

    calls = []

    class Session:
        snapshot = None

        def dispatch(self, name, args):
            calls.append((name, args))
            return ToolResult('{"ok": true}')

    out = FastGuiController(Session()).execute([
        {"wait": 5},
    ])

    assert out["ok"]
    assert out["results"][0]["action"] == "wait"
    assert "waited_ms" in out["results"][0]["preview"]
    assert calls == []


def test_fast_controller_invalid_wait_fails_batch():
    from winpilot.fast.controller import FastGuiController

    session = SimpleNamespace(snapshot=None, hwnd=None)
    out = FastGuiController(session).execute([
        {"wait": {}},
        {"click": {"x": 1, "y": 2}},
    ])

    assert not out["ok"]
    assert out["metrics"]["failed_at"] == 1
    assert "requires condition or duration" in out["results"][0]["preview"]


def test_fast_controller_adds_short_timeout_to_batch_expect():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController

    calls = []

    class Session:
        snapshot = None

        def dispatch(self, name, args):
            calls.append((name, args))
            return ToolResult('{"ok": true, "verified": true}')

    out = FastGuiController(Session()).execute([
        {"click": {"x": 1, "y": 2, "expect": {"text_appears": "Ready"}}},
    ], sync=False)

    assert out["ok"]
    assert calls[0][1]["expect"]["timeout_s"] == 2.0
    assert out["results"][0]["data"]["verified"] is True


def test_fast_controller_plain_action_error_fails_batch():
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    session = SimpleNamespace(
        snapshot=Snapshot(
            hwnd=1,
            title="Demo",
            region=Region(0, 0, 400, 300),
            elements=[UIElement(elem_id=1, role="button", text="Cancel", cx=30, cy=30)],
        ),
        hwnd=1,
    )
    out = FastGuiController(session).execute([
        {"click_element": {"label": "Export"}},
    ])

    assert not out["ok"]
    assert out["metrics"]["failed_at"] == 1
    assert "could not locate anchor" in out["results"][0]["preview"]


def test_fast_controller_batches_visual_actions_without_extra_observe():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController

    calls = []

    class Session:
        snapshot = None

        def dispatch(self, name, args):
            calls.append((name, args))
            if name == "draw_strokes":
                return ToolResult(
                    '{"ok": true, "visual_change_detected": true, '
                    '"verified": true, "verify_method": "cv-diff"}'
                )
            if name == "zoom":
                return ToolResult('{"ok": true, "nonvisual_summary": {"blankish": false}}')
            return ToolResult('{"ok": true}')

    out = FastGuiController(Session()).execute([
        {"click": {"x": 100, "y": 100}},
        {"draw_strokes": {"strokes": [[[100, 100], [150, 150]]], "duration": 0.1}},
        {"zoom": {"x": 90, "y": 90, "width": 80, "height": 80}},
    ], sync=False)

    assert out["ok"]
    assert out["needs_observe"] is False
    assert out["metrics"]["controller_round_trips_saved"] == 2
    assert out["metrics"]["observe_count"] == 1
    assert calls == [
        ("click", {"x": 100, "y": 100}),
        ("draw_strokes", {"strokes": [[[100, 100], [150, 150]]], "duration": 0.1}),
        ("zoom", {"x": 90, "y": 90, "width": 80, "height": 80}),
    ]


def test_fast_controller_unverified_mutation_after_verified_action_needs_observe():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController

    class Session:
        snapshot = None

        def dispatch(self, name, args):
            if name == "draw_strokes":
                return ToolResult('{"ok": true, "verified": true}')
            return ToolResult('{"ok": true, "verified": false}')

    out = FastGuiController(Session()).execute([
        {"draw_strokes": {"strokes": [[[100, 100], [150, 150]]]}},
        {"click": {"x": 1, "y": 2}},
    ], sync=False)

    assert out["ok"]
    assert out["needs_observe"] is True
    assert "verified" not in out


def test_fast_controller_marks_needs_observe_without_expect():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController

    class Session:
        snapshot = None

        def dispatch(self, name, args):
            return ToolResult('{"ok": true, "verified": false}')

    out = FastGuiController(Session()).execute([
        {"click": {"x": 1, "y": 2}},
    ])

    assert out["ok"]
    assert out["needs_observe"] is True
    assert "verified" not in out


def test_fast_controller_aggregates_expected_verification():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController

    class Session:
        snapshot = None

        def dispatch(self, name, args):
            return ToolResult('{"ok": true, "verified": true}')

    out = FastGuiController(Session()).execute([
        {"click": {"x": 1, "y": 2, "expect": {"text": "done"}}},
    ])

    assert out["ok"]
    assert out["verified"] is True
    assert out["needs_observe"] is False


def test_fast_controller_stops_on_failed_expect():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController

    calls = []

    class Session:
        snapshot = None

        def dispatch(self, name, args):
            calls.append((name, args))
            if len(calls) == 1:
                return ToolResult('{"ok": true, "verified": false}')
            return ToolResult('{"ok": true}')

    out = FastGuiController(Session()).execute([
        {"action": "hotkey", "keys": ["enter"], "expect": {"window_gone": "Save As"}},
        {"action": "type_text", "text": "should-not-run"},
    ])

    assert not out["ok"]
    assert out["metrics"]["failed_at"] == 1
    assert len(calls) == 1


def test_screen_watcher_updates_bulletin_with_changed_roi():
    from winpilot.fast.watcher import ScreenWatcher
    from winpilot.utils.screenshot import Region

    frames = [
        np.zeros((20, 20, 3), dtype=np.uint8),
        np.zeros((20, 20, 3), dtype=np.uint8),
    ]
    frames[1][5:13, 6:14] = 255
    calls = []

    def fake_capture(region):
        calls.append(region)
        return frames[min(len(calls) - 1, 1)]

    watcher = ScreenWatcher(
        region_provider=lambda: Region(100, 200, 20, 20),
        capture_fn=fake_capture,
        fps=10,
    )

    first = watcher.sample_once()
    second = watcher.sample_once()
    status = watcher.status()

    assert not first.changed
    assert second.changed
    assert status["last_changed"]
    changed = status["changed_regions"][0]
    assert changed["left"] <= 106
    assert changed["top"] <= 205
    assert changed["left"] + changed["width"] >= 114
    assert changed["top"] + changed["height"] >= 213


def test_fast_controller_wait_change_uses_watcher():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.types import ScreenDelta
    from winpilot.utils.screenshot import Region

    class Watcher:
        def wait_for_change(self, timeout_s=3.0, min_ratio=0.0005):
            return ScreenDelta(True, 0.01, [Region(1, 2, 3, 4)], 5)

        def status(self):
            return {"samples": 2}

    session = SimpleNamespace(snapshot=None, hwnd=None)
    out = FastGuiController(session, watcher=Watcher()).execute([
        {"wait_change": {"timeout_s": 1}},
    ])

    assert out["ok"]
    assert out["metrics"]["screen_wait_count"] == 1
    assert "changed" in out["results"][0]["preview"]


def test_fast_controller_can_auto_sync_after_batch():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController

    class Watcher:
        def wait_for_stable(self, stable_ms=500, timeout_s=5.0, max_ratio=0.0008):
            return {"stable": True, "delta": {"changed": False}}

        def status(self):
            return {"samples": 2}

    class Session:
        snapshot = None

        def dispatch(self, name, args):
            return ToolResult('{"ok": true}')

    out = FastGuiController(Session(), watcher=Watcher()).execute([
        {"click": {"x": 1, "y": 2}},
    ], sync={"mode": "stable"})

    assert out["ok"]
    assert out["metrics"]["screen_wait_count"] == 1
    assert out["results"][-1]["action"] == "sync_stable"
    assert out["sync"]["ok"] is True


def test_run_action_batch_tool_rejects_empty_actions():
    from winpilot.agent.fast_tools import FastToolMixin

    class Session(FastToolMixin):
        pass

    result = Session()._t_run_action_batch({"actions": []})

    assert "requires" in result.text


def test_run_action_batch_tool_defaults_to_stable_sync_for_mutation():
    from winpilot.agent.fast_tools import FastToolMixin

    captured = {}

    class Controller:
        def execute(self, actions, stop_on_error=True, sync=None):
            captured["sync"] = sync
            return {"ok": True, "results": [], "metrics": {}}

    class Session(FastToolMixin):
        def __init__(self):
            self._fast_gui_controller = Controller()

        def _fmt(self, obj):
            return json.dumps(obj)

    Session()._t_run_action_batch({"actions": [{"click": {"x": 1, "y": 2}}]})

    assert captured["sync"]["mode"] == "stable"


def test_run_action_batch_tool_defaults_to_stable_sync_for_draw_strokes():
    from winpilot.agent.fast_tools import FastToolMixin

    captured = {}

    class Controller:
        def execute(self, actions, stop_on_error=True, sync=None):
            captured["sync"] = sync
            return {"ok": True, "results": [], "metrics": {}}

    class Session(FastToolMixin):
        def __init__(self):
            self._fast_gui_controller = Controller()

        def _fmt(self, obj):
            return json.dumps(obj)

    Session()._t_run_action_batch({
        "actions": [{"draw_strokes": {"strokes": [[[1, 2], [3, 4]]]}}],
    })

    assert captured["sync"]["mode"] == "stable"


def test_run_action_batch_tool_can_disable_default_sync():
    from winpilot.agent.fast_tools import FastToolMixin

    captured = {}

    class Controller:
        def execute(self, actions, stop_on_error=True, sync=None):
            captured["sync"] = sync
            return {"ok": True, "results": [], "metrics": {}}

    class Session(FastToolMixin):
        def __init__(self):
            self._fast_gui_controller = Controller()

        def _fmt(self, obj):
            return json.dumps(obj)

    Session()._t_run_action_batch({
        "actions": [{"click": {"x": 1, "y": 2}}],
        "sync": False,
    })

    assert captured["sync"] is None
