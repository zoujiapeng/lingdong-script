"""Local causal proofs for pointer-authored visual gestures.

Semantic vision answers whether the requested thing is recognizable.  This
module answers a different, deterministic question: did the current run
actually realize its declared continuous pointer paths at the declared place?
Keeping these proofs separate prevents either a generic pixel delta or a VLM
opinion from standing in for both causality and meaning.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .interaction import InteractionDecision


def _point(value: list[float], width: int, height: int) -> tuple[int, int]:
    return (
        int(round(float(value[0]) / 1000.0 * max(0, width - 1))),
        int(round(float(value[1]) / 1000.0 * max(0, height - 1))),
    )


def _region_mask(
    bounds: list[float], width: int, height: int,
) -> np.ndarray:
    mask = np.zeros((height, width), dtype=np.uint8)
    if len(bounds) != 4:
        return mask
    left, top = _point([bounds[0], bounds[1]], width, height)
    right, bottom = _point([bounds[2], bounds[3]], width, height)
    # Exclude a tiny boundary band.  Static cell/canvas borders are legal
    # envelopes, not evidence that the authored path was realized.
    inset = max(1, int(round(min(right - left + 1, bottom - top + 1) * 0.025)))
    left, top = left + inset, top + inset
    right, bottom = right - inset, bottom - inset
    if right >= left and bottom >= top:
        cv2.rectangle(mask, (left, top), (right, bottom), 255, -1)
    return mask


def _pixel_box(
    bounds: list[float], width: int, height: int,
) -> tuple[int, int, int, int]:
    left, top = _point([bounds[0], bounds[1]], width, height)
    right, bottom = _point([bounds[2], bounds[3]], width, height)
    return (
        max(0, min(width - 1, left)),
        max(0, min(height - 1, top)),
        max(1, min(width, right + 1)),
        max(1, min(height, bottom + 1)),
    )


def _colour_histogram(pixels: np.ndarray) -> np.ndarray:
    """Return a compact, illumination-tolerant histogram for changed pixels."""
    if pixels.size == 0:
        return np.zeros(512, dtype=np.float32)
    values = np.asarray(pixels, dtype=np.uint8).reshape((-1, 1, 3))
    lab = cv2.cvtColor(values, cv2.COLOR_BGR2LAB).reshape((-1, 3))
    bins = np.clip(lab.astype(np.int32) // 32, 0, 7)
    indices = bins[:, 0] * 64 + bins[:, 1] * 8 + bins[:, 2]
    histogram = np.bincount(indices, minlength=512).astype(np.float32)
    total = float(histogram.sum())
    if total:
        histogram /= total
    return histogram


def _placement_geometry_valid(action) -> bool:
    if (
        action.frame_x2 is None
        or action.frame_y2 is None
        or len(action.target_region) != 4
    ):
        return False
    x, y = float(action.frame_x2), float(action.frame_y2)
    left, top, right, bottom = [float(value) for value in action.target_region]
    if not (left <= x <= right and top <= y <= bottom):
        return False
    if action.placement_requirement != "source_inside_target":
        return True
    if len(action.source_region) != 4:
        return False
    source_width = float(action.source_region[2]) - float(action.source_region[0])
    source_height = float(action.source_region[3]) - float(action.source_region[1])
    tolerance = 4.0
    return bool(
        x - source_width / 2.0 >= left - tolerance
        and x + source_width / 2.0 <= right + tolerance
        and y - source_height / 2.0 >= top - tolerance
        and y + source_height / 2.0 <= bottom + tolerance
    )


def verify_direct_manipulation_transition(
    baseline_path: str | Path,
    post_path: str | Path,
    decision: InteractionDecision,
) -> dict[str, Any]:
    """Prove a visual object transferred from its source into its target.

    This is deliberately application- and label-agnostic.  It requires a
    typed direct-manipulation drag, distinct source/target regions, causal
    changes at both ends, matching appearance in the before-source and
    after-target changed pixels, and placement geometry consistent with the
    typed contract.  It cannot be satisfied by a toast, unrelated banner, or
    a source object merely disappearing.
    """
    actions = list(decision.actions)
    eligible = bool(actions) and all(
        action.action == "drag"
        and action.interaction_mechanism == "direct_manipulation"
        and action.frame_x2 is not None
        and action.frame_y2 is not None
        and len(action.source_region) == 4
        and len(action.target_region) == 4
        for action in actions
    )
    if not eligible:
        return {
            "applicable": False,
            "verified": True,
            "provider": "not_applicable",
            "reason": "transaction_is_not_exclusively_typed_direct_manipulation",
            "actions": [],
        }

    before = cv2.imread(str(baseline_path), cv2.IMREAD_COLOR)
    after = cv2.imread(str(post_path), cv2.IMREAD_COLOR)
    if before is None or after is None or before.shape != after.shape:
        return {
            "applicable": True,
            "verified": False,
            "provider": "local_direct_manipulation_transition",
            "reason": "frame_images_unavailable_or_mismatched",
            "actions": [],
        }
    height, width = after.shape[:2]
    channel_delta = cv2.absdiff(before, after).max(axis=2)
    changed = (channel_delta >= 18).astype(np.uint8) * 255
    changed = cv2.morphologyEx(
        changed,
        cv2.MORPH_OPEN,
        np.ones((2, 2), dtype=np.uint8),
    )

    action_proofs: list[dict[str, Any]] = []
    for action_index, action in enumerate(actions):
        source_box = _pixel_box(list(action.source_region), width, height)
        target_box = _pixel_box(list(action.target_region), width, height)
        source_mask = changed[
            source_box[1]:source_box[3], source_box[0]:source_box[2]
        ]
        target_mask = changed[
            target_box[1]:target_box[3], target_box[0]:target_box[2]
        ]
        source_area = max(1, source_mask.size)
        target_area = max(1, target_mask.size)
        source_changed = int(cv2.countNonZero(source_mask))
        target_changed = int(cv2.countNonZero(target_mask))
        source_ratio = float(source_changed) / float(source_area)
        target_ratio = float(target_changed) / float(target_area)

        before_source = before[
            source_box[1]:source_box[3], source_box[0]:source_box[2]
        ]
        after_target = after[
            target_box[1]:target_box[3], target_box[0]:target_box[2]
        ]
        source_pixels = before_source[source_mask != 0]
        target_pixels = after_target[target_mask != 0]
        source_histogram = _colour_histogram(source_pixels)
        target_histogram = _colour_histogram(target_pixels)
        appearance_intersection = float(np.minimum(
            source_histogram, target_histogram,
        ).sum())

        end_x, end_y = _point(
            [float(action.frame_x2), float(action.frame_y2)], width, height,
        )
        source_width = source_box[2] - source_box[0]
        source_height = source_box[3] - source_box[1]
        radius = max(5, int(round(max(source_width, source_height) * 0.55)))
        near_left = max(target_box[0], end_x - radius)
        near_top = max(target_box[1], end_y - radius)
        near_right = min(target_box[2], end_x + radius + 1)
        near_bottom = min(target_box[3], end_y + radius + 1)
        end_neighborhood_changed = int(cv2.countNonZero(
            changed[near_top:near_bottom, near_left:near_right],
        )) if near_right > near_left and near_bottom > near_top else 0

        source_target_separated = bool(
            source_box[2] <= target_box[0]
            or target_box[2] <= source_box[0]
            or source_box[3] <= target_box[1]
            or target_box[3] <= source_box[1]
        )
        placement_valid = _placement_geometry_valid(action)
        verified = bool(
            source_target_separated
            and placement_valid
            and source_changed >= 24
            and target_changed >= 24
            and source_ratio >= 0.06
            and target_ratio >= 0.012
            and appearance_intersection >= 0.24
            and end_neighborhood_changed >= 20
        )
        terminal_verified = bool(
            verified
            and source_ratio >= 0.18
            and target_ratio >= 0.025
            and appearance_intersection >= 0.70
        )
        action_proofs.append({
            "action_index": action_index,
            "verified": verified,
            "terminal_verified": terminal_verified,
            "source_region": list(action.source_region),
            "target_region": list(action.target_region),
            "source_changed_pixels": source_changed,
            "target_changed_pixels": target_changed,
            "source_change_ratio": round(source_ratio, 6),
            "target_change_ratio": round(target_ratio, 6),
            "appearance_histogram_intersection": round(
                appearance_intersection, 6,
            ),
            "end_neighborhood_changed_pixels": end_neighborhood_changed,
            "source_target_separated": source_target_separated,
            "placement_geometry_valid": placement_valid,
        })

    verified = bool(action_proofs and all(item["verified"] for item in action_proofs))
    terminal_verified = bool(
        action_proofs
        and all(item["terminal_verified"] for item in action_proofs)
    )
    return {
        "applicable": True,
        "verified": verified,
        "terminal_verified": terminal_verified,
        "provider": "local_direct_manipulation_transition",
        "reason": (
            "source_appearance_transferred_into_typed_target"
            if verified else "typed_source_to_target_transition_not_proven"
        ),
        "frame_size": {"width": width, "height": height},
        "pixel_threshold": 18,
        "actions": action_proofs,
    }


def verify_typed_direct_manipulation_completion(
    decision: InteractionDecision,
    *,
    transition_evidence: dict[str, Any] | None,
    geometry_evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compose independent local proofs for a typed placement obligation.

    This provider is intentionally limited to an exclusively typed drag
    transaction.  It does not replace semantic judgement for drawing, text,
    arbitrary clicks, or unknown interaction mechanisms.  A completion proof
    requires terminal-grade source-to-target appearance transfer, current-pixel
    source/target grounding, and (for guided actions) an independently proven
    source-footprint-safe path.
    """
    actions = list(decision.actions)
    typed_compatible = bool(actions) and all(
        action.action == "drag"
        and action.interaction_mechanism == "direct_manipulation"
        and action.placement_requirement in {
            "point_inside_target", "source_inside_target",
        }
        and len(action.source_region) == 4
        and len(action.target_region) == 4
        for action in actions
    )
    transition = dict(transition_evidence or {})
    geometry = dict(geometry_evidence or {})
    transition_actions = {
        int(item.get("action_index")): item
        for item in (transition.get("actions") or [])
        if isinstance(item, dict) and item.get("action_index") is not None
    }
    geometry_actions = {
        int(item.get("action_index")): item
        for item in (geometry.get("refinements") or [])
        if isinstance(item, dict) and item.get("action_index") is not None
    }
    action_proofs: list[dict[str, Any]] = []
    for action_index, action in enumerate(actions):
        transition_action = transition_actions.get(action_index, {})
        geometry_action = geometry_actions.get(action_index, {})
        object_grounding_verified = bool(
            (geometry_action.get("source") or {}).get("acceptance_policy")
            and (geometry_action.get("target") or {}).get("acceptance_policy")
        )
        path_proof = dict(geometry_action.get("path_grounding") or {})
        path_verified = bool(
            action.path_mode != "guided" or path_proof.get("verified") is True
        )
        action_verified = bool(
            transition_action.get("terminal_verified") is True
            and transition_action.get("placement_geometry_valid") is True
            and object_grounding_verified
            and path_verified
        )
        action_proofs.append({
            "action_index": action_index,
            "verified": action_verified,
            "transition_terminal_verified": (
                transition_action.get("terminal_verified") is True
            ),
            "placement_geometry_valid": (
                transition_action.get("placement_geometry_valid") is True
            ),
            "object_grounding_verified": object_grounding_verified,
            "path_mode": action.path_mode,
            "path_verified": path_verified,
            "appearance_histogram_intersection": transition_action.get(
                "appearance_histogram_intersection"
            ),
            "source_change_ratio": transition_action.get("source_change_ratio"),
            "target_change_ratio": transition_action.get("target_change_ratio"),
        })
    verified = bool(
        typed_compatible
        and transition.get("terminal_verified") is True
        and geometry.get("verified") is True
        and len(action_proofs) == len(actions)
        and all(item["verified"] for item in action_proofs)
    )
    return {
        "applicable": typed_compatible,
        "verified": verified,
        "provider": "typed_direct_manipulation_completion",
        "reason": (
            "typed_source_transfer_geometry_and_path_proven"
            if verified
            else "typed_direct_manipulation_completion_not_proven"
        ),
        "transition_provider": str(transition.get("provider") or ""),
        "geometry_provider": str(geometry.get("provider") or ""),
        "actions": action_proofs,
    }


