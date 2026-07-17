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
    raw_edges = cv2.Canny(blurred, low, high)
    edges = cv2.morphologyEx(
        raw_edges, cv2.MORPH_CLOSE, np.ones((3, 3), dtype=np.uint8),
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

    # Drop zones, selection frames, crop boxes, and many direct-manipulation
    # surfaces deliberately use dashed or otherwise interrupted borders.  A
    # contour of each individual dash cannot prove the containing region, so
    # bridge short collinear gaps in both axes and then require the proposed
    # rectangle to be supported on *all four sides by the original pixels*.
    # The bridge only proposes geometry; it is never accepted as evidence by
    # itself.  This remains application-, colour-, label-, and task-neutral.
    candidates.extend(_fragmented_rectangular_boundaries(
        raw_edges,
        allowed=allowed,
        allowed_width=allowed_width,
        allowed_height=allowed_height,
    ))

    # Edge contours commonly describe both sides of the same thick border.
    # Keep one physical object without collapsing a genuinely smaller child
    # control at the same centre.
    candidates.sort(key=lambda item: item["score"], reverse=True)
    unique: list[dict[str, Any]] = []
    for candidate in candidates:
        duplicate = next((
            item for item in unique
            if (
                _iou(item["pixel_box"], candidate["pixel_box"]) >= 0.82
                or _same_outline_object(
                    item["pixel_box"], candidate["pixel_box"],
                )
            )
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


def _same_outline_object(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> bool:
    """Recognize inner/outer contours of one similarly sized outline."""
    first_width = max(1, first[2] - first[0])
    first_height = max(1, first[3] - first[1])
    second_width = max(1, second[2] - second[0])
    second_height = max(1, second[3] - second[1])
    width_ratio = min(first_width, second_width) / max(first_width, second_width)
    height_ratio = min(first_height, second_height) / max(first_height, second_height)
    if width_ratio < 0.72 or height_ratio < 0.72:
        return False
    intersection = float(_intersection_area(first, second))
    smaller_area = float(min(
        first_width * first_height,
        second_width * second_height,
    ))
    if intersection / max(1.0, smaller_area) < 0.90:
        return False
    first_center = ((first[0] + first[2]) / 2.0, (first[1] + first[3]) / 2.0)
    second_center = ((second[0] + second[2]) / 2.0, (second[1] + second[3]) / 2.0)
    return math.hypot(
        first_center[0] - second_center[0],
        first_center[1] - second_center[1],
    ) <= max(2.0, min(first_width, first_height, second_width, second_height) * 0.20)


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


def _fragmented_rectangular_boundaries(
    edge_mask: np.ndarray,
    *,
    allowed: tuple[int, int, int, int],
    allowed_width: int,
    allowed_height: int,
) -> list[dict[str, Any]]:
    """Propose interrupted rectangular boundaries with raw-edge proof.

    Morphological bridging makes separated collinear segments discoverable,
    while four-sided coverage measured against ``edge_mask`` prevents the
    synthetic bridge from turning text, a single divider, or scattered marks
    into an interaction region.
    """
    shorter_axis = max(1, min(int(allowed_width), int(allowed_height)))
    bridge_gap = max(9, min(31, int(round(shorter_axis * 0.015))))
    if bridge_gap % 2 == 0:
        bridge_gap += 1
    horizontal = cv2.morphologyEx(
        edge_mask,
        cv2.MORPH_CLOSE,
        np.ones((3, bridge_gap), dtype=np.uint8),
    )
    vertical = cv2.morphologyEx(
        edge_mask,
        cv2.MORPH_CLOSE,
        np.ones((bridge_gap, 3), dtype=np.uint8),
    )
    bridged = cv2.bitwise_or(horizontal, vertical)
    bridged = cv2.morphologyEx(
        bridged, cv2.MORPH_CLOSE, np.ones((5, 5), dtype=np.uint8),
    )
    contours, _ = cv2.findContours(
        bridged, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE,
    )
    result: list[dict[str, Any]] = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        if (
            width < 22
            or height < 22
            or width > allowed_width * 0.96
            or height > allowed_height * 0.94
            or width * height > allowed_width * allowed_height * 0.35
        ):
            continue
        aspect = float(width) / max(1.0, float(height))
        if not 0.35 <= aspect <= 3.0:
            continue
        right = x + width - 1
        bottom = y + height - 1
        side_coverage = [
            _line_coverage(
                edge_mask, horizontal=True, fixed=y, start=x, end=x + width,
            ),
            _line_coverage(
                edge_mask, horizontal=True, fixed=bottom,
                start=x, end=x + width,
            ),
            _line_coverage(
                edge_mask, horizontal=False, fixed=x,
                start=y, end=y + height,
            ),
            _line_coverage(
                edge_mask, horizontal=False, fixed=right,
                start=y, end=y + height,
            ),
        ]
        boundary_support = float(sum(side_coverage) / len(side_coverage))
        minimum_side = float(min(side_coverage))
        if boundary_support < 0.50 or minimum_side < 0.42:
            continue
        box = (
            allowed[0] + x,
            allowed[1] + y,
            allowed[0] + x + width,
            allowed[1] + y + height,
        )
        result.append({
            "pixel_box": box,
            "shape": "fragmented_boundary",
            # Keep a fully solid rectangle's native contour preferred during
            # deduplication, while still ranking stronger fragmented outlines
            # above weak proposals.
            "score": float(
                0.50 * boundary_support + 0.35 * minimum_side + 0.05
            ),
            "boundary_support": boundary_support,
            "fill_ratio": 0.0,
            "circularity": 0.0,
        })
    return result


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


def _cluster_partition_lines(
    samples: list[dict[str, float]],
    *,
    tolerance: float,
) -> list[dict[str, Any]]:
    """Merge duplicate Hough segments without merging adjacent partitions."""
    clusters: list[dict[str, Any]] = []
    for sample in sorted(samples, key=lambda item: item["position"]):
        match = next((
            item for item in reversed(clusters)
            if abs(float(item["position"]) - sample["position"]) <= tolerance
        ), None)
        if match is None:
            clusters.append({
                "position": float(sample["position"]),
                "weight": float(sample["length"]),
                "max_length": float(sample["length"]),
                "segments": [[
                    float(sample["span_start"]),
                    float(sample["span_end"]),
                ]],
                "sample_count": 1,
            })
            continue
        weight = float(match["weight"]) + float(sample["length"])
        match["position"] = (
            float(match["position"]) * float(match["weight"])
            + float(sample["position"]) * float(sample["length"])
        ) / max(1.0, weight)
        match["weight"] = weight
        match["max_length"] = max(
            float(match["max_length"]), float(sample["length"]),
        )
        match["segments"].append([
            float(sample["span_start"]), float(sample["span_end"]),
        ])
        match["sample_count"] = int(match["sample_count"]) + 1
    return clusters


def _coalesce_nearby_partition_fragments(
    lines: list[dict[str, Any]],
    *,
    reference: float,
    minimum_spacing: float,
) -> tuple[list[dict[str, Any]], int]:
    """Join nearby Hough fragments that cannot be separate partitions.

    A hand-authored divider is frequently curved or kinked.  Hough then emits
    an upper segment and a lower segment whose extrapolated positions differ
    slightly.  Treating those fragments as two adjacent dividers shifts an
    open outer interval and can put a gesture on the boundary.  Lines closer
    than the minimum admissible partition spacing cannot form a valid interval
    in this resolver, so merge their span evidence while retaining the
    position of the fragment observed nearest the semantic cross-section.

    This is geometry-only: it does not assume a grid size, application, game,
    or screen coordinate.
    """
    ordered = sorted(lines, key=lambda item: float(item["position"]))
    if len(ordered) < 2:
        return ordered, 0

    groups: list[list[dict[str, Any]]] = []
    for line in ordered:
        if (
            not groups
            or float(line["position"])
            - float(groups[-1][-1]["position"])
            >= float(minimum_spacing)
        ):
            groups.append([line])
        else:
            groups[-1].append(line)

    def span_distance(item: dict[str, Any]) -> float:
        distances = []
        for start, end in item.get("segments") or []:
            start_value, end_value = float(start), float(end)
            if start_value <= reference <= end_value:
                return 0.0
            distances.append(min(
                abs(reference - start_value),
                abs(reference - end_value),
            ))
        return min(distances) if distances else math.inf

    coalesced: list[dict[str, Any]] = []
    fragment_count = 0
    for group in groups:
        if len(group) == 1:
            coalesced.append(dict(group[0]))
            continue
        fragment_count += len(group) - 1
        representative = min(group, key=lambda item: (
            span_distance(item),
            -float(item.get("max_length") or 0.0),
            -float(item.get("weight") or 0.0),
        ))
        merged = dict(representative)
        merged["segments"] = [
            list(segment)
            for item in group
            for segment in (item.get("segments") or [])
        ]
        merged["weight"] = sum(
            float(item.get("weight") or 0.0) for item in group
        )
        merged["max_length"] = max(
            float(item.get("max_length") or 0.0) for item in group
        )
        merged["sample_count"] = sum(
            int(item.get("sample_count") or 0) for item in group
        )
        merged["fragment_positions"] = [
            round(float(item["position"]), 3) for item in group
        ]
        merged["reference_distance"] = round(
            float(span_distance(representative)), 3,
        )
        coalesced.append(merged)
    return coalesced, fragment_count


def _partition_axis_hypotheses(
    lines: list[dict[str, Any]],
    *,
    anchor: float,
    rough_start: float,
    rough_end: float,
    allowed_start: float,
    allowed_end: float,
) -> list[dict[str, Any]]:
    """Return current-pixel intervals supported by anchor or coarse overlap.

    Semantic vision is allowed to be approximate.  In particular, a window-
    chrome or canvas offset can put its anchor just outside the intended open
    partition even while the coarse box still overlaps that partition.  Keep
    exact anchor containment as the strongest signal, but admit a nearby
    interval only when the current pixels provide substantial overlap and the
    anchor is no more than one small interval away.  Final two-axis uniqueness
    is checked by :func:`resolve_visible_partition_at_anchor`.
    """
    rough_size = max(1.0, rough_end - rough_start)
    minimum_gap = max(20.0, rough_size * 0.18)
    maximum_gap = max(minimum_gap + 1.0, rough_size * 1.65)
    hypotheses: list[dict[str, Any]] = []
    for index in range(len(lines) - 1):
        first = float(lines[index]["position"])
        second = float(lines[index + 1]["position"])
        gap = second - first
        if not minimum_gap <= gap <= maximum_gap:
            continue
        intervals = [
            (first, second, False),
            (first - gap, first, True),
            (second, second + gap, True),
        ]
        for start, end, extrapolated in intervals:
            if start < allowed_start or end > allowed_end:
                continue
            overlap = max(0.0, min(end, rough_end) - max(start, rough_start))
            overlap_ratio = overlap / max(1.0, gap)
            geometrically_contains_anchor = bool(start < anchor < end)
            edge_distance = min(abs(anchor - start), abs(anchor - end))
            raw_anchor_margin = (
                min(anchor - start, end - anchor) / max(1.0, gap * 0.5)
                if geometrically_contains_anchor else 0.0
            )
            # Treat an anchor within the divider tolerance as belonging to
            # neither side.  Otherwise a half-pixel Hough offset makes one of
            # two genuinely ambiguous neighbours appear uniquely containing.
            contains_anchor = bool(
                geometrically_contains_anchor and raw_anchor_margin >= 0.08
            )
            anchor_distance = 0.0 if contains_anchor else edge_distance
            anchor_distance_ratio = anchor_distance / max(1.0, gap)
            rough_center = (rough_start + rough_end) / 2.0
            interval_center = (start + end) / 2.0
            rough_center_distance_ratio = abs(
                rough_center - interval_center
            ) / max(1.0, gap)
            if contains_anchor:
                anchor_margin = raw_anchor_margin
                if overlap_ratio < 0.12:
                    continue
                anchor_affinity = min(1.0, float(anchor_margin))
            else:
                # Coarse overlap is the independent semantic signal here.  A
                # nearby line family alone is insufficient because repeated
                # dividers deliberately create several plausible intervals.
                if (
                    overlap_ratio < 0.22
                    or anchor_distance_ratio > 1.20
                    or rough_center_distance_ratio > 1.35
                ):
                    continue
                anchor_margin = 0.0
                anchor_affinity = 0.34 * max(
                    0.0, 1.0 - anchor_distance_ratio / 1.20,
                )
            if overlap_ratio <= 0.0:
                continue
            hypotheses.append({
                "start": float(start),
                "end": float(end),
                "gap": float(gap),
                "anchor_margin": min(1.0, float(anchor_margin)),
                "anchor_affinity": min(1.0, float(anchor_affinity)),
                "contains_anchor": contains_anchor,
                "anchor_distance_ratio": round(float(anchor_distance_ratio), 6),
                "rough_center_distance_ratio": round(
                    float(rough_center_distance_ratio), 6,
                ),
                "rough_overlap": min(1.0, float(overlap_ratio)),
                "extrapolated": bool(extrapolated),
                "divider_indices": [index, index + 1],
            })
    unique: list[dict[str, Any]] = []
    for candidate in hypotheses:
        if any(
            abs(float(item["start"]) - float(candidate["start"])) <= 3.0
            and abs(float(item["end"]) - float(candidate["end"])) <= 3.0
            for item in unique
        ):
            continue
        unique.append(candidate)
    return unique


def _partition_intersection_support(
    vertical_lines: list[dict[str, Any]],
    horizontal_lines: list[dict[str, Any]],
    x_hypothesis: dict[str, Any],
    y_hypothesis: dict[str, Any],
    *,
    tolerance: float,
) -> tuple[float, list[dict[str, Any]]]:
    verticals = [
        vertical_lines[index]
        for index in x_hypothesis["divider_indices"]
    ]
    horizontals = [
        horizontal_lines[index]
        for index in y_hypothesis["divider_indices"]
    ]
    checks: list[dict[str, Any]] = []
    for vertical in verticals:
        for horizontal in horizontals:
            vertical_position = float(vertical["position"])
            horizontal_position = float(horizontal["position"])
            vertical_reaches = any(
                float(segment[0]) - tolerance
                <= horizontal_position
                <= float(segment[1]) + tolerance
                for segment in vertical["segments"]
            )
            horizontal_reaches = any(
                float(segment[0]) - tolerance
                <= vertical_position
                <= float(segment[1]) + tolerance
                for segment in horizontal["segments"]
            )
            checks.append({
                "x": round(vertical_position, 3),
                "y": round(horizontal_position, 3),
                "verified": bool(vertical_reaches and horizontal_reaches),
            })
    support = sum(int(item["verified"]) for item in checks) / max(1, len(checks))
    return float(support), checks


def _single_partition_line_cross_support(
    line: dict[str, Any],
    opposite_lines: list[dict[str, Any]],
    *,
    line_is_vertical: bool,
    tolerance: float,
) -> float:
    """Return how often one divider has a reciprocal visible crossing."""
    verified = 0
    line_position = float(line["position"])
    for opposite in opposite_lines:
        opposite_position = float(opposite["position"])
        line_reaches = any(
            float(segment[0]) - tolerance
            <= opposite_position
            <= float(segment[1]) + tolerance
            for segment in line.get("segments") or []
        )
        opposite_reaches = any(
            float(segment[0]) - tolerance
            <= line_position
            <= float(segment[1]) + tolerance
            for segment in opposite.get("segments") or []
        )
        verified += int(line_reaches and opposite_reaches)
    return float(verified) / max(1, len(opposite_lines))


def _extend_partition_family(
    lines: list[dict[str, Any]],
    pair_start: int,
    opposite_core: list[dict[str, Any]],
    *,
    line_is_vertical: bool,
    tolerance: float,
) -> list[dict[str, Any]]:
    """Extend a proven divider pair through consistent reciprocal peers."""
    first = pair_start
    last = pair_start + 1
    base_gap = max(
        1.0,
        float(lines[last]["position"]) - float(lines[first]["position"]),
    )

    def admissible(candidate_index: int, neighbor_index: int) -> bool:
        gap = abs(
            float(lines[candidate_index]["position"])
            - float(lines[neighbor_index]["position"])
        )
        if not 0.68 <= gap / base_gap <= 1.32:
            return False
        return _single_partition_line_cross_support(
            lines[candidate_index],
            opposite_core,
            line_is_vertical=line_is_vertical,
            tolerance=tolerance,
        ) >= 0.50

    while first > 0 and admissible(first - 1, first):
        first -= 1
    while last + 1 < len(lines) and admissible(last + 1, last):
        last += 1
    return [dict(item) for item in lines[first:last + 1]]


def _indexed_partition_interval(
    family: list[dict[str, Any]],
    index: int,
    *,
    allowed_start: float,
    allowed_end: float,
) -> tuple[float, float, dict[str, Any]] | None:
    """Resolve a signed one-based cell index from a divider family."""
    if len(family) < 2 or index == 0:
        return None
    positions = [float(item["position"]) for item in family]
    gaps = [
        positions[position + 1] - positions[position]
        for position in range(len(positions) - 1)
    ]
    if not gaps or min(gaps) <= 0:
        return None
    median_gap = float(np.median(gaps))
    if any(not 0.62 <= gap / median_gap <= 1.45 for gap in gaps):
        return None
    boundaries = [positions[0] - median_gap, *positions, positions[-1] + median_gap]
    cell_count = len(boundaries) - 1
    offset = index - 1 if index > 0 else cell_count + index
    if offset < 0 or offset >= cell_count:
        return None
    start, end = boundaries[offset], boundaries[offset + 1]
    if start < allowed_start or end > allowed_end or end - start < 20:
        return None
    consistency = 1.0 - min(
        1.0,
        float(np.mean([abs(gap - median_gap) / median_gap for gap in gaps])),
    )
    return float(start), float(end), {
        "index": int(index),
        "resolved_offset": int(offset),
        "cell_count": int(cell_count),
        "divider_positions": [round(value, 3) for value in positions],
        "median_gap": round(median_gap, 3),
        "gap_consistency": round(float(consistency), 6),
    }


def _interval_rough_affinity(
    start: float,
    end: float,
    rough_start: float,
    rough_end: float,
) -> float:
    overlap = max(0.0, min(end, rough_end) - max(start, rough_start))
    if overlap > 0:
        return min(1.0, overlap / max(1.0, end - start))
    distance = max(rough_start - end, start - rough_end, 0.0)
    return max(0.0, 1.0 - distance / max(1.0, (end - start) * 1.25))


def _resolve_partition_by_typed_index(
    vertical_lines: list[dict[str, Any]],
    horizontal_lines: list[dict[str, Any]],
    *,
    row: int,
    column: int,
    rough: tuple[int, int, int, int],
    allowed: tuple[int, int, int, int],
    width: int,
    height: int,
    intersection_tolerance: float,
    coalesced_fragment_count: int,
) -> dict[str, Any] | None:
    """Resolve a semantic row/column against unique repeated dividers."""
    candidates: list[dict[str, Any]] = []
    for x_index in range(len(vertical_lines) - 1):
        x_pair = {
            "divider_indices": [x_index, x_index + 1],
        }
        for y_index in range(len(horizontal_lines) - 1):
            y_pair = {
                "divider_indices": [y_index, y_index + 1],
            }
            support, checks = _partition_intersection_support(
                vertical_lines,
                horizontal_lines,
                x_pair,
                y_pair,
                tolerance=intersection_tolerance,
            )
            if support < 0.75:
                continue
            x_core = vertical_lines[x_index:x_index + 2]
            y_core = horizontal_lines[y_index:y_index + 2]
            x_family = _extend_partition_family(
                vertical_lines,
                x_index,
                y_core,
                line_is_vertical=True,
                tolerance=intersection_tolerance,
            )
            y_family = _extend_partition_family(
                horizontal_lines,
                y_index,
                x_core,
                line_is_vertical=False,
                tolerance=intersection_tolerance,
            )
            x_interval = _indexed_partition_interval(
                x_family,
                column,
                allowed_start=float(allowed[0]),
                allowed_end=float(allowed[2]),
            )
            y_interval = _indexed_partition_interval(
                y_family,
                row,
                allowed_start=float(allowed[1]),
                allowed_end=float(allowed[3]),
            )
            if x_interval is None or y_interval is None:
                continue
            left, right, x_evidence = x_interval
            top, bottom, y_evidence = y_interval
            rough_affinity = (
                _interval_rough_affinity(
                    left, right, float(rough[0]), float(rough[2]),
                )
                + _interval_rough_affinity(
                    top, bottom, float(rough[1]), float(rough[3]),
                )
            ) / 2.0
            family_consistency = (
                float(x_evidence["gap_consistency"])
                + float(y_evidence["gap_consistency"])
            ) / 2.0
            score = (
                0.62
                + 0.18 * support
                + 0.10 * rough_affinity
                + 0.10 * family_consistency
            )
            candidates.append({
                "pixel_box": (
                    int(round(left)), int(round(top)),
                    int(round(right)), int(round(bottom)),
                ),
                "score": float(score),
                "intersection_support": float(support),
                "intersection_checks": checks,
                "rough_affinity": float(rough_affinity),
                "x": x_evidence,
                "y": y_evidence,
            })
    if not candidates:
        return None
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
    top = unique[0]
    second_score = float(unique[1]["score"]) if len(unique) > 1 else 0.0
    margin = float(top["score"]) - second_score
    if float(top["score"]) < 0.82 or (len(unique) > 1 and margin < 0.05):
        return None
    return {
        "provider": "current_frame_partition_geometry",
        "acceptance_policy": "typed_repeated_partition_index",
        "locator": {
            "layout": "repeated_partition",
            "row": int(row),
            "column": int(column),
        },
        "resolved_region": _normalized_box(top["pixel_box"], width, height),
        "pixel_box": list(top["pixel_box"]),
        "score": round(float(top["score"]), 6),
        "margin": round(float(margin), 6),
        "intersection_support": round(
            float(top["intersection_support"]), 6,
        ),
        "intersection_checks": list(top["intersection_checks"]),
        "rough_affinity": round(float(top["rough_affinity"]), 6),
        "x_interval": dict(top["x"]),
        "y_interval": dict(top["y"]),
        "vertical_divider_count": len(vertical_lines),
        "horizontal_divider_count": len(horizontal_lines),
        "coalesced_fragment_count": int(coalesced_fragment_count),
        "candidate_count": len(unique),
    }


def resolve_visible_partition_at_anchor(
    image: np.ndarray,
    container_bounds: list[float],
    anchor_point: list[float],
    allowed_bounds: list[float],
    partition_locator: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Resolve an anchor inside regions formed by repeated open dividers.

    Closed-contour detection cannot bind an outer table cell, lane, or panel
    when only internal separators are drawn.  This resolver accepts a region
    only when two independent families of repeated near-orthogonal dividers
    form a unique interval around the semantic anchor.  It contains no board,
    game, application, row-count, or fixed-coordinate knowledge.
    """
    if image is None or image.size == 0 or len(anchor_point) != 2:
        return None
    height, width = image.shape[:2]
    container = _pixel_box(container_bounds, width, height)
    allowed = _pixel_box(allowed_bounds, width, height)
    anchor_x = float(anchor_point[0]) / 1000.0 * width
    anchor_y = float(anchor_point[1]) / 1000.0 * height
    if not (
        container[0] <= anchor_x <= container[2]
        and container[1] <= anchor_y <= container[3]
    ):
        return None
    container_width = max(2, container[2] - container[0])
    container_height = max(2, container[3] - container[1])
    # Two repeated dividers may both sit beyond a coarse box that overlaps only
    # the open outer interval.  Search far enough to observe that independent
    # repetition proof; candidate acceptance remains bounded by current-pixel
    # intersections and uniqueness below.
    pad_x = max(48, int(round(container_width * 1.85)))
    pad_y = max(48, int(round(container_height * 1.85)))
    search = (
        max(allowed[0], container[0] - pad_x),
        max(allowed[1], container[1] - pad_y),
        min(allowed[2], container[2] + pad_x),
        min(allowed[3], container[3] + pad_y),
    )
    if search[2] - search[0] < 40 or search[3] - search[1] < 40:
        return None
    roi = image[search[1]:search[3], search[0]:search[2]]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    median = float(np.median(blurred))
    low = max(24, min(96, int(round(median * 0.28))))
    high = max(low + 28, min(192, int(round(median * 0.64))))
    edges = cv2.Canny(blurred, low, high)
    lines = cv2.HoughLinesP(
        edges,
        1,
        np.pi / 360.0,
        threshold=max(24, int(round(min(roi.shape[:2]) * 0.055))),
        minLineLength=max(36, int(round(min(container_width, container_height) * 0.26))),
        maxLineGap=max(12, int(round(min(container_width, container_height) * 0.14))),
    )
    if lines is None:
        return None
    vertical_samples: list[dict[str, float]] = []
    horizontal_samples: list[dict[str, float]] = []
    minimum_vertical_length = max(48.0, container_height * 0.42)
    minimum_horizontal_length = max(48.0, container_width * 0.42)
    # A coarse box over an open outer interval can end before both internal
    # dividers.  Retain line candidates across the same nearby-evidence radius
    # used by the search ROI; uniqueness, gap consistency, and orthogonal
    # intersections still decide whether those lines are actionable.
    x_candidate_min = container[0] - container_width * 1.75
    x_candidate_max = container[2] + container_width * 1.75
    y_candidate_min = container[1] - container_height * 1.75
    y_candidate_max = container[3] + container_height * 1.75
    # OpenCV 4 commonly returns N x 1 x 4 while OpenCV 5 may return N x 4.
    # Both encode the same four coordinates per segment.  Normalize by total
    # scalar count instead of indexing one version-specific singleton axis.
    line_values = np.asarray(lines)
    if line_values.size == 0 or line_values.size % 4 != 0:
        return None
    for raw_line in line_values.reshape(-1, 4):
        x1, y1, x2, y2 = [int(value) for value in raw_line]
        x1 += search[0]
        x2 += search[0]
        y1 += search[1]
        y2 += search[1]
        delta_x = x2 - x1
        delta_y = y2 - y1
        length = math.hypot(delta_x, delta_y)
        angle = math.degrees(math.atan2(delta_y, delta_x)) % 180.0
        if abs(angle - 90.0) <= 13.0 and abs(delta_y) >= 2:
            position = x1 + (anchor_y - y1) * delta_x / delta_y
            if (
                length >= minimum_vertical_length
                and x_candidate_min <= position <= x_candidate_max
            ):
                vertical_samples.append({
                    "position": float(position),
                    "span_start": float(min(y1, y2)),
                    "span_end": float(max(y1, y2)),
                    "length": float(length),
                })
        elif min(angle, 180.0 - angle) <= 10.0 and abs(delta_x) >= 2:
            position = y1 + (anchor_x - x1) * delta_y / delta_x
            if (
                length >= minimum_horizontal_length
                and y_candidate_min <= position <= y_candidate_max
            ):
                horizontal_samples.append({
                    "position": float(position),
                    "span_start": float(min(x1, x2)),
                    "span_end": float(max(x1, x2)),
                    "length": float(length),
                })
    cluster_tolerance = max(7.0, min(container_width, container_height) * 0.10)
    vertical_lines = _cluster_partition_lines(
        vertical_samples, tolerance=cluster_tolerance,
    )
    horizontal_lines = _cluster_partition_lines(
        horizontal_samples, tolerance=cluster_tolerance,
    )
    vertical_lines, vertical_fragment_count = (
        _coalesce_nearby_partition_fragments(
            vertical_lines,
            reference=anchor_y,
            minimum_spacing=max(20.0, container_width * 0.18),
        )
    )
    horizontal_lines, horizontal_fragment_count = (
        _coalesce_nearby_partition_fragments(
            horizontal_lines,
            reference=anchor_x,
            minimum_spacing=max(20.0, container_height * 0.18),
        )
    )
    if len(vertical_lines) < 2 or len(horizontal_lines) < 2:
        return None
    if isinstance(partition_locator, dict):
        try:
            row = int(partition_locator["row"])
            column = int(partition_locator["column"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        if row == 0 or column == 0:
            return None
        indexed = _resolve_partition_by_typed_index(
            vertical_lines,
            horizontal_lines,
            row=row,
            column=column,
            rough=container,
            allowed=allowed,
            width=width,
            height=height,
            intersection_tolerance=max(
                10.0, min(container_width, container_height) * 0.10,
            ),
            coalesced_fragment_count=(
                vertical_fragment_count + horizontal_fragment_count
            ),
        )
        # A typed relation is part of target identity.  Do not silently fall
        # back to the coarse numeric anchor when its current-pixel proof fails.
        return indexed
    x_hypotheses = _partition_axis_hypotheses(
        vertical_lines,
        anchor=anchor_x,
        rough_start=float(container[0]),
        rough_end=float(container[2]),
        allowed_start=float(allowed[0]),
        allowed_end=float(allowed[2]),
    )
    y_hypotheses = _partition_axis_hypotheses(
        horizontal_lines,
        anchor=anchor_y,
        rough_start=float(container[1]),
        rough_end=float(container[3]),
        allowed_start=float(allowed[1]),
        allowed_end=float(allowed[3]),
    )
    intersection_tolerance = max(
        10.0, min(container_width, container_height) * 0.10,
    )
    candidates: list[dict[str, Any]] = []
    for x_hypothesis in x_hypotheses:
        for y_hypothesis in y_hypotheses:
            intersection_support, checks = _partition_intersection_support(
                vertical_lines,
                horizontal_lines,
                x_hypothesis,
                y_hypothesis,
                tolerance=intersection_tolerance,
            )
            if intersection_support < 0.75:
                continue
            pixel_box = (
                int(round(x_hypothesis["start"])),
                int(round(y_hypothesis["start"])),
                int(round(x_hypothesis["end"])),
                int(round(y_hypothesis["end"])),
            )
            if pixel_box[2] - pixel_box[0] < 20 or pixel_box[3] - pixel_box[1] < 20:
                continue
            score = (
                # Three of four divider crossings is already the strict
                # admission threshold; fragmented antialiased strokes can hide
                # one Hough segment.  Give that threshold a calibrated base,
                # while preserving the full 0.62 contribution at 4/4.
                0.48 + 0.14 * intersection_support
                + 0.12 * (
                    float(x_hypothesis["anchor_affinity"])
                    + float(y_hypothesis["anchor_affinity"])
                ) / 2.0
                + 0.12 * (
                    float(x_hypothesis["rough_overlap"])
                    + float(y_hypothesis["rough_overlap"])
                ) / 2.0
                + 0.08 * (
                    int(bool(x_hypothesis["contains_anchor"]))
                    + int(bool(y_hypothesis["contains_anchor"]))
                ) / 2.0
                + 0.06 * (
                    1.0
                    - 0.25 * int(bool(x_hypothesis["extrapolated"]))
                    - 0.25 * int(bool(y_hypothesis["extrapolated"]))
                )
            )
            candidates.append({
                "pixel_box": pixel_box,
                "score": float(score),
                "intersection_support": float(intersection_support),
                "intersection_checks": checks,
                "x": x_hypothesis,
                "y": y_hypothesis,
            })
    if not candidates:
        return None
    candidates.sort(key=lambda item: item["score"], reverse=True)
    top = candidates[0]
    runner_up = next((
        item for item in candidates[1:]
        if _iou(item["pixel_box"], top["pixel_box"]) < 0.88
    ), None)
    margin = (
        float(top["score"]) - float(runner_up["score"])
        if runner_up is not None else float(top["score"])
    )
    if float(top["score"]) < 0.78 or (runner_up is not None and margin < 0.04):
        return None
    return {
        "provider": "current_frame_partition_geometry",
        "acceptance_policy": (
            "unique_repeated_parallel_partition_at_anchor"
            if bool(top["x"]["contains_anchor"])
            and bool(top["y"]["contains_anchor"])
            else "unique_repeated_parallel_partition_near_coarse_anchor"
        ),
        "container_region": [float(value) for value in container_bounds],
        "semantic_anchor": [float(value) for value in anchor_point],
        "resolved_region": _normalized_box(top["pixel_box"], width, height),
        "pixel_box": list(top["pixel_box"]),
        "score": round(float(top["score"]), 6),
        "margin": round(float(margin), 6),
        "intersection_support": round(
            float(top["intersection_support"]), 6,
        ),
        "intersection_checks": list(top["intersection_checks"]),
        "x_interval": dict(top["x"]),
        "y_interval": dict(top["y"]),
        "vertical_divider_count": len(vertical_lines),
        "horizontal_divider_count": len(horizontal_lines),
        "coalesced_fragment_count": (
            vertical_fragment_count + horizontal_fragment_count
        ),
        "candidate_count": len(candidates),
    }


def inventory_visible_partition_regions(
    image_or_path: np.ndarray | str | Path,
    allowed_bounds: list[float] | None = None,
    *,
    max_cells: int = 36,
) -> list[dict[str, Any]]:
    """Expose pixel-proven cells from repeated open divider networks.

    This is a geometry provider, not a board or table detector.  It first finds
    connected dark divider networks inside the current work surface, then asks
    the same strict repeated-divider resolver used immediately before input to
    prove row/column families.  A single cross, toolbar rule, arbitrary dark
    object, or circular/radial partition does not satisfy that proof.  Closed
    and irregular faces remain the responsibility of
    :func:`inventory_visible_regions`.
    """
    image = (
        image_or_path
        if isinstance(image_or_path, np.ndarray)
        else cv2.imread(str(image_or_path), cv2.IMREAD_COLOR)
    )
    if image is None or image.size == 0:
        return []
    height, width = image.shape[:2]
    allowed_normalized = list(allowed_bounds or [0.0, 0.0, 1000.0, 1000.0])
    allowed = _pixel_box(allowed_normalized, width, height)
    roi = image[allowed[1]:allowed[3], allowed[0]:allowed[2]]
    if roi.size == 0:
        return []
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    # Estimate darkness relative to the actual surface, while retaining a
    # conservative absolute ceiling so shadows and coloured fills do not turn
    # into divider networks.
    background = float(np.percentile(gray, 72.0))
    threshold = int(max(36.0, min(132.0, background - 54.0)))
    dark = np.where(gray <= threshold, 255, 0).astype(np.uint8)
    dark = cv2.morphologyEx(
        dark,
        cv2.MORPH_CLOSE,
        np.ones((3, 3), dtype=np.uint8),
    )
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
        dark,
        connectivity=8,
    )
    containers: list[list[float]] = []
    allowed_width = max(1, allowed[2] - allowed[0])
    allowed_height = max(1, allowed[3] - allowed[1])
    for label in range(1, count):
        x, y, component_width, component_height, area = [
            int(value) for value in stats[label]
        ]
        if (
            component_width < 60
            or component_height < 60
            or area < 180
            or component_width > allowed_width * 0.92
            or component_height > allowed_height * 0.92
        ):
            continue
        # The seed component itself must contain at least two long supports in
        # each axis.  Without this check an unrelated closed mark inside a
        # nearby repeated surface can become the search anchor; the resolver's
        # deliberately generous recovery radius may then find the surrounding
        # dividers and assign the mark's position as row/column one.  Projection
        # support is measured only on pixels belonging to this connected
        # component, so nearby geometry cannot satisfy the seed proof.
        component = (
            labels[y:y + component_height, x:x + component_width] == label
        )

        def long_axis_supports(projection: np.ndarray) -> int:
            indices = np.flatnonzero(projection >= 0.62)
            if not len(indices):
                return 0
            groups = 1
            for previous, current in zip(indices[:-1], indices[1:]):
                if int(current) > int(previous) + 1:
                    groups += 1
            return groups

        if (
            long_axis_supports(component.mean(axis=0)) < 2
            or long_axis_supports(component.mean(axis=1)) < 2
        ):
            continue
        # This provider is intentionally limited to *open* divider networks.
        # A closed repeated surface has an outer boundary on all four sides;
        # treating those outer edges as interior dividers adds one synthetic
        # cell before and after the real layout and shifts every typed index.
        # Measure the original current-frame pixels at the component perimeter
        # and leave four-sided components to ``inventory_visible_regions``.
        # Open crosses still have only sparse hits where their divider lines
        # meet the component box, so this does not depend on an application,
        # board size, colour, or fixed coordinates.
        perimeter_coverage = [
            _line_coverage(
                dark, horizontal=True, fixed=y,
                start=x, end=x + component_width, tolerance=3,
            ),
            _line_coverage(
                dark, horizontal=True, fixed=y + component_height - 1,
                start=x, end=x + component_width, tolerance=3,
            ),
            _line_coverage(
                dark, horizontal=False, fixed=x,
                start=y, end=y + component_height, tolerance=3,
            ),
            _line_coverage(
                dark, horizontal=False, fixed=x + component_width - 1,
                start=y, end=y + component_height, tolerance=3,
            ),
        ]
        if (
            min(perimeter_coverage) >= 0.72
            and float(np.mean(perimeter_coverage)) >= 0.82
        ):
            continue
        containers.append(_normalized_box((
            allowed[0] + x,
            allowed[1] + y,
            allowed[0] + x + component_width,
            allowed[1] + y + component_height,
        ), width, height))

    groups: list[dict[str, Any]] = []
    for container in containers:
        anchor = [
            (float(container[0]) + float(container[2])) / 2.0,
            (float(container[1]) + float(container[3])) / 2.0,
        ]
        probe = resolve_visible_partition_at_anchor(
            image,
            container,
            anchor,
            allowed_normalized,
            {"layout": "repeated_partition", "row": 1, "column": 1},
        )
        if probe is None:
            continue
        rows = int((probe.get("y_interval") or {}).get("cell_count") or 0)
        columns = int((probe.get("x_interval") or {}).get("cell_count") or 0)
        if (
            rows < 2
            or columns < 2
            or rows > 12
            or columns > 12
            or rows * columns > max(1, int(max_cells))
        ):
            continue
        signature = (
            tuple((probe.get("x_interval") or {}).get("divider_positions") or []),
            tuple((probe.get("y_interval") or {}).get("divider_positions") or []),
        )
        if any(group["signature"] == signature for group in groups):
            continue
        groups.append({
            "container": container,
            "anchor": anchor,
            "rows": rows,
            "columns": columns,
            "signature": signature,
        })

    inventory: list[dict[str, Any]] = []
    for group_index, group in enumerate(groups, start=1):
        for row in range(1, int(group["rows"]) + 1):
            for column in range(1, int(group["columns"]) + 1):
                resolved = resolve_visible_partition_at_anchor(
                    image,
                    list(group["container"]),
                    list(group["anchor"]),
                    allowed_normalized,
                    {
                        "layout": "repeated_partition",
                        "row": row,
                        "column": column,
                    },
                )
                if resolved is None:
                    # A family that cannot prove every indexed member is not a
                    # stable candidate inventory.  Remove this group's partial
                    # output and fail closed for the group.
                    inventory = [
                        item for item in inventory
                        if item.get("peer_group") != f"partition-set-{group_index:02d}"
                    ]
                    break
                bounds = [float(value) for value in resolved["resolved_region"]]
                box = [int(value) for value in resolved["pixel_box"]]
                inventory.append({
                    "candidate_id": (
                        f"partition-region-{group_index:02d}-"
                        f"r{row:02d}-c{column:02d}"
                    ),
                    "bounds": bounds,
                    "center": [
                        round((bounds[0] + bounds[2]) / 2.0, 3),
                        round((bounds[1] + bounds[3]) / 2.0, 3),
                    ],
                    "shape": "open_partition_cell",
                    "pixel_size": [box[2] - box[0], box[3] - box[1]],
                    "shape_score": float(resolved.get("score") or 0.0),
                    "peer_group": f"partition-set-{group_index:02d}",
                    "peer_count": int(group["rows"] * group["columns"]),
                    "row_index": row,
                    "column_index": column,
                    "partition_locator": {
                        "layout": "repeated_partition",
                        "row": row,
                        "column": column,
                    },
                    "provider": "current_frame_partition_geometry",
                })
            else:
                continue
            break
    return inventory[:max(1, int(max_cells))]


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
