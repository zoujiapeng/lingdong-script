"""Current-frame path control for constrained pointer drags.

The semantic planner identifies that a drag follows a visible route and emits
coarse ordered waypoints.  This module turns that semantic path into a local,
source-footprint-safe controller path from the authoritative frame.  It has no
application, object-name, colour-name, game, or screen-coordinate rules.
"""
from __future__ import annotations

import heapq
import math
from typing import Any

import cv2
import numpy as np


class GuidedPathError(RuntimeError):
    """The visible pixels do not prove one safe guided drag route."""


def _pixel_point(
    point: tuple[float, float] | list[float], width: int, height: int,
) -> tuple[int, int]:
    return (
        max(0, min(width - 1, int(round(float(point[0]) / 1000.0 * width)))),
        max(0, min(height - 1, int(round(float(point[1]) / 1000.0 * height)))),
    )


def _pixel_box(
    bounds: list[float], width: int, height: int,
) -> tuple[int, int, int, int]:
    left, top = _pixel_point((bounds[0], bounds[1]), width, height)
    right, bottom = _pixel_point((bounds[2], bounds[3]), width, height)
    return left, top, max(left + 1, right), max(top + 1, bottom)


def _deduplicate(points: list[tuple[int, int]]) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for point in points:
        if not result or math.dist(result[-1], point) >= 3.0:
            result.append(point)
    return result


def _patch_median(lab: np.ndarray, point: tuple[int, int]) -> np.ndarray:
    x, y = point
    patch = lab[
        max(0, y - 4):min(lab.shape[0], y + 5),
        max(0, x - 4):min(lab.shape[1], x + 5),
    ]
    if patch.size == 0:
        raise GuidedPathError("guided path sample falls outside the frame")
    return np.median(patch.reshape(-1, 3), axis=0)


def _route_surface_colour(
    lab: np.ndarray,
    anchors: list[tuple[int, int]],
) -> tuple[np.ndarray, int, int, float]:
    sample_points = list(anchors[1:-1])
    for first, second in zip(anchors, anchors[1:]):
        if math.dist(first, second) < 8.0:
            continue
        for fraction in (0.33, 0.5, 0.67):
            sample_points.append((
                int(round(first[0] + (second[0] - first[0]) * fraction)),
                int(round(first[1] + (second[1] - first[1]) * fraction)),
            ))
    samples = [_patch_median(lab, point) for point in sample_points]
    if len(samples) < 2:
        raise GuidedPathError("guided path has too few independent surface samples")

    cluster_radius = 16.0
    best_cluster: list[np.ndarray] = []
    best_spread = float("inf")
    for seed in samples:
        cluster = [
            candidate for candidate in samples
            if float(np.linalg.norm(candidate - seed)) <= cluster_radius
        ]
        center = np.median(np.stack(cluster), axis=0)
        spread = max(
            float(np.linalg.norm(candidate - center)) for candidate in cluster
        )
        if len(cluster) > len(best_cluster) or (
            len(cluster) == len(best_cluster) and spread < best_spread
        ):
            best_cluster = cluster
            best_spread = spread
    required = max(2, int(math.ceil(len(samples) * 0.34)))
    if len(best_cluster) < required or best_spread > cluster_radius:
        raise GuidedPathError(
            "planner path does not sample one coherent visible route surface"
        )
    return (
        np.median(np.stack(best_cluster), axis=0),
        len(best_cluster),
        len(samples),
        best_spread,
    )


def _nearest_enabled(
    mask: np.ndarray,
    point: tuple[int, int],
    *,
    max_radius: int,
) -> tuple[int, int] | None:
    x, y = point
    best: tuple[float, tuple[int, int]] | None = None
    for radius in range(max_radius + 1):
        left, right = max(0, x - radius), min(mask.shape[1] - 1, x + radius)
        top, bottom = max(0, y - radius), min(mask.shape[0] - 1, y + radius)
        perimeter: list[tuple[int, int]] = []
        if radius == 0:
            perimeter.append((x, y))
        else:
            perimeter.extend((column, top) for column in range(left, right + 1))
            perimeter.extend((column, bottom) for column in range(left, right + 1))
            perimeter.extend((left, row) for row in range(top + 1, bottom))
            perimeter.extend((right, row) for row in range(top + 1, bottom))
        for candidate_x, candidate_y in perimeter:
            if mask[candidate_y, candidate_x] == 0:
                continue
            distance = float((candidate_x - x) ** 2 + (candidate_y - y) ** 2)
            if best is None or distance < best[0]:
                best = (distance, (candidate_x, candidate_y))
        if best is not None:
            return best[1]
    return None


