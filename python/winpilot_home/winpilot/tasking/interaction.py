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
from .visual_appearance import inventory_frame_appearance, match_role_colours
from .visual_regions import (
    inventory_visible_partition_regions,
    inventory_visible_regions,
)


SpatialActionName = Literal[
    "click", "double_click", "hover", "move_mouse", "drag",
    "draw_strokes", "scroll",
]
ActionName = Literal[
    "click", "double_click", "hover", "move_mouse", "mouse_down",
    "mouse_up", "drag", "draw_strokes", "scroll", "select_color",
    "type_text", "hotkey", "hold_key", "wait_stable",
]
InteractionMechanism = Literal[
    "auto",
    "control_activation",
    "point_selection",
    "click_to_place",
    "freehand_strokes",
    "direct_manipulation",
    "text_entry",
    "keyboard_command",
    "hover_reveal",
    "scroll_navigation",
    "pointer_motion",
    "press_hold",
    "timed_wait",
]


_POINT_ACTIONS = {
    "click", "double_click", "hover", "move_mouse", "mouse_down",
    "mouse_up", "scroll",
}
_SPATIAL_ACTIONS = _POINT_ACTIONS | {"drag", "draw_strokes"}
_MOUSE_BUTTONS = {"left", "right", "middle"}
_STROKE_COORDINATE_SPACES = {
    "target_region_0_1000", "frame_0_1000",
}
_UNAVAILABLE_TRANSACTION_PRIMITIVES = {
    "key_down", "key_up",
}


