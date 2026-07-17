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


def _open_partition(seed: int, *, target_cell: tuple[int, int] | None = None):
    """Random repeated dividers with no outer rectangle or domain semantics."""
    rng = random.Random(seed)
    width, height = 960, 720
    image = np.full((height, width, 3), 248, dtype=np.uint8)
    cell_width = rng.randint(78, 112)
    cell_height = rng.randint(72, 105)
    left = rng.randint(220, width - 3 * cell_width - 140)
    top = rng.randint(150, height - 3 * cell_height - 110)
    for divider in (1, 2):
        x = left + divider * cell_width
        cv2.line(
            image,
            (x + rng.randint(-3, 3), top),
            (x + rng.randint(-4, 4), top + 3 * cell_height),
            (25, 30, 36),
            5,
        )
    for divider in (1, 2):
        y = top + divider * cell_height
        cv2.line(
            image,
            (left, y + rng.randint(-3, 3)),
            (left + 3 * cell_width, y + rng.randint(-4, 4)),
            (25, 30, 36),
            5,
        )
    # Long unrelated chrome and a short orthogonal mark must not become the
    # selected partition merely because they are also axis aligned.
    cv2.line(image, (45, 80), (45, height - 80), (80, 80, 80), 4)
    cv2.line(image, (80, 95), (160, 95), (80, 80, 80), 4)
    if target_cell is None:
        column = rng.randrange(3)
        row = rng.randrange(3)
    else:
        column, row = target_cell
    target = (
        left + column * cell_width,
        top + row * cell_height,
        left + (column + 1) * cell_width,
        top + (row + 1) * cell_height,
    )
    anchor_x = (target[0] + target[2]) / 2.0 + rng.uniform(-4, 4)
    anchor_y = (target[1] + target[3]) / 2.0 + rng.uniform(-4, 4)
    coarse = (
        anchor_x - cell_width * 0.90,
        anchor_y - cell_height * 0.85,
        anchor_x + cell_width * 0.90,
        anchor_y + cell_height * 0.85,
    )
    return (
        image,
        target,
        _normalized(coarse, width, height),
        [anchor_x / width * 1000.0, anchor_y / height * 1000.0],
    )


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


def test_visible_region_inventory_merges_inner_and_outer_outline_contours():
    from winpilot.tasking.visual_regions import inventory_visible_regions

    image = np.full((520, 900, 3), 248, dtype=np.uint8)
    centers: list[tuple[int, int]] = []
    for row in range(2):
        for column in range(6):
            center = (180 + column * 105, 180 + row * 150)
            centers.append(center)
            cv2.rectangle(
                image,
                (center[0] - 28, center[1] - 28),
                (center[0] + 28, center[1] + 28),
                (40, 130, 170),
                6,
            )

    inventory = inventory_visible_regions(image)
    peers = [item for item in inventory if int(item.get("peer_count") or 0) >= 12]

    assert len(peers) == 12
    assert {(item["row_index"], item["column_index"]) for item in peers} == {
        (row, column) for row in (1, 2) for column in range(1, 7)
    }


def test_visible_region_inventory_recovers_fragmented_four_sided_boundary():
    from winpilot.tasking.visual_regions import inventory_visible_regions

    width, height = 1000, 640
    image = np.full((height, width, 3), 248, dtype=np.uint8)
    target = (610, 155, 855, 430)
    dash, gap = 15, 9
    for x in range(target[0], target[2], dash + gap):
        end = min(target[2], x + dash)
        cv2.line(image, (x, target[1]), (end, target[1]), (55, 80, 95), 4)
        cv2.line(image, (x, target[3]), (end, target[3]), (55, 80, 95), 4)
    for y in range(target[1], target[3], dash + gap):
        end = min(target[3], y + dash)
        cv2.line(image, (target[0], y), (target[0], end), (55, 80, 95), 4)
        cv2.line(image, (target[2], y), (target[2], end), (55, 80, 95), 4)

    # A closed source, a long divider, and partial chrome are competing
    # geometry.  None supplies four fragmented sides around the target.
    cv2.circle(image, (225, 300), 48, (30, 120, 180), -1)
    cv2.line(image, (50, 520), (950, 520), (90, 90, 90), 3)
    cv2.line(image, (50, 520), (50, 555), (90, 90, 90), 3)

    inventory = inventory_visible_regions(image)
    fragmented = [
        item for item in inventory
        if item.get("shape") == "fragmented_boundary"
    ]

    assert fragmented
    selected = min(fragmented, key=lambda item: (
        item["center"][0] - (target[0] + target[2]) / width * 500.0
    ) ** 2 + (
        item["center"][1] - (target[1] + target[3]) / height * 500.0
    ) ** 2)
    left, top, right, bottom = selected["bounds"]
    assert abs(left / 1000.0 * width - target[0]) <= 8
    assert abs(top / 1000.0 * height - target[1]) <= 8
    assert abs(right / 1000.0 * width - target[2]) <= 8
    assert abs(bottom / 1000.0 * height - target[3]) <= 8
    assert float(selected["boundary_support"]) >= 0.50


