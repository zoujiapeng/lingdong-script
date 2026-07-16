"""Typed, target-bound visual interaction planning and execution.

This module is intentionally application-agnostic.  A persisted authoritative
frame is converted into one semantic turn, then validated and mapped into a
local Controller transaction.  The model never supplies physical coordinates
or an invented path, and the Controller never chooses game/application
semantics.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Literal
import unicodedata

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    model_validator,
)

from ..perception import vlm
from ..perception.surface_binding import InteractionRegion


SpatialActionName = Literal[
    "click", "double_click", "hover", "move_mouse", "drag",
    "draw_strokes", "scroll",
]
ActionName = Literal[
    "click", "double_click", "hover", "move_mouse", "mouse_down",
    "mouse_up", "drag", "draw_strokes", "scroll", "select_color",
    "type_text", "hotkey", "hold_key", "wait_stable",
]


_POINT_ACTIONS = {
    "click", "double_click", "hover", "move_mouse", "mouse_down",
    "mouse_up", "scroll",
}
_SPATIAL_ACTIONS = _POINT_ACTIONS | {"drag", "draw_strokes"}
_MOUSE_BUTTONS = {"left", "right", "middle"}
_UNAVAILABLE_TRANSACTION_PRIMITIVES = {
    "key_down", "key_up",
}


class InteractionPlanOutputError(ValueError):
    """A model payload that deterministic normalization cannot make safe.

    A malformed semantic plan is not evidence that the pixels changed.  The
    stage runtime therefore treats this as a non-retryable inference result
    for the current authoritative frame instead of taking another screenshot
    and asking the model the same question again.
    """

    failure_type = "interaction_plan_invalid"

    def __init__(
        self,
        message: str,
        *,
        code: str = "schema_validation_failed",
        repairs: list[str] | tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = str(code or "schema_validation_failed")
        self.repairs = tuple(str(item) for item in repairs if str(item))


def _coord(value: Any, name: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or not 0.0 <= float(value) <= 1000.0
    ):
        raise ValueError(f"{name} must be a finite frame coordinate in 0..1000")
    return float(value)


def _region(value: Any, name: str) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(f"{name} must be [left, top, right, bottom]")
    left, top, right, bottom = (
        _coord(item, f"{name}[{index}]") for index, item in enumerate(value)
    )
    if right <= left or bottom <= top:
        raise ValueError(f"{name} must have positive width and height")
    return left, top, right, bottom


def _point_in_region(
    point: tuple[float, float],
    bounds: tuple[float, float, float, float],
) -> bool:
    x, y = point
    left, top, right, bottom = bounds
    return left <= x <= right and top <= y <= bottom


def _translated_source_inside_target(
    source: tuple[float, float, float, float],
    target: tuple[float, float, float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> bool:
    """Prove the source footprint, not merely its cursor, fits after a drag.

    Pointer drags preserve the picked offset within the source object.  Moving
    the cursor into a target therefore does not prove that the object itself is
    inside it.  This calculation is generic geometry and applies to any
    bounded source/target transfer; it contains no application semantics.
    """
    source_left, source_top, source_right, source_bottom = source
    target_left, target_top, target_right, target_bottom = target
    start_x, start_y = start
    end_x, end_y = end
    translated_left = end_x - (start_x - source_left)
    translated_top = end_y - (start_y - source_top)
    translated_right = end_x + (source_right - start_x)
    translated_bottom = end_y + (source_bottom - start_y)
    epsilon = 1e-6
    return (
        translated_left >= target_left - epsilon
        and translated_top >= target_top - epsilon
        and translated_right <= target_right + epsilon
        and translated_bottom <= target_bottom + epsilon
    )


class InteractionAction(BaseModel):
    """One fully typed action grounded in the authoritative frame."""

    model_config = ConfigDict(extra="forbid")

    action: ActionName
    frame_x: float | None = None
    frame_y: float | None = None
    frame_x1: float | None = None
    frame_y1: float | None = None
    frame_x2: float | None = None
    frame_y2: float | None = None
    frame_points: list[list[float]] = Field(default_factory=list)
    frame_strokes: list[list[list[float]]] = Field(default_factory=list)
    # Semantic object/region grounding is deliberately separate from the
    # expected effect ROI.  These bounds describe where the source and target
    # visibly are in the authoritative frame; they are never physical pixels.
    source_region: list[float] = Field(default_factory=list)
    target_region: list[float] = Field(default_factory=list)
    grounding_mode: Literal[
        "auto", "object_anchor", "semantic_control", "free_path",
        "relative_gesture", "bounded_coordinates",
    ] = "auto"
    placement_requirement: Literal[
        "point_inside_target", "source_inside_target",
    ] = "point_inside_target"
    button: Literal["left", "right", "middle"] = "left"
    double: bool = False
    color: str = ""
    text: str = ""
    keys: list[str] = Field(default_factory=list)
    direction: str = ""
    amount: int = 0
    duration_ms: int = Field(default=0, ge=0, le=30_000)
    expected_effect_region: list[float] = Field(default_factory=list)
    effect_region_binding: Literal[
        "auto", "target_region", "source_region", "surface_region",
        "independent_region",
    ] = "auto"
    requires_fresh_frame_after: bool = True
    rationale: str = ""

    @model_validator(mode="before")
    @classmethod
    def reject_unavailable_primitives(cls, value: Any) -> Any:
        action = str(value.get("action") or "") if isinstance(value, dict) else ""
        if action in _UNAVAILABLE_TRANSACTION_PRIMITIVES:
            raise ValueError(
                f"{action} is not an executable run_action_batch capability; "
                "the typed visual planner must fail closed instead of emitting "
                "an action that the Controller cannot execute"
            )
        return value

    @model_validator(mode="after")
    def validate_action(self) -> "InteractionAction":
        if self.expected_effect_region:
            _region(self.expected_effect_region, "expected_effect_region")
        source_bounds = (
            _region(self.source_region, "source_region")
            if self.source_region else None
        )
        target_bounds = (
            _region(self.target_region, "target_region")
            if self.target_region else None
        )
        if self.effect_region_binding == "auto":
            if self.expected_effect_region and target_bounds and all(
                abs(float(left) - float(right)) <= 1e-6
                for left, right in zip(self.expected_effect_region, self.target_region)
            ):
                self.effect_region_binding = "target_region"
            elif self.expected_effect_region and source_bounds and all(
                abs(float(left) - float(right)) <= 1e-6
                for left, right in zip(self.expected_effect_region, self.source_region)
            ):
                self.effect_region_binding = "source_region"
            else:
                self.effect_region_binding = "independent_region"
        if self.effect_region_binding == "target_region" and not target_bounds:
            raise ValueError("target-bound expected effect requires target_region")
        if self.effect_region_binding == "source_region" and not source_bounds:
            raise ValueError("source-bound expected effect requires source_region")
        if self.grounding_mode == "auto":
            if self.action == "drag":
                self.grounding_mode = "object_anchor"
            elif self.action == "draw_strokes":
                self.grounding_mode = "free_path"
            elif self.action == "scroll":
                self.grounding_mode = "relative_gesture"
            elif self.action in _POINT_ACTIONS and (source_bounds or target_bounds):
                self.grounding_mode = "object_anchor"
            else:
                self.grounding_mode = "bounded_coordinates"
        if self.grounding_mode in {"object_anchor", "semantic_control"}:
            if self.action in _POINT_ACTIONS and not (source_bounds or target_bounds):
                raise ValueError(
                    f"{self.grounding_mode} point action requires a visible "
                    "source_region or target_region"
                )
        if self.grounding_mode == "free_path" and self.action != "draw_strokes":
            raise ValueError("free_path grounding is valid only for draw_strokes")
        if self.grounding_mode == "relative_gesture" and self.action != "scroll":
            raise ValueError("relative_gesture grounding is valid only for scroll")
        if self.action in _POINT_ACTIONS:
            _coord(self.frame_x, "frame_x")
            _coord(self.frame_y, "frame_y")
            point = (float(self.frame_x), float(self.frame_y))
            if source_bounds and not _point_in_region(point, source_bounds):
                raise ValueError(
                    f"{self.action} point must be inside source_region"
                )
            if target_bounds and not _point_in_region(point, target_bounds):
                raise ValueError(
                    f"{self.action} point must be inside target_region"
                )
            if self.action == "double_click":
                self.double = True
            elif self.double and self.action != "click":
                raise ValueError("double is valid only for click/double_click")
            if self.action == "hover" and self.duration_ms <= 0:
                self.duration_ms = 350
            if self.action in {"mouse_down", "mouse_up"} and self.double:
                raise ValueError("mouse_down/mouse_up cannot be double-clicked")
        elif self.action == "drag":
            for name in ("frame_x1", "frame_y1", "frame_x2", "frame_y2"):
                _coord(getattr(self, name), name)
            for index, point in enumerate(self.frame_points):
                if not isinstance(point, list) or len(point) != 2:
                    raise ValueError(f"frame_points[{index}] must be [x,y]")
                _coord(point[0], f"frame_points[{index}][0]")
                _coord(point[1], f"frame_points[{index}][1]")
            start = (float(self.frame_x1), float(self.frame_y1))
            end = (float(self.frame_x2), float(self.frame_y2))
            if source_bounds and not _point_in_region(start, source_bounds):
                raise ValueError("drag start must be inside source_region")
            if target_bounds and not _point_in_region(end, target_bounds):
                raise ValueError("drag end must be inside target_region")
            if self.placement_requirement == "source_inside_target":
                if not source_bounds or not target_bounds:
                    raise ValueError(
                        "source_inside_target requires both source_region and "
                        "target_region"
                    )
                if not _translated_source_inside_target(
                    source_bounds, target_bounds, start, end,
                ):
                    raise ValueError(
                        "drag endpoint puts the cursor in target_region but does not "
                        "place the complete source_region inside target_region"
                    )
        elif self.action == "draw_strokes":
            if not self.frame_strokes:
                raise ValueError("draw_strokes requires frame_strokes")
            if not target_bounds:
                raise ValueError(
                    "interactive draw_strokes requires target_region so every "
                    "pointer sample is bounded by the visible legal surface"
                )
            for stroke_index, stroke in enumerate(self.frame_strokes):
                if not isinstance(stroke, list) or len(stroke) < 2:
                    raise ValueError(
                        f"frame_strokes[{stroke_index}] requires at least two points"
                    )
                for point_index, point in enumerate(stroke):
                    if not isinstance(point, list) or len(point) != 2:
                        raise ValueError(
                            f"frame_strokes[{stroke_index}][{point_index}] must be [x,y]"
                        )
                    _coord(point[0], "stroke x")
                    _coord(point[1], "stroke y")
                    normalized_point = (float(point[0]), float(point[1]))
                    if source_bounds and not _point_in_region(
                        normalized_point, source_bounds,
                    ):
                        raise ValueError(
                            "draw_strokes point must be inside source_region"
                        )
                    if target_bounds and not _point_in_region(
                        normalized_point, target_bounds,
                    ):
                        raise ValueError(
                            "draw_strokes point must be inside target_region"
                        )
        elif self.action == "select_color":
            if not self.color.strip():
                raise ValueError("select_color requires color")
        elif self.action == "type_text":
            if not self.text:
                raise ValueError("type_text requires non-empty text")
        elif self.action == "hotkey":
            if not self.keys:
                raise ValueError("hotkey requires keys")
        elif self.action == "hold_key":
            if not self.keys:
                raise ValueError("hold_key requires keys")
            if self.duration_ms <= 0:
                raise ValueError("hold_key requires a positive duration_ms")
        elif self.action == "wait_stable":
            if self.duration_ms <= 0:
                self.duration_ms = 300
        if (
            self.action != "drag"
            and self.placement_requirement != "point_inside_target"
        ):
            raise ValueError(
                "source_inside_target placement is valid only for drag"
            )
        if self.button not in _MOUSE_BUTTONS:
            raise ValueError("unsupported mouse button")
        return self

    def normalized_points(self) -> list[tuple[float, float]]:
        if self.action in _POINT_ACTIONS:
            return [(float(self.frame_x), float(self.frame_y))]
        if self.action == "drag":
            points = [(float(self.frame_x1), float(self.frame_y1))]
            points.extend((float(item[0]), float(item[1])) for item in self.frame_points)
            points.append((float(self.frame_x2), float(self.frame_y2)))
            return points
        if self.action == "draw_strokes":
            return [
                (float(point[0]), float(point[1]))
                for stroke in self.frame_strokes
                for point in stroke
            ]
        return []


class ExpectedInteractionEffect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = ""
    region: list[float] = Field(default_factory=list)
    region_binding: Literal[
        "auto", "target_region", "source_region", "surface_region",
        "independent_region",
    ] = "auto"
    binding_action_index: int = Field(default=-1, ge=-1, le=11)
    minimum_change_ratio: float = Field(default=0.001, ge=0.0, le=1.0)
    semantic_verification_required: bool = True

    @model_validator(mode="after")
    def validate_effect(self) -> "ExpectedInteractionEffect":
        if self.region:
            _region(self.region, "expected_effect.region")
        elif self.region_binding not in {"auto", "independent_region"}:
            raise ValueError("a bound expected effect requires a region")
        return self


class InteractionDecision(BaseModel):
    """Protocol and one proposed semantic turn from one exact frame."""

    model_config = ConfigDict(extra="forbid")

    scene_summary: str
    mode: str
    turn_owner: Literal["agent", "user", "external", "unknown"]
    agent_should_act: bool
    # Stable task surface and current-turn legal ROI are deliberately distinct.
    # The empty default preserves persisted pre-split decisions, where the
    # legacy interaction_region represented both.
    surface_region: list[float] = Field(default_factory=list)
    interaction_region: list[float]
    roles: dict[str, dict[str, str]] = Field(default_factory=dict)
    rules: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    ambiguity: list[str] = Field(default_factory=list)
    actions: list[InteractionAction] = Field(default_factory=list, max_length=12)
    expected_effect: ExpectedInteractionEffect = Field(
        default_factory=ExpectedInteractionEffect,
    )
    continuation: Literal["finish", "reobserve", "wait_external", "blocked"]
    blocked_reason: str = ""
    wait_for_external_change: bool = False
    frame_id: int = Field(default=0, ge=0)
    observation_artifact_id: str = ""
    _canonicalization_repairs: tuple[str, ...] = PrivateAttr(default=())

    @model_validator(mode="after")
    def validate_decision(self) -> "InteractionDecision":
        if not self.surface_region:
            self.surface_region = list(self.interaction_region)
        surface_bounds = _region(self.surface_region, "surface_region")
        surface_region = InteractionRegion(
            tuple(int(round(value)) for value in surface_bounds),
            coordinate_space="frame_0_1000",
            label="stable_interaction_surface",
            source="visual_interaction_planner",
            confidence=self.confidence,
        )
        interaction_bounds = _region(self.interaction_region, "interaction_region")
        interaction_region = InteractionRegion(
            tuple(int(round(value)) for value in interaction_bounds),
            coordinate_space="frame_0_1000",
            label="semantic_interaction_region",
            source="visual_interaction_planner",
            confidence=self.confidence,
        )
        if self.expected_effect.region_binding == "auto":
            matches: list[tuple[int, str]] = []
            for index, action in enumerate(self.actions):
                if not self.expected_effect.region:
                    continue
                if action.expected_effect_region and all(
                    abs(float(left) - float(right)) <= 1e-6
                    for left, right in zip(
                        self.expected_effect.region, action.expected_effect_region,
                    )
                ):
                    matches.append((index, action.effect_region_binding))
                elif action.target_region and all(
                    abs(float(left) - float(right)) <= 1e-6
                    for left, right in zip(
                        self.expected_effect.region, action.target_region,
                    )
                ):
                    matches.append((index, "target_region"))
                elif action.source_region and all(
                    abs(float(left) - float(right)) <= 1e-6
                    for left, right in zip(
                        self.expected_effect.region, action.source_region,
                    )
                ):
                    matches.append((index, "source_region"))
            unique_matches = sorted(set(matches))
            if len(unique_matches) == 1:
                (
                    self.expected_effect.binding_action_index,
                    self.expected_effect.region_binding,
                ) = unique_matches[0]
            else:
                self.expected_effect.region_binding = "independent_region"
                self.expected_effect.binding_action_index = -1
        if self.expected_effect.region_binding in {"target_region", "source_region"}:
            index = int(self.expected_effect.binding_action_index)
            if index < 0 and len(self.actions) == 1:
                index = 0
                self.expected_effect.binding_action_index = 0
            if index < 0 or index >= len(self.actions):
                raise ValueError("bound expected effect requires a valid action index")
            bound_action = self.actions[index]
            required_region = (
                bound_action.target_region
                if self.expected_effect.region_binding == "target_region"
                else bound_action.source_region
            )
            if not required_region:
                raise ValueError(
                    f"expected effect binding requires action {index} "
                    f"{self.expected_effect.region_binding}"
                )
        if not surface_region.contains(interaction_region, tolerance=1):
            raise ValueError("per-turn interaction region escaped the stable surface")
        if self.expected_effect.region:
            effect = InteractionRegion(
                tuple(int(round(value)) for value in self.expected_effect.region),
                coordinate_space="frame_0_1000",
                label="expected_effect_region",
                source="visual_interaction_planner",
            )
            if not surface_region.contains(effect, tolerance=1):
                raise ValueError("expected effect escaped the stable surface")
        for action in self.actions:
            for field_name, bounds in (
                ("source_region", action.source_region),
                ("target_region", action.target_region),
            ):
                if not bounds:
                    continue
                grounding_region = InteractionRegion(
                    tuple(int(round(value)) for value in bounds),
                    coordinate_space="frame_0_1000",
                    label=f"action_{field_name}",
                    source="visual_interaction_planner",
                )
                if not interaction_region.contains(grounding_region, tolerance=1):
                    raise ValueError(
                        f"action {field_name} escaped the interaction region"
                    )
            if action.expected_effect_region:
                action_effect = InteractionRegion(
                    tuple(int(round(value)) for value in action.expected_effect_region),
                    coordinate_space="frame_0_1000",
                    label="action_expected_effect_region",
                    source="visual_interaction_planner",
                )
                if not surface_region.contains(action_effect, tolerance=1):
                    raise ValueError("action expected effect escaped the stable surface")
            if not all(
                interaction_region.contains_point(
                    int(round(x)), int(round(y)), tolerance=1,
                )
                for x, y in action.normalized_points()
            ):
                raise ValueError("action path escaped the per-turn interaction region")

        if self.continuation == "wait_external" and not self.wait_for_external_change:
            raise ValueError("wait_external continuation requires an external-change watch")
        if self.continuation != "wait_external" and self.wait_for_external_change:
            raise ValueError("bounded continuation cannot arm an external-change watch")
        if self.continuation == "blocked":
            if self.agent_should_act or self.actions:
                raise ValueError("a blocked interaction decision cannot authorize mutation")
            if not self.blocked_reason.strip() and not self.ambiguity:
                raise ValueError("a blocked interaction decision requires a concrete reason")
        if self.agent_should_act:
            if self.turn_owner != "agent":
                raise ValueError("agent_should_act requires turn_owner=agent")
            if self.confidence < 0.55:
                raise ValueError("interaction confidence is too low for mutation")
            if self.ambiguity:
                raise ValueError("ambiguous interaction cannot authorize mutation")
            if not self.actions:
                raise ValueError("agent turn requires at least one typed action")
            if not self.expected_effect.description.strip():
                raise ValueError("agent turn requires an expected visible effect")
            if (
                self.continuation in {"finish", "wait_external"}
                and not self.expected_effect.semantic_verification_required
            ):
                raise ValueError(
                    "terminal or externally yielded actions require semantic effect verification"
                )
        else:
            if self.actions:
                raise ValueError("a no-mutation decision must not contain actions")
            if self.continuation == "reobserve":
                raise ValueError("reobserve requires a causal action or external change")
            if self.continuation == "wait_external" and self.turn_owner not in {
                "user", "external", "unknown",
            }:
                raise ValueError("an agent-owned no-op cannot wait for an external turn")

        # Several stable pointer operations may form one semantic turn, but no
        # action may depend on an unseen intermediate frame.  A semantic color
        # selection is Controller-grounded setup and does not consume this
        # open-loop budget.
        spatial = [
            item for item in self.actions
            if item.action in _SPATIAL_ACTIONS
        ]
        spatial_names = [item.action for item in spatial]
        continuous_pointer_transaction = bool(
            len(spatial) >= 3
            and spatial_names[0] == "mouse_down"
            and spatial_names[-1] == "mouse_up"
            and all(name == "move_mouse" for name in spatial_names[1:-1])
            and spatial[0].button == spatial[-1].button
        )
        if (
            any(name in {"mouse_down", "mouse_up"} for name in spatial_names)
            and not continuous_pointer_transaction
        ):
            raise ValueError(
                "mouse_down/move_mouse/mouse_up must form one balanced continuous transaction"
            )
        if (
            len(spatial) > 1
            and not continuous_pointer_transaction
            and any(item.requires_fresh_frame_after for item in spatial[:-1])
        ):
            raise ValueError(
                "multiple spatial actions require an intermediate authoritative frame"
            )
        return self

    @property
    def canonicalization_repairs(self) -> tuple[str, ...]:
        return self._canonicalization_repairs

    def region_object(self) -> InteractionRegion:
        return InteractionRegion(
            tuple(int(round(value)) for value in self.interaction_region),
            coordinate_space="frame_0_1000",
            label="semantic_interaction_region",
            source="visual_interaction_planner",
            confidence=self.confidence,
        )

    def surface_object(self) -> InteractionRegion:
        return InteractionRegion(
            tuple(int(round(value)) for value in self.surface_region),
            coordinate_space="frame_0_1000",
            label="stable_interaction_surface",
            source="visual_interaction_planner",
            confidence=self.confidence,
        )


def _planning_prompt(
    task: str,
    intent: dict[str, Any],
    prior: dict[str, Any],
    *,
    immutable_roles: dict[str, dict[str, str]] | None = None,
    primary_action_modality: str = "infer_from_scene",
) -> str:
    contract = {
        "scene_summary": "string",
        "mode": "string",
        "turn_owner": "agent|user|external|unknown",
        "agent_should_act": "boolean",
        "surface_region": ["stable surface left", "top", "right", "bottom"],
        "interaction_region": ["left", "top", "right", "bottom"],
        "roles": {"actor_id": {"constraint": "value"}},
        "rules": ["short rule"],
        "confidence": "number 0..1",
        "ambiguity": ["unresolved fact"],
        "actions": [{
            "action": (
                "click|double_click|hover|move_mouse|mouse_down|mouse_up|drag|"
                "draw_strokes|scroll|select_color|type_text|hotkey|hold_key|wait_stable"
            ),
            "frame_x": "0..1000 when required",
            "frame_y": "0..1000 when required",
            "frame_x1": "0..1000 when required",
            "frame_y1": "0..1000 when required",
            "frame_x2": "0..1000 when required",
            "frame_y2": "0..1000 when required",
            "frame_points": [],
            "frame_strokes": [],
            "source_region": ["left", "top", "right", "bottom"],
            "target_region": ["left", "top", "right", "bottom"],
            "grounding_mode": (
                "object_anchor|semantic_control|free_path|relative_gesture|"
                "bounded_coordinates"
            ),
            "placement_requirement": "point_inside_target|source_inside_target",
            "button": "left|right|middle",
            "color": "semantic color when required",
            "text": "literal text when required",
            "keys": [],
            "direction": "up|down|left|right when required",
            "amount": 0,
            "duration_ms": 0,
            "expected_effect_region": [],
            "effect_region_binding": (
                "target_region|source_region|surface_region|independent_region"
            ),
            "requires_fresh_frame_after": True,
            "rationale": "short reason",
        }],
        "expected_effect": {
            "description": "exact visible effect of this actor's one turn",
            "region": ["left", "top", "right", "bottom"],
            "region_binding": (
                "target_region|source_region|surface_region|independent_region"
            ),
            "binding_action_index": "0-based action index, or -1 when independent",
            "minimum_change_ratio": 0.001,
            "semantic_verification_required": True,
        },
        "continuation": "finish|reobserve|wait_external|blocked",
        "blocked_reason": "required only when continuation=blocked",
    }
    return (
        "You are the semantic planner for a general visual GUI interaction. "
        "The attached image is the exact authoritative target-window frame and all "
        "coordinates use that whole frame in 0..1000. Infer the current shared state, "
        "actors, role ownership, stable task surface, current-turn legal interaction ROI, "
        "whose turn it is, and at most "
        "one semantic agent turn. Respect actor assignments literally: never perform the "
        "user's role, never fabricate the other actor's turn, and emit no actions when the "
        "agent does not own the turn. Use a semantic Controller action only when it exactly "
        "matches visible evidence and the immutable task constraints. A drag or continuous gesture is one "
        "action with its complete path. Use double_click only when two clicks are semantically "
        "required, hover when dwell may reveal a state, and move_mouse for cursor placement. "
        "For every visible bounded source-to-target object transfer, source_region and "
        "target_region are required and must be the actual visible object and destination "
        "bounds. Ground click/drag endpoints in the visible interior with a safe margin from "
        "borders. Set placement_requirement=source_inside_target only when the visible "
        "instruction or interface requires the complete source to fit inside the target; in "
        "that mode the endpoint must preserve the pointer's offset so the translated complete "
        "source_region fits inside target_region. Use point_inside_target for overlap/drop "
        "interfaces whose visible acceptance area is smaller than the source. For a draggable "
        "object use its visible "
        "center unless the interface visibly requires another handle. For point actions, any "
        "declared source_region or target_region must contain the point. Visible controls and objects "
        "must declare an anchor region and use semantic_control or object_anchor so current pixels can "
        "re-ground the final point. bounded_coordinates is only for a position that has no visible object "
        "anchor and is still protected by frame lineage and ROI bounds. Interactive draw_strokes must declare target_region, "
        "use free_path, and keep every stroke point inside it. Scroll uses "
        "relative_gesture bound to the current ROI. Prefer one drag action "
        "for ordinary dragging. Use a same-transaction mouse_down, one or more move_mouse, and "
        "mouse_up sequence only when the interface genuinely requires a custom press/hold path; "
        "every point still needs current-frame grounding. Use hold_key only for a bounded duration. "
        "Do not emit standalone key_down or key_up: those are not executable transaction "
        "capabilities, and uncertainty must fail closed rather than inventing an equivalent action. "
        "surface_region is the stable outer task surface across turns. interaction_region is "
        "only this turn's legal action ROI and must be contained by surface_region. Source, "
        "target, and pointer paths must remain in interaction_region. Every explicit expected-effect "
        "region must declare whether it is bound to that action's target_region, source_region, the "
        "stable surface_region, or is an independent_region. A target/source-bound effect follows "
        "local geometry correction; independent_region never moves by heuristic overlap. The global "
        "expected_effect must also name region_binding and binding_action_index when it follows an "
        "action. Expected-effect regions "
        "may extend outside the action ROI but must remain inside surface_region. Multiple spatial actions are allowed only when "
        "their targets remain valid without inspecting an intermediate frame; otherwise "
        "plan only the first and require a fresh frame. Do not infer success from UI chrome "
        "or unrelated desktop changes. Prior state may include a CV-detected surface and the "
        "Win32 client authority, both in frame 0..1000; treat them as safety envelopes/hints, "
        "while the attached current pixels remain authoritative. If the current frame does not "
        "contain enough visible evidence to ground any safe action, return no actions, "
        "agent_should_act=false, continuation=blocked, and a concrete blocked_reason. Never use "
        "reobserve as a request to look at the same unchanged frame again. Set "
        "continuation=finish only "
        "when the proposed action "
        "plus its post-action verification can complete the bounded task, continuation=reobserve "
        "only when one or more actions in this response causally require a fresh frame before "
        "another action, and continuation=wait_external "
        "only for a real external participant turn. The runtime derives whether an external-change "
        "watch is armed from continuation, so do not return a separate wait_for_external_change "
        "field. Return ONLY one JSON object. Omit every optional "
        "action field that is not used; unknown keys are rejected.\n"
        f"User task: {task}\n"
        f"Structured intent candidate: {json.dumps(intent, ensure_ascii=False)}\n"
        f"Immutable contract roles (copy exactly; never override): "
        f"{json.dumps(immutable_roles or {}, ensure_ascii=False)}\n"
        f"Primary action modality constraint: {primary_action_modality or 'infer_from_scene'}\n"
        f"Prior verified session state: {json.dumps(prior, ensure_ascii=False)}\n"
        f"Required JSON shape: {json.dumps(contract, ensure_ascii=False)}"
    )


def _mapping_region(value: Any) -> Any:
    """Accept the common object spelling without weakening region checks."""
    if isinstance(value, dict) and all(
        key in value for key in ("left", "top", "right", "bottom")
    ):
        return [value["left"], value["top"], value["right"], value["bottom"]]
    return value


def _optional_effect_region(value: Any) -> Any:
    """Turn only genuinely empty effect sentinels into the typed empty ROI."""
    value = _mapping_region(value)
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            left, top, right, bottom = [float(item) for item in value]
        except (TypeError, ValueError):
            return list(value) if isinstance(value, tuple) else value
        if all(math.isfinite(item) for item in (left, top, right, bottom)):
            if right <= left or bottom <= top:
                return []
        return list(value)
    return value


def _canonicalize_model_decision(
    raw: dict[str, Any],
    *,
    prior_state: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Repair harmless provider drift while retaining fail-closed semantics.

    This pass never invents a target or coordinates.  It only normalizes
    equivalent JSON spellings/defaults, derives a broad visible-effect
    description from an already authorized action, and converts a causeless
    no-op ``reobserve`` into an explicit blocked result.  The latter lets the
    runtime fail the exact frame once instead of repeatedly capturing and
    querying identical pixels.
    """
    normalized = dict(raw)
    repairs: list[str] = []

    aliases = {
        "sceneSummary": "scene_summary",
        "turnOwner": "turn_owner",
        "agentShouldAct": "agent_should_act",
        "surfaceRegion": "surface_region",
        "interactionRegion": "interaction_region",
        "expectedEffect": "expected_effect",
        "blockedReason": "blocked_reason",
    }
    for alias, canonical in aliases.items():
        if canonical not in normalized and alias in normalized:
            normalized[canonical] = normalized.pop(alias)
            repairs.append(f"alias:{alias}->{canonical}")

    actions = normalized.get("actions")
    if actions is None:
        actions = []
        repairs.append("actions:null->empty")
    elif isinstance(actions, dict):
        actions = [actions]
        repairs.append("actions:object->list")
    if isinstance(actions, list):
        fixed_actions: list[Any] = []
        known_actions = {
            "click", "double_click", "hover", "move_mouse", "mouse_down",
            "mouse_up", "drag", "draw_strokes", "scroll", "select_color",
            "type_text", "hotkey", "hold_key", "wait_stable",
        }
        for index, candidate in enumerate(actions):
            if not isinstance(candidate, dict):
                fixed_actions.append(candidate)
                continue
            action = dict(candidate)
            action_name = str(action.get("action") or "")
            canonical_name = action_name.strip().casefold().replace("-", "_").replace(" ", "_")
            if canonical_name in known_actions and canonical_name != action_name:
                action["action"] = canonical_name
                repairs.append(f"actions[{index}].action:normalized")
            for key in (
                "frame_points", "frame_strokes", "source_region",
                "target_region", "expected_effect_region", "keys",
            ):
                if action.get(key) is None:
                    action[key] = []
                    repairs.append(f"actions[{index}].{key}:null->empty")
            for key in ("source_region", "target_region"):
                mapped = _mapping_region(action.get(key))
                if mapped is not action.get(key):
                    action[key] = mapped
                    repairs.append(f"actions[{index}].{key}:object->list")
            effect_region = _optional_effect_region(action.get("expected_effect_region"))
            if effect_region != action.get("expected_effect_region"):
                action["expected_effect_region"] = effect_region
                repairs.append(f"actions[{index}].expected_effect_region:empty->unset")
            if action.get("effect_region_binding") is None:
                action.pop("effect_region_binding", None)
                repairs.append(f"actions[{index}].effect_region_binding:null->auto")
            if action.get("grounding_mode") is None:
                action.pop("grounding_mode", None)
                repairs.append(f"actions[{index}].grounding_mode:null->auto")
            fixed_actions.append(action)
        actions = fixed_actions
        normalized["actions"] = actions

    if "agent_should_act" not in normalized and isinstance(actions, list):
        normalized["agent_should_act"] = bool(actions)
        repairs.append("agent_should_act:derived_from_actions")
    if normalized.get("agent_should_act") is True and not normalized.get("turn_owner"):
        normalized["turn_owner"] = "agent"
        repairs.append("turn_owner:derived_agent")

    interaction_region = _mapping_region(normalized.get("interaction_region"))
    if interaction_region is None:
        authority = prior_state.get("window_client_authority")
        if isinstance(authority, list) and len(authority) == 4:
            interaction_region = list(authority)
            repairs.append("interaction_region:controller_authority")
    if interaction_region is not None:
        normalized["interaction_region"] = interaction_region
    surface_region = _mapping_region(normalized.get("surface_region"))
    if surface_region is None:
        prior_surface = prior_state.get("surface_region")
        if isinstance(prior_surface, dict):
            prior_surface = (
                prior_surface.get("bounds")
                if prior_surface.get("binding_policy")
                or prior_surface.get("source") == "visual_interaction_planner"
                else None
            )
        if isinstance(prior_surface, list) and len(prior_surface) == 4:
            surface_region = list(prior_surface)
            repairs.append("surface_region:prior_stable_surface")
        elif isinstance(interaction_region, (list, tuple)) and len(interaction_region) == 4:
            surface_region = list(interaction_region)
            repairs.append("surface_region:legacy_interaction_region")
    if surface_region is not None:
        normalized["surface_region"] = surface_region

    for key in ("roles", "rules", "ambiguity"):
        if normalized.get(key) is None:
            normalized[key] = {} if key == "roles" else []
            repairs.append(f"{key}:null->empty")
    for key in ("rules", "ambiguity"):
        if isinstance(normalized.get(key), str):
            normalized[key] = [normalized[key]]
            repairs.append(f"{key}:string->list")

    effect = normalized.get("expected_effect")
    if effect is None:
        effect = {}
        repairs.append("expected_effect:null->object")
    elif isinstance(effect, str):
        effect = {"description": effect}
        repairs.append("expected_effect:string->object")
    if isinstance(effect, dict):
        effect = dict(effect)
        effect_region = _optional_effect_region(effect.get("region"))
        if effect_region != effect.get("region"):
            repairs.append("expected_effect.region:empty->unset")
        effect["region"] = effect_region
        if effect.get("region_binding") is None:
            effect.pop("region_binding", None)
            repairs.append("expected_effect.region_binding:null->auto")
        if effect.get("binding_action_index") is None:
            effect.pop("binding_action_index", None)
            repairs.append("expected_effect.binding_action_index:null->default")
        if (
            normalized.get("agent_should_act") is True
            and isinstance(actions, list)
            and actions
            and not str(effect.get("description") or "").strip()
        ):
            effect["description"] = (
                "the requested visible state changes within the grounded action region"
            )
            repairs.append("expected_effect.description:derived_from_grounded_action")
        if not effect.get("region") and isinstance(actions, list):
            explicit_regions: list[tuple[float, float, float, float]] = []
            for item in actions:
                if not isinstance(item, dict):
                    continue
                try:
                    explicit_regions.append(_region(
                        item.get("expected_effect_region"),
                        "expected_effect_region",
                    ))
                except (TypeError, ValueError):
                    continue
            if explicit_regions:
                effect["region"] = [
                    min(float(item[0]) for item in explicit_regions),
                    min(float(item[1]) for item in explicit_regions),
                    max(float(item[2]) for item in explicit_regions),
                    max(float(item[3]) for item in explicit_regions),
                ]
                repairs.append("expected_effect.region:derived_from_action_effects")
        normalized["expected_effect"] = effect

    continuation = str(normalized.get("continuation") or "").strip().casefold()
    continuation_aliases = {
        "complete": "finish", "done": "finish",
        "continue": "reobserve", "observe": "reobserve",
        "wait": "wait_external", "wait_for_external": "wait_external",
    }
    if continuation in continuation_aliases:
        normalized["continuation"] = continuation_aliases[continuation]
        repairs.append("continuation:normalized_alias")
    elif continuation:
        normalized["continuation"] = continuation
    elif normalized.get("agent_should_act") is True and actions:
        normalized["continuation"] = "reobserve"
        repairs.append("continuation:derived_causal_reobserve")

    if (
        normalized.get("agent_should_act") is False
        and not actions
        and normalized.get("continuation") == "reobserve"
    ):
        normalized["continuation"] = "blocked"
        ambiguity = normalized.get("ambiguity")
        reason = (
            str(ambiguity[0]).strip()
            if isinstance(ambiguity, list) and ambiguity and str(ambiguity[0]).strip()
            else "the current authoritative frame does not ground a causal action"
        )
        normalized.setdefault("blocked_reason", reason)
        repairs.append("continuation:causeless_reobserve->blocked")

    return normalized, list(dict.fromkeys(repairs))


