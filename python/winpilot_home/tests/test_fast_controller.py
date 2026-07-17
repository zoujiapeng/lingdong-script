import json
from types import SimpleNamespace

import numpy as np


def test_batch_observe_actions_are_allowed_or_internal():
    from winpilot.fast.controller import ALLOWED_BATCH_ACTIONS, BATCH_OBSERVE_ACTIONS

    assert BATCH_OBSERVE_ACTIONS <= (ALLOWED_BATCH_ACTIONS | {"auto_visual_verify"})
    assert "vlm_describe" not in BATCH_OBSERVE_ACTIONS


def test_batch_contract_exposes_real_continuous_input_primitives():
    from winpilot.fast.action_contract import (
        ALLOWED_BATCH_ACTIONS,
        BATCH_MUTATING_ACTIONS,
        validate_batch_actions,
    )

    assert {"mouse_down", "mouse_up", "hold_key"} <= ALLOWED_BATCH_ACTIONS
    assert {"mouse_down", "mouse_up", "hold_key"} <= BATCH_MUTATING_ACTIONS
    assert validate_batch_actions([
        {"action": "move_mouse", "x": 100, "y": 120},
        {"action": "mouse_down", "button": "left"},
        {"action": "move_mouse", "x": 220, "y": 180},
        {"action": "mouse_up", "button": "left"},
        {"action": "hold_key", "keys": ["shift"], "duration_ms": 250},
    ]) == []


def test_batch_contract_rejects_unbalanced_or_unbounded_continuous_input():
    from winpilot.fast.action_contract import validate_batch_actions

    assert any("no matching mouse_up" in item for item in validate_batch_actions([
        {"action": "mouse_down", "button": "left", "x": 10, "y": 20},
        {"action": "move_mouse", "x": 30, "y": 40},
    ]))
    assert any("releases right" in item for item in validate_batch_actions([
        {"action": "mouse_down", "button": "left", "x": 10, "y": 20},
        {"action": "mouse_up", "button": "right", "x": 30, "y": 40},
    ]))
    assert any("duration_ms in 1..30000" in item for item in validate_batch_actions([
        {"action": "hold_key", "keys": ["shift"], "duration_ms": 30_001},
    ]))


def test_controller_releases_held_pointer_when_transaction_aborts(monkeypatch):
    from winpilot.agent import scope_guard
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import controller as controller_mod
    from winpilot.fast.controller import FastGuiController

    monkeypatch.setattr(scope_guard, "ensure_scope", lambda *_args, **_kwargs: None)
    cleanups = iter(([], ["left"]))
    monkeypatch.setattr(
        controller_mod.action_executor,
        "release_held_mouse_buttons",
        lambda: next(cleanups),
    )

    class Session:
        hwnd = 17
        run_id = "aborted-continuous-input"
        snapshot = None
        _screen_epoch = 1

        def dispatch(self, name, _args):
            if name == "move_mouse":
                return ToolResult('{"ok": false, "error": "target moved"}')
            return ToolResult('{"ok": true}')

    result = FastGuiController(Session()).execute([
        {"action": "mouse_down", "button": "left", "x": 100, "y": 120},
        {"action": "move_mouse", "x": 220, "y": 180},
        {"action": "mouse_up", "button": "left", "x": 220, "y": 180},
    ], sync=False)

    assert result["ok"] is False
    assert result["failure_type"] == "CONTINUOUS_INPUT_ABORTED"
    assert result["input_cleanup"]["released_mouse_buttons"] == ["left"]


def test_controller_executes_continuous_input_sequence_in_one_batch(monkeypatch):
    from winpilot.agent import scope_guard
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController

    monkeypatch.setattr(scope_guard, "ensure_scope", lambda *_args, **_kwargs: None)
    calls = []

    class Session:
        hwnd = 17
        run_id = "continuous-input-batch"
        snapshot = None
        _screen_epoch = 1

        def dispatch(self, name, args):
            calls.append((name, dict(args)))
            return ToolResult('{"ok": true}')

    result = FastGuiController(Session()).execute([
        {"action": "move_mouse", "x": 100, "y": 120},
        {"action": "mouse_down", "button": "left"},
        {"action": "move_mouse", "x": 220, "y": 180},
        {"action": "mouse_up", "button": "left"},
        {"action": "hold_key", "keys": ["shift"], "duration_ms": 250},
    ], sync=False)

    assert result["ok"] is True
    assert [name for name, _args in calls] == [
        "move_mouse", "mouse_down", "move_mouse", "mouse_up", "hold_key",
    ]


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


def test_element_cache_primary_exact_label_beats_exact_fallback():
    from winpilot.fast.element_cache import ElementCache
    from winpilot.fast.types import VisualAnchor
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    snapshot = Snapshot(
        hwnd=1,
        title="Numeric Tool",
        region=Region(0, 0, 500, 700),
        elements=[
            UIElement(elem_id=1, source="uia", role="button", text="Clear entry", cx=190, cy=300),
            UIElement(elem_id=2, source="uia", role="button", text="Clear", cx=310, cy=300),
        ],
    )

    located = ElementCache().locate_with_trace(
        snapshot,
        VisualAnchor(
            label="Clear",
            fallback_text=("C", "CE", "Clear entry"),
            expected_region="center",
            role="button",
        ),
    )

    assert located.element is not None
    assert located.element.elem_id == 2


def test_batch_mutation_invalidates_pre_action_observe_cache_before_read_text():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController

    observed = []

    class Session:
        hwnd = 1
        run_id = "cache-invalidation"
        _screen_epoch = 7
        _observe_cache = {"key": "stale"}
        snapshot = None

    session = Session()
    controller = FastGuiController(session)

    def dispatch(name, args):
        if name == "hotkey":
            return ToolResult('{"ok": true}')
        if name == "read_text":
            observed.append((session._screen_epoch, session._observe_cache))
            return ToolResult("fresh state")
        raise AssertionError(name)

    controller._dispatch = dispatch
    payload = controller.execute([
        {"action": "hotkey", "keys": ["enter"]},
        {"action": "read_text"},
    ])

    assert payload["ok"] is True
    assert observed == [(8, None)]


def test_element_cache_row_header_rejects_name_box_substring():
    from winpilot.fast.element_cache import ElementCache
    from winpilot.fast.types import VisualAnchor
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    snapshot = Snapshot(
        hwnd=1,
        title="Workbook1",
        region=Region(0, 0, 1920, 1020),
        elements=[
            UIElement(elem_id=1, source="uia", role="edit", text="A1", cx=105, cy=209),
            UIElement(elem_id=2, source="ocr", role="text", text="1", cx=22, cy=274),
        ],
    )

    located = ElementCache().locate_with_trace(
        snapshot,
        VisualAnchor(label="1", expected_region="left", role="row_header"),
    )

    assert located.element is not None
    assert located.element.elem_id == 2


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


def test_element_cache_prefers_exact_native_menu_item_over_merged_ocr_text():
    from winpilot.fast.element_cache import ElementCache
    from winpilot.fast.types import VisualAnchor
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    snapshot = Snapshot(
        hwnd=1,
        title="Editor",
        region=Region(0, 0, 1000, 600),
        elements=[
            UIElement(elem_id=-1, source="ocr", role="text", text="文件(F)编辑(E)格式(O)", cx=102, cy=42, left=8, top=34, width=188, height=17),
            UIElement(elem_id=21, source="uia", role="menuitem", text="文件(F)", cx=33, cy=41, left=0, top=29, width=66, height=24),
        ],
    )

    result = ElementCache().locate_with_trace(
        snapshot,
        VisualAnchor(label="文件(F)", role="button"),
    )

    assert result.element is not None
    assert result.element.elem_id == 21


def test_element_cache_semantic_left_list_avoids_bottom_navigation_duplicate():
    from winpilot.fast.element_cache import ElementCache
    from winpilot.fast.types import VisualAnchor
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    snapshot = Snapshot(
        hwnd=1,
        title="Messaging app",
        region=Region(0, 0, 1920, 1020),
        elements=[
            UIElement(elem_id=1, source="uia", role="button", text="My Phone", cx=37, cy=937, left=17, top=917, width=41, height=41),
            UIElement(elem_id=2, source="uia", role="text", text="My Phone", cx=180, cy=142, left=150, top=132, width=60, height=20),
        ],
    )

    result = ElementCache().locate_with_trace(
        snapshot,
        VisualAnchor(label="My Phone", expected_region="conversation-list"),
    )

    assert result.element is not None
    assert result.element.elem_id == 2


def test_subgoal_infers_left_list_region_without_forcing_button_role():
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request

    request = parse_subgoal_request({
        "subgoal": 'click "My Phone" in the conversation list',
        "click": {"label": "My Phone"},
    })

    actions = SubgoalExecutor(SimpleNamespace(), None)._build_actions(request)

    assert actions[0]["action"] == "click_element"
    assert actions[0]["expected_region"] == "left-list"
    assert "role" not in actions[0]


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


def test_fast_controller_ocr_anchor_uses_physical_screen_coordinates():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []

    class Session:
        snapshot = Snapshot(
            hwnd=1,
            title="Editor",
            region=Region(0, 0, 1000, 600),
            elements=[UIElement(
                elem_id=-1,
                source="ocr",
                role="text",
                text="File",
                cx=38,
                cy=43,
                left=7,
                top=33,
                width=63,
                height=20,
            )],
        )

        def dispatch(self, name, args):
            calls.append((name, args))
            return ToolResult('{"ok": true, "verified": true}')

    out = FastGuiController(Session()).execute([
        {"action": "click_element", "label": "File", "role": "button"},
    ])

    assert out["ok"] is True
    assert calls == [("click", {
        "x": 38,
        "y": 43,
        "button": "left",
        "double": False,
        "physical": True,
    })]


def test_fast_controller_clicks_label_subregion_inside_merged_ocr_text():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []

    class Session:
        snapshot = Snapshot(
            hwnd=1,
            title="Owner-drawn editor",
            region=Region(0, 0, 1000, 600),
            elements=[UIElement(
                elem_id=7,
                source="ocr",
                role="text",
                text="文件(F)编辑(E)格式(O)",
                cx=102,
                cy=42,
                left=8,
                top=34,
                width=188,
                height=17,
            )],
        )

        def dispatch(self, name, args):
            calls.append((name, args))
            return ToolResult('{"ok": true, "verified": true}')

    out = FastGuiController(Session()).execute([
        {"action": "click_element", "label": "文件(F)", "role": "button"},
    ])

    assert out["ok"] is True
    assert calls[0][0] == "click"
    assert 28 <= calls[0][1]["x"] <= 48
    assert calls[0][1]["y"] == 42
    assert calls[0][1]["physical"] is True
    assert out["results"][0]["data"]["text_subregion_click"]["matched_text"] == "文件(F)"


def test_fast_controller_disambiguates_dialog_button_from_titlebar_button():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []

    class Session:
        snapshot = Snapshot(
            hwnd=1,
            title="Application",
            region=Region(0, 0, 1920, 1020),
            elements=[
                UIElement(elem_id=1, role="button", text="Close", cx=1892, cy=25),
                UIElement(elem_id=2, role="button", text="Close", cx=1885, cy=140),
                UIElement(elem_id=3, role="button", text="Close", cx=1720, cy=900),
                UIElement(elem_id=4, role="button", text="Close this view", cx=1919, cy=124),
            ],
        )

        def dispatch(self, name, args):
            calls.append((name, args))
            return ToolResult('{"ok": true, "verified": true}')

    out = FastGuiController(Session()).execute([{
        "action": "click_element",
        "label": "Close",
        "role": "button",
        "expected_region": "top/right",
        "context": "Close the blocking popup dialog",
    }])

    assert out["ok"]
    assert calls == [("click", {"element_id": 2, "button": "left", "double": False})]
    assert out["results"][0]["data"]["anchor_locator"]["reason"] == "dialog_context_disambiguated"


def test_cv_detects_rectangular_control_candidates_from_image():
    import cv2

    from winpilot.perception.vision_cv import _detect_control_candidates_from_image
    from winpilot.utils.screenshot import Region

    image = np.full((220, 420, 3), 28, dtype=np.uint8)
    for top in (40, 105):
        for left in (20, 125, 230):
            cv2.rectangle(image, (left, top), (left + 90, top + 48), (58, 58, 58), -1)
            cv2.rectangle(image, (left, top), (left + 90, top + 48), (110, 110, 110), 1)

    controls = _detect_control_candidates_from_image(
        image,
        Region(100, 200, image.shape[1], image.shape[0]),
    )

    assert len(controls) >= 6
    assert all(control.source == "cv-control" for control in controls)
    assert any(control.cx == 165 and control.cy == 264 for control in controls)


def test_control_glyph_geometry_recognizes_universal_line_symbols():
    import cv2

    from winpilot.perception.ocr import _control_glyph_crop

    equals = np.full((64, 116, 3), 50, dtype=np.uint8)
    cv2.line(equals, (49, 28), (67, 28), (235, 235, 235), 2)
    cv2.line(equals, (49, 35), (67, 35), (235, 235, 235), 2)
    plus = np.full((64, 116, 3), 50, dtype=np.uint8)
    cv2.line(plus, (50, 32), (66, 32), (235, 235, 235), 2)
    cv2.line(plus, (58, 24), (58, 40), (235, 235, 235), 2)

    _equals_crop, equals_label = _control_glyph_crop(equals)
    _plus_crop, plus_label = _control_glyph_crop(plus)

    assert equals_label == "="
    assert plus_label == "+"


def test_fast_controller_uses_targeted_short_control_ocr(monkeypatch):
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
            title="Keypad",
            region=Region(0, 0, 500, 700),
            elements=[UIElement(elem_id=1, source="ocr", role="text", text="CE", cx=100, cy=100)],
        )

        def dispatch(self, name, args):
            calls.append((name, args))
            return ToolResult('{"ok": true, "verified": true}')

    monkeypatch.setattr(
        "winpilot.fast.controller.ocr.locate_control_text",
        lambda _region, terms: [
            UIElement(
                source="ocr-control",
                role="button",
                text=terms[0],
                cx=300,
                cy=500,
                left=250,
                top=470,
                width=100,
                height=60,
                confidence=0.8,
            )
        ],
    )

    out = FastGuiController(Session()).execute([{
        "action": "click_element",
        "label": "6",
        "role": "button",
        "expected_region": "center",
    }])

    assert out["ok"] is True
    assert calls[0][0] == "observe"
    assert calls[1] == (
        "click",
        {"x": 300, "y": 500, "button": "left", "double": False, "physical": True},
    )
    data = out["results"][0]["data"]
    assert data["anchor_locator"]["reason"] == "targeted_control_ocr"
    assert data["targeted_control_ocr"]["match_count"] == 1


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
    assert calls[0] == ("click", {"x": 270, "y": 80, "button": "left", "double": False})
    assert calls[1:] == [
        ("hotkey", {"keys": ["ctrl", "a"]}),
        ("hotkey", {"keys": ["backspace"]}),
        ("type_text", {"text": "hello", "expect": expected_expect}),
    ]
    data = out["results"][0]["data"]
    assert data["via"] == "type_into_element"
    assert data["controller_internal_action_count"] == 4
    assert data["resolved_element"]["elem_id"] is None
    assert data["resolved_element"]["source"] == "synthetic_near_label"
    assert data["subresults"][0]["action"] == "click_element"
    assert data["subresults"][3]["action"] == "type_text"
    assert out["metrics"]["internal_action_count"] == 4
    assert out["metrics"]["compound_round_trips_saved"] == 3
    assert out["metrics"]["controller_round_trips_saved"] == 3
    assert out["controller_state"]["phase"] == "verified"


def test_type_into_element_defers_postcondition_without_retyping_literal():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []

    class Session:
        snapshot = Snapshot(
            hwnd=1,
            title="Document",
            region=Region(0, 0, 1000, 600),
            elements=[UIElement(
                elem_id=4,
                source="uia",
                role="edit",
                text="",
                cx=500,
                cy=300,
                left=100,
                top=100,
                width=800,
                height=450,
            )],
        )

        def dispatch(self, name, args):
            calls.append((name, args))
            if name == "type_text":
                return ToolResult('{"ok": true, "typed": 5, "verified": false}')
            if name == "read_text":
                return ToolResult("hello")
            return ToolResult('{"ok": true, "verified": false}')

    out = FastGuiController(Session()).execute([
        {
            "action": "type_into_element",
            "text": "hello",
            "clear": True,
            "expect": {"text_appears": "hello"},
        },
        {"action": "read_text"},
    ])

    assert out["ok"] is True
    assert [item["action"] for item in out["results"]] == [
        "type_into_element",
        "read_text",
    ]
    typed = out["results"][0]
    assert typed["ok"] is True
    assert typed["verified"] is False
    assert typed["data"]["mutation_applied"] is True
    assert typed["data"]["postcondition_pending"] is True
    assert [name for name, _args in calls].count("type_text") == 1
    assert calls[-1] == ("read_text", {})


def test_stable_batch_actions_anchor_first_line_formatting_and_split_key_sequence():
    from winpilot.fast.controller import _stable_batch_actions

    actions = _stable_batch_actions(
        [
            {"action": "click", "x": 500, "y": 300},
            {"action": "hotkey", "keys": ["Home"]},
            {"action": "hotkey", "keys": ["Shift", "End"]},
            {"action": "hotkey", "keys": ["Ctrl", "B"]},
            {"action": "hotkey", "keys": ["Ctrl", "E"]},
            {"action": "hotkey", "keys": ["Down", "Home"]},
            {"action": "hotkey", "keys": ["Shift", "Ctrl", "End"]},
            {"action": "hotkey", "keys": ["Ctrl", "L"]},
        ],
        "将第一行标题加粗并居中，其余正文左对齐",
    )

    assert actions[1] == {"action": "hotkey", "keys": ["Ctrl", "Home"]}
    assert actions[5:7] == [
        {"action": "hotkey", "keys": ["Down"]},
        {"action": "hotkey", "keys": ["Home"]},
    ]


def test_stable_batch_actions_flattens_model_params_keys():
    from winpilot.fast.controller import _stable_batch_actions

    actions = _stable_batch_actions(
        [
            {"action": "hotkey", "params": {"keys": ["home"]}},
            {"action": "hotkey", "params": {"keys": ["shift", "end"]}},
            {"action": "hotkey", "params": {"keys": ["ctrl", "b"]}},
            {"action": "hotkey", "params": {"keys": ["ctrl", "e"]}},
            {"action": "hotkey", "params": {"keys": ["down", "home"]}},
        ],
        "Select first line title, bold it and center it",
    )

    assert actions[0] == {"action": "hotkey", "keys": ["Ctrl", "Home"]}
    assert actions[-2:] == [
        {"action": "hotkey", "keys": ["down"]},
        {"action": "hotkey", "keys": ["home"]},
    ]


def test_fast_controller_wait_accepts_time_seconds_alias(monkeypatch):
    from winpilot.fast import controller as controller_mod
    from winpilot.fast.controller import FastGuiController

    waits = []
    monkeypatch.setattr(controller_mod.time, "sleep", lambda seconds: waits.append(seconds))

    class Session:
        snapshot = None
        hwnd = None

    out = FastGuiController(Session()).execute([
        {"action": "wait", "time": 1.0},
    ])

    assert out["ok"] is True
    assert waits == [1.0]
    assert out["results"][0]["data"]["waited_ms"] == 1000


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
    assert any(
        marker in data["input_locator"]["reason"]
        for marker in ("near concrete label", "filename value pattern")
    )


def test_input_locator_without_label_avoids_compact_file_item_property_edit():
    from winpilot.fast.input_locator import locate_input
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    snapshot = Snapshot(
        hwnd=1,
        title="Save As",
        region=Region(0, 0, 1920, 1020),
        elements=[
            UIElement(elem_id=1, source="uia", role="edit", text="名称=.reasonix", cx=292, cy=283, left=258, top=273, width=69, height=21),
            UIElement(elem_id=2, source="uia", role="edit", text="文件名:=*.txt", cx=1025, cy=836, left=169, top=825, width=1712, height=22),
        ],
    )

    result = locate_input(snapshot)

    assert result.found is True
    assert result.candidate is not None
    assert result.candidate.element is not None
    assert result.candidate.element.elem_id == 2
    assert "wide single-line input geometry" in result.candidate.reason


def test_input_locator_maps_filename_label_to_wildcard_value_field():
    from winpilot.fast.input_locator import locate_input
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    snapshot = Snapshot(
        hwnd=1,
        title="Save As",
        region=Region(9, 9, 1902, 1002),
        elements=[
            UIElement(elem_id=86, source="win32", role="combobox", text="*.txt", cx=1035, cy=836, left=166, top=822, width=1739, height=28),
            UIElement(elem_id=88, source="win32", role="combobox", text="文本文档(*.txt)", cx=1035, cy=867, left=166, top=853, width=1739, height=28),
        ],
    )

    result = locate_input(snapshot, label="文件名")

    assert result.found is True
    assert result.candidate is not None
    assert result.candidate.element is not None
    assert result.candidate.element.elem_id == 86
    assert "filename value pattern" in result.candidate.reason


def test_type_into_element_honors_explicit_current_snapshot_element_id():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    target = "WinPilot_Office_GUI_20260712.txt"
    calls = []

    class Session:
        hwnd = 1
        task = "save a file"
        snapshot = Snapshot(
            hwnd=1,
            title="Save As",
            region=Region(0, 0, 1920, 1020),
            elements=[
                UIElement(elem_id=1, source="uia", role="edit", text="名称=.reasonix", cx=292, cy=283, left=258, top=273, width=69, height=21),
                UIElement(elem_id=2, source="uia", role="edit", text="文件名:=*.txt", cx=1025, cy=836, left=169, top=825, width=1712, height=22),
            ],
        )

        def dispatch(self, name, args):
            calls.append((name, args))
            if name == "read_text":
                return ToolResult(f"文件名:={target}")
            return ToolResult('{"ok": true, "verified": false}')

    out = FastGuiController(Session()).execute([{
        "action": "type_into_element",
        "element_id": 2,
        "text": target,
    }])

    assert out["ok"] is True
    assert calls[:4] == [
        ("click", {"element_id": 2, "button": "left", "double": False}),
        ("hotkey", {"keys": ["ctrl", "a"]}),
        ("hotkey", {"keys": ["backspace"]}),
        ("type_text", {"text": target}),
    ]
    data = out["results"][0]["data"]
    assert data["input_locator"]["reason"] == "explicit_current_snapshot_element_id"
    assert data["resolved_element"]["elem_id"] == 2


def test_type_into_element_refuses_compact_property_editor_element_id():
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    class Session:
        hwnd = 1
        task = "save a file"
        snapshot = Snapshot(
            hwnd=1,
            title="Save As",
            region=Region(0, 0, 1920, 1020),
            elements=[UIElement(
                elem_id=1, source="uia", role="edit", text="名称=.reasonix",
                cx=292, cy=283, left=258, top=273, width=69, height=21,
            )],
        )

        def dispatch(self, name, args):
            raise AssertionError(f"unsafe property editor must not dispatch {name}")

    out = FastGuiController(Session()).execute([{
        "action": "type_into_element",
        "element_id": 1,
        "text": "result.txt",
    }])

    assert out["ok"] is False
    assert out["results"][0]["data"]["blocked"] is True
    assert "not a safe input field" in out["results"][0]["data"]["error"]


