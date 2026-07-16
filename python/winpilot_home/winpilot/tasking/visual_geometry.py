"""Local pixel grounding for typed spatial interaction plans.

The semantic planner is allowed to describe a coarse source/target region, but
its coordinates are not execution evidence.  This module refines drag geometry
from the exact authoritative frame using application-neutral saliency and edge
evidence.  It deliberately contains no labels, colours, application names, or
screen coordinates.  Ambiguous geometry is rejected instead of choosing the
nearest blob blindly.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .interaction import InteractionDecision


class VisualGeometryError(RuntimeError):
    """The current pixels cannot uniquely ground a planned spatial action."""


_PROVIDER_FAMILY = {
    "local_chroma": "color",
    "local_contrast": "photometric",
    "local_luminance": "photometric",
    "local_edges": "boundary",
}
_MIN_PROVIDER_SCORE = 0.52
_MIN_CONSENSUS_SCORE = 0.62
_MIN_CANDIDATE_MARGIN = 0.08


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
    left, top, right, bottom = box
    return [
        round(left / max(1, width) * 1000.0, 3),
        round(top / max(1, height) * 1000.0, 3),
        round(right / max(1, width) * 1000.0, 3),
        round(bottom / max(1, height) * 1000.0, 3),
    ]


def _intersection_area(
    first: tuple[int, int, int, int], second: tuple[int, int, int, int],
) -> int:
    return max(0, min(first[2], second[2]) - max(first[0], second[0])) * max(
        0, min(first[3], second[3]) - max(first[1], second[1]),
    )


def _iou(first: list[float], second: list[float]) -> float:
    left = max(float(first[0]), float(second[0]))
    top = max(float(first[1]), float(second[1]))
    right = min(float(first[2]), float(second[2]))
    bottom = min(float(first[3]), float(second[3]))
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    first_area = max(0.0, float(first[2]) - float(first[0])) * max(
        0.0, float(first[3]) - float(first[1]),
    )
    second_area = max(0.0, float(second[2]) - float(second[0])) * max(
        0.0, float(second[3]) - float(second[1]),
    )
    return intersection / max(1e-6, first_area + second_area - intersection)


def _odd(value: float, *, minimum: int = 3, maximum: int = 21) -> int:
    rounded = max(minimum, min(maximum, int(round(value))))
    return rounded if rounded % 2 else rounded + 1


def _candidate_masks(roi: np.ndarray, rough_width: int, rough_height: int) -> list[tuple[str, np.ndarray]]:
    """Return complementary colour-independent/local-contrast masks."""
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    chroma = np.where(
        (hsv[:, :, 1] >= 48) & (hsv[:, :, 2] >= 35), 255, 0,
    ).astype(np.uint8)

    lab = cv2.cvtColor(roi, cv2.COLOR_BGR2LAB).astype(np.float32)
    border_width = max(2, min(8, min(roi.shape[:2]) // 18))
    border = np.concatenate((
        lab[:border_width].reshape(-1, 3),
        lab[-border_width:].reshape(-1, 3),
        lab[:, :border_width].reshape(-1, 3),
        lab[:, -border_width:].reshape(-1, 3),
    ))
    background = np.median(border, axis=0)
    distance = np.linalg.norm(lab - background, axis=2)
    border_distance = np.linalg.norm(border - background, axis=1)
    robust_noise = float(np.median(np.abs(border_distance - np.median(border_distance))))
    contrast_threshold = max(18.0, float(np.median(border_distance)) + 4.0 * robust_noise)
    contrast = np.where(distance >= contrast_threshold, 255, 0).astype(np.uint8)

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    gray_border = np.concatenate((
        gray[:border_width].reshape(-1),
        gray[-border_width:].reshape(-1),
        gray[:, :border_width].reshape(-1),
        gray[:, -border_width:].reshape(-1),
    )).astype(np.float32)
    gray_background = float(np.median(gray_border))
    gray_noise = float(np.median(np.abs(gray_border - gray_background)))
    luminance_distance = np.abs(gray.astype(np.float32) - gray_background)
    luminance = np.where(
        luminance_distance >= max(14.0, 4.0 * gray_noise), 255, 0,
    ).astype(np.uint8)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    median = float(np.median(blurred))
    low = max(18, min(80, int(0.30 * median)))
    high = max(low + 28, min(160, int(0.65 * median)))
    canny_edges = cv2.Canny(blurred, low, high)
    threshold_boundary = cv2.morphologyEx(
        cv2.bitwise_or(contrast, luminance),
        cv2.MORPH_GRADIENT,
        np.ones((3, 3), np.uint8),
    )
    edges = cv2.bitwise_or(canny_edges, threshold_boundary)

    close_size = _odd(min(rough_width, rough_height) * 0.11)
    close_kernel = np.ones((close_size, close_size), np.uint8)
    small_kernel = np.ones((3, 3), np.uint8)
    masks: list[tuple[str, np.ndarray]] = []
    for provider, mask in (
        ("local_chroma", chroma),
        ("local_contrast", contrast),
        ("local_luminance", luminance),
        ("local_edges", edges),
    ):
        if provider == "local_edges":
            # Thin boundary evidence is destroyed by an opening operation;
            # close gaps first and then thicken it into a contour candidate.
            cleaned = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_kernel)
            cleaned = cv2.dilate(cleaned, small_kernel, iterations=1)
        else:
            cleaned = cv2.morphologyEx(mask, cv2.MORPH_OPEN, small_kernel)
            cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, close_kernel)
        masks.append((provider, cleaned))
    return masks


def _resolve_region(
    image: np.ndarray,
    rough_bounds: list[float],
    allowed_bounds: list[float],
    *,
    role: str,
) -> dict[str, Any]:
    height, width = image.shape[:2]
    rough = _pixel_box(rough_bounds, width, height)
    allowed = _pixel_box(allowed_bounds, width, height)
    rough_width = max(2, rough[2] - rough[0])
    rough_height = max(2, rough[3] - rough[1])
    pad_x = max(18, int(round(rough_width * 1.6)))
    pad_y = max(18, int(round(rough_height * 1.6)))
    search = (
        max(allowed[0], rough[0] - pad_x),
        max(allowed[1], rough[1] - pad_y),
        min(allowed[2], rough[2] + pad_x),
        min(allowed[3], rough[3] + pad_y),
    )
    if search[2] - search[0] < 12 or search[3] - search[1] < 12:
        raise VisualGeometryError(f"{role} search region has no usable pixels")
    roi = image[search[1]:search[3], search[0]:search[2]]
    rough_center = ((rough[0] + rough[2]) / 2.0, (rough[1] + rough[3]) / 2.0)
    rough_diagonal = max(1.0, math.hypot(rough_width, rough_height))

    candidates: list[dict[str, Any]] = []
    for provider, mask in _candidate_masks(roi, rough_width, rough_height):
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            x, y, candidate_width, candidate_height = cv2.boundingRect(contour)
            if not (
                rough_width * 0.34 <= candidate_width <= rough_width * 2.6
                and rough_height * 0.34 <= candidate_height <= rough_height * 2.6
            ):
                continue
            box = (
                search[0] + int(x), search[1] + int(y),
                search[0] + int(x + candidate_width),
                search[1] + int(y + candidate_height),
            )
            if (
                box[0] < allowed[0] or box[1] < allowed[1]
                or box[2] > allowed[2] or box[3] > allowed[3]
            ):
                continue
            center = ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
            distance = math.hypot(
                center[0] - rough_center[0], center[1] - rough_center[1],
            )
            if distance > rough_diagonal * 1.9:
                continue
            size_score = math.exp(-(
                abs(math.log(max(1e-6, candidate_width / rough_width)))
                + abs(math.log(max(1e-6, candidate_height / rough_height)))
            ))
            distance_score = max(0.0, 1.0 - distance / (rough_diagonal * 1.9))
            overlap_score = _intersection_area(box, rough) / max(1, rough_width * rough_height)
            local_box = mask[y:y + candidate_height, x:x + candidate_width]
            support = float(np.count_nonzero(local_box)) / max(1, local_box.size)
            score = (
                0.46 * size_score
                + 0.34 * distance_score
                + 0.12 * min(1.0, overlap_score)
                + 0.08 * min(1.0, support * 3.0)
            )
            candidates.append({
                "provider": provider,
                "provider_family": _PROVIDER_FAMILY[provider],
                "providers": [provider],
                "provider_scores": {provider: round(float(score), 6)},
                "pixel_box": box,
                "score": float(score),
                "distance_px": float(distance),
                "support_ratio": float(support),
            })

    candidates.sort(key=lambda item: item["score"], reverse=True)
    deduplicated: list[dict[str, Any]] = []
    for candidate in candidates:
        normalized = _normalized_box(candidate["pixel_box"], width, height)
        duplicate = next((
            existing for existing in deduplicated
            if _iou(normalized, existing["normalized_box"]) >= 0.72
        ), None)
        candidate["normalized_box"] = normalized
        if duplicate is None:
            deduplicated.append(candidate)
        else:
            providers = sorted(set(duplicate.get("providers") or []))
            provider_scores = dict(duplicate.get("provider_scores") or {})
            if float(candidate["score"]) >= _MIN_PROVIDER_SCORE:
                providers = sorted(set(providers) | {str(candidate["provider"])})
                provider_scores.update(candidate.get("provider_scores") or {})
            if candidate["score"] > duplicate["score"]:
                replacement = dict(candidate)
                replacement["providers"] = providers
                replacement["provider_scores"] = provider_scores
                deduplicated[deduplicated.index(duplicate)] = replacement
            else:
                duplicate["providers"] = providers
                duplicate["provider_scores"] = provider_scores
    deduplicated.sort(key=lambda item: item["score"], reverse=True)
    top = deduplicated[0] if deduplicated else None
    second = deduplicated[1] if len(deduplicated) > 1 else None
    margin = float(top["score"] - second["score"]) if top and second else float(top["score"] if top else 0.0)
    supporting_providers = [
        provider for provider, score in (top.get("provider_scores") or {}).items()
        if float(score) >= _MIN_PROVIDER_SCORE
    ] if top else []
    provider_families = sorted({
        _PROVIDER_FAMILY.get(str(provider), "unknown")
        for provider in supporting_providers
    })
    provider_count = len(supporting_providers)
    family_count = len(provider_families)
    displacement_ratio = (
        float(top["distance_px"]) / rough_diagonal if top else float("inf")
    )
    required_family_count = 3 if displacement_ratio > 0.75 else 2
    if (
        top is None
        or float(top["score"]) < _MIN_CONSENSUS_SCORE
        or margin < _MIN_CANDIDATE_MARGIN
        or family_count < required_family_count
    ):
        raise VisualGeometryError(
            f"{role} visual grounding is ambiguous "
            f"(best={float(top['score']) if top else 0.0:.3f}, "
            f"margin={margin:.3f}, providers={provider_count}, "
            f"families={family_count}/{required_family_count})"
        )
    return {
        "role": role,
        "provider": "local_geometry_consensus",
        "providers": supporting_providers,
        "provider_families": provider_families,
        "provider_scores": {
            provider: score
            for provider, score in (top.get("provider_scores") or {}).items()
            if float(score) >= _MIN_PROVIDER_SCORE
        },
        "required_family_count": required_family_count,
        "displacement_ratio": round(displacement_ratio, 6),
        "semantic_anchor": "planner_coarse_region",
        "rough_region": [float(value) for value in rough_bounds],
        "resolved_region": top["normalized_box"],
        "pixel_box": list(top["pixel_box"]),
        "score": round(float(top["score"]), 6),
        "margin": round(margin, 6),
        "distance_px": round(float(top["distance_px"]), 3),
        "candidate_count": len(deduplicated),
    }


def _project_point(
    point: tuple[float, float], old: list[float], new: list[float],
) -> tuple[float, float]:
    old_width = max(1e-6, float(old[2]) - float(old[0]))
    old_height = max(1e-6, float(old[3]) - float(old[1]))
    fraction_x = min(1.0, max(0.0, (float(point[0]) - float(old[0])) / old_width))
    fraction_y = min(1.0, max(0.0, (float(point[1]) - float(old[1])) / old_height))
    return (
        float(new[0]) + fraction_x * (float(new[2]) - float(new[0])),
        float(new[1]) + fraction_y * (float(new[3]) - float(new[1])),
    )


def refine_interaction_geometry(
    decision: InteractionDecision,
    image_path: str | Path,
    *,
    expected_size: tuple[int, int] | None = None,
    frame_region: dict[str, int] | None = None,
) -> tuple[InteractionDecision, dict[str, Any]]:
    """Prove and, when necessary, correct typed object geometry locally.

    Object-anchored drags and point actions must produce unique current-pixel
    evidence before the Controller receives physical coordinates.  Free paths,
    relative gestures, and explicitly bounded coordinates remain unchanged but
    return an explicit policy proof; frame lineage and ROI containment are
    enforced by the StageRuntime around this function.
    """
    all_drag_indices = [
        index for index, action in enumerate(decision.actions)
        if action.action == "drag"
    ]
    drag_indices = [
        index for index, action in enumerate(decision.actions)
        if action.action == "drag" and action.source_region and action.target_region
    ]
    if all_drag_indices and len(drag_indices) != len(all_drag_indices):
        raise VisualGeometryError(
            "typed drag requires explicit source_region and target_region for "
            "current-pixel grounding"
        )
    point_action_names = {
        "click", "double_click", "hover", "move_mouse", "mouse_down",
        "mouse_up",
    }
    anchored_point_indices = [
        index for index, action in enumerate(decision.actions)
        if action.action in point_action_names
        and action.grounding_mode in {"object_anchor", "semantic_control"}
    ]
    if not all_drag_indices and not anchored_point_indices:
        return decision, {
            "verified": True,
            "applied": False,
            "provider": "typed_spatial_policy",
            "reason": "frame_lineage_and_typed_roi_bounds",
            "action_grounding": [
                {
                    "action_index": index,
                    "action": action.action,
                    "grounding_mode": action.grounding_mode,
                }
                for index, action in enumerate(decision.actions)
            ],
        }
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise VisualGeometryError("authoritative frame could not be decoded for geometry grounding")
    image_height, image_width = image.shape[:2]
    if frame_region is not None:
        expected_size = (
            int(frame_region.get("width") or 0),
            int(frame_region.get("height") or 0),
        )
    if expected_size is not None:
        expected_width, expected_height = [int(value) for value in expected_size]
        if (image_width, image_height) != (expected_width, expected_height):
            raise VisualGeometryError(
                "authoritative artifact dimensions do not match the bound frame "
                f"({image_width}x{image_height} != {expected_width}x{expected_height})"
            )

    payload = decision.model_dump(mode="json")
    refinements: list[dict[str, Any]] = []
    for index in drag_indices:
        action = decision.actions[index]
        source = _resolve_region(
            image, list(action.source_region), list(decision.interaction_region), role="source",
        )
        target = _resolve_region(
            image, list(action.target_region), list(decision.interaction_region), role="target",
        )
        source_region = list(source["resolved_region"])
        target_region = list(target["resolved_region"])
        start = _project_point(
            (float(action.frame_x1), float(action.frame_y1)),
            list(action.source_region), source_region,
        )
        if action.placement_requirement == "source_inside_target":
            source_center = (
                (source_region[0] + source_region[2]) / 2.0,
                (source_region[1] + source_region[3]) / 2.0,
            )
            target_center = (
                (target_region[0] + target_region[2]) / 2.0,
                (target_region[1] + target_region[3]) / 2.0,
            )
            end = (
                target_center[0] + (start[0] - source_center[0]),
                target_center[1] + (start[1] - source_center[1]),
            )
        else:
            end = _project_point(
                (float(action.frame_x2), float(action.frame_y2)),
                list(action.target_region), target_region,
            )
        start_delta = (start[0] - float(action.frame_x1), start[1] - float(action.frame_y1))
        end_delta = (end[0] - float(action.frame_x2), end[1] - float(action.frame_y2))
        points: list[list[float]] = []
        count = len(action.frame_points)
        for point_index, point in enumerate(action.frame_points):
            fraction = (point_index + 1) / (count + 1)
            points.append([
                float(point[0]) + start_delta[0] * (1.0 - fraction) + end_delta[0] * fraction,
                float(point[1]) + start_delta[1] * (1.0 - fraction) + end_delta[1] * fraction,
            ])
        action_payload = dict(payload["actions"][index])
        action_payload.update({
            "frame_x1": start[0], "frame_y1": start[1],
            "frame_x2": end[0], "frame_y2": end[1],
            "frame_points": points,
            "source_region": source_region,
            "target_region": target_region,
        })
        effect_binding = action.effect_region_binding
        if action.expected_effect_region:
            if effect_binding == "target_region":
                action_payload["expected_effect_region"] = target_region
            elif effect_binding == "source_region":
                action_payload["expected_effect_region"] = source_region
            elif effect_binding == "surface_region":
                action_payload["expected_effect_region"] = list(
                    decision.surface_region,
                )
        payload["actions"][index] = action_payload
        global_effect = payload.get("expected_effect", {})
        if (
            global_effect.get("region")
            and int(global_effect.get("binding_action_index", -1)) == index
        ):
            global_binding = str(global_effect.get("region_binding") or "")
            if global_binding == "target_region":
                global_effect["region"] = target_region
            elif global_binding == "source_region":
                global_effect["region"] = source_region
            elif global_binding == "surface_region":
                global_effect["region"] = list(decision.surface_region)
        refinements.append({
            "action_index": index,
            "source": source,
            "target": target,
            "original_start": [float(action.frame_x1), float(action.frame_y1)],
            "refined_start": [round(start[0], 3), round(start[1], 3)],
            "original_end": [float(action.frame_x2), float(action.frame_y2)],
            "refined_end": [round(end[0], 3), round(end[1], 3)],
            "correction_distance": round(math.hypot(*start_delta) + math.hypot(*end_delta), 3),
            "effect_region_binding": effect_binding,
            "refined_effect_region": list(
                action_payload.get("expected_effect_region") or []
            ),
            "grounding_mode": action.grounding_mode,
        })

    for index in anchored_point_indices:
        action = decision.actions[index]
        anchor_field = "target_region" if action.target_region else "source_region"
        rough_region = list(getattr(action, anchor_field))
        anchor = _resolve_region(
            image,
            rough_region,
            list(decision.interaction_region),
            role=f"action[{index}]_{anchor_field}",
        )
        resolved_region = list(anchor["resolved_region"])
        point = _project_point(
            (float(action.frame_x), float(action.frame_y)),
            rough_region,
            resolved_region,
        )
        action_payload = dict(payload["actions"][index])
        action_payload.update({
            "frame_x": point[0],
            "frame_y": point[1],
            anchor_field: resolved_region,
        })
        other_field = (
            "source_region" if anchor_field == "target_region" else "target_region"
        )
        other_region = list(getattr(action, other_field))
        if other_region and all(
            abs(float(left) - float(right)) <= 1e-6
            for left, right in zip(other_region, rough_region)
        ):
            action_payload[other_field] = resolved_region
        effect_binding = action.effect_region_binding
        if action.expected_effect_region:
            if effect_binding == "target_region" and action_payload.get("target_region"):
                action_payload["expected_effect_region"] = list(
                    action_payload["target_region"],
                )
            elif effect_binding == "source_region" and action_payload.get("source_region"):
                action_payload["expected_effect_region"] = list(
                    action_payload["source_region"],
                )
            elif effect_binding == "surface_region":
                action_payload["expected_effect_region"] = list(
                    decision.surface_region,
                )
        payload["actions"][index] = action_payload
        global_effect = payload.get("expected_effect", {})
        if (
            global_effect.get("region")
            and int(global_effect.get("binding_action_index", -1)) == index
        ):
            global_binding = str(global_effect.get("region_binding") or "")
            if global_binding == "target_region" and action_payload.get("target_region"):
                global_effect["region"] = list(action_payload["target_region"])
            elif global_binding == "source_region" and action_payload.get("source_region"):
                global_effect["region"] = list(action_payload["source_region"])
            elif global_binding == "surface_region":
                global_effect["region"] = list(decision.surface_region)
        refinements.append({
            "action_index": index,
            "action": action.action,
            "grounding_mode": action.grounding_mode,
            "anchor_field": anchor_field,
            "anchor": anchor,
            "original_point": [float(action.frame_x), float(action.frame_y)],
            "refined_point": [round(point[0], 3), round(point[1], 3)],
            "effect_region_binding": effect_binding,
            "refined_effect_region": list(
                action_payload.get("expected_effect_region") or []
            ),
        })

    try:
        refined = InteractionDecision.model_validate(payload)
    except Exception as exc:
        raise VisualGeometryError(f"refined geometry violated the typed safety contract: {exc}") from exc
    refined._canonicalization_repairs = getattr(
        decision, "_canonicalization_repairs", [],
    )
    return refined, {
        "verified": True,
        "applied": True,
        "provider": "local_visual_geometry",
        "artifact": str(image_path),
        "artifact_dimensions": {
            "width": image_width,
            "height": image_height,
        },
        "pixel_to_screen_affine": (
            {
                "scale_x": float(int(frame_region["width"]) / image_width),
                "scale_y": float(int(frame_region["height"]) / image_height),
                "translate_x": int(frame_region["left"]),
                "translate_y": int(frame_region["top"]),
                "coordinate_space": "artifact_pixels_to_physical_screen",
                "dimension_identity_verified": True,
            }
            if frame_region is not None else None
        ),
        "refinements": refinements,
    }
