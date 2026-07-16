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

from ..perception import vision_cv
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


def verify_pointer_paths_in_frames(
    before: np.ndarray,
    after: np.ndarray,
    paths: list[list[list[float] | tuple[float, float]]],
    *,
    legal_mask: np.ndarray | None = None,
    pixel_threshold: int = 18,
) -> dict[str, Any]:
    """Prove that changed pixels trace every requested local pixel path.

    A generic ROI diff is not sufficient: a cursor, hover highlight, animation,
    or unrelated repaint can change many pixels without realizing a pointer
    gesture.  This provider requires coverage along every declared path, path
    extent, and concentration of the causal delta near those paths.  Inputs are
    image-local pixels, so the same proof works for a small Controller capture
    and for a complete authoritative frame.
    """
    if (
        before is None
        or after is None
        or not isinstance(before, np.ndarray)
        or not isinstance(after, np.ndarray)
        or before.size == 0
        or after.size == 0
        or before.shape != after.shape
        or before.ndim not in {2, 3}
    ):
        return {
            "verified": False,
            "provider": "local_pointer_path_pixels",
            "reason": "frame_images_unavailable_or_mismatched",
            "stroke_count": len(paths or []),
            "stroke_coverages": [],
            "mutation_evidence": False,
        }

    height, width = after.shape[:2]
    delta = cv2.absdiff(before, after)
    channel_delta = delta.max(axis=2) if delta.ndim == 3 else delta
    changed = (channel_delta >= max(1, int(pixel_threshold))).astype(np.uint8) * 255
    changed = cv2.morphologyEx(
        changed,
        cv2.MORPH_OPEN,
        np.ones((2, 2), dtype=np.uint8),
    )
    if legal_mask is None:
        legal = np.full((height, width), 255, dtype=np.uint8)
    elif legal_mask.shape[:2] != (height, width):
        return {
            "verified": False,
            "provider": "local_pointer_path_pixels",
            "reason": "legal_mask_shape_mismatch",
            "stroke_count": len(paths or []),
            "stroke_coverages": [],
            "mutation_evidence": False,
        }
    else:
        legal = (np.asarray(legal_mask) > 0).astype(np.uint8) * 255
    legal_change = cv2.bitwise_and(changed, legal)

    tolerance = max(3, int(round(min(width, height) * 0.006)))
    tolerance_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (tolerance * 2 + 1, tolerance * 2 + 1),
    )
    near_change = cv2.dilate(legal_change, tolerance_kernel)
    path_mask = np.zeros((height, width), dtype=np.uint8)
    stroke_coverages: list[float] = []
    valid_path_count = 0
    for raw_path in paths or []:
        pixels: list[tuple[int, int]] = []
        for raw_point in raw_path or []:
            if not isinstance(raw_point, (list, tuple)) or len(raw_point) != 2:
                continue
            try:
                point = (int(round(float(raw_point[0]))), int(round(float(raw_point[1]))))
            except (TypeError, ValueError, OverflowError):
                continue
            pixels.append(point)
        if len(pixels) < 2:
            stroke_coverages.append(0.0)
            continue
        valid_path_count += 1
        stroke_mask = np.zeros_like(path_mask)
        cv2.polylines(
            stroke_mask,
            [np.asarray(pixels, dtype=np.int32)],
            False,
            255,
            1,
            lineType=cv2.LINE_8,
        )
        stroke_mask = cv2.bitwise_and(stroke_mask, legal)
        planned_count = int(cv2.countNonZero(stroke_mask))
        covered = cv2.bitwise_and(stroke_mask, near_change)
        stroke_coverages.append(
            float(cv2.countNonZero(covered)) / max(1, planned_count)
        )
        path_mask = cv2.bitwise_or(path_mask, stroke_mask)

    near_paths = cv2.dilate(path_mask, tolerance_kernel)
    attributable_change = cv2.bitwise_and(legal_change, near_paths)
    changed_count = int(cv2.countNonZero(legal_change))
    attributable_count = int(cv2.countNonZero(attributable_change))
    attribution_ratio = attributable_count / max(1, changed_count)

    planned_points = cv2.findNonZero(path_mask)
    changed_points = cv2.findNonZero(attributable_change)
    if planned_points is not None and changed_points is not None:
        planned_x, planned_y, planned_w, planned_h = cv2.boundingRect(planned_points)
        changed_x, changed_y, changed_w, changed_h = cv2.boundingRect(changed_points)
        extent_x = min(1.0, changed_w / max(1.0, float(planned_w)))
        extent_y = min(1.0, changed_h / max(1.0, float(planned_h)))
        extent_ratio = min(extent_x, extent_y)
        planned_bbox = [
            planned_x, planned_y, planned_x + planned_w, planned_y + planned_h,
        ]
        changed_bbox = [
            changed_x, changed_y, changed_x + changed_w, changed_y + changed_h,
        ]
    else:
        extent_ratio = 0.0
        planned_bbox = []
        changed_bbox = []

    minimum_coverage = min(stroke_coverages) if stroke_coverages else 0.0
    maximum_coverage = max(stroke_coverages) if stroke_coverages else 0.0
    proportional_mutation_evidence = bool(
        maximum_coverage >= 0.42
        and extent_ratio >= 0.30
        and attributable_count >= 12
        and attribution_ratio >= 0.20
    )
    proportional_path_realization = bool(
        paths
        and valid_path_count == len(paths)
        and len(stroke_coverages) == len(paths)
        and minimum_coverage >= 0.58
        and extent_ratio >= 0.55
        and attributable_count >= 12
        and attribution_ratio >= 0.20
    )
    # Window chrome, cursor effects, brush previews, and composited overlays can
    # legitimately add a broad transient delta around an otherwise exact
    # gesture.  In that case the global attribution ratio is weaker, but a
    # complete trace of *every* declared path is stronger causal evidence than
    # a generic ROI delta.  Keep this branch deliberately strict: every path
    # must be nearly completely covered over nearly its full extent, and the
    # path corridor must still explain a material share of the changed pixels.
    # A full-area repaint or fill therefore cannot pass merely because it
    # happens to cover the requested coordinates.
    high_coverage_path_shape = bool(
        paths
        and valid_path_count == len(paths)
        and len(stroke_coverages) == len(paths)
        and minimum_coverage >= 0.90
        and extent_ratio >= 0.85
        and attributable_count >= 12
        and attribution_ratio >= 0.12
    )
    mutation_evidence = bool(
        proportional_mutation_evidence or high_coverage_path_shape
    )
    verified = bool(
        proportional_path_realization or high_coverage_path_shape
    )
    if verified and high_coverage_path_shape and not proportional_path_realization:
        acceptance_policy = "all_paths_high_coverage_with_broad_transient"
    elif verified:
        acceptance_policy = "all_paths_proportional_delta"
    else:
        acceptance_policy = "none"
    return {
        "verified": verified,
        "provider": "local_pointer_path_pixels",
        "reason": (
            "all_declared_pointer_paths_realized"
            if verified else "declared_pointer_paths_not_realized"
        ),
        "stroke_count": len(paths or []),
        "valid_path_count": valid_path_count,
        "stroke_coverages": [round(value, 6) for value in stroke_coverages],
        "minimum_stroke_coverage": round(minimum_coverage, 6),
        "maximum_stroke_coverage": round(maximum_coverage, 6),
        "extent_ratio": round(float(extent_ratio), 6),
        "planned_bbox": planned_bbox,
        "changed_bbox": changed_bbox,
        "changed_pixels_in_legal_region": changed_count,
        "changed_pixels_near_paths": attributable_count,
        "change_attribution_ratio": round(float(attribution_ratio), 6),
        "proportional_path_realization": proportional_path_realization,
        "high_coverage_path_shape": high_coverage_path_shape,
        "acceptance_policy": acceptance_policy,
        "visual_change_detected": changed_count >= 12,
        "mutation_evidence": mutation_evidence,
        "tolerance_px": tolerance,
        "pixel_threshold": max(1, int(pixel_threshold)),
    }


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