def _canonicalize_model_action_geometry(raw: dict[str, Any]) -> dict[str, Any]:
    """Resolve geometry that follows deterministically from typed semantics.

    The semantic planner chooses the visible source, destination, and placement
    rule.  Once it declares that the complete source must fit inside a bounded
    target, asking the model to also estimate the one exact offset-preserving
    endpoint is redundant and unstable.  Centering the translated source in the
    target is the maximum-margin solution for every rectangular source/target
    pair.  Impossible or malformed geometry is left untouched so Pydantic's
    normal fail-closed validation still rejects it.
    """
    normalized = dict(raw)
    actions = normalized.get("actions")
    if not isinstance(actions, list):
        return normalized
    normalized_actions: list[Any] = []
    for candidate in actions:
        if not isinstance(candidate, dict):
            normalized_actions.append(candidate)
            continue
        action = dict(candidate)
        if (
            action.get("action") != "drag"
            or action.get("placement_requirement") != "source_inside_target"
        ):
            normalized_actions.append(action)
            continue
        try:
            source = _region(action.get("source_region"), "source_region")
            target = _region(action.get("target_region"), "target_region")
            start = (
                _coord(action.get("frame_x1"), "frame_x1"),
                _coord(action.get("frame_y1"), "frame_y1"),
            )
        except (TypeError, ValueError):
            normalized_actions.append(action)
            continue
        source_width = source[2] - source[0]
        source_height = source[3] - source[1]
        target_width = target[2] - target[0]
        target_height = target[3] - target[1]
        if (
            not _point_in_region(start, source)
            or source_width > target_width
            or source_height > target_height
        ):
            normalized_actions.append(action)
            continue
        source_center = (
            (source[0] + source[2]) / 2.0,
            (source[1] + source[3]) / 2.0,
        )
        target_center = (
            (target[0] + target[2]) / 2.0,
            (target[1] + target[3]) / 2.0,
        )
        action["frame_x2"] = target_center[0] + (start[0] - source_center[0])
        action["frame_y2"] = target_center[1] + (start[1] - source_center[1])
        normalized_actions.append(action)
    normalized["actions"] = normalized_actions
    return normalized


