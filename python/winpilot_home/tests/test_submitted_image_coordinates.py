from __future__ import annotations

import copy
import math
import random

import pytest


def _map_point(mapper, payload):
    return mapper.map_point_pair(
        payload,
        image_keys=("image_x", "image_y"),
        frame_keys=("frame_x", "frame_y"),
        context="click",
    )


def _map_drag(mapper, payload, *, max_points=32):
    return mapper.map_drag_image_coordinates(
        payload, context="drag", max_points=max_points,
    )


def _map_strokes(mapper, payload, *, max_strokes=16, max_points=16):
    return mapper.map_image_strokes(
        payload,
        context="draw_strokes",
        max_strokes=max_strokes,
        max_points_per_stroke=max_points,
    )


def test_point_mapping_is_pure_and_preserves_exact_provenance() -> None:
    from winpilot.visual_runtime.submitted_image_coordinates import (
        SubmittedImageCoordinateMapper,
    )

    original = {"image_x": 237, "image_y": 307, "button": "left"}
    mapper = SubmittedImageCoordinateMapper((1120, 630))

    mapped, source = _map_point(mapper, original)

    assert original == {"image_x": 237, "image_y": 307, "button": "left"}
    assert mapped == {
        "frame_x": 237 * 1000.0 / 1119,
        "frame_y": 307 * 1000.0 / 629,
        "button": "left",
    }
    assert source == {"image_x": 237.0, "image_y": 307.0}


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"image_x": 1}, "requires both image_x and image_y"),
        ({"image_y": 1}, "requires both image_x and image_y"),
        (
            {"image_x": 1, "image_y": 2, "frame_x": 3},
            "mixed submitted-image and normalized-frame coordinates",
        ),
        ({"image_x": True, "image_y": 2}, "numeric submitted-image"),
        ({"image_x": math.nan, "image_y": 2}, "exact submitted image bounds"),
        ({"image_x": math.inf, "image_y": 2}, "exact submitted image bounds"),
        ({"image_x": -1, "image_y": 2}, "exact submitted image bounds"),
        ({"image_x": 10.001, "image_y": 10}, "exact submitted image bounds"),
    ],
)
def test_point_mapping_rejects_ambiguous_or_invalid_values_atomically(
    payload: dict, message: str,
) -> None:
    from winpilot.visual_runtime.submitted_image_coordinates import (
        SubmittedImageCoordinateMapper,
    )

    original = dict(payload)
    mapper = SubmittedImageCoordinateMapper((10, 10))

    with pytest.raises(ValueError, match=message):
        _map_point(mapper, payload)

    assert payload == original


def test_extent_style_right_bottom_edge_clamps_to_last_real_pixel() -> None:
    from winpilot.visual_runtime.submitted_image_coordinates import (
        SubmittedImageCoordinateMapper,
    )

    mapped, source = _map_point(
        SubmittedImageCoordinateMapper((10, 8)),
        {"image_x": 10, "image_y": 8},
    )

    assert mapped == {"frame_x": 1000.0, "frame_y": 1000.0}
    assert source == {"image_x": 9.0, "image_y": 7.0}


def test_point_mapping_is_monotonic_for_random_sizes() -> None:
    from winpilot.visual_runtime.submitted_image_coordinates import (
        SubmittedImageCoordinateMapper,
    )

    rng = random.Random(20260722)
    for _ in range(250):
        width = rng.randint(2, 4096)
        height = rng.randint(2, 2160)
        mapper = SubmittedImageCoordinateMapper((width, height))
        x1, x2 = sorted((rng.randint(0, width - 1), rng.randint(0, width - 1)))
        y1, y2 = sorted((rng.randint(0, height - 1), rng.randint(0, height - 1)))

        first, _ = _map_point(mapper, {"image_x": x1, "image_y": y1})
        second, _ = _map_point(mapper, {"image_x": x2, "image_y": y2})

        assert 0.0 <= first["frame_x"] <= second["frame_x"] <= 1000.0
        assert 0.0 <= first["frame_y"] <= second["frame_y"] <= 1000.0


