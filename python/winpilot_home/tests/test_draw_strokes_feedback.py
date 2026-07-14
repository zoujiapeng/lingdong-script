import json
from types import SimpleNamespace

import numpy as np


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
    after[20:60, 20:60] = 0
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
    assert data["verify_method"] == "cv-diff"
    assert "instead of redrawing" in data["visual_feedback"]


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
