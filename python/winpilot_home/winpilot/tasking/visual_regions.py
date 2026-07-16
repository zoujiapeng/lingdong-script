"""Application-neutral grounding for visibly bounded interaction regions.

Semantic vision models are good at selecting *which* cell, slot, signature
box, or canvas pane is relevant, but their whole-frame coordinates are too
coarse for physical input.  This module turns that coarse region into a
current-frame boundary proof.  It intentionally knows nothing about games,
applications, labels, colours, or fixed screen positions.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np


def inventory_visible_regions(
    image_or_path: np.ndarray | str | Path,
    allowed_bounds: list[float] | None = None,
    *,
    max_regions: int = 48,
) -> list[dict[str, Any]]:
    """Return current-pixel bounded objects a semantic planner can reference.

    This is deliberately a geometric inventory, not an application or game
    detector.  It exposes closed shapes and strongly bounded rectangles plus
    peer-layout ranks; the semantic model still decides which visible object
    satisfies the task.  Stable ids let the typed plan bind to local pixels
    instead of treating model-estimated coordinates as execution evidence.
    """
    image = (
        image_or_path
        if isinstance(image_or_path, np.ndarray)
        else cv2.imread(str(image_or_path), cv2.IMREAD_COLOR)
    )
    if image is None or image.size == 0:
        return []
    height, width = image.shape[:2]
    allowed = _pixel_box(
        list(allowed_bounds or [0.0, 0.0, 1000.0, 1000.0]),
        width,
        height,
    )
    roi = image[allowed[1]:allowed[3], allowed[0]:allowed[2]]
    if roi.size == 0:
        return []
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    median = float(np.median(blurred))
    low = max(24, min(96, int(round(median * 0.28))))
    high = max(low + 28, min(192, int(round(median * 0.64))))
    edges = cv2.Canny(blurred, low, high)
    edges = cv2.morphologyEx(
        edges, cv2.MORPH_CLOSE, np.ones((3, 3), dtype=np.uint8),
    )
    contours, _ = cv2.findContours(
        edges, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE,
    )
    allowed_width = max(1, allowed[2] - allowed[0])
    allowed_height = max(1, allowed[3] - allowed[1])
    candidates: list[dict[str, Any]] = []
    for contour in contours:
        x, y, candidate_width, candidate_height = cv2.boundingRect(contour)
        if (
            candidate_width < 22
            or candidate_height < 22
            or candidate_width > allowed_width * 0.96
            or candidate_height > allowed_height * 0.94
            or candidate_width * candidate_height
            > allowed_width * allowed_height * 0.35
        ):
            continue
        box = (
            allowed[0] + x,
            allowed[1] + y,
            allowed[0] + x + candidate_width,
            allowed[1] + y + candidate_height,
        )
        contour_area = float(cv2.contourArea(contour))
        fill_ratio = contour_area / max(
            1.0, float(candidate_width * candidate_height),
        )
        perimeter = float(cv2.arcLength(contour, True))
        circularity = (
            4.0 * math.pi * contour_area / max(1.0, perimeter * perimeter)
        )
        local_right = x + candidate_width - 1
        local_bottom = y + candidate_height - 1
        side_coverage = [
            _line_coverage(
                edges, horizontal=True, fixed=y,
                start=x, end=x + candidate_width,
            ),
            _line_coverage(
                edges, horizontal=True, fixed=local_bottom,
                start=x, end=x + candidate_width,
            ),
            _line_coverage(
                edges, horizontal=False, fixed=x,
                start=y, end=y + candidate_height,
            ),
            _line_coverage(
                edges, horizontal=False, fixed=local_right,
                start=y, end=y + candidate_height,
            ),
        ]
        boundary_support = float(sum(side_coverage) / len(side_coverage))
        aspect = float(candidate_width) / max(1.0, float(candidate_height))
        rectangular = bool(
            fill_ratio >= 0.70
            and boundary_support >= 0.60
            and min(side_coverage) >= 0.38
        )
        closed_shape = bool(
            0.45 <= fill_ratio <= 0.94
            and 0.45 <= circularity <= 1.15
            and 0.45 <= aspect <= 2.2
        )
        if not rectangular and not closed_shape:
            continue
        shape = "rectangular_boundary" if rectangular else "closed_shape"
        score = (
            0.50 * boundary_support + 0.35 * min(1.0, fill_ratio) + 0.15
            if rectangular
            else 0.60 * min(1.0, circularity) + 0.40 * fill_ratio
        )
        candidates.append({
            "pixel_box": box,
            "shape": shape,
            "score": float(score),
            "boundary_support": float(boundary_support),
            "fill_ratio": float(fill_ratio),
            "circularity": float(circularity),
        })

    # Edge contours commonly describe both sides of the same border.  Keep a
    # single current-pixel object without collapsing genuinely nested regions.
    candidates.sort(key=lambda item: item["score"], reverse=True)
    unique: list[dict[str, Any]] = []
    for candidate in candidates:
        duplicate = next((
            item for item in unique
            if _iou(item["pixel_box"], candidate["pixel_box"]) >= 0.82
        ), None)
        if duplicate is None:
            unique.append(candidate)
        elif candidate["score"] > duplicate["score"]:
            unique[unique.index(duplicate)] = candidate

    # Prefer task-sized objects over whole-window containers when the inventory
    # must be capped, then assign deterministic ids in visual reading order.
    unique.sort(key=lambda item: (
        (item["pixel_box"][2] - item["pixel_box"][0])
        * (item["pixel_box"][3] - item["pixel_box"][1]),
        item["pixel_box"][1],
        item["pixel_box"][0],
    ))
    unique = unique[:max(1, int(max_regions))]
    unique.sort(key=lambda item: (
        item["pixel_box"][1], item["pixel_box"][0],
        (item["pixel_box"][2] - item["pixel_box"][0])
        * (item["pixel_box"][3] - item["pixel_box"][1]),
    ))
    inventory: list[dict[str, Any]] = []
    for index, candidate in enumerate(unique, start=1):
        box = candidate["pixel_box"]
        bounds = _normalized_box(box, width, height)
        inventory.append({
            "candidate_id": f"visible-region-{index:02d}",
            "bounds": bounds,
            "center": [
                round((bounds[0] + bounds[2]) / 2.0, 3),
                round((bounds[1] + bounds[3]) / 2.0, 3),
            ],
            "shape": candidate["shape"],
            "pixel_size": [
                int(box[2] - box[0]), int(box[3] - box[1]),
            ],
            "boundary_support": round(
                float(candidate["boundary_support"]), 6,
            ),
            "shape_score": round(float(candidate["score"]), 6),
        })

    # Equal-sized peers are useful across grids, toolbars, galleries, forms,
    # and games.  Rank positions without assigning any domain meaning.
    groups: list[list[int]] = []
    for index, item in enumerate(inventory):
        width_px, height_px = item["pixel_size"]
        placed = False
        for group in groups:
            exemplar = inventory[group[0]]["pixel_size"]
            if (
                0.80 <= width_px / max(1, exemplar[0]) <= 1.25
                and 0.80 <= height_px / max(1, exemplar[1]) <= 1.25
            ):
                group.append(index)
                placed = True
                break
        if not placed:
            groups.append([index])
    peer_number = 0
    for group in groups:
        if len(group) < 2:
            continue
        peer_number += 1
        ordered_rows = sorted(group, key=lambda i: (
            inventory[i]["center"][1], inventory[i]["center"][0],
        ))
        ordered_columns = sorted(group, key=lambda i: (
            inventory[i]["center"][0], inventory[i]["center"][1],
        ))
        median_width = float(np.median([
            inventory[index]["pixel_size"][0] for index in group
        ]))
        median_height = float(np.median([
            inventory[index]["pixel_size"][1] for index in group
        ]))

        def axis_clusters(axis: int, tolerance: float) -> dict[int, int]:
            result: dict[int, int] = {}
            clusters: list[list[int]] = []
            for item_index in sorted(
                group, key=lambda value: inventory[value]["center"][axis],
            ):
                coordinate = float(inventory[item_index]["center"][axis])
                if not clusters:
                    clusters.append([item_index])
                    continue
                previous = clusters[-1]
                previous_center = float(np.mean([
                    inventory[value]["center"][axis] for value in previous
                ]))
                if abs(coordinate - previous_center) <= tolerance:
                    previous.append(item_index)
                else:
                    clusters.append([item_index])
            for rank, cluster in enumerate(clusters, start=1):
                for item_index in cluster:
                    result[item_index] = rank
            return result

        row_indices = axis_clusters(1, max(4.0, median_height * 0.40))
        column_indices = axis_clusters(0, max(4.0, median_width * 0.40))
        for index in group:
            inventory[index].update({
                "peer_group": f"peer-set-{peer_number:02d}",
                "peer_count": len(group),
                "row_index": row_indices[index],
                "column_index": column_indices[index],
                "row_major_rank": ordered_rows.index(index) + 1,
                "column_major_rank": ordered_columns.index(index) + 1,
            })
    return inventory


def _pixel_box(
    bounds: list[float], width: int, height: int,
) -> tuple[int, int, int, int]:
    left, top, right, bottom = [float(value) for value in bounds]
    return (
        max(0, min(width - 1, int(math.floor(left / 1000.0 * width)))),
        max(0, min(height - 1, int(math.floor(top / 1000.0 * height)))),
        max(1, min(width, int(math.ceil(right / 1000.0 * width)))),
        max(1, min(height, int(math.ceil(bottom / 1000.0 * height)))),
    )


def _normalized_box(
    box: tuple[int, int, int, int], width: int, height: int,
) -> list[float]:
    return [
        round(box[0] / max(1, width) * 1000.0, 3),
        round(box[1] / max(1, height) * 1000.0, 3),
        round(box[2] / max(1, width) * 1000.0, 3),
        round(box[3] / max(1, height) * 1000.0, 3),
    ]


def _intersection_area(
    first: tuple[int, int, int, int], second: tuple[int, int, int, int],
) -> int:
    return max(0, min(first[2], second[2]) - max(first[0], second[0])) * max(
        0, min(first[3], second[3]) - max(first[1], second[1]),
    )


def _iou(
    first: tuple[int, int, int, int], second: tuple[int, int, int, int],
) -> float:
    intersection = float(_intersection_area(first, second))
    first_area = float(max(0, first[2] - first[0]) * max(0, first[3] - first[1]))
    second_area = float(max(0, second[2] - second[0]) * max(0, second[3] - second[1]))
    return intersection / max(1.0, first_area + second_area - intersection)


def _line_coverage(
    edge_mask: np.ndarray,
    *,
    horizontal: bool,
    fixed: int,
    start: int,
    end: int,
    tolerance: int = 2,
) -> float:
    hits = 0
    samples = max(1, end - start)
    for moving in range(start, end):
        found = False
        for delta in range(-tolerance, tolerance + 1):
            x = moving if horizontal else fixed + delta
            y = fixed + delta if horizontal else moving
            if (
                0 <= x < edge_mask.shape[1]
                and 0 <= y < edge_mask.shape[0]
                and edge_mask[y, x] != 0
            ):
                found = True
                break
        hits += int(found)
    return float(hits) / float(samples)


def resolve_visible_bounded_region(
    image: np.ndarray,
    rough_bounds: list[float],
    allowed_bounds: list[float],
) -> dict[str, Any] | None:
    """Resolve a coarse semantic ROI to one unique current-pixel rectangle.

    Returning ``None`` means the pixels did not prove a unique visible
    boundary.  Callers may then retain a semantic free-canvas region, but they
    must not claim that a cell/slot boundary was locally resolved.
    """
    if image is None or image.size == 0:
        return None
    height, width = image.shape[:2]
    rough = _pixel_box(rough_bounds, width, height)
    allowed = _pixel_box(allowed_bounds, width, height)
    rough_width = max(2, rough[2] - rough[0])
    rough_height = max(2, rough[3] - rough[1])
    rough_diagonal = max(1.0, math.hypot(rough_width, rough_height))
    rough_center = (
        (rough[0] + rough[2]) / 2.0,
        (rough[1] + rough[3]) / 2.0,
    )

    pad_x = max(36, int(round(rough_width * 2.8)))
    pad_y = max(36, int(round(rough_height * 2.8)))
    search = (
        max(allowed[0], rough[0] - pad_x),
        max(allowed[1], rough[1] - pad_y),
        min(allowed[2], rough[2] + pad_x),
        min(allowed[3], rough[3] + pad_y),
    )
    if search[2] - search[0] < 16 or search[3] - search[1] < 16:
        return None
    roi = image[search[1]:search[3], search[0]:search[2]]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    median = float(np.median(blurred))
    low = max(24, min(96, int(round(median * 0.28))))
    high = max(low + 28, min(192, int(round(median * 0.64))))
    edges = cv2.Canny(blurred, low, high)
    edges = cv2.morphologyEx(
        edges, cv2.MORPH_CLOSE, np.ones((3, 3), dtype=np.uint8),
    )
    contours, _ = cv2.findContours(
        edges, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE,
    )

    candidates: list[dict[str, Any]] = []
    for contour in contours:
        x, y, candidate_width, candidate_height = cv2.boundingRect(contour)
        if not (
            rough_width * 0.40 <= candidate_width <= rough_width * 2.25
            and rough_height * 0.40 <= candidate_height <= rough_height * 2.25
            and candidate_width >= 14
            and candidate_height >= 14
        ):
            continue
        contour_area = float(cv2.contourArea(contour))
        rectangularity = contour_area / max(1.0, candidate_width * candidate_height)
        if rectangularity < 0.74:
            continue
        box = (
            search[0] + x,
            search[1] + y,
            search[0] + x + candidate_width,
            search[1] + y + candidate_height,
        )
        if (
            box[0] < allowed[0]
            or box[1] < allowed[1]
            or box[2] > allowed[2]
            or box[3] > allowed[3]
        ):
            continue
        local_right = x + candidate_width - 1
        local_bottom = y + candidate_height - 1
        side_coverage = [
            _line_coverage(
                edges, horizontal=True, fixed=y,
                start=x, end=x + candidate_width,
            ),
            _line_coverage(
                edges, horizontal=True, fixed=local_bottom,
                start=x, end=x + candidate_width,
            ),
            _line_coverage(
                edges, horizontal=False, fixed=x,
                start=y, end=y + candidate_height,
            ),
            _line_coverage(
                edges, horizontal=False, fixed=local_right,
                start=y, end=y + candidate_height,
            ),
        ]
        boundary_support = float(sum(side_coverage) / len(side_coverage))
        if boundary_support < 0.62 or min(side_coverage) < 0.42:
            continue
        center = ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
        distance = math.hypot(
            center[0] - rough_center[0], center[1] - rough_center[1],
        )
        if distance > rough_diagonal * 1.35:
            continue
        center_score = max(0.0, 1.0 - distance / (rough_diagonal * 2.0))
        size_score = math.exp(-(
            abs(math.log(max(1e-6, candidate_width / rough_width)))
            + abs(math.log(max(1e-6, candidate_height / rough_height)))
        ))
        overlap_score = _intersection_area(box, rough) / max(
            1, rough_width * rough_height,
        )
        score = (
            0.30 * center_score
            + 0.24 * size_score
            + 0.18 * min(1.0, overlap_score)
            + 0.18 * boundary_support
            + 0.10 * min(1.0, rectangularity)
        )
        candidates.append({
            "pixel_box": box,
            "score": float(score),
            "distance_px": float(distance),
            "center_score": float(center_score),
            "size_score": float(size_score),
            "overlap_score": float(overlap_score),
            "rectangularity": float(rectangularity),
            "boundary_support": float(boundary_support),
            "side_coverage": [round(value, 6) for value in side_coverage],
        })

    candidates.sort(key=lambda item: item["score"], reverse=True)
    unique: list[dict[str, Any]] = []
    for candidate in candidates:
        duplicate = next((
            item for item in unique
            if _iou(item["pixel_box"], candidate["pixel_box"]) >= 0.88
        ), None)
        if duplicate is None:
            unique.append(candidate)
        elif candidate["score"] > duplicate["score"]:
            unique[unique.index(duplicate)] = candidate
    unique.sort(key=lambda item: item["score"], reverse=True)
    if not unique:
        return None
    top = unique[0]
    second_score = float(unique[1]["score"]) if len(unique) > 1 else 0.0
    margin = float(top["score"]) - second_score
    if float(top["score"]) < 0.67 or margin < 0.055:
        return None
    resolved = _normalized_box(top["pixel_box"], width, height)
    return {
        "provider": "current_frame_bounded_region",
        "acceptance_policy": "unique_rectangular_boundary",
        "rough_region": [float(value) for value in rough_bounds],
        "resolved_region": resolved,
        "pixel_box": list(top["pixel_box"]),
        "score": round(float(top["score"]), 6),
        "margin": round(margin, 6),
        "distance_px": round(float(top["distance_px"]), 3),
        "boundary_support": round(float(top["boundary_support"]), 6),
        "side_coverage": list(top["side_coverage"]),
        "rectangularity": round(float(top["rectangularity"]), 6),
        "candidate_count": len(unique),
    }


def resolve_visible_subregion_at_anchor(
    image: np.ndarray,
    container_bounds: list[float],
    anchor_point: list[float],
    allowed_bounds: list[float],
) -> dict[str, Any] | None:
    """Select the deepest unique closed region containing a semantic anchor.

    This is the hierarchical counterpart to ``resolve_visible_bounded_region``:
    a planner may accurately identify a table/board/panel but estimate its
    selected child poorly.  The semantic anchor says *which* child; current
    pixels prove its exact boundary.  No row/column count or application rule
    is assumed.
    """
    if image is None or image.size == 0 or len(anchor_point) != 2:
        return None
    height, width = image.shape[:2]
    container = _pixel_box(container_bounds, width, height)
    allowed = _pixel_box(allowed_bounds, width, height)
    anchor_x = int(round(float(anchor_point[0]) / 1000.0 * width))
    anchor_y = int(round(float(anchor_point[1]) / 1000.0 * height))
    if not (
        container[0] <= anchor_x <= container[2]
        and container[1] <= anchor_y <= container[3]
    ):
        return None
    # The semantic anchor can carry the same client-vs-frame offset as the
    # coarse container.  Search beyond that box, but require either anchor
    # containment or a unique, overlapping nearest boundary before accepting
    # a correction.
    pad = max(8, int(round(max(
        container[2] - container[0],
        container[3] - container[1],
    ) * 0.90)))
    search = (
        max(allowed[0], container[0] - pad),
        max(allowed[1], container[1] - pad),
        min(allowed[2], container[2] + pad),
        min(allowed[3], container[3] + pad),
    )
    if search[2] - search[0] < 18 or search[3] - search[1] < 18:
        return None
    roi = image[search[1]:search[3], search[0]:search[2]]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    median = float(np.median(blurred))
    low = max(24, min(96, int(round(median * 0.28))))
    high = max(low + 28, min(192, int(round(median * 0.64))))
    edges = cv2.Canny(blurred, low, high)
    edges = cv2.morphologyEx(
        edges, cv2.MORPH_CLOSE, np.ones((3, 3), dtype=np.uint8),
    )
    contours, _ = cv2.findContours(
        edges, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE,
    )
    container_width = max(1, container[2] - container[0])
    container_height = max(1, container[3] - container[1])
    candidates: list[dict[str, Any]] = []
    for contour in contours:
        x, y, candidate_width, candidate_height = cv2.boundingRect(contour)
        if not (
            candidate_width >= max(14, int(container_width * 0.045))
            and candidate_height >= max(14, int(container_height * 0.045))
            and candidate_width <= container_width * 1.06
            and candidate_height <= container_height * 1.06
        ):
            continue
        box = (
            search[0] + x,
            search[1] + y,
            search[0] + x + candidate_width,
            search[1] + y + candidate_height,
        )
        if (
            box[0] < allowed[0]
            or box[1] < allowed[1]
            or box[2] > allowed[2]
            or box[3] > allowed[3]
        ):
            continue
        candidate_area = max(
            1, (box[2] - box[0]) * (box[3] - box[1]),
        )
        container_overlap = _intersection_area(box, container) / candidate_area
        contains_anchor = bool(
            box[0] <= anchor_x <= box[2]
            and box[1] <= anchor_y <= box[3]
        )
        if not contains_anchor and container_overlap < 0.12:
            continue
        contour_area = float(cv2.contourArea(contour))
        rectangularity = contour_area / max(1.0, candidate_width * candidate_height)
        if rectangularity < 0.74:
            continue
        local_right = x + candidate_width - 1
        local_bottom = y + candidate_height - 1
        side_coverage = [
            _line_coverage(
                edges, horizontal=True, fixed=y,
                start=x, end=x + candidate_width,
            ),
            _line_coverage(
                edges, horizontal=True, fixed=local_bottom,
                start=x, end=x + candidate_width,
            ),
            _line_coverage(
                edges, horizontal=False, fixed=x,
                start=y, end=y + candidate_height,
            ),
            _line_coverage(
                edges, horizontal=False, fixed=local_right,
                start=y, end=y + candidate_height,
            ),
        ]
        boundary_support = float(sum(side_coverage) / len(side_coverage))
        if boundary_support < 0.62 or min(side_coverage) < 0.42:
            continue
        center_x = (box[0] + box[2]) / 2.0
        center_y = (box[1] + box[3]) / 2.0
        anchor_distance = math.hypot(
            center_x - anchor_x, center_y - anchor_y,
        )
        if contains_anchor:
            edge_distance = min(
                anchor_x - box[0], box[2] - anchor_x,
                anchor_y - box[1], box[3] - anchor_y,
            )
            anchor_margin = max(
                0.0,
                min(
                    1.0,
                    edge_distance
                    / max(1.0, min(candidate_width, candidate_height) * 0.5),
                ),
            )
            if anchor_margin < 0.08:
                continue
        else:
            anchor_margin = 0.0
        area_ratio = (
            candidate_width * candidate_height
            / max(1.0, container_width * container_height)
        )
        candidates.append({
            "pixel_box": box,
            "area_ratio": float(area_ratio),
            "rectangularity": float(rectangularity),
            "boundary_support": float(boundary_support),
            "side_coverage": [round(value, 6) for value in side_coverage],
            "anchor_margin": float(anchor_margin),
            "anchor_distance_px": float(anchor_distance),
            "contains_anchor": contains_anchor,
            "container_overlap": float(container_overlap),
            "score": float(
                0.45 * boundary_support
                + 0.30 * min(1.0, rectangularity)
                + 0.25 * anchor_margin
            ),
        })

    containing = [item for item in candidates if item["contains_anchor"]]
    nearest_fallback = not containing
    if containing:
        candidates = containing
        candidates.sort(key=lambda item: (item["area_ratio"], -item["score"]))
    else:
        maximum_anchor_distance = max(
            24.0,
            min(container_width, container_height) * 0.85,
        )
        candidates = [
            item for item in candidates
            if item["anchor_distance_px"] <= maximum_anchor_distance
        ]
        candidates.sort(key=lambda item: (
            item["anchor_distance_px"], item["area_ratio"], -item["score"],
        ))
    unique: list[dict[str, Any]] = []
    for candidate in candidates:
        duplicate = next((
            item for item in unique
            if _iou(item["pixel_box"], candidate["pixel_box"]) >= 0.88
        ), None)
        if duplicate is None:
            unique.append(candidate)
        elif candidate["score"] > duplicate["score"]:
            unique[unique.index(duplicate)] = candidate
    if not unique:
        return None
    if nearest_fallback:
        unique.sort(key=lambda item: (
            item["anchor_distance_px"], item["area_ratio"], -item["score"],
        ))
    top = unique[0]
    if nearest_fallback:
        second = unique[1] if len(unique) > 1 else None
        distance_margin = (
            float(second["anchor_distance_px"])
            - float(top["anchor_distance_px"])
            if second is not None else float("inf")
        )
        required_distance_margin = max(
            6.0,
            min(
                top["pixel_box"][2] - top["pixel_box"][0],
                top["pixel_box"][3] - top["pixel_box"][1],
            ) * 0.08,
        )
        margin = distance_margin
        if (
            float(top["score"]) < 0.70
            or distance_margin < required_distance_margin
        ):
            return None
    else:
        same_scale = [
            item for item in unique[1:]
            if item["area_ratio"] <= top["area_ratio"] * 1.35
        ]
        second_score = max(
            (float(item["score"]) for item in same_scale), default=0.0,
        )
        margin = float(top["score"]) - second_score
        if float(top["score"]) < 0.70 or (same_scale and margin < 0.045):
            return None
    return {
        "provider": "current_frame_anchored_subregion",
        "acceptance_policy": (
            "nearest_unique_overlapping_boundary_to_coarse_anchor"
            if nearest_fallback
            else "deepest_unique_rectangular_boundary_at_anchor"
        ),
        "container_region": [float(value) for value in container_bounds],
        "semantic_anchor": [float(value) for value in anchor_point],
        "resolved_region": _normalized_box(top["pixel_box"], width, height),
        "pixel_box": list(top["pixel_box"]),
        "score": round(float(top["score"]), 6),
        "margin": round(margin, 6),
        "area_ratio": round(float(top["area_ratio"]), 6),
        "anchor_margin": round(float(top["anchor_margin"]), 6),
        "anchor_distance_px": round(
            float(top["anchor_distance_px"]), 3,
        ),
        "contains_anchor": bool(top["contains_anchor"]),
        "container_overlap": round(float(top["container_overlap"]), 6),
        "boundary_support": round(float(top["boundary_support"]), 6),
        "side_coverage": list(top["side_coverage"]),
        "rectangularity": round(float(top["rectangularity"]), 6),
        "candidate_count": len(unique),
    }