def _constraint_text(value: Any) -> str:
    return " ".join(
        unicodedata.normalize("NFKC", str(value or "")).casefold().split()
    )


def _apply_immutable_roles(
    raw: dict[str, Any],
    immutable_roles: dict[str, dict[str, str]],
    *,
    repairs: list[str],
) -> dict[str, Any]:
    """Overlay contract-owned roles while rejecting model contradictions."""
    if not immutable_roles:
        return raw
    normalized = dict(raw)
    proposed = normalized.get("roles")
    if not isinstance(proposed, dict):
        raise InteractionPlanOutputError(
            "visual model roles are incompatible with immutable contract roles",
            code="contract_role_schema_invalid",
            repairs=repairs,
        )
    effective: dict[str, Any] = {
        str(actor): dict(values) if isinstance(values, dict) else values
        for actor, values in proposed.items()
    }
    for actor, constraints in immutable_roles.items():
        actor_id = str(actor)
        current = effective.get(actor_id)
        if current is None:
            current = {}
            effective[actor_id] = current
            repairs.append(f"roles.{actor_id}:contract_injected")
        if not isinstance(current, dict):
            raise InteractionPlanOutputError(
                f"visual model role for {actor_id!r} is not an object",
                code="contract_role_schema_invalid",
                repairs=repairs,
            )
        for key, expected in constraints.items():
            role_key = str(key)
            expected_value = str(expected)
            if role_key in current and _constraint_text(current[role_key]) != _constraint_text(expected_value):
                raise InteractionPlanOutputError(
                    f"visual model contradicted immutable role {actor_id}.{role_key}",
                    code="contract_role_conflict",
                    repairs=repairs,
                )
            if role_key not in current:
                repairs.append(f"roles.{actor_id}.{role_key}:contract_injected")
            current[role_key] = expected_value
    normalized["roles"] = effective
    return normalized


