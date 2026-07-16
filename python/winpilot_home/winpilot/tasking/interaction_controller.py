"""Deterministic mapping from typed visual plans to Controller actions.

The semantic planner owns meaning in authoritative-frame coordinates.  This
module owns only geometry validation, target-relative path resolution, and the
final conversion to physical input.  Keeping that boundary out of the model
planner makes both components independently testable and prevents application
semantics from leaking into the pointer executor.
"""
from __future__ import annotations

from typing import Any

from ..perception.surface_binding import InteractionRegion
from .interaction import InteractionDecision, _POINT_ACTIONS, _region


LOCAL_PATH_INSET_FRACTION = 0.04


def _map_point(region: dict[str, int], x: float, y: float) -> tuple[int, int]:
    left = int(region["left"])
    top = int(region["top"])
    width = max(1, int(region["width"]))
    height = max(1, int(region["height"]))
    return (
        left + int(round(float(x) / 1000.0 * max(0, width - 1))),
        top + int(round(float(y) / 1000.0 * max(0, height - 1))),
    )


def _map_region(frame_region: dict[str, int], bounds: list[float]) -> dict[str, int]:
    left, top, right, bottom = _region(bounds, "grounding_region")
    physical_left, physical_top = _map_point(frame_region, left, top)
    physical_right, physical_bottom = _map_point(frame_region, right, bottom)
    return {
        "x": physical_left,
        "y": physical_top,
        "width": max(1, physical_right - physical_left + 1),
        "height": max(1, physical_bottom - physical_top + 1),
    }


def _inside(region: InteractionRegion, points: list[tuple[float, float]]) -> bool:
    return all(region.contains_point(int(round(x)), int(round(y))) for x, y in points)


def controller_actions(
    decision: InteractionDecision,
    *,
    frame_region: dict[str, int],
    bound_region: InteractionRegion,
    frame_epoch: int,
) -> list[dict[str, Any]]:
    """Validate and map one decision to executable physical Controller input."""
    if not decision.agent_should_act:
        return []
    if decision.frame_id != int(frame_epoch):
        raise ValueError("interaction decision is bound to a stale authoritative frame")
    if not bound_region.contains(decision.region_object(), tolerance=4):
        raise ValueError("semantic interaction region escaped the bound surface")

    mapped: list[dict[str, Any]] = []
    for item in decision.actions:
        points = item.normalized_points()
        if points and not _inside(decision.region_object(), points):
            raise ValueError(
                f"{item.action} contains coordinates outside the semantic interaction region"
            )
        source_coordinates = {
            key: value
            for key, value in item.model_dump(mode="json").items()
            if key.startswith("frame_") and value not in (None, [], "")
        }
        target_relative_strokes = bool(item.local_strokes) or any(
            stroke.coordinate_space == "target_region_0_1000"
            for stroke in item.stroke_paths
        )
        if item.action == "draw_strokes" and target_relative_strokes:
            resolved = item.resolved_frame_strokes(
                inset_fraction=LOCAL_PATH_INSET_FRACTION,
            )
            source_coordinates.update({
                "stroke_coordinate_space": "target_region_0_1000",
                "local_strokes": item.local_strokes,
                "stroke_paths": [
                    stroke.model_dump(mode="json") for stroke in item.stroke_paths
                ],
                "resolved_frame_strokes": resolved,
                "target_inset_fraction": LOCAL_PATH_INSET_FRACTION,
            })
        payload: dict[str, Any] = {
            "action": item.action,
            "typed_action": item.action,
            "interaction_mechanism": item.interaction_mechanism,
            "affordance_evidence": item.affordance_evidence,
            "visual_frame_id": decision.frame_id,
            "coordinate_space": "screen_physical_px",
            "source_frame_coordinates": source_coordinates,
        }
        grounding: dict[str, Any] = {
            key: list(value)
            for key, value in {
                "source_region": item.source_region,
                "target_region": item.target_region,
            }.items()
            if value
        }
        if grounding:
            grounding["placement_requirement"] = item.placement_requirement
            grounding["grounding_mode"] = item.grounding_mode
            if item.target_anchor:
                grounding["target_anchor"] = list(item.target_anchor)
            if item.action == "drag":
                grounding["path_mode"] = item.path_mode
            payload["source_frame_grounding"] = grounding
        effect_bounds = item.expected_effect_region or decision.expected_effect.region
        if effect_bounds:
            payload["effect_region"] = _map_region(frame_region, effect_bounds)
        if item.action in _POINT_ACTIONS:
            payload["x"], payload["y"] = _map_point(
                frame_region, float(item.frame_x), float(item.frame_y),
            )
            if item.action in {"click", "double_click"}:
                payload["action"] = "click"
                payload["button"] = item.button
                payload["double"] = bool(item.action == "double_click" or item.double)
            elif item.action in {"hover", "move_mouse"}:
                payload["action"] = "move_mouse"
            elif item.action in {"mouse_down", "mouse_up"}:
                mapped.append({
                    **payload,
                    "action": "move_mouse",
                    "typed_action": f"{item.action}_preposition",
                })
                payload = {
                    key: value
                    for key, value in payload.items()
                    if key not in {"x", "y", "effect_region"}
                }
                payload["action"] = item.action
                payload["typed_action"] = item.action
                payload["button"] = item.button
            elif item.action == "scroll":
                payload["direction"] = item.direction or "down"
                payload["amount"] = item.amount or 3
        elif item.action == "drag":
            payload["x1"], payload["y1"] = _map_point(
                frame_region, float(item.frame_x1), float(item.frame_y1),
            )
            payload["x2"], payload["y2"] = _map_point(
                frame_region, float(item.frame_x2), float(item.frame_y2),
            )
            if item.frame_points:
                payload["points"] = [
                    list(_map_point(frame_region, point[0], point[1]))
                    for point in item.frame_points
                ]
            payload["duration_ms"] = item.duration_ms or 700
        elif item.action == "draw_strokes":
            frame_strokes = item.resolved_frame_strokes(
                inset_fraction=(
                    LOCAL_PATH_INSET_FRACTION if target_relative_strokes else 0.0
                ),
            )
            payload["strokes"] = [
                [list(_map_point(frame_region, point[0], point[1])) for point in stroke]
                for stroke in frame_strokes
            ]
            payload["duration_ms"] = item.duration_ms or 650
        elif item.action == "select_color":
            payload["color"] = item.color
            payload["name"] = item.color
        elif item.action == "type_text":
            payload["text"] = item.text
        elif item.action == "hotkey":
            payload["keys"] = item.keys
        elif item.action == "hold_key":
            payload["keys"] = item.keys
            payload["duration_ms"] = item.duration_ms
        elif item.action == "wait_stable":
            payload["stable_ms"] = item.duration_ms or 300
            payload["timeout_s"] = max(
                1.0, min(5.0, float(payload["stable_ms"]) / 1000.0 + 1.0),
            )
        mapped.append(payload)
        if item.action == "hover":
            dwell: dict[str, Any] = {
                "action": "wait_stable",
                "typed_action": "hover_dwell",
                "stable_ms": item.duration_ms,
                "timeout_s": max(
                    1.0, min(5.0, float(item.duration_ms) / 1000.0 + 1.0),
                ),
                "required": False,
            }
            if "effect_region" in payload:
                dwell["effect_region"] = dict(payload["effect_region"])
            mapped.append(dwell)
    return mapped
