from __future__ import annotations

import math

import cv2
import numpy as np
import pytest

from winpilot.tasking.interaction import InteractionAction
from winpilot.tasking.visual_path import GuidedPathError, refine_guided_drag_path


def _normalized(point: tuple[int, int], width: int, height: int) -> list[float]:
    return [point[0] / width * 1000.0, point[1] / height * 1000.0]


def _distance_to_segment(point, first, second) -> float:
    delta_x = second[0] - first[0]
    delta_y = second[1] - first[1]
    denominator = delta_x * delta_x + delta_y * delta_y or 1.0
    fraction = max(0.0, min(1.0, (
        (point[0] - first[0]) * delta_x
        + (point[1] - first[1]) * delta_y
    ) / denominator))
    return math.hypot(
        point[0] - (first[0] + fraction * delta_x),
        point[1] - (first[1] + fraction * delta_y),
    )


def _orient(
    point: tuple[int, int],
    orientation: str,
    width: int,
    height: int,
) -> tuple[int, int]:
    x, y = point
    if "mirror_x" in orientation:
        x = width - x
    if "mirror_y" in orientation:
        y = height - y
    return x, y


@pytest.mark.parametrize(
    ("orientation", "reverse"),
    [
        ("identity", False),
        ("identity", True),
        ("mirror_x", False),
        ("mirror_x", True),
        ("mirror_y", False),
        ("mirror_y", True),
        ("mirror_x_mirror_y", False),
        ("mirror_x_mirror_y", True),
    ],
)
def test_guided_path_snaps_coarse_bends_to_source_safe_visible_centerline(
    orientation: str,
    reverse: bool,
):
    height, width = 700, 1000
    image = np.full((height, width, 3), 245, dtype=np.uint8)
    centerline = [
        _orient(point, orientation, width, height)
        for point in [(150, 550), (560, 550), (560, 200), (850, 200)]
    ]
    planner_bends = [
        _orient(point, orientation, width, height)
        for point in [(515, 550), (515, 200)]
    ]
    if reverse:
        centerline.reverse()
        planner_bends.reverse()
    cv2.polylines(
        image, [np.array(centerline, np.int32)], False,
        (70, 90, 110), 150, lineType=cv2.LINE_AA,
    )
    cv2.polylines(
        image, [np.array(centerline, np.int32)], False,
        (240, 226, 215), 140, lineType=cv2.LINE_AA,
    )
    cv2.circle(image, centerline[0], 32, (230, 110, 30), -1)
    cv2.circle(image, centerline[0], 32, (100, 50, 20), 4)
    cv2.circle(image, centerline[-1], 62, (40, 150, 60), 6)

    controller_points, proof = refine_guided_drag_path(
        image,
        start=tuple(_normalized(centerline[0], width, height)),
        end=tuple(_normalized(centerline[-1], width, height)),
        # The semantic route is correct but hugs the inside edge by 45 px.
        planner_points=[_normalized(point, width, height) for point in planner_bends],
        source_region=[
            (centerline[0][0] - 32) / width * 1000.0,
            (centerline[0][1] - 32) / height * 1000.0,
            (centerline[0][0] + 32) / width * 1000.0,
            (centerline[0][1] + 32) / height * 1000.0,
        ],
        target_region=[
            (centerline[-1][0] - 62) / width * 1000.0,
            (centerline[-1][1] - 62) / height * 1000.0,
            (centerline[-1][0] + 62) / width * 1000.0,
            (centerline[-1][1] + 62) / height * 1000.0,
        ],
        interaction_region=[0, 0, 1000, 1000],
    )

    pixel_points = [
        centerline[0],
        *[
            (point[0] / 1000.0 * width, point[1] / 1000.0 * height)
            for point in controller_points
        ],
        centerline[-1],
    ]
    for first, second in zip(pixel_points, pixel_points[1:]):
        samples = max(2, int(math.ceil(math.dist(first, second) / 2.0)))
        for index in range(samples + 1):
            fraction = index / samples
            point = (
                first[0] + (second[0] - first[0]) * fraction,
                first[1] + (second[1] - first[1]) * fraction,
            )
            distance = min(
                _distance_to_segment(point, segment_start, segment_end)
                for segment_start, segment_end in zip(centerline, centerline[1:])
            )
            # Corridor radius 70 minus source radius 32, with controller margin.
            assert distance <= 38.0
    assert proof["verified"] is True
    assert proof["direct_segment_legal"] is False
    assert proof["controller_waypoint_count"] >= 2
    assert proof["route_mask_ratio"] < 0.65


def test_guided_path_rejects_uniform_unbounded_background():
    image = np.full((500, 800, 3), (220, 220, 220), dtype=np.uint8)
    with pytest.raises(GuidedPathError, match="too broad"):
        refine_guided_drag_path(
            image,
            start=(100, 700),
            end=(900, 300),
            planner_points=[[450, 700], [450, 300]],
            source_region=[70, 650, 130, 750],
            target_region=[850, 220, 950, 380],
            interaction_region=[0, 0, 1000, 1000],
        )