_COLOUR_ALIASES: dict[str, tuple[str, ...]] = {
    "red": ("red", "\u7ea2", "\u7ea2\u8272"),
    "orange": ("orange", "\u6a59", "\u6a59\u8272"),
    "brown": ("brown", "\u68d5", "\u68d5\u8272", "\u5496\u5561\u8272"),
    "yellow": ("yellow", "\u9ec4", "\u9ec4\u8272"),
    "lime": ("lime", "\u9752\u67e0", "\u9752\u67e0\u8272"),
    "green": ("green", "\u7eff", "\u7eff\u8272"),
    "cyan": ("cyan", "\u9752", "\u9752\u8272"),
    "blue": ("blue", "\u84dd", "\u84dd\u8272"),
    "purple": ("purple", "violet", "\u7d2b", "\u7d2b\u8272"),
    "magenta": ("magenta", "\u6d0b\u7ea2", "\u54c1\u7ea2"),
    "pink": ("pink", "\u7c89", "\u7c89\u8272"),
    "black": ("black", "\u9ed1", "\u9ed1\u8272"),
    "gray": ("gray", "grey", "\u7070", "\u7070\u8272"),
    "white": ("white", "\u767d", "\u767d\u8272"),
}


def _canonical_colour(value: str) -> str:
    text = str(value or "").strip().casefold()
    compact = "".join(character for character in text if not character.isspace())
    for family, aliases in _COLOUR_ALIASES.items():
        if any(
            text == alias.casefold()
            or compact == "".join(alias.casefold().split())
            for alias in aliases
        ):
            return family
    return ""