def verify_pointer_gesture_realization(
    baseline_path: str | Path,
    post_path: str | Path,
    decision: InteractionDecision,
) -> dict[str, Any]:
    """Prove declared freehand paths were realized in current-frame pixels.

    The check is deliberately modality based, not application based.  It
    applies to any pointer-authored continuous stroke surface and knows
    nothing about Paint, games, symbols, subjects, colours, or coordinates.
    """
    draw_actions = [
        (index, action)
        for index, action in enumerate(decision.actions)
        if action.action == "draw_strokes"
    ]
    if not draw_actions:
        return {
            "applicable": False,
            "verified": True,
            "provider": "not_applicable",
            "reason": "no_pointer_authored_stroke_action",
            "actions": [],
        }

    before = cv2.imread(str(baseline_path), cv2.IMREAD_COLOR)
    after = cv2.imread(str(post_path), cv2.IMREAD_COLOR)
    if before is None or after is None or before.shape != after.shape:
        return {
            "applicable": True,
            "verified": False,
            "provider": "local_gesture_realization",
            "reason": "frame_images_unavailable_or_mismatched",
            "actions": [],
        }
    height, width = after.shape[:2]
    channel_delta = cv2.absdiff(before, after).max(axis=2)
    changed = (channel_delta >= 18).astype(np.uint8) * 255
    changed = cv2.morphologyEx(
        changed,
        cv2.MORPH_OPEN,
        np.ones((2, 2), dtype=np.uint8),
    )
    tolerance = max(3, int(round(min(width, height) * 0.006)))
    near_change = cv2.dilate(
        changed,
        cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (tolerance * 2 + 1, tolerance * 2 + 1),
        ),
    )

    action_proofs: list[dict[str, Any]] = []
    for action_index, action in draw_actions:
        paths = action.resolved_frame_strokes(inset_fraction=0.04)
        region = action.expected_effect_region or action.target_region
        legal_mask = _region_mask(list(region), width, height)
        path_mask = np.zeros((height, width), dtype=np.uint8)
        stroke_coverages: list[float] = []
        planned_points: list[tuple[int, int]] = []
        for path in paths:
            pixels = [_point(point, width, height) for point in path]
            if len(pixels) < 2:
                stroke_coverages.append(0.0)
                continue
            planned_points.extend(pixels)
            stroke_mask = np.zeros_like(path_mask)
            cv2.polylines(
                stroke_mask,
                [np.asarray(pixels, dtype=np.int32)],
                False,
                255,
                1,
                lineType=cv2.LINE_8,
            )
            stroke_mask = cv2.bitwise_and(stroke_mask, legal_mask)
            planned_count = int(cv2.countNonZero(stroke_mask))
            covered = cv2.bitwise_and(stroke_mask, near_change)
            stroke_coverages.append(
                float(cv2.countNonZero(covered)) / max(1, planned_count)
            )
            path_mask = cv2.bitwise_or(path_mask, stroke_mask)

        legal_change = cv2.bitwise_and(changed, legal_mask)
        changed_points = cv2.findNonZero(legal_change)
        planned_array = np.asarray(planned_points, dtype=np.int32)
        if changed_points is not None and planned_array.size:
            changed_x, changed_y, changed_w, changed_h = cv2.boundingRect(changed_points)
            planned_x, planned_y, planned_w, planned_h = cv2.boundingRect(planned_array)
            extent_x = min(1.0, changed_w / max(1.0, float(planned_w)))
            extent_y = min(1.0, changed_h / max(1.0, float(planned_h)))
            extent_ratio = min(extent_x, extent_y)
            changed_bbox: list[int] = [
                changed_x, changed_y,
                changed_x + changed_w, changed_y + changed_h,
            ]
            planned_bbox: list[int] = [
                planned_x, planned_y,
                planned_x + planned_w, planned_y + planned_h,
            ]
        else:
            extent_ratio = 0.0
            changed_bbox = []
            planned_bbox = []
        minimum_coverage = min(stroke_coverages) if stroke_coverages else 0.0
        verified = bool(
            paths
            and len(stroke_coverages) == len(paths)
            and minimum_coverage >= 0.58
            and extent_ratio >= 0.55
            and int(cv2.countNonZero(legal_change)) >= 12
        )
        action_proofs.append({
            "action_index": action_index,
            "verified": verified,
            "stroke_count": len(paths),
            "stroke_coverages": [round(value, 6) for value in stroke_coverages],
            "minimum_stroke_coverage": round(minimum_coverage, 6),
            "extent_ratio": round(extent_ratio, 6),
            "planned_bbox": planned_bbox,
            "changed_bbox": changed_bbox,
            "changed_pixels_in_legal_region": int(cv2.countNonZero(legal_change)),
            "target_region": list(action.target_region),
            "effect_region": list(region),
            "tolerance_px": tolerance,
        })

    verified = bool(action_proofs and all(item["verified"] for item in action_proofs))
    return {
        "applicable": True,
        "verified": verified,
        "provider": "local_gesture_realization",
        "reason": "declared_paths_realized" if verified else "declared_paths_not_realized",
        "frame_size": {"width": width, "height": height},
        "pixel_threshold": 18,
        "actions": action_proofs,
    }