def validate_primary_action_modality(
    decision: InteractionDecision,
    primary_action_modality: str,
) -> None:
    """Reject a plan that replaces one explicit task modality with another."""
    primary = _constraint_text(primary_action_modality).replace(" ", "_")
    if primary not in {"drag", "click", "draw", "type"} or not decision.agent_should_act:
        return
    action_modalities = {
        "drag": "drag",
        "mouse_down": "drag",
        "mouse_up": "drag",
        "click": "click",
        "double_click": "click",
        "draw_strokes": "draw",
        "type_text": "type",
    }
    planned = {
        action_modalities[item.action]
        for item in decision.actions
        if item.action in action_modalities
    }
    if primary not in planned:
        raise InteractionPlanOutputError(
            f"interaction plan omitted contract primary modality {primary!r}",
            code="primary_modality_missing",
            repairs=decision.canonicalization_repairs,
        )
    conflicts = sorted(item for item in planned if item != primary)
    if conflicts:
        raise InteractionPlanOutputError(
            "interaction plan introduced conflicting primary modalities: "
            + ", ".join(conflicts),
            code="primary_modality_conflict",
            repairs=decision.canonicalization_repairs,
        )


def plan_interaction(
    image_path: str | Path,
    *,
    task: str,
    intent: dict[str, Any],
    frame_id: int,
    observation_artifact_id: str,
    prior_state: dict[str, Any] | None = None,
    immutable_roles: dict[str, dict[str, str]] | None = None,
    primary_action_modality: str = "",
) -> InteractionDecision:
    prior = dict(prior_state or {})
    contract_roles = (
        dict(immutable_roles)
        if immutable_roles is not None
        else dict(intent.get("role_assignments") or {})
    )
    primary_modality = str(
        primary_action_modality
        or intent.get("primary_action_modality")
        or "infer_from_scene"
    )
    raw = vlm.structured_json_from_image(
        image_path,
        _planning_prompt(
            task,
            intent,
            prior,
            immutable_roles=contract_roles,
            primary_action_modality=primary_modality,
        ),
        max_tokens=2400,
    )
    raw, repairs = _canonicalize_model_decision(raw, prior_state=prior)
    raw = _apply_immutable_roles(raw, contract_roles, repairs=repairs)
    raw = _canonicalize_model_action_geometry(raw)
    # `wait_for_external_change` is a compatibility field for persisted decisions,
    # not an independent semantic choice for the model.  Deriving it from the
    # typed continuation prevents a redundant boolean (often copied from a JSON
    # example) from turning an otherwise valid bounded action into an impossible
    # protocol state.  Direct InteractionDecision callers remain fail-closed.
    raw["wait_for_external_change"] = raw.get("continuation") == "wait_external"
    raw["frame_id"] = int(frame_id)
    raw["observation_artifact_id"] = str(observation_artifact_id)
    try:
        decision = InteractionDecision.model_validate(raw)
    except ValidationError as exc:
        raise InteractionPlanOutputError(
            f"visual model returned an unsafe interaction plan: {exc}",
            repairs=repairs,
        ) from exc
    decision._canonicalization_repairs = tuple(repairs)
    validate_primary_action_modality(decision, primary_modality)
    return decision