def test_type_into_element_uses_targeted_input_value_when_full_read_is_truncated(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import controller as controller_mod
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    path = r"C:\Users\zou\Desktop\claude\output\paint-drawing-saved.png"

    class Session:
        hwnd = 7
        snapshot = Snapshot(
            hwnd=7,
            title="Save As",
            region=Region(0, 0, 1000, 600),
            elements=[
                UIElement(
                    elem_id=3, source="uia", role="text", text="File name:",
                    cx=100, cy=520, left=40, top=508, width=120, height=24,
                ),
                UIElement(
                    elem_id=4, source="uia", role="edit", text="Untitled.png",
                    cx=540, cy=520, left=150, top=508, width=780, height=24,
                ),
            ],
        )

        def dispatch(self, name, args):
            if name == "read_text":
                return ToolResult("top of a very long dialog; input field omitted")
            if name == "type_text":
                return ToolResult('{"ok": true, "via": "uia_set_value"}')
            return ToolResult('{"ok": true, "verified": false}')

    monkeypatch.setattr(
        controller_mod.uia,
        "collect",
        lambda hwnd: [
            UIElement(
                elem_id=-1,
                source="uia",
                role="edit",
                text=f"File name:={path}",
                cx=540,
                cy=520,
                left=150,
                top=508,
                width=780,
                height=24,
            ),
        ],
    )

    out = FastGuiController(Session()).execute([{
        "type_into_element": {
            "label": "File name",
            "role": "edit",
            "text": path,
            "clear": True,
        },
    }])

    assert out["ok"] is True
    data = out["results"][0]["data"]
    targeted = next(
        item for item in data["subresults"]
        if item["action"] == "targeted_input_read"
    )
    assert targeted["ok"] is True
    assert targeted["data"]["methods"] == ["uia-input-bbox"]


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
    assert calls == [("observe", {"full": True, "force_full": True})]


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
    assert calls[0] == ("click", {"x": 270, "y": 80, "button": "left", "double": False})
    assert calls[1:] == [
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


def test_fast_controller_fails_a_draw_action_when_pixels_do_not_change():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController

    class Session:
        snapshot = None

        def dispatch(self, name, _args):
            if name == "draw_strokes":
                return ToolResult(
                    '{"ok": true, "verified": false, '
                    '"visual_change_detected": false, "visual_change_ratio": 0.0}'
                )
            return ToolResult('{"ok": true}')

    out = FastGuiController(Session()).execute([
        {"draw_strokes": {"strokes": [[[100, 100], [150, 150]]]}},
    ], sync=False)

    assert out["ok"] is False
    assert out["controller_state"]["phase"] == "blocked"
    assert out["metrics"]["failed_at"] == 1
    assert out["results"][0]["error"] == "visual action produced no pixel change in its intended region"
    assert out["results"][0]["data"]["failure_type"] == "DRAW_NO_VISIBLE_CHANGE"
    assert out["failure_type"] == "DRAW_NO_VISIBLE_CHANGE"
    assert out["error"] == "visual action produced no pixel change in its intended region"


def test_fast_controller_classifies_no_effect_drag_as_generic_visual_failure():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController

    class Session:
        snapshot = None

        def dispatch(self, name, _args):
            if name == "drag":
                return ToolResult(
                    '{"ok": true, "verified": false, '
                    '"visual_change_detected": false, "visual_change_ratio": 0.0}'
                )
            return ToolResult('{"ok": true}')

    out = FastGuiController(Session()).execute([
        {"drag": {"x1": 100, "y1": 100, "x2": 200, "y2": 200}},
    ], sync=False)

    assert out["ok"] is False
    assert out["results"][0]["data"]["failure_type"] == "VISUAL_ACTION_NO_EFFECT"
    assert out["results"][0]["data"]["failure_subtype"] == "source_or_target_not_confirmed"


def test_batch_scope_guard_runs_before_controller_local_select_color(monkeypatch):
    from winpilot.agent import scope_guard
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController

    class Session:
        hwnd = 77
        snapshot = None

    blocked = ToolResult(
        '{"ok": false, "verified": false, "blocked": true, '
        '"error": "foreground window is not the target"}'
    )
    monkeypatch.setattr(scope_guard, "ensure_scope", lambda *_args, **_kwargs: blocked)
    controller = FastGuiController(Session())
    monkeypatch.setattr(
        controller,
        "_select_color",
        lambda _action: (_ for _ in ()).throw(AssertionError("must not inspect an out-of-scope window")),
    )

    out = controller.execute([{"select_color": {"color": "red"}}], sync=False)

    assert out["ok"] is False
    assert out["results"][0]["action"] == "select_color"
    assert out["results"][0]["data"]["blocked"] is True


def test_ensure_input_mode_switches_conflicting_accessible_mode_and_verifies(monkeypatch):
    from winpilot.agent import scope_guard
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import controller as controller_mod
    from winpilot.fast.controller import FastGuiController

    class SelectedPattern:
        IsSelected = True

    class SelectedControl:
        @staticmethod
        def GetSelectionItemPattern():
            return SelectedPattern()

        @staticmethod
        def GetTogglePattern():
            return None

    def element(text, *, selected=False, x=100, live_selected=False):
        return SimpleNamespace(
            text=text,
            role="radio",
            states=["selected"] if selected else [],
            source="uia",
            cx=x,
            cy=80,
            extra={"uia_ctrl": SelectedControl()} if live_selected else {},
        )

    elements = [
        element("Shapes", selected=True, x=100),
        element("Brushes", x=180, live_selected=True),
    ]
    monkeypatch.setattr(controller_mod.uia, "collect", lambda _hwnd: elements)
    monkeypatch.setattr(
        controller_mod.CONFIG, "enabled_dimensions", lambda: {"uia"},
    )
    monkeypatch.setattr(scope_guard, "ensure_scope", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scope_guard, "take_scope_recovery", lambda *_args, **_kwargs: None)

    class Session:
        hwnd = 77
        snapshot = None
        _screen_epoch = 1
        run_id = "input-mode-switch"

    controller = FastGuiController(Session())
    clicks = []
    monkeypatch.setattr(
        controller,
        "_dispatch",
        lambda name, args: (
            clicks.append((name, dict(args))) or ToolResult('{"ok": true}')
        ),
    )

    out = controller.execute([
        {"action": "ensure_input_mode", "mode": "continuous_freehand"},
    ], sync=False)

    assert out["ok"] is True
    assert out["verified"] is True
    assert clicks == [("click", {"x": 180, "y": 80, "skip_screen_delta": True})]
    data = out["results"][0]["data"]
    assert data["provider"] == "accessibility_post_selection"
    assert data["postcondition"]["active_matches"][0]["text"] == "Brushes"


def test_ensure_input_mode_rechecks_custom_toolbar_without_selection_pattern(monkeypatch):
    from winpilot.agent import scope_guard
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import controller as controller_mod
    from winpilot.fast.controller import FastGuiController

    def element(text, *, selected=False, x=100):
        return SimpleNamespace(
            text=text,
            role="button",
            states=["selected"] if selected else [],
            source="uia",
            cx=x,
            cy=80,
            extra={},
        )

    observations = iter([
        [element("Shape", selected=True), element("Pen", x=180)],
        [element("Shape"), element("Pen", selected=True, x=180)],
    ])
    monkeypatch.setattr(
        controller_mod.uia, "collect", lambda _hwnd: next(observations),
    )
    monkeypatch.setattr(
        controller_mod.CONFIG, "enabled_dimensions", lambda: {"uia"},
    )
    monkeypatch.setattr(scope_guard, "ensure_scope", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scope_guard, "take_scope_recovery", lambda *_args, **_kwargs: None)

    class Session:
        hwnd = 77
        snapshot = None
        _screen_epoch = 1
        run_id = "custom-mode-switch"

    controller = FastGuiController(Session())
    monkeypatch.setattr(
        controller, "_dispatch", lambda _name, _args: ToolResult('{"ok": true}'),
    )

    out = controller.execute([{
        "action": "ensure_input_mode", "mode": "continuous_freehand",
    }], sync=False)

    assert out["ok"] is True
    data = out["results"][0]["data"]
    assert data["provider"] == "fresh_accessibility_post_selection"
    assert data["postcondition"]["verification_source"] == "fresh_accessibility_state"
    assert data["postcondition"]["active_matches"][0]["text"] == "Pen"


def test_ensure_input_mode_does_not_trust_unverified_control_click(monkeypatch):
    from winpilot.agent import scope_guard
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import controller as controller_mod
    from winpilot.fast.controller import FastGuiController

    def element(text, *, selected=False, x=100):
        return SimpleNamespace(
            text=text,
            role="button",
            states=["selected"] if selected else [],
            source="uia",
            cx=x,
            cy=80,
            extra={},
        )

    unchanged = [
        element("Shape", selected=True), element("Pen", x=180),
    ]
    monkeypatch.setattr(controller_mod.uia, "collect", lambda _hwnd: unchanged)
    monkeypatch.setattr(
        controller_mod.CONFIG, "enabled_dimensions", lambda: {"uia"},
    )
    monkeypatch.setattr(scope_guard, "ensure_scope", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scope_guard, "take_scope_recovery", lambda *_args, **_kwargs: None)

    class Session:
        hwnd = 77
        snapshot = None
        _screen_epoch = 1
        run_id = "unverified-mode-switch"

    controller = FastGuiController(Session())
    monkeypatch.setattr(
        controller, "_dispatch", lambda _name, _args: ToolResult('{"ok": true}'),
    )

    out = controller.execute([{
        "action": "ensure_input_mode", "mode": "continuous_freehand",
    }], sync=False)

    assert out["ok"] is False
    data = out["results"][0]["data"]
    assert data["failure_type"] == "INPUT_MODE_ACTIVATION_UNVERIFIED"
    assert data["postcondition"]["active_matches"] == []
    assert data["postcondition"]["active_conflicts"][0]["text"] == "Shape"


def test_ensure_input_mode_blocks_unresolvable_conflicting_mode(monkeypatch):
    from winpilot.agent import scope_guard
    from winpilot.fast import controller as controller_mod
    from winpilot.fast.controller import FastGuiController

    conflicting = SimpleNamespace(
        text="Selection", role="radio", states=["selected"], source="uia",
        cx=100, cy=80,
    )
    monkeypatch.setattr(controller_mod.uia, "collect", lambda _hwnd: [conflicting])
    monkeypatch.setattr(
        controller_mod.CONFIG, "enabled_dimensions", lambda: {"uia"},
    )
    monkeypatch.setattr(scope_guard, "ensure_scope", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scope_guard, "take_scope_recovery", lambda *_args, **_kwargs: None)

    class Session:
        hwnd = 77
        snapshot = None
        _screen_epoch = 1
        run_id = "input-mode-block"

    out = FastGuiController(Session()).execute([
        {"action": "ensure_input_mode", "mode": "continuous_freehand"},
    ], sync=False)

    assert out["ok"] is False
    assert out["failure_type"] == "INPUT_MODE_CONFLICT_UNRESOLVED"


def test_ensure_input_mode_does_not_treat_accessibility_silence_as_proof(monkeypatch):
    from winpilot.agent import scope_guard
    from winpilot.fast import controller as controller_mod
    from winpilot.fast.controller import FastGuiController

    monkeypatch.setattr(controller_mod.uia, "collect", lambda _hwnd: [])
    monkeypatch.setattr(
        controller_mod.CONFIG, "enabled_dimensions", lambda: {"uia"},
    )
    monkeypatch.setattr(controller_mod.vlm, "is_configured", lambda: False)
    monkeypatch.setattr(scope_guard, "ensure_scope", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scope_guard, "take_scope_recovery", lambda *_args, **_kwargs: None)

    class Session:
        hwnd = 77
        snapshot = None
        _screen_epoch = 1
        run_id = "mode-silence"

    out = FastGuiController(Session()).execute([{
        "action": "ensure_input_mode", "mode": "continuous_freehand",
    }], sync=False)

    assert out["ok"] is False
    assert out["failure_type"] == "INPUT_MODE_VISUAL_PROOF_UNAVAILABLE"
    data = out["results"][0]["data"]
    assert data["verified"] is False
    assert data["provider"] != "implicit_single_mode_surface"


def test_ensure_input_mode_accepts_positive_intrinsic_visual_proof(monkeypatch):
    from winpilot.agent import scope_guard
    from winpilot.fast import controller as controller_mod
    from winpilot.fast.controller import FastGuiController
    from winpilot.utils.screenshot import Region

    monkeypatch.setattr(
        controller_mod.uia,
        "collect",
        lambda _hwnd: [SimpleNamespace(
            text="Draw with pointer strokes inside the target",
            role="text",
            states=[],
            source="uia",
            cx=200,
            cy=100,
        )],
    )
    monkeypatch.setattr(
        controller_mod.CONFIG, "enabled_dimensions", lambda: {"uia"},
    )
    monkeypatch.setattr(controller_mod.vlm, "is_configured", lambda: True)
    prompts = []

    def prove_direct_surface(_image, prompt, **_kwargs):
        prompts.append(prompt)
        return {
            "requested_mode_active": False,
            "explicit_mode_system": False,
            "direct_gesture_surface_proven": True,
            "selector_center": None,
            "surface_managed_requested_color": False,
            "confidence": 0.94,
            "evidence": ["visible instructions require direct pointer strokes"],
        }

    monkeypatch.setattr(
        controller_mod.vlm,
        "structured_json_from_array",
        prove_direct_surface,
    )
    monkeypatch.setattr(
        controller_mod, "window_region", lambda _hwnd: Region(10, 20, 500, 400),
    )
    monkeypatch.setattr(scope_guard, "ensure_scope", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scope_guard, "take_scope_recovery", lambda *_args, **_kwargs: None)

    class Session:
        hwnd = 77
        snapshot = None
        _screen_epoch = 1
        run_id = "intrinsic-mode-proof"

    out = FastGuiController(
        Session(),
        capture_fn=lambda _region: np.zeros((400, 500, 3), dtype=np.uint8),
    ).execute([{
        "action": "ensure_input_mode", "mode": "continuous_freehand",
        "surface_affordance_evidence": "bounded surface accepts pointer strokes",
        "target_region": [250, 300, 500, 600],
    }], sync=False)

    assert out["ok"] is True
    data = out["results"][0]["data"]
    assert data["verified"] is True
    assert data["provider"] == "visual_intrinsic_input_mode"
    assert data["model_calls"] == 1
    assert "direct_gesture_surface_proven" in prompts[0]
    assert "bounded surface accepts pointer strokes" in prompts[0]
    assert "[250, 300, 500, 600]" in prompts[0]
    assert "Draw with pointer strokes inside the target" in prompts[0]
    assert data["visible_text_hints"] == [
        "Draw with pointer strokes inside the target",
    ]


def test_mode_proof_can_verify_surface_managed_color_without_palette(monkeypatch):
    from winpilot.agent import scope_guard
    from winpilot.fast import controller as controller_mod
    from winpilot.fast.controller import FastGuiController
    from winpilot.utils.screenshot import Region

    monkeypatch.setattr(controller_mod.uia, "collect", lambda _hwnd: [])
    monkeypatch.setattr(
        controller_mod.CONFIG, "enabled_dimensions", lambda: {"uia"},
    )
    monkeypatch.setattr(controller_mod.vlm, "is_configured", lambda: True)
    calls = []

    def prove_surface_style(_image, prompt, **_kwargs):
        calls.append(prompt)
        return {
            "requested_mode_active": True,
            "explicit_mode_system": False,
            "direct_gesture_surface_proven": True,
            "selector_center": None,
            "surface_managed_requested_color": True,
            "confidence": 0.96,
            "evidence": [
                "the visible participant legend assigns red to this gesture",
            ],
        }

    monkeypatch.setattr(
        controller_mod.vlm, "structured_json_from_array", prove_surface_style,
    )
    monkeypatch.setattr(
        controller_mod, "window_region", lambda _hwnd: Region(10, 20, 500, 400),
    )
    monkeypatch.setattr(scope_guard, "ensure_scope", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        scope_guard, "take_scope_recovery", lambda *_args, **_kwargs: None,
    )

    class Session:
        hwnd = 77
        snapshot = None
        _screen_epoch = 4
        run_id = "surface-managed-color"

    controller = FastGuiController(
        Session(),
        capture_fn=lambda _region: np.zeros((400, 500, 3), dtype=np.uint8),
    )
    out = controller.execute([
        {
            "action": "ensure_input_mode",
            "mode": "continuous_freehand",
            "visual_frame_id": 4,
            "requested_color": "red",
        },
        {
            "action": "select_color",
            "color": "red",
            "visual_frame_id": 4,
        },
    ], sync=False)

    assert out["ok"] is True
    assert len(calls) == 1
    assert "Requested output color: red" in calls[0]
    color_result = out["results"][1]["data"]
    assert color_result["verified"] is True
    assert color_result["mutation_applied"] is False
    assert color_result["provider"] == "visual_surface_managed_color"


def test_visual_mode_selector_requires_fresh_post_selection_proof(monkeypatch):
    from winpilot.agent import scope_guard
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import controller as controller_mod
    from winpilot.fast.controller import FastGuiController
    from winpilot.utils.screenshot import Region

    monkeypatch.setattr(controller_mod.uia, "collect", lambda _hwnd: [])
    monkeypatch.setattr(
        controller_mod.CONFIG, "enabled_dimensions", lambda: {"uia"},
    )
    monkeypatch.setattr(controller_mod.vlm, "is_configured", lambda: True)
    verdicts = iter([
        {
            "requested_mode_active": False,
            "explicit_mode_system": True,
            "intrinsic_single_mode_surface": False,
            "selector_center": [250, 125],
            "confidence": 0.91,
            "evidence": ["one visible compatible mode control"],
        },
        {
            "requested_mode_active": True,
            "explicit_mode_system": True,
            "intrinsic_single_mode_surface": False,
            "selector_center": [250, 125],
            "confidence": 0.93,
            "evidence": ["the compatible mode is visibly active"],
        },
    ])
    monkeypatch.setattr(
        controller_mod.vlm,
        "structured_json_from_array",
        lambda *_args, **_kwargs: next(verdicts),
    )
    monkeypatch.setattr(
        controller_mod, "window_region", lambda _hwnd: Region(10, 20, 500, 400),
    )
    monkeypatch.setattr(scope_guard, "ensure_scope", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scope_guard, "take_scope_recovery", lambda *_args, **_kwargs: None)

    class Session:
        hwnd = 77
        snapshot = None
        _screen_epoch = 1
        run_id = "visual-mode-switch"

    captures = []
    controller = FastGuiController(
        Session(),
        capture_fn=lambda region: captures.append(region)
        or np.zeros((400, 500, 3), dtype=np.uint8),
    )
    clicks = []
    monkeypatch.setattr(
        controller,
        "_dispatch",
        lambda name, args: clicks.append((name, dict(args)))
        or ToolResult('{"ok": true}'),
    )

    out = controller.execute([{
        "action": "ensure_input_mode", "mode": "continuous_freehand",
    }], sync=False)

    assert out["ok"] is True
    data = out["results"][0]["data"]
    assert data["provider"] == "visual_post_selection"
    assert data["model_calls"] == 2
    assert len(captures) == 2
    assert clicks == [("click", {"x": 135, "y": 70, "skip_screen_delta": True})]


def test_batch_scope_recovery_rebinds_and_continues_only_uncommitted_action(monkeypatch):
    from winpilot.agent import scope_guard
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController

    class Session:
        hwnd = 77
        snapshot = object()
        _observe_cache = object()
        _draw_surface_cache = {"stale": True}
        _screen_epoch = 0

    session = Session()
    guard_calls = 0

    def guarded(current, name, _args):
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls == 2:
            current._last_scope_recovery = {
                "tool": name,
                "attempts": 1,
                "authoritative_frame_required": True,
            }
        return None

    monkeypatch.setattr(scope_guard, "ensure_scope", guarded)
    controller = FastGuiController(session)
    dispatched = []
    mapped_cache_states = []

    def map_draw(args):
        mapped_cache_states.append(session._draw_surface_cache)
        return {"strokes": args["normalized_strokes"], "_draw_strokes_prepared": True}

    monkeypatch.setattr(controller, "_map_canvas_relative_draw", map_draw)
    monkeypatch.setattr(
        controller,
        "_dispatch",
        lambda name, args: dispatched.append((name, dict(args)))
        or ToolResult('{"ok": true, "verified": true, "visual_change_detected": true}'),
    )
    monkeypatch.setattr(
        controller,
        "_auto_visual_verify",
        lambda *_args: ToolResult('{"ok": true, "verified": true}'),
    )

    out = controller.execute([
        {"action": "hotkey", "keys": ["ctrl", "a"]},
        {"action": "draw_strokes", "normalized_strokes": [[[1, 1], [2, 2]]]},
    ], sync=False)

    assert out["ok"] is True
    assert [name for name, _args in dispatched] == ["hotkey", "draw_strokes"]
    assert mapped_cache_states == [None]
    assert out["metrics"]["scope_recovery_count"] == 1
    assert out["transaction"] == {
        "planned_action_count": 2,
        "attempted_action_count": 2,
        "committed_action_count": 2,
        "next_uncommitted_action_index": None,
        "remaining_action_count": 0,
        "scope_recovery_count": 1,
    }
    assert out["results"][1]["scope_recovery"]["tool"] == "draw_strokes"


def test_raw_controller_dispatch_consumes_inner_scope_recovery(monkeypatch):
    import json

    from winpilot.agent import scope_guard
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController

    class Session:
        hwnd = 77
        snapshot = object()
        _observe_cache = object()
        _draw_surface_cache = {"stale": True}

    session = Session()
    controller = FastGuiController(session)
    monkeypatch.setattr(controller.raw_device, "supports", lambda name: name == "click")
    monkeypatch.setattr(
        controller.raw_device,
        "execute",
        lambda _name, _args: ToolResult('{"ok": true, "verified": true}'),
    )

    def recovered(current, name, _args):
        current._last_scope_recovery = {
            "tool": name,
            "attempts": 1,
            "authoritative_frame_required": True,
        }
        return None

    monkeypatch.setattr(scope_guard, "ensure_scope", recovered)

    payload = json.loads(controller._dispatch("click", {"x": 1, "y": 2}).text)

    assert payload["scope_recovery"]["tool"] == "click"
    assert session.snapshot is None
    assert session._draw_surface_cache is None
    assert scope_guard.take_scope_recovery(session) is None


def test_select_color_uses_vlm_labeled_control_after_swatch_cv_miss(monkeypatch):
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region
    import winpilot.fast.controller as controller_module

    class Session:
        hwnd = None
        snapshot = None

    controller = FastGuiController(Session())
    monkeypatch.setattr(controller_module.vlm, "is_configured", lambda: True)
    monkeypatch.setattr(
        controller_module.vlm,
        "collect",
        lambda _region: [
            UIElement(
                source="vlm", role="button", text="Redo",
                cx=290, cy=120, left=275, top=106, width=30, height=28,
                confidence=0.9,
            ),
            UIElement(
                source="vlm", role="button", text="Red",
                cx=340, cy=120, left=325, top=106, width=30, height=28,
                confidence=0.8,
            ),
        ],
    )

    point, meta = controller._visible_color_label_point(Region(0, 0, 800, 500), "red")

    assert point == (340, 120)
    assert meta["element_source"] == "vlm"
    assert meta["label"] == "Red"


def test_select_color_uses_fresh_uia_palette_item_after_open(monkeypatch):
    from winpilot.fast import controller as controller_module
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    class Session:
        hwnd = 7
        snapshot = None

    controller = FastGuiController(Session())
    monkeypatch.setattr(controller_module.CONFIG, "enabled_dimensions", lambda: ["uia"])
    monkeypatch.setattr(
        controller_module.uia,
        "collect",
        lambda _hwnd: [UIElement(
            source="uia", role="listitem", text="Red",
            cx=872, cy=353, left=857, top=338, width=30, height=30,
            confidence=1.0,
        )],
    )
    vlm_calls = []
    monkeypatch.setattr(controller_module.vlm, "is_configured", lambda: True)
    monkeypatch.setattr(controller_module.vlm, "collect", lambda _region: vlm_calls.append(True) or [])

    point, meta = controller._visible_color_label_point(Region(480, 154, 1008, 720), "red")

    assert point == (872, 353)
    assert meta["element_source"] == "uia"
    assert meta["source"] == "snapshot+uia"
    assert vlm_calls == []


def test_open_color_palette_uses_vlm_when_ocr_cannot_read_owner_drawn_ribbon(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region
    import winpilot.fast.controller as controller_module

    class Session:
        hwnd = None
        snapshot = None

    controller = FastGuiController(Session())
    clicks = []
    monkeypatch.setattr(controller_module.ocr, "collect", lambda _region: [])
    monkeypatch.setattr(controller_module.vlm, "is_configured", lambda: True)
    monkeypatch.setattr(
        controller_module.vlm,
        "collect",
        lambda _region: [UIElement(
            source="vlm", role="button", text="Colors",
            cx=420, cy=160, left=400, top=145, width=40, height=30,
            confidence=0.8,
        )],
    )
    monkeypatch.setattr(
        controller,
        "_dispatch",
        lambda name, args: clicks.append((name, args)) or ToolResult('{"ok": true}'),
    )

    data = json.loads(controller._open_color_palette(Region(100, 100, 800, 500)).text)

    assert data["ok"] is True
    assert data["locator_source"] == "vlm"
    assert clicks[0][0] == "click"
    assert clicks[0][1]["x"] == 420


def test_open_color_palette_prefers_structured_snapshot_button_over_vlm(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import controller as controller_module
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    class Session:
        hwnd = 1
        snapshot = type("Snapshot", (), {"elements": [UIElement(
            source="uia", role="button", text="Colors",
            cx=922, cy=298, left=901, top=270, width=42, height=57,
            confidence=1.0,
        )]})()

    controller = FastGuiController(Session())
    clicks = []
    monkeypatch.setattr(controller_module.ocr, "collect", lambda _region: [])
    monkeypatch.setattr(controller_module.vlm, "is_configured", lambda: True)
    monkeypatch.setattr(
        controller_module.vlm,
        "collect",
        lambda _region: [UIElement(
            source="vlm", role="button", text="Colors",
            cx=817, cy=263, left=800, top=250, width=34, height=26,
            confidence=0.8,
        )],
    )
    monkeypatch.setattr(
        controller,
        "_dispatch",
        lambda name, args: clicks.append((name, args)) or ToolResult('{"ok": true}'),
    )

    data = json.loads(controller._open_color_palette(Region(480, 154, 1008, 720)).text)

    assert data["ok"] is True
    assert data["locator_source"] == "snapshot"
    assert data["point"] == {"x": 922, "y": 298}
    assert clicks == [("click", {"x": 922, "y": 298, "skip_screen_delta": True})]


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


def test_optional_wait_change_does_not_abort_batch():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.types import ScreenDelta

    class Watcher:
        def wait_for_change(self, timeout_s=3.0, min_ratio=0.0005):
            return ScreenDelta(False, 0.0, [], 5)

        def wait_for_stable(self, **_kwargs):
            return {"stable": True, "delta": {"changed": False}}

        def status(self):
            return {"samples": 2}

    class Session:
        snapshot = None
        hwnd = None

        @staticmethod
        def dispatch(name, _args):
            assert name == "read_text"
            return ToolResult('{"ok": true, "text": "ready"}')

    out = FastGuiController(Session(), watcher=Watcher()).execute([
        {"action": "wait_change", "timeout_s": 0.1, "required": False},
        {"action": "read_text"},
    ], sync=False)

    assert out["ok"] is True
    assert len(out["results"]) == 2
    assert out["results"][0]["data"]["changed"] is False
    assert out["results"][0]["data"]["required"] is False


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


def test_natural_observe_subgoal_routes_to_perception_without_controller():
    from winpilot.agent.fast_tools import FastToolMixin
    from winpilot.agent.tool_result import ToolResult

    captured = {}

    class Session(FastToolMixin):
        hwnd = 42

        @staticmethod
        def _t_observe(args):
            captured.update(args)
            return ToolResult("current spreadsheet state")

        def _fast_controller(self):
            raise AssertionError("observe-only subgoal should not initialize the mutation controller")

    result = Session()._t_execute_subgoal({
        "subgoal": "Observe the spreadsheet window to identify the cell grid",
        "target_window": {"hwnd": 42},
    })

    assert result.text == "current spreadsheet state"
    assert captured["hwnd"] == 42
    assert "spreadsheet" in captured["query"]


def test_open_spreadsheet_subgoal_does_not_launch_blank_as_an_app():
    from winpilot.fast.subgoal import _app_launch_target, parse_subgoal_request

    request = parse_subgoal_request({
        "subgoal": "Open WPS Spreadsheets and create a brand-new blank workbook",
        "click": {"label": "Blank"},
    })

    assert _app_launch_target(request) == "WPS Spreadsheets"


def test_execute_subgoal_format_is_an_executable_routine():
    from winpilot.agent.fast_tools import _has_execute_subgoal_routine

    assert _has_execute_subgoal_routine({
        "subgoal": "format heading",
        "formats": [{"range": "first_line", "bold": True}],
    }) is True


def test_execute_subgoal_rejects_unresolved_target_window_without_controller(monkeypatch):
    from winpilot.agent import fast_tools
    from winpilot.agent.fast_tools import FastToolMixin

    monkeypatch.setattr(fast_tools.win32, "find_app", lambda _process: {"windows": []})

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


def test_execute_subgoal_open_app_uses_unified_launch_capability_without_hwnd():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor

    calls = []

    class Session:
        task = "打开记事本"
        hwnd = None
        snapshot = None

        def dispatch(self, name, args):
            calls.append((name, args))
            assert name == "open_app"
            self.hwnd = 77
            return ToolResult(json.dumps({
                "ok": True,
                "verified": True,
                "target_hwnd": 77,
                "title": "无标题 - 记事本",
            }, ensure_ascii=False))

    session = Session()
    result = SubgoalExecutor(session, FastGuiController(session)).execute({
        "subgoal": "打开记事本",
        "click": {"label": "记事本"},
    })
    payload = json.loads(result.text)

    assert calls == [("open_app", {"name": "记事本"})]
    assert payload["ok"] is True
    assert payload["verified"] is True
    assert payload["subgoal_route"] == "open_app_capability"
    assert payload["target_hwnd"] == 77


def test_natural_execute_subgoal_open_app_routes_without_a_failed_llm_round_trip():
    from winpilot.agent.fast_tools import FastToolMixin
    from winpilot.agent.tool_result import ToolResult

    calls = []

    class Session(FastToolMixin):
        task = "Open WPS Spreadsheets application and create a workbook"
        hwnd = None
        snapshot = None

        def dispatch(self, name, args):
            calls.append((name, args))
            self.hwnd = 88
            return ToolResult(json.dumps({
                "ok": True,
                "verified": True,
                "target_hwnd": 88,
                "title": "Workbook - WPS Office",
            }))

    result = Session()._t_execute_subgoal({
        "subgoal": "Open WPS Spreadsheets application",
        "target_window": {"process": "wps"},
    })
    payload = json.loads(result.text)

    assert calls == [("open_app", {"name": "WPS Spreadsheets"})]
    assert payload["ok"] is True
    assert payload["subgoal_route"] == "open_app_capability"


def test_execute_subgoal_open_app_accepts_shared_product_family_name():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor

    calls = []

    class Session:
        task = "打开WPS文字"
        hwnd = None
        snapshot = None

        def dispatch(self, name, args):
            calls.append((name, args))
            self.hwnd = 9
            return ToolResult('{"ok": true, "verified": true, "target_hwnd": 9}')

    session = Session()
    result = SubgoalExecutor(session, FastGuiController(session)).execute({
        "subgoal": "Open WPS Writer",
        "click": {"label": "WPS Office"},
    })

    assert calls == [("open_app", {"name": "WPS Writer"})]
    assert json.loads(result.text)["ok"] is True


def test_execute_subgoal_routes_wps_product_family_to_requested_spreadsheet_component():
    from winpilot.fast.subgoal import _app_launch_target, parse_subgoal_request

    request = parse_subgoal_request({
        "subgoal": "Open WPS Office and create a new blank spreadsheet workbook",
        "click": {"label": "WPS Office"},
    })

    assert _app_launch_target(request) == "WPS Spreadsheets"


def test_initial_task_app_target_extracts_first_explicit_app_without_workflow_rules():
    from winpilot.fast.subgoal import _initial_task_app_target

    assert _initial_task_app_target(
        "严格只通过 GUI 完成：打开WPS文字，新建空白文档并输入周报"
    ) == "WPS文字"
    assert _initial_task_app_target(
        "Open Microsoft Word and create a blank document"
    ) == "Microsoft Word"
    assert _initial_task_app_target(
        "不要打开记事本；打开画图，然后画一只猫"
    ) == "画图"
    assert _initial_task_app_target(
        "在画板上画一只五颜六色的很细节的猫"
    ) == "画板"
    assert _initial_task_app_target(
        "用QQ给我的手机发一条消息"
    ) == "QQ"
    assert _initial_task_app_target(
        "In Paint draw a detailed landscape"
    ) == "Paint"


def test_execute_subgoal_launches_initial_task_app_before_first_in_app_action():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []

    class Session:
        task = "严格只通过GUI完成：打开WPS文字，新建空白文档"
        hwnd = None
        snapshot = None
        run_id = "initial-app-prelude"

        def dispatch(self, name, args):
            calls.append((name, args))
            if name == "open_app":
                self.hwnd = 91
                self.snapshot = Snapshot(
                    hwnd=91,
                    title="WPS Office",
                    region=Region(0, 0, 1200, 800),
                    elements=[UIElement(
                        elem_id=1,
                        source="uia",
                        role="button",
                        text="文字",
                        cx=80,
                        cy=180,
                        left=40,
                        top=160,
                        width=80,
                        height=40,
                    )],
                )
                return ToolResult('{"ok": true, "verified": true, "target_hwnd": 91}')
            if name == "focus_window":
                return ToolResult('{"ok": true}')
            raise AssertionError(f"unexpected dispatch: {name}")

    session = Session()
    controller = FastGuiController(session)
    controller.watcher.status = lambda: {}
    controller.execute = lambda actions, **kwargs: {
        "ok": True,
        "verified": True,
        "results": [{"action": "click_element", "ok": True, "verified": True}],
        "controller_state": {"phase": "verified"},
        "metrics": {},
    }
    result = SubgoalExecutor(session, controller).execute({
        "subgoal": '点击WPS首页左侧导航栏的"文字"标签',
        "click": {"label": "文字"},
    })
    payload = json.loads(result.text)

    assert calls[0] == ("open_app", {"name": "WPS文字"})
    assert calls[1] == ("focus_window", {"hwnd": 91})
    assert payload["ok"] is True
    assert payload["evidence_packet"]["prelude"][0]["action"] == "open_app"
    assert payload["actions_executed"][0]["action"] == "click_element"


def test_runner_prebinds_leading_implicit_gui_application(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tools import ToolResult

    runner = AgentRunner("在画板上画一只五颜六色的猫")
    runner.messages = [{"role": "system", "content": "system"}]
    calls = []

    def dispatch(tool, args):
        calls.append((tool, args))
        return ToolResult('{"ok": true, "verified": true, "target_hwnd": 42}')

    monkeypatch.setattr(runner.session, "dispatch", dispatch)

    runner._prebind_initial_gui_target()

    assert calls == [("open_app", {"name": "画板"})]
    assert "controller target prebind" in runner.messages[-1]["content"]


def test_native_prebind_hides_text_controller_observe_request(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tools import ToolResult

    runner = AgentRunner("open Paint and operate the visible canvas")
    runner.messages = [{"role": "system", "content": "system"}]
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr(
        runner.session,
        "dispatch",
        lambda _tool, _args: ToolResult(json.dumps({
            "ok": True,
            "verified": True,
            "route": "existing_visible_window",
            "target_hwnd": 42,
            "needs_observe": True,
            "fast_path_advice": "Observe it once, then continue.",
        })),
    )

    runner._prebind_initial_gui_target()

    model_message = runner.messages[-1]["content"]
    history_result = runner._tool_history[-1][2]
    assert "fresh_authoritative_frame_pending" in model_message
    assert "needs_observe" not in model_message
    assert "Observe it once" not in model_message
    assert "needs_observe" not in history_result
    assert "Observe it once" not in history_result
    assert "do not call observe merely to reconfirm" in model_message


def test_run_action_batch_open_app_recovers_hallucinated_preobserve_element_id():
    from winpilot.agent.fast_tools import FastToolMixin
    from winpilot.agent.tool_result import ToolResult

    calls = []

    class Session(FastToolMixin):
        task = "打开WPS文字并新建文档"
        hwnd = None
        snapshot = None
        run_id = "run-batch-open-app"

        def dispatch(self, name, args):
            calls.append((name, args))
            assert name == "open_app"
            self.hwnd = 88
            return ToolResult(json.dumps({
                "ok": True,
                "verified": True,
                "target_hwnd": 88,
                "title": "WPS Office",
            }, ensure_ascii=False))

    session = Session()
    result = session._t_run_action_batch({
        "subgoal": "打开WPS文字",
        "actions": [{"action": "click", "element_id": 22}],
    })
    payload = json.loads(result.text)

    assert calls == [("open_app", {"name": "WPS Writer"})]
    assert payload["ok"] is True
    assert payload["subgoal_route"] == "open_app_capability"


def test_execute_subgoal_does_not_mutate_implicit_foreground_without_target_hwnd():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor

    class Session:
        task = "type SCOPE_TOKEN_42 and click Apply"
        hwnd = None
        snapshot = None

        def dispatch(self, name, args):
            raise AssertionError(f"unscoped subgoal must not dispatch {name}")

    session = Session()
    result = SubgoalExecutor(session, FastGuiController(session)).execute({
        "subgoal": "type and apply",
        "input": {"label": "Target Input", "text": "SCOPE_TOKEN_42"},
        "click": {"label": "Apply"},
        "verify_texts": ["SCOPE_TOKEN_42"],
    })
    data = json.loads(result.text)

    assert data["ok"] is False
    assert data["blocked"] is True
    assert data["needs_target_window"] is True
    assert data["evidence_packet"]["matched_texts"] == []
    assert "current foreground window is not an implicit target" in data["error"]


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


def test_batch_file_evidence_gets_eventual_consistency_timeout():
    from winpilot.fast.controller import _with_batch_expect_defaults

    action = _with_batch_expect_defaults(
        "click_element",
        {"expect": {"window_gone": "Save as", "file_exists": r"C:\result.png"}},
    )

    assert action["expect"]["timeout_s"] == 6.0
    assert action["expect"]["file_format"] == {
        "path": r"C:\result.png",
        "format": "png",
    }


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


def test_input_locator_handles_magnifier_q_and_search_ocr_typo():
    from winpilot.fast.input_locator import locate_input
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    snapshot = Snapshot(
        hwnd=1,
        title="Owner-drawn chat surface",
        region=Region(0, 0, 1920, 1020),
        elements=[
            UIElement(
                elem_id=1,
                source="ocr",
                role="text",
                text="Q \u641c\u7d20",
                cx=125,
                cy=83,
                left=94,
                top=71,
                width=63,
                height=25,
                confidence=0.72,
            ),
        ],
    )

    result = locate_input(
        snapshot,
        label="\u641c\u7d22",
        expected_region="top-left",
        fallback_text=["search", "\u67e5\u627e"],
    )

    assert result.found is True
    assert result.candidate is not None
    assert result.candidate.source == "synthetic_near_label"
    assert result.candidate.center != (125, 83)
    assert "search" in result.candidate.reason.casefold()
    assert result.candidate.reason.count("matches expected region") == 1


def test_input_locator_infers_custom_chat_composer_after_target_conversation_opens():
    from winpilot.fast.input_locator import locate_input
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    target = "\u6211\u7684\u624b\u673a"
    snapshot = Snapshot(
        hwnd=1,
        title="Chat",
        region=Region(0, 0, 1920, 1020),
        elements=[
            UIElement(elem_id=1, source="uia", role="edit", text="Search", cx=175, cy=82, left=117, top=70, width=116, height=25),
            UIElement(elem_id=2, source="uia", role="text", text=target, cx=180, cy=542, left=150, top=532, width=60, height=20),
            UIElement(elem_id=3, source="ocr", role="text", text=target, cx=353, cy=86, left=315, top=74, width=76, height=24),
        ],
    )

    result = locate_input(
        snapshot,
        label="message",
        expected_region="bottom-right",
        task=f"use chat to talk with {target}",
        communication_context={"active": True, "recipient": target},
    )
    untyped = locate_input(
        snapshot,
        label="message",
        expected_region="bottom-right",
        task=f"use chat to talk with {target}",
    )

    assert result.found is True
    assert result.candidate is not None
    assert result.candidate.source == "synthetic_chat_composer"
    assert result.candidate.center[0] > 900
    assert result.candidate.center[1] > 850
    assert all(
        candidate.source != "synthetic_chat_composer"
        for candidate in untyped.candidates
    )


def test_legacy_controller_communication_context_comes_from_typed_intent():
    from winpilot.agent.tools import ToolSession

    first = ToolSession(task="\u53d1\u7ed9\u6d4b\u8bd5\u8054\u7cfb\u4eba")
    second = ToolSession(task="\u53d1\u9001\u7ed9\u6d4b\u8bd5\u8054\u7cfb\u4eba")
    non_communication = ToolSession(task="\u6253\u5f00\u8bb0\u4e8b\u672c")
    try:
        assert first.communication_context == second.communication_context
        assert first.communication_context["active"] is True
        assert first.communication_context["recipient"] == "\u6d4b\u8bd5\u8054\u7cfb\u4eba"
        assert non_communication.communication_context["active"] is False
    finally:
        first.close()
        second.close()
        non_communication.close()


def test_tool_session_consumes_injected_contract_intent_without_reparsing(monkeypatch):
    from winpilot.agent.runtime_profile import RuntimeProfile
    from winpilot.agent.tools import ToolSession
    from winpilot.tasking.intent import TaskIntentParser

    monkeypatch.setattr(
        TaskIntentParser,
        "parse",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("typed ToolSession must not reparse raw task text")
        ),
    )
    session = ToolSession(
        task="this contradictory raw text says send to somebody else",
        runtime_profile=RuntimeProfile(desktop=True, coding=False),
        task_intent={
            "original_text": "compiler-owned intent",
            "operation": ["open"],
            "channel": "",
            "recipient": "",
            "send_mode": "send",
        },
    )
    try:
        assert session.task_intent["original_text"] == "compiler-owned intent"
        assert session.communication_context["active"] is False
    finally:
        session.close()


def test_chat_composer_beats_old_message_text_that_says_input_box():
    from winpilot.fast.input_locator import locate_input
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    target = "My Phone"
    snapshot = Snapshot(
        hwnd=1,
        title="Chat",
        region=Region(0, 0, 1200, 800),
        elements=[
            UIElement(elem_id=1, source="uia", role="text", text=target, cx=150, cy=150, left=100, top=140, width=100, height=20),
            UIElement(elem_id=2, source="ocr", role="text", text=target, cx=500, cy=85, left=460, top=70, width=80, height=30),
            UIElement(elem_id=3, source="ocr", role="text", text="click the input box to type", cx=700, cy=420, left=560, top=405, width=280, height=30),
        ],
    )

    result = locate_input(
        snapshot,
        label="input box",
        task=f"send a message to {target} and verify it in chat history",
        communication_context={"active": True, "recipient": target},
    )

    assert result.found is True
    assert result.candidate is not None
    assert result.candidate.source == "synthetic_chat_composer"
    assert result.candidate.center[1] >= 650


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
    assert ("hotkey", {
        "keys": ["enter"],
        "expect": {"file_exists": "C:\\Users\\zou\\Desktop\\WinPilotE2E_note.txt", "timeout_s": 8.0},
    }) in calls


def test_subgoal_formats_heading_and_body_in_one_local_sequence():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request

    class Session:
        task = "Format the first line bold and centered, with the body left aligned"

    session = Session()
    executor = SubgoalExecutor(session, FastGuiController(session))
    request = parse_subgoal_request({
        "subgoal": "Format the document heading and body",
        "formats": [
            {"range": "first_line", "bold": True, "alignment": "center"},
            {"range": "body_after_first_line", "alignment": "left"},
        ],
    }, task=session.task)

    actions = executor._build_actions(request)

    assert actions == [
        {"action": "hotkey", "keys": ["ctrl", "home"]},
        {"action": "hotkey", "keys": ["shift", "end"]},
        {"action": "hotkey", "keys": ["ctrl", "b"]},
        {"action": "hotkey", "keys": ["ctrl", "e"]},
        {"action": "hotkey", "keys": ["ctrl", "home"]},
        {"action": "hotkey", "keys": ["ctrl", "down"]},
        {"action": "hotkey", "keys": ["ctrl", "shift", "end"]},
        {"action": "hotkey", "keys": ["ctrl", "l"]},
        {"action": "hotkey", "keys": ["ctrl", "end"]},
        {"action": "wait_stable", "stable_ms": 250, "timeout_s": 2.0},
    ]


def test_subgoal_pastes_literal_spreadsheet_block_in_one_controller_batch():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    class Session:
        hwnd = 10
        task = (
            "Enter this exact table into cells A1:D3:\n"
            "Name\tQty\tPrice\tTotal\n"
            "Keyboard\t2\t199\t=B2*C2\n"
            "Marker\tSHEET_BATCH_42\t\t\n"
            "Make row 1 bold."
        )
        snapshot = Snapshot(
            hwnd=10,
            title="Workbook1",
            region=Region(100, 50, 1000, 800),
            elements=[
                UIElement(source="ocr", role="text", text="A", cx=180, cy=280, left=170, top=270, width=20, height=20),
                UIElement(source="ocr", role="text", text="1", cx=120, cy=310, left=112, top=300, width=16, height=20),
                UIElement(source="ocr", role="text", text="2", cx=120, cy=338, left=112, top=328, width=16, height=20),
            ],
        )

    session = Session()
    request = parse_subgoal_request({
        "subgoal": "Enter the complete table data into cells A1:D3",
        "input": {"label": "A1", "text": "Name", "clear": False},
        "verify_texts": ["Name"],
    }, task=session.task)

    actions = SubgoalExecutor(session, FastGuiController(session))._build_actions(request)

    assert [action["action"] for action in actions] == [
        "hotkey", "click", "hotkey", "clipboard_write", "clipboard_paste", "wait_stable", "read_text",
        "hotkey", "click", "hotkey", "wait_stable", "read_text",
    ]
    assert actions[1]["x"] == 180
    assert actions[1]["y"] == 310
    assert actions[3]["text"] == (
        "Name\tQty\tPrice\tTotal\r\n"
        "Keyboard\t2\t199\t=B2*C2\r\n"
        "Marker\tSHEET_BATCH_42\t\t"
    )
    assert actions[8]["context"] == "select spreadsheet row 1"
    assert actions[9]["keys"] == ["ctrl", "b"]

    row_by_row_request = parse_subgoal_request({
        "subgoal": "Click cell A1 and enter the table data row by row",
        "click": {"label": "cell A1", "expected_region": "spreadsheet grid area"},
        "verify_texts": ["Name"],
    }, task=session.task)
    row_by_row_actions = SubgoalExecutor(session, FastGuiController(session))._build_actions(row_by_row_request)
    assert any(action["action"] == "clipboard_paste" for action in row_by_row_actions)

    start_entry_request = parse_subgoal_request({
        "subgoal": "Click cell A1 to start data entry",
        "click": {"label": "A1 cell", "expected_region": "top-left of the spreadsheet grid"},
    }, task=session.task)
    start_entry_actions = SubgoalExecutor(session, FastGuiController(session))._build_actions(start_entry_request)
    assert any(action["action"] == "clipboard_paste" for action in start_entry_actions)

    focus_grid_request = parse_subgoal_request({
        "subgoal": "Click cell A1 to focus the spreadsheet grid",
        "click": {"label": "A1", "visual_servo": True},
        "verify_texts": ["A1"],
    }, task=session.task)
    focus_grid_actions = SubgoalExecutor(session, FastGuiController(session))._build_actions(focus_grid_request)
    assert any(action["action"] == "clipboard_paste" for action in focus_grid_actions)

    header_request = parse_subgoal_request({
        "subgoal": "Type the header row into cell A1 separated by tabs",
        "input": {"label": "cell A1", "text": "Name\tQty\tPrice\tTotal"},
    }, task=session.task)
    header_actions = SubgoalExecutor(session, FastGuiController(session))._build_actions(header_request)
    assert any(action["action"] == "clipboard_paste" for action in header_actions)

    session._spreadsheet_blocks_applied = {"A1:D3"}
    repeated_actions = SubgoalExecutor(session, FastGuiController(session))._build_actions(focus_grid_request)
    assert not any(action["action"] == "clipboard_paste" for action in repeated_actions)


def test_spreadsheet_table_batch_fuses_requested_formatting_and_exact_save():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    target = r"C:\Users\zou\Desktop\spreadsheet-compound-batch.xlsx"

    class Session:
        hwnd = 10
        task = (
            "Enter this exact table into cells A1:B2:\n"
            "Name\tValue\nMarker\tCOMPOUND_42\n"
            f"Make row 1 bold and row 5 bold. Save As exactly {target}"
        )
        snapshot = Snapshot(
            hwnd=10,
            title="Workbook1",
            region=Region(0, 0, 1920, 1020),
            elements=[
                UIElement(source="ocr", role="text", text="A", cx=67, cy=250),
                UIElement(source="ocr", role="text", text="10", cx=22, cy=490),
                UIElement(source="ocr", role="text", text="12", cx=22, cy=538),
                UIElement(source="ocr", role="text", text="14", cx=22, cy=586),
            ],
        )

    session = Session()
    request = parse_subgoal_request(
        {"subgoal": "Enter the complete table data into cells A1:B2"},
        task=session.task,
    )
    actions = SubgoalExecutor(session, FastGuiController(session))._build_actions(request)

    assert sum(action["action"] == "clipboard_paste" for action in actions) == 1
    assert [action.get("context") for action in actions if action["action"] == "click"][-2:] == [
        "select spreadsheet row 1",
        "select spreadsheet row 5",
    ]
    assert sum(action.get("keys") == ["ctrl", "b"] for action in actions) == 2
    assert any(action.get("keys") == ["f12"] for action in actions)
    final_enter = next(
        action for action in actions
        if action.get("keys") == ["enter"] and isinstance(action.get("expect"), dict)
    )
    assert final_enter["expect"]["file_exists"] == target
    assert request.constraints["save_path"] == target

def test_subgoal_formats_multiple_spreadsheet_rows_in_one_batch():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    class Session:
        hwnd = 10
        task = "Make row 1 bold and row 5 bold in the workbook"
        snapshot = Snapshot(
            hwnd=10,
            title="Workbook1",
            region=Region(100, 50, 1000, 800),
            elements=[
                UIElement(source="ocr", role="text", text="A", cx=180, cy=280, left=170, top=270, width=20, height=20),
                UIElement(source="ocr", role="text", text="1", cx=120, cy=310, left=112, top=300, width=16, height=20),
                UIElement(source="ocr", role="text", text="2", cx=120, cy=338, left=112, top=328, width=16, height=20),
                UIElement(source="ocr", role="text", text="5", cx=120, cy=422, left=112, top=412, width=16, height=20),
            ],
        )

    session = Session()
    request = parse_subgoal_request({"subgoal": session.task}, task=session.task)
    actions = SubgoalExecutor(session, FastGuiController(session))._build_actions(request)

    assert [action["action"] for action in actions] == [
        "hotkey", "click", "hotkey", "click", "hotkey", "wait_stable", "read_text",
    ]
    assert (actions[1]["x"], actions[1]["y"]) == (120, 310)
    assert (actions[3]["x"], actions[3]["y"]) == (120, 422)
    assert actions[2]["keys"] == ["ctrl", "b"]
    assert actions[4]["keys"] == ["ctrl", "b"]

    only_first_request = parse_subgoal_request({
        "subgoal": "Make row 1 bold",
    }, task=session.task)
    combined_actions = SubgoalExecutor(session, FastGuiController(session))._build_actions(only_first_request)
    assert [action.get("context") for action in combined_actions if action["action"] == "click"] == [
        "select spreadsheet row 1",
        "select spreadsheet row 5",
    ]


def test_spreadsheet_geometry_infers_single_digit_rows_from_visible_later_rows():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    class Session:
        hwnd = 10
        task = "Make row 1 bold and row 5 bold in the workbook"
        snapshot = Snapshot(
            hwnd=10,
            title="Workbook1",
            region=Region(0, 0, 1920, 1020),
            elements=[
                UIElement(source="ocr", role="text", text="A", cx=67, cy=250),
                UIElement(source="ocr", role="text", text="10", cx=22, cy=490),
                UIElement(source="ocr", role="text", text="12", cx=22, cy=538),
                UIElement(source="ocr", role="text", text="14", cx=22, cy=586),
            ],
        )

    request = parse_subgoal_request({"subgoal": Session.task}, task=Session.task)
    actions = SubgoalExecutor(Session(), FastGuiController(Session()))._build_actions(request)
    clicks = [action for action in actions if action["action"] == "click"]

    assert [(action["x"], action["y"]) for action in clicks] == [(22, 274), (22, 370)]


def test_execute_subgoal_remembers_spreadsheet_bold_rows_and_skips_toggle_repeat():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.subgoal import SubgoalExecutor
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    class Session:
        task = "Make row 1 bold and row 5 bold"
        hwnd = 10
        run_id = "spreadsheet-format-test"
        snapshot = Snapshot(
            hwnd=10,
            title="Workbook1",
            region=Region(100, 50, 1000, 800),
            elements=[
                UIElement(source="ocr", role="text", text="A", cx=180, cy=280, left=170, top=270, width=20, height=20),
                UIElement(source="ocr", role="text", text="1", cx=120, cy=310, left=112, top=300, width=16, height=20),
                UIElement(source="ocr", role="text", text="2", cx=120, cy=338, left=112, top=328, width=16, height=20),
                UIElement(source="ocr", role="text", text="5", cx=120, cy=422, left=112, top=412, width=16, height=20),
            ],
        )

        @staticmethod
        def dispatch(name, _args):
            assert name == "focus_window"
            return ToolResult('{"ok": true}')

    class Watcher:
        @staticmethod
        def status():
            return {}

    class Controller:
        watcher = Watcher()

        @staticmethod
        def execute(actions, **_kwargs):
            return {
                "ok": True,
                "verified": False,
                "controller_state": {"phase": "executed"},
                "metrics": {"action_count": len(actions)},
                "results": [
                    {
                        "action": action["action"],
                        "ok": True,
                        "verified": False,
                        "data": {"ok": True},
                    }
                    for action in actions
                ],
            }

    session = Session()
    executor = SubgoalExecutor(session, Controller())
    first = json.loads(executor.execute({
        "subgoal": "Make row 1 bold",
        "target_window": {"hwnd": 10},
    }).text)
    repeated = json.loads(executor.execute({
        "subgoal": "Make row 1 bold and row 5 bold",
        "target_window": {"hwnd": 10},
    }).text)
    save_request = executor.execute({
        "subgoal": "Click File menu to access Save As",
        "click": {"label": "File"},
        "target_window": {"hwnd": 10},
    })

    assert first["spreadsheet_row_format_applied"] == [1, 5]
    assert session._spreadsheet_bold_rows_applied == {1, 5}
    assert repeated["mutation_skipped"] is True
    assert "Do not press Ctrl+B again" in repeated["fast_path_advice"]
    assert json.loads(save_request.text).get("mutation_skipped") is not True


def test_execute_subgoal_reports_spreadsheet_clipboard_batch_without_exception():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.subgoal import SubgoalExecutor
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    snapshot = Snapshot(
        hwnd=10,
        title="Workbook1",
        region=Region(100, 50, 1000, 800),
        elements=[
            UIElement(elem_id=1, source="ocr", role="text", text="A", cx=180, cy=280, left=170, top=270, width=20, height=20),
            UIElement(elem_id=2, source="ocr", role="text", text="1", cx=120, cy=310, left=112, top=300, width=16, height=20),
            UIElement(elem_id=3, source="ocr", role="text", text="2", cx=120, cy=338, left=112, top=328, width=16, height=20),
        ],
    )

    class Session:
        hwnd = 10
        run_id = "spreadsheet-batch-test"
        task = (
            "Enter this exact table into cells A1:D3:\n"
            "Name\tQty\tPrice\tTotal\n"
            "Keyboard\t2\t199\t=B2*C2\n"
            "Marker\tSHEET_BATCH_42\t\t\n"
        )
        snapshot = None

        @staticmethod
        def dispatch(name, args):
            assert name == "focus_window"
            return ToolResult('{"ok": true}')

    class Watcher:
        @staticmethod
        def status():
            return {}

    class Controller:
        watcher = Watcher()

        @staticmethod
        def execute(actions, **_kwargs):
            results = []
            for action in actions:
                data = {"ok": True, "verified": True}
                if action["action"] == "read_text":
                    data["text"] = "Name Qty Price Total Keyboard SHEET_BATCH_42"
                results.append({
                    "action": action["action"],
                    "ok": True,
                    "verified": True,
                    "data": data,
                })
            return {
                "ok": True,
                "verified": True,
                "controller_state": {"phase": "verified"},
                "metrics": {"action_count": len(actions)},
                "results": results,
            }

    session = Session()
    session.snapshot = snapshot
    result = SubgoalExecutor(session, Controller()).execute({
        "subgoal": "Click cell A1 to focus the spreadsheet grid",
        "click": {"label": "A1"},
        "verify_texts": ["A1"],
        "target_window": {"hwnd": 10},
    })
    payload = json.loads(result.text)

    assert payload["ok"] is True
    assert payload["verified"] is True
    assert payload["spreadsheet_table_applied"] is True
    assert payload["evidence_packet"]["spreadsheet_table"] == {
        "range": "A1:D3",
        "row_count": 3,
        "column_count": 4,
        "clipboard_batch": True,
        "applied": True,
    }
    assert session._spreadsheet_blocks_applied == {"A1:D3"}


def test_spreadsheet_paste_remains_success_when_ocr_splits_final_marker():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.subgoal import SubgoalExecutor
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    class Session:
        hwnd = 10
        run_id = "spreadsheet-split-marker"
        task = (
            "Enter this exact table into cells A1:B2:\n"
            "Name\tValue\nMarker\tSHEET_GUI_0929\n"
        )
        snapshot = Snapshot(
            hwnd=10,
            title="Workbook1",
            region=Region(0, 0, 1000, 800),
            elements=[
                UIElement(source="ocr", role="text", text="A", cx=70, cy=190),
                UIElement(source="ocr", role="text", text="1", cx=20, cy=220),
                UIElement(source="ocr", role="text", text="2", cx=20, cy=244),
            ],
        )

        @staticmethod
        def dispatch(name, _args):
            assert name == "focus_window"
            return ToolResult('{"ok": true}')

    class Watcher:
        @staticmethod
        def status():
            return {}

    class Controller:
        watcher = Watcher()

        @staticmethod
        def execute(actions, **_kwargs):
            results = []
            for action in actions:
                ok = action["action"] != "read_text"
                data = {"ok": ok}
                if action["action"] == "read_text":
                    data["text"] = "Name Value Marker SHEET_GUI_C 0929"
                results.append({"action": action["action"], "ok": ok, "verified": False, "data": data})
            return {
                "ok": False,
                "verified": False,
                "controller_state": {"phase": "executed", "needs_llm_reason": True},
                "metrics": {"action_count": len(actions)},
                "results": results,
            }

    payload = json.loads(SubgoalExecutor(Session(), Controller()).execute({
        "subgoal": "Enter the complete table data into the requested range",
        "click": {"label": "A1"},
        # Models occasionally misuse draw.box as a visual target-region hint.
        # A spreadsheet semantic routine must not turn that hint into a color
        # postcondition for a draw action that it never executes.
        "draw": {"box": [100, 180, 800, 600], "shape": "rectangle", "color": "white"},
        "target_window": {"hwnd": 10},
    }).text)

    assert payload["ok"] is True
    assert payload["verified"] is False
    assert payload["mutation_applied"] is True
    assert payload["needs_llm_reason"] is False
    assert payload["evidence_packet"]["missing_texts"] == []
    assert payload["evidence_packet"]["missing_visual_colors"] == []
    assert "SHEET_GUI_0929" in payload["evidence_packet"]["pending_artifact_texts"]


def test_spreadsheet_verify_request_advances_pending_format_then_save():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.subgoal import SubgoalExecutor
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    target = r"C:\Users\zou\Desktop\spreadsheet-state-machine.xlsx"

    class Session:
        hwnd = 10
        run_id = "spreadsheet-state-machine"
        task = (
            "Enter this exact table into cells A1:B2:\nName\tValue\nMarker\tSTATE_42\n"
            f"Make row 1 bold and row 5 bold. Save As exactly {target}"
        )
        _spreadsheet_blocks_applied = {"A1:B2"}
        snapshot = Snapshot(
            hwnd=10,
            title="Workbook1",
            region=Region(0, 0, 1920, 1020),
            elements=[
                UIElement(source="ocr", role="text", text="A", cx=67, cy=250),
                UIElement(source="ocr", role="text", text="10", cx=22, cy=490),
                UIElement(source="ocr", role="text", text="12", cx=22, cy=538),
                UIElement(source="ocr", role="text", text="14", cx=22, cy=586),
            ],
        )

        @staticmethod
        def dispatch(name, _args):
            assert name == "focus_window"
            return ToolResult('{"ok": true}')

    class Watcher:
        @staticmethod
        def status():
            return {}

    class Controller:
        watcher = Watcher()
        batches = []

        @classmethod
        def execute(cls, actions, **_kwargs):
            cls.batches.append(actions)
            results = [
                {
                    "action": action["action"],
                    "ok": True,
                    "verified": True,
                    "data": {"ok": True, "text": "Name Value Marker STATE_42"},
                }
                for action in actions
            ]
            return {
                "ok": True,
                "verified": True,
                "controller_state": {"phase": "verified"},
                "metrics": {"action_count": len(actions)},
                "results": results,
            }

    session = Session()
    executor = SubgoalExecutor(session, Controller())
    verify_request = {
        "subgoal": "Verify the spreadsheet table",
        "verify_texts": ["Name", "STATE_42"],
        "target_window": {"hwnd": 10},
    }

    formatted_and_saved = json.loads(executor.execute(verify_request).text)
    repeated_verify = json.loads(executor.execute(verify_request).text)

    assert formatted_and_saved["spreadsheet_row_format_applied"] == [1, 5]
    assert formatted_and_saved["exact_save_as_applied"] is True
    assert session._spreadsheet_bold_rows_applied == {1, 5}
    assert [(a["x"], a["y"]) for a in Controller.batches[0] if a["action"] == "click"] == [
        (22, 274),
        (22, 370),
    ]
    assert any(
        action.get("action") == "hotkey" and action.get("keys") == ["f12"]
        for action in Controller.batches[0]
    )
    assert repeated_verify["mutation_skipped"] is True
    assert len(Controller.batches) == 1


def test_subgoal_formats_can_dispatch_save_in_the_same_local_sequence():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request

    class Session:
        task = "Format the first line bold and centered, then save with Ctrl+S"

    session = Session()
    executor = SubgoalExecutor(session, FastGuiController(session))
    request = parse_subgoal_request({
        "subgoal": "Format and save the document",
        "formats": [{"range": "first_line", "bold": True, "alignment": "center"}],
        "constraints": {"save_after": True, "wait": True},
    }, task=session.task)

    actions = executor._build_actions(request)

    assert actions[-3:] == [
        {"action": "hotkey", "keys": ["ctrl", "end"]},
        {"action": "hotkey", "keys": ["ctrl", "s"]},
        {"action": "wait_stable", "stable_ms": 250, "timeout_s": 2.0},
    ]


def test_shortcut_label_runs_as_hotkey_instead_of_searching_for_button():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request

    class Session:
        task = "Press Ctrl+N to create a new document"

    session = Session()
    request = parse_subgoal_request({
        "subgoal": "Press Ctrl+N and verify the new document",
        "click": {"label": "Ctrl+N"},
        "verify_texts": ["Untitled"],
        "constraints": {"wait": True},
    }, task=session.task)

    actions = SubgoalExecutor(session, FastGuiController(session))._build_actions(request)

    assert actions[0] == {"action": "hotkey", "keys": ["ctrl", "n"]}
    assert actions[1] == {
        "action": "wait_change", "timeout_s": 6.0, "min_ratio": 0.0002,
        "required": False,
    }
    assert actions[2] == {"action": "wait_stable", "stable_ms": 300, "timeout_s": 5.0, "required": True}
    assert actions[3] == {"action": "read_text"}


def test_new_document_shortcut_reads_state_even_without_verify_texts():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request

    class Session:
        task = "Create a new blank document"

    session = Session()
    request = parse_subgoal_request({
        "subgoal": "Press Ctrl+N to create a new blank document",
        "click": {"label": "Ctrl+N"},
    }, task=session.task)

    actions = SubgoalExecutor(session, FastGuiController(session))._build_actions(request)

    assert actions[-1] == {"action": "read_text"}


def test_new_blank_workbook_button_compiles_to_ctrl_n_controller_routine():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request

    class Session:
        task = "Create a new blank spreadsheet workbook"

    session = Session()
    request = parse_subgoal_request({
        "subgoal": "Create a new blank spreadsheet workbook",
        "click": {"label": "新建", "fallback_text": ["New", "Blank Workbook"]},
    }, task=session.task)

    actions = SubgoalExecutor(session, FastGuiController(session))._build_actions(request)

    assert actions[0] == {"action": "hotkey", "keys": ["ctrl", "n"]}
    assert actions[1]["action"] == "wait_change"
    assert actions[-1] == {"action": "read_text"}


def test_verbose_new_blank_workbook_label_still_compiles_to_ctrl_n():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request

    class Session:
        task = "Create a brand-new blank workbook"

    session = Session()
    request = parse_subgoal_request({
        "subgoal": "Create a brand-new blank workbook in WPS Spreadsheets",
        "click": {
            "label": "New tab or plus button to create blank workbook",
            "expected_region": "top tab bar area",
        },
    }, task=session.task)

    actions = SubgoalExecutor(session, FastGuiController(session))._build_actions(request)

    assert actions[0] == {"action": "hotkey", "keys": ["ctrl", "n"]}
    assert actions[1]["action"] == "wait_change"


def test_repeated_new_workbook_request_advances_to_pending_spreadsheet_table():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.subgoal import SubgoalExecutor
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    class Session:
        hwnd = 10
        run_id = "new-then-table"
        task = (
            "Create a new blank workbook and enter this exact table into cells A1:B2:\n"
            "Name\tValue\nMarker\tNEW_TABLE_42\n"
        )
        snapshot = Snapshot(
            hwnd=10,
            title="Workbook1",
            region=Region(0, 0, 1000, 800),
            elements=[
                UIElement(source="ocr", role="text", text="A", cx=70, cy=190),
                UIElement(source="ocr", role="text", text="1", cx=20, cy=220),
                UIElement(source="ocr", role="text", text="2", cx=20, cy=244),
            ],
        )

        @staticmethod
        def dispatch(name, _args):
            assert name == "focus_window"
            return ToolResult('{"ok": true}')

    class Watcher:
        @staticmethod
        def status():
            return {}

    class Controller:
        watcher = Watcher()
        batches = []

        @classmethod
        def execute(cls, actions, **_kwargs):
            cls.batches.append(actions)
            results = []
            for action in actions:
                data = {"ok": True}
                if action["action"] == "wait_change":
                    data["changed"] = True
                    data["delta"] = {"changed": True}
                if action["action"] == "read_text":
                    data["text"] = "Name Value Marker NEW_TABLE_42"
                results.append({"action": action["action"], "ok": True, "verified": True, "data": data})
            return {
                "ok": True,
                "verified": True,
                "controller_state": {"phase": "verified"},
                "metrics": {"action_count": len(actions)},
                "results": results,
            }

    session = Session()
    executor = SubgoalExecutor(session, Controller())
    request = {
        "subgoal": "Create a new blank workbook",
        "click": {"label": "New"},
        "target_window": {"hwnd": 10},
    }

    first = json.loads(executor.execute(request).text)
    second = json.loads(executor.execute(request).text)

    assert first["new_blank_applied"] is True
    assert first["spreadsheet_table_applied"] is True
    assert session._new_blank_applied is True
    assert second["mutation_skipped"] is True
    assert sum(
        action.get("action") == "hotkey" and action.get("keys") == ["ctrl", "n"]
        for batch in Controller.batches
        for action in batch
    ) == 1
    assert len(Controller.batches) == 1
    assert any(action.get("action") == "clipboard_paste" for action in Controller.batches[0])


def test_new_blank_document_fuses_exact_text_format_and_save():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request
    from winpilot.perception.fusion import Snapshot
    from winpilot.utils.screenshot import Region

    output = r"C:\Users\zou\Desktop\Writer_Fused_E2E.docx"
    body = "Heading\nMarker: WRITER_FUSED_42\nBody line\nConclusion"

    class Session:
        hwnd = 10
        run_id = "writer-fused"
        task = (
            "Open WPS Writer and create a brand-new blank document. "
            "Type these four lines exactly into the document body:\n"
            f"{body}\n"
            "Make the first line bold and centered. Keep the remaining three lines left aligned "
            f"and not bold. Save As exactly {output} through the visible GUI."
        )
        snapshot = Snapshot(
            hwnd=10,
            title="Document1 - WPS Office",
            region=Region(0, 0, 1200, 800),
            elements=[],
        )

    session = Session()
    request = parse_subgoal_request({
        "subgoal": "Create a brand-new blank document",
        "click": {"label": "New"},
        "target_window": {"hwnd": 10},
    }, task=session.task)

    actions = SubgoalExecutor(session, FastGuiController(session))._build_actions(request)

    assert sum(
        action.get("action") == "hotkey" and action.get("keys") == ["ctrl", "n"]
        for action in actions
    ) == 1
    typed_parts = [
        action.get("text")
        for action in actions
        if action.get("action") == "type_text"
    ]
    assert typed_parts == ["Heading", "Marker: WRITER_FUSED_42\nBody line\nConclusion"]
    assert any(action.get("action") == "hotkey" and action.get("keys") == ["ctrl", "b"] for action in actions)
    assert any(action.get("action") == "hotkey" and action.get("keys") == ["ctrl", "e"] for action in actions)
    assert any(action.get("action") == "hotkey" and action.get("keys") == ["ctrl", "l"] for action in actions)
    assert any(action.get("action") == "hotkey" and action.get("keys") == ["f12"] for action in actions)
    assert any(
        action.get("action") == "type_into_element" and action.get("text") == output
        for action in actions
    )
    assert any(
        action.get("action") == "hotkey"
        and action.get("keys") == ["enter"]
        and action.get("expect", {}).get("file_exists") == output
        for action in actions
    )
    assert request.input["text"] == body
    assert request.formats == [
        {"range": "first_line", "bold": True, "alignment": "center"},
        {"range": "body_after_first_line", "alignment": "left"},
    ]
    assert request.constraints["save_path"] == output


def test_new_blank_is_not_recorded_when_shortcut_has_no_window_or_screen_evidence():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.subgoal import SubgoalExecutor
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    class Session:
        hwnd = 10
        run_id = "new-no-evidence"
        task = "Create a new blank workbook"
        snapshot = Snapshot(
            hwnd=10,
            title="Workbook1",
            region=Region(0, 0, 1000, 800),
            elements=[UIElement(source="ocr", role="text", text="A1", cx=70, cy=190)],
        )

        @staticmethod
        def dispatch(name, _args):
            assert name == "focus_window"
            return ToolResult('{"ok": true}')

    class Watcher:
        @staticmethod
        def status():
            return {}

    class Controller:
        watcher = Watcher()

        @staticmethod
        def execute(actions, **_kwargs):
            return {
                "ok": False,
                "verified": False,
                "controller_state": {"phase": "blocked"},
                "metrics": {"action_count": len(actions)},
                "results": [
                    {
                        "action": action["action"],
                        "ok": action["action"] == "hotkey",
                        "verified": False,
                        "data": {"ok": action["action"] == "hotkey"},
                    }
                    for action in actions[:2]
                ],
            }

    session = Session()
    payload = json.loads(SubgoalExecutor(session, Controller()).execute({
        "subgoal": "Create a new blank workbook",
        "click": {"label": "New"},
        "target_window": {"hwnd": 10},
    }).text)

    assert payload["new_blank_applied"] is False
    assert not hasattr(session, "_new_blank_applied")


def test_ctrl_n_adopts_same_process_sibling_window(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import controller as controller_module
    from winpilot.fast.controller import FastGuiController

    class Session:
        hwnd = 10
        snapshot = None

        @staticmethod
        def _window_related_to_target(_hwnd, _target):
            return False

        def _remember_target(self, hwnd, _title):
            self.hwnd = hwnd

        @staticmethod
        def dispatch(name, _args):
            assert name == "hotkey"
            return ToolResult(json.dumps({
                "ok": True,
                "post_state": {"foreground_hwnd": 20, "foreground_title": "Workbook2"},
            }))

    monkeypatch.setattr(controller_module.win32gui, "GetWindow", lambda *_args: 0)
    monkeypatch.setattr(
        controller_module.win32process,
        "GetWindowThreadProcessId",
        lambda hwnd: (1, 500 if hwnd in {10, 20} else 900),
    )
    session = Session()
    result = FastGuiController(session).execute([
        {"action": "hotkey", "keys": ["ctrl", "n"]},
    ], sync=False)

    assert result["ok"] is True
    assert session.hwnd == 20
    assert result["results"][0]["data"]["controller_adopted_foreground_hwnd"] == 20


def test_shortcut_in_subgoal_overrides_non_shortcut_click_label():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request

    class Session:
        task = "Create a new blank document"

    session = Session()
    request = parse_subgoal_request({
        "subgoal": "Create a new blank document by pressing Ctrl+N and verify it",
        "click": {"label": "New Blank Document"},
    }, task=session.task)

    actions = SubgoalExecutor(session, FastGuiController(session))._build_actions(request)

    assert actions[0] == {"action": "hotkey", "keys": ["ctrl", "n"]}
    assert actions[-1] == {"action": "read_text"}


def test_subgoal_formats_save_as_exact_path_inside_controller_batch():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request

    target = r"C:\Users\zou\Desktop\report.docx"

    class Session:
        task = f"Format the heading and Save As exactly {target}"

    session = Session()
    executor = SubgoalExecutor(session, FastGuiController(session))
    request = parse_subgoal_request({
        "subgoal": "Format and save the document",
        "formats": [{"range": "first_line", "bold": True, "alignment": "center"}],
        "constraints": {"save_after": True, "save_path": target, "wait": True},
    }, task=session.task)

    actions = executor._build_actions(request)
    names = [action["action"] for action in actions]

    assert names[-8:] == [
        "hotkey", "wait_stable", "observe", "type_into_element",
        "hotkey", "wait_stable", "read_text", "wait_stable",
    ]
    assert actions[-8]["keys"] == ["ctrl", "s"]
    assert actions[-5]["text"] == target
    assert "文件名称(N)" in actions[-5]["fallback_text"]
    assert actions[-5]["verify_after_submit"] is True
    assert actions[-4]["expect"] == {"file_exists": target, "timeout_s": 8.0}


def test_exact_save_as_subgoal_opens_dialog_and_verifies_exact_path_in_one_batch():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request

    target = r"C:\Users\zou\Desktop\sheet.xlsx"

    class Session:
        task = f"Save the WPS spreadsheet As exactly {target}"

    session = Session()
    request = parse_subgoal_request({
        "subgoal": f"Save As the exact requested path: {target}",
        "input": {"label": "File name", "text": target, "clear": True},
        "click": {"label": "Save"},
        "verify_texts": ["sheet.xlsx"],
        "constraints": {"open_save_as": True, "save_path": target},
    }, task=session.task)

    actions = SubgoalExecutor(session, FastGuiController(session))._build_actions(request)

    assert [action["action"] for action in actions] == [
        "hotkey", "wait", "observe",
        "type_into_element", "hotkey", "wait_stable", "read_text",
    ]
    assert actions[0]["keys"] == ["f12"]
    assert actions[3]["text"] == target
    assert actions[4]["expect"] == {"file_exists": target, "timeout_s": 10.0}


def test_runner_upgrades_file_menu_click_to_exact_save_as_controller_routine():
    from winpilot.agent.loop import AgentRunner

    target = r"C:\Users\zou\Desktop\sheet.xlsx"
    runner = AgentRunner(f"Save As exactly {target} through the GUI")
    upgraded = runner._maybe_upgrade_exact_save_as(
        "execute_subgoal",
        {
            "subgoal": "Click File menu to access Save As",
            "click": {"label": "文件"},
            "target_window": {"hwnd": 42},
        },
    )

    assert upgraded["constraints"] == {
        "open_save_as": True,
        "save_path": target,
        "wait": True,
    }
    assert upgraded["target_window"] == {"hwnd": 42}
    assert upgraded["verify_texts"] == ["sheet.xlsx"]


def test_type_into_element_can_defer_file_path_verification_until_submit(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    region = Region(left=0, top=0, width=1000, height=700)
    field = UIElement(
        elem_id=7,
        source="uia",
        role="combobox",
        text="File name",
        cx=500,
        cy=650,
        left=200,
        top=620,
        width=600,
        height=40,
    )

    class Session:
        task = "Save As C:\\Users\\zou\\Desktop\\report.docx"
        hwnd = 42
        snapshot = Snapshot(hwnd=42, title="Save As", region=region, elements=[field])

        def dispatch(self, name, args):
            if name == "read_text":
                return ToolResult("unreadable OCR")
            return ToolResult('{"ok": true, "verified": false}')

    controller = FastGuiController(Session())
    result = controller._type_into_element({
        "label": "File name",
        "role": "combobox",
        "text": r"C:\Users\zou\Desktop\report.docx",
        "clear": True,
        "verify_after_submit": True,
    })
    payload = json.loads(result.text)

    assert payload["ok"] is True
    assert payload["mutation_applied"] is True
    assert payload["verify_after_submit"] is True


def test_document_format_prefix_stays_successful_when_later_save_input_fails():
    from winpilot.fast.subgoal import _document_format_prefix_ok

    actions = [
        {"action": "hotkey", "keys": ["ctrl", "home"]},
        {"action": "hotkey", "keys": ["shift", "end"]},
        {"action": "hotkey", "keys": ["ctrl", "b"]},
        {"action": "hotkey", "keys": ["ctrl", "end"]},
        {"action": "hotkey", "keys": ["ctrl", "s"]},
        {"action": "type_into_element", "text": "report.docx"},
    ]
    results = [
        {"ok": True}, {"ok": True}, {"ok": True}, {"ok": True},
        {"ok": True}, {"ok": False},
    ]

    assert _document_format_prefix_ok(actions, results) is True
    assert _document_format_prefix_ok(actions, results[:3]) is False


def test_subgoal_formats_focus_central_document_surface_before_shortcuts(monkeypatch):
    from winpilot.fast import subgoal as subgoal_mod
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request
    from winpilot.utils.screenshot import Region

    class Session:
        task = "Format the first line centered"
        hwnd = 42
        snapshot = None

    monkeypatch.setattr(
        subgoal_mod,
        "window_region",
        lambda _hwnd: Region(left=100, top=200, width=1000, height=800),
    )
    session = Session()
    request = parse_subgoal_request({
        "subgoal": "Format the heading",
        "target_window": {"hwnd": 42},
        "formats": [{"range": "first_line", "alignment": "center"}],
        "constraints": {"wait": False},
    }, task=session.task)

    actions = SubgoalExecutor(session, FastGuiController(session))._build_actions(request)

    assert actions[0] == {
        "action": "click",
        "x": 600,
        "y": 664,
        "button": "left",
        "physical": True,
        "skip_auto_observe": True,
        "context": "focus central editable document surface before formatting",
    }
    assert actions[1:4] == [
        {"action": "hotkey", "keys": ["ctrl", "home"]},
        {"action": "hotkey", "keys": ["shift", "end"]},
        {"action": "hotkey", "keys": ["ctrl", "e"]},
    ]


def test_runner_upgrades_low_level_title_click_to_full_format_routine():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        "Make the first line bold and centered and the remaining body left aligned"
    )
    upgraded = runner._maybe_upgrade_execute_subgoal_formats(
        "execute_subgoal",
        {
            "subgoal": "Select the first line of text",
            "click": {"label": "title line"},
            "target_window": {"hwnd": 42},
        },
    )

    assert upgraded["target_window"] == {"hwnd": 42}
    assert upgraded["formats"] == [
        {"range": "first_line", "bold": True, "alignment": "center"},
        {"range": "body_after_first_line", "alignment": "left"},
    ]
    assert "click" not in upgraded


def test_runner_upgrades_chinese_first_paragraph_click_to_full_format_routine():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        "将第一段加粗并居中，将后三段左对齐且不加粗，然后保存"
    )
    upgraded = runner._maybe_upgrade_execute_subgoal_formats(
        "execute_subgoal",
        {
            "subgoal": "点击第一段文字以定位光标",
            "click": {"label": "办公自动化验收报告"},
            "target_window": {"hwnd": 42},
        },
    )

    assert upgraded["formats"] == [
        {"range": "first_line", "bold": True, "alignment": "center"},
        {"range": "body_after_first_line", "alignment": "left"},
    ]
    assert upgraded["constraints"] == {"wait": True, "save_after": True}


def test_runner_replaces_model_format_batch_with_semantic_document_routine():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        "Make the first paragraph bold and centered. Make the remaining paragraphs left aligned. Save it."
    )
    original_actions = [
        {"action": "click", "params": {"x": 1273, "y": 457}},
        {"action": "hotkey", "params": {"keys": ["control", "b"]}},
    ]
    upgraded = runner._maybe_upgrade_execute_subgoal_formats(
        "run_action_batch",
        {"subgoal": "格式化第一段文字：加粗并居中", "actions": original_actions},
    )

    assert upgraded["semantic_override"] == "document_format"
    assert upgraded["actions"] == original_actions
    assert upgraded["formats"] == [
        {"range": "first_line", "bold": True, "alignment": "center"},
        {"range": "body_after_first_line", "alignment": "left"},
    ]


def test_runner_never_rewrites_spreadsheet_row_formatting_as_rich_text_document_formatting():
    from winpilot.agent.loop import AgentRunner

    task = (
        "Enter a table into cells A1:D7. Make row 1 bold and row 5 bold. "
        "Finally verify the workbook title contains report.xlsx."
    )
    runner = AgentRunner(task)
    original = {
        "subgoal": "Click cell A1 to focus the spreadsheet grid",
        "click": {"label": "A1", "expected_region": "center"},
        "target_window": {"hwnd": 42},
    }

    assert runner._document_format_specs(task) == []
    assert runner._maybe_upgrade_execute_subgoal_formats("execute_subgoal", original) == original


def test_runner_collapses_cell_by_cell_spreadsheet_batch_to_semantic_table_override():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        "Enter this exact table into cells A1:D2:\nName\tQty\tPrice\tTotal\nKeyboard\t2\t199\t=B2*C2"
    )
    actions = [
        {"action": "click", "x": 100, "y": 200},
        {"action": "type_text", "text": "Name"},
        {"action": "hotkey", "keys": ["Tab"]},
        {"action": "type_text", "text": "Qty"},
        {"action": "hotkey", "keys": ["Tab"]},
        {"action": "type_text", "text": "Price"},
        {"action": "hotkey", "keys": ["Tab"]},
        {"action": "type_text", "text": "Total"},
    ]

    upgraded = runner._maybe_upgrade_spreadsheet_action_batch(
        "run_action_batch",
        {"subgoal": "enter all table data row by row", "actions": actions},
    )

    assert upgraded["semantic_override"] == "spreadsheet_table"
    assert upgraded["actions"] == actions

    format_upgraded = runner._maybe_upgrade_spreadsheet_action_batch(
        "run_action_batch",
        {
            "subgoal": "Make row 1 bold and row 5 bold",
            "actions": [{"action": "hotkey", "keys": ["ctrl", "b"]}],
        },
    )
    assert format_upgraded["semantic_override"] == "spreadsheet_row_format"


def test_runner_advances_repeated_spreadsheet_format_request_to_exact_save():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        "Make row 1 bold and row 5 bold. Save As exactly "
        r"C:\Users\zou\Desktop\spreadsheet-runtime-test.xlsx"
    )
    runner.session._spreadsheet_bold_rows_applied = {1, 5}

    upgraded = runner._maybe_upgrade_spreadsheet_action_batch(
        "run_action_batch",
        {
            "subgoal": "Make row 1 bold and row 5 bold",
            "actions": [{"action": "hotkey", "keys": ["ctrl", "b"]}],
        },
    )

    assert upgraded["semantic_override"] == "exact_save_as"
    assert upgraded["constraints"]["open_save_as"] is True
    assert upgraded["constraints"]["save_path"].endswith("spreadsheet-runtime-test.xlsx")


def test_run_action_batch_semantic_format_override_uses_subgoal_executor(monkeypatch):
    from winpilot.agent import fast_tools
    from winpilot.agent.fast_tools import FastToolMixin
    from winpilot.agent.tools import ToolResult

    captured = {}

    class FakeExecutor:
        def __init__(self, session, controller):
            captured["session"] = session
            captured["controller"] = controller

        def execute(self, request):
            captured["request"] = request
            return ToolResult('{"ok": true, "format_applied": true}')

    class Session(FastToolMixin):
        task = "format document"

        def _fast_controller(self):
            return "controller"

    monkeypatch.setattr(fast_tools, "SubgoalExecutor", FakeExecutor)
    result = Session()._t_run_action_batch({
        "actions": [{"action": "hotkey", "keys": ["ctrl", "b"]}],
        "subgoal": "format heading",
        "formats": [{"range": "first_line", "bold": True}],
        "constraints": {"save_after": True},
        "semantic_override": "document_format",
        "target_window": {"hwnd": 42},
    })

    assert '"format_applied": true' in result.text
    assert captured["request"] == {
        "subgoal": "format heading",
        "formats": [{"range": "first_line", "bold": True}],
        "constraints": {"save_after": True},
        "target_window": {"hwnd": 42},
    }


def test_run_action_batch_semantic_spreadsheet_override_uses_subgoal_executor(monkeypatch):
    from winpilot.agent import fast_tools
    from winpilot.agent.fast_tools import FastToolMixin
    from winpilot.agent.tools import ToolResult

    captured = {}

    class FakeExecutor:
        def __init__(self, session, controller):
            captured["session"] = session
            captured["controller"] = controller

        def execute(self, request):
            captured["request"] = request
            return ToolResult('{"ok": true, "spreadsheet_table_applied": true}')

    class Session(FastToolMixin):
        task = "enter table into cells A1:D2"

        def _fast_controller(self):
            return "controller"

    monkeypatch.setattr(fast_tools, "SubgoalExecutor", FakeExecutor)
    result = Session()._t_run_action_batch({
        "actions": [{"action": "type_text", "text": "Name"}],
        "subgoal": "enter all table data row by row",
        "semantic_override": "spreadsheet_table",
        "target_window": {"hwnd": 42},
    })

    assert '"spreadsheet_table_applied": true' in result.text
    assert captured["request"]["click"] == {
        "label": "A1",
        "expected_region": "spreadsheet grid",
    }
    assert captured["request"]["target_window"] == {"hwnd": 42}


def test_runner_does_not_rewrite_new_document_navigation_as_formatting():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        "Create a blank document, type a report, then make the first line bold and centered"
    )
    original = {
        "subgoal": "Click the New document tab to create a blank document",
        "click": {"label": "New document", "expected_region": "top"},
        "target_window": {"hwnd": 42},
    }

    upgraded = runner._maybe_upgrade_execute_subgoal_formats("execute_subgoal", original)

    assert upgraded == original


def test_runner_does_not_rewrite_plain_document_focus_as_formatting():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        "Type four exact lines, then make the first line bold and centered and save the document"
    )
    original = {
        "subgoal": "Click the document body to place the insertion caret",
        "click": {"label": "Document", "expected_region": "center"},
        "target_window": {"hwnd": 42},
    }

    assert runner._maybe_upgrade_execute_subgoal_formats("execute_subgoal", original) == original