def _line_is_clear(
    mask: np.ndarray,
    first: tuple[int, int],
    second: tuple[int, int],
    *,
    sample_step: float = 1.5,
) -> bool:
    distance = math.dist(first, second)
    steps = max(1, int(math.ceil(distance / sample_step)))
    for index in range(steps + 1):
        fraction = float(index) / float(steps)
        x = int(round(first[0] + (second[0] - first[0]) * fraction))
        y = int(round(first[1] + (second[1] - first[1]) * fraction))
        if (
            x < 0 or y < 0 or y >= mask.shape[0] or x >= mask.shape[1]
            or mask[y, x] == 0
        ):
            return False
    return True


def _astar(
    traversable: np.ndarray,
    clearance: np.ndarray,
    start: tuple[int, int],
    target: tuple[int, int],
    *,
    work_budget: int = 120_000,
) -> list[tuple[int, int]]:
    directions = (
        (-1, -1), (-1, 0), (-1, 1), (0, -1),
        (0, 1), (1, -1), (1, 0), (1, 1),
    )
    maximum_clearance = max(1.0, float(clearance.max()))
    frontier: list[tuple[float, tuple[int, int]]] = [(0.0, start)]
    cost: dict[tuple[int, int], float] = {start: 0.0}
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    visited = 0
    while frontier:
        _, current = heapq.heappop(frontier)
        if current == target:
            break
        visited += 1
        if visited > work_budget:
            raise GuidedPathError("guided path search exceeded its local work budget")
        current_cost = cost[current]
        for delta_x, delta_y in directions:
            next_point = (current[0] + delta_x, current[1] + delta_y)
            x, y = next_point
            if (
                x < 0 or y < 0
                or y >= traversable.shape[0] or x >= traversable.shape[1]
                or traversable[y, x] == 0
            ):
                continue
            step = math.sqrt(2.0) if delta_x and delta_y else 1.0
            center_penalty = 1.6 * max(
                0.0,
                (maximum_clearance - float(clearance[y, x])) / maximum_clearance,
            )
            candidate_cost = current_cost + step * (1.0 + center_penalty)
            if candidate_cost >= cost.get(next_point, float("inf")):
                continue
            cost[next_point] = candidate_cost
            came_from[next_point] = current
            priority = candidate_cost + math.dist(next_point, target)
            heapq.heappush(frontier, (priority, next_point))
    if target != start and target not in came_from:
        raise GuidedPathError("no source-footprint-safe route connects source and target")
    path = [target]
    while path[-1] != start:
        path.append(came_from[path[-1]])
    path.reverse()
    return path


def _simplify_safe_path(
    points: list[tuple[int, int]],
    traversable: np.ndarray,
) -> list[tuple[int, int]]:
    simplified = [points[0]]
    index = 0
    while index < len(points) - 1:
        candidate = len(points) - 1
        while candidate > index and not _line_is_clear(
            traversable, points[index], points[candidate],
        ):
            candidate -= 1
        if candidate == index:
            raise GuidedPathError(
                "downsampled route contains an unsafe full-resolution segment"
            )
        simplified.append(points[candidate])
        index = candidate
    return simplified


def _polyline_length(points: list[tuple[int, int]]) -> float:
    return sum(math.dist(first, second) for first, second in zip(points, points[1:]))


def _polyline_minimum_clearance(
    clearance: np.ndarray,
    points: list[tuple[int, int]],
) -> float:
    minimum = float("inf")
    for first, second in zip(points, points[1:]):
        steps = max(1, int(math.ceil(math.dist(first, second) / 1.5)))
        for index in range(steps + 1):
            fraction = float(index) / float(steps)
            x = int(round(first[0] + (second[0] - first[0]) * fraction))
            y = int(round(first[1] + (second[1] - first[1]) * fraction))
            minimum = min(minimum, float(clearance[y, x]))
    return minimum