def test_open_partition_inventory_exposes_typed_cells_without_domain_rules():
    from winpilot.tasking.visual_regions import (
        inventory_visible_partition_regions,
    )

    for seed in range(16):
        image, _target, _coarse, _anchor = _open_partition(seed + 3000)
        inventory = inventory_visible_partition_regions(image)

        assert len(inventory) == 9
        assert {
            (item["row_index"], item["column_index"])
            for item in inventory
        } == {(row, column) for row in range(1, 4) for column in range(1, 4)}
        assert all(
            item["partition_locator"] == {
                "layout": "repeated_partition",
                "row": item["row_index"],
                "column": item["column_index"],
            }
            for item in inventory
        )
        assert all(
            item["provider"] == "current_frame_partition_geometry"
            for item in inventory
        )


def test_closed_repeated_faces_do_not_become_shifted_open_partitions():
    """Four-sided peers keep exact faces while retaining typed relations.

    A closed matrix contains both outer edges and internal dividers.  The open
    partition provider must not extrapolate synthetic cells beyond those outer
    edges, because doing so shifts semantic row/column selection.  The merged
    inventory instead exposes the current-pixel closed faces with the same
    application-neutral locator contract.
    """
    from winpilot.tasking.interaction import _merge_current_region_inventories
    from winpilot.tasking.visual_regions import (
        inventory_visible_partition_regions,
        inventory_visible_regions,
    )

    for seed in range(12):
        image, _target, board = _grid(seed + 4100)
        bounded = inventory_visible_regions(image)
        partitions = inventory_visible_partition_regions(image)

        assert partitions == [], seed
        merged = _merge_current_region_inventories(bounded, partitions)
        cells = [
            item for item in merged
            if item.get("provider") == "current_frame_closed_peer_geometry"
        ]
        expected_count = int(cells[0]["peer_count"]) if cells else 0
        assert expected_count >= 9, seed
        assert len(cells) == expected_count, seed
        rows = max(int(item["row_index"]) for item in cells)
        columns = max(int(item["column_index"]) for item in cells)
        assert len(cells) == rows * columns
        assert {
            tuple(item["partition_locator"][key] for key in ("row", "column"))
            for item in cells
        } == {
            (row, column)
            for row in range(1, rows + 1)
            for column in range(1, columns + 1)
        }
        board_left, board_top, board_right, board_bottom = board
        height, width = image.shape[:2]
        for item in cells:
            center_x = float(item["center"][0]) / 1000.0 * width
            center_y = float(item["center"][1]) / 1000.0 * height
            assert board_left < center_x < board_right
            assert board_top < center_y < board_bottom