def test_runner_rewrites_formatting_tab_navigation_to_semantic_routine():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        "Make the first paragraph bold and centered and the remaining paragraphs left aligned"
    )
    upgraded = runner._maybe_upgrade_execute_subgoal_formats(
        "execute_subgoal",
        {
            "subgoal": "Click the Home tab to access formatting tools",
            "click": {"label": "开始", "role": "tab"},
            "target_window": {"hwnd": 42},
        },
    )

    assert upgraded["formats"] == [
        {"range": "first_line", "bold": True, "alignment": "center"},
        {"range": "body_after_first_line", "alignment": "left"},
    ]


def test_runner_does_not_treat_literal_input_word_format_as_format_command():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        "Type a report containing the literal 已完成：输入、格式化与状态核验, then format the heading"
    )
    original = {
        "subgoal": "点击文档编辑区并输入四段文字：已完成：输入、格式化与状态核验",
        "click": {"label": "文档编辑区"},
        "target_window": {"hwnd": 42},
    }

    upgraded = runner._maybe_upgrade_execute_subgoal_formats("execute_subgoal", original)

    assert upgraded == original


def test_runner_upgrades_natural_language_document_input_to_exact_batch():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        """Use WPS and type exactly these four paragraphs, one paragraph per line:
办公自动化验收报告
测试标识：OFFICE_GUI_712
已完成：输入、格式化与状态核验
结论：真实文件保存成功
Make the first paragraph bold and centered. Save the file."""
    )
    upgraded = runner._maybe_upgrade_execute_subgoal_document_input(
        "execute_subgoal",
        {
            "subgoal": "点击文档编辑区并输入四段文字",
            "click": {"label": "文档编辑区"},
            "target_window": {"hwnd": 42},
        },
    )

    assert upgraded["input"] == {
        "text": "办公自动化验收报告\n测试标识：OFFICE_GUI_712\n已完成：输入、格式化与状态核验\n结论：真实文件保存成功",
        "document_surface": True,
        "clear": True,
    }
    assert upgraded["target_window"] == {"hwnd": 42}
    assert "OFFICE_GUI_712" in "\n".join(upgraded["verify_texts"])