def refine_guided_drag_path(
    image: np.ndarray,
    *,
    start: tuple[float, float],
    end: tuple[float, float],
    planner_points: list[list[float]],
    source_region: list[float],
    target_region: list[float],
    interaction_region: list[float],
) -> tuple[list[list[float]], dict[str, Any]]:
    """Return safe intermediate frame waypoints plus an auditable pixel proof."""
    height, width = image.shape[:2]
    start_px = _pixel_point(start, width, height)
    end_px = _pixel_point(end, width, height)
    planner_px = [_pixel_point(point, width, height) for point in planner_points]
    anchors = _deduplicate([start_px, *planner_px, end_px])
    if len(anchors) < 3:
        raise GuidedPathError("guided drag requires a visible non-direct route")

    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)
    route_colour, cluster_count, sample_count, colour_spread = (
        _route_surface_colour(lab, anchors)
    )
    colour_distance = np.linalg.norm(lab - route_colour, axis=2)
    route_mask = np.where(colour_distance <= 16.0, 255, 0).astype(np.uint8)
    route_mask = cv2.morphologyEx(
        route_mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8),
    )
    route_mask = cv2.morphologyEx(
        route_mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8),
    )

    allowed = np.zeros(route_mask.shape, dtype=np.uint8)
    allowed_left, allowed_top, allowed_right, allowed_bottom = _pixel_box(
        interaction_region, width, height,
    )
    allowed[allowed_top:allowed_bottom, allowed_left:allowed_right] = 255
    route_mask = cv2.bitwise_and(route_mask, allowed)

    source_box = _pixel_box(source_region, width, height)
    target_box = _pixel_box(target_region, width, height)
    endpoint_overlay_allowances: list[int] = []
    for endpoint_index, box in enumerate((source_box, target_box)):
        center = ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)
        half_width = max(2, (box[2] - box[0]) // 2)
        half_height = max(2, (box[3] - box[1]) // 2)
        # Source/target paint, text, dashed borders, shadows, and handles are
        # visual overlays on an otherwise traversable route surface.  Fill a
        # tightly bounded allowance around each already grounded endpoint so
        # those pixels do not become fictitious obstacles after footprint
        # erosion.  The destination gets a slightly larger allowance because
        # its visible border commonly lies just outside the usable interior;
        # no pixels elsewhere on the route are changed.
        overlay_ratio = 0.20 if endpoint_index == 1 else 0.12
        overlay_allowance = max(
            3, int(round(min(half_width, half_height) * overlay_ratio)),
        )
        endpoint_overlay_allowances.append(overlay_allowance)
        axes = (
            half_width + overlay_allowance,
            half_height + overlay_allowance,
        )
        cv2.ellipse(route_mask, center, axes, 0, 0, 360, 255, -1)

    component_count, labels = cv2.connectedComponents(route_mask)
    if component_count <= 1:
        raise GuidedPathError("visible route segmentation produced no connected surface")
    component_search = max(
        24,
        int(round(max(
            source_box[2] - source_box[0], source_box[3] - source_box[1],
            target_box[2] - target_box[0], target_box[3] - target_box[1],
        ))),
    )
    source_on_mask = _nearest_enabled(
        route_mask, start_px, max_radius=component_search,
    )
    target_on_mask = _nearest_enabled(
        route_mask, end_px, max_radius=component_search,
    )
    if source_on_mask is None or target_on_mask is None:
        raise GuidedPathError("source or target is not on the visible route surface")
    source_label = int(labels[source_on_mask[1], source_on_mask[0]])
    target_label = int(labels[target_on_mask[1], target_on_mask[0]])
    if source_label <= 0 or source_label != target_label:
        raise GuidedPathError("source and target do not share one visible route component")
    route_mask = np.where(labels == source_label, 255, 0).astype(np.uint8)

    allowed_area = max(1, int(np.count_nonzero(allowed)))
    mask_ratio = float(np.count_nonzero(route_mask)) / float(allowed_area)
    if mask_ratio > 0.65:
        raise GuidedPathError(
            "segmented surface is too broad to prove a constrained pointer route"
        )

    distance = cv2.distanceTransform(route_mask, cv2.DIST_L2, 5)
    source_radius = max(
        2.0,
        min(
            source_box[2] - source_box[0],
            source_box[3] - source_box[1],
        ) / 2.0,
    )
    # Keep a genuine controller margin beyond the object's visible footprint.
    # Anti-aliased route edges, shadows and coarse downsampling can otherwise
    # overestimate clearance by a few pixels and cut an inside corner.
    required_clearance = source_radius + max(8.0, source_radius * 0.30)
    traversable = np.where(distance >= required_clearance, 255, 0).astype(np.uint8)

    scale = max(2, min(5, int(round(min(width, height) / 180.0))))
    small_width = int(math.ceil(width / scale))
    small_height = int(math.ceil(height / scale))
    grid_safe = cv2.erode(
        traversable,
        np.ones((scale + 1, scale + 1), np.uint8),
        iterations=1,
    )
    small = cv2.resize(
        grid_safe, (small_width, small_height), interpolation=cv2.INTER_NEAREST,
    )
    small_clearance = cv2.resize(
        distance, (small_width, small_height), interpolation=cv2.INTER_AREA,
    )
    start_small = _nearest_enabled(
        small,
        (int(round(start_px[0] / scale)), int(round(start_px[1] / scale))),
        max_radius=max(8, int(math.ceil(component_search / scale))),
    )
    target_small = _nearest_enabled(
        small,
        (int(round(end_px[0] / scale)), int(round(end_px[1] / scale))),
        max_radius=max(8, int(math.ceil(component_search / scale))),
    )
    coarse_failure = ""
    grid_path: list[tuple[int, int]] = []
    if start_small is None or target_small is None:
        coarse_failure = "coarse grid has no safe route-center position"
    else:
        try:
            grid_path = _astar(
                small, small_clearance, start_small, target_small,
            )
        except GuidedPathError as exc:
            coarse_failure = str(exc)

    search_scale = scale
    search_fallback = "none"
    if grid_path:
        pixel_path = [
            (
                min(width - 1, point[0] * scale),
                min(height - 1, point[1] * scale),
            )
            for point in grid_path
        ]
    else:
        # A safe full-resolution corridor can be only a few pixels wide after
        # subtracting the source footprint.  Nearest-neighbour downsampling
        # may disconnect that legitimate neck depending on its sub-pixel grid
        # phase.  Retry on the authoritative mask; all later line checks still
        # validate the simplified transaction at full resolution.
        full_start = _nearest_enabled(
            traversable, start_px, max_radius=component_search,
        )
        full_target = _nearest_enabled(
            traversable, end_px, max_radius=component_search,
        )
        if full_start is None or full_target is None:
            raise GuidedPathError(
                "source footprint has no safe route-center position"
            )
        full_budget = max(
            120_000,
            min(600_000, int(np.count_nonzero(traversable)) * 4),
        )
        pixel_path = _astar(
            traversable,
            distance,
            full_start,
            full_target,
            work_budget=full_budget,
        )
        search_scale = 1
        search_fallback = "full_resolution_after_coarse_disconnect"
    if not _line_is_clear(traversable, start_px, pixel_path[0]):
        raise GuidedPathError("source center cannot enter the proven route safely")
    if not _line_is_clear(traversable, pixel_path[-1], end_px):
        raise GuidedPathError("proven route cannot enter the target safely")
    pixel_path = _deduplicate([start_px, *pixel_path, end_px])
    simplified = _simplify_safe_path(pixel_path, traversable)
    if any(
        not _line_is_clear(traversable, first, second)
        for first, second in zip(simplified, simplified[1:])
    ):
        raise GuidedPathError("simplified controller path lost its clearance proof")

    planner_length = _polyline_length(anchors)
    controller_length = _polyline_length(simplified)
    if controller_length > max(planner_length * 1.8, planner_length + 240.0):
        raise GuidedPathError("local route diverges too far from the semantic path")
    sampled_clearance = _polyline_minimum_clearance(distance, simplified)
    normalized = [
        [
            round(point[0] / max(1, width) * 1000.0, 3),
            round(point[1] / max(1, height) * 1000.0, 3),
        ]
        for point in simplified[1:-1]
    ]
    return normalized, {
        "verified": True,
        "policy": "authoritative_route_mask_astar_max_clearance",
        "coordinate_space": "frame_0_1000",
        "planner_waypoint_count": len(planner_points),
        "controller_waypoint_count": len(normalized),
        "search_scale": search_scale,
        "search_fallback": search_fallback,
        "coarse_failure": coarse_failure,
        "surface_cluster_samples": cluster_count,
        "surface_total_samples": sample_count,
        "surface_colour_spread": round(colour_spread, 3),
        "route_mask_ratio": round(mask_ratio, 6),
        "source_radius_px": round(source_radius, 3),
        "required_clearance_px": round(required_clearance, 3),
        "source_overlay_allowance_px": endpoint_overlay_allowances[0],
        "target_overlay_allowance_px": endpoint_overlay_allowances[1],
        "sampled_min_clearance_px": round(sampled_clearance, 3),
        "direct_segment_legal": _line_is_clear(traversable, start_px, end_px),
        "planner_length_px": round(planner_length, 3),
        "controller_length_px": round(controller_length, 3),
    }