def test_guided_path_retries_authoritative_resolution_when_coarse_grid_disconnects(
    monkeypatch,
):
    import winpilot.tasking.visual_path as path_module

    height, width = 700, 1000
    image = np.full((height, width, 3), 245, dtype=np.uint8)
    centerline = [(150, 550), (560, 550), (560, 200), (850, 200)]
    cv2.polylines(
        image, [np.array(centerline, np.int32)], False,
        (70, 90, 110), 150, lineType=cv2.LINE_AA,
    )
    cv2.polylines(
        image, [np.array(centerline, np.int32)], False,
        (240, 226, 215), 140, lineType=cv2.LINE_AA,
    )
    cv2.circle(image, centerline[0], 32, (230, 110, 30), -1)
    cv2.circle(image, centerline[-1], 62, (40, 150, 60), 6)
    real_astar = path_module._astar
    calls: list[tuple[int, int]] = []

    def disconnect_coarse(mask, clearance, start, target, *, work_budget=120_000):
        calls.append(mask.shape)
        if mask.shape != image.shape[:2]:
            raise GuidedPathError("synthetic coarse-grid disconnect")
        return real_astar(
            mask, clearance, start, target, work_budget=work_budget,
        )

    monkeypatch.setattr(path_module, "_astar", disconnect_coarse)
    points, proof = refine_guided_drag_path(
        image,
        start=tuple(_normalized(centerline[0], width, height)),
        end=tuple(_normalized(centerline[-1], width, height)),
        planner_points=[
            _normalized((515, 550), width, height),
            _normalized((515, 200), width, height),
        ],
        source_region=[118, 740, 182, 831],
        target_region=[788, 197, 912, 374],
        interaction_region=[0, 0, 1000, 1000],
    )

    assert points
    assert len(calls) == 2
    assert proof["search_scale"] == 1
    assert proof["search_fallback"] == "full_resolution_after_coarse_disconnect"
    assert proof["coarse_failure"] == "synthetic coarse-grid disconnect"


def test_guided_path_does_not_treat_destination_decoration_as_obstacle():
    height, width = 700, 1000
    image = np.full((height, width, 3), 245, dtype=np.uint8)
    centerline = [(850, 550), (560, 550), (560, 200), (150, 200)]
    cv2.polylines(
        image, [np.array(centerline, np.int32)], False,
        (70, 90, 110), 150, lineType=cv2.LINE_AA,
    )
    cv2.polylines(
        image, [np.array(centerline, np.int32)], False,
        (240, 226, 215), 140, lineType=cv2.LINE_AA,
    )
    cv2.circle(image, centerline[0], 30, (230, 110, 30), -1)
    # A dashed destination border extends a few pixels outside the grounded
    # usable interior.  These are endpoint decorations, not route obstacles.
    for angle in range(0, 360, 30):
        cv2.ellipse(
            image, centerline[-1], (56, 56), 0, angle, angle + 18,
            (35, 155, 85), 5,
        )
    cv2.putText(
        image, "END", (126, 206), cv2.FONT_HERSHEY_SIMPLEX,
        0.45, (20, 95, 55), 1, cv2.LINE_AA,
    )

    points, proof = refine_guided_drag_path(
        image,
        start=tuple(_normalized(centerline[0], width, height)),
        end=tuple(_normalized(centerline[-1], width, height)),
        planner_points=[
            _normalized((515, 550), width, height),
            _normalized((515, 200), width, height),
        ],
        source_region=[820, 743, 880, 829],
        # Deliberately represents the inner usable destination, not the full
        # anti-aliased/dashed decoration.
        target_region=[100, 214, 200, 357],
        interaction_region=[0, 0, 1000, 1000],
    )

    assert points
    assert proof["verified"] is True
    assert proof["target_overlay_allowance_px"] > proof[
        "source_overlay_allowance_px"
    ]
    assert proof["sampled_min_clearance_px"] >= proof["required_clearance_px"]


def test_drag_path_mode_is_typed_and_cannot_contradict_waypoints():
    guided = InteractionAction(
        action="drag",
        frame_x1=100,
        frame_y1=100,
        frame_x2=900,
        frame_y2=900,
        frame_points=[[500, 100], [500, 900]],
        source_region=[50, 50, 150, 150],
        target_region=[850, 850, 950, 950],
    )
    assert guided.path_mode == "guided"

    with pytest.raises(ValueError, match="guided drag requires"):
        InteractionAction(
            action="drag",
            path_mode="guided",
            frame_x1=100,
            frame_y1=100,
            frame_x2=900,
            frame_y2=900,
            source_region=[50, 50, 150, 150],
            target_region=[850, 850, 950, 950],
        )

    with pytest.raises(ValueError, match="direct drag cannot"):
        InteractionAction(
            action="drag",
            path_mode="direct",
            frame_x1=100,
            frame_y1=100,
            frame_x2=900,
            frame_y2=900,
            frame_points=[[500, 500]],
            source_region=[50, 50, 150, 150],
            target_region=[850, 850, 950, 950],
        )