def test_runner_rewrites_new_click_when_blank_document_is_already_ready():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        """Use WPS Writer and type exactly these paragraphs:
Research report
SOURCE_TOKEN_712
Make the first paragraph bold and centered. Save the file."""
    )
    runner.session._new_blank_applied = True

    upgraded = runner._maybe_upgrade_execute_subgoal_document_input(
        "execute_subgoal",
        {
            "subgoal": "Click New or the document title to create a blank document",
            "click": {"label": "New"},
            "target_window": {"hwnd": 42},
        },
    )

    assert upgraded["input"]["text"] == "Research report\nSOURCE_TOKEN_712"
    assert upgraded["input"]["document_surface"] is True
    assert "click" not in upgraded
    assert upgraded["target_window"] == {"hwnd": 42}


def test_document_routine_creates_surface_first_when_writer_home_is_known():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request

    class Session:
        task = """Type exactly these paragraphs:
Heading
Body TOKEN_712
Make the first paragraph bold and centered."""
        hwnd = 42
        snapshot = None
        _document_surface_ready = False

    session = Session()
    request = parse_subgoal_request({
        "subgoal": "Enter the exact content into the document",
        "input": {"text": "Heading\nBody TOKEN_712", "document_surface": True},
        "target_window": {"hwnd": 42},
    }, task=session.task)

    actions = SubgoalExecutor(session, FastGuiController(session))._document_task_actions(request)

    assert actions[0] == {"action": "ensure_document_surface", "max_attempts": 3}
    assert any(action.get("action") == "type_text" for action in actions)