def test_closed_child_cannot_seed_nearby_open_partition_inventory():
    """A mark near dividers is not evidence that the mark is their container."""
    from winpilot.tasking.visual_regions import (
        inventory_visible_partition_regions,
    )

    image = np.full((720, 960, 3), 248, dtype=np.uint8)
    left, top, cell = 280, 180, 105
    for offset in range(4):
        cv2.line(
            image,
            (left + offset * cell, top),
            (left + offset * cell, top + 3 * cell),
            (28, 32, 38),
            5,
        )
        cv2.line(
            image,
            (left, top + offset * cell),
            (left + 3 * cell, top + offset * cell),
            (28, 32, 38),
            5,
        )
    cv2.circle(
        image,
        (left + 2 * cell + cell // 2, top + 2 * cell + cell // 2),
        29,
        (28, 32, 38),
        5,
    )

    assert inventory_visible_partition_regions(image) == []


def test_circular_radial_faces_remain_general_closed_region_candidates():
    from winpilot.tasking.visual_regions import (
        inventory_visible_partition_regions,
        inventory_visible_regions,
    )

    image = np.full((600, 800, 3), 245, dtype=np.uint8)
    center = (400, 300)
    radius = 180
    cv2.circle(image, center, radius, (20, 20, 20), 5)
    for angle in range(0, 360, 60):
        radians = np.deg2rad(angle)
        cv2.line(
            image,
            center,
            (
                int(round(center[0] + radius * np.cos(radians))),
                int(round(center[1] + radius * np.sin(radians))),
            ),
            (20, 20, 20),
            5,
        )

    faces = inventory_visible_regions(image, [100, 100, 900, 900])

    assert len(faces) == 6
    assert all(item["shape"] == "closed_shape" for item in faces)
    assert all(item["peer_count"] == 6 for item in faces)
    assert inventory_visible_partition_regions(
        image, [100, 100, 900, 900],
    ) == []


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


def test_open_repeated_partitions_resolve_anchor_without_outer_box():
    from winpilot.tasking.visual_regions import resolve_visible_partition_at_anchor

    for seed in range(40):
        image, target, coarse, anchor = _open_partition(seed)
        resolved = resolve_visible_partition_at_anchor(
            image, coarse, anchor, [0, 0, 1000, 1000],
        )

        assert resolved is not None, seed
        box = resolved["pixel_box"]
        center = ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
        assert target[0] - 8 <= center[0] <= target[2] + 8, (seed, box, target)
        assert target[1] - 8 <= center[1] <= target[3] + 8, (seed, box, target)
        assert resolved["provider"] == "current_frame_partition_geometry"
        assert resolved["intersection_support"] >= 0.75


def test_open_partition_accepts_flat_opencv5_hough_segments(monkeypatch):
    """The resolver accepts both OpenCV 4 and OpenCV 5 line layouts."""
    import winpilot.tasking.visual_regions as visual_regions

    original_hough = visual_regions.cv2.HoughLinesP

    def flat_hough(*args, **kwargs):
        lines = original_hough(*args, **kwargs)
        if lines is None:
            return None
        return np.asarray(lines).reshape(-1, 4)

    monkeypatch.setattr(visual_regions.cv2, "HoughLinesP", flat_hough)
    image, target, coarse, anchor = _open_partition(17)

    resolved = visual_regions.resolve_visible_partition_at_anchor(
        image, coarse, anchor, [0, 0, 1000, 1000],
    )

    assert resolved is not None
    box = resolved["pixel_box"]
    center = ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
    assert target[0] - 8 <= center[0] <= target[2] + 8
    assert target[1] - 8 <= center[1] <= target[3] + 8


def test_open_partition_coalesces_bent_fragments_at_target_cross_section():
    """A curved divider must not become two fake adjacent partitions.

    The upper and lower pieces have different Hough extrapolations, but their
    separation is smaller than any admissible cell.  The resolver must retain
    the upper piece's position near the target while combining both pieces'
    intersection spans.
    """
    from winpilot.tasking.visual_regions import resolve_visible_partition_at_anchor

    width, height = 900, 650
    image = np.full((height, width, 3), 248, dtype=np.uint8)
    left, top = 280, 180
    cell_width, cell_height = 110, 100
    # First divider bends after the target row; the second stays nearly
    # straight.  There is deliberately no outer rectangle.
    cv2.line(image, (390, top), (388, top + 122), (24, 29, 36), 5)
    cv2.line(image, (420, top + 130), (438, top + 3 * cell_height), (24, 29, 36), 5)
    cv2.line(
        image,
        (left + 2 * cell_width, top),
        (left + 2 * cell_width + 8, top + 3 * cell_height),
        (24, 29, 36),
        5,
    )
    for divider in (1, 2):
        y = top + divider * cell_height
        cv2.line(
            image, (left, y), (left + 3 * cell_width, y + 2),
            (24, 29, 36), 5,
        )

    target = (left, top, left + cell_width, top + cell_height)
    anchor = [
        (target[0] + target[2]) / 2.0 / width * 1000.0,
        (target[1] + target[3]) / 2.0 / height * 1000.0,
    ]
    coarse = _normalized((
        target[0] - cell_width * 0.30,
        target[1] - cell_height * 0.25,
        target[2] + cell_width * 0.30,
        target[3] + cell_height * 0.25,
    ), width, height)

    resolved = resolve_visible_partition_at_anchor(
        image, coarse, anchor, [0, 0, 1000, 1000],
    )

    assert resolved is not None
    box = resolved["pixel_box"]
    center = ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
    assert target[0] - 10 <= center[0] <= target[2] + 10
    assert target[1] - 10 <= center[1] <= target[3] + 10
    assert box[2] <= left + cell_width + 12
    assert resolved["coalesced_fragment_count"] >= 1


def test_typed_partition_index_survives_bad_whole_frame_coordinates():
    """Semantic row/column identity is stronger than a VLM's rough box."""
    from winpilot.tasking.visual_regions import resolve_visible_partition_at_anchor

    for column, signed_column in ((0, 1), (2, -1)):
        image, target, _coarse, _anchor = _open_partition(
            1800 + column, target_cell=(column, 0),
        )
        height, width = image.shape[:2]
        cell_width = target[2] - target[0]
        cell_height = target[3] - target[1]
        # The rough box and anchor are entirely above the selected cell and
        # substantially wider than it, matching real whole-frame VLM drift.
        bad_rough_pixels = (
            target[0] - cell_width * 1.15,
            target[1] - cell_height * 1.55,
            target[2] + cell_width * 1.15,
            target[1] - cell_height * 0.05,
        )
        bad_rough = _normalized(bad_rough_pixels, width, height)
        bad_anchor = [
            (bad_rough[0] + bad_rough[2]) / 2.0,
            (bad_rough[1] + bad_rough[3]) / 2.0,
        ]

        resolved = resolve_visible_partition_at_anchor(
            image,
            bad_rough,
            bad_anchor,
            [0, 0, 1000, 1000],
            {
                "layout": "repeated_partition",
                "row": 1,
                "column": signed_column,
            },
        )

        assert resolved is not None, (column, bad_rough, target)
        assert resolved["acceptance_policy"] == "typed_repeated_partition_index"
        box = resolved["pixel_box"]
        center = ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
        assert target[0] - 10 <= center[0] <= target[2] + 10
        assert target[1] - 10 <= center[1] <= target[3] + 10
        assert resolved["x_interval"]["cell_count"] == 3
        assert resolved["y_interval"]["cell_count"] == 3


def test_open_partition_recovers_unique_coarse_overlap_when_anchor_is_offset():
    """Approximate semantic geometry must be corrected by current pixels.

    The anchor is deliberately above the open outer interval while the rough
    box retains only a modest overlap.  No application, game, mark, or fixed
    screen coordinate is involved.
    """
    from winpilot.tasking.visual_regions import resolve_visible_partition_at_anchor

    for seed in range(30):
        image, target, _coarse, _anchor = _open_partition(
            seed + 900, target_cell=(0, 0),
        )
        height, width = image.shape[:2]
        cell_width = target[2] - target[0]
        cell_height = target[3] - target[1]
        shifted_coarse = _normalized((
            target[0] - cell_width * 0.25,
            target[1] - cell_height * 0.90,
            target[2] + cell_width * 0.25,
            target[1] + cell_height * 0.30,
        ), width, height)
        shifted_anchor = [
            (target[0] + target[2]) / 2.0 / width * 1000.0,
            (target[1] - cell_height * 0.30) / height * 1000.0,
        ]

        resolved = resolve_visible_partition_at_anchor(
            image,
            shifted_coarse,
            shifted_anchor,
            [0, 0, 1000, 1000],
        )

        assert resolved is not None, seed
        assert resolved["acceptance_policy"] == (
            "unique_repeated_parallel_partition_near_coarse_anchor"
        )
        box = resolved["pixel_box"]
        center = ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
        assert target[0] - 8 <= center[0] <= target[2] + 8
        assert target[1] - 8 <= center[1] <= target[3] + 8
        assert resolved["y_interval"]["contains_anchor"] is False
        assert resolved["y_interval"]["rough_overlap"] >= 0.22


def test_open_partition_rejects_equal_nearby_overlap_without_unique_geometry():
    from winpilot.tasking.visual_regions import resolve_visible_partition_at_anchor

    width, height = 900, 650
    image = np.full((height, width, 3), 248, dtype=np.uint8)
    left, top = 220, 170
    cell_width, cell_height = 105, 92
    for divider in (1, 2, 3):
        x = left + divider * cell_width
        cv2.line(image, (x, top), (x, top + 3 * cell_height), (25, 30, 36), 5)
    for divider in (1, 2):
        y = top + divider * cell_height
        cv2.line(image, (left, y), (left + 4 * cell_width, y), (25, 30, 36), 5)
    # The x anchor lies exactly on the shared divider and the coarse box gives
    # the two interior intervals identical overlap.  Guessing either would be
    # a hidden coordinate policy, so the resolver must fail closed.
    rough = _normalized((
        left + cell_width,
        top,
        left + 3 * cell_width,
        top + cell_height,
    ), width, height)
    anchor = [
        (left + 2 * cell_width) / width * 1000.0,
        (top + cell_height * 0.5) / height * 1000.0,
    ]

    assert resolve_visible_partition_at_anchor(
        image, rough, anchor, [0, 0, 1000, 1000],
    ) is None


def test_partition_resolver_requires_two_orthogonal_separator_families():
    from winpilot.tasking.visual_regions import resolve_visible_partition_at_anchor

    image = np.full((500, 700, 3), 246, dtype=np.uint8)
    cv2.line(image, (350, 100), (350, 420), (25, 30, 38), 5)
    cv2.line(image, (100, 250), (600, 250), (25, 30, 38), 5)

    assert resolve_visible_partition_at_anchor(
        image,
        [300, 300, 700, 700],
        [500, 500],
        [0, 0, 1000, 1000],
    ) is None


def test_draw_strokes_refines_open_partition_and_contains_complete_paths(tmp_path):
    from winpilot.tasking.interaction import (
        ExpectedInteractionEffect,
        InteractionAction,
        InteractionDecision,
    )
    from winpilot.tasking.visual_geometry import refine_interaction_geometry

    image, target, coarse, anchor = _open_partition(91)
    height, width = image.shape[:2]
    decision = InteractionDecision(
        scene_summary="a repeated partitioned writable surface",
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
            target_region=coarse,
            target_anchor=anchor,
            interaction_mechanism="freehand_strokes",
            affordance_evidence="repeated visible dividers bound writable regions",
            expected_effect_region=coarse,
            effect_region_binding="target_region",
        )],
        expected_effect=ExpectedInteractionEffect(
            description="a visible mark appears inside the selected partition",
            region=coarse,
            region_binding="target_region",
            binding_action_index=0,
        ),
        continuation="reobserve",
        frame_id=4,
        observation_artifact_id="art-current",
    )
    path = tmp_path / "open-partitions.png"
    assert cv2.imwrite(str(path), image)

    refined, proof = refine_interaction_geometry(
        decision, path, expected_size=(width, height),
    )

    action = refined.actions[0]
    resolved = action.target_region
    center = (
        (resolved[0] + resolved[2]) / 2.0 / 1000.0 * width,
        (resolved[1] + resolved[3]) / 2.0 / 1000.0 * height,
    )
    assert target[0] - 8 <= center[0] <= target[2] + 8
    assert target[1] - 8 <= center[1] <= target[3] + 8
    for stroke in action.resolved_frame_strokes():
        assert all(
            resolved[0] <= point[0] <= resolved[2]
            and resolved[1] <= point[1] <= resolved[3]
            for point in stroke
        )
    assert refined.expected_effect.region == resolved
    assert proof["applied"] is True
    assert proof["refinements"][0]["provider"] == (
        "current_frame_partition_geometry"
    )


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
    assert proof["refinements"][0]["provider"] == (
        "current_frame_anchored_subregion"
    )
