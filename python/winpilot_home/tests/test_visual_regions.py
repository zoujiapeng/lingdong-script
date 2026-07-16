from __future__ import annotations

import random

import cv2
import numpy as np


def _normalized(box, width, height):
    left, top, right, bottom = box
    return [
        left / width * 1000.0,
        top / height * 1000.0,
        right / width * 1000.0,
        bottom / height * 1000.0,
    ]


def _grid(seed: int):
    rng = random.Random(seed)
    width, height = 960, 720
    image = np.full((height, width, 3), 248, dtype=np.uint8)
    cell = rng.randint(68, 100)
    columns = rng.randint(3, 5)
    rows = rng.randint(3, 5)
    left = rng.randint(100, width - columns * cell - 100)
    top = rng.randint(90, height - rows * cell - 90)
    for x in range(0, width, 42):
        cv2.line(image, (x, 0), (x, height), (232, 235, 239), 1)
    for y in range(0, height, 42):
        cv2.line(image, (0, y), (width, y), (232, 235, 239), 1)
    for column in range(columns + 1):
        x = left + column * cell
        cv2.line(image, (x, top), (x, top + rows * cell), (30, 35, 42), 5)
    for row in range(rows + 1):
        y = top + row * cell
        cv2.line(image, (left, y), (left + columns * cell, y), (30, 35, 42), 5)
    occupied = (rng.randrange(columns), rng.randrange(rows))
    occupied_center = (
        left + int((occupied[0] + 0.5) * cell),
        top + int((occupied[1] + 0.5) * cell),
    )
    cv2.circle(
        image, occupied_center, max(12, cell // 4), (35, 38, 44), 5,
    )
    candidates = [
        (column, row)
        for row in range(rows)
        for column in range(columns)
        if (column, row) != occupied
    ]
    target = rng.choice(candidates)
    target_box = (
        left + target[0] * cell,
        top + target[1] * cell,
        left + (target[0] + 1) * cell,
        top + (target[1] + 1) * cell,
    )
    board_box = (left, top, left + columns * cell, top + rows * cell)
    return image, target_box, board_box


def test_visible_region_inventory_exposes_repeated_current_pixel_cells():
    from winpilot.tasking.visual_regions import inventory_visible_regions

    for seed in range(12):
        image, target, _board = _grid(seed + 700)
        inventory = inventory_visible_regions(image)
        repeated = [
            item for item in inventory
            if item.get("shape") == "rectangular_boundary"
            and int(item.get("peer_count") or 0) >= 8
        ]
        assert repeated, seed
        target_center = (
            (target[0] + target[2]) / 2.0,
            (target[1] + target[3]) / 2.0,
        )
        height, width = image.shape[:2]
        selected = min(repeated, key=lambda item: (
            item["center"][0] / 1000.0 * width - target_center[0]
        ) ** 2 + (
            item["center"][1] / 1000.0 * height - target_center[1]
        ) ** 2)
        selected_center = (
            selected["center"][0] / 1000.0 * width,
            selected["center"][1] / 1000.0 * height,
        )
        assert target[0] <= selected_center[0] <= target[2]
        assert target[1] <= selected_center[1] <= target[3]
        assert int(selected["row_index"]) >= 1
        assert int(selected["column_index"]) >= 1
        assert selected["candidate_id"].startswith("visible-region-")


def test_visible_bounded_region_randomized_grid_cells_are_contained():
    from winpilot.tasking.visual_regions import resolve_visible_bounded_region

    for seed in range(30):
        image, target, _board = _grid(seed)
        height, width = image.shape[:2]
        rng = random.Random(seed + 1000)
        center_x = (target[0] + target[2]) / 2.0 + rng.uniform(-7, 7)
        center_y = (target[1] + target[3]) / 2.0 + rng.uniform(-7, 7)
        rough_width = (target[2] - target[0]) * rng.uniform(0.72, 1.14)
        rough_height = (target[3] - target[1]) * rng.uniform(0.68, 1.10)
        rough = _normalized((
            center_x - rough_width / 2.0,
            center_y - rough_height / 2.0,
            center_x + rough_width / 2.0,
            center_y + rough_height / 2.0,
        ), width, height)
        resolved = resolve_visible_bounded_region(
            image, rough, [0, 0, 1000, 1000],
        )
        assert resolved is not None, (seed, rough, target)
        box = resolved["pixel_box"]
        resolved_center = ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
        assert target[0] <= resolved_center[0] <= target[2]
        assert target[1] <= resolved_center[1] <= target[3]
        assert resolved["boundary_support"] >= 0.62
        assert resolved["margin"] >= 0.055


def test_visible_bounded_region_does_not_invent_boundary_on_blank_canvas():
    from winpilot.tasking.visual_regions import resolve_visible_bounded_region

    image = np.full((500, 700, 3), 245, dtype=np.uint8)
    assert resolve_visible_bounded_region(
        image, [300, 300, 600, 650], [0, 0, 1000, 1000],
    ) is None


def test_anchored_subregion_selects_child_not_coarse_container():
    from winpilot.tasking.visual_regions import resolve_visible_subregion_at_anchor

    for seed in range(20):
        image, target, board = _grid(seed + 200)
        height, width = image.shape[:2]
        anchor = [
            (target[0] + target[2]) / 2.0 / width * 1000.0,
            (target[1] + target[3]) / 2.0 / height * 1000.0,
        ]
        resolved = resolve_visible_subregion_at_anchor(
            image,
            _normalized(board, width, height),
            anchor,
            [0, 0, 1000, 1000],
        )
        assert resolved is not None, seed
        box = resolved["pixel_box"]
        center = ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
        assert target[0] <= center[0] <= target[2]
        assert target[1] <= center[1] <= target[3]
        assert resolved["area_ratio"] < 0.45


def test_anchored_subregion_recovers_unique_nearby_overlapping_boundary():
    """A coarse semantic box may share a frame/client chrome offset.

    Recovery is based on current-pixel boundary evidence and uniqueness, not
    on a known grid layout or an application-specific coordinate correction.
    """
    from winpilot.tasking.visual_regions import resolve_visible_subregion_at_anchor

    image, target, board = _grid(404)
    height, width = image.shape[:2]
    cell_width = target[2] - target[0]
    cell_height = target[3] - target[1]
    top_left = (
        board[0], board[1], board[0] + cell_width, board[1] + cell_height,
    )
    # This coarse region overlaps the top of the visible child, while its
    # semantic anchor is displaced just above it.  Exact containment cannot
    # work, but there is one uniquely nearest closed boundary.
    rough = (
        top_left[0] - cell_width * 0.35,
        top_left[1] - cell_height * 0.85,
        top_left[2] + cell_width * 0.35,
        top_left[1] + cell_height * 0.35,
    )
    anchor = [
        (top_left[0] + top_left[2]) / 2.0 / width * 1000.0,
        (top_left[1] - cell_height * 0.25) / height * 1000.0,
    ]

    resolved = resolve_visible_subregion_at_anchor(
        image,
        _normalized(rough, width, height),
        anchor,
        [0, 0, 1000, 1000],
    )

    assert resolved is not None
    assert (
        resolved["acceptance_policy"]
        == "nearest_unique_overlapping_boundary_to_coarse_anchor"
    )
    box = resolved["pixel_box"]
    center = ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
    assert top_left[0] <= center[0] <= top_left[2]
    assert top_left[1] <= center[1] <= top_left[3]
    assert resolved["contains_anchor"] is False
    assert resolved["container_overlap"] >= 0.12


def test_anchored_subregion_rejects_equidistant_nearby_boundaries():
    from winpilot.tasking.visual_regions import resolve_visible_subregion_at_anchor

    width, height = 700, 500
    image = np.full((height, width, 3), 246, dtype=np.uint8)
    # Two equally supported, equally distant candidates overlap the same
    # coarse region.  The resolver must fail closed instead of guessing.
    for left in (205, 395):
        cv2.rectangle(image, (left, 260), (left + 100, 360), (25, 30, 38), 5)
    rough = _normalized((245, 150, 455, 300), width, height)
    anchor = [350 / width * 1000.0, 210 / height * 1000.0]

    assert resolve_visible_subregion_at_anchor(
        image, rough, anchor, [0, 0, 1000, 1000],
    ) is None


def test_draw_strokes_refines_target_relative_path_to_current_boundary(tmp_path):
    from winpilot.tasking.interaction import (
        ExpectedInteractionEffect,
        InteractionAction,
        InteractionDecision,
    )
    from winpilot.tasking.visual_geometry import refine_interaction_geometry

    image, target, board = _grid(81)
    height, width = image.shape[:2]
    exact = _normalized(target, width, height)
    rough = _normalized(board, width, height)
    anchor = [
        (exact[0] + exact[2]) / 2.0,
        (exact[1] + exact[3]) / 2.0,
    ]
    decision = InteractionDecision(
        scene_summary="a bounded writable surface",
        mode="shared_visual_session",
        turn_owner="agent",
        agent_should_act=True,
        surface_region=[0, 0, 1000, 1000],
        interaction_region=[0, 0, 1000, 1000],
        roles={},
        rules=[],
        confidence=0.95,
        ambiguity=[],
        actions=[InteractionAction(
            action="draw_strokes",
            stroke_paths=[
                {
                    "coordinate_space": "target_region_0_1000",
                    "points": [[180, 180], [820, 820]],
                },
                {
                    "coordinate_space": "target_region_0_1000",
                    "points": [[820, 180], [180, 820]],
                },
            ],
            target_region=rough,
            target_anchor=anchor,
            interaction_mechanism="freehand_strokes",
            affordance_evidence="visible bounded writable region",
            expected_effect_region=rough,
            effect_region_binding="target_region",
        )],
        expected_effect=ExpectedInteractionEffect(
            description="a visible mark appears inside the selected region",
            region=rough,
            region_binding="target_region",
            binding_action_index=0,
        ),
        continuation="reobserve",
        frame_id=4,
        observation_artifact_id="art-current",
    )
    path = tmp_path / "grid.png"
    assert cv2.imwrite(str(path), image)

    refined, proof = refine_interaction_geometry(
        decision, path, expected_size=(width, height),
    )

    resolved = refined.actions[0].target_region
    center = (
        (resolved[0] + resolved[2]) / 2.0 / 1000.0 * width,
        (resolved[1] + resolved[3]) / 2.0 / 1000.0 * height,
    )
    assert target[0] <= center[0] <= target[2]
    assert target[1] <= center[1] <= target[3]
    assert refined.expected_effect.region == resolved
    assert proof["applied"] is True
    assert proof["refinements"][0]["provider"] == "current_frame_bounded_region"