def test_runner_rewrites_model_document_body_input_and_strips_premature_format():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        """Type exactly these two paragraphs:
Heading
Body TOKEN_712
Make the first paragraph bold and centered. Save the file."""
    )
    upgraded = runner._maybe_upgrade_execute_subgoal_document_input(
        "execute_subgoal",
        {
            "subgoal": "Type the paragraphs and format them",
            "input": {"text": "Heading\nBody TOKEN_712", "label": "Document body"},
            "format": {"range": "all", "alignment": "left"},
            "target_window": {"hwnd": 42},
        },
    )

    assert upgraded["input"]["document_surface"] is True
    assert upgraded["input"]["text"] == "Heading\nBody TOKEN_712"
    assert "format" not in upgraded
    assert "formats" not in upgraded


def test_runner_preserves_explicit_document_append_instead_of_auto_replacing():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("""Type exactly these paragraphs:
Heading
Body TOKEN_KEEP_712""")
    original = {
        "subgoal": "Append one note without replacing existing content",
        "input": {
            "label": "Document body",
            "text": "one extra note",
            "document_surface": True,
            "clear": False,
        },
    }

    upgraded = runner._maybe_upgrade_execute_subgoal_document_input(
        "execute_subgoal", original,
    )

    assert upgraded == original
    assert upgraded["input"]["clear"] is False


def test_runner_upgrades_multiline_document_input_despite_terminal_punctuation_drift():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        """Type exactly these paragraphs:
WPS controller report
TOKEN_20260712
GUI save completed.
Make the first paragraph bold and centered."""
    )
    runner.session._document_surface_ready = False

    upgraded = runner._maybe_upgrade_execute_subgoal_document_input(
        "execute_subgoal",
        {
            "subgoal": "Type the three paragraphs into the document",
            "input": {
                "text": "WPS controller report\nTOKEN_20260712\nGUI save completed",
            },
        },
    )

    assert upgraded["input"]["document_surface"] is True
    assert upgraded["input"]["text"].endswith("GUI save completed.")


def test_runner_extracts_document_body_when_first_paragraph_follows_colon_inline():
    from winpilot.agent.loop import AgentRunner

    task = """Open Writer. Type exactly these three paragraphs, one per line: Heading
TOKEN_INLINE_712
Final sentence. Make only the first paragraph bold and centered. Save As C:\\tmp\\x.docx."""
    runner = AgentRunner(task)

    assert runner._exact_document_text_block(task) == (
        "Heading\nTOKEN_INLINE_712\nFinal sentence."
    )


def test_document_routine_refreshes_stale_ready_state_from_current_home_title(monkeypatch):
    from winpilot.fast import subgoal as subgoal_mod
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request

    class Session:
        task = """Type exactly these paragraphs:
Heading
Body TOKEN_712"""
        hwnd = 42
        snapshot = None
        _document_surface_ready = True

    monkeypatch.setattr(subgoal_mod.win32, "_window_text", lambda _hwnd: "文档 - WPS Office")
    session = Session()
    request = parse_subgoal_request({
        "subgoal": "Type the exact paragraphs",
        "input": {"text": "Heading\nBody TOKEN_712", "document_surface": True},
    }, task=session.task)

    actions = SubgoalExecutor(session, FastGuiController(session))._document_task_actions(request)

    assert actions[0] == {"action": "ensure_document_surface", "max_attempts": 3}
    assert session._document_surface_ready is False


def test_document_surface_input_routes_to_full_document_task_batch(monkeypatch):
    from winpilot.fast import subgoal as subgoal_mod
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request

    class Session:
        task = """Type exactly these paragraphs: Heading
Body TOKEN_FULL_712. Make only the first paragraph bold and centered; keep body left aligned. Save As C:\\Users\\zou\\Desktop\\full.docx."""
        hwnd = 42
        snapshot = None
        _document_surface_ready = True
        _exact_save_paths_applied = set()

    monkeypatch.setattr(subgoal_mod.win32, "_window_text", lambda _hwnd: "文档 - WPS Office")
    session = Session()
    request = parse_subgoal_request({
        "subgoal": "Enter the exact document text",
        "input": {
            "text": "Heading\nBody TOKEN_FULL_712.",
            "document_surface": True,
            "clear": True,
        },
    }, task=session.task)

    actions = SubgoalExecutor(session, FastGuiController(session))._build_actions(request)

    assert actions[0] == {"action": "ensure_document_surface", "max_attempts": 3}
    assert any(action.get("keys") == ["ctrl", "b"] for action in actions)
    assert any(action.get("action") == "type_text" and "TOKEN_FULL_712" in action.get("text", "") for action in actions)
    assert any(
        action.get("action") == "hotkey"
        and action.get("keys") == ["enter"]
        and action.get("expect", {}).get("file_exists", "").endswith("full.docx")
        for action in actions
    )


def test_exact_document_controller_respects_explicit_append_semantics(monkeypatch):
    from winpilot.fast import subgoal as subgoal_mod
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request
    from winpilot.utils.screenshot import Region

    class Session:
        task = """Type exactly these paragraphs:
Heading
Body TOKEN_KEEP_712"""
        hwnd = 42
        snapshot = None
        _document_surface_ready = True

    monkeypatch.setattr(
        subgoal_mod,
        "window_region",
        lambda _hwnd: Region(left=100, top=200, width=1000, height=800),
    )
    session = Session()
    request = parse_subgoal_request({
        "subgoal": "Append one note without replacing the existing document",
        "input": {
            "text": "one extra note",
            "document_surface": True,
            "clear": False,
        },
    }, task=session.task)
    executor = SubgoalExecutor(session, FastGuiController(session))

    assert executor._document_task_actions(request) == []
    actions = executor._build_actions(request)

    assert any(
        action.get("action") == "type_text"
        and action.get("text") == "one extra note"
        for action in actions
    )
    assert not any(
        action.get("action") == "hotkey"
        and set(action.get("keys") or ()) & {"a", "backspace", "delete"}
        for action in actions
    )


def test_ensure_document_surface_retries_locally_until_editor_chrome_is_visible(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import controller as controller_mod
    from winpilot.fast.controller import FastGuiController

    class Session:
        hwnd = 42

    session = Session()
    controller = FastGuiController(session)
    reads = iter([
        "新建\n最近\n打开",
        "正在新建文档",
        "开始\n插入\n页面：1/1\n字数：0",
    ])
    hotkeys = []
    clicks = []

    def dispatch(name, args):
        if name == "read_text":
            return ToolResult(next(reads))
        if name == "hotkey":
            hotkeys.append(args["keys"])
            return ToolResult('{"ok": true}')
        raise AssertionError(name)

    monkeypatch.setattr(controller, "_dispatch", dispatch)
    monkeypatch.setattr(
        controller,
        "_click_element",
        lambda args: clicks.append(args) or ToolResult('{"ok": true}'),
    )
    monkeypatch.setattr(controller, "_wait_change", lambda _args: ToolResult('{"ok": true}'))
    monkeypatch.setattr(controller, "_wait_stable", lambda _args: ToolResult('{"ok": true}'))
    titles = iter(["文档 - WPS Office", "新标签页 - WPS Office", "文字文稿2 - WPS Office"])
    monkeypatch.setattr(controller_mod.win32gui, "GetWindowText", lambda _hwnd: next(titles))

    payload = json.loads(controller._ensure_document_surface({"max_attempts": 3}).text)

    assert payload["verified"] is True
    assert payload["attempts"] == 3
    assert len(clicks) == 1
    assert clicks[0]["label"] == "新建"
    assert hotkeys == [["ctrl", "n"]]
    assert session._document_surface_ready is True


def test_execute_subgoal_blocks_clicking_grounded_nonblocking_notice():
    from winpilot.agent.fast_tools import FastToolMixin
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    class Session(FastToolMixin):
        snapshot = Snapshot(
            hwnd=42,
            title="report.docx - WPS Office",
            region=Region(0, 0, 1920, 1020),
            elements=[
                UIElement(elem_id=1, source="uia", role="window", text="report.docx - WPS Writer", cx=960, cy=530),
                UIElement(elem_id=2, source="uia", role="text", text="上一次未正常关闭，已恢复到最新版本。", cx=900, cy=150),
                UIElement(elem_id=3, source="uia", role="button", text="发送报告", cx=1700, cy=150),
            ],
        )

    payload = json.loads(Session()._t_execute_subgoal({
        "subgoal": "Dismiss the recovery banner",
        "click": {"label": "发送报告"},
    }).text)

    assert payload["blocked"] is True
    assert payload["non_blocking_notice_click_blocked"] is True
    assert payload["needs_llm_reason"] is False


def test_clipboard_placeholder_replace_compiles_to_one_controller_batch(monkeypatch):
    from winpilot.fast import subgoal as subgoal_mod
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request

    class Session:
        task = "replace the first placeholder with the copied browser URL"
        hwnd = 42
        snapshot = None

    monkeypatch.setattr(subgoal_mod.win32, "_window_text", lambda _hwnd: "report.docx - Writer")
    session = Session()
    request = parse_subgoal_request({
        "subgoal": "Use Find and Replace to replace [[MASTER_URL]] with the clipboard content",
        "click": {"label": "File"},
        "target_window": {"hwnd": 42},
    }, task=session.task)

    actions = SubgoalExecutor(session, FastGuiController(session))._build_actions(request)
    names = [action["action"] for action in actions]

    assert names[:3] == ["hotkey", "wait_stable", "observe"]
    assert names.count("type_into_element") == 1
    assert any(action.get("keys") == ["ctrl", "v"] for action in actions)
    assert any(action.get("label") == "全部替换" for action in actions)
    assert all(action.get("label") != "File" for action in actions)
    assert request.constraints["clipboard_replace_token"] == "[[MASTER_URL]]"


def test_find_replace_subgoal_inherits_clipboard_constraint_from_task():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request

    class Session:
        task = "Copy the browser URL and replace only [[MASTER_URL]] with the copied clipboard text"
        hwnd = 42
        snapshot = None

    session = Session()
    request = parse_subgoal_request({
        "subgoal": "Open Find and Replace to locate [[MASTER_URL]]",
        "click": {"label": "Find and Replace"},
    }, task=session.task)

    actions = SubgoalExecutor(session, FastGuiController(session))._build_actions(request)

    assert actions[0]["action"] == "hotkey"
    assert actions[0]["keys"] == ["ctrl", "h"]
    assert any(action.get("keys") == ["ctrl", "v"] for action in actions)


def test_runner_upgrades_repeated_editor_focus_from_pending_clipboard_replace_plan():
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.planner import Plan

    runner = AgentRunner("copy a browser URL into the matching document placeholder")
    runner.session.plan = Plan([
        "Copy the current source URL from the browser",
        "Paste and replace [[MASTER_URL]] with the copied URL",
        "Save the document",
    ])
    runner.session.plan.complete(1)
    original = {
        "subgoal": "Click into the document editing area",
        "actions": [{"action": "click", "x": 960, "y": 600}],
        "target_window": {"hwnd": 42},
    }

    upgraded = runner._maybe_upgrade_pending_clipboard_replace(
        "run_action_batch",
        original,
    )

    assert upgraded["semantic_override"] == "clipboard_replace"
    assert "[[MASTER_URL]]" in upgraded["subgoal"]
    assert upgraded["target_window"] == {"hwnd": 42}


def test_runner_recovers_clipboard_delivery_without_an_explicit_plan():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        "Copy browser values into [[FIRST_VALUE]] and [[SECOND_VALUE]] in the document"
    )
    runner._tool_history.extend([
        ("hotkey", {"keys": ["c", "ctrl"]}, '{"ok": true}'),
        (
            "execute_subgoal",
            {"subgoal": "replace [[FIRST_VALUE]]"},
            '{"ok": true, "clipboard_replace_applied": true}',
        ),
        ("hotkey", {"keys": ["ctrl", "c"]}, '{"ok": true}'),
    ])

    upgraded = runner._maybe_upgrade_pending_clipboard_replace(
        "execute_subgoal",
        {
            "subgoal": "Click Find and Replace in the document editor",
            "click": {"label": "Find and Replace"},
            "target_window": {"hwnd": 77},
        },
    )

    assert "[[SECOND_VALUE]]" in upgraded["subgoal"]
    assert "current clipboard content" in upgraded["subgoal"]
    assert upgraded["target_window"] == {"hwnd": 77}


