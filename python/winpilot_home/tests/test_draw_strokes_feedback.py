import json
from types import SimpleNamespace

import cv2
import numpy as np
import pytest


def test_draw_stroke_normalizer_accepts_flat_segments_and_unwrapped_polyline():
    from winpilot.agent.interaction_tools import _normalize_draw_strokes

    assert _normalize_draw_strokes([
        [10, 20, 30, 40],
        [30, 40, 50, 60],
    ]) == [
        [[10, 20], [30, 40]],
        [[30, 40], [50, 60]],
    ]
    assert _normalize_draw_strokes([
        [10, 20],
        [30, 40],
        [50, 60],
    ]) == [[
        [10, 20],
        [30, 40],
        [50, 60],
    ]]


def test_draw_strokes_visual_change_marks_action_verified(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession

    before = np.full((80, 80, 3), 255, dtype=np.uint8)
    after = before.copy()
    cv2.line(after, (10, 10), (50, 50), (0, 0, 0), 6)
    captures = [before, after]

    monkeypatch.setattr(tools, "capture", lambda _region: captures.pop(0))
    monkeypatch.setattr(
        tools,
        "executor",
        SimpleNamespace(
            draw_strokes=lambda strokes, duration_s, hwnd: {
                "ok": True,
                "strokes_drawn": len(strokes),
            }
        ),
    )

    session = ToolSession(task="draw a simple visible shape")
    session.hwnd = 123
    result = session._t_draw_strokes({
        "strokes": [[[10, 10], [50, 50]]],
        "duration": 0.1,
    })
    data = json.loads(result.text)

    assert data["visual_change_detected"] is True
    assert data["verified"] is True
    assert data["verify_method"] == "pointer-path-realization"
    assert data["path_realization"]["minimum_stroke_coverage"] >= 0.58


def test_draw_strokes_rejects_cursor_only_pixel_delta(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession

    before = np.full((96, 96, 3), 255, dtype=np.uint8)
    after = before.copy()
    # The capture ROI starts at physical (2, 2), so this is the local endpoint
    # of the requested physical path.  It approximates a cursor/hover blob but
    # deliberately does not trace the path.
    cv2.circle(after, (68, 68), 9, (20, 180, 220), -1)
    captures = [before, after]
    monkeypatch.setattr(tools, "capture", lambda _region: captures.pop(0))
    monkeypatch.setattr(
        tools,
        "executor",
        SimpleNamespace(draw_strokes=lambda strokes, duration_s, hwnd: {
            "ok": True,
            "strokes_drawn": len(strokes),
        }),
    )

    session = ToolSession(task="realize a continuous pointer path")
    session.hwnd = 123
    data = json.loads(session._t_draw_strokes({
        "strokes": [[[30, 30], [70, 70]]],
        "duration": 0.1,
    }).text)

    assert data["visual_change_detected"] is True
    assert data["path_realization_detected"] is False
    assert data["ok"] is False
    assert data["mutation_applied"] is False
    assert data["failure_type"] == "DRAW_PATH_NOT_REALIZED"


def test_draw_strokes_parks_pointer_before_current_pixel_path_proof(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession
    from winpilot.utils.screenshot import Region

    before = np.full((96, 96, 3), 255, dtype=np.uint8)
    after = before.copy()
    # The physical path is (30,30)->(70,70); the effect ROI starts at (2,2).
    cv2.line(after, (28, 28), (68, 68), (0, 0, 180), 5)
    state = {"captures": 0, "parked": False, "point": None}

    def capture_frame(_region):
        state["captures"] += 1
        if state["captures"] == 1:
            return before.copy()
        # A current-pixel proof is only authoritative after the controller has
        # moved the transient pointer/hover overlay away from the gesture ROI.
        assert state["parked"] is True
        return after.copy()

    def move_mouse(x, y, hwnd=None):
        state.update({"parked": True, "point": (x, y)})
        return {"ok": True, "x": x, "y": y, "hwnd": hwnd}

    monkeypatch.setattr(tools, "capture", capture_frame)
    monkeypatch.setattr(
        "winpilot.agent.interaction_tools.window_region",
        lambda _hwnd: Region(0, 0, 320, 220),
    )
    monkeypatch.setattr(
        tools,
        "executor",
        SimpleNamespace(
            draw_strokes=lambda strokes, duration_s, hwnd: {
                "ok": True,
                "strokes_drawn": len(strokes),
            },
            move_mouse=move_mouse,
        ),
    )

    session = ToolSession(task="realize a visible pointer path")
    session.hwnd = 123
    data = json.loads(session._t_draw_strokes({
        "strokes": [[[30, 30], [70, 70]]],
        "duration": 0.1,
    }).text)

    assert data["verified"] is True
    assert data["verify_method"] == "pointer-path-realization"
    assert data["verification_cursor_park"]["ok"] is True
    assert state["point"] is not None
    x, y = state["point"]
    assert not (2 - 24 <= x <= 98 + 24 and 2 - 24 <= y <= 98 + 24)


def test_compacted_pixel_effect_does_not_look_like_goal_verification():
    from winpilot.agent.loop import AgentRunner

    raw = json.dumps({
        "ok": True,
        "verified": True,
        "wrapped_by_fast_controller": True,
        "visual_change_detected": True,
        "visual_change_ratio": 0.04,
        "verify_method": "source-verification+pixel-effect-summary",
        "native_action_executed": True,
        "auto_visual_verify": {
            "source_action": "draw_strokes",
            "verified": True,
            "visual_change_detected": True,
            "visual_change_ratio": 0.04,
            "verify_method": "source-verification+pixel-effect-summary",
            "semantic_verified": False,
            "completion_evidence": False,
            "summary_kind": "pixel_effect_only",
        },
    })

    for tool_name in ("draw_strokes", "drag"):
        compact = json.loads(AgentRunner._compact_tool_evidence(tool_name, raw))

        assert "verified" not in compact
        assert compact["action_verified"] is True
        assert compact["semantic_verified"] is False
        assert compact["completion_evidence"] is False
        assert compact["summary_kind"] == "pixel_effect_only"
        assert compact["auto_visual_verify"]["action_verified"] is True
        assert compact["auto_visual_verify"]["semantic_verified"] is False
        assert compact["auto_visual_verify"]["completion_evidence"] is False
        assert compact["auto_visual_verify"]["summary_kind"] == "pixel_effect_only"

    rolled_preview = json.loads(AgentRunner._native_action_result_preview(raw))
    assert "verified" not in rolled_preview
    assert rolled_preview["action_verified"] is True
    assert rolled_preview["semantic_verified"] is False
    assert rolled_preview["completion_evidence"] is False
    assert rolled_preview["summary_kind"] == "pixel_effect_only"


def test_draw_strokes_without_pixel_change_is_a_fail_closed_result(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession

    still_frame = np.full((80, 80, 3), 255, dtype=np.uint8)
    monkeypatch.setattr(tools, "capture", lambda _region: still_frame.copy())
    monkeypatch.setattr(
        tools,
        "executor",
        SimpleNamespace(draw_strokes=lambda strokes, duration_s, hwnd: {
            "ok": True,
            "strokes_drawn": len(strokes),
        }),
    )

    session = ToolSession(task="draw a visible line")
    session.hwnd = 123
    data = json.loads(session._t_draw_strokes({
        "strokes": [[[10, 10], [50, 50]]],
    }).text)

    assert data["ok"] is False
    assert data["verified"] is False
    assert data["visual_change_detected"] is False
    assert data["failure_type"] == "DRAW_NO_VISIBLE_CHANGE"


def test_draw_strokes_default_motion_budget_is_shared_across_many_paths(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession

    durations = []
    monkeypatch.setattr(
        tools,
        "executor",
        SimpleNamespace(draw_strokes=lambda strokes, duration_s, hwnd: (
            durations.append(duration_s)
            or {"ok": True, "strokes_drawn": len(strokes)}
        )),
    )
    session = ToolSession(task="create several continuous paths")
    session.hwnd = 123
    paths = [[[index, 10], [index + 5, 20]] for index in range(12)]

    data = json.loads(session._t_draw_strokes({
        "_draw_strokes_prepared": True,
        "strokes": paths,
        "defer_visual_verify": True,
    }).text)

    assert durations == [pytest.approx(0.1)]
    assert data["motion_duration_per_stroke_s"] == 0.1
    assert data["motion_duration_defaulted"] is True


def test_draw_strokes_returns_frame_preparation_evidence(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession

    monkeypatch.setattr(
        tools,
        "executor",
        SimpleNamespace(draw_strokes=lambda strokes, duration_s, hwnd: {
            "ok": True,
            "strokes_drawn": len(strokes),
        }),
    )
    evidence = {
        "policy": "whole_composition_uniform_affine_fit",
        "applied": True,
        "uniform_scale": 0.72,
        "translation": [0.0, 150.0],
        "input_point_count": 18,
        "execution_point_count": 12,
        "point_clipping_used": False,
    }
    session = ToolSession(task="create continuous paths")
    session.hwnd = 123

    result = session._t_draw_strokes({
        "_draw_strokes_prepared": True,
        "strokes": [[[10, 10], [50, 50]]],
        "frame_stroke_preparation": evidence,
        "defer_visual_verify": True,
    })
    data = json.loads(result.text)

    assert data["frame_stroke_preparation"] == evidence
    assert result.replay["frame_stroke_preparation"] == evidence


def test_normalized_strokes_map_through_current_target_surface(monkeypatch):
    from winpilot.agent.tools import ToolSession
    from winpilot.utils.screenshot import Region

    session = ToolSession(task="create continuous paths on the visible work surface")
    window = Region(100, 200, 400, 300)
    session._draw_target_geometry = lambda: (123, window, "")
    frame = np.zeros((300, 400, 3), dtype=np.uint8)
    monkeypatch.setattr(
        "winpilot.agent.interaction_tools.vision_cv.credible_light_work_surface",
        lambda _image: {
            "ok": True,
            "bbox": [50, 60, 350, 260],
            "light_ratio": 0.99,
            "source": "current_target_pixels",
        },
    )

    prepared = session._prepare_draw_strokes_args({
        "normalized_strokes": [[[0, 0], [500, 500], [1000, 1000]]],
    }, capture_fn=lambda region: frame.copy())

    assert prepared["strokes"] == [[
        [150, 260],
        [300, 360],
        [449, 459],
    ]]
    assert prepared["mapped_from_coordinate_space"] == "work_surface_normalized_0_1000"
    assert prepared["_draw_surface_evidence"]["surface_region"] == {
        "x": 150, "y": 260, "width": 300, "height": 200,
    }


def test_normalized_strokes_reject_missing_or_ambiguous_surface(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession
    from winpilot.utils.screenshot import Region

    session = ToolSession(task="write on a visible work surface")
    session.hwnd = 123
    session._draw_target_geometry = lambda: (123, Region(10, 20, 400, 300), "")
    frame = np.zeros((300, 400, 3), dtype=np.uint8)
    monkeypatch.setattr(tools, "capture", lambda _region: frame.copy())
    monkeypatch.setattr(
        "winpilot.agent.interaction_tools.vision_cv.credible_light_work_surface",
        lambda _image: {
            "ok": False,
            "reason": "multiple similarly plausible light work surfaces were visible",
        },
    )
    called = []
    monkeypatch.setattr(
        tools,
        "executor",
        SimpleNamespace(draw_strokes=lambda *_args, **_kwargs: called.append(True)),
    )

    data = json.loads(session._t_draw_strokes({
        "normalized_strokes": [[[100, 100], [900, 900]]],
    }).text)

    assert data["ok"] is False
    assert data["blocked"] is True
    assert data["failure_type"] == "DRAW_SURFACE_NOT_GROUNDED"
    assert "multiple similarly plausible" in data["error"]
    assert called == []


def test_normalized_strokes_reject_out_of_bounds_and_mixed_sources(monkeypatch):
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="draw paths")
    captures = []

    out_of_bounds = session._prepare_draw_strokes_args({
        "normalized_strokes": [[[0, 0], [1001, 500]]],
    }, capture_fn=lambda _region: captures.append(True))
    mixed = session._prepare_draw_strokes_args({
        "strokes": [[[1, 2], [3, 4]]],
        "normalized_strokes": [[[0, 0], [1000, 1000]]],
    }, capture_fn=lambda _region: captures.append(True))

    assert out_of_bounds["_draw_strokes_error"]["failure_type"] == "INVALID_NORMALIZED_STROKES"
    assert "outside 0..1000" in out_of_bounds["_draw_strokes_error"]["error"]
    assert mixed["_draw_strokes_error"]["failure_type"] == "DRAW_COORDINATE_SOURCE_INVALID"
    assert captures == []


def test_pre_action_surface_cache_survives_drawn_pixels_for_same_window(monkeypatch):
    from winpilot.agent.tools import ToolSession
    from winpilot.utils.screenshot import Region

    session = ToolSession(task="add several paths to one surface")
    window = Region(20, 30, 500, 400)
    session._draw_target_geometry = lambda: (55, window, "")
    frame = np.zeros((400, 500, 3), dtype=np.uint8)
    detections = []
    captures = []

    def detect(_image):
        detections.append(True)
        return {"ok": True, "bbox": [40, 80, 460, 360], "light_ratio": 0.98}

    monkeypatch.setattr(
        "winpilot.agent.interaction_tools.vision_cv.credible_light_work_surface",
        detect,
    )

    def capture_window(_region):
        captures.append(True)
        return frame.copy()

    first = session._prepare_draw_strokes_args({
        "normalized_strokes": [[[0, 0], [1000, 1000]]],
    }, capture_fn=capture_window)
    second = session._prepare_draw_strokes_args({
        "normalized_strokes": [[[1000, 0], [0, 1000]]],
    }, capture_fn=capture_window)

    assert first["strokes"] == [[[60, 110], [479, 389]]]
    assert second["strokes"] == [[[479, 110], [60, 389]]]
    assert len(captures) == 1
    assert len(detections) == 1
    assert second["_draw_surface_evidence"]["mapping_source"] == (
        "same_hwnd_and_window_geometry_cache"
    )


def test_physical_strokes_remain_compatible_and_prime_surface_cache(monkeypatch):
    from winpilot.agent.tools import ToolSession
    from winpilot.utils.screenshot import Region

    session = ToolSession(task="draw grounded physical paths")
    window = Region(0, 0, 400, 300)
    session._draw_target_geometry = lambda: (77, window, "")
    monkeypatch.setattr(
        "winpilot.agent.interaction_tools.vision_cv.credible_light_work_surface",
        lambda _image: {"ok": True, "bbox": [20, 40, 380, 280], "light_ratio": 0.97},
    )
    physical = [[[120, 140], [180, 200]]]

    prepared = session._prepare_draw_strokes_args(
        {"strokes": physical},
        capture_fn=lambda _region: np.zeros((300, 400, 3), dtype=np.uint8),
    )

    assert prepared["strokes"] == physical
    assert prepared["_draw_surface_evidence"]["surface_region"] == {
        "x": 20, "y": 40, "width": 360, "height": 240,
    }
