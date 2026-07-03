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


def test_element_cache_revalidates_cached_anchor_without_reusing_old_element_id():
    from winpilot.fast.element_cache import ElementCache
    from winpilot.fast.types import VisualAnchor
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    first = Snapshot(
        hwnd=1,
        title="Demo",
        region=Region(0, 0, 1000, 600),
        elements=[
            UIElement(elem_id=1, role="button", text="Cancel", cx=80, cy=50),
            UIElement(elem_id=2, role="button", text="Export", cx=920, cy=50),
        ],
    )
    second = Snapshot(
        hwnd=1,
        title="Demo",
        region=Region(0, 0, 1000, 600),
        elements=[
            UIElement(elem_id=2, role="button", text="Cancel", cx=920, cy=50),
            UIElement(elem_id=7, role="button", text="Export", cx=920, cy=50),
        ],
    )
    cache = ElementCache()
    anchor = VisualAnchor(label="Export", expected_region="top-right", role="button")

    assert cache.locate(first, anchor).elem_id == 2
    located = cache.locate_with_trace(second, anchor)

    assert located.element is not None
    assert located.element.elem_id == 7
    assert located.cache_hit is True
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
    data = out["results"][0]["data"]
    assert data["via"] == "click_element"
    assert data["resolved_element"]["elem_id"] == 5
    assert data["anchor_locator"]["found"] is True
    assert out["metrics"]["controller_round_trips_saved"] == 0
    assert out["controller_state"]["phase"] == "verified"


def test_fast_controller_click_element_auto_observes_then_retries_anchor():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []
    initial = Snapshot(
        hwnd=1,
        title="Demo",
        region=Region(0, 0, 1000, 600),
        elements=[UIElement(elem_id=1, role="button", text="Cancel", cx=60, cy=50)],
    )
    refreshed = Snapshot(
        hwnd=1,
        title="Demo",
        region=Region(0, 0, 1000, 600),
        elements=[
            UIElement(elem_id=1, role="button", text="Cancel", cx=60, cy=50),
            UIElement(elem_id=8, role="button", text="Export", cx=920, cy=50),
        ],
    )

    class Session:
        def __init__(self):
            self.snapshot = initial

        def dispatch(self, name, args):
            calls.append((name, args))
            if name == "observe":
                self.snapshot = refreshed
                return ToolResult('{"ok": true}')
            return ToolResult('{"ok": true, "verified": true}')

    out = FastGuiController(Session()).execute([
        {"action": "click_element", "label": "Export", "expected_region": "top-right", "role": "button"}
    ])

    assert out["ok"]
    assert calls == [
        ("observe", {"query": "Export", "max_elements": 160}),
        ("click", {"element_id": 8, "button": "left", "double": False}),
    ]
    data = out["results"][0]["data"]
    assert data["via"] == "click_element"
    assert data["resolved_element"]["elem_id"] == 8
    assert data["controller_auto_observe"]["used"] is True
    assert data["controller_auto_observe"]["found_after_retry"] is True
    assert out["metrics"]["auto_observe_count"] == 1
    assert out["metrics"]["observe_count"] == 1
    assert out["metrics"]["controller_round_trips_saved"] == 1


def test_fast_controller_type_into_element_clicks_clears_and_types():
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
            elements=[UIElement(elem_id=4, role="edit", text="Search", cx=300, cy=80)],
        )

        def dispatch(self, name, args):
            calls.append((name, args))
            if name == "type_text":
                return ToolResult('{"ok": true, "verified": true}')
            return ToolResult('{"ok": true, "verified": false}')

    expect = {"text_appears": "hello"}
    out = FastGuiController(Session()).execute([
        {
            "type_into_element": {
                "label": "Search",
                "role": "edit",
                "text": "hello",
                "clear": True,
                "expect": expect,
            }
        }
    ])

    assert out["ok"]
    expected_expect = {"text_appears": "hello", "timeout_s": 2.0}
    assert calls == [
        ("click", {"element_id": 4, "button": "left", "double": False}),
        ("hotkey", {"keys": ["ctrl", "a"]}),
        ("type_text", {"text": "hello", "expect": expected_expect}),
    ]
    data = out["results"][0]["data"]
    assert data["via"] == "type_into_element"
    assert data["controller_internal_action_count"] == 3
    assert data["resolved_element"]["elem_id"] == 4
    assert data["subresults"][0]["action"] == "click_element"
    assert data["subresults"][2]["action"] == "type_text"
    assert out["metrics"]["internal_action_count"] == 3
    assert out["metrics"]["compound_round_trips_saved"] == 2
    assert out["metrics"]["controller_round_trips_saved"] == 2
    assert out["controller_state"]["phase"] == "verified"