def test_runner_recovers_clipboard_delivery_from_a_batch_copy_action():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("Copy browser text into [[TARGET_SLOT]] in the document")
    runner._tool_history.append((
        "run_action_batch",
        {
            "subgoal": "select and copy the browser address",
            "actions": [
                {"action": "hotkey", "params": {"keys": ["l", "ctrl"]}},
                {"action": "hotkey", "keys": ["c", "control"]},
            ],
        },
        '{"ok": true}',
    ))

    upgraded = runner._maybe_upgrade_pending_clipboard_replace(
        "run_action_batch",
        {
            "subgoal": "Find TARGET_SLOT in the document",
            "actions": [{"action": "click", "element_id": 11}],
        },
    )

    assert upgraded["semantic_override"] == "clipboard_replace"
    assert "[[TARGET_SLOT]]" in upgraded["subgoal"]


def test_runner_triggers_pending_clipboard_delivery_after_destination_focus(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tools import ToolResult

    runner = AgentRunner(
        "Copy the browser value into [[TARGET_SLOT]] in Report_Target_42.docx"
    )
    runner._tool_history.append((
        "hotkey",
        {"keys": ["c", "ctrl"]},
        '{"ok": true}',
    ))
    dispatched = []

    def fake_dispatch(tool_name, args):
        dispatched.append((tool_name, args))
        return ToolResult('{"ok": true, "clipboard_replace_applied": true}')

    monkeypatch.setattr(runner, "_dispatch_with_fast_visual_wrap", fake_dispatch)

    result = runner._maybe_deliver_clipboard_after_focus(
        "focus_window",
        {"hwnd": 99},
        ToolResult(json.dumps({
            "ok": True,
            "wrapped_by_fast_controller": True,
            "results": [{
                "action": "focus_window",
                "data": {
                    "ok": True,
                    "hwnd": 99,
                    "title": "Report_Target_42.docx - Editor",
                },
            }],
        })),
    )

    assert dispatched[0][0] == "run_action_batch"
    assert "[[TARGET_SLOT]]" in dispatched[0][1]["subgoal"]
    assert dispatched[0][1]["semantic_override"] == "clipboard_replace"
    assert json.loads(result.text)["auto_triggered_after_focus"] is True


def test_runner_blocks_repeating_applied_clipboard_delivery_without_a_new_copy():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("Copy browser text into [[TARGET_SLOT]] in Report.docx")
    runner._tool_history.extend([
        ("hotkey", {"keys": ["ctrl", "c"]}, '{"ok": true, "verified": true}'),
        (
            "focus_window",
            {"hwnd": 99},
            '{"ok": true, "clipboard_replace_applied": true, "mutation_applied": true}',
        ),
    ])

    blocked = runner._duplicate_clipboard_delivery_result(
        "run_action_batch",
        {
            "subgoal": "Replace [[TARGET_SLOT]] with clipboard content again",
            "actions": [{"action": "hotkey", "keys": ["ctrl", "v"]}],
        },
    )

    assert blocked is not None
    payload = json.loads(blocked.text)
    assert payload["duplicate_clipboard_delivery"] is True
    assert payload["mutation_applied"] is True


def test_verified_clipboard_delivery_completes_matching_plan_item_across_languages():
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.planner import Plan
    from winpilot.agent.tools import ToolResult

    runner = AgentRunner("Replace [[MASTER_URL]] in Report.docx")
    runner.session.plan = Plan([
        "在浏览器中复制实际地址",
        "切换到 Report.docx",
        "将 [[MASTER_URL]] 替换为剪贴板内容",
        "保存文档",
    ])
    runner.session.plan.complete(1)
    runner.session.plan.complete(2)
    result = ToolResult(json.dumps({
        "ok": True,
        "verified": True,
        "auto_triggered_after_focus": True,
        "clipboard_replace_applied": True,
        "clipboard_replace_token": "[[MASTER_URL]]",
        "replacement_count": 1,
        "subgoal": "Use GUI Find and Replace",
    }))

    updated = runner._maybe_auto_complete_verified_subgoal_plan_items(
        "focus_window",
        {"hwnd": 99},
        result,
    )

    assert runner.session.plan.steps[2]["done"] is True
    assert json.loads(updated.text)["auto_completed_plan_items"][0]["source"] == (
        "verified_clipboard_delivery"
    )


def test_clipboard_targets_exclude_literals_explicitly_kept_unchanged():
    from winpilot.agent.loop import _clipboard_target_tokens

    task = (
        "Replace only [[MASTER_URL]] with the copied value. "
        "Keep [[ANYWHERE_URL]] and [[AUDIT_TOKEN]] unchanged."
    )

    assert _clipboard_target_tokens(task) == ["[[MASTER_URL]]"]


def test_repeated_copy_of_same_clipboard_value_does_not_open_another_delivery():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("Replace [[TARGET_SLOT]] in Report.docx; keep [[PROTECTED]] unchanged")
    copy_result = json.dumps({
        "ok": True,
        "verified": True,
        "clipboard_text_fingerprint": "same-value-fingerprint",
    })
    runner._tool_history.extend([
        ("hotkey", {"keys": ["ctrl", "c"]}, copy_result),
        ("hotkey", {"keys": ["ctrl", "c"]}, copy_result),
        (
            "focus_window",
            {"hwnd": 99},
            json.dumps({
                "ok": True,
                "verified": True,
                "clipboard_replace_applied": True,
                "clipboard_replace_token": "[[TARGET_SLOT]]",
            }),
        ),
    ])
    original = {
        "subgoal": "Click the document body",
        "click": {"label": "document body"},
    }

    assert runner._maybe_upgrade_pending_clipboard_replace(
        "execute_subgoal", original
    ) == original


def test_batch_action_params_wrapper_and_reversed_hotkey_are_normalized():
    from winpilot.fast.action_contract import validate_batch_actions
    from winpilot.fast.controller import _normalise_action, _stable_batch_actions

    actions = [
        {"action": "click", "params": {"x": 911, "y": 79, "button": "left"}},
        {"action": "hotkey", "params": {"keys": ["l", "ctrl"]}},
    ]

    assert validate_batch_actions(actions) == []
    assert _normalise_action(actions[0]) == (
        "click",
        {"x": 911, "y": 79, "button": "left"},
    )
    assert _stable_batch_actions(actions, "copy address")[-1] == {
        "action": "hotkey",
        "keys": ["ctrl", "l"],
    }


def test_standalone_hotkey_canonicalizes_reversed_modifier_order():
    from winpilot.agent.interaction_tools import canonical_hotkey_order

    assert canonical_hotkey_order(["l", "ctrl"]) == ["ctrl", "l"]
    assert canonical_hotkey_order(["c", "control"]) == ["ctrl", "c"]
    assert canonical_hotkey_order(["down", "home"]) == ["down", "home"]


def test_verify_only_literals_do_not_trigger_document_input_or_format_rewrite():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        """Type exactly these two paragraphs:
Heading
已完成：输入、格式化与状态核验
Make the heading bold and centered. Save the file."""
    )
    original = {
        "subgoal": "Verify file content, formatting, and window title",
        "verify_texts": ["Heading", "已完成：输入、格式化与状态核验", "report.docx"],
    }

    after_input = runner._maybe_upgrade_execute_subgoal_document_input(
        "execute_subgoal",
        original,
    )
    after_format = runner._maybe_upgrade_execute_subgoal_formats(
        "execute_subgoal",
        after_input,
    )

    assert after_input == original
    assert after_format == original


def test_input_locator_rejects_focused_top_level_window_as_text_field():
    from winpilot.fast.input_locator import locate_input
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    region = Region(left=100, top=100, width=1000, height=700)
    snapshot = Snapshot(
        hwnd=42,
        title="Document",
        region=region,
        elements=[UIElement(
            elem_id=1,
            source="uia",
            role="window",
            text="Document - Office",
            cx=600,
            cy=450,
            left=100,
            top=100,
            width=1000,
            height=700,
            states=("focused",),
        )],
    )

    result = locate_input(snapshot, label="Document body")

    assert result.found is False


def test_document_surface_input_builds_physical_focus_clear_type_batch(monkeypatch):
    from winpilot.fast import subgoal as subgoal_mod
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request
    from winpilot.utils.screenshot import Region

    class Session:
        task = "Type exact paragraphs into the document"
        hwnd = 42
        snapshot = None

    monkeypatch.setattr(
        subgoal_mod,
        "window_region",
        lambda _hwnd: Region(left=100, top=200, width=1000, height=800),
    )
    session = Session()
    request = parse_subgoal_request({
        "subgoal": "Enter exact text",
        "target_window": {"hwnd": 42},
        "input": {"text": "Heading\nBody", "document_surface": True, "clear": True},
        "verify_texts": ["Heading", "Body"],
        "constraints": {"wait": True},
    }, task=session.task)

    actions = SubgoalExecutor(session, FastGuiController(session))._build_actions(request)

    assert [item["action"] for item in actions[:4]] == ["click", "hotkey", "hotkey", "type_text"]
    assert actions[1]["keys"] == ["ctrl", "a"]
    assert actions[2]["keys"] == ["backspace"]
    assert actions[3]["text"] == "Heading\nBody"


def test_natural_url_subgoal_compiles_address_input_and_enter():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    class Session:
        task = "Open Chrome and navigate to https://example.com through the address bar"
        hwnd = 42
        snapshot = Snapshot(
            hwnd=42,
            title="New Tab - Google Chrome",
            region=Region(0, 0, 1200, 800),
            elements=[
                UIElement(
                    elem_id=7,
                    source="uia",
                    role="edit",
                    text="Search Google or type a URL",
                    cx=600,
                    cy=55,
                    left=220,
                    top=35,
                    width=760,
                    height=40,
                )
            ],
        )

    request = parse_subgoal_request({
        "subgoal": "Click the address bar and type https://example.com then press Enter to navigate",
        "click": {
            "label": "address bar",
            "role": "text",
            "expected_region": "top of Chrome window",
            "fallback_text": ["address bar", "omnibox"],
        },
        "target_window": {"hwnd": 42},
    }, task=Session.task)
    actions = SubgoalExecutor(Session(), FastGuiController(Session()))._build_actions(request)

    assert request.click == {}
    assert request.clicks == []
    assert request.input == {
        "label": "address bar",
        "expected_region": "top of Chrome window",
        "fallback_text": ["address bar", "omnibox"],
        "text": "https://example.com",
        "clear": True,
        "role": "edit",
        "navigation": True,
    }
    assert request.constraints["submit_with_enter"] is True
    assert request.verify_texts == []
    assert actions[0]["action"] == "type_into_element"
    assert actions[0]["text"] == "https://example.com"
    assert actions[1] == {"action": "hotkey", "keys": ["enter"]}
    assert [action["action"] for action in actions[2:5]] == [
        "wait_change",
        "wait_stable",
        "read_text",
    ]


def test_explicit_address_input_and_duplicate_click_compile_to_one_submit_routine():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    class Session:
        task = "Search for two product pages in Chrome"
        hwnd = 42
        snapshot = Snapshot(
            hwnd=42,
            title="New Tab - Google Chrome",
            region=Region(0, 0, 1200, 800),
            elements=[UIElement(
                elem_id=7,
                source="uia",
                role="edit",
                text="Search Google or type a URL",
                cx=600,
                cy=55,
                left=220,
                top=35,
                width=760,
                height=40,
            )],
        )

    request = parse_subgoal_request({
        "subgoal": "Type the product search query into the address bar and press Enter",
        "input": {
            "label": "Address bar",
            "text": "Logitech MX Master 3S official product page",
            "expect_text": True,
        },
        "click": {
            "label": "Address bar",
            "role": "edit",
            "expected_region": "top",
        },
    }, task=Session.task)
    actions = SubgoalExecutor(Session(), FastGuiController(Session()))._build_actions(request)

    assert request.click == {}
    assert request.clicks == []
    assert request.input["navigation"] is True
    assert request.constraints["submit_with_enter"] is True
    assert [action["action"] for action in actions[:5]] == [
        "type_into_element",
        "hotkey",
        "wait_change",
        "wait_stable",
        "read_text",
    ]
    assert actions[1]["keys"] == ["enter"]
    assert sum(action["action"] == "click_element" for action in actions) == 0


def test_navigation_evidence_requires_changed_page_and_final_address_identity():
    from winpilot.fast.subgoal import _navigation_evidence, parse_subgoal_request

    request = parse_subgoal_request({
        "subgoal": "Navigate to the official product page",
        "input": {
            "label": "Address bar",
            "text": "https://www.logitech.com/products/mice/mx-master-3s.html",
        },
    })
    batch = {
        "results": [
            {"action": "wait_change", "data": {"changed": True}},
            {
                "action": "read_text",
                "full_text": (
                    "Product - Google Chrome\n"
                    "Address and search bar=logicool.co.jp/ja-jp/shop/p/mx-master-3s\n"
                    "MX Master 3S Bluetooth edition"
                ),
            },
        ],
    }

    evidence = _navigation_evidence(request, batch)

    assert evidence["required"] is True
    assert evidence["page_changed"] is True
    assert evidence["identity_matched"] is True
    assert evidence["ok"] is True


def test_navigation_evidence_rejects_old_page_text_that_only_echoes_verify_label():
    from winpilot.fast.subgoal import _navigation_evidence, parse_subgoal_request

    request = parse_subgoal_request({
        "subgoal": "Navigate to the official product page",
        "input": {
            "label": "Address bar",
            "text": "https://www.logitech.com/products/mice/mx-master-3s.html",
        },
    })
    batch = {
        "results": [
            {"action": "wait_change", "data": {"changed": True}},
            {
                "action": "read_text",
                "full_text": (
                    "MX Master 3S search results - Google Chrome\n"
                    "Address and search bar=baidu.com/s?wd=MX+Master+3S"
                ),
            },
        ],
    }

    evidence = _navigation_evidence(request, batch)

    assert evidence["page_changed"] is True
    assert evidence["identity_matched"] is False
    assert evidence["ok"] is False


def test_input_locator_address_intent_excludes_in_page_search_control():
    from winpilot.fast.input_locator import locate_input
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    snapshot = Snapshot(
        hwnd=42,
        title="Product - Google Chrome",
        region=Region(0, 0, 1920, 1020),
        elements=[
            UIElement(
                elem_id=1, source="uia", role="edit",
                text="地址和搜索栏=logitech.com/en-us/shop/p/item",
                cx=910, cy=79, left=198, top=64, width=1427, height=31,
            ),
            UIElement(
                elem_id=2, source="uia", role="button", text="搜索",
                cx=1538, cy=194, left=1447, top=174, width=182, height=40,
            ),
        ],
    )

    result = locate_input(snapshot, label="地址栏", expected_region="top")

    assert result.found is True
    assert result.candidate is not None
    assert result.candidate.element is not None
    assert result.candidate.element.elem_id == 1
    assert "explicit browser location intent" in result.candidate.reason


def test_click_only_browser_address_bar_subgoal_compiles_to_ctrl_l():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request

    class Session:
        task = "Open the browser address bar"
        hwnd = 42
        snapshot = None

    request = parse_subgoal_request({
        "subgoal": "Click the address bar to enter a URL",
        "click": {
            "label": "地址栏",
            "role": "address bar",
            "fallback_text": ["address bar", "搜索或输入网址"],
        },
        "target_window": {"hwnd": 42},
    }, task=Session.task)

    actions = SubgoalExecutor(Session(), FastGuiController(Session()))._build_actions(request)

    assert actions[0] == {"action": "hotkey", "keys": ["ctrl", "l"]}
    assert all(action.get("action") != "click_element" for action in actions)


def test_runner_format_upgrade_fuses_explicit_save_request():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        "Make the first line bold and centered, make the body left aligned, and save with Ctrl+S"
    )
    upgraded = runner._maybe_upgrade_execute_subgoal_formats(
        "execute_subgoal",
        {"subgoal": "Select and format the title", "target_window": {"hwnd": 42}},
    )

    assert upgraded["constraints"] == {"wait": True, "save_after": True}


def test_runner_format_upgrade_fuses_exact_save_as_path():
    from winpilot.agent.loop import AgentRunner

    target = r"C:\Users\zou\Desktop\report.docx"
    runner = AgentRunner(
        f"Make the first paragraph bold and centered, then Save As exactly {target}"
    )
    upgraded = runner._maybe_upgrade_execute_subgoal_formats(
        "execute_subgoal",
        {"subgoal": "Click the first paragraph", "click": {"label": "Heading"}},
    )

    assert upgraded["constraints"] == {
        "wait": True,
        "save_after": True,
        "save_path": target,
    }


def test_runner_format_upgrade_does_not_toggle_existing_bold_off(monkeypatch, tmp_path):
    from winpilot.agent import loop
    from winpilot.agent.loop import AgentRunner

    target = tmp_path / "existing.docx"
    target.write_bytes(b"placeholder")
    monkeypatch.setattr(loop, "_explicit_saved_paths", lambda _task: [target])
    monkeypatch.setattr(
        loop,
        "_docx_document_evidence",
        lambda _path: ("Heading", [{"text": "Heading", "bold_all": True, "alignment": "left"}], ""),
    )
    runner = AgentRunner(
        "Make the first line bold and centered in the existing document, then save"
    )

    upgraded = runner._maybe_upgrade_execute_subgoal_formats(
        "execute_subgoal",
        {"subgoal": "Select and format the title"},
    )

    assert upgraded["formats"][0] == {"range": "first_line", "alignment": "center"}


def test_file_dialog_navigation_runs_before_filename_input_and_refreshes_snapshot():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request

    class Session:
        task = "Save file to desktop"
        hwnd = 1
        snapshot = None

    executor = SubgoalExecutor(Session(), FastGuiController(Session()))
    request = parse_subgoal_request({
        "subgoal": "save to Desktop",
        "target_window": {"hwnd": 1, "title": "Save As"},
        "clicks": [
            {"label": "Desktop", "role": "treeitem", "expected_region": "left"},
            {"label": "File name", "role": "combobox"},
            {"label": "Save", "role": "button"},
        ],
        "input": {"label": "File name", "text": "report.txt"},
    })

    actions = executor._build_actions(request)

    assert [action["action"] for action in actions[:6]] == [
        "click_element",
        "wait_stable",
        "observe",
        "type_into_element",
        "hotkey",
        "wait_stable",
    ]
    assert actions[0]["label"] == "Desktop"
    assert actions[3]["label"] == "File name"
    assert actions[4]["keys"] == ["enter"]


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


def test_subgoal_explicit_expression_compiles_to_semantic_numeric_keypad_sequence():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request

    class Session:
        task = "Open the calculator and calculate 2026*7*12"

    executor = SubgoalExecutor(Session(), FastGuiController(Session()))
    request = parse_subgoal_request({
        "subgoal": "Calculate 2026*7*12",
        "clicks": [
            {"label": "Clear"},
            {"label": "2"}, {"label": "0"}, {"label": "2"}, {"label": "6"},
            {"label": "Multiply by"}, {"label": "7"}, {"label": "Equals"},
            {"label": "Multiply by"}, {"label": "1"}, {"label": "2"},
            {"label": "Equals"},
        ],
        "verify_texts": ["141,82"],
    }, task=Session.task)

    actions = executor._build_actions(request)

    click_labels = [
        action.get("label")
        for action in actions
        if action.get("action") == "click_element"
    ]
    assert click_labels == [
        "Clear", "2", "0", "2", "6", "Multiply by", "7",
        "Multiply by", "1", "2", "Equals",
    ]
    assert all(
        action.get("numeric_keypad_sequence") is True
        for action in actions
        if action.get("action") == "click_element"
    )
    assert [action["action"] for action in actions[-2:]] == ["wait_stable", "read_text"]
    assert not any(action.get("action") == "type_text" for action in actions)


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
    math_actions = executor._build_actions(math_request)
    assert [
        action.get("label")
        for action in math_actions
        if action.get("action") == "click_element"
    ] == ["Clear", "1", "2", "Multiply by", "3", "4", "="]

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
            if name == "focus_window":
                return ToolResult('{"ok": true, "verified": true}')
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


def test_subgoal_arithmetic_fallback_refreshes_full_window_before_keypad_clicks():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    observe_args = []

    class Session:
        task = "Enter 12*34= and verify 408"
        hwnd = 1
        run_id = "run-keypad-refresh"
        snapshot = Snapshot(
            hwnd=1,
            title="Numeric Tool",
            region=Region(0, 0, 500, 400),
            elements=[UIElement(elem_id=1, source="ocr", role="text", text="Display")],
        )

        def dispatch(self, name, args):
            if name == "observe":
                observe_args.append(dict(args))
                labels = ["0", "1", "2", "3", "4", "=", "+"]
                self.snapshot = Snapshot(
                    hwnd=1,
                    title="Numeric Tool",
                    region=Region(0, 0, 500, 400),
                    elements=[
                        UIElement(
                            elem_id=index,
                            source="ocr",
                            role="text",
                            text=label,
                            cx=20 * index,
                            cy=300,
                            left=20 * index - 5,
                            top=290,
                            width=10,
                            height=20,
                        )
                        for index, label in enumerate(labels, start=1)
                    ],
                )
                return ToolResult('{"ok": true}', is_observe=True)
            return ToolResult('{"ok": false, "blocked": true}')

    session = Session()
    executor = SubgoalExecutor(session, FastGuiController(session))
    request = parse_subgoal_request({
        "subgoal": "calculate using the visible controls",
        "input": {"label": "main input", "text": "12*34="},
        "verify_texts": ["408"],
    }, task=Session.task)

    actions = executor._keyboard_fallback_actions(request)

    assert observe_args == [{
        "full": True,
        "force_full": True,
        "include_icons": True,
        "max_elements": 240,
    }]
    assert actions[0]["action"] == "click_element"
    assert actions[0]["label"] == "Clear"
    assert any(
        action.get("action") == "click_element"
        and action.get("label") == "Multiply by"
        for action in actions
    )
    assert actions[-1]["action"] == "read_text"


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


def test_subgoal_target_hwnd_resolves_visible_process_hint(monkeypatch):
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast import subgoal as subgoal_mod

    class Session:
        hwnd = None

    monkeypatch.setattr(
        subgoal_mod.win32,
        "find_app",
        lambda _process: {"windows": [
            {"hwnd": 101, "visible": False},
            {"hwnd": 202, "visible": True},
        ]},
    )

    session = Session()
    executor = subgoal_mod.SubgoalExecutor(session, FastGuiController(session))

    assert executor._target_hwnd(
        subgoal_mod.parse_subgoal_request({"target_window": {"process": "mspaint"}}),
        allow_foreground=False,
    ) == 202
    assert session.hwnd == 202


def test_fast_tool_guard_resolves_visible_process_hint_before_blocking(monkeypatch):
    from winpilot.agent import fast_tools

    class Session:
        hwnd = None

    monkeypatch.setattr(
        fast_tools.win32,
        "find_app",
        lambda _process: {"windows": [{"hwnd": 303, "visible": True}]},
    )
    args = {"target_window": {"process": "example-app"}}
    session = Session()

    assert fast_tools._needs_resolved_target_window(session, args) is False
    assert session.hwnd == 303
    assert args["target_window"]["hwnd"] == 303


def test_subgoal_draw_raises_visible_low_opacity_slider_for_solid_color():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    class Session:
        task = "draw a solid red line"
        snapshot = Snapshot(
            hwnd=7,
            title="Canvas",
            region=Region(100, 100, 800, 600),
            elements=[UIElement(
                source="uia", role="slider", text="Opacity=30%",
                cx=180, cy=350, left=160, top=200, width=40, height=300,
                confidence=1.0,
            )],
        )

    action = SubgoalExecutor(Session(), FastGuiController(Session()))._drawing_opacity_action(
        parse_subgoal_request({"subgoal": "draw a red line", "draw": {"color": "red"}})
    )

    assert action is not None
    assert action["context"].startswith("raise visible drawing opacity")
    assert action["x"] == 180
    assert action["y"] == 212


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


def test_subgoal_parser_does_not_execute_partial_input_and_click_prose():
    from winpilot.fast.subgoal import parse_subgoal_request

    request = parse_subgoal_request({
        "subgoal": (
            "Type E2E_COMPOUND_741 into the target input and click the Apply button. "
            "Verify APPLIED_TEXT_1 appears."
        ),
        "verify_texts": ["APPLIED_TEXT_1"],
    })

    assert request.input == {}
    assert request.click == {}
    assert request.clicks == []
    assert request.verify_texts == ["APPLIED_TEXT_1"]


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
        hwnd = 1
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