def _map_point(
    region: dict[str, int], x: float, y: float,
) -> tuple[int, int]:
    left = int(region["left"])
    top = int(region["top"])
    width = max(1, int(region["width"]))
    height = max(1, int(region["height"]))
    return (
        left + int(round(float(x) / 1000.0 * max(0, width - 1))),
        top + int(round(float(y) / 1000.0 * max(0, height - 1))),
    )


def _map_region(
    frame_region: dict[str, int], bounds: list[float],
) -> dict[str, int]:
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
        payload: dict[str, Any] = {
            "action": item.action,
            "typed_action": item.action,
            "visual_frame_id": decision.frame_id,
            "coordinate_space": "screen_physical_px",
            "source_frame_coordinates": {
                key: value
                for key, value in item.model_dump(mode="json").items()
                if key.startswith("frame_") and value not in (None, [], "")
            },
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
            payload["source_frame_grounding"] = grounding
        effect_bounds = (
            item.expected_effect_region
            or decision.expected_effect.region
        )
        if effect_bounds:
            payload["effect_region"] = _map_region(frame_region, effect_bounds)
        if item.action in _POINT_ACTIONS:
            payload["x"], payload["y"] = _map_point(
                frame_region, float(item.frame_x), float(item.frame_y),
            )
            if item.action in {"click", "double_click"}:
                payload["action"] = "click"
                payload["button"] = item.button
                payload["double"] = bool(
                    item.action == "double_click" or item.double
                )
            elif item.action in {"hover", "move_mouse"}:
                payload["action"] = "move_mouse"
            elif item.action in {"mouse_down", "mouse_up"}:
                # The raw press/release primitive acts at the current cursor.
                # Prepositioning and the button transition stay in one local
                # Controller transaction, so no model round-trip can split a
                # continuous gesture or reuse coordinates after a new frame.
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
            payload["strokes"] = [
                [list(_map_point(frame_region, point[0], point[1])) for point in stroke]
                for stroke in item.frame_strokes
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
            # Moving the pointer is the real input primitive.  A bounded local
            # stability wait supplies the dwell needed by hover-driven UI and
            # leaves the authoritative post-frame capture to the next stage.
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


def verify_interaction_effect(
    image_path: str | Path,
    *,
    task: str,
    decision: InteractionDecision,
) -> dict[str, Any]:
    """Semantically verify the post-action frame after causal ROI change."""
    effect_regions = [
        list(item.expected_effect_region)
        for item in decision.actions
        if len(item.expected_effect_region) == 4
    ]
    if len(decision.expected_effect.region) == 4:
        effect_regions.append(list(decision.expected_effect.region))
    if not effect_regions:
        effect_regions.append(list(decision.interaction_region))
    prompt = (
        "You are a strict verifier for one visual GUI interaction turn. Judge only "
        "the attached post-action target-window frame. The runtime separately proves "
        "causal pixel change, so decide whether the expected local semantic effect is "
        "visibly present, belongs to the agent's assigned role, and does not include an "
        "extra fabricated participant turn. Return ONLY JSON with keys passed (boolean), "
        "confidence (0..1), observed (array of strings), issues (array of strings), "
        "effect_region ([left,top,right,bottom] in 0..1000 or []), and task_complete "
        "(boolean). task_complete may be true only when the original user task, not just "
        "this local action, is visibly complete in this frame.\n"
        f"User task: {task}\n"
        f"Roles: {json.dumps(decision.roles, ensure_ascii=False)}\n"
        f"Expected one-turn effect: {decision.expected_effect.description}\n"
        f"Expected effect regions: {json.dumps(effect_regions)}"
    )
    raw = vlm.structured_json_from_image(image_path, prompt, max_tokens=800)
    confidence = raw.get("confidence")
    confidence_valid = (
        isinstance(confidence, (int, float))
        and not isinstance(confidence, bool)
        and math.isfinite(float(confidence))
        and 0.0 <= float(confidence) <= 1.0
    )
    issues = raw.get("issues")
    observed = raw.get("observed")
    reported_region = raw.get("effect_region")
    region_valid = False
    if isinstance(reported_region, list):
        try:
            reported = InteractionRegion(
                tuple(int(round(value)) for value in _region(reported_region, "effect_region")),
                coordinate_space="frame_0_1000",
                label="verified_effect_region",
                source="interaction_effect_verifier",
            )
            region_valid = any(
                InteractionRegion(
                    tuple(int(round(value)) for value in expected_bounds),
                    coordinate_space="frame_0_1000",
                    label="expected_effect_region",
                    source="interaction_contract",
                ).contains(reported, tolerance=6)
                for expected_bounds in effect_regions
            )
        except (TypeError, ValueError):
            region_valid = False
    passed = bool(
        raw.get("passed") is True
        and confidence_valid
        and float(confidence) >= 0.72
        and isinstance(issues, list)
        and not any(str(item).strip() for item in issues)
        and isinstance(observed, list)
        and any(str(item).strip() for item in observed)
        and region_valid
    )
    task_complete = bool(passed and raw.get("task_complete") is True)
    return {
        "ok": True,
        "verified": passed,
        "semantic_visual_verified": passed,
        "confidence": float(confidence) if confidence_valid else None,
        "observed": observed if isinstance(observed, list) else [],
        "issues": issues if isinstance(issues, list) else ["invalid verifier payload"],
        "effect_region": reported_region if isinstance(reported_region, list) else [],
        "task_complete": task_complete,
        "expected_effect": decision.expected_effect.model_dump(mode="json"),
        "verify_method": "interaction-post-frame-vlm",
    }


def verify_interaction_completion(
    image_path: str | Path,
    *,
    task: str,
    decision: InteractionDecision,
) -> dict[str, Any]:
    """Fail-closed proof for a bounded task that needs no further mutation.

    A planner saying ``finish`` is not evidence by itself.  This second pass
    judges the exact authoritative frame against the original task and the
    bound interaction region before the runtime can take the terminal branch.
    """
    prompt = (
        "You are a strict completion verifier for a bounded visual GUI task. "
        "Judge only the attached authoritative target-window frame and the original "
        "task. A planner's no-action decision is not evidence. Return ONLY JSON with "
        "keys passed (boolean), confidence (0..1), observed (array of concrete visible "
        "facts), issues (array of strings), and region ([left,top,right,bottom] in "
        "0..1000 or []). Reject uncertain, hidden, off-target, or merely planned states.\n"
        f"Original task: {task}\n"
        f"Bound stable surface region: {decision.surface_region}\n"
        f"Scene summary: {decision.scene_summary}"
    )
    raw = vlm.structured_json_from_image(image_path, prompt, max_tokens=700)
    confidence = raw.get("confidence")
    confidence_valid = (
        isinstance(confidence, (int, float))
        and not isinstance(confidence, bool)
        and math.isfinite(float(confidence))
        and 0.0 <= float(confidence) <= 1.0
    )
    issues = raw.get("issues")
    observed = raw.get("observed")
    reported_region = raw.get("region")
    region_valid = False
    if isinstance(reported_region, list):
        try:
            reported = InteractionRegion(
                tuple(int(round(value)) for value in _region(reported_region, "region")),
                coordinate_space="frame_0_1000",
                label="verified_completion_region",
                source="interaction_completion_verifier",
            )
            region_valid = decision.surface_object().contains(reported, tolerance=6)
        except (TypeError, ValueError):
            region_valid = False
    verified = bool(
        raw.get("passed") is True
        and confidence_valid
        and float(confidence) >= 0.78
        and isinstance(issues, list)
        and not any(str(item).strip() for item in issues)
        and isinstance(observed, list)
        and any(str(item).strip() for item in observed)
        and region_valid
    )
    return {
        "ok": True,
        "verified": verified,
        "semantic_visual_verified": verified,
        "confidence": float(confidence) if confidence_valid else None,
        "observed": observed if isinstance(observed, list) else [],
        "issues": issues if isinstance(issues, list) else ["invalid verifier payload"],
        "region": reported_region if isinstance(reported_region, list) else [],
        "verify_method": "bounded-interaction-completion-vlm",
    }