def test_fast_controller_upgrades_semantic_click_to_anchor_click():
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
            elements=[UIElement(elem_id=9, role="button", text="Export", cx=920, cy=50)],
        )

        def dispatch(self, name, args):
            calls.append((name, args))
            return ToolResult('{"ok": true, "verified": true}')

    out = FastGuiController(Session()).execute([
        {"click": {"label": "Export", "expected_region": "top-right", "role": "button"}}
    ])

    assert out["ok"]
    assert calls == [("click", {"element_id": 9, "button": "left", "double": False})]
    assert out["results"][0]["action"] == "click_element"
    assert out["results"][0]["upgraded_from"] == "click"
    assert out["results"][0]["data"]["via"] == "click_element"
    assert out["results"][0]["data"]["resolved_element"]["elem_id"] == 9


def test_fast_controller_upgrades_semantic_type_text_to_type_into_element():
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
            elements=[UIElement(elem_id=11, role="edit", text="Search", cx=300, cy=80)],
        )

        def dispatch(self, name, args):
            calls.append((name, args))
            return ToolResult('{"ok": true, "verified": true}')

    out = FastGuiController(Session()).execute([
        {"action": "type_text", "label": "Search", "role": "edit", "text": "hello", "clear": True}
    ])

    assert out["ok"]
    assert calls == [
        ("click", {"element_id": 11, "button": "left", "double": False}),
        ("hotkey", {"keys": ["ctrl", "a"]}),
        ("type_text", {"text": "hello"}),
    ]
    assert out["results"][0]["action"] == "type_into_element"
    assert out["results"][0]["upgraded_from"] == "type_text"
    assert out["results"][0]["data"]["via"] == "type_into_element"
    assert out["metrics"]["compound_round_trips_saved"] == 2


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
        {"hotkey": ["6"]},
    ])

    assert out["ok"]
    assert calls == [
        ("click", {"element_id": 1}),
        ("hotkey", {"keys": ["enter"]}),
        ("hotkey", {"keys": ["6"]}),
    ]
    assert out["metrics"]["controller_round_trips_saved"] == 2


def test_fast_controller_hotkey_list_shorthand_maps_to_keys():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController

    calls = []

    class Session:
        snapshot = None

        def dispatch(self, name, args):
            calls.append((name, args))
            return ToolResult('{"ok": true}')

    out = FastGuiController(Session()).execute([
        {"hotkey": ["6"]},
        {"hotkey": ["+"]},
    ], sync=False)

    assert out["ok"]
    assert calls == [
        ("hotkey", {"keys": ["6"]}),
        ("hotkey", {"keys": ["+"]}),
    ]


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
    assert out["results"][0]["data"]["anchor_locator"]["found"] is False


def test_fast_controller_records_subgoal_and_needs_observe_phase():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController

    class Session:
        snapshot = None

        def dispatch(self, name, args):
            return ToolResult('{"ok": true, "verified": false}')

    out = FastGuiController(Session()).execute(
        [{"click": {"x": 1, "y": 2}}],
        sync=False,
        subgoal="open menu",
    )

    assert out["ok"]
    assert out["subgoal"] == "open menu"
    assert out["needs_observe"] is True
    assert out["controller_state"]["phase"] == "needs_observe"


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


def test_fast_controller_auto_verifies_visual_mutation_region():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController

    calls = []
    captures = []

    def fake_capture(region):
        captures.append(region)
        image = np.full((80, 100, 3), 255, dtype=np.uint8)
        image[20:60, 45:55] = 0
        image[50:60, 25:75] = 0
        return image

    class Session:
        snapshot = None
        run_id = "run-1"

        def dispatch(self, name, args):
            calls.append((name, args))
            if name == "draw_strokes":
                return ToolResult(
                    '{"ok": true, "verified": true, '
                    '"visual_change_detected": true, "visual_change_ratio": 0.04}'
                )
            return ToolResult('{"ok": true}')

    out = FastGuiController(Session(), capture_fn=fake_capture).execute([
        {"click": {"x": 100, "y": 100}},
        {"draw_strokes": {"strokes": [[[100, 100], [150, 150]]], "duration": 0.1}},
    ], sync=False)

    assert out["ok"]
    assert [item["action"] for item in out["results"]] == [
        "click",
        "draw_strokes",
        "auto_visual_verify",
    ]
    auto = out["results"][-1]["data"]
    assert auto["source_action"] == "draw_strokes"
    assert auto["visual_change_detected"] is True
    assert auto["nonvisual_summary"]["blankish"] is False
    assert captures
    assert out["needs_observe"] is False
    assert "call done instead of adding another zoom/observe" in out["fast_path_advice"]
    assert out["metrics"]["action_count"] == 2
    assert out["metrics"]["observe_count"] == 1