def test_subgoal_input_and_click_uses_targeted_non_input_read_for_final_literal():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []

    class Session:
        task = "Type RUNTIME_MARKER_811, apply it, and verify it"
        hwnd = 1
        run_id = "run-subgoal-input-click-query"
        snapshot = Snapshot(
            hwnd=1,
            title="Target",
            region=Region(0, 0, 900, 500),
            elements=[
                UIElement(elem_id=1, source="uia", role="edit", text="Target Input", cx=250, cy=100, left=100, top=80, width=300, height=36),
                UIElement(elem_id=2, source="uia", role="button", text="Apply", cx=470, cy=100, left=420, top=80, width=100, height=36),
            ],
        )

        def dispatch(self, name, args):
            calls.append((name, args))
            if name == "type_text":
                return ToolResult('{"ok": true, "verified": true}')
            if name == "click":
                return ToolResult('{"ok": true, "verified": false}')
            if name == "read_text":
                assert args == {
                    "query": "RUNTIME_MARKER_811",
                    "require_non_input": True,
                }
                return ToolResult("RUNTIME_MARKER_811")
            return ToolResult('{"ok": true}')

    result = SubgoalExecutor(Session(), FastGuiController(Session())).execute({
        "subgoal": "fill and apply",
        "input": {"label": "Target Input", "text": "RUNTIME_MARKER_811", "clear": False},
        "click": {"label": "Apply"},
        "verify_texts": ["RUNTIME_MARKER_811"],
        "constraints": {"wait": False},
    })
    payload = json.loads(result.text)

    assert payload["ok"] is True
    assert payload["verified"] is True
    assert [action["action"] for action in payload["actions_executed"]] == [
        "type_into_element",
        "click_element",
        "read_text",
    ]
    assert [name for name, _args in calls] == ["focus_window", "click", "type_text", "click", "read_text"]


def test_subgoal_does_not_duplicate_explicit_marker_with_inherited_prefix():
    from winpilot.fast.subgoal import parse_subgoal_request

    request = parse_subgoal_request(
        {
            "subgoal": "type and apply the runtime marker",
            "input": {"label": "Target Input", "text": "DAQ_PERCEPTION_E2E_811"},
            "click": {"label": "Apply"},
            "verify_texts": ["DAQ_PERCEPTION_E2E_811"],
        },
        task=(
            "In the visible GUI, type DAQ_PERCEPTION_E2E_811, apply it, "
            "and verify DAQ_PERCEPTION_E2E is visible."
        ),
    )

    assert request.verify_texts == ["DAQ_PERCEPTION_E2E_811"]


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
                assert args == {
                    "x": 870,
                    "y": 90,
                    "button": "left",
                    "double": False,
                    "physical": True,
                }
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
        hwnd = 1
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
                assert args == {
                    "x": 210,
                    "y": 200,
                    "button": "left",
                    "double": False,
                    "physical": True,
                }
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
            image[120:360, 80:460] = (255, 0, 0)
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
        calls = []

        def dispatch(self, name, args):
            self.calls.append(name)
            assert name in {"focus_window", "observe"}
            return ToolResult('{"ok": true}')

    session = Session()
    result = SubgoalExecutor(session, Controller()).execute({
        "subgoal": "draw a blue triangle on the visible canvas",
        "draw": {"shape": "triangle", "color": "blue"},
        "constraints": {"wait": False},
    })
    payload = json.loads(result.text)

    assert captured_actions[0] == {
        "action": "select_color",
        "color": "blue",
        "strict": True,
        "required_by_user": True,
    }
    draw = captured_actions[1]
    canvas = draw["canvas_region"]
    assert payload["ok"] is True
    assert payload["verified"] is True
    assert session.calls == ["focus_window", "observe"]
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
            image[120:360, 80:460] = (255, 0, 0)
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
            assert name in {"focus_window", "observe"}
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
            assert name in {"focus_window", "observe"}
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
    assert "drew visible pixels" in payload["error"]


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

    def fake_surface(_image):
        bbox_calls.append(1)
        if len(bbox_calls) == 1:
            return {"ok": False, "reason": "not ready"}
        return {"ok": True, "bbox": [80, 120, 460, 360]}

    monkeypatch.setattr(
        subgoal_mod.vision_cv, "credible_light_work_surface", fake_surface
    )

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
        task = "draw a blue triangle"
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
    assert "established target hwnd" in payload["error"]
    assert "not an implicit target" in payload["error"]
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


def test_normalized_drawing_geometry_maps_to_detected_canvas():
    from winpilot.fast.subgoal import (
        _normalized_box_region,
        _normalized_strokes_to_screen,
        _shape_strokes,
    )
    from winpilot.utils.screenshot import Region

    canvas = Region(100, 200, 1000, 500)

    box = _normalized_box_region(canvas, [100, 200, 300, 400])
    strokes = _normalized_strokes_to_screen(
        canvas,
        [[[0, 0], [500, 500], [1000, 1000]]],
    )

    assert box == Region(200, 300, 300, 200)
    assert strokes == [[[130, 215], [600, 450], [1070, 685]]]
    assert len(_shape_strokes("ellipse", box)[0]) == 41
    assert _shape_strokes("freeform", box) == []


def test_explicit_shape_box_uses_full_rectangular_geometry():
    from winpilot.fast.subgoal import _shape_strokes
    from winpilot.utils.screenshot import Region

    strokes = _shape_strokes("rectangle", Region(100, 200, 40, 90), margin_ratio=0.0)

    assert strokes == [[
        [100, 200], [139, 200], [139, 289], [100, 289], [100, 200],
    ]]


def test_closed_shape_honors_explicit_xywh_without_semantic_rewrite(monkeypatch):
    from winpilot.fast.subgoal import _normalized_closed_shape_region
    from winpilot.utils.screenshot import Region

    monkeypatch.setattr(
        "winpilot.utils.screenshot.virtual_screen_region",
        lambda: Region(0, 0, 2000, 1200),
    )
    region, metadata = _normalized_closed_shape_region(
        Region(100, 100, 1000, 800),
        [430, 260, 470, 310],
        mode="xywh",
        semantic_part="left ear",
    )

    assert region == Region(530, 308, 470, 248)
    assert metadata["box_interpretation"] == "xywh"
    assert "box_interpretation_corrected" not in metadata


def test_closed_shape_preserves_large_core_xywh_box(monkeypatch):
    from winpilot.fast.subgoal import _normalized_closed_shape_region
    from winpilot.utils.screenshot import Region

    monkeypatch.setattr(
        "winpilot.utils.screenshot.virtual_screen_region",
        lambda: Region(0, 0, 2000, 1200),
    )
    region, metadata = _normalized_closed_shape_region(
        Region(100, 100, 1000, 800),
        [320, 400, 360, 420],
        mode="xywh",
        semantic_part="body",
    )

    assert region == Region(420, 420, 360, 336)
    assert metadata["box_interpretation"] == "xywh"
    assert "box_interpretation_corrected" not in metadata


def test_file_path_input_covers_embedded_task_year_literal():
    from winpilot.fast.subgoal import parse_subgoal_request

    path = r"C:\Users\zou\Desktop\WinPilot_Office_GUI_20260712.txt"
    request = parse_subgoal_request(
        {
            "subgoal": "input filename",
            "input": {"label": "File name", "text": path},
            "click": {"label": "File name"},
        },
        task=(
            "Save the meeting dated 2026-07-12 to Desktop as "
            "WinPilot_Office_GUI_20260712.txt"
        ),
    )

    assert request.verify_texts == [path]


def test_file_dialog_enter_submit_verifies_current_dialog_title(monkeypatch):
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request

    class Session:
        hwnd = 77
        task = "save the file"
        snapshot = None

    monkeypatch.setattr("winpilot.fast.subgoal.win32._window_text", lambda hwnd: "Save As")
    executor = SubgoalExecutor(Session(), FastGuiController(Session()))
    request = parse_subgoal_request({
        "subgoal": "save as file",
        "target_window": {"hwnd": 77},
        "input": {
            "label": "File name",
            "text": r"C:\Users\zou\Desktop\demo.txt",
        },
        "click": {"label": "File name"},
    })

    actions = executor._build_actions(request)

    enter = next(action for action in actions if action.get("action") == "hotkey")
    assert enter["expect"] == {"file_exists": r"C:\Users\zou\Desktop\demo.txt", "timeout_s": 8.0}


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