@pytest.mark.parametrize("submitted_size", [None, (0, 0), (10, 0), ("10", 10)])
def test_invalid_dimensions_are_ignored_when_no_image_coordinate_is_consumed(
    submitted_size,
) -> None:
    from winpilot.visual_runtime.submitted_image_coordinates import (
        SubmittedImageCoordinateMapper,
    )

    mapper = SubmittedImageCoordinateMapper(submitted_size)
    original = {"frame_x": 25, "frame_y": 75, "text": "unchanged"}

    point_payload, point_source = _map_point(mapper, original)
    constraint_payload, constraint_source = mapper.map_constraint_image_region(
        original, context="click",
    )

    assert point_payload == original
    assert constraint_payload == original
    assert point_source == {}
    assert constraint_source == []


def test_constraint_mapping_is_pure_and_preserves_both_coordinate_layers() -> None:
    from winpilot.visual_runtime.submitted_image_coordinates import (
        SubmittedImageCoordinateMapper,
    )

    original = {
        "constraint_image_region": [100, 50, 900, 500],
        "button": "left",
    }
    mapper = SubmittedImageCoordinateMapper((1120, 630))

    mapped, source = mapper.map_constraint_image_region(original, context="drag")

    assert original == {
        "constraint_image_region": [100, 50, 900, 500],
        "button": "left",
    }
    assert mapped == {
        "constraint_frame_region": [
            100 * 1000.0 / 1119,
            50 * 1000.0 / 629,
            900 * 1000.0 / 1119,
            500 * 1000.0 / 629,
        ],
        "button": "left",
    }
    assert source == [100.0, 50.0, 900.0, 500.0]


@pytest.mark.parametrize(
    "payload, message",
    [
        (
            {
                "constraint_image_region": [0, 0, 8, 8],
                "constraint_frame_region": [0, 0, 8, 8],
            },
            "both submitted-image and normalized-frame constraints",
        ),
        ({"constraint_image_region": [0, 0, 8]}, "requires.*bounds"),
        ({"constraint_image_region": [0, 0, True, 8]}, "numeric.*bounds"),
        ({"constraint_image_region": [0, 0, math.nan, 8]}, "positive rectangle"),
        ({"constraint_image_region": [0, 0, math.inf, 8]}, "positive rectangle"),
        ({"constraint_image_region": [4, 4, 4, 8]}, "positive rectangle"),
        ({"constraint_image_region": [0, 0, 10, 8]}, "exact submitted image bounds"),
    ],
)
def test_constraint_mapping_rejects_invalid_values_without_mutating_input(
    payload: dict, message: str,
) -> None:
    from winpilot.visual_runtime.submitted_image_coordinates import (
        SubmittedImageCoordinateMapper,
    )

    original = {
        key: list(value) if isinstance(value, list) else value
        for key, value in payload.items()
    }
    mapper = SubmittedImageCoordinateMapper((10, 10))

    with pytest.raises(ValueError, match=message):
        mapper.map_constraint_image_region(payload, context="drag")

    assert payload == original


def test_drag_mapping_preserves_path_order_adjacent_dedup_and_input() -> None:
    from winpilot.visual_runtime.submitted_image_coordinates import (
        SubmittedImageCoordinateMapper,
    )

    original = {
        "image_x1": 0,
        "image_y1": 0,
        "image_x2": 99,
        "image_y2": 49,
        "image_points": [[10, 5], [10, 5], [70, 30], [10, 5]],
        "duration_ms": 400,
    }
    before = copy.deepcopy(original)

    mapped, source = _map_drag(
        SubmittedImageCoordinateMapper((100, 50)), original,
    )

    assert original == before
    assert mapped["frame_points"] == [
        [10 * 1000.0 / 99, 5 * 1000.0 / 49],
        [70 * 1000.0 / 99, 30 * 1000.0 / 49],
        [10 * 1000.0 / 99, 5 * 1000.0 / 49],
    ]
    assert source["image_points"] == [[10.0, 5.0], [70.0, 30.0], [10.0, 5.0]]
    assert [source[key] for key in ("image_x1", "image_y1", "image_x2", "image_y2")] == [
        0.0, 0.0, 99.0, 49.0,
    ]
    assert mapped["duration_ms"] == 400