def _requested_colour_near_paths(
    before: np.ndarray,
    after: np.ndarray,
    paths: list[list[list[int]]],
    *,
    legal_mask: np.ndarray,
    requested_colour: str,
    pixel_threshold: int = 18,
) -> dict[str, Any]:
    """Prove newly changed path pixels have the requested coarse colour."""
    if not str(requested_colour or "").strip():
        return {
            "applicable": False,
            "verified": True,
            "requested_colour": "",
            "reason": "no_colour_obligation",
        }
    family = _canonical_colour(requested_colour)
    masks = vision_cv.coarse_color_masks(after)
    wanted = masks.get(family) if family else None
    if wanted is None:
        return {
            "applicable": True,
            "verified": False,
            "requested_colour": str(requested_colour),
            "canonical_colour": family,
            "reason": "requested_colour_outside_local_vocabulary",
        }
    height, width = after.shape[:2]
    delta = cv2.absdiff(before, after)
    channel_delta = delta.max(axis=2) if delta.ndim == 3 else delta
    changed = (channel_delta >= max(1, int(pixel_threshold))).astype(np.uint8)
    legal = (np.asarray(legal_mask) > 0).astype(np.uint8)
    path_mask = np.zeros((height, width), dtype=np.uint8)
    for path in paths:
        if len(path) < 2:
            continue
        cv2.polylines(
            path_mask,
            [np.asarray(path, dtype=np.int32)],
            False,
            1,
            1,
            lineType=cv2.LINE_8,
        )
    tolerance = max(3, int(round(min(width, height) * 0.006)))
    corridor = cv2.dilate(
        path_mask,
        cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (tolerance * 2 + 1, tolerance * 2 + 1),
        ),
    )
    causal = (corridor > 0) & (legal > 0) & (changed > 0)
    causal_count = int(causal.sum())
    colour_count = int((causal & np.asarray(wanted, dtype=bool)).sum())
    ratio = colour_count / max(1, causal_count)
    verified = bool(colour_count >= 8 and ratio >= 0.08)
    return {
        "applicable": True,
        "verified": verified,
        "requested_colour": str(requested_colour),
        "canonical_colour": family,
        "new_requested_colour_pixels_near_paths": colour_count,
        "changed_pixels_near_paths": causal_count,
        "requested_colour_ratio": round(float(ratio), 6),
        "reason": (
            "new_requested_colour_pixels_follow_declared_paths"
            if verified else "requested_colour_not_proven_on_declared_paths"
        ),
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
    action_proofs: list[dict[str, Any]] = []
    for action_index, action in draw_actions:
        paths = action.resolved_frame_strokes(inset_fraction=0.04)
        region = action.expected_effect_region or action.target_region
        legal_mask = _region_mask(list(region), width, height)
        pixel_paths = [
            [list(_point(point, width, height)) for point in path]
            for path in paths
        ]
        proof = verify_pointer_paths_in_frames(
            before,
            after,
            pixel_paths,
            legal_mask=legal_mask,
        )
        colour_proof = _requested_colour_near_paths(
            before,
            after,
            pixel_paths,
            legal_mask=legal_mask,
            requested_colour=str(action.color or ""),
        )
        action_proofs.append({
            "action_index": action_index,
            **proof,
            "colour_evidence": colour_proof,
            "target_region": list(action.target_region),
            "effect_region": list(region),
        })

    verified = bool(
        action_proofs
        and all(
            item["verified"]
            and (item.get("colour_evidence") or {}).get("verified") is True
            for item in action_proofs
        )
    )
    return {
        "applicable": True,
        "verified": verified,
        "provider": "local_gesture_realization",
        "reason": "declared_paths_realized" if verified else "declared_paths_not_realized",
        "frame_size": {"width": width, "height": height},
        "pixel_threshold": 18,
        "actions": action_proofs,
    }


def verify_typed_freehand_turn_completion(
    decision: InteractionDecision,
    *,
    gesture_evidence: dict[str, Any] | None,
    geometry_evidence: dict[str, Any] | None,
    input_mode_evidence: list[dict[str, Any]] | None,
    persistent_session: bool,
) -> dict[str, Any]:
    """Compose a model-free proof for one persistent freehand actor turn.

    This deliberately proves only the committed local turn, never the overall
    semantic task.  One-shot drawings and ungrounded paths still require the
    visual semantic verifier.  The provider is therefore useful for arbitrary
    turn-taking or cooperative surfaces without encoding a game, symbol, app,
    colour, or coordinate.
    """
    actions = list(decision.actions)
    compatible = bool(
        persistent_session
        and actions
        and decision.continuation == "wait_external"
        and all(
            action.action == "draw_strokes"
            and action.interaction_mechanism == "freehand_strokes"
            for action in actions
        )
    )
    gesture = dict(gesture_evidence or {})
    geometry = dict(geometry_evidence or {})
    modes = [
        dict(item) for item in (input_mode_evidence or [])
        if isinstance(item, dict)
    ]
    mode_verified = any(
        item.get("verified") is True
        and item.get("input_mode") == "continuous_freehand"
        for item in modes
    )
    gesture_actions = {
        int(item.get("action_index")): item
        for item in (gesture.get("actions") or [])
        if isinstance(item, dict) and item.get("action_index") is not None
    }
    geometry_actions = {
        int(item.get("action_index")): item
        for item in (geometry.get("refinements") or [])
        if isinstance(item, dict) and item.get("action_index") is not None
    }
    action_proofs: list[dict[str, Any]] = []
    for action_index, action in enumerate(actions):
        gesture_action = dict(gesture_actions.get(action_index) or {})
        colour = dict(gesture_action.get("colour_evidence") or {})
        geometry_action = dict(geometry_actions.get(action_index) or {})
        current_pixel_geometry = bool(
            geometry.get("verified") is True
            and geometry.get("provider") == "local_visual_geometry"
            and geometry_action
        )
        action_verified = bool(
            gesture_action.get("verified") is True
            and colour.get("verified") is True
            and current_pixel_geometry
        )
        action_proofs.append({
            "action_index": action_index,
            "verified": action_verified,
            "path_realization_verified": gesture_action.get("verified") is True,
            "minimum_stroke_coverage": gesture_action.get(
                "minimum_stroke_coverage"
            ),
            "requested_colour": str(action.color or ""),
            "colour_verified": colour.get("verified") is True,
            "colour_evidence": colour,
            "current_pixel_geometry_verified": current_pixel_geometry,
        })
    verified = bool(
        compatible
        and gesture.get("verified") is True
        and mode_verified
        and len(action_proofs) == len(actions)
        and all(item["verified"] for item in action_proofs)
    )
    return {
        "applicable": compatible,
        "verified": verified,
        "task_complete": False,
        "provider": "typed_freehand_turn_completion",
        "reason": (
            "input_mode_geometry_colour_and_all_paths_proven"
            if verified else "typed_freehand_turn_completion_not_proven"
        ),
        "gesture_provider": str(gesture.get("provider") or ""),
        "geometry_provider": str(geometry.get("provider") or ""),
        "input_mode_verified": mode_verified,
        "actions": action_proofs,
    }