def test_fast_controller_select_color_clicks_visible_palette_swatch(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent import scope_guard
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

    monkeypatch.setattr(scope_guard, "ensure_scope", lambda *_args, **_kwargs: None)
    controller = FastGuiController(Session(), capture_fn=lambda region: image)
    out = controller.execute([{"action": "select_color", "color": "blue"}], sync=False)

    assert out["ok"] is True
    assert out.get("verified") is not True
    assert out["needs_observe"] is True
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


def test_palette_swatch_detection_accepts_dense_top_ribbon_palette():
    from winpilot.fast.controller import FastGuiController
    from winpilot.utils.screenshot import Region

    image = np.full((500, 1000, 3), 96, dtype=np.uint8)
    swatches = [
        (400, (0, 0, 255)),
        (430, (0, 128, 255)),
        (460, (0, 255, 255)),
        (490, (0, 180, 0)),
        (520, (255, 0, 0)),
        (550, (180, 0, 180)),
    ]
    for row in (90, 120):
        for x, color in swatches:
            image[row:row + 18, x:x + 18] = color

    point = FastGuiController._palette_swatch_point(
        image,
        Region(0, 0, 1000, 500),
        "blue",
    )

    assert point is not None
    assert 518 <= point[0] <= 540
    assert 88 <= point[1] <= 140


def test_palette_swatch_detection_rejects_multihue_edit_colors_icon():
    from winpilot.fast.controller import FastGuiController
    from winpilot.utils.screenshot import Region

    image = np.full((220, 400, 3), 96, dtype=np.uint8)
    swatches = [
        (40, (0, 0, 255)),
        (70, (0, 128, 255)),
        (100, (0, 255, 255)),
        (130, (0, 180, 0)),
        (160, (255, 0, 0)),
        (190, (180, 0, 180)),
    ]
    for row in (100, 130):
        for x, value in swatches:
            image[row:row + 18, x:x + 18] = value

    # A larger orange component inside a multihue icon used to outrank the
    # real orange swatch by area and open an Edit colors modal.
    image[94:142, 286:334] = (255, 0, 0)
    image[94:118, 286:310] = (0, 180, 0)
    image[94:118, 310:334] = (180, 0, 180)
    image[118:142, 286:310] = (0, 128, 255)
    image[118:142, 310:334] = (0, 255, 255)

    point = FastGuiController._palette_swatch_point(
        image,
        Region(0, 0, 400, 220),
        "orange",
    )

    assert point is not None
    assert 68 <= point[0] <= 90
    assert 98 <= point[1] <= 150


def test_palette_neutral_swatch_must_share_chromatic_palette_envelope():
    from winpilot.fast.controller import FastGuiController
    from winpilot.utils.screenshot import Region

    image = np.full((240, 1000, 3), (80, 90, 100), dtype=np.uint8)
    palette = [
        (180, (0, 0, 0)),
        (210, (160, 160, 160)),
        (240, (0, 0, 255)),
        (270, (0, 128, 255)),
        (300, (0, 255, 255)),
        (330, (0, 180, 0)),
        (360, (255, 0, 0)),
        (390, (180, 0, 180)),
    ]
    for row in (100, 132):
        for x, value in palette:
            image[row:row + 18, x:x + 18] = value
    # This is analogous to a neutral Layers toolbar icon. It is near enough to
    # see some palette colors, but outside their horizontal row envelope.
    image[105:127, 650:672] = (0, 0, 0)

    point = FastGuiController._palette_swatch_point(
        image,
        Region(0, 0, image.shape[1], image.shape[0]),
        "black",
    )

    assert point is not None
    assert 175 <= point[0] <= 205


def test_palette_and_visual_evidence_share_extended_color_vocabulary():
    from winpilot.fast.controller import FastGuiController, _color_mask
    from winpilot.perception.vision_cv import color_presence
    from winpilot.utils.screenshot import Region

    image = np.full((240, 640, 3), (80, 90, 100), dtype=np.uint8)
    palette = [
        (80, (0, 0, 0)),
        (110, (150, 150, 150)),
        (140, (255, 255, 255)),
        (170, (0, 0, 255)),
        (200, (0, 128, 255)),
        (230, (0, 255, 255)),
        (260, (0, 180, 0)),
        (290, (255, 0, 0)),
        (320, (180, 0, 180)),
        (350, (201, 174, 255)),
        (380, (87, 122, 185)),
        (410, (0, 255, 128)),
        (440, (255, 255, 0)),
        (470, (255, 0, 255)),
    ]
    for row in (100, 132):
        for x, value in palette:
            image[row:row + 18, x:x + 18] = value

    for color in ("pink", "gray", "white", "brown", "lime", "cyan", "magenta"):
        point = FastGuiController._palette_swatch_point(
            image,
            Region(0, 0, image.shape[1], image.shape[0]),
            color,
        )
        assert point is not None
        assert _color_mask(image, color) is not None
        assert color_presence(image)["colors"][color]["visible"] is True


def test_draw_color_parser_supports_neutral_and_pale_colors():
    from winpilot.fast.subgoal import _canonical_color_name, _requested_color

    assert _requested_color({"color": "pink"}, "", "") == "pink"
    assert _requested_color({"colour": "grey"}, "", "") == "gray"
    assert _requested_color({"color": "white"}, "", "") == "white"
    assert _requested_color({"color": "brown"}, "", "") == "brown"
    assert _requested_color({"color": "lime"}, "", "") == "lime"
    assert _requested_color({"color": "cyan"}, "", "") == "cyan"
    assert _requested_color({"color": "magenta"}, "", "") == "magenta"
    assert _requested_color({"color": "#FF6B35"}, "", "") == "orange"
    assert _requested_color({"color": "#4169E1"}, "", "") == "blue"
    assert _requested_color({"color": "#FF1493"}, "", "") == "pink"
    assert _requested_color({"color": "#FFC0CB"}, "", "") == "pink"
    assert _canonical_color_name("#D2691E") == "orange"
    assert _canonical_color_name("#8B4513") == "brown"
    assert _canonical_color_name("grey") == "gray"


def test_only_user_explicit_draw_colors_are_strict_requirements():
    from winpilot.fast.subgoal import _required_user_draw_colors

    assert _required_user_draw_colors("用画板画一只狗，要好看一点不要简陋") == []
    assert _required_user_draw_colors("draw a blue and brown dog in Paint") == ["blue", "brown"]
    # The drawing surface name is not itself a request for white paint.
    assert _required_user_draw_colors("draw a dog on a whiteboard") == []
    assert _required_user_draw_colors("use one ordered draws list with semantic part labels") == []


def test_unexecuted_white_primitive_is_not_satisfied_by_canvas_background():
    from winpilot.fast.subgoal import _matched_executed_draw_colors

    matched = _matched_executed_draw_colors(
        ["orange", "white"],
        [{"requested_color": "orange", "matched": True, "visible_colors": {"orange": {}, "white": {}}}],
    )

    assert matched == ["orange"]


def test_draw_color_evidence_filters_out_unexecuted_white_plan():
    from winpilot.fast.subgoal import (
        SubgoalExecutor,
        _matched_executed_draw_colors,
    )

    controller = SimpleNamespace(
        capture_fn=lambda region: np.full((region.height, region.width, 3), 255, dtype=np.uint8)
    )
    executor = SubgoalExecutor(SimpleNamespace(), controller)
    actions = [
        {
            "action": "draw_strokes",
            "requested_color": "orange",
            "strokes": [[[120, 120], [180, 180]]],
            "canvas_region": {"x": 100, "y": 100, "width": 300, "height": 300},
            "defer_visual_verify": True,
        },
        {
            "action": "draw_strokes",
            "requested_color": "white",
            "strokes": [[[200, 120], [240, 160]]],
            "canvas_region": {"x": 100, "y": 100, "width": 300, "height": 300},
        },
    ]
    batch_results = [{
        "action": "draw_strokes",
        "ok": True,
        "data": {"visual_verify_deferred": True},
    }]

    _visible, per_draw = executor._draw_color_evidence(actions, batch_results)
    matched = _matched_executed_draw_colors(["orange", "white"], per_draw)

    assert [item["requested_color"] for item in per_draw] == ["orange"]
    assert "white" in per_draw[0]["visible_colors"]
    assert "white" not in matched


def test_normalized_line_box_accepts_typed_endpoints_and_xywh():
    from winpilot.fast.subgoal import _normalized_line_geometry
    from winpilot.utils.screenshot import Region

    canvas = Region(100, 200, 1000, 500)
    endpoints = _normalized_line_geometry(canvas, [460, 360, 500, 370])
    typed_xywh = _normalized_line_geometry(canvas, [380, 290, 60, 0], "xywh")

    assert endpoints is not None
    assert endpoints[0] == [[[560, 380], [600, 385]]]
    assert endpoints[1].width == 41
    assert typed_xywh is not None
    assert typed_xywh[0] == [[[480, 345], [540, 345]]]
    assert typed_xywh[1].width == 61


def test_boxed_eye_pupil_and_shine_strokes_shrink_monotonically():
    from winpilot.fast.subgoal import _normalized_box_region, _shape_strokes
    from winpilot.utils.screenshot import Region

    canvas = Region(100, 200, 866, 413)
    regions = [
        _normalized_box_region(canvas, [400, 250, size, size])
        for size in (30, 14, 8)
    ]

    def stroke_extent(region):
        assert region is not None
        stroke = _shape_strokes("circle", region, margin_ratio=0.0)[0]
        xs = [point[0] for point in stroke]
        ys = [point[1] for point in stroke]
        return max(xs) - min(xs), max(ys) - min(ys)

    extents = [stroke_extent(region) for region in regions]

    assert extents[0][0] > extents[1][0] > extents[2][0]
    assert extents[0][1] > extents[1][1] > extents[2][1]


def test_task_wording_does_not_rewrite_detected_canvas_geometry():
    from winpilot.fast.subgoal import (
        _logical_scene_canvas,
        _normalized_box_region,
        _shape_strokes,
    )
    from winpilot.utils.screenshot import Region

    real_canvas = Region(100, 200, 866, 413)
    logical = _logical_scene_canvas(
        real_canvas,
        [{"shape": "ellipse"}, {"shape": "ellipse"}],
        "用画板画一只狗，要好看一点不要简陋",
    )
    repair_logical = _logical_scene_canvas(
        Region(90, 190, 886, 433),
        [{"shape": "circle", "part": "左眼"}],
        "用画板画一只狗，要好看一点不要简陋",
        {
            "x": logical.left,
            "y": logical.top,
            "width": logical.width,
            "height": logical.height,
        },
    )
    mapped = _normalized_box_region(logical, [400, 250, 180, 180])

    assert logical == real_canvas
    assert repair_logical == Region(90, 190, 886, 433)
    assert mapped is not None

    stroke = _shape_strokes("ellipse", mapped, margin_ratio=0.0)[0]
    assert all(
        real_canvas.left <= x <= real_canvas.right
        and real_canvas.top <= y <= real_canvas.bottom
        for x, y in stroke
    )


def test_each_draw_uses_current_detected_canvas_without_hidden_scene_transform(
    monkeypatch,
):
    from winpilot.fast import subgoal as subgoal_mod
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request
    from winpilot.utils import screenshot as screenshot_mod
    from winpilot.utils.screenshot import Region

    window = Region(480, 154, 1008, 720)
    # Region.clamp_to_screen() is part of the production mapping path.  Bind
    # its desktop authority explicitly so this geometry test does not inherit
    # the worker's monitor size, DPI layout, or headless virtual display.
    monkeypatch.setattr(
        screenshot_mod,
        "virtual_screen_region",
        lambda: Region(0, 0, 2560, 1440),
    )
    image = np.zeros((window.height, window.width, 3), dtype=np.uint8)
    bboxes = iter([
        (126, 258, 992, 671),  # absolute x=606, width=866
        (634, 258, 992, 671),  # absolute x=1114, width=358
        (126, 258, 482, 671),  # absolute x=606, width=356
    ])
    monkeypatch.setattr(subgoal_mod, "window_region", lambda _hwnd: window)
    monkeypatch.setattr(
        subgoal_mod.vision_cv,
        "credible_light_work_surface",
        lambda _image: {"ok": True, "bbox": list(next(bboxes))},
    )

    class Controller:
        capture_fn = staticmethod(lambda _region: image)

    class Session:
        task = "draw a detailed cute dog"
        hwnd = 3344228
        snapshot = None
        _canvas_generation = 0

    session = Session()
    executor = SubgoalExecutor(session, Controller())
    request = parse_subgoal_request({
        "subgoal": "add one labelled mouth detail",
        "draws": [{
            "shape": "line",
            "part": "mouth",
            "box": [490, 180, 520, 190],
            "box_mode": "endpoints",
        }],
    }, task=session.task)

    def draw_action() -> dict:
        actions = executor._build_actions(request)
        return next(action for action in actions if action.get("action") == "draw_strokes")

    first_action = draw_action()
    first = first_action["canvas_region"]
    right_component = draw_action()["canvas_region"]
    left_component = draw_action()["canvas_region"]

    assert first == {"x": 606, "y": 412, "width": 866, "height": 413}
    assert first_action["strokes"] == [[[1030, 486], [1056, 490]]]
    assert right_component == {"x": 1114, "y": 412, "width": 358, "height": 413}
    assert left_component == {"x": 606, "y": 412, "width": 356, "height": 413}
    assert session._last_canvas_region == {
        "x": 606,
        "y": 412,
        "width": 356,
        "height": 413,
    }
    assert "_canvas_transform_cache" not in session._last_canvas_region


def test_drawing_canvas_defaults_to_fail_closed_for_unrecognized_bright_window(
    monkeypatch,
):
    from winpilot.fast import subgoal as subgoal_mod
    from winpilot.utils.screenshot import Region

    window = Region(10, 20, 800, 600)
    bright_modal = np.full((window.height, window.width, 3), 245, dtype=np.uint8)
    controller = SimpleNamespace(capture_fn=lambda _region: bright_modal)
    executor = subgoal_mod.SubgoalExecutor(SimpleNamespace(), controller)
    monkeypatch.setattr(subgoal_mod, "_canvas_region_from_image", lambda *_args: None)
    monkeypatch.setattr(subgoal_mod, "_looks_like_unready_window", lambda _image: False)

    strict, attempts = executor._ready_canvas_region(window, timeout_s=0.1)

    assert strict is None
    assert attempts[-1]["ok"] is False


def test_execute_subgoal_default_draw_route_never_dispatches_surface_fallback(
    monkeypatch,
):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import subgoal as subgoal_mod
    from winpilot.utils.screenshot import Region

    window = Region(10, 20, 800, 600)
    image = np.full((window.height, window.width, 3), 245, dtype=np.uint8)
    dispatched = []
    monkeypatch.setattr(subgoal_mod, "window_region", lambda _hwnd: window)
    monkeypatch.setattr(subgoal_mod, "_canvas_region_from_image", lambda *_args: None)
    monkeypatch.setattr(subgoal_mod, "_looks_like_unready_window", lambda _image: False)

    class Watcher:
        @staticmethod
        def status():
            return {}

    class Controller:
        watcher = Watcher()
        capture_fn = staticmethod(lambda _region: image)

        def execute(self, actions, **_kwargs):
            dispatched.extend(actions)
            raise AssertionError("surface fallback must not reach the controller")

    class Session:
        task = "trace a continuous path on the visible work surface"
        hwnd = 7
        snapshot = None
        run_id = "surface-fail-closed"

        @staticmethod
        def dispatch(name, _args):
            assert name == "focus_window"
            return ToolResult('{"ok": true}')

    result = subgoal_mod.SubgoalExecutor(Session(), Controller()).execute({
        "subgoal": "trace the supplied continuous path",
        "draw": {
            "shape": "line",
            "box": [200, 300, 800, 700],
            "box_mode": "endpoints",
        },
    })
    payload = json.loads(result.text)

    assert payload["ok"] is False
    assert payload["blocked"] is True
    assert payload["work_surface_fail_closed"] is True
    assert "uniquely ground one credible work surface" in payload["error"]
    assert dispatched == []


def test_normalized_line_box_mode_can_override_ambiguous_coordinates():
    from winpilot.fast.subgoal import _normalized_line_geometry
    from winpilot.utils.screenshot import Region

    canvas = Region(0, 0, 1000, 1000)

    assert _normalized_line_geometry(canvas, [50, 50, 100, 100], "endpoints")[0] == [
        [[50, 50], [100, 100]]
    ]
    assert _normalized_line_geometry(canvas, [50, 50, 100, 100], "xywh")[0] == [
        [[50, 50], [150, 150]]
    ]


def test_semantic_part_never_rewrites_typed_line_coordinates():
    from winpilot.fast.subgoal import _normalized_line_geometry
    from winpilot.utils.screenshot import Region

    canvas = Region(0, 0, 1000, 1000)

    declared_endpoints = _normalized_line_geometry(
        canvas,
        [490, 180, 30, 10],
        "endpoints",
        semantic_part="mouth",
    )
    declared_offsets = _normalized_line_geometry(
        canvas,
        [490, 180, 30, 10],
        "xywh",
        semantic_part="mouth",
    )
    front_leg = _normalized_line_geometry(
        canvas,
        [400, 450, 400, 550],
        "endpoints",
        semantic_part="left_front_leg",
    )
    tail = _normalized_line_geometry(
        canvas,
        [650, 300, 750, 200],
        "endpoints",
        semantic_part="tail",
    )

    assert declared_endpoints is not None
    assert declared_endpoints[0] == [[[490, 180], [30, 10]]]
    assert declared_offsets is not None
    assert declared_offsets[0] == [[[490, 180], [520, 190]]]
    assert front_leg is not None
    assert front_leg[0] == [[[400, 450], [400, 550]]]
    assert tail is not None
    assert tail[0] == [[[650, 300], [750, 200]]]


def test_normalized_multi_point_geometry_supports_triangle_and_bent_tail():
    from winpilot.fast.subgoal import _normalized_point_geometry
    from winpilot.utils.screenshot import Region

    canvas = Region(100, 200, 1000, 500)
    triangle = _normalized_point_geometry(
        canvas, [100, 100, 200, 20, 300, 100], close=True
    )
    tail = _normalized_point_geometry(
        canvas, [700, 500, 800, 350, 780, 280]
    )

    assert triangle is not None
    assert triangle[0][0] == [
        [200, 250], [300, 210], [400, 250], [200, 250]
    ]
    assert tail is not None
    assert tail[0][0] == [[800, 450], [900, 375], [880, 340]]


def test_detailed_scene_preflight_defers_semantics_to_fresh_visual_judge():
    from winpilot.fast.subgoal import _scene_structure_preflight

    sparse = [
        {"shape": "ellipse", "color": "orange", "box": [300, 300, 400, 300]},
        {"shape": "line", "color": "black", "box": [300, 300, 400, 320]},
    ]
    many = [
        {"shape": "circle", "part": f"detail-{index}", "box": [index, index, 10, 10]}
        for index in range(40)
    ]
    collapsed = [
        {"shape": "circle", "part": f"layer-{index}", "box": [400, 400, 200, 200]}
        for index in range(4)
    ]

    for specs in (sparse, many, collapsed):
        result = _scene_structure_preflight(specs, "draw a detailed polished illustration")
        assert result["required"] is True
        assert result["ok"] is True
        assert result["quality_deferred_to_visual_judge"] is True
        assert "creature_scene" not in result
        assert "creature_part_counts" not in result

    assert _scene_structure_preflight(
        collapsed, "draw a detailed polished illustration"
    )["warnings"]
    unsupported = _scene_structure_preflight(
        [{**sparse[0], "filled": True}],
        "draw a detailed polished illustration",
    )
    assert unsupported["ok"] is False
    assert any("unsupported" in error for error in unsupported["errors"])


def test_exact_polished_dog_request_has_no_animal_part_or_count_gate():
    from winpilot.fast.subgoal import _scene_structure_preflight

    task = "用画板画一只狗,要好看一点不要简陋的"
    two_shapes = [
        {"shape": "ellipse", "color": "orange", "part": "body", "box": [300, 420, 400, 300]},
        {"shape": "ellipse", "color": "orange", "part": "head", "box": [390, 220, 220, 220]},
    ]

    result = _scene_structure_preflight(two_shapes, task)

    assert result["required"] is True
    assert result["ok"] is True
    assert result["primitive_count"] == 2
    assert result["quality_deferred_to_visual_judge"] is True
    assert "creature_scene" not in result
    assert "creature_part_counts" not in result


def test_normalized_strokes_are_explicit_and_box_geometry_is_not_auto_compiled():
    from winpilot.fast.subgoal import _effective_normalized_strokes

    explicit = [[[100, 200], [300, 400], [500, 250]]]
    supplied = {
        "shape": "line",
        "part": "accent",
        "normalized_strokes": explicit,
    }
    box_only = {
        "shape": "ellipse",
        "part": "subject",
        "box": [300, 180, 260, 240],
        "box_mode": "xywh",
    }

    assert _effective_normalized_strokes(supplied) is explicit
    assert _effective_normalized_strokes(box_only) is None


def test_draw_action_does_not_silently_fit_supplied_geometry(monkeypatch):
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request
    from winpilot.utils.screenshot import Region

    supplied_path = [[[300, 259], [309, 250], [318, 259], [309, 268], [300, 259]]]
    draw = {
        "part": "highlight",
        "shape": "circle",
        "box": [300, 250, 18, 18],
        "normalized_strokes": supplied_path,
    }
    class Session:
        task = "draw a polished illustration"
        _verified_draw_scene_specs = [{"part": "subject", "shape": "ellipse", "box": [1, 2, 3, 4]}]
        _last_canvas_region = None

    executor = SubgoalExecutor(Session(), SimpleNamespace())
    request = parse_subgoal_request({"subgoal": "add highlight", "draw": draw})
    action = executor._build_draw_action(
        request,
        draw,
        canvas_context=(Region(0, 0, 1000, 1000), []),
    )

    assert action is not None
    assert action["strokes"] == supplied_path


def test_repeated_draw_actions_do_not_create_hidden_scene_fit_state(monkeypatch):
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request
    from winpilot.utils.screenshot import Region

    class Session:
        task = "draw a polished illustration"
        _verified_draw_scene_specs = []
        _last_canvas_region = None

    session = Session()
    executor = SubgoalExecutor(session, SimpleNamespace())
    for part, offset in (("subject", 0), ("detail", 20)):
        draw = {
            "part": part,
            "shape": "line",
            "normalized_strokes": [[[100 + offset, 100], [200 + offset, 200]]],
        }
        request = parse_subgoal_request({"subgoal": f"draw {part}", "draw": draw})
        assert executor._build_draw_action(
            request,
            draw,
            canvas_context=(Region(0, 0, 1000, 1000), []),
        ) is not None

    assert not hasattr(session, "_drawing_scene_fit_basis")


def test_line_box_default_is_endpoints_and_xywh_requires_explicit_mode():
    from winpilot.fast.subgoal import _line_box_coordinates

    default = _line_box_coordinates([490, 180, 30, 10], semantic_part="mouth")
    offsets = _line_box_coordinates(
        [490, 180, 30, 10],
        "xywh",
        semantic_part="mouth",
    )

    assert default == (490.0, 180.0, 30.0, 10.0, False)
    assert offsets == (490.0, 180.0, 520.0, 190.0, True)


def test_legacy_drawing_profile_is_general_and_keeps_plan_mutators():
    from winpilot.agent.tool_schemas import tool_schemas

    standard = tool_schemas(
        planning=True,
        coding_runtime=False,
        allow_shell=False,
        allow_files=False,
        profile="standard",
    )
    drawing = tool_schemas(
        planning=True,
        coding_runtime=False,
        allow_shell=False,
        allow_files=False,
        profile="drawing_gui",
    )
    standard_names = {tool["function"]["name"] for tool in standard}
    drawing_names = {tool["function"]["name"] for tool in drawing}
    execute = next(
        tool for tool in drawing if tool["function"]["name"] == "execute_subgoal"
    )
    description = execute["function"]["description"]

    assert {"revise_plan", "complete_subgoal"} <= standard_names
    assert {"revise_plan", "complete_subgoal"} <= drawing_names
    assert {"open_app", "execute_subgoal", "done", "blocked"} <= drawing_names
    assert "one complete 15-30" not in description
    assert "outline-only" not in description
    assert "pose-visible limbs" not in description
    assert "dog" not in description.casefold()


def test_drawing_quantity_range_is_not_treated_as_arithmetic_expression():
    from winpilot.fast.subgoal import _request_arithmetic_keyboard_text, parse_subgoal_request

    drawing = parse_subgoal_request({
        "subgoal": "Draw a colorful cat using 20-30 primitives",
        "draws": [{"shape": "ellipse", "part": "body", "color": "orange"}],
    }, task="draw a detailed colorful cat")
    arithmetic = parse_subgoal_request({
        "subgoal": "calculate 2026*7*12",
    }, task="open a numeric tool and calculate 2026*7*12")

    assert _request_arithmetic_keyboard_text(drawing, "draw a detailed colorful cat") == ""
    assert _request_arithmetic_keyboard_text(arithmetic, arithmetic.subgoal) == "2026*7*12"


def test_partial_draw_specs_merge_without_anatomy_threshold():
    from winpilot.fast.subgoal import (
        _merge_draw_scene_specs,
        _scene_structure_preflight,
        _successful_draw_scene_specs,
    )

    base = [
        {"shape": "ellipse", "color": "orange", "part": "area", "box": [300, 420, 400, 300]},
        {"shape": "line", "color": "black", "part": "accent", "box": [300, 300, 400, 320]},
    ]
    actions = [
        {"action": "draw_strokes", "defer_visual_verify": True},
        {"action": "draw_strokes", "defer_visual_verify": False},
    ]
    results = [
        {"index": 1, "ok": True, "data": {"visual_verify_deferred": True}},
        {"index": 2, "ok": True, "data": {"visual_change_detected": True}},
    ]

    remembered = _successful_draw_scene_specs(base, actions, {"results": results})
    incremental = [
        {"shape": "circle", "color": "black", "part": "detail", "box": [538, 293, 14, 14]}
    ]
    merged = _merge_draw_scene_specs(remembered, incremental)
    base_result = _scene_structure_preflight(base, "draw a detailed illustration")
    merged_result = _scene_structure_preflight(merged, "draw a detailed illustration")

    assert remembered == base
    assert len(merged) == 3
    assert base_result["ok"] is True
    assert merged_result["ok"] is True
    assert base_result["quality_deferred_to_visual_judge"] is True
    assert merged_result["quality_deferred_to_visual_judge"] is True


def test_batch_delegates_normalized_strokes_to_interaction_surface_resolver():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController

    dispatched = []

    class Session:
        hwnd = 1
        run_id = "normalized-canvas"
        _screen_epoch = 0
        _observe_cache = None
        snapshot = None
        # Historical controller geometry must not ground a new mutation.
        _last_canvas_region = {"x": 1, "y": 2, "width": 3, "height": 4}

        def _prepare_draw_strokes_args(self, args, capture_fn):
            assert callable(capture_fn)
            prepared = dict(args)
            prepared.pop("normalized_strokes")
            prepared["strokes"] = [[[100, 200], [599, 449], [1099, 699]]]
            prepared["mapped_from_coordinate_space"] = "work_surface_normalized_0_1000"
            return prepared

    controller = FastGuiController(Session())

    def dispatch(name, args):
        dispatched.append((name, dict(args)))
        return ToolResult('{"ok": true}') if name == "draw_strokes" else ToolResult("fresh")

    controller._dispatch = dispatch
    payload = controller.execute([
        {
            "action": "draw_strokes",
            "normalized_strokes": [[[0, 0], [500, 500], [1000, 1000]]],
        },
        {"action": "read_text"},
    ])

    assert payload["ok"] is True
    assert dispatched[0][0] == "draw_strokes"
    assert dispatched[0][1]["strokes"] == [[[100, 200], [599, 449], [1099, 699]]]
    assert dispatched[0][1]["mapped_from_coordinate_space"] == (
        "work_surface_normalized_0_1000"
    )


def test_multi_draw_build_locates_canvas_once_and_defers_inner_verification(monkeypatch):
    from winpilot.fast import subgoal as subgoal_mod
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request
    from winpilot.utils.screenshot import Region

    window = Region(10, 20, 500, 400)
    image = np.zeros((400, 500, 3), dtype=np.uint8)
    image[120:360, 80:460] = 255
    captures = []
    monkeypatch.setattr(subgoal_mod, "window_region", lambda _hwnd: window)
    monkeypatch.setattr(
        subgoal_mod.vision_cv,
        "credible_light_work_surface",
        lambda _image: {"ok": True, "bbox": [80, 120, 460, 360]},
    )

    class Controller:
        @staticmethod
        def capture_fn(region):
            captures.append(region)
            return image

    class Session:
        task = "draw four blue shapes"
        hwnd = 7
        snapshot = None

    executor = SubgoalExecutor(Session(), Controller())
    request = parse_subgoal_request({
        "subgoal": "draw four blue shapes",
        "draws": [
            {"shape": "circle", "color": "blue", "box": [100 + i * 180, 300, 100, 100]}
            for i in range(4)
        ],
    }, task=Session.task)

    actions = executor._build_actions(request)
    draws = [action for action in actions if action.get("action") == "draw_strokes"]
    colors = [action for action in actions if action.get("action") == "select_color"]

    assert captures == [window]
    assert len(draws) == 4
    assert [draw.get("defer_visual_verify") for draw in draws] == [True, True, True, False]
    assert draws[0]["trace_checkpoint"] is True
    assert draws[1]["trace_checkpoint"] is False
    assert draws[-1]["trace_checkpoint"] is True
    assert len(colors) == 1
    assert all(action["strict"] is True for action in colors)


def test_typed_draw_colors_drive_palette_without_aesthetic_keyword_policy(monkeypatch):
    from winpilot.fast import subgoal as subgoal_mod
    from winpilot.fast.subgoal import SubgoalExecutor, parse_subgoal_request
    from winpilot.utils.screenshot import Region

    window = Region(10, 20, 900, 600)
    image = np.zeros((600, 900, 3), dtype=np.uint8)
    image[120:570, 40:860] = 255
    monkeypatch.setattr(subgoal_mod, "window_region", lambda _hwnd: window)
    monkeypatch.setattr(
        subgoal_mod.vision_cv,
        "credible_light_work_surface",
        lambda _image: {"ok": True, "bbox": [40, 120, 860, 570]},
    )

    class Controller:
        capture_fn = staticmethod(lambda _region: image)

    class Session:
        task = "用画板画一只狗,要好看一点不要简陋的"
        hwnd = 7
        snapshot = None

    colors = ["#D2691E", "white", "#8B4513", "black", "pink", "orange"]
    executor = SubgoalExecutor(Session(), Controller())
    request = parse_subgoal_request({
        "subgoal": "画一只有细节的狗",
        "draws": [
            {
                "shape": "ellipse" if index < 2 else "circle",
                "color": color,
                "part": f"part-{index}",
                "box": [200 + index * 40, 200 + index * 20, 80, 60],
            }
            for index, color in enumerate(colors)
        ],
    }, task=Session.task)

    actions = executor._build_actions(request)
    selections = [action for action in actions if action.get("action") == "select_color"]

    assert [item["color"] for item in selections] == [
        "orange", "white", "brown", "black", "pink", "orange",
    ]
    assert all(item["strict"] is False for item in selections)




def test_select_color_prefers_target_hwnd_region_over_stale_snapshot(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent import scope_guard
    from winpilot.fast import controller as controller_mod
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.fusion import Snapshot
    from winpilot.utils.screenshot import Region

    monkeypatch.setattr(scope_guard, "ensure_scope", lambda *_args, **_kwargs: None)
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
    assert captured_regions[0] == Region(100, 200, 300, 180)
    # The controller also captures the selected swatch ROI for local effect
    # evidence instead of treating unrelated target-window pixels as progress.
    assert len(captured_regions) == 2
    assert captured_regions[1].width == 96
    assert captured_regions[1].height == 96
    assert clicks


def test_optional_missing_color_keeps_active_color_without_opening_palette(monkeypatch):
    from winpilot.fast import controller as controller_mod
    from winpilot.fast.controller import FastGuiController
    from winpilot.utils.screenshot import Region

    target_region = Region(100, 200, 400, 500)
    monkeypatch.setattr(controller_mod, "window_region", lambda _hwnd: target_region)

    class Session:
        hwnd = 7
        snapshot = None

    captures = []

    def capture_fn(region):
        captures.append(region)
        return np.full((region.height, region.width, 3), 96, dtype=np.uint8)

    controller = FastGuiController(Session(), capture_fn=capture_fn)
    controller._active_color = "orange"

    def forbidden_open(*_args, **_kwargs):
        raise AssertionError("optional compact miss with an active color must not open the palette")

    controller._open_color_palette = forbidden_open
    payload = json.loads(controller._select_color({"color": "brown", "strict": False}).text)

    assert payload["ok"] is True
    assert payload["color_selection_skipped"] is True
    assert payload["active_color"] == "orange"
    assert len(captures) == 1


def test_missing_color_is_fail_closed_only_when_strict(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import controller as controller_mod
    from winpilot.fast.controller import FastGuiController
    from winpilot.utils.screenshot import Region

    target_region = Region(100, 200, 400, 500)
    monkeypatch.setattr(controller_mod, "window_region", lambda _hwnd: target_region)

    class Session:
        hwnd = 7
        snapshot = None

    controller = FastGuiController(
        Session(),
        capture_fn=lambda region: np.full((region.height, region.width, 3), 96, dtype=np.uint8),
    )
    controller._open_color_palette = lambda *_args, **_kwargs: ToolResult(
        '{"ok": false, "error": "not found"}'
    )

    optional = json.loads(controller._select_color({"color": "brown", "strict": False}).text)
    strict = json.loads(controller._select_color({"color": "brown", "strict": True}).text)

    assert optional["ok"] is True
    assert optional["fallback_to_current_color"] is True
    assert strict["ok"] is False


def test_select_color_scans_expanded_popup_below_compact_toolbar(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import controller as controller_mod
    from winpilot.fast.controller import FastGuiController
    from winpilot.utils.screenshot import Region

    target_region = Region(100, 200, 640, 600)
    monkeypatch.setattr(controller_mod, "window_region", lambda _hwnd: target_region)
    opened = {"value": False}
    captured_heights = []
    clicks = []

    class Session:
        hwnd = 7
        snapshot = None

    def capture_fn(region):
        captured_heights.append(region.height)
        image = np.full((region.height, region.width, 3), (80, 90, 100), dtype=np.uint8)
        if opened["value"] and region.height > 220:
            palette = [
                (80, (0, 0, 0)),
                (110, (150, 150, 150)),
                (140, (255, 255, 255)),
                (170, (0, 0, 255)),
                (200, (0, 128, 255)),
                (230, (0, 255, 255)),
                (260, (0, 180, 0)),
                (290, (255, 0, 0)),
                (320, (180, 0, 180)),
                (350, (201, 174, 255)),
                (380, (87, 122, 185)),
                (410, (0, 255, 128)),
                (440, (255, 255, 0)),
                (470, (255, 0, 255)),
            ]
            for row in (240, 272):
                for x, value in palette:
                    image[row:row + 18, x:x + 18] = value
        return image

    controller = FastGuiController(Session(), capture_fn=capture_fn)

    def open_palette(_region, **_kwargs):
        opened["value"] = True
        return ToolResult('{"ok": true, "cached": false}')

    controller._open_color_palette = open_palette
    controller._dispatch = lambda name, args: (
        clicks.append((name, args)) or ToolResult('{"ok": true}')
    )

    payload = json.loads(controller._select_color({"color": "brown", "strict": False}).text)

    assert payload["ok"] is True
    assert payload.get("color_selection_skipped") is not True
    assert captured_heights[0] == 180
    assert max(captured_heights) >= 320
    assert clicks and clicks[-1][0] == "click"


def test_open_palette_uses_accessible_snapshot_without_ocr(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import controller as controller_mod
    from winpilot.fast.controller import FastGuiController
    from winpilot.utils.screenshot import Region

    region = Region(100, 200, 400, 500)

    class Session:
        hwnd = 7
        snapshot = SimpleNamespace(elements=[SimpleNamespace(
            text="Colors", cx=420, cy=260, source="uia", role="button", confidence=1.0,
        )])

    monkeypatch.setattr(
        controller_mod.ocr,
        "collect",
        lambda _region: (_ for _ in ()).throw(AssertionError("OCR should be skipped")),
    )
    controller = FastGuiController(Session())
    controller._dispatch = lambda _name, _args: ToolResult('{"ok": true}')

    payload = json.loads(controller._open_color_palette(region).text)

    assert payload["ok"] is True
    assert payload["locator_source"] == "snapshot"
    assert payload["ocr_ms"] == 0


def test_stable_wait_condition_normalizes_to_bounded_screen_wait():
    from winpilot.fast.controller import _normalise_action

    name, args = _normalise_action({
        "action": "wait",
        "condition": {"type": "stable", "duration_ms": 300, "timeout_s": 10},
    })

    assert name == "wait_stable"
    assert args["stable_ms"] == 300
    assert args["timeout_s"] <= 2.0


def test_select_color_reuses_cached_palette_button(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent import scope_guard
    from winpilot.fast import controller as controller_mod
    from winpilot.fast.controller import FastGuiController
    from winpilot.utils.screenshot import Region

    monkeypatch.setattr(scope_guard, "ensure_scope", lambda *_args, **_kwargs: None)
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


def test_wait_for_visual_change_uses_exact_explicit_roi():
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.types import ScreenDelta
    from winpilot.utils.screenshot import Region

    watched = []

    class Watcher:
        def wait_for_region_change(self, region, timeout_s, min_ratio):
            watched.append((region, timeout_s, min_ratio))
            return ScreenDelta(True, 0.12, [region], 4)

        def status(self):
            return {"running": False}

    class Session:
        hwnd = 11
        run_id = "roi-wait"
        snapshot = None

    roi = {"x": 300, "y": 220, "width": 180, "height": 140}
    controller = FastGuiController(
        Session(), watcher=Watcher(),
        capture_fn=lambda region: np.zeros((region.height, region.width, 3), dtype=np.uint8),
    )
    out = controller.execute([{
        "action": "wait_for_visual_change",
        "region": roi,
        "timeout_s": 1.5,
        "min_ratio": 0.01,
    }], sync=False)

    assert out["ok"] is True
    assert watched[0][0] == Region(300, 220, 180, 140)
    assert out["results"][0]["data"]["change_scope"] == "explicit_or_previous_action_roi"
    assert out["results"][0]["data"]["wait_kind"] == "visual_change"


def test_local_transaction_reuses_preceding_action_roi_for_visual_wait(monkeypatch):
    from winpilot.agent import scope_guard
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.types import ScreenDelta

    monkeypatch.setattr(scope_guard, "ensure_scope", lambda *_args, **_kwargs: None)
    watched = []

    class Watcher:
        def wait_for_region_change(self, region, timeout_s, min_ratio):
            watched.append(region)
            return ScreenDelta(True, 0.2, [region], 2)

        def status(self):
            return {"running": False}

    class Session:
        hwnd = 12
        run_id = "transaction-roi"
        snapshot = None

        def _t_click(self, _args):
            return ToolResult('{"ok": true}')

    controller = FastGuiController(
        Session(), watcher=Watcher(),
        capture_fn=lambda region: np.zeros((region.height, region.width, 3), dtype=np.uint8),
    )
    out = controller.execute([
        {"action": "click", "x": 500, "y": 400},
        {"action": "wait_for_visual_change", "timeout_s": 1.0},
    ], sync=False)

    assert out["ok"] is True
    assert watched
    assert watched[0].left < 500 < watched[0].right
    assert watched[0].top < 400 < watched[0].bottom
    wait_data = out["results"][1]["data"]
    assert wait_data["region"] == {
        "x": watched[0].left, "y": watched[0].top,
        "width": watched[0].width, "height": watched[0].height,
    }


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


def test_raw_batch_child_rechecks_scope_before_device_action(monkeypatch):
    from winpilot.agent import scope_guard
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController

    called = []

    class Session:
        run_id = "scope-batch"

        def _t_click(self, args):
            called.append(args)
            return ToolResult('{"ok": true}')

    blocked = ToolResult('{"ok": false, "blocked": true, "verified": false}')
    monkeypatch.setattr(scope_guard, "ensure_scope", lambda *_args, **_kwargs: blocked)
    controller = FastGuiController(Session())

    result = controller._dispatch("click", {"x": 12, "y": 34})

    assert json.loads(result.text)["blocked"] is True
    assert called == []


def test_batch_rejects_self_fulfilling_text_wait(monkeypatch):
    from winpilot.agent import scope_guard
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController

    calls = []

    class Session:
        run_id = "self-fulfilling-batch"

        def _t_type_text(self, args):
            calls.append(("type_text", args))
            return ToolResult('{"ok": true, "verified": true}')

        def dispatch(self, name, args):
            calls.append((name, args))
            return ToolResult('{"ok": true, "verified": true}')

    monkeypatch.setattr(scope_guard, "ensure_scope", lambda *_args, **_kwargs: None)
    out = FastGuiController(Session()).execute([
        {"action": "type_text", "text": "REPORT_DONE"},
        {"action": "wait", "condition": {"text_appears": "REPORT_DONE"}},
    ], sync=False)

    assert out["ok"] is False
    assert out["controller_state"]["failed_at"] == 2
    assert "self-fulfilling" in out["results"][1]["preview"]
    assert [name for name, _args in calls] == ["type_text"]
def test_direct_subgoal_refreshes_same_hwnd_snapshot_before_grounding():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.subgoal import SubgoalExecutor
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []

    class Session:
        task = "Click Fresh Target"
        hwnd = 71
        run_id = "same-hwnd-stale-snapshot"
        _screen_epoch = 4
        snapshot = Snapshot(
            hwnd=71,
            title="Target",
            region=Region(0, 0, 800, 500),
            elements=[UIElement(
                elem_id=1, source="uia", role="button", text="Stale Target",
                cx=120, cy=100, left=80, top=80, width=80, height=40,
            )],
        )

        def dispatch(self, name, args):
            calls.append((name, dict(args)))
            if name == "observe":
                assert args == {"full": True, "force_full": True}
                self.snapshot = Snapshot(
                    hwnd=71,
                    title="Target",
                    region=Region(0, 0, 800, 500),
                    elements=[UIElement(
                        elem_id=2, source="uia", role="button", text="Fresh Target",
                        cx=320, cy=100, left=280, top=80, width=80, height=40,
                    )],
                )
                return ToolResult("fresh target observed")
            if name == "click":
                return ToolResult('{"ok": true, "verified": true}')
            return ToolResult('{"ok": true}')

    session = Session()
    result = SubgoalExecutor(session, FastGuiController(session)).execute({
        "subgoal": "click the current Fresh Target button",
        "click": {"label": "Fresh Target"},
        "constraints": {"wait": False},
    })

    payload = json.loads(result.text)
    assert any(name == "observe" for name, _args in calls)
    click_args = next(args for name, args in calls if name == "click")
    assert click_args.get("element_id") == 2
    assert click_args.get("element_id") != 1
    assert payload["ok"] is True