def test_drag_maps_each_endpoint_and_meaningful_path_point_once(monkeypatch) -> None:
    from winpilot.visual_runtime.submitted_image_coordinates import (
        SubmittedImageCoordinateMapper,
    )

    calls = []
    original_number = SubmittedImageCoordinateMapper._number

    def counting_number(value, field, **kwargs):
        calls.append((value, field))
        return original_number(value, field, **kwargs)

    monkeypatch.setattr(
        SubmittedImageCoordinateMapper, "_number", staticmethod(counting_number),
    )
    _map_drag(SubmittedImageCoordinateMapper((100, 100)), {
        "image_x1": 1, "image_y1": 2, "image_x2": 90, "image_y2": 91,
        "image_points": [[20, 21], [20, 21], [30, 31]],
    })

    assert len(calls) == 2 * (2 + 2)


@pytest.mark.parametrize(
    "payload, max_points, message",
    [
        ({"image_x1": 1}, 32, "requires both image_x1 and image_y1"),
        (
            {"image_x1": 1, "image_y1": 2, "image_x2": 8, "image_y2": 9,
             "frame_x1": 1},
            32,
            "mixed submitted-image and normalized-frame coordinates",
        ),
        ({"image_points": [[1, 2]]}, 32, "require drag endpoints"),
        (
            {"image_x1": 1, "image_y1": 2, "image_x2": 8, "image_y2": 9,
             "frame_points": [[3, 4]]},
            32,
            "image endpoints with normalized frame_points",
        ),
        (
            {"image_x1": 1, "image_y1": 2, "image_x2": 8, "image_y2": 9,
             "image_points": "bad"},
            32,
            "image_points must be an array",
        ),
        (
            {"image_x1": 1, "image_y1": 2, "image_x2": 8, "image_y2": 9,
             "image_points": [[1, 1], [2, 2], [3, 3]]},
            2,
            "at most 2 meaningful points",
        ),
        (
            {"image_x1": 1, "image_y1": 2, "image_x2": 8, "image_y2": 9,
             "image_points": [[1, 2, 3]]},
            32,
            r"image_points\[0\] must be",
        ),
        (
            {"image_x1": 1, "image_y1": 2, "image_x2": math.inf, "image_y2": 9},
            32,
            "exact submitted image bounds",
        ),
        (
            {"image_x1": 1, "image_y1": 2, "image_x2": 8, "image_y2": 9,
             "image_points": [[math.nan, 2]]},
            32,
            "exact submitted image bounds",
        ),
    ],
)
def test_drag_mapping_fail_closed_without_mutating_input(
    payload: dict, max_points: int, message: str,
) -> None:
    from winpilot.visual_runtime.submitted_image_coordinates import (
        SubmittedImageCoordinateMapper,
    )

    before = copy.deepcopy(payload)
    with pytest.raises(ValueError, match=message):
        _map_drag(
            SubmittedImageCoordinateMapper((10, 10)),
            payload,
            max_points=max_points,
        )
    assert payload == before


def test_stroke_mapping_preserves_stroke_point_order_count_and_input() -> None:
    from winpilot.visual_runtime.submitted_image_coordinates import (
        SubmittedImageCoordinateMapper,
    )

    original = {
        "image_strokes": [
            [[0, 0], [25, 10], [99, 49]],
            [[80, 40], [20, 5]],
        ],
        "color": "red",
    }
    before = copy.deepcopy(original)

    mapped, source = _map_strokes(
        SubmittedImageCoordinateMapper((100, 50)), original,
    )

    assert original == before
    assert [len(stroke) for stroke in mapped["frame_strokes"]] == [3, 2]
    assert source["image_strokes"] == [
        [[0.0, 0.0], [25.0, 10.0], [99.0, 49.0]],
        [[80.0, 40.0], [20.0, 5.0]],
    ]
    assert mapped["frame_strokes"][0][1] == [25 * 1000.0 / 99, 10 * 1000.0 / 49]
    assert mapped["color"] == "red"