def test_fast_controller_publishes_batch_child_events():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.utils.log import BUS

    events = []

    def fake_capture(_region):
        return np.full((20, 20, 3), 255, dtype=np.uint8)

    def sink(event):
        if str(event.get("type", "")).startswith("agent_batch_action"):
            events.append(event)

    class Session:
        snapshot = None
        run_id = "run-1"

        def dispatch(self, name, args):
            if name == "draw_strokes":
                return ToolResult('{"ok": true, "verified": true, "visual_change_detected": true}')
            return ToolResult('{"ok": true}')

    BUS.add_sink(sink)
    try:
        out = FastGuiController(Session(), capture_fn=fake_capture).execute([
            {"click": {"x": 100, "y": 100}},
            {"draw_strokes": {"strokes": [[[100, 100], [150, 150]]]}}
        ], sync=False)
    finally:
        BUS.remove_sink(sink)

    assert out["ok"]
    assert [(e["type"], e["tool"], e["batch_index"]) for e in events] == [
        ("agent_batch_action_start", "click", 1),
        ("agent_batch_action", "click", 1),
        ("agent_batch_action_start", "draw_strokes", 2),
        ("agent_batch_action", "draw_strokes", 2),
        ("agent_batch_action_start", "auto_visual_verify", 3),
        ("agent_batch_action", "auto_visual_verify", 3),
    ]
    assert events[0]["run_id"] == "run-1"
    assert events[3]["result_meta"]["visual_change_detected"] is True


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


def test_screen_watcher_keeps_recent_changed_roi_after_stable_sample():
    import numpy as np

    from winpilot.fast.watcher import ScreenWatcher
    from winpilot.utils.screenshot import Region

    frames = [
        np.zeros((20, 20, 3), dtype=np.uint8),
        np.zeros((20, 20, 3), dtype=np.uint8),
        np.zeros((20, 20, 3), dtype=np.uint8),
    ]
    frames[1][5:13, 6:14] = 255
    frames[2][5:13, 6:14] = 255
    calls = []

    def fake_capture(region):
        calls.append(region)
        return frames[min(len(calls) - 1, 2)]

    watcher = ScreenWatcher(
        region_provider=lambda: Region(100, 200, 20, 20),
        capture_fn=fake_capture,
        fps=10,
    )

    watcher.sample_once()
    changed = watcher.sample_once()
    stable = watcher.sample_once()
    status = watcher.status()

    assert changed.changed
    assert not stable.changed
    assert status["last_changed"] is False
    assert status["recent_changed_regions"]
    recent = status["recent_changed_regions"][0]
    assert recent["left"] <= 106
    assert recent["top"] <= 205


def test_screen_watcher_records_action_delta_separately_from_recent_churn():
    from winpilot.fast.types import ScreenDelta
    from winpilot.fast.watcher import ScreenWatcher
    from winpilot.utils.screenshot import Region

    watcher = ScreenWatcher(region_provider=lambda: Region(0, 0, 20, 20))
    watcher.record_action_delta(
        ScreenDelta(True, 0.02, [Region(100, 200, 30, 40)], 3),
        Region(0, 0, 20, 20),
        capture_ms=7,
    )
    watcher._update_bulletin(
        ScreenDelta(True, 0.01, [Region(500, 600, 20, 20)], 2),
        Region(0, 0, 20, 20),
        capture_ms=5,
        diff_ms=2,
    )
    status = watcher.status()

    assert status["recent_changed_regions"][0]["left"] == 500
    assert status["action_changed_regions"][0]["left"] == 100
    assert status["last_action_change_age_ms"] >= 0


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
        def execute(self, actions, stop_on_error=True, sync=None, subgoal=""):
            captured["sync"] = sync
            captured["subgoal"] = subgoal
            return {"ok": True, "results": [], "metrics": {}}

    class Session(FastToolMixin):
        def __init__(self):
            self._fast_gui_controller = Controller()

        def _fmt(self, obj):
            return json.dumps(obj)

    Session()._t_run_action_batch({
        "subgoal": "open menu",
        "actions": [{"click": {"x": 1, "y": 2}}],
    })

    assert captured["sync"]["mode"] == "stable"
    assert captured["subgoal"] == "open menu"


