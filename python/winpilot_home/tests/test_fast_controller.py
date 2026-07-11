import json
from types import SimpleNamespace

import numpy as np


def test_batch_observe_actions_are_allowed_or_internal():
    from winpilot.fast.controller import ALLOWED_BATCH_ACTIONS, BATCH_OBSERVE_ACTIONS

    assert BATCH_OBSERVE_ACTIONS <= (ALLOWED_BATCH_ACTIONS | {"auto_visual_verify"})
    assert "vlm_describe" not in BATCH_OBSERVE_ACTIONS


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


def test_element_cache_rejects_browser_url_when_button_role_requested():
    from winpilot.fast.element_cache import ElementCache
    from winpilot.fast.types import VisualAnchor
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    snapshot = Snapshot(
        hwnd=1,
        title="Target - Browser",
        region=Region(0, 0, 1200, 800),
        elements=[
            UIElement(
                elem_id=4,
                source="uia",
                role="text",
                text="http://127.0.0.1:8877/gui_agent_target.html?v=coord1",
                cx=430,
                cy=56,
                left=160,
                top=40,
                width=700,
                height=28,
            ),
            UIElement(
                elem_id=9,
                source="ocr",
                role="text",
                text="校准点",
                cx=620,
                cy=620,
                left=580,
                top=600,
                width=80,
                height=36,
            ),
        ],
    )
    cache = ElementCache()

    located = cache.locate_with_trace(
        snapshot,
        VisualAnchor(label="校准点", fallback_text=("Calibration",), role="button"),
    )

    assert located.element is not None
    assert located.element.elem_id == 9


def test_element_cache_rejects_long_log_substring_as_click_anchor():
    from winpilot.fast.element_cache import ElementCache, anchor_from_dict
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    snapshot = Snapshot(
        hwnd=1,
        title="Target - Browser",
        region=Region(0, 0, 1200, 800),
        elements=[
            UIElement(
                elem_id=1,
                source="uia",
                role="text",
                text='{"error":"Press pulse was not found", "trace":"' + "x" * 500 + '"}',
                cx=300,
                cy=650,
                left=20,
                top=610,
                width=620,
                height=80,
            ),
            UIElement(
                elem_id=2,
                source="ocr",
                role="text",
                text="Press pulse",
                cx=250,
                cy=180,
                left=190,
                top=160,
                width=120,
                height=40,
            ),
        ],
    )
    anchor = anchor_from_dict({
        "label": "Press pulse",
        "visual_anchor": "teal button",
        "expected_region": "top-left",
    })

    located = ElementCache().locate_with_trace(snapshot, anchor)

    assert anchor.role == "button"
    assert located.element is not None
    assert located.element.elem_id == 2


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


def test_fast_controller_click_element_uses_real_mouse_by_default_when_enabled():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []

    class Session:
        physical_anchor_clicks = True
        snapshot = Snapshot(
            hwnd=1,
            title="Demo",
            region=Region(0, 0, 1000, 600),
            elements=[UIElement(elem_id=5, role="button", text="Pulse", cx=920, cy=50)],
        )

        def dispatch(self, name, args):
            calls.append((name, args))
            return ToolResult('{"ok": true, "verified": true}')

    out = FastGuiController(Session()).execute([
        {"action": "click_element", "label": "Pulse", "role": "button"}
    ])

    assert out["ok"]
    assert calls == [(
        "click",
        {"element_id": 5, "button": "left", "double": False, "physical": True},
    )]


def test_fast_controller_click_element_can_prefer_uia_invoke():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []

    class Session:
        physical_anchor_clicks = True
        snapshot = Snapshot(
            hwnd=1,
            title="Demo",
            region=Region(0, 0, 1000, 600),
            elements=[UIElement(elem_id=5, role="button", text="Export", cx=920, cy=50)],
        )

        def dispatch(self, name, args):
            calls.append((name, args))
            return ToolResult('{"ok": true, "verified": true}')

    out = FastGuiController(Session()).execute([{
        "action": "click_element",
        "label": "Export",
        "role": "button",
        "prefer_uia_invoke": True,
    }])

    assert out["ok"]
    assert calls == [(
        "click",
        {"element_id": 5, "button": "left", "double": False},
    )]


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
        ("observe", {"query": "Export", "max_elements": 160, "actionable_query": True}),
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
        ("hotkey", {"keys": ["backspace"]}),
        ("type_text", {"text": "hello", "expect": expected_expect}),
    ]
    data = out["results"][0]["data"]
    assert data["via"] == "type_into_element"
    assert data["controller_internal_action_count"] == 4
    assert data["resolved_element"]["elem_id"] == 4
    assert data["subresults"][0]["action"] == "click_element"
    assert data["subresults"][3]["action"] == "type_text"
    assert out["metrics"]["internal_action_count"] == 4
    assert out["metrics"]["compound_round_trips_saved"] == 3
    assert out["metrics"]["controller_round_trips_saved"] == 3
    assert out["controller_state"]["phase"] == "verified"