@pytest.mark.parametrize(
    "payload, limits, message",
    [
        (
            {"image_strokes": [[[1, 1], [2, 2]]], "frame_strokes": [[[1, 1], [2, 2]]]},
            (16, 16),
            "mixed submitted-image and normalized-frame coordinates",
        ),
        ({"image_strokes": []}, (16, 16), "at least one stroke"),
        (
            {"image_strokes": [[[1, 1], [2, 2]], [[3, 3], [4, 4]]]},
            (1, 16),
            "at most 1 strokes",
        ),
        ({"image_strokes": [[[1, 1]]]}, (16, 16), "must contain 2..16"),
        (
            {"image_strokes": [[[1, 1], [2, 2], [3, 3]]]},
            (16, 2),
            "must contain 2..2",
        ),
        ({"image_strokes": [[[1, 1], [2, 2, 3]]]}, (16, 16), r"\[0\]\[1\] must be"),
        ({"image_strokes": [[[1, 1], [math.nan, 2]]]}, (16, 16), "exact submitted image bounds"),
        ({"image_strokes": [[[1, 1], [math.inf, 2]]]}, (16, 16), "exact submitted image bounds"),
    ],
)
def test_stroke_mapping_fail_closed_without_mutating_input(
    payload: dict, limits: tuple[int, int], message: str,
) -> None:
    from winpilot.visual_runtime.submitted_image_coordinates import (
        SubmittedImageCoordinateMapper,
    )

    before = copy.deepcopy(payload)
    with pytest.raises(ValueError, match=message):
        _map_strokes(
            SubmittedImageCoordinateMapper((10, 10)),
            payload,
            max_strokes=limits[0],
            max_points=limits[1],
        )
    assert payload == before


def test_legacy_constraint_rejects_normalized_drag_path_atomically(monkeypatch) -> None:
    from winpilot.agent.loop import AgentRunner, NativeFrameCoordinateError
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("keep the whole drag inside the visible constraint")
    runner.session.hwnd = 77
    runner.session._screen_epoch = 5
    runner._visual_frame_hwnd = 77
    runner._visual_frame_epoch = 5
    runner._visual_frame_region = (0, 0, 100, 100)
    runner._visual_submitted_size = (100, 100)
    runner._visual_frame_id = 9
    runner._last_visual_feedback_b64 = "frame"
    region = Region(0, 0, 100, 100)
    monkeypatch.setattr(Region, "clamp_to_screen", lambda self: self)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: region)
    original = {
        "image_x1": 20, "image_y1": 20, "image_x2": 40, "image_y2": 40,
        "image_points": [[60, 30]],
        "constraint_image_region": [10, 10, 50, 50],
    }
    before = copy.deepcopy(original)

    with pytest.raises(NativeFrameCoordinateError, match="leaves the declared"):
        runner._map_normalized_frame_coordinates("drag", original)

    assert original == before


def test_legacy_point_delegate_keeps_negative_origin_corner_and_monotonic_mapping(
    monkeypatch,
) -> None:
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("operate from the exact submitted image")
    runner.session.hwnd = 77
    runner.session._screen_epoch = 5
    runner._visual_frame_hwnd = 77
    runner._visual_frame_epoch = 5
    runner._visual_frame_region = (-1500, -400, 1920, 1080)
    runner._visual_submitted_size = (1120, 630)
    runner._visual_frame_id = 9
    runner._last_visual_feedback_b64 = "frame"
    region = Region(-1500, -400, 1920, 1080)
    monkeypatch.setattr(Region, "clamp_to_screen", lambda self: self)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: region)

    points = [
        runner._map_normalized_frame_coordinates(
            "click", {"image_x": image_x, "image_y": image_y},
        )
        for image_x, image_y in ((0, 0), (200, 100), (800, 500), (1119, 629))
    ]

    assert [(item["x"], item["y"]) for item in points][0] == (-1500, -400)
    assert [(item["x"], item["y"]) for item in points][-1] == (419, 679)
    assert [item["x"] for item in points] == sorted(item["x"] for item in points)
    assert [item["y"] for item in points] == sorted(item["y"] for item in points)


def test_legacy_frame_coordinates_do_not_require_submitted_image_dimensions(
    monkeypatch,
) -> None:
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("use legacy normalized coordinates without image pixels")
    runner.session.hwnd = 77
    runner.session._screen_epoch = 5
    runner._visual_frame_hwnd = 77
    runner._visual_frame_epoch = 5
    runner._visual_frame_region = (-100, 50, 1001, 501)
    runner._visual_submitted_size = (0, 0)
    runner._visual_frame_id = 9
    runner._last_visual_feedback_b64 = "frame"
    region = Region(-100, 50, 1001, 501)
    monkeypatch.setattr(Region, "clamp_to_screen", lambda self: self)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: region)

    mapped = runner._map_normalized_frame_coordinates(
        "click", {"frame_x": 500, "frame_y": 500},
    )

    assert (mapped["x"], mapped["y"]) == (400, 300)