def test_run_action_batch_tool_defaults_to_stable_sync_for_draw_strokes():
    from winpilot.agent.fast_tools import FastToolMixin

    captured = {}

    class Controller:
        def execute(self, actions, stop_on_error=True, sync=None, subgoal=""):
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
        def execute(self, actions, stop_on_error=True, sync=None, subgoal=""):
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


def test_type_into_element_uses_synthetic_nearby_input_locator():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []

    class Session:
        snapshot = Snapshot(
            hwnd=1,
            title="Custom Target",
            region=Region(0, 0, 900, 500),
            elements=[
                UIElement(elem_id=1, source="ocr", role="text", text="Target", cx=65, cy=50, left=30, top=40, width=70, height=20),
            ],
        )

        def dispatch(self, name, args):
            calls.append((name, args))
            if name == "type_text":
                return ToolResult('{"ok": true, "verified": true}')
            return ToolResult('{"ok": true, "verified": false}')

    out = FastGuiController(Session()).execute([
        {"type_into_element": {"label": "Target", "text": "hello", "clear": False}}
    ], sync=False)

    assert out["ok"]
    assert calls[0][0] == "click"
    assert "x" in calls[0][1] and "y" in calls[0][1]
    assert calls[1] == ("type_text", {"text": "hello"})
    data = out["results"][0]["data"]
    assert data["input_locator"]["found"] is True
    assert data["input_locator"]["candidate"]["source"] == "synthetic_near_label"


def test_subgoal_executor_runs_input_click_verify_routine():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []

    class Session:
        task = "Type FAST_WRAP_OK_099 and verify LOCAL_COUNTER"
        hwnd = 1
        run_id = "run-subgoal"
        snapshot = Snapshot(
            hwnd=1,
            title="Target",
            region=Region(0, 0, 900, 500),
            elements=[
                UIElement(elem_id=1, source="uia", role="edit", text="Target Input", cx=250, cy=100, left=100, top=80, width=300, height=36),
                UIElement(elem_id=2, source="uia", role="button", text="Apply Input", cx=470, cy=100, left=420, top=80, width=100, height=36),
            ],
        )

        def dispatch(self, name, args):
            calls.append((name, args))
            if name == "read_text":
                return ToolResult("FAST_WRAP_OK_099\nLOCAL_COUNTER_1")
            if name == "type_text":
                return ToolResult('{"ok": true, "verified": true}')
            return ToolResult('{"ok": true, "verified": false}')

    session = Session()
    result = SubgoalExecutor(session, FastGuiController(session)).execute({
        "subgoal": "fill target and apply",
        "input": {"label": "Target Input", "text": "FAST_WRAP_OK_099", "clear": False},
        "click": {"label": "Apply Input"},
        "verify_texts": ["FAST_WRAP_OK_099", "LOCAL_COUNTER"],
        "constraints": {"wait": False},
    })
    payload = json.loads(result.text)

    assert payload["ok"] is True
    assert payload["verified"] is True
    assert payload["subgoal_route"] == "controller_first"
    assert payload["evidence_packet"]["matched_texts"] == ["FAST_WRAP_OK_099", "LOCAL_COUNTER"]
    assert [name for name, _args in calls] == ["focus_window", "click", "type_text", "click", "read_text"]


def test_subgoal_verify_scope_does_not_inherit_unrelated_task_targets():
    from winpilot.fast.subgoal import parse_subgoal_request

    task = "Type FAST_WRAP_OK_099 into the input, then verify LOCAL_COUNTER before done."

    input_request = parse_subgoal_request({
        "subgoal": "type FAST_WRAP_OK_099 into the input",
        "input": {"text": "FAST_WRAP_OK_099"},
        "verify_texts": ["FAST_WRAP_OK_099"],
    }, task=task)
    assert input_request.verify_texts == ["FAST_WRAP_OK_099"]

    verify_request = parse_subgoal_request({
        "subgoal": "verify LOCAL_COUNTER is visible",
    }, task=task)
    assert verify_request.verify_texts == ["LOCAL_COUNTER"]