def test_fast_controller_visual_servo_corrects_target_before_click(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import controller as controller_mod
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []

    class Session:
        snapshot = Snapshot(
            hwnd=1,
            title="Moving UI",
            region=Region(0, 0, 1000, 600),
            elements=[UIElement(
                elem_id=5, role="button", text="Export",
                cx=100, cy=100, left=70, top=85, width=60, height=30,
            )],
        )

        def dispatch(self, name, args):
            calls.append((name, args))
            return ToolResult('{"ok": true, "verified": true}')

    def fake_track(initial, labels, **kwargs):
        assert labels == ["Export"]
        kwargs["move_fn"](116, 109)
        return UIElement(
            elem_id=initial.elem_id,
            source="servo-template",
            role="button",
            text="Export",
            cx=116,
            cy=109,
            left=86,
            top=94,
            width=60,
            height=30,
        ), {
            "enabled": True,
            "confirmed": True,
            "corrected": True,
            "correction_count": 1,
            "elapsed_ms": 12,
            "source_counts": {"servo-template": 2},
            "lost_reason": "",
            "final": {"x": 116, "y": 109},
        }

    monkeypatch.setattr(controller_mod, "track_visual_target", fake_track)

    out = FastGuiController(Session()).execute([{
        "action": "click_element",
        "label": "Export",
        "role": "button",
        "visual_servo": True,
        "servo_iterations": 1,
    }])

    assert out["ok"] is True
    assert calls[0] == ("move_mouse", {"x": 116, "y": 109, "skip_screen_delta": True})
    assert calls[1] == ("click", {
        "x": 116,
        "y": 109,
        "button": "left",
        "double": False,
        "physical": True,
        "_prepositioned": True,
    })
    servo = out["results"][0]["data"]["visual_servo"]
    assert servo["corrected"] is True
    assert servo["final"] == {"x": 116, "y": 109}
    assert out["metrics"]["visual_servo_count"] == 1
    assert out["metrics"]["visual_servo_correction_count"] == 1


def test_fast_controller_visual_servo_lost_target_blocks_click(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import controller as controller_mod
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []

    class Session:
        snapshot = Snapshot(
            hwnd=1,
            title="Moving UI",
            region=Region(0, 0, 1000, 600),
            elements=[UIElement(
                elem_id=5, role="button", text="Export",
                cx=100, cy=100, left=70, top=85, width=60, height=30,
            )],
        )

        def dispatch(self, name, args):
            calls.append((name, args))
            return ToolResult('{"ok": true, "verified": true}')

    monkeypatch.setattr(
        controller_mod,
        "track_visual_target",
        lambda *_args, **_kwargs: (None, {
            "enabled": True,
            "confirmed": False,
            "corrected": False,
            "correction_count": 0,
            "elapsed_ms": 9,
            "source_counts": {},
            "lost_reason": "final_confirmation_lost_target",
        }),
    )

    out = FastGuiController(Session()).execute([{
        "action": "click_element",
        "label": "Export",
        "role": "button",
        "visual_servo": True,
    }])

    assert out["ok"] is False
    assert calls == []
    assert out["results"][0]["data"]["blocked"] is True
    assert out["results"][0]["data"]["visual_servo"]["confirmed"] is False
    assert out["metrics"]["visual_servo_failure_count"] == 1


def test_fast_controller_adaptively_servos_recently_changed_target(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import controller as controller_mod
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    element = UIElement(
        elem_id=5, role="button", text="Export",
        cx=100, cy=100, left=70, top=85, width=60, height=30,
    )
    calls = []

    class Session:
        snapshot = Snapshot(
            hwnd=1,
            title="Moving UI",
            region=Region(0, 0, 1000, 600),
            elements=[element],
        )

        def dispatch(self, name, args):
            calls.append((name, args))
            return ToolResult('{"ok": true, "verified": true}')

    class Watcher:
        @staticmethod
        def status():
            return {
                "last_change_age_ms": 0,
                "last_action_change_age_ms": 10_000,
                "recent_changed_regions": [
                    {"left": 80, "top": 90, "width": 30, "height": 20},
                ],
            }

    monkeypatch.setattr(
        controller_mod,
        "track_visual_target",
        lambda initial, *_args, **_kwargs: (initial, {
            "enabled": True,
            "confirmed": True,
            "corrected": False,
            "correction_count": 0,
            "elapsed_ms": 5,
            "source_counts": {"servo-template": 1},
            "lost_reason": "",
        }),
    )

    out = FastGuiController(Session(), watcher=Watcher()).execute([{
        "action": "click_element",
        "label": "Export",
        "role": "button",
    }])

    assert out["ok"] is True
    assert calls[0][0] == "click"
    servo = out["results"][0]["data"]["visual_servo"]
    assert servo["activation"] == "recent_target_roi_change"
    assert out["metrics"]["visual_servo_count"] == 1


def test_type_into_element_append_newline_moves_caret_to_end_first():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []

    class Session:
        snapshot = Snapshot(
            hwnd=1,
            title="Untitled - Notepad",
            region=Region(0, 0, 1000, 600),
            elements=[UIElement(elem_id=4, role="edit", text="first line", cx=500, cy=300)],
        )

        def dispatch(self, name, args):
            calls.append((name, args))
            if name == "type_text":
                return ToolResult('{"ok": true, "verified": true}')
            return ToolResult('{"ok": true, "verified": false}')

    out = FastGuiController(Session()).execute([
        {
            "type_into_element": {
                "label": "editor",
                "role": "edit",
                "text": "\nsecond line",
                "clear": False,
            }
        }
    ])

    assert out["ok"]
    assert calls == [
        ("click", {"element_id": 4, "button": "left", "double": False}),
        ("hotkey", {"keys": ["ctrl", "end"]}),
        ("type_text", {"text": "\nsecond line"}),
    ]
    data = out["results"][0]["data"]
    assert data["controller_internal_action_count"] == 3
    assert data["subresults"][1]["purpose"] == "move_to_end_before_append"


def test_fast_controller_type_into_element_rejects_wildcard_prefix_after_clear():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []

    class Session:
        snapshot = Snapshot(
            hwnd=1,
            title="Save As",
            region=Region(0, 0, 1000, 600),
            elements=[
                UIElement(elem_id=3, source="ocr", role="text", text="文件名(N):", cx=120, cy=520, left=70, top=510, width=100, height=22),
                UIElement(elem_id=4, role="edit", text="*.txt", cx=300, cy=520),
            ],
        )

        def dispatch(self, name, args):
            calls.append((name, args))
            if name == "read_text":
                return ToolResult("文件名(N): *.txtWinPilotE2E_note.txt\n保存(S)")
            if name == "type_text":
                return ToolResult('{"ok": true, "verified": true}')
            return ToolResult('{"ok": true, "verified": false}')

    out = FastGuiController(Session()).execute([
        {
            "type_into_element": {
                "label": "文件名",
                "role": "edit",
                "text": "WinPilotE2E_note.txt",
                "clear": True,
                "expect": {"text_appears": "WinPilotE2E_note.txt"},
            }
        }
    ])

    assert out["ok"] is False
    data = out["results"][0]["data"]
    assert data["stage"] == "replace_guard"
    assert "wildcard" in data["error"] or "glob" in data["error"]
    assert [name for name, _args in calls] == [
        "click",
        "hotkey",
        "hotkey",
        "type_text",
        "read_text",
    ]


def test_type_into_element_prefers_real_input_near_concrete_label():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []

    class Session:
        snapshot = Snapshot(
            hwnd=1,
            title="Save As",
            region=Region(0, 0, 1000, 600),
            elements=[
                UIElement(elem_id=3, source="ocr", role="text", text="文件名(N):", cx=90, cy=520, left=40, top=508, width=100, height=24),
                UIElement(elem_id=4, source="win32", role="edit", text="*.txt", cx=540, cy=520, left=150, top=508, width=780, height=24),
            ],
        )

        def dispatch(self, name, args):
            calls.append((name, args))
            if name == "read_text":
                return ToolResult("文件名(N): WinPilotE2E_note.txt")
            if name == "type_text":
                return ToolResult('{"ok": true, "verified": true}')
            return ToolResult('{"ok": true, "verified": false}')

    out = FastGuiController(Session()).execute([
        {
            "type_into_element": {
                "label": "文件名",
                "role": "edit",
                "text": "C:\\Users\\zou\\Desktop\\WinPilotE2E_note.txt",
                "clear": True,
            }
        }
    ])

    assert out["ok"]
    assert calls[0] == ("click", {"element_id": 4, "button": "left", "double": False})
    data = out["results"][0]["data"]
    assert data["resolved_element"]["elem_id"] == 4
    assert "near concrete label" in data["input_locator"]["reason"]


def test_type_into_element_rejects_missing_high_risk_text_after_typing():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    class Session:
        snapshot = Snapshot(
            hwnd=1,
            title="Save As",
            region=Region(0, 0, 1000, 600),
            elements=[
                UIElement(elem_id=3, source="ocr", role="text", text="文件名(N):", cx=90, cy=520, left=40, top=508, width=100, height=24),
                UIElement(elem_id=4, source="win32", role="edit", text="*.txt", cx=540, cy=520, left=150, top=508, width=780, height=24),
            ],
        )

        def dispatch(self, name, args):
            if name == "read_text":
                return ToolResult("文件名(N): *.txt\n保存(S)")
            if name == "type_text":
                return ToolResult('{"ok": true, "verified": true}')
            return ToolResult('{"ok": true, "verified": false}')

    out = FastGuiController(Session()).execute([
        {
            "type_into_element": {
                "label": "文件名",
                "role": "edit",
                "text": "C:\\Users\\zou\\Desktop\\WinPilotE2E_note.txt",
                "clear": True,
            }
        }
    ])

    assert out["ok"] is False
    data = out["results"][0]["data"]
    assert data["stage"] == "replace_guard"
    assert data["error"] == "typed text was not visible after input"


def test_type_into_element_does_not_use_generic_edit_for_missing_filename_label():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []

    class Session:
        snapshot = Snapshot(
            hwnd=1,
            title="Untitled - Notepad",
            region=Region(0, 0, 1000, 600),
            elements=[UIElement(elem_id=4, role="edit", text="", cx=500, cy=300)],
        )

        def dispatch(self, name, args):
            calls.append((name, args))
            return ToolResult('{"ok": true, "verified": false}')

    out = FastGuiController(Session()).execute([
        {
            "type_into_element": {
                "label": "文件名(N):",
                "role": "edit",
                "text": "C:\\Users\\zou\\Desktop\\WinPilotE2E_note.txt",
                "clear": True,
            }
        }
    ])

    assert out["ok"] is False
    data = out["results"][0]["data"]
    assert data["stage"] == "focus"
    assert data["input_locator"]["reason"] == "concrete_label_not_found"
    assert calls == []


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
        ("hotkey", {"keys": ["backspace"]}),
        ("type_text", {"text": "hello"}),
    ]
    assert out["results"][0]["action"] == "type_into_element"
    assert out["results"][0]["upgraded_from"] == "type_text"
    assert out["results"][0]["data"]["via"] == "type_into_element"
    assert out["metrics"]["compound_round_trips_saved"] == 3


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


def test_fast_controller_requires_transient_screen_evidence_when_requested():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController

    class Session:
        snapshot = None

        def dispatch(self, name, args):
            return ToolResult(json.dumps({
                "ok": True,
                "screen_delta": {
                    "changed": False,
                    "transient_observed": False,
                },
            }))

    out = FastGuiController(Session()).execute([
        {"action": "click", "x": 10, "y": 20, "require_transient": True},
    ], sync=False)

    assert out["ok"] is False
    assert out["metrics"]["failed_at"] == 1
    assert out["results"][0]["error"] == "required controller evidence was not observed"


def test_fast_controller_promotes_captured_transient_to_batch_evidence():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController

    class Session:
        snapshot = None

        def dispatch(self, name, args):
            return ToolResult(json.dumps({
                "ok": True,
                "verified": False,
                "verify_detail": "no explicit expect",
                "screen_delta": {
                    "changed": True,
                    "delta_source": "press",
                    "transient_observed": True,
                },
            }))

    out = FastGuiController(Session()).execute([
        {"action": "click", "x": 10, "y": 20, "require_transient": True},
    ], sync=False)

    assert out["ok"] is True
    assert out["verified"] is True
    assert out["controller_state"]["verified"] is True
    assert out["results"][0]["verified"] is True


def test_fast_controller_required_observe_rejects_long_log_match():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    class Session:
        snapshot = Snapshot(
            hwnd=1,
            title="Demo",
            region=Region(0, 0, 1000, 700),
            elements=[UIElement(
                source="uia",
                role="text",
                text="PRESS_PULSE_ACTIVE appeared in an old trace " + "x" * 500,
                cx=400,
                cy=500,
            )],
        )

        def dispatch(self, name, args):
            return ToolResult("observe completed", is_observe=True)

    out = FastGuiController(Session()).execute([
        {"action": "observe", "query": "PRESS_PULSE_ACTIVE", "require_match": True},
    ], sync=False)

    assert out["ok"] is False
    assert out["results"][0]["query_match"] is False


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


def test_fast_controller_uses_raw_device_adapter_inside_batch():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController

    calls = []

    class Session:
        snapshot = None
        hwnd = 42

        def _t_click(self, args):
            calls.append(("raw", args))
            return ToolResult('{"ok": true, "verified": false}')

        def dispatch(self, name, args):
            raise AssertionError(f"recursive ToolSession dispatch: {name} {args}")

    out = FastGuiController(Session()).execute(
        [{"click": {"x": 11, "y": 22}}],
        sync=False,
    )

    assert out["ok"] is True
    assert calls == [("raw", {"x": 11, "y": 22})]


def test_fast_controller_acquires_combined_clipboard_and_physical_lease(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import controller as controller_module
    from winpilot.fast.controller import FastGuiController

    leases = []

    def fake_run(action, fn, **kwargs):
        leases.append((action, kwargs.get("scope"), kwargs.get("target_hwnd")))
        return fn()

    monkeypatch.setattr(controller_module.DESKTOP_ACTION_QUEUE, "run", fake_run)

    class Session:
        snapshot = None
        hwnd = 42

        def dispatch(self, name, args):
            return ToolResult('{"ok": true, "verified": true}')

    controller = FastGuiController(Session())
    clipboard_only = controller.execute(
        [{"clipboard_write": {"text": "value"}}],
        sync=False,
    )
    mixed = controller.execute(
        [
            {"clipboard_write": {"text": "value"}},
            {"click": {"x": 11, "y": 22}},
        ],
        sync=False,
    )

    assert clipboard_only["ok"] is True
    assert mixed["ok"] is True
    assert leases == [
        ("controller_batch", "clipboard", 42),
        ("controller_batch", "physical_clipboard", 42),
    ]


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
    evidence = watcher.recent_evidence()
    assert evidence
    crop, crop_region = evidence[0]
    assert crop.size > 0
    assert crop_region.left <= 106
    assert crop_region.top <= 205


def test_screen_watcher_notifies_and_can_remove_change_listener():
    from winpilot.fast.watcher import ScreenWatcher
    from winpilot.utils.screenshot import Region

    frames = [
        np.zeros((30, 30, 3), dtype=np.uint8),
        np.full((30, 30, 3), 255, dtype=np.uint8),
        np.zeros((30, 30, 3), dtype=np.uint8),
    ]
    calls = 0

    def capture_frame(_region):
        nonlocal calls
        frame = frames[min(calls, len(frames) - 1)]
        calls += 1
        return frame

    watcher = ScreenWatcher(
        region_provider=lambda: Region(50, 60, 30, 30),
        capture_fn=capture_frame,
        event_context_provider=lambda: {"run_id": "listener-run"},
    )
    observed = []

    def listener(delta, crops, metadata):
        observed.append((delta, crops, metadata))

    watcher.add_change_listener(listener)
    watcher.sample_once()
    watcher.sample_once()
    watcher.remove_change_listener(listener)
    watcher.sample_once()

    assert len(observed) == 1
    delta, crops, metadata = observed[0]
    assert delta.changed is True
    assert crops and crops[0][1].left == 50
    assert metadata["run_id"] == "listener-run"
    assert metadata["action_delta"] is False


def test_screen_watcher_notifies_transient_action_evidence():
    from winpilot.fast.types import ScreenDelta
    from winpilot.fast.watcher import ScreenWatcher
    from winpilot.utils.screenshot import Region

    watcher = ScreenWatcher(region_provider=lambda: Region(0, 0, 40, 40))
    observed = []
    watcher.add_change_listener(
        lambda delta, crops, metadata: observed.append((delta, crops, metadata))
    )
    frame = np.zeros((40, 40, 3), dtype=np.uint8)
    frame[10:20, 10:20] = 255

    watcher.record_action_delta(
        ScreenDelta(True, 0.06, [Region(10, 10, 10, 10)], 2),
        Region(0, 0, 40, 40),
        evidence_frame=frame,
        transient=True,
    )

    assert len(observed) == 1
    assert observed[0][1]
    assert observed[0][2]["action_delta"] is True
    assert observed[0][2]["transient"] is True


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


def test_execute_subgoal_tool_rejects_empty_routine_without_controller():
    from winpilot.agent.fast_tools import FastToolMixin

    class Session(FastToolMixin):
        def _fast_controller(self):
            raise AssertionError("empty execute_subgoal should not initialize controller/perception")

    result = Session()._t_execute_subgoal({
        "subgoal": "Open Windows Paint app and verify the canvas is ready",
        "constraints": {"wait": True},
    })
    data = json.loads(result.text)

    assert data["ok"] is False
    assert data["blocked"] is True
    assert "subgoal string is only a label" in data["error"]
    assert "launch/find_app/focus_window" in data["fast_path_advice"]


def test_execute_subgoal_rejects_unresolved_target_window_without_controller():
    from winpilot.agent.fast_tools import FastToolMixin

    class Session(FastToolMixin):
        hwnd = None

        def _fast_controller(self):
            raise AssertionError("unresolved target_window should not initialize controller/perception")

    result = Session()._t_execute_subgoal({
        "subgoal": "Open Windows Paint and click the blank canvas",
        "target_window": {"process": "mspaint.exe"},
        "click": {"label": "blank canvas"},
    })
    data = json.loads(result.text)

    assert data["ok"] is False
    assert data["blocked"] is True
    assert data["needs_target_window"] is True
    assert "Resolve it to a real hwnd" in data["error"]
    assert "launch/find_app/list_windows/scan_windows/focus_window" in data["fast_path_advice"]


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


def test_input_locator_prefers_placeholder_over_field_label_text():
    from winpilot.fast.input_locator import locate_input
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    snapshot = Snapshot(
        hwnd=1,
        title="Custom Target",
        region=Region(0, 0, 900, 500),
        elements=[
            UIElement(elem_id=1, source="ocr", role="text", text="目标输入框", cx=90, cy=300, left=45, top=290, width=90, height=22),
            UIElement(elem_id=2, source="ocr", role="text", text="在这里输入测试文字", cx=152, cy=346, left=61, top=336, width=182, height=23),
        ],
    )

    result = locate_input(snapshot, label="目标输入框", expected_region="left")

    assert result.found
    assert result.candidate is not None
    assert result.candidate.source == "synthetic_near_label"
    assert result.candidate.center[1] > 320


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


def test_subgoal_file_dialog_submission_uses_enter_after_filename_input():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []

    class Session:
        task = "Save file to desktop"
        hwnd = 1
        run_id = "run-file-dialog-enter"
        snapshot = Snapshot(
            hwnd=1,
            title="Save As",
            region=Region(0, 0, 900, 500),
            elements=[
                UIElement(elem_id=1, source="ocr", role="text", text="文件名(N):", cx=90, cy=420, left=40, top=410, width=90, height=22),
                UIElement(elem_id=2, source="uia", role="edit", text="*.txt", cx=330, cy=420, left=150, top=405, width=360, height=32),
                UIElement(elem_id=3, source="uia", role="button", text="保存", cx=760, cy=460, left=720, top=445, width=80, height=32),
            ],
        )

        def dispatch(self, name, args):
            calls.append((name, args))
            if name == "read_text":
                return ToolResult("WinPilotE2E_note.txt")
            if name == "type_text":
                return ToolResult('{"ok": true, "verified": true}')
            if name == "hotkey":
                return ToolResult('{"ok": true, "verified": true}')
            return ToolResult('{"ok": true, "verified": false}')

    session = Session()
    result = SubgoalExecutor(session, FastGuiController(session)).execute({
        "subgoal": "Save As to desktop",
        "target_window": {"title": "Save As"},
        "input": {"label": "文件名", "text": "C:\\Users\\zou\\Desktop\\WinPilotE2E_note.txt", "clear": True},
        "click": {"label": "保存"},
        "verify_texts": ["WinPilotE2E_note"],
    })
    payload = json.loads(result.text)

    assert payload["ok"] is True
    assert [action["action"] for action in payload["actions_executed"]] == [
        "type_into_element",
        "hotkey",
        "wait_stable",
        "read_text",
    ]
    assert ("hotkey", {"keys": ["enter"], "expect": {"window_gone": "Save As", "timeout_s": 5.0}}) in calls


def test_subgoal_printable_click_sequence_prefers_keyboard_text():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request

    class Session:
        task = "Use keyboard to calculate result 408"

    executor = SubgoalExecutor(Session(), FastGuiController(Session()))
    request = parse_subgoal_request({
        "subgoal": "enter 12 multiply by 34 equals and read the result",
        "clicks": [
            {"label": "1"},
            {"label": "2"},
            {"label": "Multiply by"},
            {"label": "3"},
            {"label": "4"},
            {"label": "Equals"},
        ],
        "verify_texts": ["408"],
    }, task=Session.task)

    actions = executor._build_actions(request)

    assert actions[0] == {
        "action": "type_text",
        "text": "12*34=",
        "keyboard_sequence_from_clicks": True,
        "expect": {"text_appears": "408"},
    }
    assert [action["action"] for action in actions] == ["type_text", "wait_stable"]


def test_subgoal_printable_click_sequence_normalizes_chinese_operators():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request

    class Session:
        task = "计算 12 乘 34 等于 408"

    executor = SubgoalExecutor(Session(), FastGuiController(Session()))
    request = parse_subgoal_request({
        "subgoal": "计算 12 乘 34",
        "clicks": [
            {"label": "1"},
            {"label": "2"},
            {"label": "乘"},
            {"label": "3"},
            {"label": "4"},
            {"label": "等于"},
        ],
        "verify_texts": ["408"],
    }, task=Session.task)

    actions = executor._build_actions(request)

    assert actions[0]["action"] == "type_text"
    assert actions[0]["text"] == "12*34="
    assert actions[0]["keyboard_sequence_from_clicks"] is True


def test_subgoal_printable_click_sequence_maps_x_only_in_arithmetic_context():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request

    class Session:
        task = "calculate 12 x 34"

    executor = SubgoalExecutor(Session(), FastGuiController(Session()))
    math_request = parse_subgoal_request({
        "subgoal": "calculate 12 x 34",
        "clicks": [
            {"label": "1"}, {"label": "2"}, {"label": "x"},
            {"label": "3"}, {"label": "4"}, {"label": "="},
        ],
        "verify_texts": ["408"],
    }, task=Session.task)
    assert executor._build_actions(math_request)[0]["text"] == "12*34="

    plain_request = parse_subgoal_request({
        "subgoal": "enter code xabc",
        "clicks": [
            {"label": "x"}, {"label": "a"}, {"label": "b"}, {"label": "c"},
        ],
        "verify_texts": ["xabc"],
    }, task="enter code xabc")
    assert executor._build_actions(plain_request)[0]["text"] == "xabc"


def test_subgoal_executable_keyboard_input_reads_output_not_raw_echo():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request

    class Session:
        task = "Open a keyboard-first surface and compute 12 times 34."

    executor = SubgoalExecutor(Session(), FastGuiController(Session()))
    request = parse_subgoal_request({
        "subgoal": "compute 12 times 34",
        "input": {"label": "display", "text": "12*34=", "clear": False},
    }, task=Session.task)

    actions = executor._build_actions(request)

    assert request.verify_texts == []
    assert actions[0]["action"] == "type_into_element"
    assert "expect" not in actions[0]
    assert [action["action"] for action in actions] == [
        "type_into_element",
        "wait_stable",
        "read_text",
    ]


def test_subgoal_plain_button_sequence_stays_as_clicks():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request

    class Session:
        task = "Click visible choices A and B"

    executor = SubgoalExecutor(Session(), FastGuiController(Session()))
    request = parse_subgoal_request({
        "subgoal": "click A then B",
        "clicks": [{"label": "A"}, {"label": "B"}],
    }, task=Session.task)

    actions = executor._build_actions(request)

    assert [action["action"] for action in actions[:3]] == ["click_element", "wait", "click_element"]
    assert not any(action.get("keyboard_sequence_from_clicks") for action in actions)


def test_subgoal_input_locator_failure_falls_back_to_keyboard_text():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor
    from winpilot.perception.fusion import Snapshot
    from winpilot.utils.screenshot import Region

    calls = []

    class Session:
        task = "Enter 12*34= and verify 408"
        hwnd = 1
        run_id = "run-keyboard-fallback"
        snapshot = Snapshot(hwnd=1, title="Keyboard Target", region=Region(0, 0, 500, 400), elements=[])

        def dispatch(self, name, args):
            calls.append((name, args))
            if name == "type_text":
                return ToolResult('{"ok": true, "verified": true}')
            return ToolResult('{"ok": false, "blocked": true}')

    session = Session()
    result = SubgoalExecutor(session, FastGuiController(session)).execute({
        "subgoal": "calculate with keyboard input",
        "input": {"label": "main input", "text": "12*34="},
        "verify_texts": ["408"],
        "constraints": {"wait": False},
    })
    payload = json.loads(result.text)

    assert payload["ok"] is True
    assert payload["verified"] is True
    assert payload["metrics"]["fallback_from_type_into_element"] is True
    assert payload["actions_executed"][-1]["action"] == "type_text"
    assert payload["actions_executed"][-1]["keyboard_fallback_from_type_into_element"] is True
    assert calls[-1] == (
        "type_text",
        {"text": "12*34=", "keyboard_fallback_from_type_into_element": True,
         "expect": {"text_appears": "408", "timeout_s": 2.0}},
    )


def test_subgoal_target_hwnd_prefers_matching_foreground_over_stale_session(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast import subgoal as subgoal_mod

    class Session:
        hwnd = 100

        def dispatch(self, name, args):
            return ToolResult('{"ok": true}')

    monkeypatch.setattr(subgoal_mod.win32gui, "GetForegroundWindow", lambda: 200)
    monkeypatch.setattr(
        subgoal_mod.win32,
        "_window_text",
        lambda hwnd: "另存为" if int(hwnd) == 200 else "*无标题 - 记事本",
    )

    session = Session()
    executor = subgoal_mod.SubgoalExecutor(session, FastGuiController(session))

    assert executor._target_hwnd(
        subgoal_mod.parse_subgoal_request({"target_window": {"title": "另存为"}})
    ) == 200
    assert session.hwnd == 200


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


def test_subgoal_parser_infers_click_label_from_button_subgoal():
    from winpilot.fast.subgoal import parse_subgoal_request

    request = parse_subgoal_request({
        "subgoal": "检查局部计数是否为0，若不为0则点击重置按钮",
        "verify_texts": ["LOCAL_COUNTER_0"],
    })

    assert request.click["label"] == "重置"
    assert request.clicks[0]["label"] == "重置"
    assert request.clicks[0]["role"] == "button"


def test_subgoal_parser_expands_short_numeric_verify_to_task_literal():
    from winpilot.fast.subgoal import parse_subgoal_request

    request = parse_subgoal_request({
        "subgoal": "点击局部计数按钮四次",
        "click": {"label": "只改局部状态"},
        "verify_texts": ["4"],
    }, task="最后确认页面能看到 APPLIED_TEXT_1: DEMO、LOCAL_COUNTER_4。")

    assert request.verify_texts == ["LOCAL_COUNTER_4"]


def test_subgoal_parser_prefers_distinctive_verify_tokens_over_aliases():
    from winpilot.fast.subgoal import parse_subgoal_request

    request = parse_subgoal_request({
        "subgoal": "关闭弹窗",
        "click": {"label": "关闭"},
        "verify_texts": ["MODAL_CLOSED", "弹窗已关闭"],
    })
    assert request.verify_texts == ["MODAL_CLOSED"]

    final_request = parse_subgoal_request({
        "subgoal": "最终确认",
        "verify_texts": ["APPLIED_TEXT_1: DEMO", "LOCAL_COUNTER_4"],
    })
    assert final_request.verify_texts == ["APPLIED_TEXT_1: DEMO", "LOCAL_COUNTER_4"]


def test_subgoal_visible_text_ignores_observe_query_diagnostics():
    from winpilot.action.verify import _match_visible_text
    from winpilot.fast.subgoal import _pick_text

    visible_text = _pick_text({
        "results": [{
            "action": "observe",
            "preview": (
                'Changed-only observe query fallback: recent ROI did not contain '
                'query "local_counter_0"; returned a full observe snapshot.\n'
                "Perception reducer:\n"
                '- id=7 role=text text="LOCAL_COUNTER_20" center=(100,100)'
            ),
        }]
    })

    assert "local_counter_0" not in visible_text.casefold()
    assert _match_visible_text("LOCAL_COUNTER_20", visible_text)
    assert not _match_visible_text("LOCAL_COUNTER_0", visible_text)


def test_visible_text_tolerates_bounded_ocr_digit_letter_confusion():
    from winpilot.action.verify import _match_visible_text

    assert _match_visible_text("LOCAL_COUNTER_0", "IDLE\nLOCAL COUNTER O")
    assert _match_visible_text("BUILD_ID_101", "BUILD ID IOI")
    assert not _match_visible_text("LOCAL_COUNTER_0", "LOCAL_COUNTER_20")
    assert not _match_visible_text("10", "port 8100")


def test_subgoal_visible_text_uses_internal_full_observe_text():
    from winpilot.action.verify import _match_visible_text
    from winpilot.fast.subgoal import _pick_text

    visible_text = _pick_text({
        "results": [{
            "action": "observe",
            "preview": "Perception reducer:\n- id=1 text=\"only early candidates\"",
            "full_text": "Window: Target\n[9] text \"LOCAL COUNTER 4\"",
        }]
    })

    assert _match_visible_text("LOCAL_COUNTER_4", visible_text)


def test_subgoal_build_actions_honors_click_count_constraint():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request

    class Session:
        task = "最后确认 LOCAL_COUNTER_4"

    executor = SubgoalExecutor(Session(), FastGuiController(Session()))
    request = parse_subgoal_request({
        "subgoal": "点击局部计数按钮四次",
        "click": {"label": "只改局部状态", "expected_region": "left"},
        "verify_texts": ["4"],
        "constraints": {"click_count": 4},
    }, task=Session.task)

    actions = executor._build_actions(request)

    clicks = [action for action in actions if action.get("action") == "click_element"]
    waits = [action for action in actions if action.get("action") == "wait"]
    assert len(clicks) == 4
    assert [action["duration_ms"] for action in waits] == [140, 140, 140]
    assert {action["repeat_count"] for action in clicks} == {4}
    assert actions[-1]["query"] == "LOCAL_COUNTER_4"


def test_execute_subgoal_verifies_with_full_text_but_returns_public_results():
    import json

    from winpilot.fast.subgoal import SubgoalExecutor

    class Session:
        task = "confirm LOCAL_COUNTER_4"
        hwnd = None
        snapshot = None

    class Controller:
        def execute(self, actions, **kwargs):
            return {
                "ok": True,
                "verified": False,
                "controller_state": {"phase": "executed"},
                "results": [{
                    "index": 1,
                    "action": "observe",
                    "ok": True,
                    "preview": "Perception reducer:\n- id=1 text=\"early candidate\"",
                    "full_text": "Window: Target\n[4] text \"LOCAL COUNTER 4\"",
                }],
                "metrics": {},
            }

    executor = SubgoalExecutor(Session(), Controller())
    executor._ensure_observed = lambda request: (None, [], False)

    result = executor.execute({
        "subgoal": "verify local counter",
        "click": {"label": "只改局部状态"},
        "verify_texts": ["LOCAL_COUNTER_4"],
    })
    payload = json.loads(result.text)

    assert payload["ok"] is True
    assert payload["verified"] is True
    assert payload["controller_state"]["verified"] is True
    assert payload["evidence_packet"]["matched_texts"] == ["LOCAL_COUNTER_4"]
    assert "full_text" not in payload["results"][0]


def test_subgoal_parser_does_not_infer_extra_click_when_clicks_provided():
    from winpilot.fast.subgoal import parse_subgoal_request

    request = parse_subgoal_request({
        "subgoal": "点击只改局部状态按钮四次",
        "clicks": [
            {"label": "只改局部状态"},
            {"label": "只改局部状态"},
            {"label": "只改局部状态"},
            {"label": "只改局部状态"},
        ],
        "verify_texts": ["LOCAL_COUNTER_4"],
    })

    assert len(request.clicks) == 4


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


def test_subgoal_targeted_roi_no_label_match_retries_with_query_observe(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import subgoal as subgoal_mod
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []
    full_snapshot = None

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
            nonlocal full_snapshot
            calls.append((name, args))
            if name == "observe":
                if full_snapshot is None:
                    from winpilot.perception.fusion import Snapshot

                    full_snapshot = Snapshot(
                        hwnd=1,
                        title="Target",
                        region=Region(0, 0, 1000, 800),
                        elements=[
                            UIElement(elem_id=1, source="ocr", role="button", text="Local Counter", cx=210, cy=200, left=155, top=188, width=110, height=24),
                        ],
                    )
                self.snapshot = full_snapshot
                return ToolResult("LOCAL_COUNTER")
            if name == "click":
                assert args["element_id"] == 1
                return ToolResult('{"ok": true, "verified": false}')
            return ToolResult('{"ok": true}')

    session = Session()
    result = SubgoalExecutor(session, FastGuiController(session)).execute({
        "subgoal": "click Local Counter",
        "click": {"label": "Local Counter", "expected_region": "left"},
        "verify_texts": ["LOCAL_COUNTER"],
        "constraints": {"wait": False},
    })
    payload = json.loads(result.text)

    assert payload["ok"] is True
    assert payload["verified"] is True
    assert "skip_auto_observe" not in payload["actions_executed"][0]
    assert payload["evidence_packet"]["prelude"][1]["action"] == "targeted_observe"
    assert payload["evidence_packet"]["prelude"][1]["label_matched"] is False
    assert payload["evidence_packet"]["matched_texts"] == ["LOCAL_COUNTER"]
    assert [name for name, _args in calls] == ["focus_window", "observe", "click", "observe"]


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
        ("observe", {"query": "Local Counter", "max_elements": 160, "actionable_query": True}),
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


def test_execute_subgoal_draw_discards_stale_snapshot_for_different_target(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import subgoal as subgoal_mod
    from winpilot.fast.subgoal import SubgoalExecutor
    from winpilot.perception.fusion import Snapshot
    from winpilot.utils.screenshot import Region

    window = Region(10, 20, 500, 400)
    image = np.zeros((400, 500, 3), dtype=np.uint8)
    image[120:360, 80:460] = 255
    monkeypatch.setattr(subgoal_mod, "window_region", lambda hwnd: window)

    class Watcher:
        @staticmethod
        def status():
            return {}

    class Controller:
        watcher = Watcher()
        capture_fn = staticmethod(lambda region: image)

        def execute(self, actions, **kwargs):
            return {
                "ok": True,
                "verified": True,
                "controller_state": {"phase": "verified"},
                "metrics": {"action_count": len(actions)},
                "results": [
                    {"action": "select_color", "verified": True, "data": {"ok": True, "verified": True}},
                    {"action": "draw_strokes", "verified": True, "data": {"ok": True, "verified": True}},
                    {
                        "action": "auto_visual_verify",
                        "verified": True,
                        "data": {
                            "ok": True,
                            "verified": True,
                            "nonvisual_summary": {"visible_colors": {"blue": {"pixels": 100}}},
                        },
                    },
                ],
            }

    class Session:
        task = "draw a blue triangle on the canvas"
        hwnd = 7
        run_id = "run-draw-stale-snapshot"
        snapshot = Snapshot(hwnd=99, title="Codex", region=Region(0, 0, 800, 600), elements=[])

        def dispatch(self, name, args):
            assert name == "focus_window"
            return ToolResult('{"ok": true}')

    session = Session()
    result = SubgoalExecutor(session, Controller()).execute({
        "subgoal": "draw a blue triangle on the visible canvas",
        "draw": {"shape": "triangle", "color": "blue"},
    })
    payload = json.loads(result.text)

    assert payload["ok"] is True
    assert session.snapshot is None
    assert any(item["action"] == "discard_stale_snapshot" for item in payload["evidence_packet"]["prelude"])
    assert payload["evidence_packet"]["perception_reducer"] == {}


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


def test_execute_subgoal_draw_waits_for_ready_canvas(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import subgoal as subgoal_mod
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request
    from winpilot.utils.screenshot import Region

    window = Region(10, 20, 500, 400)
    dark = np.zeros((400, 500, 3), dtype=np.uint8)
    ready = np.zeros((400, 500, 3), dtype=np.uint8)
    ready[120:360, 80:460] = 255
    captures = []
    bbox_calls = []

    monkeypatch.setattr(subgoal_mod, "window_region", lambda hwnd: window)
    monkeypatch.setattr(subgoal_mod.time, "sleep", lambda _seconds: None)

    def fake_bbox(_image):
        bbox_calls.append(1)
        return None if len(bbox_calls) == 1 else (80, 120, 460, 360)

    monkeypatch.setattr(subgoal_mod.vision_cv, "likely_canvas_bbox", fake_bbox)

    class Watcher:
        @staticmethod
        def status():
            return {}

    class Controller:
        watcher = Watcher()

        @staticmethod
        def capture_fn(region):
            captures.append(region)
            return dark if len(captures) == 1 else ready

    class Session:
        task = "draw a blue triangle on the canvas"
        hwnd = 7
        run_id = "run-draw-ready"
        snapshot = None

        def dispatch(self, name, args):
            assert name == "focus_window"
            return ToolResult('{"ok": true}')

    executor = SubgoalExecutor(Session(), Controller())
    request = parse_subgoal_request({
        "subgoal": "draw a blue triangle",
        "draw": {"shape": "triangle", "color": "blue"},
    })
    action = executor._build_draw_action(request, request.draw)

    assert action is not None
    assert len(captures) >= 2
    assert action["canvas_region"] == {"x": 90, "y": 140, "width": 380, "height": 240}
    assert action["canvas_ready_attempts"][0]["looks_unready"] is True


def test_execute_subgoal_draw_without_target_hwnd_fails_fast(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import subgoal as subgoal_mod
    from winpilot.fast.subgoal import SubgoalExecutor

    monkeypatch.setattr(subgoal_mod, "window_region", lambda hwnd: (_ for _ in ()).throw(AssertionError("no hwnd expected")))

    class Watcher:
        @staticmethod
        def status():
            return {}

    class Controller:
        watcher = Watcher()

        @staticmethod
        def capture_fn(_region):
            raise AssertionError("should not capture foreground window without target hwnd")

        def execute(self, *_args, **_kwargs):
            raise AssertionError("no actions should execute")

    class Session:
        task = "open Paint and draw a blue triangle"
        hwnd = None
        run_id = "run-draw-no-target"
        snapshot = None

        def dispatch(self, name, args):
            raise AssertionError(f"unexpected dispatch {name} {args}")

    result = SubgoalExecutor(Session(), Controller()).execute({
        "subgoal": "draw a blue triangle before the app is open",
        "draw": {"shape": "triangle", "color": "blue"},
    })
    payload = json.loads(result.text)

    assert payload["ok"] is False
    assert payload["needs_llm_reason"] is True
    assert payload["needs_target_window"] is True
    assert payload["controller_state"]["needs_target_window"] is True
    assert "ready canvas" in payload["error"]
    assert "launch/find_app/focus_window" in payload["fast_path_advice"]


def test_execute_subgoal_verify_only_without_target_hwnd_fails_fast():
    from winpilot.fast.subgoal import SubgoalExecutor

    class Watcher:
        @staticmethod
        def status():
            return {}

    class Controller:
        watcher = Watcher()

        def execute(self, *_args, **_kwargs):
            raise AssertionError("verify-only routine without target hwnd should not read foreground")

    class Session:
        task = "open Paint and verify it"
        hwnd = None
        run_id = "run-verify-no-target"
        snapshot = None

        def dispatch(self, name, args):
            raise AssertionError(f"unexpected dispatch {name} {args}")

    result = SubgoalExecutor(Session(), Controller()).execute({
        "subgoal": "Open Windows Paint and verify a blank canvas is ready",
        "verify_texts": ["Paint"],
    })
    payload = json.loads(result.text)

    assert payload["ok"] is False
    assert payload["needs_target_window"] is True
    assert "verify-only" in payload["error"]
    assert "launch/find_app/focus_window" in payload["fast_path_advice"]


def test_execute_subgoal_builds_multi_draws_with_canvas_regions(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import subgoal as subgoal_mod
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request
    from winpilot.utils.screenshot import Region

    window = Region(10, 20, 500, 400)
    image = np.zeros((400, 500, 3), dtype=np.uint8)
    image[120:360, 80:460] = 255

    monkeypatch.setattr(subgoal_mod, "window_region", lambda hwnd: window)

    class Watcher:
        @staticmethod
        def status():
            return {}

    class Controller:
        watcher = Watcher()
        capture_fn = staticmethod(lambda region: image)

    class Session:
        task = "draw a blue triangle and red rectangle on the canvas"
        hwnd = 7
        run_id = "run-draws"
        snapshot = None

        def dispatch(self, name, args):
            assert name == "focus_window"
            return ToolResult('{"ok": true}')

    executor = SubgoalExecutor(Session(), Controller())
    request = parse_subgoal_request({
        "subgoal": "draw two colored shapes",
        "draws": [
            {"shape": "triangle", "color": "blue", "expected_region": "center-left"},
            {"shape": "rectangle", "color": "red", "expected_region": "center-right"},
        ],
        "constraints": {"wait": False},
    })
    actions = executor._build_actions(request)

    assert [item["action"] for item in actions] == [
        "select_color",
        "draw_strokes",
        "select_color",
        "draw_strokes",
    ]
    assert actions[0]["color"] == "blue"
    assert actions[2]["color"] == "red"
    blue_draw = actions[1]
    red_draw = actions[3]
    assert blue_draw["shape"] == "triangle"
    assert red_draw["shape"] == "rectangle"
    assert blue_draw["draw_region"]["x"] < red_draw["draw_region"]["x"]
    blue_xs = [point[0] for stroke in blue_draw["strokes"] for point in stroke]
    red_xs = [point[0] for stroke in red_draw["strokes"] for point in stroke]
    assert max(blue_xs) < max(red_xs)


def test_agent_tool_message_compacts_execute_subgoal_result():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a blue triangle")
    raw = json.dumps({
        "ok": True,
        "verified": True,
        "subgoal": "draw",
        "subgoal_route": "controller_first",
        "controller_state": {"phase": "verified"},
        "actions_executed": [
            {
                "action": "draw_strokes",
                "requested_color": "blue",
                "shape": "triangle",
                "strokes": [[[1, 2], [3, 4], [5, 6], [1, 2]]] * 80,
                "canvas_region": {"x": 10, "y": 20, "width": 300, "height": 200},
            }
        ],
        "evidence_packet": {
            "matched_visual_colors": ["blue"],
            "missing_visual_colors": [],
            "visible_colors": {"blue": {"pixels": 123, "ratio": 0.02}},
        },
    }, ensure_ascii=False)

    runner._append_tool_result("call-1", raw, tool_name="execute_subgoal")
    content = runner.messages[-1]["content"]

    assert len(content) < 2000
    assert '"matched_visual_colors": ["blue"]' in content
    assert '"stroke_count": 80' in content
    assert "[[1, 2], [3, 4]" not in content


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


def test_select_color_reuses_cached_palette_button(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import controller as controller_mod
    from winpilot.fast.controller import FastGuiController
    from winpilot.utils.screenshot import Region

    target_region = Region(100, 200, 300, 220)
    monkeypatch.setattr(controller_mod, "window_region", lambda hwnd: target_region)
    closed_image = np.full((220, 300, 3), 96, dtype=np.uint8)
    open_image = closed_image.copy()
    for x, color in [
        (20, (0, 0, 255)),
        (50, (0, 128, 255)),
        (80, (0, 255, 255)),
        (110, (0, 180, 0)),
        (140, (255, 0, 0)),
        (170, (180, 0, 180)),
        (200, (0, 0, 255)),
        (230, (0, 255, 255)),
        (20, (0, 180, 0)),
        (50, (255, 0, 0)),
    ]:
        open_image[110:128, x:x + 18] = color

    ocr_calls = {"count": 0}

    def collect(_region):
        ocr_calls["count"] += 1
        return [SimpleNamespace(text="Colors", cx=250, cy=280)]

    monkeypatch.setattr(controller_mod.ocr, "collect", collect)
    palette_open = {"value": False}
    clicks = []

    class Session:
        task = "draw a blue triangle and a red rectangle"
        hwnd = 7
        run_id = "run-select-color-cache"
        snapshot = None

        def dispatch(self, name, args):
            assert name == "click"
            clicks.append(args)
            if int(args["y"]) < 280:
                palette_open["value"] = True
            else:
                palette_open["value"] = False
            return ToolResult('{"ok": true}')

    def capture_fn(_region):
        return open_image if palette_open["value"] else closed_image

    controller = FastGuiController(Session(), capture_fn=capture_fn)
    out = controller.execute([
        {"action": "select_color", "color": "blue"},
        {"action": "select_color", "color": "red"},
    ], sync=False)

    assert out["ok"] is True
    assert ocr_calls["count"] == 1
    assert len(clicks) == 4
    assert clicks[0] == clicks[2]  # cached palette opener reused for red
    assert all(click.get("skip_screen_delta") is True for click in clicks)


def test_desktop_icon_drag_adjusts_drop_away_from_existing_icon():
    from winpilot.agent.tools import ToolSession
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    session = ToolSession(
        task=(
            "\u628a\u684c\u9762\u4e0a\u7684 WinPilotPerfDrag_Eval "
            "\u6587\u4ef6\u5939\u56fe\u6807\u62d6\u52a8\u5230\u684c\u9762\u53f3\u4e0a\u89d2\u9644\u8fd1"
        )
    )
    session.snapshot = Snapshot(
        hwnd=1,
        title="Program Manager",
        region=Region(0, 0, 1920, 1080),
        elements=[
            UIElement(
                elem_id=1,
                source="uia",
                role="listitem",
                text="WinPilotPerfDrag_Eval",
                cx=1757,
                cy=144,
                left=1710,
                top=111,
                width=94,
                height=67,
            ),
            UIElement(
                elem_id=2,
                source="uia",
                role="listitem",
                text="\u671f\u672b\u590d\u4e60",
                cx=1850,
                cy=50,
                left=1805,
                top=5,
                width=94,
                height=67,
            ),
        ],
    )

    out = session._desktop_icon_safe_drop_target(1757, 144, 1850, 50)

    assert out is not None
    assert out["adjusted"] is True
    assert (out["x"], out["y"]) != (1850, 50)
    assert not (1793 <= out["x"] <= 1911 and -7 <= out["y"] <= 84)