class InteractionPlanOutputError(ValueError):
    """A model payload that deterministic normalization cannot make safe.

    A malformed semantic plan is not evidence that the pixels changed.  It may
    be corrected once against the *same* authoritative frame, but it must never
    trigger a mutation or masquerade as a reason to capture different pixels.
    """

    failure_type = "interaction_plan_invalid"

    def __init__(
        self,
        message: str,
        *,
        code: str = "schema_validation_failed",
        repairs: list[str] | tuple[str, ...] = (),
        retryable: bool = True,
        raw_payload: dict[str, Any] | None = None,
        validation_errors: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = str(code or "schema_validation_failed")
        self.repairs = tuple(str(item) for item in repairs if str(item))
        self.retryable = bool(retryable)
        self.raw_payload = dict(raw_payload) if isinstance(raw_payload, dict) else None
        self.validation_errors = tuple(
            dict(item) for item in validation_errors if isinstance(item, dict)
        )

    def attach_payload(self, payload: dict[str, Any] | None) -> None:
        """Retain model JSON for audit/correction without changing the error."""
        if self.raw_payload is None and isinstance(payload, dict):
            self.raw_payload = dict(payload)

    def correction_feedback(self, *, max_payload_chars: int = 6000) -> dict[str, Any]:
        """Return bounded, JSON-safe feedback for one same-frame correction."""
        payload_text = ""
        if self.raw_payload is not None:
            payload_text = json.dumps(
                self.raw_payload,
                ensure_ascii=False,
                default=str,
                separators=(",", ":"),
            )
            limit = max(256, int(max_payload_chars))
            if len(payload_text) > limit:
                payload_text = payload_text[:limit] + "...[truncated]"
        feedback = {
            "error_code": self.code,
            "message": str(self)[:2000],
            "validation_errors": [dict(item) for item in self.validation_errors],
            "canonicalization_repairs": list(self.repairs),
            "previous_payload_json": payload_text,
        }
        if self.code == "blocking_ambiguity":
            feedback["ambiguity_contract"] = {
                "ambiguity": (
                    "blocking semantic uncertainty only; an acting plan must "
                    "leave this empty"
                ),
                "resolution_requirements": [{
                    "kind": "spatial_geometry",
                    "resolver": "current_frame_geometry",
                    "action_index": 0,
                    "description": (
                        "use only when the selected target is semantically clear "
                        "but its exact executable boundary needs local pixel proof"
                    ),
                }],
                "fail_closed": (
                    "if the uncertainty is semantic, ownership-related, or target "
                    "identity-related, emit no action and return blocked"
                ),
            }
        return feedback


def _validation_error_details(exc: ValidationError) -> list[dict[str, Any]]:
    """Project Pydantic diagnostics into stable, JSON-safe trace records."""
    details: list[dict[str, Any]] = []
    for item in exc.errors(include_url=False, include_context=False, include_input=False):
        details.append({
            "location": [str(part) for part in item.get("loc", ())],
            "type": str(item.get("type") or "validation_error"),
            "message": str(item.get("msg") or "invalid value"),
        })
    return details


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


class InteractionStroke(BaseModel):
    """One pointer-down/move/pointer-up path with an explicit coordinate space."""

    model_config = ConfigDict(extra="forbid")

    coordinate_space: Literal[
        "target_region_0_1000", "frame_0_1000",
    ]
    points: list[list[float]] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_points(self) -> "InteractionStroke":
        for index, point in enumerate(self.points):
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError(f"points[{index}] must be [x,y]")
            _coord(point[0], f"points[{index}][0]")
            _coord(point[1], f"points[{index}][1]")
        return self


class InteractionPartitionLocator(BaseModel):
    """Semantic index inside a visibly repeated two-dimensional partition.

    Positive indices count from the top/left starting at one; negative indices
    count from the bottom/right.  The locator is semantic input only.  A local
    current-frame provider still has to prove one unique repeated divider
    family and the selected cell before any pointer input is authorized.
    """

    model_config = ConfigDict(extra="forbid")

    layout: Literal["repeated_partition"]
    row: int = Field(ge=-12, le=12)
    column: int = Field(ge=-12, le=12)

    @model_validator(mode="after")
    def validate_indices(self) -> "InteractionPartitionLocator":
        if self.row == 0 or self.column == 0:
            raise ValueError("partition row/column indices are one-based and cannot be zero")
        return self


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
    path_mode: Literal["auto", "direct", "guided"] = "auto"
    frame_strokes: list[list[list[float]]] = Field(default_factory=list)
    # Paths inside a visible bounded surface can be expressed in that
    # target_region's own 0..1000 space.  The Controller resolves them against
    # the current bound region immediately before input, so the planner does
    # not have to guess every sample in whole-frame coordinates.
    local_strokes: list[list[list[float]]] = Field(default_factory=list)
    # Canonical model-facing representation.  Each object is exactly one
    # physical pointer-down/up path, which avoids the ambiguous three-level
    # array nesting that providers commonly flatten.  The two legacy arrays
    # above remain accepted for persisted plans and compatibility APIs.
    stroke_paths: list[InteractionStroke] = Field(default_factory=list)
    # Semantic object/region grounding is deliberately separate from the
    # expected effect ROI.  These bounds describe where the source and target
    # visibly are in the authoritative frame; they are never physical pixels.
    source_region: list[float] = Field(default_factory=list)
    target_region: list[float] = Field(default_factory=list)
    # Optional references into the runtime-generated current-pixel inventory.
    # The ids are resolved before validation and remain in the trace so coarse
    # semantic coordinates are never mistaken for local geometry evidence.
    source_candidate_id: str = ""
    target_candidate_id: str = ""
    # Semantic point identifying the intended child when target_region is a
    # coarse container.  It is never clicked directly; local current-frame
    # geometry resolves the deepest unique bounded region at this point.
    target_anchor: list[float] = Field(default_factory=list)
    # Optional semantic relation for a cell/slot in current-frame repeated
    # rows and columns.  This survives poor whole-frame coordinate estimates;
    # it never replaces the local repeated-divider proof.
    partition_locator: InteractionPartitionLocator | None = None
    grounding_mode: Literal[
        "auto", "object_anchor", "semantic_control", "free_path",
        "relative_gesture", "bounded_coordinates",
    ] = "auto"
    # The physical primitive and the visible input mechanism are separate.
    # For example, a click can activate a control, select an item, or place a
    # value on an explicitly click-to-place surface; it cannot silently stand
    # in for freehand drawing.  ``auto`` is retained for persisted/programmatic
    # decisions, while model-authored shared-session plans are checked below.
    interaction_mechanism: InteractionMechanism = "auto"
    affordance_evidence: str = ""
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
        target_anchor: tuple[float, float] | None = None
        if self.target_anchor:
            if not isinstance(self.target_anchor, list) or len(self.target_anchor) != 2:
                raise ValueError("target_anchor must be [x,y]")
            target_anchor = (
                _coord(self.target_anchor[0], "target_anchor[0]"),
                _coord(self.target_anchor[1], "target_anchor[1]"),
            )
            if not target_bounds or not _point_in_region(target_anchor, target_bounds):
                raise ValueError("target_anchor must be inside target_region")
        if self.partition_locator is not None:
            if not target_bounds or target_anchor is None:
                raise ValueError(
                    "partition_locator requires a coarse target_region and target_anchor"
                )
            if self.action not in _SPATIAL_ACTIONS:
                raise ValueError("partition_locator is valid only for spatial actions")
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
            if self.path_mode == "auto":
                self.path_mode = "guided" if self.frame_points else "direct"
            if self.path_mode == "guided" and not self.frame_points:
                raise ValueError("guided drag requires intermediate frame_points")
            if self.path_mode == "direct" and self.frame_points:
                raise ValueError("direct drag cannot declare intermediate frame_points")
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
            representation_count = sum((
                bool(self.frame_strokes),
                bool(self.local_strokes),
                bool(self.stroke_paths),
            ))
            if representation_count != 1:
                raise ValueError(
                    "draw_strokes requires exactly one of frame_strokes, "
                    "local_strokes, or stroke_paths"
                )
            if not target_bounds:
                raise ValueError(
                    "interactive draw_strokes requires target_region so every "
                    "pointer sample is bounded by the visible legal surface"
                )
            if source_bounds:
                raise ValueError(
                    "draw_strokes is a free path and cannot declare a source_region"
                )
            if self.stroke_paths:
                stroke_items = [
                    (stroke.coordinate_space, stroke.points)
                    for stroke in self.stroke_paths
                ]
                field_name = "stroke_paths"
            else:
                coordinate_space = (
                    "frame_0_1000" if self.frame_strokes
                    else "target_region_0_1000"
                )
                stroke_items = [
                    (coordinate_space, stroke)
                    for stroke in (self.frame_strokes or self.local_strokes)
                ]
                field_name = (
                    "frame_strokes" if self.frame_strokes else "local_strokes"
                )
            for stroke_index, (coordinate_space, stroke) in enumerate(stroke_items):
                if not isinstance(stroke, list) or len(stroke) < 2:
                    raise ValueError(
                        f"{field_name}[{stroke_index}] requires at least two points"
                    )
                for point_index, point in enumerate(stroke):
                    if not isinstance(point, list) or len(point) != 2:
                        raise ValueError(
                            f"{field_name}[{stroke_index}][{point_index}] must be [x,y]"
                        )
                    _coord(point[0], "stroke x")
                    _coord(point[1], "stroke y")
                    if coordinate_space == "target_region_0_1000":
                        continue
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
            # Reject model paths whose footprint is too small to be a robust,
            # observable freehand action in the declared local space.  This is
            # deliberately shape- and application-agnostic: it prevents a
            # percent/coordinate misunderstanding from becoming a 4-8 pixel
            # physical scribble, while preserving long narrow strokes.
            all_points = [
                (float(point[0]), float(point[1]))
                for _coordinate_space, stroke in stroke_items
                for point in stroke
            ]
            x_span = max(point[0] for point in all_points) - min(
                point[0] for point in all_points
            )
            y_span = max(point[1] for point in all_points) - min(
                point[1] for point in all_points
            )
            if max(x_span, y_span) < 120.0:
                raise ValueError(
                    "draw_strokes footprint is below 12% of its coordinate "
                    "space; emit an observable path or use the correct primitive"
                )
            if len(stroke_items) == 1:
                _space, only_stroke = stroke_items[0]
                first = only_stroke[0]
                last = only_stroke[-1]
                closure_distance = math.hypot(
                    float(last[0]) - float(first[0]),
                    float(last[1]) - float(first[1]),
                )
                if closure_distance <= 40.0 and min(x_span, y_span) < 160.0:
                    raise ValueError(
                        "closed draw_strokes footprint is below 16% in one "
                        "dimension; emit a visibly verifiable local path"
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
        if self.action != "drag" and self.path_mode not in {"auto", "direct"}:
            raise ValueError("guided path_mode is valid only for drag")
        if (
            self.action != "drag"
            and self.placement_requirement != "point_inside_target"
        ):
            raise ValueError(
                "source_inside_target placement is valid only for drag"
            )
        if self.action != "draw_strokes" and self.local_strokes:
            raise ValueError("local_strokes are valid only for draw_strokes")
        if self.action != "draw_strokes" and self.frame_strokes:
            raise ValueError("frame_strokes are valid only for draw_strokes")
        if self.action != "draw_strokes" and self.stroke_paths:
            raise ValueError("stroke_paths are valid only for draw_strokes")
        compatible_mechanisms = {
            "click": {"control_activation", "point_selection", "click_to_place"},
            "double_click": {"control_activation", "point_selection"},
            "hover": {"hover_reveal"},
            "move_mouse": {"pointer_motion"},
            "mouse_down": {"press_hold"},
            "mouse_up": {"press_hold"},
            "drag": {"direct_manipulation"},
            "draw_strokes": {"freehand_strokes"},
            "scroll": {"scroll_navigation"},
            "select_color": {"control_activation", "point_selection"},
            "type_text": {"text_entry"},
            "hotkey": {"keyboard_command"},
            "hold_key": {"keyboard_command", "press_hold"},
            "wait_stable": {"timed_wait"},
        }
        allowed_mechanisms = compatible_mechanisms.get(self.action, set())
        if (
            self.interaction_mechanism != "auto"
            and self.interaction_mechanism not in allowed_mechanisms
        ):
            raise ValueError(
                f"{self.action} is incompatible with interaction_mechanism="
                f"{self.interaction_mechanism}"
            )
        if self.button not in _MOUSE_BUTTONS:
            raise ValueError("unsupported mouse button")
        return self

    def resolved_frame_strokes(
        self,
        *,
        inset_fraction: float = 0.0,
    ) -> list[list[list[float]]]:
        """Resolve a draw path into authoritative-frame coordinates.

        A small Controller-owned inset can keep the pointer or brush footprint
        away from a legal-region border.  This is generic geometry: it has no
        application, game, shape, colour, or screen-coordinate knowledge.
        """
        if self.action != "draw_strokes":
            return []
        if self.frame_strokes:
            return [
                [[float(point[0]), float(point[1])] for point in stroke]
                for stroke in self.frame_strokes
            ]
        if self.stroke_paths:
            resolved: list[list[list[float]]] = []
            for stroke in self.stroke_paths:
                if stroke.coordinate_space == "frame_0_1000":
                    resolved.append([
                        [float(point[0]), float(point[1])]
                        for point in stroke.points
                    ])
                    continue
                if len(self.target_region) != 4:
                    return []
                left, top, right, bottom = _region(
                    self.target_region, "target_region",
                )
                inset = min(0.20, max(0.0, float(inset_fraction)))
                width = right - left
                height = bottom - top
                inner_left = left + width * inset
                inner_top = top + height * inset
                inner_width = width * (1.0 - 2.0 * inset)
                inner_height = height * (1.0 - 2.0 * inset)
                resolved.append([
                    [
                        inner_left + float(point[0]) / 1000.0 * inner_width,
                        inner_top + float(point[1]) / 1000.0 * inner_height,
                    ]
                    for point in stroke.points
                ])
            return resolved
        if not self.local_strokes or len(self.target_region) != 4:
            return []
        left, top, right, bottom = _region(self.target_region, "target_region")
        inset = min(0.20, max(0.0, float(inset_fraction)))
        width = right - left
        height = bottom - top
        inner_left = left + width * inset
        inner_top = top + height * inset
        inner_width = width * (1.0 - 2.0 * inset)
        inner_height = height * (1.0 - 2.0 * inset)
        return [
            [
                [
                    inner_left + float(point[0]) / 1000.0 * inner_width,
                    inner_top + float(point[1]) / 1000.0 * inner_height,
                ]
                for point in stroke
            ]
            for stroke in self.local_strokes
        ]

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
                for stroke in self.resolved_frame_strokes()
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


class InteractionResolutionRequirement(BaseModel):
    """A model-declared uncertainty that a deterministic provider must close.

    ``ambiguity`` remains blocking semantic uncertainty.  This separate typed
    contract lets the planner admit that its coarse spatial estimate is not
    execution evidence without disabling an otherwise valid turn.  The
    StageRuntime must attach the named provider's current-frame proof before
    any input is dispatched.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["spatial_geometry"]
    resolver: Literal["current_frame_geometry"]
    action_index: int = Field(ge=0, le=11)
    description: str = Field(min_length=1, max_length=500)


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
    # Legacy/free-form ambiguity is deliberately blocking.  Locally resolvable
    # uncertainty must use the typed requirements below so execution can prove
    # that the designated provider actually closed it.
    ambiguity: list[str] = Field(default_factory=list)
    resolution_requirements: list[InteractionResolutionRequirement] = Field(
        default_factory=list,
        max_length=12,
    )
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
    _deferred_actions: tuple[InteractionAction, ...] = PrivateAttr(default=())
    _program_continuation: str = PrivateAttr(default="finish")
    _region_inventory: tuple[dict[str, Any], ...] = PrivateAttr(default=())

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

        for requirement in self.resolution_requirements:
            index = int(requirement.action_index)
            if index >= len(self.actions):
                raise ValueError(
                    "resolution requirement references a missing action"
                )
            action = self.actions[index]
            if requirement.kind == "spatial_geometry":
                resolvable = bool(
                    (
                        action.action == "draw_strokes"
                        and action.target_region
                        and (action.target_anchor or action.partition_locator)
                    )
                    or (
                        action.action == "drag"
                        and action.source_region
                        and action.target_region
                    )
                    or (
                        action.action in _POINT_ACTIONS
                        and action.grounding_mode in {
                            "object_anchor", "semantic_control",
                        }
                        and (action.source_region or action.target_region)
                    )
                )
                if not resolvable:
                    raise ValueError(
                        "current_frame_geometry requirement needs a typed "
                        "spatial action with explicit local grounding"
                    )

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

    @property
    def deferred_actions(self) -> tuple[InteractionAction, ...]:
        return self._deferred_actions

    @property
    def program_continuation(self) -> str:
        return self._program_continuation

    @property
    def region_inventory(self) -> tuple[dict[str, Any], ...]:
        return self._region_inventory

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
    validation_feedback: dict[str, Any] | None = None,
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
        "ambiguity": [
            "blocking semantic uncertainty only; must be empty when acting"
        ],
        "resolution_requirements": [{
            "kind": "spatial_geometry",
            "resolver": "current_frame_geometry",
            "action_index": 0,
            "description": (
                "selected target identity is clear but exact executable geometry "
                "must be proven from the current frame"
            ),
        }],
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
            "frame_points": (
                "ordered intermediate [x,y] waypoints for drag; required at "
                "every bend when the direct segment is not visibly legal"
            ),
            "path_mode": "direct|guided for drag",
            "stroke_paths": [{
                "coordinate_space": "target_region_0_1000|frame_0_1000",
                "points": [[150, 150], [850, 850]],
            }],
            "source_region": ["left", "top", "right", "bottom"],
            "target_region": ["left", "top", "right", "bottom"],
            "source_candidate_id": "current-frame inventory id when matched",
            "target_candidate_id": "current-frame inventory id when matched",
            "target_anchor": ["semantic target center x", "semantic target center y"],
            "partition_locator": {
                "layout": "repeated_partition",
                "row": "nonzero 1-based index; negative counts from bottom",
                "column": "nonzero 1-based index; negative counts from right",
            },
            "grounding_mode": (
                "object_anchor|semantic_control|free_path|relative_gesture|"
                "bounded_coordinates"
            ),
            "interaction_mechanism": (
                "control_activation|point_selection|click_to_place|"
                "freehand_strokes|direct_manipulation|text_entry|"
                "keyboard_command|hover_reveal|scroll_navigation|"
                "pointer_motion|press_hold|timed_wait"
            ),
            "affordance_evidence": (
                "concrete current-frame evidence for this input mechanism"
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
            "requires_fresh_frame_after": (
                "boolean; false on every non-final action in a safe stable batch, "
                "true when later geometry must be re-observed"
            ),
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
    has_region_inventory = bool(prior.get("current_frame_region_inventory"))
    if not has_region_inventory:
        contract["actions"][0].pop("source_candidate_id", None)
        contract["actions"][0].pop("target_candidate_id", None)
    inventory_guidance = (
        "current_frame_region_inventory is pixel geometry, not semantic truth. "
        "Select a visible source/target by copying its candidate_id; the runtime "
        "binds its bounds, safe anchor, and any partition_locator. Use peer ranks "
        "only to express the choice you made from the image. Never invent an id. "
        if has_region_inventory
        else ""
    )
    appearance_guidance = (
        "current_frame_appearance_inventory reports current-pixel colours and "
        "topology without assigning ownership. Respect immutable_role_colour_matches; "
        "use a component only when its position also fits the visible shared state. "
        if prior.get("current_frame_appearance_inventory")
        else ""
    )
    correction_guidance = ""
    if validation_feedback:
        correction_json = json.dumps(
            validation_feedback,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        )
        if len(correction_json) > 8000:
            correction_json = correction_json[:8000] + "...[truncated]"
        correction_guidance = (
            "\nSCHEMA CORRECTION FOR THIS SAME AUTHORITATIVE FRAME: The previous "
            "response below was rejected before any input occurred. Return one complete "
            "replacement JSON object, preserving the task meaning, selected visible target, "
            "roles, and geometry unless a listed validation error requires a correction. "
            "Fix every listed structural error; do not wrap the JSON, explain it, emit a "
            "patch, or add unknown fields. Previous rejection: "
            + correction_json
        )
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
        "action with its complete path. Before emitting a drag, inspect the entire source-to-target "
        "route, not only its endpoints. If a direct segment would leave a visible track/corridor, "
        "cross an obstacle, or violate source-footprint clearance, frame_points must trace the "
        "visible legal route in order with a waypoint at every bend and enough clearance for the "
        "whole source and path_mode must be guided; ordinary unobstructed drags use path_mode "
        "direct. Never interpolate directly through an obstacle; return blocked when no "
        "complete legal route can be grounded. Use double_click only when two clicks are semantically "
        "required, hover when dwell may reveal a state, and move_mouse for cursor placement. "
        "Choose the primitive from the visible input affordance, not merely from the desired outcome: "
        "click activates an existing visible control or an explicitly click-to-place surface; it does "
        "not itself render a line, symbol, signature, or other multi-segment mark. When this actor must "
        "create a visible mark and the current surface or existing marks show freehand/stroke input, use "
        "draw_strokes with free_path grounding. Its target_region must be the exact writable bounded "
        "region for that mark, and stroke_paths must express the mark relative to that region. A blank "
        "writable region is not an object anchor: do not give it source_region or object_anchor merely "
        "because it is where the effect should appear. "
        "For draw_strokes, target_anchor is required and must identify the intended writable child "
        "inside target_region. If target_region is only a coarse board, table, panel, or grouped "
        "container, target_anchor must be the center of the one selected child; local vision will bind "
        "its exact border. Never use a whole container as the execution region without this anchor. "
        "When the selected target is a cell or slot in visibly repeated rows and columns, also set "
        "partition_locator with layout=repeated_partition. row and column are one-based: positive "
        "values count from the top/left and negative values count from the bottom/right. For example, "
        "the upper-left repeated partition is row=1,column=1 and the upper-right is row=1,column=-1. "
        "Use this only when the current pixels visibly support a repeated two-axis partition; do not "
        "invent rows or columns for a free canvas or unrelated controls. Numeric target_region and "
        "target_anchor remain coarse search hints, while the typed locator preserves the semantic "
        "choice and the runtime must still prove a unique current-pixel divider family. "
        "Every model-authored action must declare interaction_mechanism and concrete "
        "affordance_evidence from this exact frame. Do not use application/game convention as "
        "evidence. control_activation requires a visible control, point_selection a visible "
        "selectable item, and click_to_place an explicit visible click-to-place instruction or "
        "state. A writable surface or existing freehand marks require freehand_strokes. "
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
        "anchor and is still protected by frame lineage and ROI bounds. Interactive draw_strokes must declare target_region "
        "and use free_path. Model output must use stroke_paths. Each stroke_paths object is exactly one "
        "pointer-down/move/pointer-up path; its points field is one ordered list of [x,y] pairs. Two "
        "separate pen strokes require two separate objects in the outer list--never flatten strokes into "
        "one points list and never add a connector between them. Use coordinate_space="
        "target_region_0_1000 for paths contained by a visible cell, canvas, track, signature box, or "
        "other bounded surface. 0..1000 is not a percentage: 150 means 15% and 850 means 85%; "
        "values 10..90 mean only 1%..9%, not 10%..90%. Use frame_0_1000 only when a path "
        "genuinely spans whole-frame geometry; "
        "those points must remain inside target_region. local_strokes and frame_strokes are legacy "
        "compatibility fields and must not be emitted by the model. Scroll uses "
        "relative_gesture bound to the current ROI. Prefer one drag action "
        "for ordinary dragging. Use a same-transaction mouse_down, one or more move_mouse, and "
        "mouse_up sequence only when the interface genuinely requires a custom press/hold path; "
        "every point still needs current-frame grounding. Use hold_key only for a bounded duration. "
        "Do not emit standalone key_down or key_up: those are not executable transaction "
        "capabilities, and uncertainty must fail closed rather than inventing an equivalent action. "
        "Use ambiguity only for blocking semantic uncertainty such as actor ownership, target "
        "identity, rule meaning, or whether an action is legal; an acting response must leave it "
        "empty. If the semantic target is clear but its coarse coordinates or boundary are not "
        "precise enough for input, declare a spatial_geometry resolution_requirement for that "
        "action with resolver=current_frame_geometry. This does not authorize input by itself: "
        "the runtime will fail closed unless a unique current-pixel provider proves it. "
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
        "plan only the first and require a fresh frame. Prefer the longest safe local transaction: "
        "a complete ordered series of simultaneously visible bounded targets is one semantic "
        "turn, not several turns, so at-most-one-turn never means at-most-one-action. "
        "when several requested targets are simultaneously visible, spatially disjoint, and each "
        "later target keeps the same identity and geometry even if an earlier target changes only "
        "its own local appearance, emit all of those actions in order and set "
        "requires_fresh_frame_after=false on every non-final action. A local pressed/selected/color "
        "change on an already-used target does not by itself invalidate a different bound target. "
        "Do not batch when a later target is revealed, moved, enabled, relabelled, occluded, or "
        "otherwise depends on an earlier result. Do not infer success from UI chrome "
        "or unrelated desktop changes. "
        f"{inventory_guidance}"
        f"{appearance_guidance}"
        "Prior state may also include a CV-detected surface and the "
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
        f"{correction_guidance}"
    )


def _mapping_region(value: Any) -> Any:
    """Accept unambiguous provider spellings without weakening region checks.

    Some vision providers serialize one rectangle as ``[[l,t,r,b]]`` even
    when the requested schema is a flat four-number array.  Unwrapping exactly
    one numeric rectangle preserves the same geometry; larger/nested
    collections remain invalid so this never guesses which region was meant.
    """
    if isinstance(value, dict) and all(
        key in value for key in ("left", "top", "right", "bottom")
    ):
        return [value["left"], value["top"], value["right"], value["bottom"]]
    if (
        isinstance(value, (list, tuple))
        and len(value) == 1
        and isinstance(value[0], (list, tuple))
        and len(value[0]) == 4
        and all(
            isinstance(item, (int, float))
            and not isinstance(item, bool)
            and math.isfinite(float(item))
            for item in value[0]
        )
    ):
        return list(value[0])
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


def _redundant_direct_drag_points(action: dict[str, Any]) -> bool:
    """Return whether provider points only restate one straight drag.

    Some structured-output providers emit the start and end again in
    ``frame_points`` even when they correctly classify an unobstructed drag as
    direct.  Treating those duplicate samples as a constrained route invokes a
    corridor proof that can never be satisfied on an open surface.  Only
    monotonic, near-collinear points are removable; a real bend remains guided.
    """
    points = action.get("frame_points")
    if not isinstance(points, list) or not points:
        return False
    try:
        start = (float(action["frame_x1"]), float(action["frame_y1"]))
        end = (float(action["frame_x2"]), float(action["frame_y2"]))
        samples = [
            (float(point[0]), float(point[1]))
            for point in points
            if isinstance(point, list) and len(point) == 2
        ]
    except (KeyError, TypeError, ValueError):
        return False
    if len(samples) != len(points):
        return False
    delta_x = end[0] - start[0]
    delta_y = end[1] - start[1]
    length_squared = delta_x * delta_x + delta_y * delta_y
    if length_squared < 64.0:
        return False
    length = math.sqrt(length_squared)
    tolerance = max(6.0, length * 0.025)
    previous_projection = -0.04
    for point_x, point_y in samples:
        projection = (
            (point_x - start[0]) * delta_x
            + (point_y - start[1]) * delta_y
        ) / length_squared
        perpendicular = abs(
            (point_x - start[0]) * delta_y
            - (point_y - start[1]) * delta_x
        ) / length
        if (
            projection < -0.04
            or projection > 1.04
            or projection + 0.04 < previous_projection
            or perpendicular > tolerance
        ):
            return False
        previous_projection = max(previous_projection, projection)
    return True


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
    if "blocked_reason" in normalized and normalized.get("blocked_reason") is None:
        normalized["blocked_reason"] = ""
        repairs.append("blocked_reason:null->empty")

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
            if "local_strokes" not in action and "target_relative_strokes" in action:
                action["local_strokes"] = action.pop("target_relative_strokes")
                repairs.append(
                    f"actions[{index}].target_relative_strokes->local_strokes"
                )
            if "local_strokes" not in action and "localStrokes" in action:
                action["local_strokes"] = action.pop("localStrokes")
                repairs.append(f"actions[{index}].localStrokes->local_strokes")
            if "stroke_paths" not in action and "strokePaths" in action:
                action["stroke_paths"] = action.pop("strokePaths")
                repairs.append(f"actions[{index}].strokePaths->stroke_paths")
            if "target_anchor" not in action and "targetAnchor" in action:
                action["target_anchor"] = action.pop("targetAnchor")
                repairs.append(f"actions[{index}].targetAnchor->target_anchor")
            if "partition_locator" not in action and "partitionLocator" in action:
                action["partition_locator"] = action.pop("partitionLocator")
                repairs.append(f"actions[{index}].partitionLocator->partition_locator")
            if "path_mode" not in action and "pathMode" in action:
                action["path_mode"] = action.pop("pathMode")
                repairs.append(f"actions[{index}].pathMode->path_mode")
            if (
                "interaction_mechanism" not in action
                and "interactionMechanism" in action
            ):
                action["interaction_mechanism"] = action.pop(
                    "interactionMechanism"
                )
                repairs.append(
                    f"actions[{index}].interactionMechanism->interaction_mechanism"
                )
            if "affordance_evidence" not in action and "affordanceEvidence" in action:
                action["affordance_evidence"] = action.pop("affordanceEvidence")
                repairs.append(
                    f"actions[{index}].affordanceEvidence->affordance_evidence"
                )
            action_name = str(action.get("action") or "")
            canonical_name = action_name.strip().casefold().replace("-", "_").replace(" ", "_")
            if canonical_name in known_actions and canonical_name != action_name:
                action["action"] = canonical_name
                repairs.append(f"actions[{index}].action:normalized")
            if (
                canonical_name == "draw_strokes"
                and "coordinate_space" not in action
                and "coordinateSpace" in action
            ):
                action["coordinate_space"] = action.pop("coordinateSpace")
                repairs.append(
                    f"actions[{index}].coordinateSpace->coordinate_space"
                )
            # Some providers hoist a property shared by every stroke onto the
            # action object.  Moving it back down is safe only when the value is
            # a declared coordinate space, every path is structurally present,
            # and no child declares a conflicting space.  Unknown values,
            # non-draw actions, empty paths, and conflicts remain untouched so
            # Pydantic rejects them before any Controller mutation.
            action_coordinate_space = action.get("coordinate_space")
            action_stroke_paths = action.get("stroke_paths")
            if (
                canonical_name == "draw_strokes"
                and action_coordinate_space in _STROKE_COORDINATE_SPACES
                and isinstance(action_stroke_paths, list)
                and action_stroke_paths
                and all(isinstance(stroke, dict) for stroke in action_stroke_paths)
                and all(
                    stroke.get("coordinate_space") in {
                        None, "", action_coordinate_space,
                    }
                    for stroke in action_stroke_paths
                )
            ):
                action["stroke_paths"] = [
                    {
                        **stroke,
                        "coordinate_space": (
                            stroke.get("coordinate_space")
                            or action_coordinate_space
                        ),
                    }
                    for stroke in action_stroke_paths
                ]
                action.pop("coordinate_space", None)
                repairs.append(
                    f"actions[{index}].coordinate_space:moved_to_stroke_paths"
                )
            if canonical_name == "draw_strokes" and action.get("source_region"):
                # A free path has no pickup object.  Some providers duplicate
                # target_region into source_region even after selecting the
                # correct primitive.  Removing that unused contradiction does
                # not invent geometry or alter the pointer path.
                action["source_region"] = []
                repairs.append(
                    f"actions[{index}].source_region:removed_for_free_path"
                )
            if (
                canonical_name == "drag"
                and action.get("path_mode") == "direct"
                and isinstance(action.get("frame_points"), list)
                and action.get("frame_points")
            ):
                if _redundant_direct_drag_points(action):
                    action["frame_points"] = []
                    repairs.append(
                        f"actions[{index}].frame_points:removed_redundant_direct_path"
                    )
                else:
                    # A genuine intermediate bend is an explicit routed path.
                    # Calling that same path "direct" is provider label drift,
                    # so retain every point and correct only the enum.
                    action["path_mode"] = "guided"
                    repairs.append(
                        f"actions[{index}].path_mode:direct->guided_from_points"
                    )
            stroke_paths = action.get("stroke_paths")
            if canonical_name == "draw_strokes" and isinstance(stroke_paths, list):
                target_relative_points: list[list[float]] = []
                structurally_valid = True
                for stroke in stroke_paths:
                    if not isinstance(stroke, dict):
                        structurally_valid = False
                        break
                    if stroke.get("coordinate_space") != "target_region_0_1000":
                        structurally_valid = False
                        break
                    points = stroke.get("points")
                    if not isinstance(points, list):
                        structurally_valid = False
                        break
                    for point in points:
                        if not (
                            isinstance(point, list)
                            and len(point) == 2
                            and all(
                                isinstance(value, (int, float))
                                and not isinstance(value, bool)
                                and math.isfinite(float(value))
                                for value in point
                            )
                        ):
                            structurally_valid = False
                            break
                        target_relative_points.append(point)
                    if not structurally_valid:
                        break
                values = [
                    float(value)
                    for point in target_relative_points
                    for value in point
                ]
                # Repair only the strong whole-plan signature of a provider
                # that used percent-like values after explicitly declaring the
                # 0..1000 target coordinate space.  Ambiguous partial ranges
                # remain untouched and are judged at their literal scale.
                if (
                    structurally_valid
                    and values
                    and min(values) >= 0.0
                    and max(values) <= 100.0
                    and min(values) <= 25.0
                    and max(values) >= 75.0
                    and max(values) - min(values) >= 50.0
                ):
                    action["stroke_paths"] = [
                        {
                            **stroke,
                            "points": [
                                [float(point[0]) * 10.0, float(point[1]) * 10.0]
                                for point in stroke["points"]
                            ],
                        }
                        for stroke in stroke_paths
                    ]
                    repairs.append(
                        f"actions[{index}].stroke_paths:percent_like->0_1000"
                    )
            for key in (
                "frame_points", "frame_strokes", "local_strokes", "stroke_paths", "source_region",
                "target_region", "target_anchor", "expected_effect_region", "keys",
            ):
                if action.get(key) is None:
                    action[key] = []
                    repairs.append(f"actions[{index}].{key}:null->empty")
            for key in ("source_candidate_id", "target_candidate_id"):
                if action.get(key) is None:
                    action[key] = ""
                    repairs.append(f"actions[{index}].{key}:null->empty")
            for key in (
                "affordance_evidence", "color", "text", "direction", "rationale",
            ):
                if action.get(key) is None:
                    action[key] = ""
                    repairs.append(f"actions[{index}].{key}:null->empty")
            for key in ("source_region", "target_region"):
                mapped = _mapping_region(action.get(key))
                if mapped is not action.get(key):
                    action[key] = mapped
                    repairs.append(f"actions[{index}].{key}:shape->flat_region")
            effect_region = _optional_effect_region(action.get("expected_effect_region"))
            if effect_region != action.get("expected_effect_region"):
                action["expected_effect_region"] = effect_region
                repairs.append(
                    f"actions[{index}].expected_effect_region:shape->flat_region"
                )
            if action.get("effect_region_binding") is None:
                action.pop("effect_region_binding", None)
                repairs.append(f"actions[{index}].effect_region_binding:null->auto")
            if action.get("grounding_mode") is None:
                action.pop("grounding_mode", None)
                repairs.append(f"actions[{index}].grounding_mode:null->auto")
            fixed_actions.append(action)
        # A provider may omit the dependency flag while still returning an
        # explicit ordered spatial batch.  The ordered list itself asserts
        # that those later targets belong to this same authoritative frame;
        # normalize only omitted non-final flags.  An explicit ``true`` is a
        # semantic dependency and remains fail-closed in InteractionDecision.
        # This is provider-schema normalization, not a target-specific retry.
        spatial_indices = [
            index
            for index, item in enumerate(fixed_actions)
            if isinstance(item, dict)
            and str(item.get("action") or "") in _SPATIAL_ACTIONS
        ]
        spatial_names = [
            str(fixed_actions[index].get("action") or "")
            for index in spatial_indices
        ]
        continuous_pointer_transaction = bool(
            len(spatial_names) >= 3
            and spatial_names[0] == "mouse_down"
            and spatial_names[-1] == "mouse_up"
            and all(name == "move_mouse" for name in spatial_names[1:-1])
        )
        if len(spatial_indices) > 1 and not continuous_pointer_transaction:
            for index in spatial_indices[:-1]:
                item = fixed_actions[index]
                if "requires_fresh_frame_after" not in item:
                    item["requires_fresh_frame_after"] = False
                    repairs.append(
                        f"actions[{index}].requires_fresh_frame_after:"
                        "derived_stable_batch"
                    )
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

    for key in ("roles", "rules", "ambiguity", "resolution_requirements"):
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
            repairs.append("expected_effect.region:shape->flat_region")
        effect["region"] = effect_region
        if effect.get("region_binding") is None:
            effect.pop("region_binding", None)
            repairs.append("expected_effect.region_binding:null->auto")
        if effect.get("binding_action_index") is None:
            effect.pop("binding_action_index", None)
            repairs.append("expected_effect.binding_action_index:null->default")
        if isinstance(actions, list) and not actions:
            if effect.get("region_binding") in {
                "target_region", "source_region",
            }:
                effect["region_binding"] = "independent_region"
                repairs.append(
                    "expected_effect.region_binding:unbound_without_actions"
                )
            if effect.get("binding_action_index", -1) not in {None, -1}:
                effect["binding_action_index"] = -1
                repairs.append(
                    "expected_effect.binding_action_index:unbound_without_actions"
                )
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

    # Compile an internally contradictory multi-action proposal into the
    # longest safe executable prefix. A model can identify several requested
    # actions yet explicitly state that a fresh authoritative frame is needed
    # after an earlier one. Rejecting the whole plan loses valid progress;
    # executing past that boundary is unsafe. The stage graph already owns
    # re-observation, so retain actions through the first dependency and make
    # the continuation explicit. Balanced press/move/release gestures remain
    # indivisible and are validated separately by InteractionDecision.
    if isinstance(actions, list):
        spatial_indices = [
            index
            for index, item in enumerate(actions)
            if isinstance(item, dict)
            and str(item.get("action") or "") in _SPATIAL_ACTIONS
        ]
        spatial_names = [
            str(actions[index].get("action") or "")
            for index in spatial_indices
        ]
        continuous_pointer_transaction = bool(
            len(spatial_names) >= 3
            and spatial_names[0] == "mouse_down"
            and spatial_names[-1] == "mouse_up"
            and all(name == "move_mouse" for name in spatial_names[1:-1])
        )
        dependency_index = next(
            (
                index
                for index in spatial_indices[:-1]
                if actions[index].get("requires_fresh_frame_after") is True
            ),
            None,
        )
        if dependency_index is not None and not continuous_pointer_transaction:
            deferred_actions = actions[dependency_index + 1:]
            program_continuation = str(
                normalized.get("continuation") or "reobserve"
            ).strip().casefold()
            actions = actions[:dependency_index + 1]
            normalized["actions"] = actions
            normalized["_runtime_deferred_actions"] = deferred_actions
            normalized["_runtime_program_continuation"] = program_continuation
            normalized["continuation"] = "reobserve"
            normalized["blocked_reason"] = ""
            retained_regions: list[tuple[float, float, float, float]] = []
            for item in actions:
                if not isinstance(item, dict):
                    continue
                try:
                    retained_regions.append(_region(
                        item.get("expected_effect_region"),
                        "expected_effect_region",
                    ))
                except (TypeError, ValueError):
                    continue
            retained_effect = dict(normalized.get("expected_effect") or {})
            retained_effect.update({
                "description": (
                    "the retained stable action prefix produces its declared "
                    "visible effects before a fresh frame is required"
                ),
                "region_binding": "independent_region",
                "binding_action_index": -1,
            })
            if retained_regions:
                retained_effect["region"] = [
                    min(region[0] for region in retained_regions),
                    min(region[1] for region in retained_regions),
                    max(region[2] for region in retained_regions),
                    max(region[3] for region in retained_regions),
                ]
            normalized["expected_effect"] = retained_effect
            repairs.append(
                "actions:truncated_at_explicit_fresh_frame_dependency"
            )

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


def validate_model_action_affordances(
    decision: InteractionDecision,
    intent: dict[str, Any],
) -> None:
    """Require an explicit visible mechanism for inferred shared-session input.

    An explicit task contract such as "click the button" already fixes the
    modality.  A context-only handoff such as "your turn" does not: the model
    must distinguish a writable surface from a click-to-place control using
    the authoritative frame.  Keeping this check at the model boundary avoids
    weakening programmatic/persisted action compatibility.
    """
    mode = str(intent.get("mode") or "").strip().casefold()
    if mode not in {
        "shared_visual_session", "turn_based", "continuous_cooperation",
    } or not decision.agent_should_act:
        return
    for index, action in enumerate(decision.actions):
        if action.interaction_mechanism == "auto":
            raise InteractionPlanOutputError(
                f"shared-session action {index} omitted the visible input mechanism",
                code="interaction_mechanism_missing",
                repairs=decision.canonicalization_repairs,
            )
        if not action.affordance_evidence.strip():
            raise InteractionPlanOutputError(
                f"shared-session action {index} omitted current-frame affordance evidence",
                code="affordance_evidence_missing",
                repairs=decision.canonicalization_repairs,
            )
        if action.action == "draw_strokes" and len(action.target_anchor) != 2:
            raise InteractionPlanOutputError(
                f"shared-session draw action {index} omitted its semantic target anchor",
                code="target_anchor_missing",
                repairs=decision.canonicalization_repairs,
            )


def _bind_visible_region_candidates(
    payload: dict[str, Any],
    inventory: list[dict[str, Any]],
    *,
    repairs: list[str],
) -> dict[str, Any]:
    """Resolve model-selected ids against the immutable current-frame list."""
    if not inventory:
        for actions in (
            payload.get("actions"), payload.get("_runtime_deferred_actions"),
        ):
            for action in actions if isinstance(actions, list) else []:
                if not isinstance(action, dict):
                    continue
                if any(
                    str(action.get(key) or "").strip()
                    for key in ("source_candidate_id", "target_candidate_id")
                ):
                    raise InteractionPlanOutputError(
                        "visual model selected a region candidate when the "
                        "current stage exposed no region inventory",
                        code="interaction_region_candidate_unavailable",
                        repairs=repairs,
                    )
        return payload
    by_id = {
        str(item.get("candidate_id") or ""): item
        for item in inventory
        if str(item.get("candidate_id") or "")
    }

    def same_region(first: Any, second: Any) -> bool:
        return bool(
            isinstance(first, list)
            and isinstance(second, list)
            and len(first) == len(second) == 4
            and all(
                abs(float(left) - float(right)) <= 1e-6
                for left, right in zip(first, second)
            )
        )

    def inside(point: Any, bounds: list[float]) -> bool:
        return bool(
            isinstance(point, list)
            and len(point) == 2
            and bounds[0] <= float(point[0]) <= bounds[2]
            and bounds[1] <= float(point[1]) <= bounds[3]
        )

    def bind_action(action: dict[str, Any], label: str) -> None:
        original_source = list(action.get("source_region") or [])
        original_target = list(action.get("target_region") or [])
        selected: dict[str, list[float]] = {}
        for role in ("source", "target"):
            candidate_id = str(
                action.get(f"{role}_candidate_id") or ""
            ).strip()
            if not candidate_id:
                continue
            candidate = by_id.get(candidate_id)
            if candidate is None:
                raise InteractionPlanOutputError(
                    f"{label} selected unknown current-frame region "
                    f"{candidate_id!r}",
                    code="interaction_region_candidate_unknown",
                    repairs=repairs,
                )
            bounds = [float(value) for value in candidate.get("bounds") or []]
            if len(bounds) != 4:
                raise InteractionPlanOutputError(
                    f"{label} selected malformed current-frame region "
                    f"{candidate_id!r}",
                    code="interaction_region_candidate_invalid",
                    repairs=repairs,
                )
            action[f"{role}_region"] = bounds
            selected[role] = bounds
            repairs.append(
                f"{label}.{role}_region:bound->{candidate_id}"
            )

        target = selected.get("target")
        source = selected.get("source")
        if target is not None:
            target_candidate = by_id.get(str(
                action.get("target_candidate_id") or ""
            ).strip())
            candidate_locator = (
                target_candidate.get("partition_locator")
                if isinstance(target_candidate, dict)
                else None
            )
            if isinstance(candidate_locator, dict):
                existing_locator = action.get("partition_locator")
                if existing_locator and existing_locator != candidate_locator:
                    raise InteractionPlanOutputError(
                        f"{label} partition locator contradicts selected "
                        "current-frame candidate",
                        code="interaction_partition_candidate_conflict",
                        repairs=repairs,
                    )
                action["partition_locator"] = dict(candidate_locator)
                repairs.append(f"{label}.partition_locator:candidate_bound")
            center = [
                (target[0] + target[2]) / 2.0,
                (target[1] + target[3]) / 2.0,
            ]
            if not inside(action.get("target_anchor"), target):
                action["target_anchor"] = center
                repairs.append(f"{label}.target_anchor:candidate_center")
            if str(action.get("action") or "") in _POINT_ACTIONS:
                point = [action.get("frame_x"), action.get("frame_y")]
                if not inside(point, target):
                    action["frame_x"], action["frame_y"] = center
                    repairs.append(f"{label}.point:candidate_center")
            if str(action.get("action") or "") == "drag":
                endpoint = [action.get("frame_x2"), action.get("frame_y2")]
                if not inside(endpoint, target):
                    action["frame_x2"], action["frame_y2"] = center
                    repairs.append(f"{label}.drag_end:candidate_center")
            if same_region(original_source, original_target):
                action["source_region"] = list(target)
        if source is not None and str(action.get("action") or "") == "drag":
            center = [
                (source[0] + source[2]) / 2.0,
                (source[1] + source[3]) / 2.0,
            ]
            start = [action.get("frame_x1"), action.get("frame_y1")]
            if not inside(start, source):
                action["frame_x1"], action["frame_y1"] = center
                repairs.append(f"{label}.drag_start:candidate_center")
        binding = str(action.get("effect_region_binding") or "")
        if binding == "target_region" and action.get("target_region"):
            action["expected_effect_region"] = list(action["target_region"])
        elif binding == "source_region" and action.get("source_region"):
            action["expected_effect_region"] = list(action["source_region"])

    action_lists = [payload.get("actions")]
    deferred = payload.get("_runtime_deferred_actions")
    if isinstance(deferred, list):
        action_lists.append(deferred)
    for list_index, actions in enumerate(action_lists):
        if not isinstance(actions, list):
            continue
        prefix = "actions" if list_index == 0 else "deferred_actions"
        for index, action in enumerate(actions):
            if isinstance(action, dict):
                bind_action(action, f"{prefix}[{index}]")

    actions = payload.get("actions")
    effect = payload.get("expected_effect")
    if isinstance(actions, list) and isinstance(effect, dict):
        index = int(effect.get("binding_action_index", -1) or -1)
        if index < 0 and len(actions) == 1:
            index = 0
        if 0 <= index < len(actions) and isinstance(actions[index], dict):
            binding = str(effect.get("region_binding") or "")
            if binding == "target_region" and actions[index].get("target_region"):
                effect["region"] = list(actions[index]["target_region"])
            elif binding == "source_region" and actions[index].get("source_region"):
                effect["region"] = list(actions[index]["source_region"])
    return payload


def _merge_current_region_inventories(
    bounded: list[dict[str, Any]],
    partitions: list[dict[str, Any]],
    *,
    limit: int = 36,
) -> list[dict[str, Any]]:
    """Merge complementary geometry providers without duplicate faces."""
    def overlap_over_smaller(first: list[float], second: list[float]) -> float:
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
        return intersection / max(1e-6, min(first_area, second_area))

    retained_bounded = [
        item for item in bounded
        if not any(
            overlap_over_smaller(
                list(item.get("bounds") or []),
                list(partition.get("bounds") or []),
            ) >= 0.88
            for partition in partitions
            if len(item.get("bounds") or []) == 4
            and len(partition.get("bounds") or []) == 4
        )
    ]
    combined = list(partitions) + retained_bounded
    combined.sort(key=lambda item: (
        float((item.get("center") or [0.0, 0.0])[1]),
        float((item.get("center") or [0.0, 0.0])[0]),
        str(item.get("candidate_id") or ""),
    ))
    return combined[:max(1, int(limit))]


def _compact_region_inventory(
    inventory: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Project only model-relevant candidate fields into the prompt."""
    fields = {
        "candidate_id", "bounds", "center", "shape", "peer_group",
        "peer_count", "row_index", "column_index", "partition_locator",
    }
    compact: list[dict[str, Any]] = []
    for item in inventory:
        if isinstance(item.get("partition_locator"), dict):
            # Repeated partition candidates already encode one exact typed
            # relation.  Repeating bounds, center, shape, peer count, and the
            # locator object for every cell bloats every visual request without
            # helping semantic selection.
            compact.append({
                "candidate_id": item.get("candidate_id"),
                "row_index": item.get("row_index"),
                "column_index": item.get("column_index"),
            })
        else:
            compact.append({
                key: value for key, value in item.items() if key in fields
            })
    return compact


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
    validation_feedback: dict[str, Any] | None = None,
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
    target_binding = prior.get("target_binding") or {}
    appearance = inventory_frame_appearance(
        image_path,
        client_frame_bounds=(
            target_binding.get("client_frame_bounds")
            if isinstance(target_binding, dict)
            else None
        ),
    )
    surface_bounds = (
        list(appearance.get("surface_bounds") or [])
        if appearance.get("ok") is True
        else []
    )
    allowed_bounds = surface_bounds if len(surface_bounds) == 4 else None
    # Geometry providers create current-frame candidates before semantic
    # planning.  Closed/irregular faces and open repeated partitions are
    # complementary; neither assigns application or game meaning.
    bounded_inventory = inventory_visible_regions(
        image_path, allowed_bounds=allowed_bounds,
    ) if allowed_bounds is not None else inventory_visible_regions(image_path)
    partition_inventory = inventory_visible_partition_regions(
        image_path, allowed_bounds=allowed_bounds,
    )
    region_inventory = _merge_current_region_inventories(
        bounded_inventory,
        partition_inventory,
    )
    if (
        primary_modality not in {"click", "draw"}
        and not (
            primary_modality == "infer_from_scene"
            and str(intent.get("session_mode") or "") == "persistent"
        )
    ):
        region_inventory = []
    elif primary_modality == "infer_from_scene":
        region_inventory = [
            item for item in region_inventory
            if int(item.get("peer_count") or 0) >= 3
        ]
    planning_prior = dict(prior)
    if region_inventory:
        planning_prior["current_frame_region_inventory"] = (
            _compact_region_inventory(region_inventory)
        )
    if appearance.get("ok") is True and (
        appearance.get("colour_components") or appearance.get("dark_components")
    ):
        planning_prior["current_frame_appearance_inventory"] = appearance
        role_matches = match_role_colours(contract_roles, appearance)
        if role_matches:
            planning_prior["immutable_role_colour_matches"] = role_matches
    raw = vlm.structured_json_from_image(
        image_path,
        _planning_prompt(
            task,
            intent,
            planning_prior,
            immutable_roles=contract_roles,
            primary_action_modality=primary_modality,
            validation_feedback=validation_feedback,
        ),
        max_tokens=2400,
    )
    model_payload = dict(raw)
    try:
        raw, repairs = _canonicalize_model_decision(raw, prior_state=prior)
        raw = _apply_immutable_roles(raw, contract_roles, repairs=repairs)
        raw = _canonicalize_model_action_geometry(raw)
        raw = _bind_visible_region_candidates(
            raw, region_inventory, repairs=repairs,
        )
    except InteractionPlanOutputError as exc:
        exc.attach_payload(model_payload)
        raise
    deferred_raw = raw.pop("_runtime_deferred_actions", [])
    program_continuation = str(
        raw.pop("_runtime_program_continuation", "finish") or "finish"
    ).strip().casefold()
    continuation_aliases = {
        "complete": "finish", "done": "finish",
        "continue": "reobserve", "observe": "reobserve",
        "wait": "wait_external", "wait_for_external": "wait_external",
    }
    program_continuation = continuation_aliases.get(
        program_continuation, program_continuation,
    )
    if program_continuation not in {
        "finish", "reobserve", "wait_external", "blocked",
    }:
        program_continuation = "reobserve"
        repairs.append("program_continuation:invalid->reobserve")
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
        validation_errors = _validation_error_details(exc)
        error_code = (
            "blocking_ambiguity"
            if any(
                "ambiguous interaction cannot authorize mutation"
                in str(item.get("message") or "")
                for item in validation_errors
            )
            else "schema_validation_failed"
        )
        raise InteractionPlanOutputError(
            f"visual model returned an unsafe interaction plan: {exc}",
            code=error_code,
            repairs=repairs,
            raw_payload=model_payload,
            validation_errors=validation_errors,
        ) from exc
    deferred_actions: list[InteractionAction] = []
    try:
        for candidate in (
            deferred_raw if isinstance(deferred_raw, list) else []
        ):
            normalized_candidate = _canonicalize_model_action_geometry({
                "actions": [candidate],
            }).get("actions", [candidate])[0]
            action = InteractionAction.model_validate(normalized_candidate)
            probe = decision.model_dump(mode="python")
            probe["actions"] = [action.model_dump(mode="python")]
            effect_region = list(action.expected_effect_region)
            probe["expected_effect"] = {
                "description": (
                    "the next deferred grounded action produces its declared "
                    "visible effect"
                ),
                "region": effect_region,
                "region_binding": "independent_region",
                "binding_action_index": -1,
                "minimum_change_ratio": 0.001,
                "semantic_verification_required": True,
            }
            probe["continuation"] = "reobserve"
            probe["wait_for_external_change"] = False
            InteractionDecision.model_validate(probe)
            deferred_actions.append(action)
    except (ValidationError, ValueError, TypeError) as exc:
        raise InteractionPlanOutputError(
            f"visual model returned an unsafe deferred action program: {exc}",
            repairs=repairs,
            raw_payload=model_payload,
            validation_errors=(
                _validation_error_details(exc)
                if isinstance(exc, ValidationError)
                else []
            ),
        ) from exc
    if len(decision.actions) + len(deferred_actions) > 12:
        raise InteractionPlanOutputError(
            "visual model action program exceeds the typed action budget",
            repairs=repairs,
            raw_payload=model_payload,
        )
    decision._canonicalization_repairs = tuple(repairs)
    decision._deferred_actions = tuple(deferred_actions)
    decision._program_continuation = program_continuation
    decision._region_inventory = tuple(dict(item) for item in region_inventory)
    try:
        validate_model_action_affordances(decision, intent)
        validate_primary_action_modality(decision, primary_modality)
    except InteractionPlanOutputError as exc:
        exc.attach_payload(model_payload)
        raise
    return decision


def controller_actions(
    decision: InteractionDecision,
    *,
    frame_region: dict[str, int],
    bound_region: InteractionRegion,
    frame_epoch: int,
) -> list[dict[str, Any]]:
    """Compatibility import for callers that used the former monolith."""
    from .interaction_controller import controller_actions as map_actions

    return map_actions(
        decision,
        frame_region=frame_region,
        bound_region=bound_region,
        frame_epoch=frame_epoch,
    )


def verify_interaction_effect(
    image_path: str | Path,
    *,
    task: str,
    decision: InteractionDecision,
    authoritative_effect_regions: list[list[float]] | None = None,
    transition_evidence: dict[str, Any] | None = None,
    gesture_evidence: dict[str, Any] | None = None,
    verified_turn_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Verify one local turn and terminal task completion as separate facts.

    A valid intermediate action is not a failed task merely because more work
    remains.  Keeping these judgments independent lets the stage graph consume
    an explicit ``reobserve`` continuation while the final completion contract
    remains fail-closed.
    """
    effect_regions = [
        list(item.expected_effect_region)
        for item in decision.actions
        if len(item.expected_effect_region) == 4
    ]
    if len(decision.expected_effect.region) == 4:
        effect_regions.append(list(decision.expected_effect.region))
    if not effect_regions:
        effect_regions.append(list(decision.interaction_region))
    if authoritative_effect_regions:
        effect_regions.extend([
            list(bounds)
            for bounds in authoritative_effect_regions
            if isinstance(bounds, list) and len(bounds) == 4
        ])
    effect_regions = [
        list(bounds) for bounds in dict.fromkeys(
            tuple(float(value) for value in bounds)
            for bounds in effect_regions
        )
    ]
    typed_actions = [
        {
            "action": item.action,
            "interaction_mechanism": item.interaction_mechanism,
            "target_region": list(item.target_region),
            "target_anchor": list(item.target_anchor),
            "effect_region": list(item.expected_effect_region),
            "affordance_evidence": item.affordance_evidence,
            "color": item.color,
            "text": item.text,
            "stroke_count": len(item.resolved_frame_strokes()),
        }
        for item in decision.actions
    ]
    transition = dict(transition_evidence or {})
    transition_typed_compatible = bool(decision.actions) and all(
        item.action == "drag"
        and item.interaction_mechanism == "direct_manipulation"
        and len(item.source_region) == 4
        and len(item.target_region) == 4
        for item in decision.actions
    )
    transition_summary = {
        "applicable": transition.get("applicable") is True,
        "verified": (
            transition.get("verified") is True
            and transition_typed_compatible
        ),
        "provider": str(transition.get("provider") or ""),
        "typed_compatible": transition_typed_compatible,
        "actions": [
            {
                key: item.get(key)
                for key in (
                    "action_index", "verified", "source_change_ratio",
                    "target_change_ratio", "appearance_histogram_intersection",
                    "placement_geometry_valid",
                )
            }
            for item in (transition.get("actions") or [])
            if isinstance(item, dict)
        ],
    }
    gesture = dict(gesture_evidence or {})
    gesture_typed_compatible = bool(decision.actions) and all(
        item.action == "draw_strokes"
        and item.interaction_mechanism == "freehand_strokes"
        for item in decision.actions
    )
    gesture_summary = {
        "applicable": gesture.get("applicable") is True,
        "verified": (
            gesture.get("verified") is True
            and gesture_typed_compatible
        ),
        "provider": str(gesture.get("provider") or ""),
        "typed_compatible": gesture_typed_compatible,
        "actions": [
            {
                key: item.get(key)
                for key in (
                    "action_index", "verified", "stroke_count",
                    "minimum_stroke_coverage", "extent_ratio",
                    "changed_pixels_in_legal_region",
                )
            }
            for item in (gesture.get("actions") or [])
            if isinstance(item, dict)
        ],
    }
    prior_verified_turns = [
        dict(item)
        for item in (verified_turn_history or [])[-12:]
        if isinstance(item, dict)
    ]
    prompt = (
        "You are a strict verifier for one visual GUI interaction turn. Judge only "
        "the attached post-action target-window frame. The runtime separately proves "
        "causal pixel change and exact current-frame geometry. Decide whether the local "
        "semantic content requested by the original user and the typed role/rule contract "
        "is visibly present inside the "
        "typed target/effect region, belongs to the agent's assigned role, and does not "
        "include an extra fabricated participant turn. Typed regions and target anchors "
        "are authoritative for WHERE. Planner rationale and free-form expected-effect prose "
        "are not verifier obligations because they can contain a stale relative-position "
        "label; judge WHAT from the original task, immutable roles/rules, action type, and "
        "visible affordance evidence. A verified deterministic transition means current-run "
        "pixels independently prove the typed source appearance left its source and arrived "
        "inside its typed target; use it to resolve spatial ambiguity, but still judge the "
        "task's semantics yourself. A verified deterministic gesture means this current "
        "run causally authored every declared pointer path inside the typed target. It is "
        "authoritative for authorship and pre/post causality: do not claim that the visible "
        "mark pre-existed or was not drawn by this turn when that evidence is verified. "
        "You still judge whether the resulting content has the requested semantics. "
        "Make two independent judgments: local_effect_passed says whether every typed "
        "action in THIS turn produced its requested visible semantic effect in the typed "
        "region; task_complete says whether the ORIGINAL user task is now terminally "
        "complete. More work remaining is a completion issue, never a local-effect issue, "
        "when this turn itself visibly succeeded. Return ONLY JSON with keys "
        "local_effect_passed (boolean), task_complete (boolean), confidence (0..1), "
        "observed (array of strings), local_effect_issues (array of strings), "
        "completion_issues (array of strings), and effect_region "
        "([left,top,right,bottom] in 0..1000 or []). local_effect_issues MUST be [] "
        "whenever local_effect_passed is true. completion_issues MUST be [] whenever "
        "task_complete is true. task_complete may be true only when the original user "
        "task, not just this local action, is visibly complete in this frame.\n"
        "Prior verified turns below are current-run evidence emitted only after "
        "a controller action, authoritative post-frame causal change, and its "
        "local evidence contract succeeded. Treat those earlier actions as "
        "facts when judging order and progress; do not call an earlier visibly "
        "completed step unperformed merely because it is outside this turn's "
        "effect ROI.\n"
        f"User task: {task}\n"
        f"Roles: {json.dumps(decision.roles, ensure_ascii=False)}\n"
        f"Rules: {json.dumps(decision.rules, ensure_ascii=False)}\n"
        f"Typed executed actions: {json.dumps(typed_actions, ensure_ascii=False)}\n"
        f"Expected effect regions: {json.dumps(effect_regions)}\n"
        f"Deterministic transition evidence: {json.dumps(transition_summary)}\n"
        f"Deterministic gesture evidence: {json.dumps(gesture_summary)}\n"
        f"Prior current-run verified turns: "
        f"{json.dumps(prior_verified_turns, ensure_ascii=False)}"
    )
    raw = vlm.structured_json_from_image(image_path, prompt, max_tokens=800)
    confidence = raw.get("confidence")
    confidence_valid = (
        isinstance(confidence, (int, float))
        and not isinstance(confidence, bool)
        and math.isfinite(float(confidence))
        and 0.0 <= float(confidence) <= 1.0
    )
    new_verdict_schema = "local_effect_passed" in raw
    raw_local_effect_passed = (
        raw.get("local_effect_passed") is True
        if new_verdict_schema
        else raw.get("passed") is True
    )
    local_effect_issues = (
        raw.get("local_effect_issues")
        if new_verdict_schema
        else raw.get("issues")
    )
    completion_issues = (
        raw.get("completion_issues")
        if new_verdict_schema
        else raw.get("issues")
    )
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
    local_effect_issues_valid = bool(
        isinstance(local_effect_issues, list)
        and not any(str(item).strip() for item in local_effect_issues)
    )
    completion_issues_valid = bool(
        isinstance(completion_issues, list)
        and not any(str(item).strip() for item in completion_issues)
    )
    issues_adjudicated_by_transition = bool(
        not local_effect_issues_valid
        and transition_summary["applicable"]
        and transition_summary["verified"]
        and raw_local_effect_passed
    )
    legacy_completion_issue_adjudicated = bool(
        not new_verdict_schema
        and issues_adjudicated_by_transition
    )
    passed = bool(
        raw_local_effect_passed
        and confidence_valid
        and float(confidence) >= 0.72
        and (
            local_effect_issues_valid
            or issues_adjudicated_by_transition
        )
        and isinstance(observed, list)
        and any(str(item).strip() for item in observed)
        and region_valid
    )
    task_complete = bool(
        passed
        and raw.get("task_complete") is True
        and (
            completion_issues_valid
            or legacy_completion_issue_adjudicated
        )
    )
    return {
        "ok": True,
        "verified": passed,
        "semantic_visual_verified": passed,
        "confidence": float(confidence) if confidence_valid else None,
        "observed": observed if isinstance(observed, list) else [],
        "issues": (
            local_effect_issues
            if isinstance(local_effect_issues, list)
            else ["invalid local-effect verifier payload"]
        ),
        "local_effect_issues": (
            local_effect_issues
            if isinstance(local_effect_issues, list)
            else ["invalid local-effect verifier payload"]
        ),
        "completion_issues": (
            completion_issues
            if isinstance(completion_issues, list)
            else ["invalid completion verifier payload"]
        ),
        "effect_region": reported_region if isinstance(reported_region, list) else [],
        "task_complete": task_complete,
        "expected_effect": decision.expected_effect.model_dump(mode="json"),
        "verifier_contract": {
            "verdict_schema": (
                "local-effect-and-terminal-v2"
                if new_verdict_schema
                else "legacy-passed-v1"
            ),
            "raw_passed": raw_local_effect_passed,
            "raw_local_effect_passed": raw_local_effect_passed,
            "confidence_valid": confidence_valid,
            "issues_valid": local_effect_issues_valid,
            "local_effect_issues_valid": local_effect_issues_valid,
            "completion_issues_valid": completion_issues_valid,
            "legacy_completion_issue_adjudicated": (
                legacy_completion_issue_adjudicated
            ),
            "issues_adjudicated_by_transition": (
                issues_adjudicated_by_transition
            ),
            "observed_valid": isinstance(observed, list)
            and any(str(item).strip() for item in observed),
            "effect_region_valid": region_valid,
            "typed_actions": typed_actions,
            "transition_evidence": transition_summary,
            "gesture_evidence": gesture_summary,
            "prior_verified_turns": prior_verified_turns,
        },
        "raw_verdict": raw,
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