def test_subgoal_executor_runs_ordered_clicks_in_one_routine():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []

    class Session:
        task = "Type MULTI_CLICK_OK and verify FINAL_STATE"
        hwnd = 1
        run_id = "run-subgoal-clicks"
        snapshot = Snapshot(
            hwnd=1,
            title="Target",
            region=Region(0, 0, 900, 500),
            elements=[
                UIElement(elem_id=1, source="uia", role="edit", text="Target Input", cx=250, cy=100, left=100, top=80, width=300, height=36),
                UIElement(elem_id=2, source="uia", role="button", text="Apply", cx=470, cy=100, left=420, top=80, width=100, height=36),
                UIElement(elem_id=3, source="uia", role="button", text="Refresh", cx=590, cy=100, left=540, top=80, width=100, height=36),
            ],
        )

        def dispatch(self, name, args):
            calls.append((name, args))
            if name == "read_text":
                return ToolResult("MULTI_CLICK_OK\nFINAL_STATE")
            if name == "type_text":
                return ToolResult('{"ok": true, "verified": true}')
            return ToolResult('{"ok": true, "verified": false}')

    result = SubgoalExecutor(Session(), FastGuiController(Session())).execute({
        "subgoal": "fill input and run two buttons",
        "input": {"label": "Target Input", "text": "MULTI_CLICK_OK", "clear": False},
        "clicks": [{"label": "Apply"}, {"label": "Refresh"}],
        "verify_texts": ["MULTI_CLICK_OK", "FINAL_STATE"],
        "constraints": {"wait": False},
    })
    payload = json.loads(result.text)

    assert payload["ok"] is True
    assert [action["action"] for action in payload["actions_executed"]] == [
        "type_into_element",
        "click_element",
        "click_element",
        "read_text",
    ]
    assert [name for name, _args in calls] == [
        "focus_window",
        "click",
        "type_text",
        "click",
        "click",
        "read_text",
    ]


def test_subgoal_click_only_uses_changed_observe_without_read_text():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []

    class Session:
        task = "Click status button until READY_STATE appears"
        hwnd = 1
        run_id = "run-subgoal-click-expect"
        snapshot = Snapshot(
            hwnd=1,
            title="Target",
            region=Region(0, 0, 900, 500),
            elements=[
                UIElement(elem_id=7, source="uia", role="button", text="Refresh status", cx=250, cy=100, left=100, top=80, width=180, height=36),
            ],
        )

        def dispatch(self, name, args):
            calls.append((name, args))
            if name == "click":
                assert "expect" not in args
                return ToolResult('{"ok": true, "verified": false}')
            if name == "observe":
                assert args["changed_only"] is True
                assert args["query"] == "READY_STATE"
                return ToolResult("READY_STATE")
            if name == "read_text":
                raise AssertionError("click-only verified subgoal should not fall back to read_text")
            return ToolResult('{"ok": true}')

    result = SubgoalExecutor(Session(), FastGuiController(Session())).execute({
        "subgoal": "click Refresh status until READY_STATE appears",
        "click": {"label": "Refresh status"},
        "verify_texts": ["READY_STATE"],
        "constraints": {"wait": False},
    })
    payload = json.loads(result.text)

    assert payload["ok"] is True
    assert payload["verified"] is True
    assert payload["evidence_packet"]["matched_texts"] == ["READY_STATE"]
    assert payload["evidence_packet"]["verified_expect_texts"] == []
    assert "complete the matching" in payload["fast_path_advice"].lower()
    assert [action["action"] for action in payload["actions_executed"]] == ["click_element", "observe"]
    assert [name for name, _args in calls] == ["focus_window", "click", "observe"]


def test_subgoal_uses_targeted_initial_roi_when_region_hint_matches(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import subgoal as subgoal_mod
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []
    captured_regions = []

    monkeypatch.setattr(subgoal_mod, "window_region", lambda hwnd: Region(0, 0, 1000, 800))
    monkeypatch.setattr(subgoal_mod.win32, "_window_text", lambda hwnd: "Target")
    monkeypatch.setattr(subgoal_mod.win32gui, "GetForegroundWindow", lambda: 1)

    def fake_ocr_collect(region, min_conf=None):
        captured_regions.append(region)
        return [
            UIElement(source="ocr", role="text", text="Export", cx=870, cy=90, left=830, top=78, width=80, height=24),
        ]

    monkeypatch.setattr(subgoal_mod.ocr, "collect", fake_ocr_collect)
    monkeypatch.setattr(subgoal_mod.vision_cv, "detect_icon_candidates", lambda region: [])

    class Session:
        task = "Click Export"
        hwnd = 1
        run_id = "run-targeted-roi"
        snapshot = None

        def dispatch(self, name, args):
            calls.append((name, args))
            if name == "observe":
                raise AssertionError("targeted initial ROI should avoid full observe")
            if name == "click":
                assert args["element_id"] == 1
                return ToolResult('{"ok": true, "verified": false}')
            return ToolResult('{"ok": true}')

    session = Session()
    result = SubgoalExecutor(session, FastGuiController(session)).execute({
        "subgoal": "click Export",
        "click": {"label": "Export", "expected_region": "top-right"},
        "constraints": {"wait": False},
    })
    payload = json.loads(result.text)

    assert payload["ok"] is True
    assert any(item["action"] == "targeted_observe" for item in payload["evidence_packet"]["prelude"])
    assert session.snapshot is not None
    assert session.snapshot.region == Region(0, 0, 1000, 800)
    assert session.snapshot.elements[0].states == ["targeted_initial_roi"]
    assert captured_regions == [Region(450, 0, 550, 384)]
    assert [name for name, _args in calls] == ["focus_window", "click"]


def test_subgoal_targeted_roi_no_label_match_returns_candidates_without_full_observe(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import subgoal as subgoal_mod
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []

    monkeypatch.setattr(subgoal_mod, "window_region", lambda hwnd: Region(0, 0, 1000, 800))
    monkeypatch.setattr(subgoal_mod.win32, "_window_text", lambda hwnd: "Target")
    monkeypatch.setattr(subgoal_mod.win32gui, "GetForegroundWindow", lambda: 1)
    monkeypatch.setattr(
        subgoal_mod.ocr,
        "collect",
        lambda region, min_conf=None: [
            UIElement(source="ocr", role="text", text="只改局部状态", cx=210, cy=200, left=155, top=188, width=110, height=24),
            UIElement(source="ocr", role="text", text="打开弹窗", cx=340, cy=200, left=300, top=188, width=80, height=24),
        ],
    )
    monkeypatch.setattr(subgoal_mod.vision_cv, "detect_icon_candidates", lambda region: [])

    class Session:
        task = "Click Local Counter and verify LOCAL_COUNTER"
        hwnd = None
        run_id = "run-targeted-roi-miss"
        snapshot = None

        def dispatch(self, name, args):
            calls.append((name, args))
            if name == "observe":
                raise AssertionError("label-miss targeted ROI should not fall back to full observe")
            return ToolResult('{"ok": true}')

    session = Session()
    result = SubgoalExecutor(session, FastGuiController(session)).execute({
        "subgoal": "click Local Counter",
        "click": {"label": "Local Counter", "expected_region": "left"},
        "verify_texts": ["LOCAL_COUNTER"],
        "constraints": {"wait": False},
    })
    payload = json.loads(result.text)

    assert payload["ok"] is False
    assert payload["actions_executed"][0]["skip_auto_observe"] is True
    assert payload["evidence_packet"]["prelude"][1]["action"] == "targeted_observe"
    assert payload["evidence_packet"]["prelude"][1]["label_matched"] is False
    assert "只改局部状态" in payload["evidence_packet"]["candidate_click_labels"]
    assert calls == [("focus_window", {"hwnd": 1})]


def test_subgoal_anchor_failure_returns_visible_region_candidates():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []

    class Session:
        task = "Click Local Counter and verify LOCAL_COUNTER"
        hwnd = 1
        run_id = "run-subgoal-candidates"
        snapshot = Snapshot(
            hwnd=1,
            title="Target",
            region=Region(0, 0, 900, 500),
            elements=[
                UIElement(elem_id=1, source="ocr", role="text", text="局部变化测试区", cx=100, cy=120, left=40, top=108, width=130, height=24),
                UIElement(elem_id=2, source="ocr", role="text", text="应用输入", cx=90, cy=200, left=60, top=188, width=72, height=24),
                UIElement(elem_id=3, source="ocr", role="text", text="只改局部状态", cx=210, cy=200, left=155, top=188, width=110, height=24),
                UIElement(elem_id=4, source="ocr", role="text", text="打开弹窗", cx=330, cy=200, left=292, top=188, width=76, height=24),
            ],
        )

        def dispatch(self, name, args):
            calls.append((name, args))
            return ToolResult('{"ok": true}')

    session = Session()
    result = SubgoalExecutor(session, FastGuiController(session)).execute({
        "subgoal": "click Local Counter",
        "click": {"label": "Local Counter", "expected_region": "left"},
        "verify_texts": ["LOCAL_COUNTER"],
        "constraints": {"wait": False},
    })
    payload = json.loads(result.text)

    assert payload["ok"] is False
    assert payload["needs_llm_reason"] is True
    assert "只改局部状态" in payload["evidence_packet"]["candidate_click_labels"]
    assert "retry execute_subgoal" in payload["fast_path_advice"]
    assert calls == [
        ("focus_window", {"hwnd": 1}),
        ("observe", {"query": "Local Counter", "max_elements": 160}),
    ]


def test_execute_subgoal_draw_generates_strokes_inside_likely_canvas(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import subgoal as subgoal_mod
    from winpilot.fast.subgoal import SubgoalExecutor
    from winpilot.utils.screenshot import Region

    window = Region(10, 20, 500, 400)
    image = np.zeros((400, 500, 3), dtype=np.uint8)
    image[120:360, 80:460] = 255
    captured_actions = []

    monkeypatch.setattr(subgoal_mod, "window_region", lambda hwnd: window)

    class Watcher:
        @staticmethod
        def status():
            return {}

    class Controller:
        watcher = Watcher()
        capture_fn = staticmethod(lambda region: image)

        def execute(self, actions, **kwargs):
            captured_actions.extend(actions)
            return {
                "ok": True,
                "verified": True,
                "controller_state": {"phase": "verified"},
                "metrics": {"action_count": len(actions)},
                "results": [
                    {
                        "action": "draw_strokes",
                        "verified": True,
                        "data": {
                            "ok": True,
                            "verified": True,
                            "visual_change_detected": True,
                        },
                    },
                    {
                        "action": "auto_visual_verify",
                        "verified": True,
                        "data": {
                            "ok": True,
                            "verified": True,
                            "visual_change_detected": True,
                            "nonvisual_summary": {
                                "visible_colors": {
                                    "blue": {"pixels": 120, "ratio": 0.02},
                                },
                            },
                        },
                    },
                ],
            }

    class Session:
        task = "draw a blue triangle on the canvas"
        hwnd = 7
        run_id = "run-draw-subgoal"
        snapshot = None

        def dispatch(self, name, args):
            assert name == "focus_window"
            return ToolResult('{"ok": true}')

    result = SubgoalExecutor(Session(), Controller()).execute({
        "subgoal": "draw a blue triangle on the visible canvas",
        "draw": {"shape": "triangle", "color": "blue"},
        "constraints": {"wait": False},
    })
    payload = json.loads(result.text)

    assert captured_actions[0] == {"action": "select_color", "color": "blue"}
    draw = captured_actions[1]
    canvas = draw["canvas_region"]
    assert payload["ok"] is True
    assert payload["verified"] is True
    assert draw["action"] == "draw_strokes"
    assert draw["shape"] == "triangle"
    assert draw["requested_color"] == "blue"
    assert canvas == {"x": 90, "y": 140, "width": 380, "height": 240}
    for stroke in draw["strokes"]:
        for x, y in stroke:
            assert canvas["x"] <= x <= canvas["x"] + canvas["width"]
            assert canvas["y"] <= y <= canvas["y"] + canvas["height"]
    assert "controller already executed and verified" in payload["fast_path_advice"].casefold()


def test_execute_subgoal_draw_rejects_missing_requested_color(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import subgoal as subgoal_mod
    from winpilot.fast.subgoal import SubgoalExecutor
    from winpilot.utils.screenshot import Region

    window = Region(0, 0, 400, 300)
    image = np.full((300, 400, 3), 255, dtype=np.uint8)
    captured_actions = []

    monkeypatch.setattr(subgoal_mod, "window_region", lambda hwnd: window)

    class Watcher:
        @staticmethod
        def status():
            return {}

    class Controller:
        watcher = Watcher()
        capture_fn = staticmethod(lambda region: image)

        def execute(self, actions, **kwargs):
            captured_actions.extend(actions)
            return {
                "ok": True,
                "verified": True,
                "controller_state": {"phase": "verified"},
                "metrics": {"action_count": len(actions)},
                "results": [
                    {
                        "action": "draw_strokes",
                        "verified": True,
                        "data": {
                            "ok": True,
                            "verified": True,
                            "visual_change_detected": True,
                        },
                    },
                    {
                        "action": "auto_visual_verify",
                        "verified": True,
                        "data": {
                            "ok": True,
                            "verified": True,
                            "visual_change_detected": True,
                            "nonvisual_summary": {
                                "visible_colors": {
                                    "black": {"pixels": 120, "ratio": 0.02},
                                },
                            },
                        },
                    },
                ],
            }

    class Session:
        task = "draw a blue triangle on the canvas"
        hwnd = 7
        run_id = "run-draw-color-miss"
        snapshot = None

        def dispatch(self, name, args):
            assert name == "focus_window"
            return ToolResult('{"ok": true}')

    result = SubgoalExecutor(Session(), Controller()).execute({
        "subgoal": "draw a blue triangle on the visible canvas",
        "draw": {"shape": "triangle", "color": "blue"},
        "constraints": {"wait": False},
    })
    payload = json.loads(result.text)

    assert payload["ok"] is False
    assert payload["verified"] is False
    assert payload["evidence_packet"]["missing_visual_colors"] == ["blue"]
    assert "required color evidence is missing" in payload["error"]


def test_fast_controller_select_color_clicks_visible_palette_swatch():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.fusion import Snapshot
    from winpilot.utils.screenshot import Region

    image = np.full((220, 300, 3), 96, dtype=np.uint8)
    swatches = [
        ((20, 110), (0, 0, 255)),      # red
        ((50, 110), (0, 128, 255)),    # orange
        ((80, 110), (0, 255, 255)),    # yellow
        ((110, 110), (0, 180, 0)),     # green
        ((140, 110), (255, 0, 0)),     # blue
        ((170, 110), (180, 0, 180)),   # purple
        ((20, 145), (0, 0, 255)),
        ((50, 145), (0, 255, 255)),
        ((80, 145), (0, 180, 0)),
        ((110, 145), (255, 0, 0)),
    ]
    for (x, y), color in swatches:
        image[y:y + 18, x:x + 18] = color

    clicks = []

    class Session:
        task = "draw a blue triangle"
        hwnd = 1
        run_id = "run-select-color"
        snapshot = Snapshot(hwnd=1, title="Palette", region=Region(100, 200, 300, 220), elements=[])

        def dispatch(self, name, args):
            assert name == "click"
            clicks.append(args)
            return ToolResult('{"ok": true}')

    controller = FastGuiController(Session(), capture_fn=lambda region: image)
    out = controller.execute([{"action": "select_color", "color": "blue"}], sync=False)

    assert out["ok"] is True
    assert out["verified"] is True
    assert clicks
    assert 235 <= clicks[0]["x"] <= 258
    assert 305 <= clicks[0]["y"] <= 322


def test_palette_swatch_detection_ignores_titlebar_colorful_icon():
    from winpilot.fast.controller import FastGuiController
    from winpilot.utils.screenshot import Region

    image = np.full((220, 300, 3), 96, dtype=np.uint8)
    # A colorful app icon in the title bar should not be treated as an open
    # palette; there is no local cluster of color choices.
    title_colors = [
        ((12, 12), (255, 0, 0)),
        ((26, 12), (0, 255, 0)),
        ((12, 26), (0, 0, 255)),
        ((26, 26), (0, 255, 255)),
        ((150, 90), (255, 0, 0)),
        ((190, 110), (0, 255, 0)),
        ((230, 130), (0, 0, 255)),
        ((260, 150), (180, 0, 180)),
    ]
    for (x, y), color in title_colors:
        image[y:y + 10, x:x + 10] = color

    assert FastGuiController._palette_swatch_point(image, Region(100, 200, 300, 220), "blue") is None


def test_select_color_prefers_target_hwnd_region_over_stale_snapshot(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import controller as controller_mod
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.fusion import Snapshot
    from winpilot.utils.screenshot import Region

    target_region = Region(100, 200, 300, 220)
    monkeypatch.setattr(controller_mod, "window_region", lambda hwnd: target_region)
    image = np.full((220, 300, 3), 96, dtype=np.uint8)
    for x, color in [
        (20, (0, 0, 255)),
        (50, (0, 128, 255)),
        (80, (0, 255, 255)),
        (110, (0, 180, 0)),
        (140, (255, 0, 0)),
        (170, (180, 0, 180)),
        (200, (0, 0, 255)),
        (230, (0, 255, 255)),
    ]:
        image[110:128, x:x + 18] = color
    clicks = []
    captured_regions = []

    class Session:
        task = "draw a blue triangle"
        hwnd = 7
        run_id = "run-select-color-hwnd"
        snapshot = Snapshot(hwnd=99, title="Stale", region=Region(0, 0, 1920, 1080), elements=[])

        def dispatch(self, name, args):
            clicks.append(args)
            return ToolResult('{"ok": true}')

    def capture_fn(region):
        captured_regions.append(region)
        return image

    controller = FastGuiController(Session(), capture_fn=capture_fn)
    out = controller.execute([{"action": "select_color", "color": "blue"}], sync=False)

    assert out["ok"] is True
    assert captured_regions == [target_region]
    assert clicks
