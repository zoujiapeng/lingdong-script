"""Unified WinPilot Agent loop.

One AgentRunner now owns desktop GUI work, Coding Runtime work, tool dispatch,
trace, usage accounting, and done verification. Provider-specific chat details
are centralized in ``winpilot.model_adapter``; this loop keeps the stable
function-calling history, context folding, and tool-result repair behaviors.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from ..config import CONFIG, TRACES_DIR
from ..memory import compiler, experience
from ..model_adapter import (
    accumulate_usage,
    build_model_adapter,
    empty_usage_total,
    model_profile_from_config,
)
from ..perception import fusion, intent, vlm
from ..runtime_info import runtime_fingerprint
from ..fast.action_contract import build_action_envelope
from ..utils.image_capture import (
    capture_window_jpeg_b64,
    credible_work_surface_frame_bounds,
    jpeg_diff_ratio,
    jpeg_diff_ratio_in_normalized_roi,
    select_visual_capture_scope,
)
from ..utils.log import BUS, logger
from ..utils.screenshot import virtual_screen_region, window_region
from ..utils.trace_payload import sanitize_trace_payload
from . import playbooks, prompts, session as session_mod
from .context_guidance import ContextGuidanceMixin
from .done_verifier import (
    DoneVerifierMixin,
    _deterministic_arithmetic_verdict,
    _docx_document_evidence,
    _docx_task_format_errors,
    _explicit_saved_paths,
)
from .failure_recovery import FailureRecoveryLayer, attach_recovery_metadata
from .gui_plan_helpers import (
    _looks_like_pure_open_plan_goal,
    _schema_profile_for_task,
)
from .planner import planning_enabled
from .partial_output_guard import PartialOutputDestructiveGuard
from .reflection import ReflectionMixin
from .run_state import ACTION_TOOLS as _RUN_ACTION_TOOLS, RunStateMachine
from .runtime_profile import RuntimeProfile, build_runtime_profile
from .semantic_progress import (
    ContinuousSemanticProgress,
    GeometryFingerprint,
    TargetKey,
    fingerprint_geometry,
    structured_primitive_paths,
    structured_effect_applied,
)
from .task_rules import (
    _communication_mode,
    _document_format_specs as _task_document_format_specs,
    _exact_document_text_block as _task_exact_document_text_block,
    _blocks_store_search_action,
    _blocks_upload_media_analysis,
    _gui_only_forbids_shell,
    _gui_only_launch_uses_shell,
    _gui_only_shell_allowed,
    _is_coding_task,
    _is_communication_task,
    _is_desktop_icon_drag_task,
    _is_arithmetic_task,
    _is_gui_only_task,
    _is_multi_source_research_task,
    _is_upload_publish_task,
    _quoted_payloads,
    _required_image_paths,
    _required_literal_tokens,
    _required_visible_payloads,
    _visible_state_targets,
)
from .tools import ToolResult, ToolSession, tool_schemas

_KEEP_FULL_OBSERVATIONS = 1
_API_RETRIES = 4
# 429/rate-limit backoff base seconds (exponential: 5, 10, 20...). Bursts of
# rapid LLM calls (e.g. drawing many strokes) can trip provider rate limits;
# a short retry isn't enough, so wait progressively longer.
_RATE_LIMIT_BACKOFF_S = 5.0
# Keep recovery bounded, but leave enough room for one complete structured GUI
# call. A 1024-token cap made the correction itself truncate on ordinary
# multi-path gestures, turning a recoverable provider formatting miss into a
# guaranteed second failure.
_GUI_TOOL_RECOVERY_MAX_TOKENS = 4096
# Fold old observations only past this size (~12K tokens × ~4 chars/token).
# Below it, history stays append-only so the DeepSeek prefix cache hits ~100%.
_CONTEXT_FOLD_THRESHOLD_CHARS = 32000
_MAX_ASSISTANT_HISTORY_CHARS = 900
_ACTION_TOOLS = set(_RUN_ACTION_TOOLS)
_VISUAL_MUTATION_TOOLS = {
    "open_app", "launch", "focus_window", "click", "type_text", "hotkey",
    "scroll", "drag", "draw_strokes", "tray_click", "clipboard_paste",
    "paste_image_file", "mouse_down", "mouse_up", "move_mouse", "hold_key",
    "run_action_batch", "execute_subgoal", "coordinate_click_test",
}
_LOW_LEVEL_VISUAL_TOOLS = {
    "click", "type_text", "hotkey", "scroll", "drag", "draw_strokes",
    "tray_click", "clipboard_paste", "paste_image_file", "mouse_down",
    "mouse_up", "move_mouse", "hold_key", "coordinate_click_test",
    "run_action_batch", "execute_subgoal",
}
# A native model grounds these actions in one submitted image.  Letting two of
# them share a batch recreates an open-loop macro: the second target may no
# longer exist after the first action.  Keyboard-only work may remain beside a
# single spatial mutation because it does not select another pixel target.
_NATIVE_BATCH_SPATIAL_MUTATIONS = {
    "click",
    "click_element",
    "type_into_element",  # locates/focuses the field with a click before typing
    "scroll",
    "move_mouse",
    "drag",
    "draw_strokes",
    "select_color",
    "ensure_document_surface",  # may click through document-creation surfaces
}

# Native visual turns are transactions, not a chat transcript.  Re-sending
# every prior function call/result makes the prompt grow once per click while
# the only authoritative GUI state is the newest frame.  These bounds keep a
# small rolling action bulletin without losing the original task or plan.
_NATIVE_CONTEXT_RECENT_ACTIONS = 8
_NATIVE_CONTEXT_RECENT_GUIDANCE = 4
_NATIVE_CONTEXT_MAX_DIRECTIVE_CHARS = 4000
_NATIVE_CONTEXT_MAX_SUMMARY_CHARS = 3200
_NATIVE_NO_PROGRESS_RATIO = 0.0005
_NATIVE_NO_PROGRESS_STREAK = 2
_NATIVE_NO_PROGRESS_MAX_BLOCKS = 2
_NATIVE_STAGNATION_SPATIAL_TOOLS = {
    "click", "scroll", "drag", "draw_strokes", "move_mouse",
    "tray_click", "coordinate_click_test", "run_action_batch",
}
_CONTINUOUS_POINTER_SETUP_CLICKS = 3
_CONTINUOUS_POINTER_LOW_CHANGE_CLICKS = 2
_CONTINUOUS_POINTER_MAX_BLOCKS = 2
_TEXT_CONTINUOUS_POINTER_SETUP_MUTATIONS = 2
_NATIVE_DRAW_SURFACE_MIN_POINT_RATIO = 1.0
_NATIVE_DRAW_SURFACE_SOFT_MIN_POINT_RATIO = 0.80
_NATIVE_DRAW_SURFACE_SOFT_MARGIN = 8.0
_NATIVE_DRAW_SURFACE_SOFT_MIN_POINTS = 12
_NATIVE_DRAW_MAX_STROKES = 24
_NATIVE_DRAW_COMPOSITION_FIT_MIN_OUTSIDE_RATIO = 0.20
_NATIVE_DRAW_COMPOSITION_FIT_MIN_OUTSIDE_POINTS = 3
_NATIVE_DRAW_COMPOSITION_FIT_MIN_BOUNDED_OUTSIDE_RATIO = 0.05
_NATIVE_DRAW_COMPOSITION_FIT_MIN_OUTSIDE_STROKES = 2
_NATIVE_DRAW_COMPOSITION_FIT_MAX_AXIS_OVERFLOW_RATIO = 0.20
_NATIVE_DRAW_COMPOSITION_FIT_MIN_STROKES = 2
_NATIVE_DRAW_COMPOSITION_FIT_MIN_POINTS = 8
_NATIVE_DRAW_COMPOSITION_FIT_INSET = 8.0
_NATIVE_DRAW_COMPOSITION_TRANSLATE_MAX_OVERFLOW = 16.0
_NATIVE_DRAW_DENSE_PATH_MIN_POINTS = 24
_NATIVE_DRAW_SIMPLIFY_TOLERANCE = 1.5
_UNEXECUTED_CONSTRAINT_MAX_REJECTIONS = 3
_INITIAL_FRAME_SETTLE_INTERVAL_S = 0.12
_INITIAL_FRAME_SETTLE_MIN_S = 0.48
_INITIAL_FRAME_SETTLE_MAX_S = 1.20
_INITIAL_FRAME_SETTLE_MAX_CAPTURES = 10
_INITIAL_FRAME_SETTLE_STABLE_PAIRS = 2


class NativeFrameCoordinateError(ValueError):
    """A fail-closed coordinate error with structured model feedback."""

    def __init__(self, message: str, **metadata: Any) -> None:
        super().__init__(message)
        self.metadata = metadata


def _frame_strokes_bbox(
    strokes: list[list[tuple[float, float]]],
) -> tuple[float, float, float, float]:
    points = [point for stroke in strokes for point in stroke]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _normalize_stringified_frame_strokes(
    raw: str,
) -> tuple[list[list[list[float]]], dict[str, Any]]:
    """Recover only unambiguous numeric polylines from a stringified array.

    Some function-calling providers serialize an array-valued argument a
    second time and occasionally omit or append redundant trailing closing
    brackets. We accept only bracket/comma/numeric text, balance missing
    closings or remove count-proven trailing extras, then
    require every numeric leaf to belong to a complete ``[x,y]`` point in a
    2-24 point polyline.  Nothing is clamped or invented; ambiguous text stays
    fail-closed and never reaches the desktop executor.
    """
    text = str(raw or "").strip()
    if not text or len(text) > 50_000:
        raise ValueError("stringified frame_strokes is empty or too large")
    if re.fullmatch(r"[\s\[\],+\-.0-9eE]+", text) is None:
        raise ValueError("stringified frame_strokes contains non-numeric content")
    open_count = text.count("[")
    close_count = text.count("]")
    missing_closures = open_count - close_count
    extra_closures_removed = 0
    if missing_closures < 0:
        extra_closures_removed = -missing_closures
        if (
            extra_closures_removed > 32
            or not text.endswith("]" * extra_closures_removed)
        ):
            raise ValueError("stringified frame_strokes has ambiguous bracket structure")
        text = text[:-extra_closures_removed]
        missing_closures = 0
    if missing_closures > 32:
        raise ValueError("stringified frame_strokes has ambiguous bracket structure")
    try:
        parsed = json.loads(text + ("]" * missing_closures))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError("stringified frame_strokes is not a recoverable numeric array") from exc

    def is_number(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    def is_point(value: Any) -> bool:
        return (
            isinstance(value, list)
            and len(value) == 2
            and all(is_number(item) for item in value)
        )

    def is_flat_stroke(value: Any) -> bool:
        return (
            isinstance(value, list)
            and 4 <= len(value) <= 2 * _NATIVE_DRAW_MAX_STROKES
            and len(value) % 2 == 0
            and all(is_number(item) for item in value)
        )

    def pair_flat_stroke(value: list[Any]) -> list[list[Any]]:
        return [list(value[index:index + 2]) for index in range(0, len(value), 2)]

    strokes: list[list[list[float]]] = []
    flat_numeric_paths_normalized = 0

    def collect(value: Any) -> None:
        nonlocal flat_numeric_paths_normalized
        if (
            isinstance(value, list)
            and 2 <= len(value) <= _NATIVE_DRAW_MAX_STROKES
            and all(is_point(item) for item in value)
        ):
            strokes.append(value)
            return
        if is_flat_stroke(value):
            strokes.append(pair_flat_stroke(value))
            flat_numeric_paths_normalized += 1
            return
        if isinstance(value, list):
            for item in value:
                collect(item)

    def numeric_leaf_count(value: Any) -> int:
        if isinstance(value, list):
            return sum(numeric_leaf_count(item) for item in value)
        return int(is_number(value))

    collect(parsed)
    covered_numeric_leaves = 2 * sum(len(stroke) for stroke in strokes)
    if (
        not strokes
        or covered_numeric_leaves != numeric_leaf_count(parsed)
    ):
        raise ValueError(
            "stringified frame_strokes does not contain only complete 2-24 point polylines"
        )
    if len(strokes) > _NATIVE_DRAW_MAX_STROKES:
        raise ValueError(
            "frame_strokes contains too many independent paths: "
            f"count={len(strokes)}, limit={_NATIVE_DRAW_MAX_STROKES}"
        )
    return strokes, {
        "stringified_array_normalized": True,
        "missing_closing_brackets_repaired": missing_closures,
        "extra_closing_brackets_removed": extra_closures_removed,
        "stroke_count_recovered": len(strokes),
        "point_count_recovered": sum(len(stroke) for stroke in strokes),
        "flat_numeric_paths_normalized": flat_numeric_paths_normalized,
    }


def _normalize_frame_stroke_list_structure(
    raw: list[Any],
) -> tuple[list[list[Any]], dict[str, Any]]:
    """Flatten only redundant list nesting around complete numeric paths."""
    def is_number(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    def is_point(value: Any) -> bool:
        return (
            isinstance(value, (list, tuple))
            and len(value) == 2
            and all(is_number(item) for item in value)
        )

    def is_stroke(value: Any) -> bool:
        return (
            isinstance(value, list)
            and len(value) >= 2
            and all(is_point(item) for item in value)
        )

    def is_flat_stroke(value: Any) -> bool:
        return (
            isinstance(value, list)
            and len(value) >= 4
            and len(value) % 2 == 0
            and all(is_number(item) for item in value)
        )

    def pair_flat_stroke(value: list[Any]) -> list[list[Any]]:
        return [list(value[index:index + 2]) for index in range(0, len(value), 2)]

    if raw and all(is_point(item) for item in raw):
        return [raw], {"unwrapped_single_polyline": True}
    if is_flat_stroke(raw):
        return [pair_flat_stroke(raw)], {
            "unwrapped_single_polyline": True,
            "flat_numeric_paths_normalized": 1,
        }
    if raw and all(is_stroke(item) or is_flat_stroke(item) for item in raw):
        if len(raw) > _NATIVE_DRAW_MAX_STROKES:
            raise ValueError(
                "frame_strokes contains too many independent paths: "
                f"count={len(raw)}, limit={_NATIVE_DRAW_MAX_STROKES}"
            )
        flat_count = sum(1 for item in raw if is_flat_stroke(item))
        return [
            pair_flat_stroke(item) if is_flat_stroke(item) else item
            for item in raw
        ], {
            "unwrapped_single_polyline": False,
            "flat_numeric_paths_normalized": flat_count,
        }

    strokes: list[list[Any]] = []
    flat_numeric_paths_normalized = 0

    def collect(value: Any) -> None:
        nonlocal flat_numeric_paths_normalized
        if is_stroke(value):
            strokes.append(value)
            return
        if is_flat_stroke(value):
            strokes.append(pair_flat_stroke(value))
            flat_numeric_paths_normalized += 1
            return
        if isinstance(value, list):
            for item in value:
                collect(item)

    def leaf_counts(value: Any) -> tuple[int, int]:
        if isinstance(value, list):
            counts = [leaf_counts(item) for item in value]
            return sum(item[0] for item in counts), sum(item[1] for item in counts)
        return 1, int(is_number(value))

    collect(raw)
    total_leaves, numeric_leaves = leaf_counts(raw)
    covered_numeric_leaves = 2 * sum(len(stroke) for stroke in strokes)
    if (
        not strokes
        or total_leaves != numeric_leaves
        or covered_numeric_leaves != numeric_leaves
    ):
        raise ValueError(
            "frame_strokes has ambiguous nesting; provide only complete numeric [x,y] paths"
        )
    if len(strokes) > _NATIVE_DRAW_MAX_STROKES:
        raise ValueError(
            "frame_strokes contains too many independent paths: "
            f"count={len(strokes)}, limit={_NATIVE_DRAW_MAX_STROKES}"
        )
    return strokes, {
        "unwrapped_single_polyline": False,
        "redundant_list_nesting_normalized": True,
        "stroke_count_recovered": len(strokes),
        "point_count_recovered": sum(len(stroke) for stroke in strokes),
        "flat_numeric_paths_normalized": flat_numeric_paths_normalized,
    }


def _simplify_dense_frame_stroke(
    points: list[tuple[float, float]],
    *,
    tolerance: float = _NATIVE_DRAW_SIMPLIFY_TOLERANCE,
) -> list[tuple[float, float]]:
    """Remove only dense, near-collinear samples while preserving endpoints.

    The desktop executor already connects successive anchors continuously.  A
    small Ramer-Douglas-Peucker tolerance therefore removes model-authored
    pixel/step enumeration without replacing a short or deliberately precise
    path.  Coordinates remain in authoritative-frame units (0..1000).
    """
    if len(points) < _NATIVE_DRAW_DENSE_PATH_MIN_POINTS:
        return list(points)

    def distance_to_segment(
        point: tuple[float, float],
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> float:
        px, py = point
        x1, y1 = start
        x2, y2 = end
        dx = x2 - x1
        dy = y2 - y1
        length_squared = dx * dx + dy * dy
        if length_squared <= 1e-12:
            return ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
        projection = max(
            0.0,
            min(1.0, ((px - x1) * dx + (py - y1) * dy) / length_squared),
        )
        nearest_x = x1 + projection * dx
        nearest_y = y1 + projection * dy
        return ((px - nearest_x) ** 2 + (py - nearest_y) ** 2) ** 0.5

    keep = {0, len(points) - 1}
    pending: list[tuple[int, int]] = [(0, len(points) - 1)]
    while pending:
        start_index, end_index = pending.pop()
        if end_index - start_index <= 1:
            continue
        start = points[start_index]
        end = points[end_index]
        farthest_index = -1
        farthest_distance = -1.0
        for index in range(start_index + 1, end_index):
            distance = distance_to_segment(points[index], start, end)
            if distance > farthest_distance:
                farthest_distance = distance
                farthest_index = index
        if farthest_index >= 0 and farthest_distance > tolerance:
            keep.add(farthest_index)
            pending.append((start_index, farthest_index))
            pending.append((farthest_index, end_index))
    return [points[index] for index in sorted(keep)]


def _fit_frame_stroke_composition_to_surface(
    strokes: list[list[tuple[float, float]]],
    surface_bounds: tuple[int, int, int, int],
) -> tuple[list[list[tuple[float, float]]] | None, dict[str, Any]]:
    """Conservatively fit one misframed multi-path composition to one surface.

    This is deliberately not a generic clamp.  A small edge miss may receive
    one rigid translation when the complete multi-path bounding box still fits
    the sole grounded work surface.  A substantially misframed composition may
    receive one uniform scale/translation.  Every point moves together, so the
    authored geometry is preserved and no individual coordinate is clipped.
    """
    left, top, right, bottom = [float(value) for value in surface_bounds]
    points = [point for stroke in strokes for point in stroke]
    source_bbox = _frame_strokes_bbox(strokes)

    def inside(point: tuple[float, float]) -> bool:
        x, y = point
        return left <= x <= right and top <= y <= bottom

    inside_count = sum(1 for point in points if inside(point))
    outside_count = len(points) - inside_count
    outside_ratio = outside_count / max(1, len(points))
    outside_stroke_count = sum(
        1 for stroke in strokes if any(not inside(point) for point in stroke)
    )
    source_left, source_top, source_right, source_bottom = source_bbox
    source_width = source_right - source_left
    source_height = source_bottom - source_top
    intersects_surface = not (
        source_right < left
        or source_left > right
        or source_bottom < top
        or source_top > bottom
    )
    edge_overflow = {
        "left": max(0.0, left - source_left),
        "top": max(0.0, top - source_top),
        "right": max(0.0, source_right - right),
        "bottom": max(0.0, source_bottom - bottom),
    }
    max_edge_overflow = max(edge_overflow.values())
    substantial_outside = (
        outside_count >= _NATIVE_DRAW_COMPOSITION_FIT_MIN_OUTSIDE_POINTS
        and outside_ratio >= _NATIVE_DRAW_COMPOSITION_FIT_MIN_OUTSIDE_RATIO
    )
    surface_width = right - left
    surface_height = bottom - top
    bounded_axis_overflow = bool(
        edge_overflow["left"]
        <= surface_width * _NATIVE_DRAW_COMPOSITION_FIT_MAX_AXIS_OVERFLOW_RATIO
        and edge_overflow["right"]
        <= surface_width * _NATIVE_DRAW_COMPOSITION_FIT_MAX_AXIS_OVERFLOW_RATIO
        and edge_overflow["top"]
        <= surface_height * _NATIVE_DRAW_COMPOSITION_FIT_MAX_AXIS_OVERFLOW_RATIO
        and edge_overflow["bottom"]
        <= surface_height * _NATIVE_DRAW_COMPOSITION_FIT_MAX_AXIS_OVERFLOW_RATIO
    )
    bounded_multi_stroke_overflow = bool(
        outside_count >= _NATIVE_DRAW_COMPOSITION_FIT_MIN_OUTSIDE_POINTS
        and outside_ratio
        >= _NATIVE_DRAW_COMPOSITION_FIT_MIN_BOUNDED_OUTSIDE_RATIO
        and outside_stroke_count
        >= _NATIVE_DRAW_COMPOSITION_FIT_MIN_OUTSIDE_STROKES
        and bounded_axis_overflow
    )
    small_rigid_translation = bool(
        outside_count > 0
        and max_edge_overflow <= _NATIVE_DRAW_COMPOSITION_TRANSLATE_MAX_OVERFLOW
        and source_width <= right - left
        and source_height <= bottom - top
    )
    eligibility = {
        "unique_current_work_surface": True,
        "multi_path_composition": len(strokes) >= _NATIVE_DRAW_COMPOSITION_FIT_MIN_STROKES,
        "minimum_total_points": len(points) >= _NATIVE_DRAW_COMPOSITION_FIT_MIN_POINTS,
        "substantial_outside_points": substantial_outside,
        "bounded_multi_stroke_overflow": bounded_multi_stroke_overflow,
        "bounded_axis_overflow": bounded_axis_overflow,
        "small_rigid_translation": small_rigid_translation,
        "repairable_overflow": (
            substantial_outside
            or bounded_multi_stroke_overflow
            or small_rigid_translation
        ),
        "has_surface_grounded_point": inside_count > 0,
        "composition_bbox_intersects_surface": intersects_surface,
    }
    evidence: dict[str, Any] = {
        "policy": "whole_composition_uniform_affine_fit",
        "applied": False,
        "source_coordinate_space": "current_frame_normalized_0_1000",
        "destination": "unique_current_work_surface",
        "work_surface_frame_bounds": [int(value) for value in surface_bounds],
        "source_bbox": [round(value, 3) for value in source_bbox],
        "stroke_count": len(strokes),
        "input_point_count": len(points),
        "inside_point_count": inside_count,
        "outside_point_count": outside_count,
        "outside_point_ratio": round(outside_ratio, 6),
        "outside_stroke_count": outside_stroke_count,
        "edge_overflow": {
            key: round(value, 3) for key, value in edge_overflow.items()
        },
        "maximum_edge_overflow": round(max_edge_overflow, 3),
        "maximum_axis_overflow_ratio": (
            _NATIVE_DRAW_COMPOSITION_FIT_MAX_AXIS_OVERFLOW_RATIO
        ),
        "eligibility": eligibility,
        "shape_preserving": True,
        "point_clipping_used": False,
    }
    required_conditions = (
        "unique_current_work_surface",
        "multi_path_composition",
        "minimum_total_points",
        "repairable_overflow",
        "has_surface_grounded_point",
        "composition_bbox_intersects_surface",
    )
    failed_conditions = [
        name for name in required_conditions if not eligibility[name]
    ]
    if failed_conditions:
        evidence["rejected_reason"] = "failed_conditions: " + ", ".join(failed_conditions)
        return None, evidence

    max_inset_x = max(0.0, (right - left - 2.0) / 4.0)
    max_inset_y = max(0.0, (bottom - top - 2.0) / 4.0)
    inset = (
        0.0
        if small_rigid_translation
        else min(
            _NATIVE_DRAW_COMPOSITION_FIT_INSET,
            max_inset_x,
            max_inset_y,
        )
    )
    destination_left = left + inset
    destination_top = top + inset
    destination_right = right - inset
    destination_bottom = bottom - inset
    destination_width = destination_right - destination_left
    destination_height = destination_bottom - destination_top
    if destination_width <= 0.0 or destination_height <= 0.0:
        evidence["rejected_reason"] = "the inset work surface had no usable area"
        return None, evidence
    if source_width <= 0.0 and source_height <= 0.0:
        evidence["rejected_reason"] = "the composition had no spatial extent"
        return None, evidence

    if small_rigid_translation:
        scale = 1.0
    else:
        scales = [1.0]
        if source_width > 0.0:
            scales.append(destination_width / source_width)
        if source_height > 0.0:
            scales.append(destination_height / source_height)
        scale = min(scales)
    center_x = (source_left + source_right) / 2.0
    center_y = (source_top + source_bottom) / 2.0
    scaled_left = center_x + (source_left - center_x) * scale
    scaled_right = center_x + (source_right - center_x) * scale
    scaled_top = center_y + (source_top - center_y) * scale
    scaled_bottom = center_y + (source_bottom - center_y) * scale
    translate_x = 0.0
    translate_y = 0.0
    if scaled_left < destination_left:
        translate_x = destination_left - scaled_left
    elif scaled_right > destination_right:
        translate_x = destination_right - scaled_right
    if scaled_top < destination_top:
        translate_y = destination_top - scaled_top
    elif scaled_bottom > destination_bottom:
        translate_y = destination_bottom - scaled_bottom

    fitted = [
        [
            (
                center_x + (x - center_x) * scale + translate_x,
                center_y + (y - center_y) * scale + translate_y,
            )
            for x, y in stroke
        ]
        for stroke in strokes
    ]
    fitted_bbox = _frame_strokes_bbox(fitted)
    if any(not inside(point) for stroke in fitted for point in stroke):
        evidence["rejected_reason"] = "the computed affine fit did not remain inside the work surface"
        return None, evidence
    evidence.update({
        "policy": (
            "whole_composition_rigid_translation"
            if small_rigid_translation
            else "whole_composition_uniform_affine_fit"
        ),
        "applied": True,
        "reason": (
            "small multi-path edge overflow translated into one grounded work surface"
            if small_rigid_translation
            else (
                "substantial multi-path composition overflow on one grounded work surface"
                if substantial_outside
                else "bounded multi-stroke composition overflow on one grounded work surface"
            )
        ),
        "surface_inset_bounds": [
            round(destination_left, 3), round(destination_top, 3),
            round(destination_right, 3), round(destination_bottom, 3),
        ],
        "mapped_bbox": [round(value, 3) for value in fitted_bbox],
        "uniform_scale": round(scale, 8),
        "translation": [round(translate_x, 3), round(translate_y, 3)],
    })
    return fitted, evidence


def _result_explicitly_blocked_before_execution(result_text: str) -> bool:
    """Recognize controller-internal barriers that provably touched no GUI.

    Entering a controller is not the same as executing one of its requested
    actions.  In particular, work-surface discovery may fail closed after the
    outer dispatcher has been called.  Such results must retain their precise
    "not executed" meaning instead of entering recovery logic that assumes the
    action might have succeeded.
    """
    try:
        payload = json.loads(result_text or "{}")
    except (json.JSONDecodeError, TypeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    no_execution_markers = {
        "work_surface_fail_closed",
        "frame_coordinate_invalid",
        "visual_preflight_failed",
        "visual_frame_changed",
        "visual_grounding_required",
        "visual_turn_barrier",
        "terminal_turn_barrier",
    }

    def marked(value: Any) -> bool:
        if isinstance(value, dict):
            if any(value.get(key) is True for key in no_execution_markers):
                return True
            if any(
                key in value and value.get(key) is False
                for key in (
                    "action_executed",
                    "mutation_executed",
                    "requested_action_executed",
                )
            ):
                return True
            return any(marked(child) for child in value.values())
        if isinstance(value, list):
            return any(marked(child) for child in value)
        return False

    if payload.get("blocked") is True and marked(payload):
        return True

    def cooperative_guard(value: Any) -> bool:
        if isinstance(value, dict):
            if (
                value.get("blocked") is True
                and value.get("cooperative_input") is True
            ):
                scope = value.get("scope_guard")
                if (
                    isinstance(scope, dict)
                    and scope.get("recovery_attempted") is False
                    and scope.get("focus_stolen") is False
                ):
                    return True
                # Exact payload contract returned by cooperative_input_guard
                # before keyboard/focus/physical input is attempted.  Do not
                # match mid-action arbitration failures, which deliberately
                # carry progress fields instead of this target/foreground
                # guard tuple and may need normal recovery.
                if (
                    value.get("ok") is False
                    and value.get("verified") is False
                    and "action" in value
                    and "target_hwnd" in value
                    and "foreground_hwnd" in value
                ):
                    return True
            return any(cooperative_guard(child) for child in value.values())
        if isinstance(value, list):
            return any(cooperative_guard(child) for child in value)
        return False

    def controller_already_executed_child(value: Any) -> bool:
        if isinstance(value, dict):
            children = value.get("results")
            if isinstance(children, list) and any(
                isinstance(child, dict) and child.get("ok") is True
                for child in children
            ):
                return True
            return any(
                controller_already_executed_child(child)
                for child in value.values()
            )
        if isinstance(value, list):
            return any(controller_already_executed_child(child) for child in value)
        return False

    return bool(
        cooperative_guard(payload)
        and not controller_already_executed_child(payload)
    )


def _unexecuted_constraint_category(result_text: str) -> str:
    """Return a stable category for a fail-closed, unexecuted rejection.

    The category intentionally ignores tool names, coordinates, point counts,
    and changing error prose.  A model that changes the rejected geometry but
    keeps violating the same controller contract is still repeating the same
    failure class.  Administrative same-turn barriers and guards that already
    own a bounded retry policy are excluded.
    """
    try:
        payload = json.loads(result_text or "{}")
    except (json.JSONDecodeError, TypeError, ValueError):
        return ""
    if not isinstance(payload, dict):
        return ""

    def any_true(value: Any, marker: str) -> bool:
        if isinstance(value, dict):
            return value.get(marker) is True or any(
                any_true(child, marker) for child in value.values()
            )
        if isinstance(value, list):
            return any(any_true(child, marker) for child in value)
        return False

    def contains_blocked_result(value: Any) -> bool:
        if isinstance(value, dict):
            return value.get("blocked") is True or any(
                contains_blocked_result(child) for child in value.values()
            )
        if isinstance(value, list):
            return any(contains_blocked_result(child) for child in value)
        return False

    if not contains_blocked_result(payload):
        return ""

    def requested_action_executed(value: Any) -> bool:
        if isinstance(value, dict):
            if any(
                value.get(marker) is True
                for marker in (
                    "native_action_executed",
                    "action_executed",
                    "mutation_executed",
                    "requested_action_executed",
                )
            ):
                return True
            if any(
                isinstance(value.get(marker), list) and bool(value.get(marker))
                for marker in ("actions_executed", "executed_actions")
            ):
                return True
            children = value.get("results")
            if isinstance(children, list) and any(
                isinstance(child, dict)
                and child.get("ok") is True
                and child.get("blocked") is not True
                for child in children
            ):
                return True
            return any(requested_action_executed(child) for child in value.values())
        if isinstance(value, list):
            return any(requested_action_executed(child) for child in value)
        return False

    # These barriers either attach a new authoritative frame or already have a
    # dedicated retry/safe-stop budget.  Counting them here would double-charge
    # one incident or treat a same-turn acknowledgement as a new model choice.
    excluded = {
        "visual_turn_barrier",
        "terminal_turn_barrier",
        "visual_frame_changed",
        "visual_grounding_required",
        "partial_output_destructive_guard",
        "semantic_progress_guard",
        "continuous_pointer_guard",
        "native_no_progress_guard",
        "constraint_rejection_guard",
    }
    if any(any_true(payload, marker) for marker in excluded):
        return ""
    if requested_action_executed(payload):
        return ""
    if not _result_explicitly_blocked_before_execution(result_text):
        return ""

    # Ordered broad categories are more useful than brittle error-string
    # fingerprints.  In particular every malformed/out-of-surface frame path
    # is one coordinate-contract incident even when its points differ.
    for marker, category in (
        ("frame_coordinate_invalid", "frame_coordinate_invalid"),
        ("work_surface_fail_closed", "work_surface_fail_closed"),
        ("visual_preflight_failed", "visual_preflight_failed"),
        ("cooperative_input", "cooperative_input_scope"),
    ):
        if any_true(payload, marker):
            return category

    # Generic controller/policy contracts can explicitly say that no requested
    # action ran without adding one of the known markers above.  Preserve the
    # most specific boolean marker when possible, but never fingerprint prose.
    marker_candidates: set[str] = set()

    def collect_markers(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if child is True and (
                    key.endswith("_invalid")
                    or key.endswith("_fail_closed")
                    or key.endswith("_denied")
                    or key.endswith("_guard")
                ):
                    marker_candidates.add(str(key))
                collect_markers(child)
        elif isinstance(value, list):
            for child in value:
                collect_markers(child)

    collect_markers(payload)
    if marker_candidates:
        return sorted(marker_candidates)[0]
    return "unexecuted_policy_constraint"


def _requests_continuous_pointer_output(task: str) -> bool:
    """Return whether the user explicitly asks for freehand surface output.

    ``write`` by itself intentionally does not match because ordinary editor
    input should use the keyboard.  App/control names such as Paint, 画板, 画笔,
    and Sign in are also excluded: merely opening or clicking them is not a
    request to produce pointer strokes.
    """
    text = re.sub(r"\s+", " ", str(task or "")).strip().casefold()
    if not text:
        return False

    # Remove common negations and UI-label activations before looking for an
    # output verb.  This keeps "click Sign in" and "open Paint" out of a guard
    # that is deliberately narrower than general visual-task detection.
    text = re.sub(
        r"\b(?:do\s+not|don't|dont|never|without)\s+"
        r"(?:draw|sketch|doodle|trace|hand[ -]?write|write\s+by\s+hand|sign)\b",
        " ",
        text,
    )
    text = re.sub(
        r"\b(?:click|press|tap|select)\s+(?:the\s+)?"
        r"(?:(?:draw|sketch|trace|sign)\s+)?(?:button|menu|tab|tool)\b",
        " ",
        text,
    )
    text = re.sub(r"\bsign\s+(?:in|into|on|up|out)\b", " ", text)
    text = re.sub(
        r"\b(?:open|launch|start|run)\s+(?:microsoft\s+)?"
        r"(?:paint|a\s+drawing\s+app|the\s+drawing\s+app)\b",
        " ",
        text,
    )
    text = re.sub(
        r"(?:不要|别|无需|不用|停止)\s*"
        r"(?:画|绘制|绘画|涂鸦|手写|签名|签字|描线|描摹|临摹|勾勒)",
        " ",
        text,
    )
    text = re.sub(
        r"(?:打开|启动|运行|进入|切换到)\s*(?:画图|画板|绘图)(?:应用|程序)?",
        " ",
        text,
    )

    if re.search(r"\b(?:draw|sketch|doodle)(?:ing|n|ed)?\b", text):
        return True
    if re.search(
        r"\b(?:hand[ -]?write|write(?:\s+\w+){0,3}\s+by\s+hand|free[ -]?hand)\b",
        text,
    ):
        return True
    if re.search(
        r"\btrace\s+(?:over\s+)?"
        r"(?:(?:a|an|the|this|that|visible|shown|current)\s+){0,3}"
        r"(?:line|path|shape|outline|image|picture|letter|word|signature)\b",
        text,
    ):
        return True
    if re.search(
        r"\b(?:sign\s+(?:(?:my|your|the|this|that|a)\s+)?"
        r"(?:name|document|form|page|contract)|add\s+(?:my|your|a|the)\s+signature)\b",
        text,
    ):
        return True
    if re.search(r"(?:绘制|绘画|涂鸦|手写|签名|签字|描线|描摹|临摹|勾勒)", text):
        return True
    return bool(re.search(r"画(?!板|笔|面|布|家|廊|框|风|质|作)[\u4e00-\u9fff]", text))




def _clipboard_target_tokens(task: str) -> list[str]:
    """Return placeholder tokens requested as destinations, not protected literals."""
    text = str(task or "")
    targets: list[str] = []
    all_tokens = list(dict.fromkeys(re.findall(r"\[\[[^\]\r\n]{1,100}\]\]", text)))
    for token in all_tokens:
        position = text.find(token)
        left_boundary = max(
            text.rfind(marker, 0, position) for marker in (".", "。", ";", "；", "\n")
        )
        right_candidates = [
            index for marker in (".", "。", ";", "；", "\n")
            if (index := text.find(marker, position + len(token))) >= 0
        ]
        right_boundary = min(right_candidates) if right_candidates else len(text)
        clause = text[left_boundary + 1:right_boundary]
        mutates = bool(re.search(
            r"replace|paste|insert|fill|write|替换|粘贴|填入|写入",
            clause,
            re.IGNORECASE,
        ))
        protects = bool(re.search(
            r"keep|preserve|unchanged|do not (?:change|replace)|保留|保持|不变|不要(?:修改|替换)",
            clause,
            re.IGNORECASE,
        ))
        if mutates and not protects:
            targets.append(token)
    return targets or all_tokens


def _clipboard_fingerprints(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        fingerprint = value.get("clipboard_text_fingerprint")
        if isinstance(fingerprint, str) and fingerprint:
            found.add(fingerprint)
        for nested in value.values():
            found.update(_clipboard_fingerprints(nested))
    elif isinstance(value, list):
        for nested in value:
            found.update(_clipboard_fingerprints(nested))
    return found


def _client():
    from openai import OpenAI

    api = CONFIG.get("api", default={})
    if not api.get("api_key"):
        raise RuntimeError("config.json 缺少 api.api_key")
    return OpenAI(base_url=api["base_url"], api_key=api["api_key"], timeout=120)


def _main_model_has_vision() -> bool:
    """True when the configured main model natively supports image input."""
    api = CONFIG.get("api", default={})
    return model_profile_from_config(api).supports_vision


def _main_agent_api_settings() -> dict[str, Any]:
    """Return an isolated API config for the main loop adapter.

    Spatial reasoning is selected per decision in ``AgentRunner._chat``. It
    must not be baked into the adapter, otherwise planners, ordinary clicks,
    completion checks, and every post-action turn inherit the latency cost.
    """
    return dict(CONFIG.get("api", default={}) or {})


def _sanitize_assistant(
    msg,
    *,
    preserve_reasoning: bool = False,
) -> dict[str, Any]:
    """Convert an SDK assistant message into stable request history.

    Complete reasoning is retained only when the next request keeps the
    corresponding thinking/tool-call transcript.
    """
    content = msg.content or ""
    if len(content) > _MAX_ASSISTANT_HISTORY_CHARS:
        content = (
            content[:_MAX_ASSISTANT_HISTORY_CHARS]
            + "\n[assistant content truncated in history; full response is kept in the LLM trace]"
        )
    out: dict[str, Any] = {"role": "assistant", "content": content}
    if msg.tool_calls:
        out["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in msg.tool_calls
        ]
    reasoning = getattr(msg, "reasoning_content", None)
    if preserve_reasoning and isinstance(reasoning, str) and reasoning:
        out["reasoning_content"] = reasoning
    return out


class AgentRunner(ContextGuidanceMixin, ReflectionMixin, DoneVerifierMixin):
    """One task execution. Designed to run on a background thread."""

    def __init__(self, task: str, stop_event: threading.Event | None = None,
                 session_context: list | None = None,
                 max_steps_override: int | None = None,
                 runtime_profile: RuntimeProfile | None = None,
                 allow_shell_tools: bool | None = None) -> None:
        self.task = task
        self.run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.stop_event = stop_event or threading.Event()
        self.runtime_profile = runtime_profile or build_runtime_profile(task)
        self.session = ToolSession(
            stop_event=self.stop_event,
            task=task,
            runtime_profile=self.runtime_profile,
        )
        self.session.run_id = self.run_id
        # Prior finished tasks this session (list[SessionEntry]) for continuity.
        self.session_context = session_context or []
        self.messages: list[dict] = []
        self._foldable: list[tuple[int, str]] = []  # (message index, summary line)
        self._steps_log: list[str] = []             # action recipe for experience memory
        self._hint_ids: list[str] = []              # injected recipe ids (score feedback)
        self._injected_playbooks: set[str] = set()  # avoid double-injecting playbooks
        self._had_verified_action = False           # any action passed external verify
        self._last_verify_ok: bool | None = None    # most recent verify outcome
        self._done_rejects = 0                       # done-gate bounce count
        self._done_reject_terminal = False           # repeated claim with no new evidence
        self._done_unverified = False                # forced-accept after max rejects
        self._done_verified = False                  # done-gate judge genuinely passed
        self._fresh_done_image_b64 = ""              # authoritative frame for final judge
        self._fresh_done_capture_scope = ""
        self._fresh_done_capture_region = None
        self._fresh_done_capture_reason = ""
        self._pending_visual_literal_tokens: list[str] = []
        self._pending_visual_payloads: list[str] = []
        self._pending_visual_spatial_check = ""
        self._pending_visual_arithmetic_check = ""
        self._pending_visual_communication_check = ""
        self._saved_artifact_verified = False
        self._recent_results: list[tuple[str, str]] = []  # tool outputs as done-gate evidence
        self._visible_evidence_ledger: list[dict[str, Any]] = []
        self.session.visible_evidence_ledger = self._visible_evidence_ledger
        self._partial_output_guard = PartialOutputDestructiveGuard(task)
        self._mutation_ledger = self._partial_output_guard.ledger
        self.session.mutation_ledger = self._mutation_ledger
        self._last_done_reject_reason = ""
        self._last_done_reject_generation: tuple[int, int, int] | None = None
        self._escalate_next = False                  # use stronger model on next _chat
        self._consecutive_unverified = 0             # steps without a verified=true action
        self._last_reflect_step = 0                  # step number of last reflect injection
        self.status = "pending"                     # running/done/fail/reply/stopped/error
        self.result_text = ""
        self.usage_total = empty_usage_total()
        self._model_adapter = None
        self._last_llm_model = ""
        self._last_llm_thinking_enabled = False
        self._interjections: list[str] = []          # mid-run user hints
        self._interject_lock = threading.Lock()
        self._llm_trace_index = 0                    # local forensic trace files
        self.max_steps_override = max_steps_override
        self.allow_shell_tools = allow_shell_tools
        self.run_state = RunStateMachine()
        self.session.run_state = self.run_state
        # Coding is now an in-process runtime capability of this AgentRunner,
        # not a separate sub-agent / runner loop.
        self._is_coding = self.runtime_profile.coding
        self._tool_history: list[tuple[str, dict[str, Any], str]] = []
        self._completion_rejects = 0
        self._truncated_response_retries = 0
        self._gui_truncated_tool_retries = 0
        self._gui_tool_call_retry_pending = False
        self._failure_recovery_layer = FailureRecoveryLayer()
        self._started_wall_time = time.time()
        self._new_document_plan_state: dict[int, dict[str, Any]] = {}
        self._delivered_text_mutations: list[dict[str, Any]] = []
        self._applied_format_mutations: list[dict[str, Any]] = []
        self._tool_sequence = 0
        # Native-vision closed loop.  It is activated only by run(), so helper
        # unit tests and direct ToolSession callers keep their existing API.
        self._visual_closed_loop_active = False
        self._visual_frame_id = 0
        self._visual_frame_epoch = -1
        self._visual_frame_hwnd = 0
        self._visual_frame_region: tuple[int, int, int, int] | None = None
        self._visual_frame_scope = ""
        self._visual_frame_scope_reason = ""
        self._visual_work_surface_frame_bounds: tuple[int, int, int, int] | None = None
        self._visual_work_surface_frame_id = 0
        self._visual_work_surface_anchor_bounds: tuple[int, int, int, int] | None = None
        self._visual_work_surface_anchor_hwnd = 0
        self._visual_work_surface_anchor_region: tuple[int, int, int, int] | None = None
        self._visual_work_surface_anchor_scope = ""
        self._visual_work_surface_pending_bounds: tuple[int, int, int, int] | None = None
        self._visual_work_surface_pending_count = 0
        self._visual_work_surface_blocked_reason = ""
        self._visual_work_surface_blocked_frame_id = 0
        self._last_visual_feedback_b64 = ""
        self._last_visual_feedback_action = ""
        # Bounded, immutable post-action frames retained across app switches.
        # A multi-window task cannot be verified from the final foreground
        # window alone (for example, type in Notepad and then draw in Paint).
        self._historical_visual_checkpoints: list[dict[str, Any]] = []
        self._visual_grounding_block_count = 0
        self._native_visual_static_messages: list[dict[str, Any]] = []
        self._native_visual_user_directives: list[str] = []
        self._last_visual_change_ratio: float | None = None
        self._last_visual_change_comparable = False
        self._native_no_progress_signature = ""
        self._native_no_progress_streak = 0
        self._native_no_progress_block_count = 0
        self._continuous_pointer_setup_clicks = 0
        self._continuous_pointer_low_change_clicks = 0
        self._continuous_pointer_guard_blocks = 0
        self._continuous_pointer_gesture_progress = False
        self._continuous_pointer_last_change_ratio: float | None = None
        self._continuous_pointer_forward_only_correction = False
        self._continuous_pointer_done_repair_required = False
        self._continuous_pointer_text_setup_mutations = 0
        self._continuous_semantic_progress = ContinuousSemanticProgress()
        self._continuous_semantic_surface_region: tuple[float, float, float, float] | None = None
        self._continuous_semantic_surface_hwnd = 0
        self._constraint_rejection_category = ""
        self._constraint_rejection_streak = 0

    def interject(self, text: str) -> None:
        """Queue a user hint to inject before the agent's next decision."""
        text = (text or "").strip()
        if text:
            with self._interject_lock:
                self._interjections.append(text)

    def _drain_interjections(self) -> None:
        """Append any queued user hints as user messages (valid after tool
        results) so the model sees them before the next API call."""
        with self._interject_lock:
            pending = self._interjections
            self._interjections = []
        if pending:
            # A new human directive is a genuine change of decision context.
            # It may authorize a different route, so old controller-constraint
            # debt must not force-stop the newly directed attempt.
            self._reset_constraint_rejection_guard()
            self._continuous_pointer_forward_only_correction = False
            self._continuous_pointer_done_repair_required = False
        for text in pending:
            self._native_visual_user_directives.append(text)
            while (
                len(self._native_visual_user_directives) > 8
                or sum(len(item) for item in self._native_visual_user_directives)
                > _NATIVE_CONTEXT_MAX_DIRECTIVE_CHARS
            ):
                self._native_visual_user_directives.pop(0)
            self.messages.append({
                "role": "user",
                "content": f"[用户实时指示] {text}\n"
                           f"（用户在旁观察后给出的实时指示，请【优先、按字面】遵循；"
                           f"若与当前做法冲突，以用户指示为准。"
                           f"若用户表达停止/取消/不要这样做，就立即停下当前动作并据此调整，"
                           f"不要自行换一种方式硬继续原目标。）",
            })
            BUS.publish("chat", run_id=self.run_id, role="user", content=text,
                        interject=True)
            logger.info("收到用户插话: %s", text[:80])

    # ------------------------------------------------------------- tracing
    @staticmethod
    def _jsonable(obj: Any) -> Any:
        """Best-effort conversion for OpenAI SDK objects into JSON data."""
        if obj is None or isinstance(obj, (str, int, float, bool)):
            return obj
        if isinstance(obj, dict):
            return {str(k): AgentRunner._jsonable(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [AgentRunner._jsonable(v) for v in obj]
        if hasattr(obj, "model_dump"):
            try:
                return AgentRunner._jsonable(obj.model_dump())
            except Exception:
                pass
        if hasattr(obj, "dict"):
            try:
                return AgentRunner._jsonable(obj.dict())
            except Exception:
                pass
        return str(obj)

    def _trace_debug(self, kind: str, payload: dict[str, Any]) -> None:
        """Write exact per-step forensic data to traces/<run_id>/.

        This is intentionally file-only, not a BUS event, so the web UI does
        not receive huge prompt payloads. It lets us reproduce what the LLM saw
        and why it made each decision after a failed desktop run.
        """
        if not CONFIG.get("agent", "llm_trace", default=True):
            return
        try:
            self._llm_trace_index += 1
            run_dir = TRACES_DIR / self.run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            safe_kind = re.sub(r"[^a-zA-Z0-9_.-]+", "_", kind).strip("_") or "event"
            path = run_dir / f"{self._llm_trace_index:03d}_{safe_kind}.json"
            serializable = self._jsonable(payload)
            trace_payload = sanitize_trace_payload(
                serializable,
                frame_id=int(getattr(self, "_visual_frame_id", 0) or 0) or None,
            )
            path.write_text(
                json.dumps(trace_payload, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.debug("LLM trace write failed: %s", exc)

    # ------------------------------------------------------------- history
    def _native_visual_context_enabled(self) -> bool:
        return bool(
            self._visual_closed_loop_active
            and not self._is_coding
            and _main_model_has_vision()
        )

    @staticmethod
    def _message_has_inline_image(message: dict[str, Any]) -> bool:
        content = message.get("content")
        return bool(
            isinstance(content, list)
            and any(
                isinstance(part, dict)
                and str(part.get("type") or "") in {"image_url", "input_image"}
                for part in content
            )
        )

    def _freeze_native_visual_static_context(self) -> None:
        """Snapshot the immutable prompt prefix before GUI transactions start."""
        if self._native_visual_static_messages:
            return
        self._native_visual_static_messages = [
            dict(message)
            for message in self.messages
            if message.get("role") in {"system", "user"}
            and not self._message_has_inline_image(message)
        ]

    @staticmethod
    def _native_action_args_preview(name: str, args: dict[str, Any]) -> str:
        useful = (
            "frame_x", "frame_y", "frame_x1", "frame_y1", "frame_x2", "frame_y2",
            "element_id", "label", "fallback_text", "direction", "amount", "keys",
            "name", "title", "subgoal", "index", "operation",
        )
        compact = {key: args[key] for key in useful if key in args}
        if "text" in args:
            text = str(args.get("text") or "")
            compact["text"] = text[:80]
            if len(text) > 80:
                compact["text_chars"] = len(text)
        if name == "draw_strokes":
            source = args.get("source_frame_coordinates")
            strokes = (
                args.get("frame_strokes")
                or args.get("normalized_strokes")
                or (
                    source.get("frame_strokes")
                    if isinstance(source, dict)
                    else None
                )
                or args.get("strokes")
                or []
            )
            compact["stroke_count"] = len(strokes) if isinstance(strokes, list) else 0
            compact["point_count"] = (
                sum(len(stroke) for stroke in strokes if isinstance(stroke, list))
                if isinstance(strokes, list)
                else 0
            )
        if name == "run_action_batch":
            actions = args.get("actions") or []
            compact["actions"] = [
                str(item.get("action") or item.get("tool") or "")
                for item in actions[:6] if isinstance(item, dict)
            ] if isinstance(actions, list) else []
        try:
            return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))[:420]
        except (TypeError, ValueError):
            return "{}"

    @staticmethod
    def _native_action_result_preview(result_text: str) -> str:
        try:
            payload = json.loads(result_text or "{}")
        except (json.JSONDecodeError, TypeError):
            return re.sub(r"\s+", " ", str(result_text or "")).strip()[:220]
        if not isinstance(payload, dict):
            return str(payload)[:220]
        keys = (
            "ok", "verified", "blocked", "native_action_executed",
            "visual_change_detected", "visual_change_ratio", "no_visual_progress",
            "error", "fast_path_advice",
        )
        compact = {key: payload[key] for key in keys if key in payload}
        auto = payload.get("auto_visual_verify")
        if (
            isinstance(auto, dict)
            and auto.get("summary_kind") == "pixel_effect_only"
            and auto.get("semantic_verified") is False
            and auto.get("completion_evidence") is False
        ):
            compact.pop("verified", None)
            compact.update({
                "action_verified": payload.get("verified"),
                "semantic_verified": False,
                "completion_evidence": False,
                "summary_kind": "pixel_effect_only",
            })
        try:
            return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))[:420]
        except (TypeError, ValueError):
            return "{}"

    @staticmethod
    def _prebind_result_for_model(result_text: str, *, native_vision: bool) -> str:
        """Return the target-binding facts without a contradictory observe request.

        ``open_app`` is shared with the text controller, where ``needs_observe``
        remains a useful state-machine signal.  Native prebind immediately adds
        a fresh authoritative frame, so forwarding that historical signal (or
        its ``Observe it once`` advice) only induces a redundant perception
        turn.  Keep the original ToolResult for runtime bookkeeping and remove
        the conflict only from model-visible prebind/history text.
        """
        if not native_vision:
            return str(result_text or "")[:800]
        try:
            payload = json.loads(result_text or "{}")
        except (json.JSONDecodeError, TypeError):
            return re.sub(r"\s+", " ", str(result_text or "")).strip()[:800]
        if not isinstance(payload, dict):
            return str(payload)[:800]
        keys = (
            "ok", "verified", "route", "app", "target_hwnd", "title",
            "blank_document_ready", "document_surface_ready", "workspace_stage",
            "needs_llm_reason", "error",
        )
        compact = {key: payload[key] for key in keys if key in payload}
        compact["fresh_authoritative_frame_pending"] = True
        try:
            return json.dumps(compact, ensure_ascii=False, default=str)[:800]
        except (TypeError, ValueError):
            return "{}"

    def _native_transaction_summary(self) -> str:
        lines = [
            "[Native visual rolling transaction context]",
            "Old assistant tool calls and tool results were intentionally discarded. "
            "Use the newest authoritative frame below plus this bounded state summary.",
        ]
        plan = getattr(self.session, "plan", None)
        if plan is not None:
            try:
                rendered = re.sub(r"\s+", " ", str(plan.render() or "")).strip()
            except Exception:
                rendered = ""
            if rendered:
                lines.append(f"Current mutable plan: {rendered[:1200]}")
        # Count only the latest uninterrupted draw sequence. A later click,
        # hotkey, app switch, or other visual mutation may have opened a new
        # document/surface even when the HWND stayed the same; older drawings
        # must not constrain the current pixels.
        committed_compositions = 0
        for name, _args, result_text in reversed(self._tool_history):
            if name != "draw_strokes":
                if name in _VISUAL_MUTATION_TOOLS:
                    break
                continue
            try:
                payload = json.loads(result_text or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            if (
                isinstance(payload, dict)
                and payload.get("ok") is True
                and payload.get("native_action_executed") is True
                and payload.get("visual_change_detected") is True
            ):
                committed_compositions += 1
        if committed_compositions:
            lines.append(
                "Continuous-output state: "
                f"{committed_compositions} pixel-changing draw transaction(s) are already "
                "committed in the latest uninterrupted surface sequence. Judge the whole visible "
                "result now. If it satisfies the request, call done; otherwise change only what "
                "the current pixels justify and require measurable progress after each mutation. "
                "Do not add an alternative whole layout, unsolicited scaffolding, edge-reaching "
                "strokes, or unrelated menu actions."
            )
        if self._continuous_pointer_forward_only_correction:
            lines.append(
                "Forward-correction mode: an autonomous destructive rollback was already "
                "blocked after visible output. Do not request another undo/reset/clear. "
                "Use a compact local draw/drag correction grounded in the newest frame, or "
                "call done if the visible result now meets the task."
            )
        if self._continuous_pointer_done_repair_required:
            lines.append(
                "Completion repair required: the visual done gate rejected the current "
                "result. Do not repeat done or spend turns on setup clicks/undo. Make one "
                "measurable forward draw/drag correction grounded in the newest frame; "
                "done becomes available again after its pixels visibly change."
            )
        recent = self._tool_history[-_NATIVE_CONTEXT_RECENT_ACTIONS:]
        if recent:
            lines.append("Recent transactions (oldest to newest):")
            for name, args, result_text in recent:
                lines.append(
                    f"- {name} {self._native_action_args_preview(name, args)} -> "
                    f"{self._native_action_result_preview(result_text)}"
                )
        if self._last_visual_change_comparable:
            ratio = float(self._last_visual_change_ratio or 0.0)
            lines.append(f"Latest same-region post-action pixel change ratio: {ratio:.6f}.")
        if self._native_no_progress_streak:
            lines.append(
                "Runtime no-progress state: "
                f"same spatial action low-change streak={self._native_no_progress_streak}; "
                "do not claim success from execution alone."
            )
        if self._constraint_rejection_streak:
            lines.append(
                "Runtime unexecuted-constraint state: "
                f"category={self._constraint_rejection_category}, "
                f"consecutive_rejections={self._constraint_rejection_streak}/"
                f"{_UNEXECUTED_CONSTRAINT_MAX_REJECTIONS}; choose a materially different "
                "grounded strategy instead of changing only the rejected arguments."
            )
        return "\n".join(lines)[:_NATIVE_CONTEXT_MAX_SUMMARY_CHARS]

    def _capture_settled_initial_frame(self, hwnd: int, region: Any) -> str:
        """Take the newest of a short, stability-bounded startup sample.

        A newly focused application can still be painting its toolbar or
        resizing its client area when ``open_app`` returns.  Giving the model
        that transient image makes the later preflight correctly cancel its
        first action, but wastes a whole model turn.  Sample only after an
        internal target prebind, stop on the first stable pair, and cap both
        time and capture count so ordinary launches pay about one interval.
        """
        current = capture_window_jpeg_b64(hwnd, region=region)
        if not current:
            return ""
        captures = 1
        stable = False
        stable_pairs = 0
        last_ratio: float | None = None
        settle_started = time.monotonic()
        deadline = settle_started + _INITIAL_FRAME_SETTLE_MAX_S
        threshold = max(0.0, float(CONFIG.get(
            "controller", "visual_preflight_change_ratio", default=0.0025,
        )))
        while captures < _INITIAL_FRAME_SETTLE_MAX_CAPTURES:
            if self.stop_event.is_set():
                break
            remaining = deadline - time.monotonic()
            if remaining < _INITIAL_FRAME_SETTLE_INTERVAL_S:
                break
            time.sleep(_INITIAL_FRAME_SETTLE_INTERVAL_S)
            try:
                candidate = capture_window_jpeg_b64(hwnd, region=region)
            except Exception:
                break
            if not candidate:
                break
            captures += 1
            try:
                ratio = jpeg_diff_ratio(current, candidate)
            except Exception:
                ratio = None
            current = candidate
            if ratio is None:
                continue
            last_ratio = float(ratio)
            if last_ratio <= threshold:
                stable_pairs += 1
            else:
                stable_pairs = 0
            if (
                stable_pairs >= _INITIAL_FRAME_SETTLE_STABLE_PAIRS
                and (captures - 1) * _INITIAL_FRAME_SETTLE_INTERVAL_S
                >= _INITIAL_FRAME_SETTLE_MIN_S
            ):
                stable = True
                break
        BUS.publish(
            "initial_visual_settle",
            run_id=self.run_id,
            captures=captures,
            stable=stable,
            stable_pairs=stable_pairs,
            last_change_ratio=(round(last_ratio, 6) if last_ratio is not None else None),
            threshold=threshold,
            min_wait_ms=int(_INITIAL_FRAME_SETTLE_MIN_S * 1000),
            max_wait_ms=int(_INITIAL_FRAME_SETTLE_MAX_S * 1000),
        )
        return current

    def _roll_native_visual_context(self) -> None:
        """Replace transcript history with a bounded, valid visual transaction.

        Function-call history is removed as complete assistant/tool pairs.  The
        next OpenAI request therefore never contains an orphan tool message,
        while the immutable instructions, current plan/actions, live user
        directives, and newest authoritative pixels remain available.
        """
        if not self._native_visual_context_enabled():
            return
        self._freeze_native_visual_static_context()
        static = [dict(message) for message in self._native_visual_static_messages]
        if not static:
            static = [
                dict(message) for message in self.messages
                if message.get("role") == "system"
            ][:1]

        latest_frame = next(
            (
                message for message in reversed(self.messages)
                if self._message_has_inline_image(message)
            ),
            None,
        )
        static_text = {
            str(message.get("content") or "")
            for message in static if isinstance(message.get("content"), str)
        }
        recent_guidance: list[str] = []
        for message in self.messages:
            content = message.get("content")
            if message.get("role") != "user" or not isinstance(content, str):
                continue
            if content in static_text or content.startswith(
                "[Native visual rolling transaction context]"
            ) or content.startswith("[Recent runtime guidance]") or content.startswith(
                "[Live user instructions; authoritative for all later turns]"
            ) or content.startswith("[folded context checkpoint]"):
                continue
            if any(directive and directive in content for directive in self._native_visual_user_directives):
                continue
            recent_guidance.append(re.sub(r"\s+", " ", content).strip()[:700])
        recent_guidance = [item for item in recent_guidance if item][
            -_NATIVE_CONTEXT_RECENT_GUIDANCE:
        ]

        rolled = static
        if self._native_visual_user_directives:
            rolled.append({
                "role": "user",
                "content": (
                    "[Live user instructions; authoritative for all later turns]\n"
                    + "\n".join(
                        f"- {text}" for text in self._native_visual_user_directives
                    )
                )[:_NATIVE_CONTEXT_MAX_DIRECTIVE_CHARS],
            })
        if recent_guidance:
            rolled.append({
                "role": "user",
                "content": "[Recent runtime guidance]\n" + "\n".join(
                    f"- {item}" for item in recent_guidance
                ),
            })
        rolled.append({"role": "user", "content": self._native_transaction_summary()})
        if latest_frame is not None:
            rolled.append(latest_frame)
        self.messages = rolled
        self._foldable = (
            [(len(self.messages) - 1, "[folded screenshot]")]
            if latest_frame is not None else []
        )

    def _model_tools_for_step(
        self,
        tools: list[dict[str, Any]],
        step: int,
    ) -> list[dict[str, Any]]:
        """Avoid a duplicate structured observe on the first native frame."""
        selected = tools
        if (
            step == 1
            and self._native_visual_context_enabled()
            and self._has_fresh_visual_grounding()
        ):
            selected = [
                item for item in selected
                if str(item.get("function", {}).get("name") or "") != "observe"
            ]
        if (
            self._continuous_pointer_forward_only_correction
            and self._continuous_pointer_output_requested()
        ):
            selected = [
                item for item in selected
                if str(item.get("function", {}).get("name") or "") != "hotkey"
            ]
        if (
            self._continuous_pointer_done_repair_required
            and self._continuous_pointer_output_requested()
        ):
            forward_names = {
                "draw_strokes", "drag", "observe", "focus_window", "list_windows",
                "blocked", "revise_plan", "complete_subgoal",
            }
            selected = [
                item for item in selected
                if str(item.get("function", {}).get("name") or "") in forward_names
            ]
        return selected

    def _append_tool_result(self, tool_call_id: str, result_text: str,
                            summary: str = "",
                            image_b64: str = "",
                            tool_name: str = "") -> None:
        # NOTE: mime API does not support multipart tool results (image_url in tool role).
        # Always use plain text; screenshot is injected as a separate user message via
        # _inject_observe_screenshot when image_b64 is empty (fallback path).
        content = result_text
        if tool_name and tool_name not in {"observe", "read_text"}:
            content = self._compact_tool_evidence(tool_name, result_text)
        self.messages.append(
            {"role": "tool", "tool_call_id": tool_call_id, "content": content})
        if summary:
            self._foldable.append((len(self.messages) - 1, summary))
            self._fold_old()

    @staticmethod
    def _folded_tool_evidence_preview(result_text: str, *, limit: int = 520) -> str:
        """Keep the conclusion of an old tool result, never its coordinate bulk."""
        try:
            data = json.loads(result_text or "{}")
        except (json.JSONDecodeError, TypeError):
            return re.sub(r"\s+", " ", str(result_text or "")).strip()[:limit]
        if not isinstance(data, dict):
            return re.sub(r"\s+", " ", str(result_text or "")).strip()[:limit]
        keys = (
            "ok",
            "verified",
            "blocked",
            "mutation_applied",
            "destructive_action",
            "partial_output_destructive_guard",
            "protected_mutation",
            "guard_blocks",
            "safe_stop",
            "drawing_scene_replacement_blocked",
            "destructive_clear_blocked",
            "destructive_restart_blocked",
            "destructive_replan_blocked",
            "destructive_bulk_undo_blocked",
            "drawing_quality_repair_blocked",
            "drawing_quality_repair_limit_reached",
            "drawing_quality_repair_used",
            "drawing_quality_rejects",
            "structured_scene_verified",
            "matched_visual_colors",
            "missing_visual_colors",
            "optional_missing_visual_colors",
            "effective_visual_colors",
            "visible_colors",
            "error",
            "warning",
            "fast_path_advice",
            "scene_structure",
            "draw_actions_summary",
        )
        return AgentRunner._bounded_compact_json(
            [(key, data.get(key)) for key in keys if key in data],
            limit=limit,
        )

    def _fold_checkpoint(self, folded_summary: str) -> str:
        """Compact state that survives when an old bulky tool result is folded."""
        parts = ["[folded context checkpoint]", f"goal: {self.task[:200]}"]
        snapshot = self.session.snapshot
        if snapshot is not None:
            elements = getattr(snapshot, "elements", []) or []
            parts.append(
                f'screen: title="{getattr(snapshot, "title", "")[:80]}" '
                f'hwnd={getattr(snapshot, "hwnd", "")} elements={len(elements)}'
            )
        state = self.run_state.to_dict()
        if state.get("phase") and state.get("phase") != "idle":
            issue = str(state.get("last_issue") or "")[:180]
            parts.append(
                f'run_state: phase={state.get("phase")} '
                f'last_tool={state.get("last_tool")} issue="{issue}"'
            )
        if self.session.world_enabled:
            world_state = self.session.world.state()
            if world_state.get("last_change") or world_state.get("screen_watch"):
                parts.append(
                    "world: "
                    f'last_change="{str(world_state.get("last_change") or "")[:180]}" '
                    f'repeated={world_state.get("repeated")} '
                    f'identical_observes={world_state.get("identical_observes")}'
                )
        if self._recent_results:
            parts.append("recent_tool_evidence:")
            for name, text in reversed(self._recent_results[-3:]):
                one_line = self._folded_tool_evidence_preview(str(text), limit=520)
                parts.append(f"- {name}: {one_line}")
        parts.append(f"folded_item: {folded_summary}")
        return "\n".join(parts)[:1800]

    def _fold_old(self) -> None:
        """Compress old bulky observations — but ONLY when the context is large.

        Folding mutates a historical message, which breaks DeepSeek's prefix
        cache from that point once. So we avoid it entirely while the context
        is small (the common case → near-100% cache hits), and only start
        folding the oldest observations once total size crosses a threshold.
        The newest _KEEP_FULL_OBSERVATIONS are always kept full. Inline visual
        frames are stricter: the closed loop only reasons from the current
        frame, so older image parts are folded immediately even below the text
        threshold instead of being resent on every later model request.
        """
        def has_inline_frame(index: int) -> bool:
            content = self.messages[index].get("content") or ""
            return bool(
                isinstance(content, list)
                and any(
                    isinstance(part, dict)
                    and str(part.get("type") or "") in {"image_url", "input_image"}
                    for part in content
                )
            )

        visual_items = [
            (idx, summary)
            for idx, summary in self._foldable
            if has_inline_frame(idx)
        ]
        fold_visual = visual_items[:-_KEEP_FULL_OBSERVATIONS]
        if fold_visual:
            folded_indices = {idx for idx, _summary in fold_visual}
            for idx, summary in fold_visual:
                self.messages[idx]["content"] = self._fold_checkpoint(summary)
            self._foldable = [
                item for item in self._foldable if item[0] not in folded_indices
            ]

        def total_chars() -> int:
            total = 0
            for m in self.messages:
                c = m.get("content") or ""
                total += len(c) if isinstance(c, str) else sum(
                    len(str(part)) for part in c
                )
            return total

        while (
            len(self._foldable) > _KEEP_FULL_OBSERVATIONS
            and total_chars() > int(CONFIG.get(
                "agent", "context_fold_threshold_chars",
                default=_CONTEXT_FOLD_THRESHOLD_CHARS,
            ))
        ):
            idx, summary = self._foldable.pop(0)
            content = self.messages[idx]["content"]
            already_folded = (
                isinstance(content, str)
                and (
                    content.startswith("[已折叠]")
                    or content.startswith("[folded context checkpoint]")
                )
            )
            if not already_folded:
                self.messages[idx]["content"] = self._fold_checkpoint(summary)

    @staticmethod
    def _native_spatial_action_signature(
        tool_name: str,
        args: dict[str, Any],
        *,
        frame_region: tuple[int, int, int, int] | None = None,
    ) -> str:
        """Stable coarse identity for one model-grounded spatial mutation."""
        name = str(tool_name or "").strip().casefold()
        payload = dict(args or {})
        if name == "run_action_batch":
            spatial: list[tuple[str, dict[str, Any]]] = []
            for item in payload.get("actions") or []:
                if not isinstance(item, dict):
                    continue
                child_name = str(item.get("action") or item.get("tool") or "").casefold()
                if not child_name and len(item) == 1:
                    child_name = str(next(iter(item))).casefold()
                if child_name not in _NATIVE_STAGNATION_SPATIAL_TOOLS - {"run_action_batch"}:
                    continue
                child_args = dict(item.get("params") or {})
                child_args.update({
                    key: value for key, value in item.items()
                    if key not in {"action", "tool", "params"}
                })
                spatial.append((child_name, child_args))
            if len(spatial) != 1:
                return ""
            name, payload = spatial[0]
        if name not in _NATIVE_STAGNATION_SPATIAL_TOOLS - {"run_action_batch"}:
            return ""

        source = payload.get("source_frame_coordinates")
        if isinstance(source, dict):
            payload = {**payload, **source}

        def normalize(value: Any, key: str = "") -> Any:
            if isinstance(value, dict):
                return {
                    str(child_key): normalize(child, str(child_key))
                    for child_key, child in sorted(value.items())
                }
            if isinstance(value, (list, tuple)):
                return [normalize(child, key) for child in value]
            if isinstance(value, (int, float)) and (
                key.startswith("frame_") or key in {"x", "y", "x1", "y1", "x2", "y2"}
            ):
                # Small coordinate jitter should not bypass the repeat guard.
                return int(round(float(value) / 25.0) * 25)
            if isinstance(value, str):
                return value.strip().casefold()[:160]
            return value if isinstance(value, (int, float, bool)) or value is None else str(value)[:160]

        keys = (
            "frame_x", "frame_y", "frame_x1", "frame_y1", "frame_x2", "frame_y2",
            "frame_points", "frame_strokes", "normalized_strokes", "element_id", "label",
            "fallback_text", "role", "expected_region", "direction", "amount", "name",
        )
        canonical = {
            "tool": name,
            "frame_region": list(frame_region or ()),
            "target": {
                key: normalize(payload[key], key) for key in keys if key in payload
            },
        }
        if not canonical["target"]:
            return ""
        encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return f"{name}:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:16]}"

    @staticmethod
    def _native_no_progress_threshold() -> float:
        value = float(CONFIG.get(
            "controller", "native_no_progress_change_ratio",
            default=_NATIVE_NO_PROGRESS_RATIO,
        ) or _NATIVE_NO_PROGRESS_RATIO)
        return max(0.0, min(0.05, value))

    def _continuous_pointer_output_requested(self) -> bool:
        directives = "\n".join(self._native_visual_user_directives[-4:])
        return _requests_continuous_pointer_output(
            f"{self.task}\n{directives}" if directives else self.task
        )

    def _native_visual_thinking_mode_for_decision(
        self,
        model: str,
    ) -> str | None:
        """Select MiMo reasoning only for unresolved continuous geometry.

        This is task-shape routing, not an application or drawing special case:
        freehand writing, signatures, tracing, canvas paths, and similar
        continuous pointer output all use the same gate. A rejected proposal
        keeps reasoning enabled because no gesture progress was recorded. Once
        a real drag/stroke changes comparable pixels, later inspection and
        completion turns switch it off.
        """
        if not bool(CONFIG.get(
            "controller", "native_visual_thinking", default=False,
        )):
            return None
        api = dict(CONFIG.get("api", default={}) or {})
        api["model"] = str(model or api.get("model") or "")
        profile = model_profile_from_config(api)
        if not (
            profile.provider == "mimo"
            and profile.supports_vision
            and self._native_visual_context_enabled()
            and self._continuous_pointer_output_requested()
        ):
            return None
        return (
            "disabled"
            if self._continuous_pointer_gesture_progress
            else "enabled"
        )

    def _continuous_semantic_progress_active(self) -> bool:
        return bool(
            not _main_model_has_vision()
            and self._continuous_pointer_output_requested()
        )

    @staticmethod
    def _continuous_semantic_action_payloads(
        tool_name: str,
        args: dict[str, Any],
    ) -> list[tuple[str, dict[str, Any]]]:
        """Return direct and controller-nested continuous path actions."""
        root = str(tool_name or "").strip().casefold().replace("-", "_")
        found: list[tuple[str, dict[str, Any]]] = []
        if root in {"drag", "draw_strokes"}:
            found.append((root, dict(args or {})))

        if root in {"run_action_batch", "execute_subgoal"}:
            for child in (args or {}).get("actions") or []:
                if not isinstance(child, dict):
                    continue
                name = str(
                    child.get("action") or child.get("tool") or child.get("name") or ""
                ).strip().casefold().replace("-", "_")
                payload: dict[str, Any]
                if not name and len(child) == 1:
                    name = str(next(iter(child))).strip().casefold().replace("-", "_")
                    nested = next(iter(child.values()))
                    payload = dict(nested) if isinstance(nested, dict) else {}
                else:
                    payload = (
                        dict(child.get("params") or {})
                        if isinstance(child.get("params"), dict)
                        else {}
                    )
                    payload.update({
                        key: value for key, value in child.items()
                        if key not in {"action", "tool", "name", "params"}
                    })
                if name in {"drag", "draw_strokes"}:
                    found.append((name, payload))

        if root == "execute_subgoal":
            for key, name in (("draw", "draw_strokes"), ("drag", "drag")):
                value = (args or {}).get(key)
                if isinstance(value, dict):
                    found.append((name, dict(value)))
            for key, name in (("draws", "draw_strokes"), ("drags", "drag")):
                for value in (args or {}).get(key) or []:
                    if isinstance(value, dict):
                        found.append((name, dict(value)))
        return found

    def _continuous_semantic_physical_surface(
        self,
    ) -> tuple[float, float, float, float]:
        hwnd = int(getattr(self.session, "hwnd", 0) or 0)
        if (
            self._continuous_semantic_surface_region is not None
            and self._continuous_semantic_surface_hwnd == hwnd
        ):
            return self._continuous_semantic_surface_region
        if hwnd:
            try:
                region = window_region(hwnd)
                return (
                    float(region.left), float(region.top),
                    float(region.width), float(region.height),
                )
            except Exception:
                pass
        try:
            region = virtual_screen_region()
            return (
                float(region.left), float(region.top),
                float(region.width), float(region.height),
            )
        except Exception:
            return 0.0, 0.0, 1000.0, 1000.0

    @staticmethod
    def _continuous_semantic_normalize_physical_paths(
        paths: list[list[list[float]]],
        surface: tuple[float, float, float, float],
    ) -> list[list[list[float]]]:
        left, top, width, height = surface
        if width <= 0 or height <= 0:
            return []
        return [
            [
                [
                    (float(point[0]) - left) * 1000.0 / width,
                    (float(point[1]) - top) * 1000.0 / height,
                ]
                for point in path
            ]
            for path in paths
        ]

    def _continuous_semantic_geometry(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> GeometryFingerprint | None:
        if not self._continuous_semantic_progress_active():
            return None
        normalized: list[list[list[float]]] = []
        physical: list[list[list[float]]] = []
        for name, payload in self._continuous_semantic_action_payloads(tool_name, args):
            if name == "draw_strokes":
                raw = payload.get("normalized_strokes")
                if raw is None:
                    raw = payload.get("frame_strokes")
                destination = normalized
                if raw is None:
                    raw = payload.get("strokes")
                    destination = physical
                if isinstance(raw, list) and raw:
                    if not all(isinstance(path, list) for path in raw):
                        return None
                    destination.extend(raw)
                elif payload.get("shape") and payload.get("box") is not None:
                    normalized.extend(structured_primitive_paths(payload))
                continue

            frame_values = (
                payload.get("frame_x1"), payload.get("frame_y1"),
                payload.get("frame_x2"), payload.get("frame_y2"),
            )
            physical_values = (
                payload.get("x1"), payload.get("y1"),
                payload.get("x2"), payload.get("y2"),
            )
            if all(isinstance(value, (int, float)) and not isinstance(value, bool)
                   for value in frame_values):
                x1, y1, x2, y2 = frame_values
                points = payload.get("frame_points") or []
                normalized.append([[x1, y1], *points, [x2, y2]])
            elif all(isinstance(value, (int, float)) and not isinstance(value, bool)
                     for value in physical_values):
                x1, y1, x2, y2 = physical_values
                points = payload.get("points") or []
                physical.append([[x1, y1], *points, [x2, y2]])

        if physical:
            try:
                normalized.extend(self._continuous_semantic_normalize_physical_paths(
                    physical,
                    self._continuous_semantic_physical_surface(),
                ))
            except (IndexError, TypeError, ValueError):
                return None
        if not normalized:
            return None
        try:
            return fingerprint_geometry(normalized)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _continuous_semantic_surface_from_payload(
        value: Any,
    ) -> tuple[float, float, float, float] | None:
        if isinstance(value, dict):
            raw = value.get("surface_region")
            if isinstance(raw, dict):
                try:
                    left = float(raw.get("x", raw.get("left")))
                    top = float(raw.get("y", raw.get("top")))
                    width = float(raw.get("width"))
                    height = float(raw.get("height"))
                    if width > 0 and height > 0:
                        return left, top, width, height
                except (TypeError, ValueError):
                    pass
            for child in value.values():
                found = AgentRunner._continuous_semantic_surface_from_payload(child)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = AgentRunner._continuous_semantic_surface_from_payload(child)
                if found is not None:
                    return found
        return None

    def _continuous_semantic_target(
        self,
        surface: tuple[float, float, float, float] | None = None,
    ) -> TargetKey:
        hwnd = int(getattr(self.session, "hwnd", 0) or 0)
        if self._continuous_semantic_surface_hwnd != hwnd:
            self._continuous_semantic_surface_region = None
            self._continuous_semantic_surface_hwnd = hwnd
        if surface is not None:
            self._continuous_semantic_surface_region = surface
        return TargetKey(
            identity=f"hwnd:{hwnd}" if hwnd else "unbound-target",
            region=self._continuous_semantic_surface_region,
        )

    def _continuous_semantic_progress_guard(
        self,
        tool_name: str,
        args: dict[str, Any],
        geometry: GeometryFingerprint | None,
    ) -> dict[str, Any] | None:
        if geometry is None or not self._continuous_semantic_progress_active():
            return None
        decision = self._continuous_semantic_progress.evaluate_candidate(
            target=self._continuous_semantic_target(),
            geometry=geometry,
        )
        if decision.allowed:
            return None
        payload = decision.to_dict()
        fresh_required = decision.reason == "fresh_semantic_required"
        payload.update({
            "ok": False,
            "verified": False,
            "blocked": True,
            "pixel_effect_only": True,
            "materially_different_geometry_required": not fresh_required,
            "fresh_semantic_required": fresh_required,
            "strategy_change_required": True,
            "needs_replan": not fresh_required,
            "error": (
                "A materially different continuous surface mutation changed pixels, but no "
                "fresh semantic observation followed it. This mutation was not executed. "
                "Use vlm_describe or vlm_verify on the current target before another mutation."
                if fresh_required else
                "Fresh semantic observations remained materially unchanged across successful "
                "pixel-changing continuous mutations, and this candidate repeats prior path "
                "geometry. It was not executed. Use materially different geometry or a genuinely "
                "different controller route, then obtain fresh semantic evidence."
            ),
        })
        return payload

    @staticmethod
    def _continuous_semantic_description_ok(text: str) -> bool:
        normalized = str(text or "").strip().casefold()
        if not normalized:
            return False
        return not (
            "vlm" in normalized
            and any(marker in normalized for marker in (
                "failed", "failure", "error", "not configured", "unavailable",
                "未配置", "失败", "不可用",
            ))
        )

    def _record_continuous_semantic_result(
        self,
        tool_name: str,
        result_text: str,
        *,
        geometry: GeometryFingerprint | None,
        dispatched: bool,
        terminal: str | None,
    ) -> None:
        if not self._continuous_semantic_progress_active():
            return
        name = str(tool_name or "").strip().casefold()
        if name == "vlm_describe":
            self._continuous_semantic_progress.record_description(
                result_text,
                target=self._continuous_semantic_target(),
                ok=bool(
                    dispatched
                    and terminal not in {"fail", "error", "stopped"}
                    and self._continuous_semantic_description_ok(result_text)
                ),
            )
            return

        try:
            payload = json.loads(result_text or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if name == "vlm_verify":
            method = str(payload.get("verify_method") or "").casefold()
            self._continuous_semantic_progress.record_verification(
                target=self._continuous_semantic_target(),
                ok=bool(dispatched and payload.get("ok") is True and method == "semantic-vlm"),
                passed=bool(
                    payload.get("verified") is True
                    and payload.get("semantic_visual_verified") is True
                ),
                confidence=payload.get("verify_confidence"),
                minimum_confidence=payload.get("verify_min_confidence", 0.8),
                details=payload.get("verdict") or {},
            )
            return

        if geometry is None:
            return
        surface = self._continuous_semantic_surface_from_payload(payload)
        target = self._continuous_semantic_target(surface)
        self._continuous_semantic_progress.record_effect(
            target=target,
            geometry=geometry,
            applied=bool(dispatched and structured_effect_applied(payload)),
        )

    @staticmethod
    def _native_single_spatial_action_kind(
        tool_name: str,
        args: dict[str, Any],
    ) -> str:
        """Return the one spatial child relevant to pointer-output guarding."""
        name = str(tool_name or "").strip().casefold()
        if name != "run_action_batch":
            return name
        spatial: list[str] = []
        for item in (args or {}).get("actions") or []:
            if not isinstance(item, dict):
                continue
            child = str(item.get("action") or item.get("tool") or "").strip().casefold()
            if not child and len(item) == 1:
                child = str(next(iter(item))).strip().casefold()
            if child in _NATIVE_STAGNATION_SPATIAL_TOOLS - {"run_action_batch"}:
                spatial.append(child)
        return spatial[0] if len(spatial) == 1 else ""

    def _record_continuous_pointer_progress(
        self,
        action_signature: str,
        change_ratio: float | None,
        *,
        comparable: bool,
    ) -> None:
        """Track setup clicks separately from visible continuous gestures.

        Whole-frame changes caused by a toolbar, menu, or brush picker are not
        trusted as subject progress.  Consequently a click always consumes the
        small setup allowance.  A drag/stroke only releases the tool-choice
        guard when the same capture region shows measurable pixel change; this
        is runtime routing evidence, never semantic task-completion evidence.
        """
        if not self._continuous_pointer_output_requested() or not action_signature:
            return
        action = str(action_signature).partition(":")[0].casefold()
        if action in {"drag", "draw_strokes"}:
            if (
                comparable
                and change_ratio is not None
                and float(change_ratio) > self._native_no_progress_threshold()
            ):
                self._continuous_pointer_gesture_progress = True
                self._continuous_pointer_forward_only_correction = False
                self._continuous_pointer_done_repair_required = False
                self._continuous_pointer_setup_clicks = 0
                self._continuous_pointer_low_change_clicks = 0
                self._continuous_pointer_guard_blocks = 0
                self._continuous_pointer_last_change_ratio = float(change_ratio)
            return
        if action not in {"click", "click_element"}:
            return
        if self._continuous_pointer_gesture_progress:
            return

        self._continuous_pointer_setup_clicks += 1
        self._continuous_pointer_last_change_ratio = (
            float(change_ratio) if comparable and change_ratio is not None else None
        )
        if (
            comparable
            and change_ratio is not None
            and float(change_ratio) <= self._native_no_progress_threshold()
        ):
            self._continuous_pointer_low_change_clicks += 1
        elif comparable:
            self._continuous_pointer_low_change_clicks = 0

    def _continuous_pointer_click_guard(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any] | None:
        if (
            not self._continuous_pointer_output_requested()
            or self._continuous_pointer_gesture_progress
            or self._native_single_spatial_action_kind(tool_name, args)
            not in {"click", "click_element"}
        ):
            return None
        setup_limit_reached = (
            self._continuous_pointer_setup_clicks >= _CONTINUOUS_POINTER_SETUP_CLICKS
        )
        low_change_limit_reached = (
            self._continuous_pointer_low_change_clicks
            >= _CONTINUOUS_POINTER_LOW_CHANGE_CLICKS
        )
        if not (setup_limit_reached or low_change_limit_reached):
            return None

        self._continuous_pointer_guard_blocks += 1
        return {
            "ok": False,
            "verified": False,
            "blocked": True,
            "continuous_pointer_guard": True,
            "continuous_gesture_required": True,
            "strategy_change_required": True,
            "setup_clicks_observed": self._continuous_pointer_setup_clicks,
            "low_change_click_streak": self._continuous_pointer_low_change_clicks,
            "observed_change_ratio": (
                round(self._continuous_pointer_last_change_ratio, 6)
                if self._continuous_pointer_last_change_ratio is not None
                else None
            ),
            "error": (
                "This task explicitly requires continuous freehand surface output, but the "
                "model kept choosing point clicks and has not produced measurable progress with "
                "a matching continuous gesture. The click was not executed. Clicks may select "
                "a tool, but toolbar/menu pixel changes are not subject progress. Use drag or "
                "draw_strokes now from the authoritative frame; do not approximate a path, "
                "handwriting, signature, or trace with more clicks, and do not treat tool "
                "execution as semantic success."
            ),
            "needs_replan": True,
            "repeated_guard_blocks": self._continuous_pointer_guard_blocks,
        }

    @staticmethod
    def _contains_explicit_continuous_pointer_action(value: Any) -> bool:
        if isinstance(value, dict):
            name = str(value.get("action") or value.get("tool") or "").strip().casefold()
            if name in {"drag", "draw_strokes"}:
                return True
            for key in (
                "frame_strokes", "normalized_strokes", "strokes",
                "draw", "draws", "drag", "drags",
            ):
                if value.get(key):
                    return True
            return any(
                AgentRunner._contains_explicit_continuous_pointer_action(child)
                for key, child in value.items()
                if key not in {"subgoal", "description", "context", "label"}
            )
        if isinstance(value, (list, tuple)):
            return any(
                AgentRunner._contains_explicit_continuous_pointer_action(child)
                for child in value
            )
        return False

    def _text_continuous_pointer_preparation_candidate(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> bool:
        name = str(tool_name or "").strip().casefold()
        if name == "click":
            return True
        return bool(
            name == "execute_subgoal"
            and not self._contains_explicit_continuous_pointer_action(args)
        )

    @staticmethod
    def _successful_tool_payload(result: ToolResult) -> dict[str, Any] | None:
        if result.terminal in {"fail", "error", "stopped"}:
            return None
        try:
            payload = json.loads(result.text or "{}")
        except (json.JSONDecodeError, TypeError):
            return None
        if (
            not isinstance(payload, dict)
            or payload.get("ok") is not True
            or payload.get("blocked") is True
        ):
            return None
        return payload

    @staticmethod
    def _payload_has_successful_continuous_pointer_action(
        tool_name: str,
        payload: dict[str, Any],
    ) -> bool:
        gestures = {"drag", "draw_strokes"}
        name = str(tool_name or "").strip().casefold()
        if name in gestures:
            return True
        if str(payload.get("source_action") or "").casefold() in gestures:
            return payload.get("source_action_ok") is not False
        state = payload.get("controller_state")
        if (
            isinstance(state, dict)
            and str(state.get("last_action") or "").casefold() in gestures
            and state.get("blocked") is not True
        ):
            return True
        for action in payload.get("actions_executed") or []:
            if isinstance(action, dict):
                action_name = str(action.get("action") or action.get("tool") or "").casefold()
                if action_name in gestures and action.get("ok") is not False:
                    return True
            elif str(action or "").casefold() in gestures:
                return True
        for item in payload.get("results") or []:
            if not isinstance(item, dict):
                continue
            action_name = str(item.get("action") or item.get("tool") or "").casefold()
            if action_name not in gestures:
                continue
            data = item.get("data")
            if isinstance(data, dict):
                if data.get("blocked") is not True and any(
                    data.get(key) is True
                    for key in ("ok", "verified", "visual_change_detected")
                ):
                    return True
            elif item.get("blocked") is not True and any(
                item.get(key) is True
                for key in ("ok", "verified", "visual_change_detected")
            ):
                return True
        return False

    @staticmethod
    def _payload_has_preparation_mutation(
        tool_name: str,
        args: dict[str, Any],
        payload: dict[str, Any],
    ) -> bool:
        name = str(tool_name or "").strip().casefold()
        if name == "click":
            return True
        if name != "execute_subgoal":
            return False
        non_mutating = {
            "observe", "read_text", "verify", "wait", "focus_window",
            "open_app", "list_windows", "scan_windows",
        }
        actions = payload.get("actions_executed") or []
        if isinstance(actions, list):
            for action in actions:
                if isinstance(action, dict):
                    action_name = str(action.get("action") or action.get("tool") or "").casefold()
                else:
                    action_name = str(action or "").casefold()
                if action_name and action_name not in non_mutating:
                    return True
        if payload.get("mutation_applied") is True or payload.get("input_applied") is True:
            return True
        return any(
            args.get(key)
            for key in (
                "click", "clicks", "format", "formats", "input", "inputs",
                "type_text", "hotkey", "select_color",
            )
        )

    def _record_text_continuous_pointer_outcome(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: ToolResult,
    ) -> None:
        if _main_model_has_vision() or not self._continuous_pointer_output_requested():
            return
        payload = self._successful_tool_payload(result)
        if payload is None:
            return
        if self._payload_has_successful_continuous_pointer_action(tool_name, payload):
            # Routing state only. Semantic success remains the responsibility
            # of the normal evidence ledger and done gate.
            self._continuous_pointer_gesture_progress = True
            self._continuous_pointer_text_setup_mutations = 0
            self._continuous_pointer_guard_blocks = 0
            return
        if (
            self._continuous_pointer_gesture_progress
            or payload.get("verified") is True
            or not self._payload_has_preparation_mutation(tool_name, args, payload)
        ):
            return
        self._continuous_pointer_text_setup_mutations += 1

    def _text_continuous_pointer_preparation_guard(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any] | None:
        if (
            _main_model_has_vision()
            or not self._continuous_pointer_output_requested()
            or self._continuous_pointer_gesture_progress
            or not self._text_continuous_pointer_preparation_candidate(tool_name, args)
            or self._continuous_pointer_text_setup_mutations
            < _TEXT_CONTINUOUS_POINTER_SETUP_MUTATIONS
        ):
            return None
        self._continuous_pointer_guard_blocks += 1
        return {
            "ok": False,
            "verified": False,
            "blocked": True,
            "continuous_pointer_guard": True,
            "text_only_continuous_setup_guard": True,
            "continuous_gesture_required": True,
            "strategy_change_required": True,
            "setup_mutations_observed": self._continuous_pointer_text_setup_mutations,
            "error": (
                "This continuous surface-output task already used the allowed unverified "
                "click/execute_subgoal setup mutations without a successful drag or draw_strokes. "
                "This preparation mutation was not executed. For a work surface without grounded "
                "physical coordinates, call direct draw_strokes with normalized_strokes in 0..1000 "
                "and include the currently known independent paths together. Otherwise use drag or "
                "draw_strokes with a genuinely grounded physical path. Observe may clarify state, "
                "but it does not create output or renew the preparation budget."
            ),
            "needs_replan": True,
            "repeated_guard_blocks": self._continuous_pointer_guard_blocks,
        }

    def _reset_constraint_rejection_guard(self) -> None:
        self._constraint_rejection_category = ""
        self._constraint_rejection_streak = 0

    def _apply_constraint_rejection_guard(
        self,
        tool_name: str,
        result: ToolResult,
        *,
        dispatched: bool,
    ) -> None:
        """Bound repeated fail-closed attempts that never touched the GUI.

        Pixel/semantic no-progress guards necessarily start *after* execution.
        Coordinate, scope, and controller-policy validation happens before
        execution and therefore has no post-action frame for those guards to
        count.  This independent budget closes that blind spot.
        """
        category = _unexecuted_constraint_category(result.text or "")
        if not category:
            try:
                payload = json.loads(result.text or "{}")
            except (json.JSONDecodeError, TypeError, ValueError):
                payload = {}
            # Only real new evidence resets the debt.  A successful dispatched
            # action changes controller state; an explicit successful observe
            # gives the model a new authoritative basis.  Arbitrary failures,
            # same-turn barriers, and recovery hints cannot launder the streak.
            observe_succeeded = bool(
                dispatched
                and result.is_observe
                and result.terminal not in {"fail", "error", "stopped"}
                and not (isinstance(payload, dict) and payload.get("ok") is False)
            )
            action_succeeded = bool(
                dispatched
                and isinstance(payload, dict)
                and payload.get("ok") is True
                and payload.get("blocked") is not True
            )
            if observe_succeeded or action_succeeded:
                self._reset_constraint_rejection_guard()
            return

        if category == self._constraint_rejection_category:
            self._constraint_rejection_streak += 1
        else:
            self._constraint_rejection_category = category
            self._constraint_rejection_streak = 1

        try:
            payload = json.loads(result.text or "{}")
        except (json.JSONDecodeError, TypeError, ValueError):
            return
        if not isinstance(payload, dict):
            return

        streak = int(self._constraint_rejection_streak)
        limit = int(CONFIG.get(
            "controller",
            "unexecuted_constraint_max_rejections",
            default=_UNEXECUTED_CONSTRAINT_MAX_REJECTIONS,
        ) or _UNEXECUTED_CONSTRAINT_MAX_REJECTIONS)
        limit = max(2, min(5, limit))
        payload.update({
            "constraint_rejection_guard": True,
            "constraint_category": category,
            "constraint_rejection_streak": streak,
            "constraint_rejection_limit": limit,
            "action_executed": False,
            "strategy_change_required": streak >= 2,
        })
        if streak >= 2:
            payload["needs_replan"] = True

        error = str(payload.get("error") or "").rstrip()
        if streak >= limit:
            payload["safe_stop"] = True
            payload["error"] = (
                f"{error} Safety stop: the same unexecuted controller constraint "
                f"({category}) rejected {streak} consecutive attempts. The model changed "
                "arguments without obtaining new evidence or a successful execution, so the "
                "run is stopping instead of continuing confidently."
            ).strip()
            result.terminal = "reply"
        elif streak >= 2:
            payload["error"] = (
                f"{error} This is consecutive unexecuted rejection {streak}/{limit} for the "
                f"same constraint category ({category}). Do not merely adjust the rejected "
                "arguments again; inspect fresh evidence or choose a materially different "
                "controller strategy."
            ).strip()
        else:
            payload["error"] = (
                f"{error} This unexecuted constraint incident is now tracked as "
                f"{category} (1/{limit}); correct the controller contract before retrying."
            ).strip()
        result.text = json.dumps(payload, ensure_ascii=False, default=str)
        BUS.publish(
            "constraint_rejection",
            run_id=self.run_id,
            tool=tool_name,
            category=category,
            streak=streak,
            limit=limit,
            safe_stop=bool(streak >= limit),
        )

    def _record_native_visual_progress(
        self,
        action_signature: str,
        change_ratio: float | None,
        *,
        comparable: bool,
    ) -> None:
        self._record_continuous_pointer_progress(
            action_signature,
            change_ratio,
            comparable=comparable,
        )
        if not comparable or change_ratio is None or not action_signature:
            self._native_no_progress_signature = ""
            self._native_no_progress_streak = 0
            self._native_no_progress_block_count = 0
            return
        if float(change_ratio) <= self._native_no_progress_threshold():
            if action_signature == self._native_no_progress_signature:
                self._native_no_progress_streak += 1
            else:
                self._native_no_progress_signature = action_signature
                self._native_no_progress_streak = 1
                self._native_no_progress_block_count = 0
            return
        self._native_no_progress_signature = ""
        self._native_no_progress_streak = 0
        self._native_no_progress_block_count = 0

    def _native_no_progress_guard(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any] | None:
        signature = self._native_spatial_action_signature(
            tool_name,
            args,
            frame_region=self._visual_frame_region,
        )
        if (
            not signature
            or signature != self._native_no_progress_signature
            or self._native_no_progress_streak < int(CONFIG.get(
                "controller", "native_no_progress_streak",
                default=_NATIVE_NO_PROGRESS_STREAK,
            ) or _NATIVE_NO_PROGRESS_STREAK)
        ):
            return None
        self._native_no_progress_block_count += 1
        return {
            "ok": False,
            "verified": False,
            "blocked": True,
            "native_no_progress_guard": True,
            "no_visual_progress": True,
            "repeat_signature": signature,
            "no_progress_streak": self._native_no_progress_streak,
            "observed_change_ratio": round(float(self._last_visual_change_ratio or 0.0), 6),
            "strategy_change_required": True,
            "error": (
                "This same spatial action already produced consecutive same-region frames with "
                "negligible pixel change. It was not executed again. Re-read the authoritative "
                "frame and choose a materially different target, tool, or plan; execution alone "
                "is not evidence of success."
            ),
            "needs_replan": True,
            "repeated_guard_blocks": self._native_no_progress_block_count,
        }

    def _stable_visual_work_surface(
        self,
        candidate: tuple[int, int, int, int] | None,
        *,
        hwnd: int,
        frame_region: tuple[int, int, int, int],
        capture_scope: str = "",
        initial: bool = False,
    ) -> tuple[tuple[int, int, int, int] | None, str]:
        """Keep document-surface geometry stable as ink changes its pixels.

        A light-component detector naturally sees less white area after marks
        are added.  Rebinding coordinates to that smaller component shifts all
        later actions. Bind by target window, capture rectangle, and capture
        scope. Retain only edge-continuous shrink/jitter, fail closed on a
        missing or nested candidate, and require repeated evidence before a
        same-target layout displacement can become the new binding.
        """
        anchor = self._visual_work_surface_anchor_bounds
        same_target = bool(
            anchor is not None
            and self._visual_work_surface_anchor_hwnd == int(hwnd)
            and self._visual_work_surface_anchor_region == frame_region
            and self._visual_work_surface_anchor_scope == capture_scope
        )
        if initial or not same_target:
            self._visual_work_surface_anchor_bounds = candidate
            self._visual_work_surface_anchor_hwnd = int(hwnd)
            self._visual_work_surface_anchor_region = frame_region
            self._visual_work_surface_anchor_scope = capture_scope
            self._visual_work_surface_pending_bounds = None
            self._visual_work_surface_pending_count = 0
            return candidate, "fresh_target_binding" if candidate else "not_detected"
        assert anchor is not None
        if candidate is None:
            self._visual_work_surface_pending_bounds = None
            self._visual_work_surface_pending_count = 0
            return None, "current_detection_missing_fail_closed"

        al, at, ar, ab = anchor
        cl, ct, cr, cb = candidate
        anchor_area = max(1, (ar - al) * (ab - at))
        candidate_area = max(1, (cr - cl) * (cb - ct))
        tolerance = 12
        edge_deltas = (
            abs(cl - al), abs(ct - at), abs(cr - ar), abs(cb - ab),
        )
        matching_edges = sum(delta <= tolerance for delta in edge_deltas)
        candidate_is_subset = bool(
            al - tolerance <= cl
            and at - tolerance <= ct
            and ar + tolerance >= cr
            and ab + tolerance >= cb
        )
        candidate_area_ratio = candidate_area / anchor_area
        detector_jitter = all(delta <= tolerance for delta in edge_deltas)
        credible_component_shrink = bool(
            candidate_is_subset
            and matching_edges >= 3
            and candidate_area_ratio >= 0.35
        )
        if detector_jitter or credible_component_shrink:
            self._visual_work_surface_pending_bounds = None
            self._visual_work_surface_pending_count = 0
            return anchor, (
                "stable_binding_retained_after_component_shrink"
                if credible_component_shrink and candidate_area < anchor_area * 0.90
                else "stable_binding_retained_after_detector_jitter"
            )

        # A central subset is indistinguishable from a nested panel/modal from
        # geometry alone. Never silently promote it to the mutation surface.
        if candidate_is_subset:
            self._visual_work_surface_pending_bounds = None
            self._visual_work_surface_pending_count = 0
            return None, "possible_nested_surface_fail_closed"

        pending = self._visual_work_surface_pending_bounds
        if pending is not None and all(
            abs(current - previous) <= tolerance
            for current, previous in zip(candidate, pending)
        ):
            self._visual_work_surface_pending_count += 1
        else:
            self._visual_work_surface_pending_bounds = candidate
            self._visual_work_surface_pending_count = 1
        if self._visual_work_surface_pending_count < 2:
            return None, "layout_displacement_pending_confirmation"

        self._visual_work_surface_anchor_bounds = candidate
        self._visual_work_surface_pending_bounds = None
        self._visual_work_surface_pending_count = 0
        return candidate, "rebound_after_confirmed_layout_displacement"

    def _inject_visual_frame(
        self,
        *,
        phase: str,
        action: str = "",
        image_b64: str = "",
        frame_region: Any | None = None,
        result_preview: str = "",
        action_signature: str = "",
    ) -> bool:
        """Append one authoritative GUI frame to the model history.

        The old loop only attached pixels after an explicit ``observe`` call.
        That left the model blind after actions and allowed it to keep operating
        from stale structured summaries.  This helper is used for the initial
        bound window, explicit observations, and every completed visual
        transaction.  It also records the screen epoch used by the pre-action
        grounding gate.
        """
        if not _main_model_has_vision():
            return False
        hwnd = int(getattr(self.session, "hwnd", 0) or 0)
        previous_frame_id = int(self._visual_frame_id or 0)
        previous_b64 = self._last_visual_feedback_b64
        previous_hwnd = int(self._visual_frame_hwnd or 0)
        previous_region = self._visual_frame_region
        previous_scope = self._visual_frame_scope
        started = time.monotonic()
        try:
            cached = getattr(self.session, "_last_native_frame_capture", None)
            cached_matches = bool(
                image_b64
                and isinstance(cached, dict)
                and cached.get("image_b64") == image_b64
                and int(cached.get("hwnd") or 0) == hwnd
                and cached.get("region") is not None
            )
            if frame_region is not None:
                # Preflight captured these pixels from this exact rectangle.
                # Never query a later window rectangle and pair it with an
                # older JPEG: a move/resize between the two would corrupt all
                # subsequent normalized coordinates.
                region = frame_region
                scope = (
                    "full_desktop"
                    if region == virtual_screen_region()
                    else "target_window"
                )
                scope_reason = "preflight_exact_capture_region"
            elif cached_matches:
                # Explicit observe captured these pixels and this rectangle in
                # one operation. Reuse the pair without a redundant screenshot.
                region = cached["region"]
                scope = str(cached.get("scope") or "target_window")
                scope_reason = str(
                    cached.get("scope_reason") or "observe_exact_capture_region"
                )
            else:
                decision = select_visual_capture_scope(
                    hwnd or None,
                    task=self.task,
                    related_window=getattr(
                        self.session, "_window_related_to_target", None,
                    ),
                )
                region = decision.region
                scope = decision.scope
                scope_reason = decision.reason
            if region.width < 16 or region.height < 16:
                return False
            if image_b64:
                b64 = image_b64
            elif phase == "initial" and action == "target_prebind":
                b64 = self._capture_settled_initial_frame(hwnd, region)
            else:
                b64 = capture_window_jpeg_b64(hwnd, region=region)
            if not b64:
                return False
            region_tuple = (
                int(region.left), int(region.top), int(region.width), int(region.height),
            )
            surface_info = credible_work_surface_frame_bounds(b64)
            surface_failure_reason = (
                str(surface_info.get("reason") or "")
                if isinstance(surface_info, dict)
                else ""
            )
            surface_detection_ambiguous = bool(
                isinstance(surface_info, dict)
                and (
                    surface_info.get("ambiguous") is True
                    or surface_info.get("failure_kind") == "ambiguous"
                )
            )
            raw_surface_bounds = (
                surface_info.get("work_surface_frame_bounds")
                if isinstance(surface_info, dict)
                else None
            )
            detected_work_surface_bounds = (
                tuple(int(value) for value in raw_surface_bounds)
                if isinstance(raw_surface_bounds, (list, tuple))
                and len(raw_surface_bounds) == 4
                else None
            )
            work_surface_bounds, work_surface_policy = self._stable_visual_work_surface(
                detected_work_surface_bounds,
                hwnd=hwnd,
                frame_region=region_tuple,
                capture_scope=scope,
                initial=(phase == "initial"),
            )
            surface_blocked_reason = ""
            if work_surface_bounds is None:
                fail_closed_policies = {
                    "current_detection_missing_fail_closed",
                    "possible_nested_surface_fail_closed",
                    "layout_displacement_pending_confirmation",
                }
                if (
                    surface_detection_ambiguous
                    or work_surface_policy in fail_closed_policies
                ):
                    surface_blocked_reason = (
                        surface_failure_reason
                        or work_surface_policy.replace("_", " ")
                    )
            change_comparable = bool(
                phase == "post_action"
                and previous_b64
                and previous_hwnd == hwnd
                and previous_region == region_tuple
                and previous_scope == scope
            )
            change_ratio = (
                jpeg_diff_ratio(previous_b64, b64) if change_comparable else None
            )
            no_visual_progress = bool(
                change_comparable
                and change_ratio is not None
                and float(change_ratio) <= self._native_no_progress_threshold()
            )
            max_side = 1120
            scale = min(1.0, max_side / max(1, region.width, region.height))
            submitted_width = max(1, int(region.width * scale))
            submitted_height = max(1, int(region.height * scale))
            self._visual_frame_id += 1
            frame_id = self._visual_frame_id
            self._visual_work_surface_frame_bounds = work_surface_bounds
            self._visual_work_surface_frame_id = frame_id if work_surface_bounds else 0
            self._visual_work_surface_blocked_reason = surface_blocked_reason
            self._visual_work_surface_blocked_frame_id = (
                frame_id if surface_blocked_reason else 0
            )
            epoch = int(getattr(self.session, "_screen_epoch", 0) or 0)
            action_line = f" after action {action!r}" if action else ""
            preview_line = (
                " Tool output preview is diagnostic only: "
                + re.sub(r"\s+", " ", result_preview).strip()[:240]
                if result_preview else ""
            )
            scope_line = (
                "This image is a verified foreground target-window crop. "
                if scope == "target_window"
                else "This image is the complete virtual desktop, including all monitors. "
            )
            if phase == "post_action" and change_comparable and change_ratio is not None:
                progress_line = (
                    f"Same-region pixel change ratio since frame #{previous_frame_id}: "
                    f"{float(change_ratio):.6f}. "
                )
                if no_visual_progress:
                    progress_line += (
                        "NO VISUAL PROGRESS DETECTED. Do not claim success and do not repeat "
                        "the same spatial action; inspect the pixels and change strategy. "
                    )
                else:
                    progress_line += "Visible pixels changed; judge whether that change serves the task. "
            elif phase == "post_action":
                progress_line = (
                    "No comparable same-region prior frame is available, so do not infer success "
                    "from the action result alone. "
                )
            else:
                progress_line = ""
            surface_line = (
                "Runtime pixels uniquely ground one credible light work surface: "
                f"work_surface_frame_bounds={list(work_surface_bounds)} in the same frame 0..1000 "
                f"coordinate system (binding_policy={work_surface_policy}). "
                "For draw_strokes, every stroke must start inside these bounds "
                "and every authored point must remain inside; the runtime keeps only a tiny "
                "internal tolerance for long paths at a detector edge. Do not reinterpret the "
                "bounds as a second coordinate space or trace along their boundary. "
                if work_surface_bounds is not None else
                (
                    "Runtime cannot uniquely ground a safe work surface in the current "
                    "pixels, so continuous pointer mutation is fail-closed until a fresh "
                    f"frame resolves it (reason={surface_blocked_reason[:180]}). "
                    if surface_blocked_reason else ""
                )
            )
            self.messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"[Visual closed-loop frame #{frame_id}; phase={phase}{action_line}. "
                            "This real GUI image is the authoritative current state. "
                            "Judge it as a whole before the next action; structured elements, "
                            "rules, and controller summaries are only hints and must not "
                            "override contradictory pixels. If the intended target/result is "
                            "not visually clear, do not guess or continue blindly. "
                            f"Frame scope={scope}; selection_reason={scope_reason}. "
                            f"{scope_line}{progress_line}{surface_line}"
                            f"物理屏幕区域 left={region.left}, top={region.top}, "
                            f"width={region.width}, height={region.height}；"
                            f"提交图片约 {submitted_width}x{submitted_height}。"
                            "直接依据此图操作时必须使用 frame_x/frame_y（0..1000，"
                            "相对整张当前帧）；运行时会映射到物理屏幕。不要自行按图片"
                            "像素缩放，也不要把图片坐标填进 x/y。结构化 observe 的"
                            f"目标应使用 element_id；物理 x/y 仅供控制器内部使用。{preview_line}]"
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            })
            self._foldable.append((len(self.messages) - 1, "[已折叠 screenshot]"))
            self._fold_old()
            self._visual_frame_epoch = epoch
            self._visual_frame_hwnd = int(hwnd)
            self._visual_frame_scope = scope
            self._visual_frame_scope_reason = scope_reason
            self._visual_frame_region = region_tuple
            # FastGuiController owns child actions but shares ToolSession, not
            # AgentRunner.  Mirror the exact submitted frame there so typed
            # action events cannot silently fall back to a stale UIA snapshot
            # region (especially when this is a full-desktop frame).
            self.session._visual_frame_id = frame_id
            self.session._visual_frame_epoch = epoch
            self.session._visual_frame_scope = scope
            self.session._visual_frame_region = {
                "left": int(region.left),
                "top": int(region.top),
                "width": int(region.width),
                "height": int(region.height),
            }
            self.session._visual_work_surface_frame_bounds = (
                list(work_surface_bounds) if work_surface_bounds is not None else None
            )
            self.session._visual_work_surface_frame_id = self._visual_work_surface_frame_id
            self._last_visual_feedback_b64 = b64
            self._last_visual_feedback_action = str(action or phase)
            self._last_visual_change_ratio = change_ratio
            self._last_visual_change_comparable = change_comparable
            if phase == "post_action":
                self._partial_output_guard.record_post_action(
                    action,
                    hwnd=hwnd,
                    screen_epoch=epoch,
                    frame_id=frame_id,
                    comparable=change_comparable,
                    change_ratio=change_ratio,
                    threshold=self._native_no_progress_threshold(),
                )
                self._record_native_visual_progress(
                    action_signature,
                    change_ratio,
                    comparable=change_comparable,
                )
            self._visual_grounding_block_count = 0
            if phase in {"post_action", "preflight"}:
                # Element ids/trees describe the pre-action frame.  Keeping
                # them around lets a later execute_subgoal silently act on a
                # stale UI.  Controller-local batches retain their snapshot
                # only inside the transaction; the next model turn must
                # re-ground structure if it chooses a structured action.
                self._invalidate_structured_grounding()
            BUS.publish(
                "visual_feedback",
                run_id=self.run_id,
                frame_id=frame_id,
                phase=phase,
                action=action,
                hwnd=int(hwnd),
                screen_epoch=epoch,
                capture_scope=scope,
                capture_scope_reason=scope_reason,
                capture_region=[
                    int(region.left), int(region.top), int(region.width), int(region.height),
                ],
                coordinate_space="frame_normalized_0_1000",
                compared_to_frame_id=previous_frame_id if change_comparable else None,
                pixel_change_comparable=change_comparable,
                pixel_change_ratio=(
                    round(float(change_ratio), 8) if change_ratio is not None else None
                ),
                no_visual_progress=no_visual_progress,
                no_progress_streak=int(self._native_no_progress_streak),
                work_surface_frame_bounds=(
                    list(work_surface_bounds) if work_surface_bounds is not None else None
                ),
                work_surface_binding_policy=work_surface_policy,
                work_surface_blocked_reason=surface_blocked_reason or None,
                capture_latency_ms=int((time.monotonic() - started) * 1000),
            )
            return True
        except Exception as exc:
            logger.debug("visual closed-loop frame injection failed: %s", exc)
            return False

    def _inject_observe_screenshot(self, image_b64: str = "") -> bool:
        """Backward-compatible observe hook using the unified frame path."""
        return self._inject_visual_frame(
            phase="observe",
            action="observe",
            image_b64=image_b64,
        )

    def _invalidate_structured_grounding(self) -> None:
        """Drop structured state that no longer belongs to the visual frame.

        External UI changes do not advance ToolSession's action epoch.  When a
        preflight capture replaces the authoritative frame, keeping the old
        snapshot or its short-lived observe cache would let a later element_id
        or controller refresh silently ground against the previous pixels.
        """
        self.session.snapshot = None
        self.session._observe_cache = None
        self.session._last_native_frame_capture = None

    def _visual_preflight_target_roi(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> tuple[float, float, float, float] | None:
        """Find the smallest safely grounded action ROI in frame coordinates.

        Unrelated animation should not cancel a click whose target pixels are
        unchanged.  The ROI comes only from explicit frame coordinates, a
        current structured element, or a coarse expected-region hint.  If no
        such grounding exists, callers retain the conservative whole-frame
        preflight.
        """
        margin = float(CONFIG.get(
            "controller", "visual_preflight_target_margin", default=60.0,
        ) or 60.0)
        margin = max(8.0, min(200.0, margin))
        rois: list[tuple[float, float, float, float]] = []

        def number(value: Any) -> float | None:
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                return None
            return parsed if 0.0 <= parsed <= 1000.0 else None

        def add_rect(
            left: float,
            top: float,
            right: float,
            bottom: float,
            *,
            pad: float = margin,
        ) -> None:
            left = max(0.0, min(1000.0, left - pad))
            top = max(0.0, min(1000.0, top - pad))
            right = max(0.0, min(1000.0, right + pad))
            bottom = max(0.0, min(1000.0, bottom + pad))
            if right > left and bottom > top:
                rois.append((left, top, right, bottom))

        def add_points(points: list[tuple[float, float]]) -> None:
            if points:
                add_rect(
                    min(point[0] for point in points),
                    min(point[1] for point in points),
                    max(point[0] for point in points),
                    max(point[1] for point in points),
                )

        def add_element(element_id: Any) -> bool:
            try:
                wanted = int(element_id)
            except (TypeError, ValueError):
                return False
            snapshot = getattr(self.session, "snapshot", None)
            frame = self._visual_frame_region
            if snapshot is None or frame is None:
                return False
            try:
                snapshot_hwnd = int(getattr(snapshot, "hwnd", 0) or 0)
                current_hwnd = int(getattr(self.session, "hwnd", 0) or 0)
            except (TypeError, ValueError):
                return False
            if snapshot_hwnd and current_hwnd and snapshot_hwnd != current_hwnd:
                return False
            frame_left, frame_top, frame_width, frame_height = frame
            if frame_width <= 0 or frame_height <= 0:
                return False
            for element in (getattr(snapshot, "elements", None) or []):
                if int(getattr(element, "elem_id", -1) or -1) != wanted:
                    continue
                width = max(1.0, float(getattr(element, "width", 0) or 0))
                height = max(1.0, float(getattr(element, "height", 0) or 0))
                left = float(getattr(element, "left", 0) or 0)
                top = float(getattr(element, "top", 0) or 0)
                if not left and getattr(element, "cx", None) is not None:
                    left = float(element.cx) - width / 2.0
                if not top and getattr(element, "cy", None) is not None:
                    top = float(element.cy) - height / 2.0
                add_rect(
                    (left - frame_left) / frame_width * 1000.0,
                    (top - frame_top) / frame_height * 1000.0,
                    (left + width - frame_left) / frame_width * 1000.0,
                    (top + height - frame_top) / frame_height * 1000.0,
                )
                return True
            return False

        def add_semantic_target(payload: dict[str, Any]) -> bool:
            snapshot = getattr(self.session, "snapshot", None)
            if snapshot is None:
                return False
            labels = [
                str(payload.get(key) or "").strip().casefold()
                for key in ("label", "fallback_text", "text_query", "name")
                if str(payload.get(key) or "").strip()
            ]
            if not labels:
                return False
            matches = []
            for element in (getattr(snapshot, "elements", None) or []):
                text = str(getattr(element, "text", "") or "").strip().casefold()
                if text and any(label == text or label in text or text in label for label in labels):
                    matches.append(element)
            if not matches or len(matches) > 4:
                return False
            added = False
            for element in matches:
                added = add_element(getattr(element, "elem_id", None)) or added
            return added

        def add_expected_region(value: Any) -> bool:
            hint = str(value or "").strip().casefold().replace("_", "-").replace(" ", "-")
            regions = {
                "top-left": (0.0, 0.0, 520.0, 520.0),
                "top-right": (480.0, 0.0, 1000.0, 520.0),
                "bottom-left": (0.0, 480.0, 520.0, 1000.0),
                "bottom-right": (480.0, 480.0, 1000.0, 1000.0),
                "top": (0.0, 0.0, 1000.0, 420.0),
                "bottom": (0.0, 580.0, 1000.0, 1000.0),
                "left": (0.0, 0.0, 520.0, 1000.0),
                "right": (480.0, 0.0, 1000.0, 1000.0),
                "center": (220.0, 180.0, 780.0, 820.0),
                "middle": (220.0, 180.0, 780.0, 820.0),
            }
            region = regions.get(hint)
            if region is None:
                return False
            add_rect(*region, pad=0.0)
            return True

        def collect(name: str, payload: dict[str, Any]) -> None:
            if name == "run_action_batch":
                for item in payload.get("actions") or []:
                    if not isinstance(item, dict):
                        continue
                    child_name = str(item.get("action") or item.get("tool") or "")
                    if child_name:
                        params = item.get("params") if isinstance(item.get("params"), dict) else {}
                        child_args = {
                            **params,
                            **{
                                key: value for key, value in item.items()
                                if key not in {"action", "tool", "params"}
                            },
                        }
                    elif len(item) == 1:
                        child_name, raw = next(iter(item.items()))
                        child_args = dict(raw) if isinstance(raw, dict) else {}
                    else:
                        continue
                    collect(str(child_name), child_args)
                return
            if name == "execute_subgoal":
                click_specs = []
                if isinstance(payload.get("click"), dict):
                    click_specs.append(payload["click"])
                click_specs.extend(
                    item for item in (payload.get("clicks") or []) if isinstance(item, dict)
                )
                for spec in click_specs:
                    collect("click", spec)
                input_spec = payload.get("input")
                if isinstance(input_spec, dict):
                    add_element(input_spec.get("element_id")) or add_semantic_target(input_spec)
                    add_expected_region(input_spec.get("expected_region"))
                return
            if name in {"click", "scroll", "move_mouse"}:
                x = number(payload.get("frame_x"))
                y = number(payload.get("frame_y"))
                if x is not None and y is not None:
                    add_points([(x, y)])
                    return
            elif name == "drag":
                points: list[tuple[float, float]] = []
                for x_key, y_key in (("frame_x1", "frame_y1"), ("frame_x2", "frame_y2")):
                    x = number(payload.get(x_key))
                    y = number(payload.get(y_key))
                    if x is not None and y is not None:
                        points.append((x, y))
                for raw in payload.get("frame_points") or []:
                    if isinstance(raw, (list, tuple)) and len(raw) == 2:
                        x, y = number(raw[0]), number(raw[1])
                        if x is not None and y is not None:
                            points.append((x, y))
                if points:
                    add_points(points)
                    return
            elif name == "draw_strokes":
                points: list[tuple[float, float]] = []
                raw_strokes = payload.get("frame_strokes")
                try:
                    if isinstance(raw_strokes, str):
                        normalized_strokes, _ = _normalize_stringified_frame_strokes(
                            raw_strokes,
                        )
                    elif isinstance(raw_strokes, list):
                        normalized_strokes, _ = _normalize_frame_stroke_list_structure(
                            raw_strokes,
                        )
                    else:
                        normalized_strokes = []
                except ValueError:
                    # Malformed paths are rejected by the coordinate contract
                    # later.  Preflight stays conservative and compares the
                    # whole frame instead of inventing an action ROI.
                    normalized_strokes = []
                for stroke in normalized_strokes:
                    for raw in stroke if isinstance(stroke, list) else []:
                        if isinstance(raw, (list, tuple)) and len(raw) == 2:
                            x, y = number(raw[0]), number(raw[1])
                            if x is not None and y is not None:
                                points.append((x, y))
                if points:
                    add_points(points)
                    return
            if payload.get("element_id") is not None and add_element(payload.get("element_id")):
                return
            if add_semantic_target(payload):
                return
            add_expected_region(payload.get("expected_region"))

        collect(str(tool_name or ""), args if isinstance(args, dict) else {})
        if not rois:
            return None
        return (
            min(roi[0] for roi in rois),
            min(roi[1] for roi in rois),
            max(roi[2] for roi in rois),
            max(roi[3] for roi in rois),
        )

    def _has_fresh_visual_grounding(self) -> bool:
        hwnd = int(getattr(self.session, "hwnd", 0) or 0)
        epoch = int(getattr(self.session, "_screen_epoch", 0) or 0)
        region_matches = False
        scope_matches = False
        try:
            if self._visual_frame_scope == "full_desktop":
                # A full-desktop frame remains a valid (strictly richer)
                # coordinate space when the crop optimizer later decides that
                # a target-window crop has become worthwhile, e.g. while an
                # app finishes its maximize/restore startup animation.
                current = virtual_screen_region()
                current_scope = "full_desktop"
            elif not self._visual_frame_scope and hwnd:
                # Compatibility for replay/tests that hydrate a legacy frame
                # tuple directly. Newly captured frames always carry scope.
                current = window_region(hwnd).clamp_to_screen()
                current_scope = "target_window"
            else:
                decision = select_visual_capture_scope(
                    hwnd or None,
                    task=self.task,
                    related_window=getattr(
                        self.session, "_window_related_to_target", None,
                    ),
                )
                current = decision.region
                current_scope = decision.scope
            region_matches = self._visual_frame_region == (
                int(current.left), int(current.top),
                int(current.width), int(current.height),
            )
            scope_matches = (
                not self._visual_frame_scope
                or self._visual_frame_scope == current_scope
            )
        except Exception:
            region_matches = False
            scope_matches = False
        hwnd_matches = bool(
            self._visual_frame_scope == "full_desktop"
            or self._visual_frame_hwnd == hwnd
        )
        return bool(
            hwnd_matches
            and self._visual_frame_epoch == epoch
            and self._last_visual_feedback_b64
            and region_matches
            and scope_matches
        )

    def _preflight_visual_frame(
        self,
        tool_name: str = "",
        args: dict[str, Any] | None = None,
    ) -> tuple[str, float | None, str, Any | None]:
        """Detect external pixel changes that happened during model inference.

        Internal screen epochs cannot see a webpage auto-refresh, popup, user
        occlusion, or animation. A cheap fresh capture before low-level input
        closes that gap. Meaningful changes return the new frame and cancel the
        pending action; tiny caret/antialiasing changes stay below the threshold.
        """
        previous = self._last_visual_feedback_b64
        # Unit tests and legacy synthetic callers use short placeholders, not
        # real JPEGs. Production frames are substantially larger.
        if len(previous) < 128:
            return "", 0.0, "", None
        if not self._has_fresh_visual_grounding():
            # Freshness is checked once before entering preflight, but the
            # foreground window, epoch, or capture scope can change in that
            # small interval.  A real authoritative frame becoming stale is
            # a hard barrier for every low-level action, including keyboard
            # input that has no coordinates to reject later.
            return (
                "",
                None,
                "authoritative visual grounding became stale before preflight",
                None,
            )
        hwnd = int(getattr(self.session, "hwnd", 0) or 0)
        started = time.monotonic()
        try:
            if self._visual_frame_scope == "full_desktop":
                # Preflight in the exact coordinate space the model saw. A
                # newly available crop is an optimization for the *next*
                # frame, not permission to remap the pending action mid-turn.
                region = virtual_screen_region()
                capture_scope = "full_desktop"
                capture_scope_reason = "preserve_authoritative_full_desktop_scope"
            elif not self._visual_frame_scope and hwnd:
                region = window_region(hwnd).clamp_to_screen()
                capture_scope = "target_window"
                capture_scope_reason = "legacy_grounding_region"
            else:
                decision = select_visual_capture_scope(
                    hwnd or None,
                    task=self.task,
                    related_window=getattr(
                        self.session, "_window_related_to_target", None,
                    ),
                )
                region = decision.region
                capture_scope = decision.scope
                capture_scope_reason = decision.reason
            capture_region_tuple = (
                int(region.left), int(region.top), int(region.width), int(region.height),
            )
            geometry_changed_before_capture = bool(
                self._visual_frame_region is not None
                and (
                    capture_region_tuple != self._visual_frame_region
                    or (
                        self._visual_frame_scope
                        and capture_scope != self._visual_frame_scope
                    )
                )
            )
            current = capture_window_jpeg_b64(hwnd, region=region)
            if self._visual_frame_scope == "full_desktop":
                after_region = virtual_screen_region()
                after_scope = "full_desktop"
            elif not self._visual_frame_scope and hwnd:
                after_region = window_region(hwnd).clamp_to_screen()
                after_scope = "target_window"
            else:
                after_decision = select_visual_capture_scope(
                    hwnd or None,
                    task=self.task,
                    related_window=getattr(
                        self.session, "_window_related_to_target", None,
                    ),
                )
                after_region = after_decision.region
                after_scope = after_decision.scope
            after_region_tuple = (
                int(after_region.left), int(after_region.top),
                int(after_region.width), int(after_region.height),
            )
            geometry_changed_during_capture = bool(
                after_region_tuple != capture_region_tuple
                or after_scope != capture_scope
            )
        except Exception as exc:
            current = ""
            error = str(exc)
            region = None
            capture_region_tuple = None
            after_region_tuple = None
            geometry_changed_before_capture = False
            geometry_changed_during_capture = False
        else:
            error = ""
        latency_ms = int((time.monotonic() - started) * 1000)
        if not current:
            BUS.publish(
                "visual_preflight",
                run_id=self.run_id,
                ok=False,
                capture_latency_ms=latency_ms,
                error=error or "fresh visual capture returned empty",
            )
            return "", None, error or "fresh visual capture returned empty", region
        if geometry_changed_before_capture or geometry_changed_during_capture:
            BUS.publish(
                "visual_preflight",
                run_id=self.run_id,
                ok=True,
                changed=True,
                geometry_changed=True,
                capture_region=list(capture_region_tuple or ()),
                post_capture_region=list(after_region_tuple or ()),
                capture_scope=capture_scope,
                change_ratio=1.0,
                decision_scope=(
                    "window_geometry"
                    if capture_scope == "target_window"
                    else "capture_scope_geometry"
                ),
                capture_latency_ms=latency_ms,
            )
            # ``current`` is bound to ``region`` even if the window moved
            # immediately afterward. Injection preserves that exact pair, and
            # the next grounding check will reject it if geometry moved again.
            return current, 1.0, "", region
        global_ratio = jpeg_diff_ratio(previous, current)
        if global_ratio is None:
            BUS.publish(
                "visual_preflight",
                run_id=self.run_id,
                ok=False,
                capture_latency_ms=latency_ms,
                error="could not compare the current frame with its visual grounding",
            )
            return "", None, "could not compare the current frame with its visual grounding", region
        global_threshold = float(CONFIG.get(
            "controller", "visual_preflight_change_ratio", default=0.0025,
        ))
        target_roi = self._visual_preflight_target_roi(tool_name, args or {})
        target_ratio: float | None = None
        decision_ratio = float(global_ratio)
        threshold = max(0.0, global_threshold)
        decision_scope = "whole_frame"
        if target_roi is not None:
            target_ratio = jpeg_diff_ratio_in_normalized_roi(
                previous, current, target_roi,
            )
            if target_ratio is not None:
                decision_ratio = float(target_ratio)
                threshold = max(0.0, float(CONFIG.get(
                    "controller", "visual_preflight_target_change_ratio", default=0.01,
                )))
                decision_scope = "action_target_roi"
        changed = bool(decision_ratio > threshold)
        BUS.publish(
            "visual_preflight",
            run_id=self.run_id,
            ok=True,
            changed=changed,
            change_ratio=round(decision_ratio, 6),
            global_change_ratio=round(float(global_ratio), 6),
            target_change_ratio=(
                round(float(target_ratio), 6) if target_ratio is not None else None
            ),
            target_roi=(
                [round(float(value), 2) for value in target_roi]
                if target_roi is not None else None
            ),
            decision_scope=decision_scope,
            capture_scope=capture_scope,
            capture_scope_reason=capture_scope_reason,
            capture_region=list(capture_region_tuple or ()),
            threshold=threshold,
            capture_latency_ms=latency_ms,
        )
        return (current if changed else ""), decision_ratio, "", region

    def _map_normalized_frame_coordinates(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Map explicit 0..1000 frame coordinates to physical screen pixels.

        Native multimodal models naturally ground within the submitted image.
        Asking them to also infer JPEG resize and DPI/window offsets caused
        repeatable misses.  The schema exposes unambiguous ``frame_*`` fields;
        physical ``x/y`` remain available for structured/controller callers.
        """
        def contains_frame_coordinate(value: Any) -> bool:
            if isinstance(value, dict):
                return any(
                    str(key).startswith("frame_") or contains_frame_coordinate(child)
                    for key, child in value.items()
                )
            if isinstance(value, list):
                return any(contains_frame_coordinate(child) for child in value)
            return False

        def batch_action_name(action: Any) -> str:
            if not isinstance(action, dict):
                return ""
            name = str(action.get("action") or action.get("tool") or "").strip()
            if name:
                return name
            if len(action) == 1:
                return str(next(iter(action))).strip()
            return ""

        if tool_name == "run_action_batch":
            # Validate before frame lookup, preflight, coordinate mapping, or
            # dispatch.  The limit applies to semantic/element-id targets just
            # as it does to frame_* coordinates: all of them consume the same
            # model-visible scene and become stale after the first mutation.
            actions = args.get("actions")
            if isinstance(actions, list):
                spatial = [
                    (index, name)
                    for index, action in enumerate(actions)
                    if (name := batch_action_name(action)) in _NATIVE_BATCH_SPATIAL_MUTATIONS
                ]
                if len(spatial) > 1:
                    locations = ", ".join(
                        f"actions[{index}]={name}" for index, name in spatial
                    )
                    raise ValueError(
                        "native run_action_batch allows at most one spatial UI mutation "
                        f"per authoritative frame ({locations}). No batch action was executed. "
                        "Keep keyboard-only input/waits with the first mutation if useful, then "
                        "put each later click/element click/scroll/mouse/drag/draw action in a "
                        "new model turn after inspecting the next real post-action frame."
                    )

        region = self._visual_frame_region
        if not region or not self._has_fresh_visual_grounding():
            if contains_frame_coordinate(args):
                raise ValueError(
                    f"{tool_name} frame coordinates require a fresh authoritative GUI frame"
                )
            return args
        left, top, width, height = region
        mapped = dict(args)

        if tool_name == "run_action_batch":
            actions = mapped.get("actions")
            if not isinstance(actions, list):
                return mapped
            mapped_actions: list[Any] = []
            coordinate_actions = {"click", "scroll", "drag", "draw_strokes", "move_mouse"}
            for index, action in enumerate(actions):
                if not isinstance(action, dict):
                    mapped_actions.append(action)
                    continue
                name = str(action.get("action") or action.get("tool") or "")
                action_args: dict[str, Any]
                if name:
                    action_args = {
                        key: value
                        for key, value in action.items()
                        if key not in {"action", "tool", "params"}
                    }
                    params = action.get("params")
                    if isinstance(params, dict):
                        action_args = {**params, **action_args}
                elif len(action) == 1:
                    name, raw_args = next(iter(action.items()))
                    name = str(name)
                    action_args = dict(raw_args) if isinstance(raw_args, dict) else {"value": raw_args}
                else:
                    mapped_actions.append(action)
                    continue
                if name not in coordinate_actions:
                    mapped_actions.append(action)
                    continue
                try:
                    action_args = self._map_normalized_frame_coordinates(name, action_args)
                except ValueError as exc:
                    metadata = dict(getattr(exc, "metadata", {}) or {})
                    raise NativeFrameCoordinateError(
                        f"run_action_batch.actions[{index}]: {exc}",
                        **metadata,
                    ) from None
                mapped_actions.append({"action": name, **action_args})
            mapped["actions"] = mapped_actions
            return mapped

        def point(x_key: str, y_key: str) -> tuple[int, int] | None:
            present = (x_key in mapped, y_key in mapped)
            if not any(present):
                return None
            if not all(present):
                raise ValueError(
                    f"{tool_name} requires both {x_key} and {y_key} from the current frame"
                )
            def numeric_frame_value(value: Any) -> tuple[float, bool]:
                if isinstance(value, bool):
                    raise ValueError
                try:
                    return float(value), False
                except (TypeError, ValueError):
                    pass
                if isinstance(value, str):
                    # Some OpenAI-compatible providers occasionally append a
                    # single JSON closer inside an otherwise numeric string
                    # (for example "203]"). Repair only that exact,
                    # information-preserving form; never extract a number from
                    # arbitrary text.
                    match = re.fullmatch(
                        r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*[\]\}\)]{1,3}\s*",
                        value,
                    )
                    if match:
                        return float(match.group(1)), True
                raise ValueError

            try:
                nx, repaired_x = numeric_frame_value(mapped[x_key])
                ny, repaired_y = numeric_frame_value(mapped[y_key])
            except (TypeError, ValueError):
                raise ValueError(
                    f"{x_key}/{y_key} must be numeric values in 0..1000"
                ) from None
            if repaired_x:
                mapped[x_key] = nx
            if repaired_y:
                mapped[y_key] = ny
            if not (0.0 <= nx <= 1000.0 and 0.0 <= ny <= 1000.0):
                raise ValueError(
                    f"{x_key}/{y_key} must stay inside the current frame's 0..1000 bounds"
                )
            # Region.right/bottom are exclusive.  Mapping 1000 through width
            # itself would click one pixel outside the captured window.
            px = int(round(left + nx / 1000.0 * max(0, width - 1)))
            py = int(round(top + ny / 1000.0 * max(0, height - 1)))
            return px, py

        def require_normalized_instead_of_physical(
            physical_keys: tuple[str, ...],
            *,
            bypass: bool = False,
        ) -> None:
            if bypass:
                return
            if any(key in mapped for key in physical_keys):
                joined = "/".join(physical_keys)
                raise ValueError(
                    f"native visual {tool_name} rejected model-supplied physical {joined}; "
                    "use the frame_* 0..1000 fields from the current authoritative frame"
                )

        used = False
        source: dict[str, Any] = {}
        if tool_name in {"click", "scroll", "move_mouse"}:
            has_frame_point = "frame_x" in mapped or "frame_y" in mapped
            if has_frame_point and any(key in mapped for key in ("x", "y")):
                raise ValueError(
                    f"{tool_name} received mixed frame and physical coordinate sources"
                )
            if tool_name == "click" and has_frame_point and mapped.get("element_id") is not None:
                raise ValueError("click received both element_id and frame coordinates; choose one grounded target")
            mapped_point = point("frame_x", "frame_y")
            if mapped_point is not None:
                mapped["x"], mapped["y"] = mapped_point
                source = {"frame_x": mapped.pop("frame_x"), "frame_y": mapped.pop("frame_y")}
                used = True
            else:
                # This method only processes model-authored tool arguments.
                # Semantic hints do not make raw x/y safe: direct click gives
                # physical coordinates precedence and would silently ignore a
                # label. Controller-generated physical coordinates are added
                # later, after this boundary, so no model x/y needs a bypass.
                require_normalized_instead_of_physical(("x", "y"))
                if tool_name != "click" or not (
                    mapped.get("element_id") is not None
                    or any(mapped.get(key) for key in (
                        "label", "fallback_text", "role", "expected_region", "visual_anchor",
                    ))
                ):
                    raise ValueError(
                        f"{tool_name} requires frame_x/frame_y from the current frame"
                    )
        elif tool_name == "drag":
            if any(key in mapped for key in ("frame_x1", "frame_y1", "frame_x2", "frame_y2")) \
                    and any(key in mapped for key in ("x1", "y1", "x2", "y2", "points")):
                raise ValueError("drag received mixed frame and physical coordinate sources")
            first = point("frame_x1", "frame_y1")
            second = point("frame_x2", "frame_y2")
            if (first is None) != (second is None):
                raise ValueError("drag requires both normalized frame endpoints")
            if first is not None and second is not None:
                mapped["x1"], mapped["y1"] = first
                mapped["x2"], mapped["y2"] = second
                source = {
                    key: mapped.pop(key)
                    for key in ("frame_x1", "frame_y1", "frame_x2", "frame_y2")
                }
                if "frame_points" in mapped:
                    raw_points = mapped.pop("frame_points")
                    if not isinstance(raw_points, list):
                        raise ValueError("frame_points must be an array of [x,y] points")
                    physical_points: list[list[int]] = []
                    for index, raw_point in enumerate(raw_points):
                        if not isinstance(raw_point, (list, tuple)) or len(raw_point) != 2:
                            raise ValueError(f"frame_points[{index}] must be [x,y]")
                        temp = {"frame_x": raw_point[0], "frame_y": raw_point[1]}
                        prior = mapped
                        mapped = temp
                        try:
                            physical = point("frame_x", "frame_y")
                        finally:
                            mapped = prior
                        assert physical is not None
                        physical_points.append([physical[0], physical[1]])
                    mapped["points"] = physical_points
                    source["frame_points"] = raw_points
                used = True
            else:
                require_normalized_instead_of_physical(("x1", "y1", "x2", "y2", "points"))
                raise ValueError("drag requires frame_x1/frame_y1 and frame_x2/frame_y2")
        elif tool_name == "draw_strokes":
            if "normalized_strokes" in mapped:
                raise ValueError(
                    "native visual draw_strokes rejected normalized_strokes; use frame_strokes "
                    "from the current authoritative frame"
                )
            if "frame_strokes" in mapped:
                if "strokes" in mapped:
                    raise ValueError("draw_strokes received mixed frame and physical coordinate sources")
                raw_strokes = mapped.pop("frame_strokes")
                stringified_repair: dict[str, Any] = {}
                if isinstance(raw_strokes, str):
                    raw_strokes, stringified_repair = _normalize_stringified_frame_strokes(
                        raw_strokes,
                    )
                if not isinstance(raw_strokes, list) or not raw_strokes:
                    raise ValueError("frame_strokes must contain at least one stroke")
                source_frame_strokes = raw_strokes
                raw_strokes, structure_repair = _normalize_frame_stroke_list_structure(
                    raw_strokes,
                )
                normalized_strokes: list[list[tuple[float, float]]] = []
                surface_bounds = (
                    self._visual_work_surface_frame_bounds
                    if self._visual_work_surface_frame_id == self._visual_frame_id
                    else None
                )
                surface_blocked_reason = (
                    self._visual_work_surface_blocked_reason
                    if self._visual_work_surface_blocked_frame_id
                    == self._visual_frame_id
                    else ""
                )
                if surface_blocked_reason:
                    raise NativeFrameCoordinateError(
                        (
                            "The current authoritative pixels do not uniquely ground a safe "
                            "continuous-input work surface. The action was not executed; obtain "
                            "a fresh frame or resolve the visible overlay/layout ambiguity "
                            f"({surface_blocked_reason})."
                        ),
                        work_surface_fail_closed=True,
                        work_surface_detection_failed=True,
                        work_surface_blocked_reason=surface_blocked_reason,
                    )
                for stroke_index, raw_stroke in enumerate(raw_strokes):
                    if not isinstance(raw_stroke, list) or len(raw_stroke) < 2:
                        raise ValueError(
                            f"frame_strokes[{stroke_index}] must contain at least two [x,y] points"
                        )
                    normalized_stroke: list[tuple[float, float]] = []
                    for point_index, raw_point in enumerate(raw_stroke):
                        if not isinstance(raw_point, (list, tuple)) or len(raw_point) != 2:
                            raise ValueError(
                                f"frame_strokes[{stroke_index}][{point_index}] must be [x,y]"
                            )
                        temp = {"frame_x": raw_point[0], "frame_y": raw_point[1]}
                        prior = mapped
                        mapped = temp
                        try:
                            physical = point("frame_x", "frame_y")
                        finally:
                            mapped = prior
                        assert physical is not None
                        normalized_stroke.append((float(raw_point[0]), float(raw_point[1])))
                    normalized_strokes.append(normalized_stroke)

                frame_preparation: dict[str, Any] = {
                    "policy": "authoritative_frame_direct_mapping",
                    "applied": False,
                    "source_coordinate_space": "current_frame_normalized_0_1000",
                    "stroke_count": len(normalized_strokes),
                    "input_point_count": sum(len(stroke) for stroke in normalized_strokes),
                    **structure_repair,
                    **stringified_repair,
                }
                if surface_bounds is not None:
                    bound_left, bound_top, bound_right, bound_bottom = surface_bounds

                    def containment(stroke: list[tuple[float, float]]) -> tuple[list[bool], int, float, bool]:
                        inside = [
                            bound_left <= x <= bound_right
                            and bound_top <= y <= bound_bottom
                            for x, y in stroke
                        ]
                        inside_count = sum(inside)
                        inside_ratio = inside_count / max(1, len(stroke))
                        return inside, inside_count, inside_ratio, bool(inside and inside[0])

                    failures: list[tuple[int, int, float, bool]] = []
                    soft_edge_tolerance_strokes: list[int] = []
                    for stroke_index, normalized_stroke in enumerate(normalized_strokes):
                        _inside, inside_count, inside_ratio, start_inside = containment(
                            normalized_stroke,
                        )
                        within_detector_margin = all(
                            bound_left - _NATIVE_DRAW_SURFACE_SOFT_MARGIN <= x
                            <= bound_right + _NATIVE_DRAW_SURFACE_SOFT_MARGIN
                            and bound_top - _NATIVE_DRAW_SURFACE_SOFT_MARGIN <= y
                            <= bound_bottom + _NATIVE_DRAW_SURFACE_SOFT_MARGIN
                            for x, y in normalized_stroke
                        )
                        soft_edge_accept = bool(
                            start_inside
                            and len(normalized_stroke)
                            >= _NATIVE_DRAW_SURFACE_SOFT_MIN_POINTS
                            and inside_ratio
                            >= _NATIVE_DRAW_SURFACE_SOFT_MIN_POINT_RATIO
                            and within_detector_margin
                        )
                        if (
                            inside_ratio < _NATIVE_DRAW_SURFACE_MIN_POINT_RATIO
                            and soft_edge_accept
                        ):
                            soft_edge_tolerance_strokes.append(stroke_index)
                        if (
                            not start_inside
                            or (
                                inside_ratio < _NATIVE_DRAW_SURFACE_MIN_POINT_RATIO
                                and not soft_edge_accept
                            )
                        ):
                            failures.append(
                                (stroke_index, inside_count, inside_ratio, start_inside),
                            )

                    frame_preparation.update({
                        "soft_edge_tolerance_applied": bool(
                            soft_edge_tolerance_strokes
                        ),
                        "soft_edge_tolerance_strokes": soft_edge_tolerance_strokes,
                        "soft_edge_minimum_inside_point_ratio": (
                            _NATIVE_DRAW_SURFACE_SOFT_MIN_POINT_RATIO
                        ),
                        "soft_edge_margin": _NATIVE_DRAW_SURFACE_SOFT_MARGIN,
                        "soft_edge_minimum_point_count": (
                            _NATIVE_DRAW_SURFACE_SOFT_MIN_POINTS
                        ),
                    })

                    if failures:
                        fitted, fit_evidence = _fit_frame_stroke_composition_to_surface(
                            normalized_strokes,
                            surface_bounds,
                        )
                        if fitted is not None:
                            normalized_strokes = fitted
                            # Keep provider-normalization evidence alongside
                            # the later shape-preserving surface fit. Otherwise
                            # a successful fit would erase why a non-canonical
                            # but unambiguous function argument was accepted.
                            frame_preparation = {
                                **frame_preparation,
                                **fit_evidence,
                            }
                        else:
                            (
                                stroke_index,
                                inside_count,
                                inside_ratio,
                                start_inside,
                            ) = failures[0]
                            normalized_stroke = normalized_strokes[stroke_index]
                            raise NativeFrameCoordinateError(
                                (
                                    f"frame_strokes[{stroke_index}] is not contained in the "
                                    "uniquely grounded work surface: every stroke must start "
                                    f"inside work_surface_frame_bounds={list(surface_bounds)} "
                                    "and every point must remain inside (long paths receive only "
                                    f"a tiny detector-edge tolerance; start_inside={start_inside}, "
                                    f"inside={inside_count}/{len(normalized_stroke)}, "
                                    f"ratio={inside_ratio:.3f}). The action was not executed."
                                ),
                                work_surface_containment_failed=True,
                                work_surface_frame_bounds=list(surface_bounds),
                                stroke_index=stroke_index,
                                stroke_start=list(normalized_stroke[0]),
                                stroke_point_count=len(normalized_stroke),
                                inside_point_count=inside_count,
                                inside_point_ratio=round(inside_ratio, 6),
                                minimum_inside_point_ratio=_NATIVE_DRAW_SURFACE_MIN_POINT_RATIO,
                                start_inside=start_inside,
                                composition_affine_fit=fit_evidence,
                            )

                simplified_strokes = [
                    _simplify_dense_frame_stroke(stroke)
                    for stroke in normalized_strokes
                ]
                input_point_count = sum(len(stroke) for stroke in normalized_strokes)
                execution_point_count = sum(len(stroke) for stroke in simplified_strokes)
                frame_preparation.update({
                    "input_point_count": input_point_count,
                    "execution_point_count": execution_point_count,
                    "points_simplified": input_point_count - execution_point_count,
                    "simplification": (
                        "near_collinear_rdp"
                        if execution_point_count < input_point_count
                        else "not_needed"
                    ),
                    "simplification_tolerance_frame_units": (
                        _NATIVE_DRAW_SIMPLIFY_TOLERANCE
                        if execution_point_count < input_point_count
                        else 0.0
                    ),
                })
                physical_strokes: list[list[list[int]]] = []
                for normalized_stroke in simplified_strokes:
                    physical_stroke: list[list[int]] = []
                    for normalized_x, normalized_y in normalized_stroke:
                        temp = {"frame_x": normalized_x, "frame_y": normalized_y}
                        prior = mapped
                        mapped = temp
                        try:
                            physical = point("frame_x", "frame_y")
                        finally:
                            mapped = prior
                        assert physical is not None
                        physical_stroke.append([physical[0], physical[1]])
                    physical_strokes.append(physical_stroke)
                mapped["strokes"] = physical_strokes
                mapped["frame_stroke_preparation"] = frame_preparation
                source = {"frame_strokes": source_frame_strokes}
                used = True
            else:
                require_normalized_instead_of_physical(("strokes",))
                raise ValueError("draw_strokes requires frame_strokes from the current frame")
        if used:
            mapped["coordinate_space"] = "current_frame_normalized_0_1000"
            mapped["visual_frame_id"] = self._visual_frame_id
            mapped["source_frame_coordinates"] = source
        return mapped

    # ----------------------------------------------------------------- api
    def _chat(self, client, tools: list[dict]):
        """Run the (blocking) completion in a worker thread so a Stop can
        abandon an in-flight LLM call immediately, instead of waiting up to the
        120s request timeout. Returns the response, or None if stopped."""
        self._roll_native_visual_context()
        decision_started = time.monotonic()
        api = CONFIG.get("api", default={})
        model = api.get("model", "deepseek-v4-flash")
        configured_max_tokens = int(api.get("max_tokens", 4096))
        gui_tool_recovery_request = bool(
            not self._is_coding and self._gui_tool_call_retry_pending
        )
        request_max_tokens = (
            min(configured_max_tokens, _GUI_TOOL_RECOVERY_MAX_TOKENS)
            if gui_tool_recovery_request
            else configured_max_tokens
        )
        # The cap is request-scoped.  A valid tool call returns the normal
        # budget to later GUI turns; a second truncated/no-tool response is
        # handled fail-closed by run() instead of scheduling another retry.
        if gui_tool_recovery_request:
            self._gui_tool_call_retry_pending = False
        # Escalate ONE decision to a stronger model when we just got stuck
        # (model-tier mitigation): the weak model flails, the strong model
        # picks the next move, then we drop back to cheap.
        if self._escalate_next:
            self._escalate_next = False
            esc = CONFIG.get("agent", "escalation_model", default="")
            if esc:
                logger.info("本步升级到更强模型: %s", esc)
                model = esc
            else:
                logger.warning("卡住需升级模型，但 agent.escalation_model 未配置，仍用 %s", model)

        thinking_mode = self._native_visual_thinking_mode_for_decision(model)
        # A GUI truncation recovery must emit one complete real function call.
        # Hidden reasoning competes with its structured argument budget, so
        # recovery is explicitly non-thinking even though it keeps enough
        # output room for a concise multi-path gesture.
        if gui_tool_recovery_request:
            thinking_mode = "disabled"
        configured_thinking = api.get("thinking")
        configured_thinking_enabled = bool(
            configured_thinking is True
            or (
                isinstance(configured_thinking, dict)
                and str(configured_thinking.get("type") or "").casefold() == "enabled"
            )
        )
        thinking_enabled = bool(
            thinking_mode == "enabled"
            or (thinking_mode is None and configured_thinking_enabled)
        )
        box: dict[str, Any] = {}
        self._last_llm_model = model
        self._last_llm_thinking_enabled = thinking_enabled
        self._trace_debug("llm_request", {
            "run_id": self.run_id,
            "task": self.task,
            "model": model,
            "temperature": float(api.get("temperature", 0.3)),
            "max_tokens": request_max_tokens,
            "max_completion_tokens": (
                request_max_tokens if thinking_enabled else None
            ),
            "thinking_mode": thinking_mode,
            "gui_tool_call_recovery": gui_tool_recovery_request,
            "messages": self.messages,
            "tools": tools,
        })

        def _is_rate_limit(exc: Exception) -> bool:
            text = str(exc).lower()
            return "429" in text or "too many requests" in text or "rate limit" in text

        def _backoff_sleep(seconds: float) -> bool:
            """Sleep in small slices so a Stop interrupts the wait. Returns
            False if a Stop was requested mid-wait."""
            waited = 0.0
            while waited < seconds:
                if self.stop_event.is_set():
                    return False
                time.sleep(0.2)
                waited += 0.2
            return True

        def worker() -> None:
            last_exc: Exception | None = None
            for attempt in range(1, _API_RETRIES + 1):
                if self.stop_event.is_set():
                    return
                try:
                    request_params: dict[str, Any] = {
                        "model": model,
                        "messages": self.messages,
                        "tools": tools,
                    }
                    if thinking_enabled:
                        request_params["max_completion_tokens"] = request_max_tokens
                    else:
                        request_params["temperature"] = float(api.get("temperature", 0.3))
                        request_params["max_tokens"] = request_max_tokens
                    if thinking_mode is not None:
                        request_params["extra_body"] = {
                            "thinking": {"type": thinking_mode},
                        }
                    box["resp"] = client.chat.completions.create(**request_params)
                    return
                except Exception as exc:
                    last_exc = exc
                    logger.warning("API 调用失败 (第%d次): %s", attempt, exc)
                    if attempt < _API_RETRIES:
                        # Rate limits (429) need a much longer wait than a
                        # transient network blip — back off exponentially
                        # (5s, 10s, 20s...) so a burst of drawing strokes
                        # doesn't kill the run. Stop interrupts the wait.
                        if _is_rate_limit(exc):
                            if not _backoff_sleep(_RATE_LIMIT_BACKOFF_S * (2 ** (attempt - 1))):
                                return
                        else:
                            if not _backoff_sleep(1.5 * attempt):
                                return
            box["exc"] = RuntimeError(f"API 连续 {_API_RETRIES} 次失败: {last_exc}")

        thread = threading.Thread(target=worker, daemon=True, name="llm-call")
        thread.start()
        while thread.is_alive():
            if self.stop_event.is_set():
                logger.info("Stop 请求：放弃进行中的 LLM 调用")
                self._trace_debug("llm_abandoned", {
                    "run_id": self.run_id,
                    "model": model,
                    "reason": "stop_event",
                })
                BUS.publish(
                    "agent_decision",
                    run_id=self.run_id,
                    model=model,
                    ok=False,
                    canceled=True,
                    elapsed_ms=int((time.monotonic() - decision_started) * 1000),
                )
                return None  # abandon the in-flight request (it finishes unused)
            thread.join(timeout=0.2)
        if "exc" in box:
            self._trace_debug("llm_error", {
                "run_id": self.run_id,
                "model": model,
                "error": str(box["exc"]),
            })
            BUS.publish(
                "agent_decision",
                run_id=self.run_id,
                model=model,
                ok=False,
                elapsed_ms=int((time.monotonic() - decision_started) * 1000),
            )
            raise box["exc"]
        if box.get("resp") is not None:
            self._trace_debug("llm_response", {
                "run_id": self.run_id,
                "model": model,
                "response": box.get("resp"),
            })
        BUS.publish(
            "agent_decision",
            run_id=self.run_id,
            model=model,
            ok=box.get("resp") is not None,
            elapsed_ms=int((time.monotonic() - decision_started) * 1000),
        )
        return box.get("resp")

    def _recover_truncated_gui_tool_call(self) -> bool:
        """Queue one bounded GUI correction, then fail closed on recurrence.

        Returns True only when the caller should run one more model turn.  The
        truncated prose is never retained as executable-looking history.
        Coding keeps its existing large-artifact recovery path in run().
        """
        if self.messages:
            self.messages[-1] = {
                "role": "assistant",
                "content": (
                    "[GUI model output truncated before a real tool call; "
                    "partial content omitted]"
                ),
            }
        if self._gui_truncated_tool_retries == 0:
            self._gui_truncated_tool_retries = 1
            self._gui_tool_call_retry_pending = True
            self.messages.append({
                "role": "user",
                "content": (
                    "Your previous GUI response hit the output limit without issuing a real "
                    "function tool call. Reply now with exactly one real tool call and no XML, "
                    "pseudo-tool markup, or prose coordinate dump. If the call uses pointer "
                    "paths, use at most 12 independent paths and at most 12 points per path; "
                    "include only endpoints and meaningful turns, never regularly spaced "
                    "samples. Submit one useful coherent part now and split later work across "
                    "fresh verified turns."
                ),
            })
            BUS.publish(
                "agent_recovery",
                run_id=self.run_id,
                kind="truncated_gui_tool_call",
                attempt=1,
                next_max_tokens=_GUI_TOOL_RECOVERY_MAX_TOKENS,
            )
            return True

        self._gui_tool_call_retry_pending = False
        self.status = "fail"
        self.result_text = (
            "Safety stop: the GUI model twice reached its output limit without issuing "
            "a real tool call. Truncated pseudo-tool content was discarded and no action "
            "from it was executed."
        )
        BUS.publish(
            "agent_recovery",
            run_id=self.run_id,
            kind="truncated_gui_tool_call_safe_stop",
            attempt=2,
            safe_stop=True,
        )
        BUS.publish(
            "chat",
            run_id=self.run_id,
            role="assistant",
            content=self.result_text,
            reasoning="",
        )
        return False

    def _track_usage(self, resp) -> None:
        api = CONFIG.get("api", default={})
        profile = model_profile_from_config({
            **api,
            "model": self._last_llm_model or api.get("model", ""),
        })
        adapter = getattr(self, "_model_adapter", None)
        if adapter is not None and not self._last_llm_model:
            profile = getattr(adapter, "profile", profile)
        step_usage = accumulate_usage(self.usage_total, resp, profile=profile, api=api)
        if not step_usage:
            return
        BUS.publish(
            "agent_usage",
            run_id=self.run_id,
            step_usage=step_usage,
            total=dict(self.usage_total),
        )

    def _track_aux_model_response(self, resp, *, purpose: str, model: str) -> None:
        previous_model = self._last_llm_model
        self._last_llm_model = str(model or previous_model or "")
        try:
            self._track_usage(resp)
        finally:
            self._last_llm_model = previous_model
        BUS.publish(
            "agent_model_call",
            run_id=self.run_id,
            purpose=str(purpose),
            model=str(model),
            accounted=True,
        )

    # ---------------------------------------------------------------- steps
    def _record_step(self, name: str, args: dict, replay: dict | None = None) -> None:
        """Log an intent-level step for experience memory.

        Prefer replay metadata (target text / command) over raw args: element
        ids and absolute coordinates are meaningless in a future session, while
        "click 保存(S)" generalizes across resolutions and layouts.
        """
        if name not in _ACTION_TOOLS:
            return
        meta = replay or {}
        line = ""
        if name == "click":
            target = meta.get("text") or ""
            line = f'click "{target}"' if target else (
                f'click ({meta["coords"][0]},{meta["coords"][1]})'
                if meta.get("coords") else "click")
        elif name == "type_text":
            text = str(meta.get("text_input") or args.get("text") or "")[:60]
            where = meta.get("element_text") or ""
            line = f'type_text "{text}"' + (f' 在"{where}"' if where else "")
        elif name == "hotkey":
            line = f'hotkey {"+".join(meta.get("keys") or args.get("keys") or [])}'
        elif name in ("launch", "run_shell"):
            line = f'{name} {str(meta.get("command") or args.get("command") or "")[:120]}'
        elif name == "focus_window":
            line = f'focus_window "{meta.get("title") or ""}"'
        elif name == "tray_click":
            line = f'tray_click "{meta.get("name") or args.get("name") or ""}"'
        elif name == "wait":
            cond = meta.get("condition") or args.get("condition") or {}
            line = f"wait {json.dumps(cond, ensure_ascii=False)}"
        elif name == "execute_subgoal":
            line = f'execute_subgoal "{str(args.get("subgoal") or meta.get("subgoal") or "")[:120]}"'
        if not line:  # scroll/drag stay positional by nature
            compact = {k: v for k, v in args.items() if k != "expect"}
            line = f"{name}({json.dumps(compact, ensure_ascii=False)})"
        self._steps_log.append(line)

    def _track_verification(self, tool_name: str, result_text: str) -> None:
        """Note external verify outcomes so a run whose actions never verified
        can't be recorded as trusted experience (anti fake-done)."""
        if tool_name not in _ACTION_TOOLS:
            return
        try:
            data = json.loads(result_text)
        except (json.JSONDecodeError, TypeError):
            return
        if isinstance(data, dict) and "verified" in data:
            ok = data["verified"] is True
            self._last_verify_ok = ok
            if ok:
                self._had_verified_action = True

    def _visible_evidence_targets(self) -> list[str]:
        targets = [
            *_visible_state_targets(self.task),
            *_required_visible_payloads(self.task),
            *_required_literal_tokens(self.task),
        ]
        plan = getattr(self.session, "plan", None)
        for step in (getattr(plan, "steps", None) or []):
            if not isinstance(step, dict) or step.get("done"):
                continue
            check = step.get("check")
            if not isinstance(check, dict):
                continue
            for key in ("text_appears", "text_contains", "window_title", "window_appears"):
                value = check.get(key)
                if isinstance(value, str) and value.strip():
                    targets.append(value.strip())
        seen: set[str] = set()
        out: list[str] = []
        for target in targets:
            target = str(target or "").strip()
            key = self._normalize_visible_text(target)
            if target and key and key not in seen:
                seen.add(key)
                out.append(target)
        return out

    def _visible_snippet(self, text: str, target: str, limit: int = 260) -> str:
        target_norm = self._normalize_visible_text(target)
        lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
        negative_markers = (
            "no elements matched",
            "did not contain query",
            "does not contain query",
            "not contain query",
            "no match",
            "not found",
            "未匹配",
            "没有命中",
            "未找到",
            "没有找到",
        )
        matched_negative = False
        matched_task_echo = False
        metadata_markers = (
            "why=", "perception trace:", "screen watch bulletin:",
            "uncertainties:", "recommended_next_controller_actions:",
            "recent_changes=", "top_candidates:", "raw element sample",
        )
        for line in lines:
            if target_norm and target_norm in self._normalize_visible_text(line):
                if any(marker in line.casefold() for marker in metadata_markers):
                    continue
                if any(marker in line.casefold() for marker in negative_markers):
                    matched_negative = True
                    continue
                if self._looks_like_task_echo_line(line, target):
                    matched_task_echo = True
                    continue
                return line[:limit]
        if matched_negative or matched_task_echo:
            return ""
        return ""

    def _looks_like_task_echo_line(self, line: str, target: str) -> bool:
        """Reject a control surface echoing the user's own command as evidence."""
        line_norm = self._normalize_visible_text(line)
        target_norm = self._normalize_visible_text(target)
        task_norm = self._normalize_visible_text(self.task)
        if not line_norm or not target_norm or target_norm not in line_norm:
            return False
        if len(line_norm) < max(24, len(target_norm) * 2):
            return False
        return line_norm in task_norm or task_norm in line_norm

    def _append_visible_evidence(self, source: str, target: str, snippet: str) -> None:
        target = str(target or "").strip()
        snippet = str(snippet or "").strip()
        if not target or not snippet:
            return
        key = (source, self._normalize_visible_text(target), self._normalize_visible_text(snippet))
        for item in self._visible_evidence_ledger:
            existing = (
                str(item.get("source") or ""),
                self._normalize_visible_text(str(item.get("target") or "")),
                self._normalize_visible_text(str(item.get("snippet") or "")),
            )
            if existing == key:
                return
        entry = {
            "source": source,
            "target": target,
            "snippet": snippet[:260],
            "screen_epoch": int(getattr(self.session, "_screen_epoch", 0) or 0),
            "tool_sequence": int(self._tool_sequence),
            "hwnd": int(getattr(self.session, "hwnd", 0) or 0),
            "at": time.time(),
        }
        self._visible_evidence_ledger.append(entry)
        self._visible_evidence_ledger = self._visible_evidence_ledger[-30:]
        if getattr(self, "session", None) is not None:
            self.session.visible_evidence_ledger = self._visible_evidence_ledger
        BUS.publish("visible_evidence", run_id=self.run_id, **entry)

    @staticmethod
    def _expect_visible_values(args: Any) -> list[str]:
        if not isinstance(args, dict):
            return []
        values: list[str] = []
        expect = args.get("expect")
        if isinstance(expect, dict):
            for key in ("text_appears", "text_contains", "window_title", "window_appears"):
                value = expect.get(key)
                if isinstance(value, str) and value.strip():
                    values.append(value.strip())
        actions = args.get("actions")
        if isinstance(actions, list):
            for action in actions:
                if not isinstance(action, dict):
                    continue
                payload = action
                if len(action) == 1 and not ("action" in action or "tool" in action):
                    only_args = next(iter(action.values()))
                    payload = only_args if isinstance(only_args, dict) else {}
                values.extend(AgentRunner._expect_visible_values(payload))
        return values

    def _track_visible_evidence(self, tool_name: str, args: dict, result_text: str) -> None:
        targets = self._visible_evidence_targets()
        if tool_name in {"observe", "read_text"}:
            haystack = self._normalize_visible_text(result_text)
            for target in targets:
                if self._normalize_visible_text(target) in haystack:
                    self._append_visible_evidence(
                        tool_name,
                        target,
                        self._visible_snippet(result_text, target),
                    )
            return
        try:
            data = json.loads(result_text or "{}")
        except (json.JSONDecodeError, TypeError):
            data = {}
        if isinstance(data, dict) and tool_name == "execute_subgoal":
            if data.get("ok") is not True or data.get("verified") is not True:
                return
            packet = data.get("evidence_packet")
            if isinstance(packet, dict):
                matched = packet.get("matched_texts") or []
                preview = str(packet.get("visible_text_preview") or "")
                if isinstance(matched, list):
                    for value in matched:
                        text = str(value or "").strip()
                        if text:
                            self._append_visible_evidence(
                                "execute_subgoal:evidence_packet",
                                text,
                                self._visible_snippet(preview, text) or f"subgoal matched visible text: {text}",
                            )
                visual_colors = packet.get("matched_visual_colors") or []
                if isinstance(visual_colors, list):
                    for value in visual_colors:
                        color = str(value or "").strip().casefold()
                        if color:
                            self._append_visible_evidence(
                                "execute_subgoal:visual_color",
                                color,
                                f"subgoal matched visual color: {color}",
                            )
        if not (isinstance(data, dict) and data.get("verified") is True):
            if not (isinstance(data, dict) and tool_name == "run_action_batch"):
                return
        if isinstance(data, dict) and tool_name == "run_action_batch":
            for item in data.get("results") or []:
                if not isinstance(item, dict):
                    continue
                if item.get("action") not in {"observe", "read_text"}:
                    continue
                preview = str(item.get("preview") or "")
                child_data = item.get("data") if isinstance(item.get("data"), dict) else {}
                if isinstance(child_data, dict):
                    preview = "\n".join(
                        part for part in [
                            preview,
                            str(child_data.get("text") or ""),
                            str(child_data.get("body") or ""),
                            str(child_data.get("visible_text") or ""),
                        ]
                        if part
                    )
                haystack = self._normalize_visible_text(preview)
                for target in targets:
                    if self._normalize_visible_text(target) in haystack:
                        self._append_visible_evidence(
                            f"run_action_batch:{item.get('action')}",
                            target,
                            self._visible_snippet(preview, target),
                        )
            return
        for value in self._expect_visible_values(args):
            self._append_visible_evidence(
                f"{tool_name}:verified_expect",
                value,
                f"verified expect text appeared: {value}",
            )

    @staticmethod
    def _strong_auto_done_target(target: str) -> bool:
        value = str(target or "").strip()
        if len(value) < 4:
            return False
        return (
            "_" in value
            or any(ch.isdigit() for ch in value)
            or (len(value) >= 6 and value.upper() == value and any(ch.isalpha() for ch in value))
        )

    def _auto_done_visible_targets(self) -> list[str]:
        if _is_arithmetic_task(self.task):
            return []
        return [
            target
            for target in self._visible_evidence_targets()
            if self._strong_auto_done_target(target)
        ]

    @staticmethod
    def _auto_done_evidence_source(source: str) -> bool:
        return source in {"observe", "read_text", "execute_subgoal:evidence_packet"} or source in {
            "run_action_batch:observe",
            "run_action_batch:read_text",
        }

    def _visible_targets_proven_by_controller_evidence(self, targets: list[str]) -> tuple[bool, list[str]]:
        seen = {
            self._normalize_visible_text(str(item.get("target") or ""))
            for item in self._visible_evidence_ledger
            if self._auto_done_evidence_source(str(item.get("source") or ""))
        }
        missing = [
            target
            for target in targets
            if self._normalize_visible_text(target) not in seen
        ]
        return not missing, missing

    @staticmethod
    def _controller_observation_text(result_text: str) -> str:
        """Extract only post-action observe/read_text evidence from a batch."""
        try:
            payload = json.loads(result_text or "{}")
        except (json.JSONDecodeError, TypeError):
            return ""
        if not isinstance(payload, dict):
            return ""
        parts: list[str] = []
        packet = payload.get("evidence_packet")
        if isinstance(packet, dict):
            visible = str(packet.get("visible_text_preview") or "").strip()
            if visible:
                parts.append(visible)
        for item in payload.get("results") or []:
            if not isinstance(item, dict) or item.get("action") not in {"observe", "read_text"}:
                continue
            text = str(item.get("full_text") or item.get("preview") or "").strip()
            if text:
                parts.append(text)
        return "\n".join(parts)

    @staticmethod
    def _controller_has_successful_mutation(result_text: str) -> bool:
        try:
            payload = json.loads(result_text or "{}")
        except (json.JSONDecodeError, TypeError):
            return False
        if not isinstance(payload, dict):
            return False
        mutation_actions = {
            "click", "click_element", "type_text", "type_into_element", "hotkey",
            "drag", "draw_strokes", "scroll", "clipboard_paste", "select_color",
        }
        return any(
            isinstance(item, dict)
            and item.get("action") in mutation_actions
            and item.get("ok") is True
            for item in (payload.get("results") or [])
        )

    def _maybe_auto_done_from_visible_evidence(
        self,
        tool_name: str,
        result: ToolResult,
        ledger_len_before: int,
    ) -> ToolResult:
        if self._is_coding or result.terminal:
            return result
        if tool_name not in {"observe", "read_text", "execute_subgoal", "run_action_batch"}:
            return result
        if _is_arithmetic_task(self.task):
            if tool_name in {"execute_subgoal", "run_action_batch"}:
                mutation_seen = self._controller_has_successful_mutation(result.text)
            else:
                mutation_seen = any(
                    (
                        history_tool in {"execute_subgoal", "run_action_batch"}
                        and self._controller_has_successful_mutation(history_text)
                    )
                    or (
                        history_tool in {"type_text", "hotkey", "click"}
                        and '"ok": true' in str(history_text or "").casefold()
                    )
                    for history_tool, _history_args, history_text in self._tool_history
                )
            if mutation_seen:
                # Observe/read_text already carry the newly sampled state.  A
                # controller result needs an independent fresh sample because
                # its compact packet may omit the display value.
                if tool_name in {"observe", "read_text"}:
                    screen = result.text
                else:
                    screen = self._controller_observation_text(result.text)
                    if not screen:
                        screen = self._fresh_done_screen()
                arithmetic_verdict = _deterministic_arithmetic_verdict(
                    self.task,
                    screen,
                )
                if arithmetic_verdict is not None and arithmetic_verdict[0]:
                    reason = arithmetic_verdict[1]
                    self.run_state.accept_verification(tool_name, reason)
                    BUS.publish("run_state", run_id=self.run_id, **self.run_state.to_dict())
                    try:
                        arithmetic_payload = json.loads(result.text or "{}")
                    except (json.JSONDecodeError, TypeError):
                        arithmetic_payload = {}
                    if not isinstance(arithmetic_payload, dict):
                        arithmetic_payload = {}
                    arithmetic_payload["auto_done"] = {
                        "ok": True,
                        "reason": reason,
                        "source": "deterministic_fresh_arithmetic",
                    }
                    if tool_name in {"observe", "read_text"}:
                        arithmetic_payload["fresh_evidence_preview"] = screen[:1000]
                    BUS.publish(
                        "agent_auto_done_candidate",
                        run_id=self.run_id,
                        tool=tool_name,
                        reason=reason,
                    )
                    return ToolResult(
                        json.dumps(arithmetic_payload, ensure_ascii=False, default=str),
                        is_observe=result.is_observe,
                        terminal="done",
                        summary=result.summary,
                        replay=result.replay,
                        image_b64=result.image_b64,
                    )
        parsed_payload: dict[str, Any] = {}
        if tool_name in {"execute_subgoal", "run_action_batch"}:
            try:
                parsed_payload = json.loads(result.text or "{}")
            except (json.JSONDecodeError, TypeError):
                parsed_payload = {}
            if (
                isinstance(parsed_payload, dict)
                and parsed_payload.get("ok") is True
                and parsed_payload.get("verified") is True
                and parsed_payload.get("saved_paths")
            ):
                _recent, artifact_evidence = self._done_evidence()
                artifact_screen = self._fresh_done_screen()
                artifact_verdict = self._deterministic_saved_file_verdict(
                    artifact_screen,
                    artifact_evidence,
                )
                if artifact_verdict is not None and artifact_verdict[0]:
                    reason = artifact_verdict[1]
                    self.run_state.accept_verification(tool_name, reason)
                    BUS.publish("run_state", run_id=self.run_id, **self.run_state.to_dict())
                    parsed_payload["auto_done"] = {
                        "ok": True,
                        "reason": reason,
                        "source": "deterministic_saved_artifact",
                    }
                    BUS.publish(
                        "agent_auto_done_candidate",
                        run_id=self.run_id,
                        tool=tool_name,
                        reason=reason,
                    )
                    return ToolResult(
                        json.dumps(parsed_payload, ensure_ascii=False, default=str),
                        is_observe=result.is_observe,
                        terminal="done",
                        summary=result.summary,
                        replay=result.replay,
                        image_b64=result.image_b64,
                    )
            if (
                not isinstance(parsed_payload, dict)
                or parsed_payload.get("ok") is not True
                or parsed_payload.get("verified") is not True
            ):
                return result
        new_entries = self._visible_evidence_ledger[ledger_len_before:]
        if not any(
            self._auto_done_evidence_source(str(item.get("source") or ""))
            for item in new_entries
        ):
            return result
        targets = self._auto_done_visible_targets()
        if not targets:
            return result
        proven, missing = self._visible_targets_proven_by_controller_evidence(targets)
        if not proven:
            self._trace_debug("auto_done_visible_evidence_wait", {
                "run_id": self.run_id,
                "tool": tool_name,
                "targets": targets,
                "missing": missing,
            })
            return result
        explicit_paths = _explicit_saved_paths(self.task)
        if explicit_paths:
            missing_paths: list[str] = []
            for path in explicit_paths:
                try:
                    if (
                        not path.is_file()
                        or path.stat().st_size <= 0
                        or path.stat().st_mtime < self._started_wall_time - 2.0
                    ):
                        missing_paths.append(str(path))
                except OSError:
                    missing_paths.append(str(path))
            if missing_paths:
                self._trace_debug("auto_done_output_file_wait", {
                    "run_id": self.run_id,
                    "tool": tool_name,
                    "targets": targets,
                    "missing_paths": missing_paths,
                })
                return result
        payload = {
            "ok": True,
            "auto_done": True,
            "reason": "all strong task-visible targets were proven by real observe/read_text/controller evidence",
            "targets": targets,
        }
        try:
            original = json.loads(result.text or "{}")
        except (json.JSONDecodeError, TypeError):
            original = {}
        if isinstance(original, dict):
            original["auto_done"] = payload
            text = json.dumps(original, ensure_ascii=False, default=str)
        else:
            text = json.dumps(payload, ensure_ascii=False, default=str)
        BUS.publish(
            "agent_auto_done_candidate",
            run_id=self.run_id,
            tool=tool_name,
            targets=targets,
            reason=payload["reason"],
        )
        self.run_state.accept_verification(tool_name, payload["reason"])
        BUS.publish("run_state", run_id=self.run_id, **self.run_state.to_dict())
        return ToolResult(
            text,
            is_observe=result.is_observe,
            terminal="done",
            summary=result.summary,
            replay=result.replay,
            image_b64=result.image_b64,
        )

    @staticmethod
    def _compact_visual_summary(summary: Any) -> Any:
        if not isinstance(summary, dict):
            return summary
        keep = {
            "width",
            "height",
            "blankish",
            "ink_ratio",
            "ink_bbox",
            "component_count",
            "likely_canvas_bbox",
        }
        out = {key: summary[key] for key in keep if key in summary}
        colors = summary.get("visible_colors")
        if isinstance(colors, dict):
            out["visible_colors"] = {
                name: {
                    key: value
                    for key, value in stats.items()
                    if key in {"pixels", "ratio", "bbox", "largest_bbox", "largest_area"}
                }
                for name, stats in colors.items()
                if isinstance(stats, dict)
            }
        likely = summary.get("likely_canvas_summary")
        if isinstance(likely, dict):
            out["likely_canvas_summary"] = AgentRunner._compact_visual_summary(likely)
        return out

    @staticmethod
    def _compact_evidence_list(value: Any, *, limit: int = 12, item_limit: int = 64) -> list[Any]:
        """Bound a diagnostic list without turning the enclosing JSON invalid."""
        if not isinstance(value, (list, tuple, set)):
            return []
        compact: list[Any] = []
        for item in list(value)[:limit]:
            if isinstance(item, str):
                compact.append(item[:item_limit])
            elif isinstance(item, (bool, int, float)) or item is None:
                compact.append(item)
            elif isinstance(item, dict):
                compact.append({
                    str(key)[:40]: (str(child)[:item_limit] if not isinstance(child, (bool, int, float)) else child)
                    for key, child in list(item.items())[:5]
                })
            else:
                compact.append(str(item)[:item_limit])
        return compact

    @staticmethod
    def _compact_visible_colors(value: Any, *, limit: int = 8) -> dict[str, Any]:
        """Keep actual colour evidence small enough to precede drawing geometry."""
        if not isinstance(value, dict):
            return {}
        compact: dict[str, Any] = {}
        for raw_name, raw_stats in list(value.items())[:limit]:
            name = str(raw_name)[:32]
            if isinstance(raw_stats, dict):
                stats = {
                    key: raw_stats[key]
                    for key in ("pixels", "ratio", "largest_area")
                    if key in raw_stats
                }
                compact[name] = stats or True
            elif isinstance(raw_stats, (bool, int, float)):
                compact[name] = raw_stats
            else:
                compact[name] = str(raw_stats)[:48]
        return compact

    @staticmethod
    def _bounded_compact_json(
        items: list[tuple[str, Any]],
        *,
        limit: int = 1800,
    ) -> str:
        """Serialize priority-ordered evidence as valid JSON within ``limit``.

        Values are inserted in outcome-first order.  If a lower-priority value
        does not fit, it is shortened structurally instead of slicing the JSON
        text and potentially deleting a later verdict or producing malformed
        JSON.  Callers therefore put every safety/result field before geometry,
        controller diagnostics, and metrics.
        """
        payload: dict[str, Any] = {}

        def encoded(candidate: dict[str, Any]) -> str:
            return json.dumps(candidate, ensure_ascii=False, default=str)

        for key, value in items:
            if value is None or key in payload:
                continue
            candidate = {**payload, key: value}
            if len(encoded(candidate)) <= limit:
                payload[key] = value
                continue

            # Preserve the key, and as much of its value as the remaining
            # budget permits.  This is especially important for
            # fast_path_advice and matched/missing colour lists.
            if isinstance(value, str):
                low, high = 0, len(value)
                best: str | None = None
                while low <= high:
                    middle = (low + high) // 2
                    shortened = value[:middle]
                    if middle < len(value) and middle >= 3:
                        shortened = shortened[:-3] + "..."
                    if len(encoded({**payload, key: shortened})) <= limit:
                        best = shortened
                        low = middle + 1
                    else:
                        high = middle - 1
                if best is not None:
                    payload[key] = best
                continue
            if isinstance(value, list):
                shortened: list[Any] = []
                if len(encoded({**payload, key: shortened})) > limit:
                    continue
                payload[key] = shortened
                for child in value:
                    candidate_list = [*shortened, child]
                    if len(encoded({**payload, key: candidate_list})) > limit:
                        break
                    shortened = candidate_list
                    payload[key] = shortened
                continue
            if isinstance(value, dict):
                shortened_dict: dict[str, Any] = {}
                if len(encoded({**payload, key: shortened_dict})) > limit:
                    continue
                payload[key] = shortened_dict
                for child_key, child_value in value.items():
                    candidate_dict = {**shortened_dict, child_key: child_value}
                    if len(encoded({**payload, key: candidate_dict})) > limit:
                        break
                    shortened_dict = candidate_dict
                    payload[key] = shortened_dict

        return encoded(payload)

    @staticmethod
    def _compact_outcome_items(
        data: dict[str, Any],
        packet: dict[str, Any] | None = None,
    ) -> list[tuple[str, Any]]:
        """Return safety and delivery conclusions in stable priority order."""
        packet = packet if isinstance(packet, dict) else {}
        items: list[tuple[str, Any]] = []
        for key in (
            "ok",
            "verified",
            "blocked",
            "mutation_applied",
            "destructive_action",
            "partial_output_destructive_guard",
            "protected_mutation",
            "guard_blocks",
            "safe_stop",
            "drawing_scene_replacement_blocked",
            "destructive_clear_blocked",
            "destructive_restart_blocked",
            "destructive_replan_blocked",
            "destructive_bulk_undo_blocked",
            "drawing_quality_repair_blocked",
            "drawing_quality_repair_limit_reached",
            "drawing_quality_repair_used",
            "drawing_quality_rejects",
            "structured_scene_verified",
            "needs_observe",
            "needs_llm_reason",
        ):
            if key in data:
                items.append((key, data.get(key)))

        # These four fields answer the important drawing question directly:
        # which requested colours really appeared, which did not, and which
        # planner-only palette misses should not trigger a redraw.
        for key in (
            "matched_visual_colors",
            "missing_visual_colors",
            "optional_missing_visual_colors",
        ):
            if key in packet:
                items.append((
                    key,
                    AgentRunner._compact_evidence_list(packet.get(key), limit=8, item_limit=32),
                ))

        effective = packet.get("effective_visual_colors")
        if effective is None:
            effective = packet.get("effective_colors")
        if effective is None and "matched_visual_colors" in packet:
            # A matched draw colour is the conservative subset known to have
            # been effectively delivered.  Raw visible colours remain below as
            # supporting pixel evidence.
            effective = packet.get("matched_visual_colors")
        if effective is not None:
            items.append((
                "effective_visual_colors",
                AgentRunner._compact_evidence_list(effective, limit=8, item_limit=32),
            ))

        visible_colors = AgentRunner._compact_visible_colors(packet.get("visible_colors"))
        if visible_colors:
            items.append(("visible_colors", visible_colors))
        for key in ("error", "warning", "fast_path_advice"):
            if data.get(key) not in (None, ""):
                value = str(data.get(key))
                items.append((key, value[:600]))
        return items

    @staticmethod
    def _compact_tool_evidence(tool_name: str, result_text: str) -> str:
        if tool_name in {"complete_subgoal", "revise_plan"}:
            plan_items: list[dict[str, Any]] = []
            for line in (result_text or "").splitlines():
                match = re.match(r"\[(?P<done>[xX ])\]\s*(?P<index>\d+)\.\s*(?P<goal>.+)", line.strip())
                if not match:
                    continue
                plan_items.append({
                    "index": int(match.group("index")),
                    "done": match.group("done").casefold() == "x",
                    "goal": match.group("goal")[:180],
                })
            first_line = (result_text or "").splitlines()[0] if (result_text or "").splitlines() else ""
            compact: dict[str, Any] = {
                "tool": tool_name,
                "message": first_line[:240],
            }
            completed_match = re.search(r"子目标\s*(\d+)", first_line)
            if completed_match:
                compact["completed_index"] = int(completed_match.group(1))
            if "已核验" in first_line:
                compact["verified"] = True
            if "所有子目标均完成" in (result_text or ""):
                compact["all_done"] = True
            if plan_items:
                compact["completed_indices"] = [
                    item["index"] for item in plan_items if item["done"]
                ]
                compact["remaining_steps"] = [
                    item for item in plan_items if not item["done"]
                ][:6]
                compact["next_unfinished"] = next(
                    (item for item in plan_items if not item["done"]),
                    None,
                )
            compact["guidance"] = (
                "continue with next_unfinished; combine obvious adjacent drawing/click routines "
                "in one execute_subgoal when possible; do not revise_plan just to restate the same todo"
            )
            return json.dumps(compact, ensure_ascii=False, default=str)[:1000]
        visual_compaction_tools = {
            "execute_subgoal", "run_action_batch", "draw_strokes", "drag",
            "zoom", "screenshot",
        }
        # Keep fresh, reasonably sized deterministic output intact. Immediate
        # lossy compaction previously reduced results such as
        # {ok, exit_code, stdout} to only {ok}; the model then repeated commands
        # because it literally could not see FILE_EXISTS/line counts/content.
        # Historical context folding still handles old bulky messages later.
        if tool_name not in visual_compaction_tools and len(result_text or "") <= 6000:
            return result_text or ""
        try:
            data = json.loads(result_text or "{}")
        except (json.JSONDecodeError, TypeError):
            return (result_text or "")[:6000]
        if not isinstance(data, dict):
            return (result_text or "")[:6000]
        if tool_name in {"run_shell", "run_detached"}:
            compact = {
                key: data.get(key)
                for key in (
                    "ok", "verified", "exit_code", "timeout", "pid",
                    "process_running", "error",
                )
                if data.get(key) is not None
            }
            for key, limit in (("stdout", 5000), ("stderr", 2000), ("output_preview", 3000)):
                value = data.get(key)
                if value in (None, ""):
                    continue
                text = str(value)
                compact[key] = (
                    text if len(text) <= limit
                    else text[:limit] + f"...[truncated {len(text) - limit} chars]"
                )
            return json.dumps(compact, ensure_ascii=False, default=str)[:8500]
        if tool_name == "execute_subgoal":
            packet = data.get("evidence_packet") if isinstance(data.get("evidence_packet"), dict) else {}
            compact_actions: list[dict[str, Any]] = []
            draw_actions: list[dict[str, Any]] = []
            for action in data.get("actions_executed") or []:
                if not isinstance(action, dict):
                    continue
                if action.get("action") == "draw_strokes":
                    draw_actions.append(action)
                    continue
                compact_action = {
                    key: action.get(key)
                    for key in (
                        "action",
                        "color",
                        "button",
                        "label",
                        "expected_region",
                    )
                    if key in action
                }
                if action.get("strokes"):
                    compact_action["stroke_count"] = len(action.get("strokes") or [])
                if compact_action:
                    compact_actions.append(compact_action)
            draw_parts = list(dict.fromkeys(
                str(action.get("semantic_part") or "")[:64]
                for action in draw_actions
                if str(action.get("semantic_part") or "").strip()
            ))
            draw_canvases: list[dict[str, Any]] = []
            seen_canvases: set[str] = set()
            for action in draw_actions:
                canvas = action.get("canvas_region")
                if not isinstance(canvas, dict):
                    continue
                compact_canvas = {
                    key: canvas.get(key)
                    for key in ("x", "y", "width", "height")
                    if key in canvas
                }
                signature = json.dumps(compact_canvas, sort_keys=True, default=str)
                if signature in seen_canvases:
                    continue
                seen_canvases.add(signature)
                draw_canvases.append(compact_canvas)
            draw_summary: dict[str, Any] = {}
            if draw_actions:
                draw_summary = {
                    "action": "draw_strokes",
                    "count": len(draw_actions),
                    "stroke_count": sum(
                        len(action.get("strokes") or []) for action in draw_actions
                    ),
                    "parts": draw_parts,
                    "canvas": draw_canvases[0] if draw_canvases else None,
                }
                if len(draw_canvases) > 1:
                    draw_summary["canvas_count"] = len(draw_canvases)

            raw_scene = packet.get("scene_structure")
            raw_scene = raw_scene if isinstance(raw_scene, dict) else {}
            scene_structure = {
                key: raw_scene.get(key)
                for key in (
                    "required",
                    "ok",
                    "quality_deferred_to_visual_judge",
                    "primitive_count",
                    "semantic_part_count",
                    "executed_draw_count",
                    "matched_visual_color_count",
                    "structured_scene_verified",
                    "incremental",
                    "prior_primitive_count",
                    "new_primitive_count",
                    "warnings",
                    "errors",
                )
                if key in raw_scene
            }
            controller_state = data.get("controller_state")
            controller_state = controller_state if isinstance(controller_state, dict) else {}
            compact_controller_state = {
                key: controller_state.get(key)
                for key in (
                    "phase", "verified", "needs_observe", "failed_at",
                    "last_action", "needs_llm_reason",
                )
                if key in controller_state
            }
            auto_items = []
            for item in (data.get("auto_completed_plan_items") or [])[:6]:
                if not isinstance(item, dict):
                    continue
                auto_items.append({
                    key: (str(item.get(key))[:140] if key == "goal" else item.get(key))
                    for key in ("index", "goal")
                    if key in item
                })

            ordered = AgentRunner._compact_outcome_items(data, packet)
            for key in (
                "matched_texts",
                "missing_texts",
                "requested_visual_colors",
                "required_visual_colors",
                "matched_required_visual_colors",
                "missing_required_visual_colors",
                "candidate_click_labels",
            ):
                if key in packet:
                    ordered.append((
                        key,
                        AgentRunner._compact_evidence_list(
                            packet.get(key), limit=8, item_limit=64,
                        ),
                    ))
            if data.get("semantic_overlap") is not None:
                ordered.append(("semantic_overlap", data.get("semantic_overlap")))
            if scene_structure:
                ordered.append(("scene_structure", scene_structure))
            if draw_summary:
                ordered.append(("draw_actions_summary", draw_summary))
            if compact_actions:
                ordered.append(("actions_executed", compact_actions[:8]))
            for key in (
                "input_applied",
                "spreadsheet_table_applied",
                "spreadsheet_row_format_applied",
                "exact_save_as_applied",
                "format_applied",
                "format_verified",
                "format_already_applied",
                "mutation_skipped",
                "save_dispatched",
                "saved_paths",
                "artifact_format_errors",
            ):
                if key in data:
                    ordered.append((key, data.get(key)))
            if auto_items or "auto_completed_plan_items" in data:
                ordered.append(("auto_completed_plan_items", auto_items))
            if "plan_after_auto_complete" in data:
                ordered.append((
                    "plan_after_auto_complete",
                    str(data.get("plan_after_auto_complete") or "")[:300],
                ))
            if compact_controller_state:
                ordered.append(("controller_state", compact_controller_state))
            for key, limit in (
                ("subgoal", 220),
                ("subgoal_route", 80),
            ):
                if data.get(key) is not None:
                    ordered.append((key, str(data.get(key))[:limit]))
            for key in ("failure_classification", "failure_recovery", "metrics"):
                if data.get(key):
                    ordered.append((key, data.get(key)))
            return AgentRunner._bounded_compact_json(ordered, limit=1800)
        if tool_name == "run_action_batch":
            compact_results: list[dict[str, Any]] = []
            for item in data.get("results") or []:
                if not isinstance(item, dict):
                    continue
                child: dict[str, Any] = {
                    "action": item.get("action"),
                    "ok": item.get("ok"),
                }
                for key in ("verified", "elapsed_ms"):
                    if key in item:
                        child[key] = item[key]
                child_data = item.get("data")
                if not isinstance(child_data, dict):
                    child_data = {}
                    preview = item.get("preview")
                    if isinstance(preview, str):
                        try:
                            parsed_preview = json.loads(preview)
                            if isinstance(parsed_preview, dict):
                                child_data = parsed_preview
                        except (json.JSONDecodeError, TypeError):
                            child_data = {}
                for key in (
                    "verified",
                    "visual_change_detected",
                    "visual_change_ratio",
                    "verify_method",
                    "strokes_drawn",
                    "frame_stroke_preparation",
                    "changed",
                    "stable",
                    "waited_ms",
                ):
                    if key in child_data:
                        child[key] = child_data[key]
                preview = item.get("preview")
                if item.get("action") in {"observe", "read_text"} and isinstance(preview, str) and preview.strip():
                    child["preview"] = preview[:500]
                if "nonvisual_summary" in child_data:
                    child["nonvisual_summary"] = AgentRunner._compact_visual_summary(
                        child_data.get("nonvisual_summary")
                    )
                compact_results.append(child)
            ordered = AgentRunner._compact_outcome_items(data)
            for key in (
                "spreadsheet_table_applied",
                "spreadsheet_row_format_applied",
                "exact_save_as_applied",
                "mutation_skipped",
                "native_action_executed",
                "native_post_action_frame_pending",
                "last_action",
            ):
                if key in data:
                    ordered.append((key, data.get(key)))
            if compact_results:
                ordered.append(("results", compact_results))
            for key in ("failure_classification", "failure_recovery", "metrics"):
                if data.get(key):
                    ordered.append((key, data.get(key)))
            return AgentRunner._bounded_compact_json(ordered, limit=1800)
        if tool_name in {"draw_strokes", "drag"} and data.get("wrapped_by_fast_controller"):
            auto = data.get("auto_visual_verify")
            auto = auto if isinstance(auto, dict) else {}
            pixel_effect_only = bool(
                auto.get("summary_kind") == "pixel_effect_only"
                and auto.get("semantic_verified") is False
                and auto.get("completion_evidence") is False
            )
            compact: dict[str, Any] = {
                "ok": data.get("ok"),
                "wrapped_by_fast_controller": data.get("wrapped_by_fast_controller"),
                "visual_change_detected": data.get("visual_change_detected"),
                "visual_change_ratio": data.get("visual_change_ratio"),
                "verify_method": data.get("verify_method"),
                "needs_observe": data.get("needs_observe"),
                "native_action_executed": data.get("native_action_executed"),
                "native_post_action_frame_pending": data.get("native_post_action_frame_pending"),
                "last_action": data.get("last_action"),
                "frame_stroke_preparation": data.get("frame_stroke_preparation"),
                "fast_path_advice": data.get("fast_path_advice"),
                "metrics": data.get("metrics"),
            }
            if pixel_effect_only:
                # ``verified`` here belongs to the low-level input/effect
                # contract.  Presenting it without the negative semantic
                # fields makes a pixel delta look like goal verification to
                # the next model turn.  Keep the action fact under an explicit
                # name and carry the verifier's actual semantic conclusion.
                compact.update({
                    "action_verified": data.get("verified"),
                    "semantic_verified": False,
                    "completion_evidence": False,
                    "summary_kind": "pixel_effect_only",
                })
            else:
                compact["verified"] = data.get("verified")
            if data.get("failure_classification"):
                compact["failure_classification"] = data.get("failure_classification")
            if data.get("failure_recovery"):
                compact["failure_recovery"] = data.get("failure_recovery")
            if auto:
                compact["auto_visual_verify"] = {
                    "source_action": auto.get("source_action"),
                    "region": auto.get("region"),
                    (
                        "action_verified" if pixel_effect_only else "verified"
                    ): auto.get("verified"),
                    "visual_change_detected": auto.get("visual_change_detected"),
                    "visual_change_ratio": auto.get("visual_change_ratio"),
                    "verify_method": auto.get("verify_method"),
                    "semantic_verified": auto.get("semantic_verified"),
                    "completion_evidence": auto.get("completion_evidence"),
                    "summary_kind": auto.get("summary_kind"),
                    "nonvisual_summary": AgentRunner._compact_visual_summary(
                        auto.get("nonvisual_summary")
                    ),
                }
            if "nonvisual_summary" in data:
                compact["nonvisual_summary"] = AgentRunner._compact_visual_summary(
                    data.get("nonvisual_summary")
                )
            return json.dumps(compact, ensure_ascii=False, default=str)[:1600]
        if tool_name in {"zoom", "screenshot"}:
            compact = {
                "ok": data.get("ok"),
                "region": data.get("region"),
                "nonvisual_summary": AgentRunner._compact_visual_summary(
                    data.get("nonvisual_summary")
                ),
            }
            return json.dumps(compact, ensure_ascii=False, default=str)[:1600]
        compact = {
            key: data.get(key)
            for key in (
                "ok", "verified", "error", "blocked", "tool",
                "wrapped_by_fast_controller", "source_action",
                "source_action_ok", "source_action_verified",
                "needs_observe", "native_action_executed",
                "native_post_action_frame_pending", "last_action",
                "fast_path_advice", "metrics",
            )
            if data.get(key) is not None
        }
        delta = data.get("screen_delta")
        if isinstance(delta, dict):
            compact["screen_delta"] = {
                key: delta.get(key)
                for key in (
                    "changed", "ratio", "elapsed_ms", "capture_latency_ms",
                    "delta_source", "transient_observed",
                    "changed_in_target_region", "foreground_matches_target",
                )
                if delta.get(key) is not None
            }
        children = data.get("results")
        if isinstance(children, list):
            compact["results"] = [
                {
                    key: item.get(key)
                    for key in ("action", "ok", "verified", "elapsed_ms")
                    if isinstance(item, dict) and item.get(key) is not None
                }
                for item in children[:8]
                if isinstance(item, dict)
            ]
        return json.dumps(compact or data, ensure_ascii=False, default=str)[:1800]

    @staticmethod
    def _should_fast_wrap_visual_action(tool_name: str, args: dict[str, Any]) -> bool:
        """Route standalone GUI mutations through the fast controller.

        The model still decides the next subgoal, but routine visible actions
        should go through the controller so they get batching metadata,
        screen-sync, anchor upgrades, and needs-observe feedback even when the
        model did not explicitly call run_action_batch.
        """
        if tool_name not in {
            "click",
            "type_text",
            "hotkey",
            "scroll",
            "focus_window",
            "draw_strokes",
            "drag",
        }:
            return False
        return not isinstance(args.get("expect"), dict)

    @staticmethod
    def _normalise_fast_wrapped_action(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        payload = dict(args)
        payload["action"] = tool_name
        return payload

    @staticmethod
    def _fast_wrapped_result(tool_name: str, args: dict[str, Any], batch_result: ToolResult) -> ToolResult:
        try:
            data = json.loads(batch_result.text or "{}")
        except (json.JSONDecodeError, TypeError):
            data = {}
        if not isinstance(data, dict):
            data = {}

        children = [item for item in (data.get("results") or []) if isinstance(item, dict)]
        action_aliases = {tool_name}
        if tool_name == "click":
            action_aliases.add("click_element")
        if tool_name == "type_text":
            action_aliases.add("type_into_element")
        source_child = next((item for item in children if item.get("action") in action_aliases), {})
        auto_child = next((item for item in children if item.get("action") == "auto_visual_verify"), {})
        source_data = source_child.get("data") if isinstance(source_child.get("data"), dict) else {}
        auto_data = auto_child.get("data") if isinstance(auto_child.get("data"), dict) else {}

        wrapped: dict[str, Any] = {
            "ok": bool(data.get("ok")) if "ok" in data else not bool(data.get("error")),
            "wrapped_by_fast_controller": True,
            "tool": tool_name,
            "source_action": source_child.get("action") or tool_name,
            "source_batch_ok": data.get("ok"),
            "source_batch_verified": data.get("verified"),
            "needs_observe": data.get("needs_observe"),
            "last_action": data.get("last_action"),
            "metrics": data.get("metrics"),
        }
        if data.get("fast_path_advice"):
            wrapped["fast_path_advice"] = data.get("fast_path_advice")
        if source_child:
            wrapped["source_action_ok"] = source_child.get("ok")
            if "verified" in source_child:
                wrapped["source_action_verified"] = source_child.get("verified")
        if "verified" in source_data:
            wrapped["source_action_verified"] = source_data.get("verified")
        if auto_child:
            wrapped["auto_visual_verify"] = auto_data or auto_child
            if "verified" in auto_data:
                wrapped["verified"] = bool(auto_data.get("verified"))
            elif "verified" in auto_child:
                wrapped["verified"] = bool(auto_child.get("verified"))
        elif "verified" in data:
            wrapped["verified"] = bool(data.get("verified"))
        for source in (auto_data, source_data, data):
            if not isinstance(source, dict):
                continue
            for key in (
                "visual_change_detected",
                "visual_change_ratio",
                "verify_method",
                "nonvisual_summary",
                "frame_stroke_preparation",
            ):
                if key in source and key not in wrapped:
                    wrapped[key] = source[key]
        wrapped["results"] = children
        return ToolResult(
            json.dumps(wrapped, ensure_ascii=False, default=str),
            summary=batch_result.summary,
            replay=batch_result.replay or {"tool": tool_name, **{k: v for k, v in args.items() if k != "expect"}},
        )

    def _dispatch_with_fast_visual_wrap(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        if not self._should_fast_wrap_visual_action(tool_name, args):
            return self.session.dispatch(tool_name, args)
        save_path = str(args.get("text") or "").strip()
        file_dialog_path = bool(
            tool_name == "type_text"
            and self._looks_like_absolute_output_path(save_path)
            and self._file_dialog_is_active()
        )
        if file_dialog_path:
            actions = [{
                "action": "type_into_element",
                "text": save_path,
                "clear": True,
                "expected_region": "bottom",
                "upgraded_from": "type_text",
            }]
            actions.extend([
                {
                    "action": "hotkey",
                    "keys": ["Enter"],
                    "expect": {"file_exists": save_path},
                },
                {"action": "wait_stable", "stable_ms": 250, "timeout_s": 2.0},
            ])
        else:
            actions = [self._normalise_fast_wrapped_action(tool_name, args)]
        batch_args = {
            "actions": actions,
        }
        if len(actions) > 1:
            batch_args["subgoal"] = f"Submit file dialog path and verify saved output: {save_path}"
        if tool_name in {"draw_strokes", "drag"}:
            batch_args["sync"] = False
        batch_result = self.session.dispatch("run_action_batch", batch_args)
        return self._fast_wrapped_result(tool_name, args, batch_result)

    @staticmethod
    def _looks_like_absolute_output_path(value: str) -> bool:
        text = str(value or "").strip().strip('"')
        return bool(
            re.match(r"^[A-Za-z]:[\\/]", text)
            and re.search(r"\.[A-Za-z0-9]{1,8}$", text)
        )

    def _file_dialog_is_active(self) -> bool:
        snapshot = getattr(self.session, "snapshot", None)
        if snapshot is None:
            return False
        title = str(getattr(snapshot, "title", "") or "").casefold()
        texts = [
            str(getattr(element, "text", "") or "").casefold()
            for element in (getattr(snapshot, "elements", None) or [])
            if str(getattr(element, "text", "") or "").strip()
        ]
        combined = "\n".join(texts)
        title_match = any(marker in title for marker in (
            "save as", "另存为", "保存为", "open file", "打开文件",
        ))
        filename_field = any(marker in combined for marker in (
            "file name", "filename", "文件名称", "文件名",
        ))
        submit_control = any(marker in combined for marker in (
            "save", "保存", "另存为",
        ))
        return title_match or (filename_field and submit_control)


















    @staticmethod
    def _looks_like_spreadsheet_task(task: str) -> bool:
        text = str(task or "")
        return bool(re.search(
            r"\b(?:spreadsheet|workbook|worksheet|cells?|cell\s+range)\b|"
            r"工作簿|工作表|表格|单元格|"
            r"=[A-Z]{1,3}\d+\s*[*+\-/]|=SUM\s*\(",
            text,
            re.IGNORECASE,
        ))

    @staticmethod
    def _document_format_specs(task: str) -> list[dict[str, Any]]:
        return [dict(spec) for spec in _task_document_format_specs(task)]

    @staticmethod
    def _exact_document_text_block(task: str) -> str:
        return _task_exact_document_text_block(task)

    def _maybe_upgrade_execute_subgoal_document_input(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        if tool_name != "execute_subgoal":
            return args
        if self._looks_like_spreadsheet_task(self.task):
            return args
        input_spec = args.get("input") if isinstance(args.get("input"), dict) else {}
        if (
            input_spec
            and input_spec.get("clear", input_spec.get("replace", True)) is False
        ):
            # Explicit append/preserve semantics are authoritative. Never
            # rewrite them into the exact-document replacement routine, whose
            # physical contract begins with Ctrl+A and Backspace.
            return args
        intent_scope = json.dumps({
            "subgoal": args.get("subgoal"),
            "click": args.get("click"),
            "clicks": args.get("clicks"),
            "input_label": input_spec.get("label") or input_spec.get("role"),
        }, ensure_ascii=False, default=str).casefold()
        blank_document_ready = bool(getattr(self.session, "_new_blank_applied", False))
        mistaken_blank_navigation = bool(
            blank_document_ready
            and re.search(
                r"\b(?:new|blank|create|document\s+title|title\s+field|template)\b|"
                r"新建|空白|创建|文档标题|标题栏|模板",
                intent_scope,
                re.IGNORECASE,
            )
        )
        if not input_spec and not mistaken_blank_navigation and not re.search(
            r"\b(?:type|input|enter|write)\b|\u8f93\u5165|\u952e\u5165|\u5199\u5165|\u5f55\u5165",
            intent_scope,
        ):
            return args
        block = self._exact_document_text_block(self.task)
        if not block:
            return args
        supplied_text = str(
            input_spec.get("text")
            or input_spec.get("value")
            or input_spec.get("text_input")
            or ""
        )
        input_label = str(input_spec.get("label") or input_spec.get("role") or "").casefold()
        block_lines = [line for line in block.splitlines() if line.strip()]
        supplied_lines = [line for line in supplied_text.splitlines() if line.strip()]
        normalize_line = lambda value: re.sub(
            r"[\s\.,;:!?。；：！？、]+$",
            "",
            str(value or "").strip().casefold(),
        )
        normalized_block_lines = {normalize_line(line) for line in block_lines if normalize_line(line)}
        normalized_supplied_lines = {
            normalize_line(line) for line in supplied_lines if normalize_line(line)
        }
        line_overlap = len(normalized_block_lines & normalized_supplied_lines)
        supplied_matches_block = bool(
            supplied_text
            and block_lines
            and (
                (
                    block_lines[0] in supplied_text
                    and block_lines[-1] in supplied_text
                )
                or (
                    len(block_lines) >= 2
                    and line_overlap / len(block_lines) >= 0.6
                )
            )
        )
        document_label = bool(re.search(
            r"\b(?:document|body|editor|page)\b|\u6587\u6863|\u6b63\u6587|\u7f16\u8f91\u533a|\u9875\u9762",
            input_label,
        ))
        home_multiline_document_input = bool(
            getattr(self.session, "_document_surface_ready", None) is False
            and len(supplied_lines) >= 2
            and block_lines
            and normalize_line(block_lines[0]) == normalize_line(supplied_lines[0])
        )
        if input_spec and not (
            supplied_matches_block or document_label or home_multiline_document_input
        ):
            return args
        lines = block_lines
        upgraded: dict[str, Any] = {
            "subgoal": "Enter the user's exact paragraph block into the focused document surface.",
            "input": {
                "text": block,
                "document_surface": True,
                "clear": True,
            },
            "verify_texts": list(dict.fromkeys([
                *lines[:1],
                *[line for line in lines if re.search(r"[A-Za-z]+[_-][A-Za-z0-9_-]+|\d{3,}", line)],
                *lines[-1:],
            ]))[:4],
            "constraints": {"wait": True},
        }
        if isinstance(args.get("target_window"), dict):
            upgraded["target_window"] = dict(args["target_window"])
        self._trace_debug("execute_subgoal_document_input_auto_upgraded", {
            "run_id": self.run_id,
            "original": args,
            "upgraded": upgraded,
            "line_count": len(lines),
        })
        return upgraded

    def _maybe_upgrade_pending_clipboard_replace(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        if tool_name not in {"execute_subgoal", "run_action_batch"}:
            return args

        # Cheap models often arrive in the destination editor and then keep
        # clicking its body or Find menu. Recover the pending cross-app
        # delivery from observable GUI history: each successful Ctrl+C opens
        # one delivery and each controller replacement consumes one. The
        # target tokens still come from the user's task, not an app rule.
        task_tokens = _clipboard_target_tokens(self.task)
        successful_copy_events = 0
        copied_fingerprints: set[str] = set()
        completed_deliveries = 0
        for history_tool, history_args, history_text in self._tool_history:
            try:
                history_payload = json.loads(history_text or "{}")
            except (json.JSONDecodeError, TypeError):
                history_payload = {}
            history_ok = not isinstance(history_payload, dict) or history_payload.get("ok", True)
            candidate_actions: list[dict[str, Any]] = []
            if history_tool == "hotkey":
                candidate_actions.append(history_args)
            elif history_tool == "run_action_batch":
                candidate_actions.extend(
                    action for action in (history_args.get("actions") or [])
                    if isinstance(action, dict) and action.get("action") == "hotkey"
                )
            for action in candidate_actions:
                action_args = dict(action.get("params") or {})
                action_args.update({key: value for key, value in action.items() if key != "params"})
                keys = {
                    str(key).strip().lower()
                    for key in (action_args.get("keys") or [])
                }
                if (
                    history_ok
                    and "c" in keys
                    and keys.intersection({"ctrl", "control", "ctl"})
                ):
                    successful_copy_events += 1
                    copied_fingerprints.update(_clipboard_fingerprints(history_payload))
            if "clipboard_replace_applied" in (history_text or ""):
                if isinstance(history_payload, dict) and history_payload.get("clipboard_replace_applied"):
                    completed_deliveries += 1
        successful_copies = (
            len(copied_fingerprints) if copied_fingerprints else successful_copy_events
        )

        semantic = json.dumps({
            "subgoal": args.get("subgoal"),
            "click": args.get("click"),
            "actions": args.get("actions"),
        }, ensure_ascii=False, default=str)
        destination_interaction = bool(re.search(
            r"document|editor|editing\s+area|body|find|replace|"
            r"文档|编辑|正文|查找|替换|点击",
            semantic,
            re.IGNORECASE,
        ))
        source_interaction = bool(re.search(
            r"address\s*bar|browser|navigate|url|copy|ctrl.?[lc]|"
            r"地址栏|浏览器|导航|复制",
            semantic,
            re.IGNORECASE,
        ))
        history_pending = (
            bool(task_tokens)
            and successful_copies > completed_deliveries
            and completed_deliveries < len(task_tokens)
            and destination_interaction
            and not source_interaction
        )

        plan = getattr(self.session, "plan", None)
        steps = getattr(plan, "steps", None)
        pending = next(
            (step for step in steps if not step.get("done")),
            None,
        ) if isinstance(steps, list) else None
        goal = str(pending.get("goal") or "") if isinstance(pending, dict) else ""
        token_match = re.search(r"\[\[[^\]\r\n]{1,100}\]\]", goal)
        plan_pending = bool(
            token_match
            and re.search(r"\breplac(?:e|ing)\b|\bpaste\b|替换|粘贴", goal, re.IGNORECASE)
            and any(
                bool(step.get("done"))
                and re.search(r"\bcopy\b|复制", str(step.get("goal") or ""), re.IGNORECASE)
                for step in (steps or [])
            )
        )
        only_focuses_editor = bool(re.search(
            r"document\s+(?:editing\s+)?(?:area|body|editor)|"
            r"editing\s+area|focus.{0,30}document|"
            r"文档编辑区|正文|聚焦.{0,20}文档|点击.{0,20}文档",
            semantic,
            re.IGNORECASE,
        )) and not re.search(
            r"\breplac(?:e|ing)\b|替换|\bclipboard\b|剪贴板",
            semantic,
            re.IGNORECASE,
        )
        if not history_pending and not (plan_pending and only_focuses_editor):
            return args
        token = task_tokens[completed_deliveries] if history_pending else token_match.group(0)
        upgraded: dict[str, Any] = {
            "subgoal": f"Use GUI Find and Replace to replace {token} with the current clipboard content",
        }
        if isinstance(args.get("target_window"), dict):
            upgraded["target_window"] = dict(args["target_window"])
        if tool_name == "run_action_batch":
            upgraded["actions"] = list(args.get("actions") or [])
            upgraded["semantic_override"] = "clipboard_replace"
        self._trace_debug("pending_clipboard_replace_auto_upgraded", {
            "run_id": self.run_id,
            "original": args,
            "pending_goal": goal,
            "clipboard_copies": successful_copies,
            "completed_deliveries": completed_deliveries,
            "history_pending": history_pending,
            "upgraded": upgraded,
        })
        return upgraded

    def _maybe_upgrade_execute_subgoal_formats(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        if tool_name not in {"execute_subgoal", "run_action_batch"} or args.get("format") or args.get("formats"):
            return args
        if self._looks_like_spreadsheet_task(self.task):
            return args
        if any(args.get(key) for key in ("input", "draw", "draws")):
            return args
        if (
            args.get("verify_texts")
            and not any(args.get(key) for key in ("click", "clicks", "format", "formats"))
            and re.search(
                r"\b(?:verify|confirm|check)\b|\u9a8c\u8bc1|\u6838\u9a8c|\u786e\u8ba4|\u68c0\u67e5",
                str(args.get("subgoal") or "").casefold(),
            )
        ):
            return args
        formats = self._document_format_specs(self.task)
        if not formats:
            return args
        # Ctrl+B is a toggle, not an idempotent setter. When an existing DOCX
        # is the explicit target, inspect its current direct formatting and do
        # not flip bold off when the requested state is already satisfied.
        for path in _explicit_saved_paths(self.task):
            try:
                if path.suffix.casefold() != ".docx" or not path.is_file():
                    continue
                _text, paragraphs, package_error = _docx_document_evidence(path)
            except OSError:
                continue
            if package_error or not paragraphs or paragraphs[0].get("bold_all") is not True:
                continue
            for spec in formats:
                if str(spec.get("range") or "").casefold() == "first_line":
                    spec.pop("bold", None)
            break
        click_spec = args.get("click") if isinstance(args.get("click"), dict) else {}
        click_specs = args.get("clicks") if isinstance(args.get("clicks"), list) else []
        model_scope = json.dumps({
            "subgoal": args.get("subgoal"),
            "click_label": click_spec.get("label") or click_spec.get("text") or click_spec.get("name"),
            "click_labels": [
                item.get("label") or item.get("text") or item.get("name")
                for item in click_specs
                if isinstance(item, dict)
            ],
        }, ensure_ascii=False, default=str).casefold()
        if not re.search(
            r"\b(?:format(?:ting|ted)?|bold|italic|align|center|centre|justify|heading|title)\b|"
            r"\bfirst\s+(?:line|paragraph)\b|"
            r"\u683c\u5f0f|\u52a0\u7c97|\u659c\u4f53|\u5bf9\u9f50|\u5c45\u4e2d|\u4e24\u7aef\u5bf9\u9f50|"
            r"\u6807\u9898|\u7b2c\u4e00(?:\u884c|\u6bb5)|\u9996(?:\u884c|\u6bb5)",
            model_scope,
            re.IGNORECASE,
        ):
            return args
        navigation_without_format = bool(re.search(
            r"\b(?:new|create|open|launch|blank|template|save\s+as)\b|"
            r"\u65b0\u5efa|\u521b\u5efa|\u6253\u5f00|\u542f\u52a8|\u7a7a\u767d|\u6a21\u677f|\u53e6\u5b58\u4e3a",
            model_scope,
        ))
        new_document_tab = bool(
            re.search(r"\btab\b|\u9009\u9879\u5361", model_scope)
            and re.search(
                r"\b(?:new|blank|document|template)\b|\u65b0\u5efa|\u7a7a\u767d|\u6587\u6863|\u6a21\u677f",
                model_scope,
            )
            and not re.search(
                r"\b(?:format(?:ting)?|bold|center|align)\b|\u683c\u5f0f|\u52a0\u7c97|\u5c45\u4e2d|\u5bf9\u9f50",
                model_scope,
            )
        )
        if navigation_without_format or new_document_tab:
            return args
        if re.search(
            r"\b(?:type|input|enter|write|insert|paste)\b|"
            r"\u8f93\u5165|\u952e\u5165|\u5199\u5165|\u5f55\u5165|\u63d2\u5165|\u7c98\u8d34",
            model_scope,
        ):
            return args
        if not re.search(
            r"\b(?:format(?:ting)?|bold|center|align|heading|title|first\s+(?:line|paragraph)|body)\b|"
            r"\u683c\u5f0f|\u52a0\u7c97|\u5c45\u4e2d|\u5bf9\u9f50|"
            r"\u7b2c\u4e00(?:\u884c|\u6bb5)|\u9996(?:\u884c|\u6bb5)|\u6b63\u6587|\u6807\u9898|"
            r"\u540e(?:\u7eed|\u9762|\u4e09)?\u6bb5|\u5176\u4ed6\u6bb5\u843d",
            model_scope,
        ):
            return args
        save_after = bool(re.search(
            r"\b(?:save|ctrl\s*\+\s*s)\b|\u4fdd\u5b58",
            str(self.task or "").casefold(),
        ))
        save_as_requested = bool(re.search(
            r"\bsave\s+as\b|\u53e6\u5b58\u4e3a|\u53e6\u5b58",
            str(self.task or "").casefold(),
        ))
        save_path = ""
        if save_as_requested:
            explicit_paths = _explicit_saved_paths(self.task)
            if explicit_paths:
                save_path = str(explicit_paths[0])
        upgraded = {
            "subgoal": "Apply all explicitly requested document text formatting in one local routine.",
            "formats": formats,
            "constraints": {
                "wait": bool(save_after),
                "save_after": bool(save_after),
            },
        }
        if save_path:
            upgraded["constraints"]["save_path"] = save_path
        if tool_name == "run_action_batch":
            # Preserve a non-empty actions array for schema compatibility, but
            # FastToolMixin will replace these fragile model-authored cursor
            # movements with the semantic formats above before execution.
            upgraded["actions"] = list(args.get("actions") or [])
            upgraded["semantic_override"] = "document_format"
        if isinstance(args.get("target_window"), dict):
            upgraded["target_window"] = dict(args["target_window"])
        self._trace_debug("execute_subgoal_format_auto_upgraded", {
            "run_id": self.run_id,
            "original": args,
            "upgraded": upgraded,
        })
        return upgraded

    def _maybe_deliver_clipboard_after_focus(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: ToolResult,
    ) -> ToolResult:
        """Continue a pending GUI copy into a named destination document.

        This sits below planner/tool-choice quality. A model may choose a bare
        focus_window followed by dozens of clicks instead of execute_subgoal;
        once the requested destination document is foreground, the controller
        can safely consume the already copied clipboard value itself.
        """
        if tool_name != "focus_window":
            return result
        try:
            payload = json.loads(result.text or "{}")
        except (json.JSONDecodeError, TypeError):
            return result
        if not isinstance(payload, dict) or not payload.get("ok"):
            return result
        focus_data = payload
        title = str(payload.get("title") or payload.get("foreground_title") or "")
        if not title:
            for item in payload.get("results") or []:
                if not isinstance(item, dict):
                    continue
                data = item.get("data")
                if not isinstance(data, dict):
                    continue
                candidate_title = str(
                    data.get("title")
                    or data.get("foreground_title")
                    or (data.get("post_state") or {}).get("foreground_title")
                    or ""
                )
                if candidate_title:
                    focus_data = data
                    title = candidate_title
                    break
        named_targets = re.findall(
            r"[A-Za-z0-9_.-]+\.(?:docx?|xlsx?|pptx?|odt|ods|rtf|txt|md|csv)",
            self.task,
            re.IGNORECASE,
        )
        if not title or not any(name.casefold() in title.casefold() for name in named_targets):
            return result
        hwnd = focus_data.get("hwnd") or payload.get("hwnd") or args.get("hwnd")
        candidate = {
            "subgoal": "Focus the destination document editor for the pending clipboard delivery",
            "target_window": {"hwnd": hwnd} if hwnd else {},
            # The semantic override replaces this placeholder action before
            # controller execution; a non-empty batch satisfies the external
            # tool contract without relying on natural-language-only routing.
            "actions": [{"action": "observe"}],
        }
        upgraded = self._maybe_upgrade_pending_clipboard_replace("run_action_batch", candidate)
        if upgraded is candidate or upgraded.get("subgoal") == candidate["subgoal"]:
            return result
        self._trace_debug("clipboard_delivery_triggered_after_focus", {
            "run_id": self.run_id,
            "focus_result": payload,
            "delivery": upgraded,
        })
        delivery = self._dispatch_with_fast_visual_wrap("run_action_batch", upgraded)
        try:
            delivery_payload = json.loads(delivery.text or "{}")
        except (json.JSONDecodeError, TypeError):
            return delivery
        if isinstance(delivery_payload, dict):
            delivery_payload["auto_triggered_after_focus"] = True
            delivery_payload["focused_destination_title"] = title
            delivery.text = json.dumps(delivery_payload, ensure_ascii=False)
        return delivery

    def _duplicate_clipboard_delivery_result(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> ToolResult | None:
        if tool_name not in {"execute_subgoal", "run_action_batch"}:
            return None
        tokens = _clipboard_target_tokens(self.task)
        if not tokens:
            return None
        semantic = json.dumps(args, ensure_ascii=False, default=str)
        if not any(token in semantic for token in tokens):
            return None
        if not re.search(r"replace|paste|替换|粘贴", semantic, re.IGNORECASE):
            return None
        copy_events = 0
        copied_fingerprints: set[str] = set()
        deliveries = 0
        verified_deliveries = 0
        for history_tool, history_args, history_text in self._tool_history:
            try:
                payload = json.loads(history_text or "{}")
            except (json.JSONDecodeError, TypeError):
                payload = {}
            if isinstance(payload, dict) and payload.get("clipboard_replace_applied"):
                deliveries += 1
                if payload.get("verified") is True:
                    verified_deliveries += 1
            candidate_actions = []
            if history_tool == "hotkey":
                candidate_actions = [history_args]
            elif history_tool == "run_action_batch":
                candidate_actions = list(history_args.get("actions") or [])
            for action in candidate_actions:
                if not isinstance(action, dict) or (
                    history_tool == "run_action_batch" and action.get("action") != "hotkey"
                ):
                    continue
                action_args = dict(action.get("params") or {})
                action_args.update({key: value for key, value in action.items() if key != "params"})
                keys = {str(key).strip().casefold() for key in action_args.get("keys") or []}
                if "c" in keys and keys.intersection({"ctrl", "control", "ctl"}):
                    if not isinstance(payload, dict) or payload.get("ok", True):
                        copy_events += 1
                        copied_fingerprints.update(_clipboard_fingerprints(payload))
        copies = len(copied_fingerprints) if copied_fingerprints else copy_events
        if deliveries <= 0 or copies > deliveries:
            return None
        return ToolResult(json.dumps({
            "ok": verified_deliveries > 0,
            "blocked": True,
            "duplicate_clipboard_delivery": True,
            "mutation_applied": True,
            "verified": verified_deliveries > 0,
            "fast_path_advice": (
                "This clipboard delivery was already verified and no newer Ctrl+C copy exists. "
                "Do not mutate the placeholder again. Save the destination and continue with "
                "the next distinct transfer."
                if verified_deliveries > 0
                else "This clipboard delivery was already applied. Read/observe it before saving."
            ),
        }, ensure_ascii=False))

    def _maybe_upgrade_spreadsheet_action_batch(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Collapse model-authored cell-by-cell batches into one table paste."""
        if tool_name != "run_action_batch" or not self._looks_like_spreadsheet_task(self.task):
            return args
        if args.get("semantic_override"):
            return args
        actions = args.get("actions") if isinstance(args.get("actions"), list) else []
        scope = f"{args.get('subgoal') or ''} {json.dumps(actions, ensure_ascii=False, default=str)}"
        if re.search(
            r"\bbold\b.{0,60}\brow\s*\d+|\brow\s*\d+.{0,60}\bbold\b|"
            r"加粗.{0,40}第?\s*\d+\s*行|第?\s*\d+\s*行.{0,40}加粗",
            scope,
            re.IGNORECASE,
        ):
            applied_rows = getattr(self.session, "_spreadsheet_bold_rows_applied", set())
            paths = _explicit_saved_paths(self.task)
            saved_paths = getattr(self.session, "_exact_save_paths_applied", set())
            if not isinstance(saved_paths, set):
                saved_paths = set()
            if (
                isinstance(applied_rows, set)
                and applied_rows
                and paths
                and str(paths[0]).casefold() not in saved_paths
            ):
                save_seed = dict(args)
                save_seed["subgoal"] = f"Save As the exact requested path: {paths[0]}"
                self._trace_debug("spreadsheet_format_repeat_advanced_to_save", {
                    "run_id": self.run_id,
                    "applied_rows": sorted(applied_rows),
                    "save_path": str(paths[0]),
                })
                return self._maybe_upgrade_exact_save_as(tool_name, save_seed)
            upgraded = dict(args)
            upgraded["semantic_override"] = "spreadsheet_row_format"
            self._trace_debug("spreadsheet_row_format_batch_auto_upgraded", {
                "run_id": self.run_id,
                "original_action_count": len(actions),
                "subgoal": args.get("subgoal"),
            })
            return upgraded
        type_count = sum(
            1 for action in actions
            if isinstance(action, dict)
            and str(action.get("action") or action.get("tool") or "") in {"type_text", "type_into_element"}
        )
        table_intent = bool(re.search(
            r"\b(?:all|complete|entire|whole)\s+table\b|"
            r"\btable\s+(?:data|content)\b|\brow\s+by\s+row\b|"
            r"全部(?:表格|数据)|整个表格|表格(?:数据|内容)|逐行",
            scope,
            re.IGNORECASE,
        ))
        if not table_intent and type_count < 4:
            return args
        upgraded = dict(args)
        upgraded["semantic_override"] = "spreadsheet_table"
        self._trace_debug("spreadsheet_action_batch_auto_upgraded", {
            "run_id": self.run_id,
            "original_action_count": len(actions),
            "type_action_count": type_count,
            "subgoal": args.get("subgoal"),
        })
        return upgraded

    def _maybe_upgrade_exact_save_as(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        if tool_name not in {"execute_subgoal", "run_action_batch"}:
            return args
        paths = _explicit_saved_paths(self.task)
        if not paths:
            return args
        click_spec = args.get("click") if isinstance(args.get("click"), dict) else {}
        scope = " ".join([
            str(args.get("subgoal") or ""),
            str(click_spec.get("label") or click_spec.get("text") or click_spec.get("name") or ""),
            json.dumps(args.get("actions") or [], ensure_ascii=False, default=str),
        ])
        if not re.search(
            r"\bsave\s+as\b|\bsave\b|\bfile\s+menu\b|"
            r"另存为|另存|保存|文件菜单|点击.{0,12}文件",
            scope,
            re.IGNORECASE,
        ):
            return args
        save_path = str(paths[0])
        upgraded: dict[str, Any] = {
            "subgoal": f"Save As the exact requested path: {save_path}",
            "input": {
                "label": "File name",
                "text": save_path,
                "clear": True,
            },
            "click": {"label": "Save", "fallback_text": ["保存", "Save"]},
            "verify_texts": [Path(save_path).name],
            "constraints": {
                "open_save_as": True,
                "save_path": save_path,
                "wait": True,
            },
        }
        if isinstance(args.get("target_window"), dict):
            upgraded["target_window"] = dict(args["target_window"])
        if tool_name == "run_action_batch":
            upgraded["actions"] = list(args.get("actions") or [])
            upgraded["semantic_override"] = "exact_save_as"
        self._trace_debug("exact_save_as_auto_upgraded", {
            "run_id": self.run_id,
            "original": args,
            "upgraded": upgraded,
        })
        return upgraded

    def _maybe_auto_complete_document_format_plan_items(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: ToolResult,
    ) -> ToolResult:
        if tool_name not in {"execute_subgoal", "run_action_batch"} or not args.get("formats"):
            return result
        try:
            payload = json.loads(result.text or "{}")
        except (json.JSONDecodeError, TypeError):
            return result
        if not isinstance(payload, dict) or payload.get("format_applied") is not True:
            return result
        plan = getattr(self.session, "plan", None)
        steps = getattr(plan, "steps", None) or []
        completed: list[dict[str, Any]] = []
        fresh_paths = self._fresh_explicit_output_paths() if payload.get("save_dispatched") else []
        format_errors: list[str] = []
        format_verified = False
        for path in fresh_paths:
            if path.suffix.casefold() != ".docx":
                continue
            try:
                _document_text, paragraphs, package_error = _docx_document_evidence(path)
                if package_error:
                    format_errors.append(package_error)
                    continue
                errors = _docx_task_format_errors(self.task, paragraphs)
            except (OSError, ValueError, KeyError):
                continue
            if errors:
                format_errors.extend(errors)
                continue
            format_verified = True
            break
        if fresh_paths:
            payload["saved_paths"] = [str(path) for path in fresh_paths]
        if format_errors:
            payload["artifact_format_errors"] = list(dict.fromkeys(format_errors))[:8]
            payload["verified"] = False
            payload["needs_llm_reason"] = True
            payload["postcondition_pending"] = False
            controller_state = payload.get("controller_state")
            if isinstance(controller_state, dict):
                controller_state["needs_llm_reason"] = True
                controller_state["verified"] = False
            payload["fast_path_advice"] = (
                "The document was saved, but deterministic DOCX inspection found formatting "
                f"mismatches: {'; '.join(payload['artifact_format_errors'])}. Do not repeat the "
                "identical toggle routine. Re-observe the current document state and use a corrective, "
                "state-aware formatting action."
            )
            return ToolResult(
                json.dumps(payload, ensure_ascii=False, default=str),
                is_observe=result.is_observe,
                terminal=result.terminal,
                summary=result.summary,
                replay=result.replay,
                image_b64=result.image_b64,
            )
        if format_verified:
            payload["format_verified"] = True
            payload["verified"] = True
            payload["postcondition_pending"] = False
            packet = payload.get("evidence_packet")
            if not isinstance(packet, dict):
                packet = {}
                payload["evidence_packet"] = packet
            packet["format_verified"] = True
            packet["saved_paths"] = [str(path) for path in fresh_paths]
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict) or step.get("done"):
                continue
            goal = str(step.get("goal") or "")
            folded = goal.casefold()
            is_save_or_verify = bool(re.search(
                r"\bsave\b|\bexport\b|\bverify\b|\u4fdd\u5b58|\u53e6\u5b58|\u5bfc\u51fa|\u9a8c\u8bc1|\u6838\u9a8c|\u786e\u8ba4",
                folded,
            ))
            if is_save_or_verify:
                if not (payload.get("save_dispatched") and fresh_paths and format_verified):
                    break
                if re.search(r"\b(?:window|title)\b|\u7a97\u53e3|\u6807\u9898", folded):
                    current_title = str(
                        getattr(getattr(self.session, "snapshot", None), "title", "") or ""
                    )
                    if not current_title or not any(path.name in current_title for path in fresh_paths):
                        break
                if plan.complete(index):
                    completed.append({
                        "index": index,
                        "goal": goal[:160],
                        "source": "saved_docx_artifact",
                    })
                continue
            if not re.search(
                r"\b(?:select|format|bold|center|align|heading|title|paragraph|body)\b|"
                r"\u9009\u4e2d|\u683c\u5f0f|\u52a0\u7c97|\u5c45\u4e2d|\u5bf9\u9f50|\u7b2c\u4e00\u884c|\u6b63\u6587|\u6bb5\u843d|\u6807\u9898",
                folded,
            ):
                break
            if plan.complete(index):
                completed.append({"index": index, "goal": goal[:160], "source": "format_routine"})
        if not completed and not fresh_paths and not format_errors:
            return result
        payload["auto_completed_plan_items"] = completed
        payload["plan_after_auto_complete"] = plan.render()
        if format_verified and plan.all_done():
            payload["plan_all_done"] = True
            payload["auto_done"] = True
            payload["auto_done_reason"] = (
                "saved DOCX passed deterministic text-format checks and the target window title "
                "still identifies the exact output file"
            )
        payload["fast_path_advice"] = (
            str(payload.get("fast_path_advice") or "").strip()
            + (
                " Formatting and Save were applied in one controller batch and the saved DOCX "
                "artifact passed deterministic formatting checks. Do not repeat formatting."
                if format_verified else
                " Formatting selection and shortcuts were applied in one controller batch. "
                "Continue directly to Save, then rely on artifact verification instead of repeating formatting."
            )
        ).strip()
        return ToolResult(
            json.dumps(payload, ensure_ascii=False, default=str),
            is_observe=result.is_observe,
            terminal="done" if payload.get("auto_done") else result.terminal,
            summary=result.summary,
            replay=result.replay,
            image_b64=result.image_b64,
        )









    @staticmethod
    def _payload_has_verified_drag(payload: dict[str, Any]) -> bool:
        if not isinstance(payload, dict):
            return False
        if payload.get("verified") is not True:
            return False
        state = payload.get("controller_state")
        if isinstance(state, dict) and state.get("last_action") == "drag":
            return True
        if payload.get("source_action") == "drag" and payload.get("visual_change_detected") is True:
            return True
        for item in payload.get("results") or []:
            if not isinstance(item, dict) or item.get("action") != "drag":
                continue
            data = item.get("data")
            if isinstance(data, dict) and (
                data.get("verified") is True or data.get("visual_change_detected") is True
            ):
                return True
            preview = str(item.get("preview") or "")
            if '"visual_change_detected": true' in preview.casefold() or '"verified": true' in preview.casefold():
                return True
        return False

    @staticmethod
    def _looks_like_desktop_drag_plan_goal(goal: str) -> bool:
        text = str(goal or "")
        low = text.casefold()
        has_drag = any(term in low for term in ("drag", "move", "icon", "folder"))
        has_drag = has_drag or any(term in text for term in ("拖动", "移动", "图标", "文件夹", "整理", "排列"))
        return has_drag

    def _maybe_auto_complete_desktop_drag_plan_item(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: ToolResult,
    ) -> ToolResult:
        if tool_name not in {"run_action_batch", "drag"} or not _is_desktop_icon_drag_task(self.task):
            return result
        try:
            payload = json.loads(result.text or "{}")
        except (json.JSONDecodeError, TypeError):
            return result
        if not self._payload_has_verified_drag(payload):
            return result

        plan = getattr(self.session, "plan", None)
        steps = getattr(plan, "steps", None) or []
        completed: list[dict[str, Any]] = []
        if steps:
            for index, step in enumerate(steps, start=1):
                if not isinstance(step, dict) or step.get("done"):
                    continue
                goal = str(step.get("goal") or "")
                if not self._looks_like_desktop_drag_plan_goal(goal):
                    continue
                if plan.complete(index):
                    item = {"index": index, "goal": goal[:160], "source": tool_name}
                    completed.append(item)
                    BUS.publish(
                        "agent_plan",
                        run_id=self.run_id,
                        action="auto_complete",
                        index=index,
                        source=f"{tool_name}.verified_drag",
                        plan=plan.snapshot(),
                    )
                break
            if not completed:
                return result
            payload["auto_completed_plan_items"] = completed
            payload["plan_after_auto_complete"] = plan.render()
            if plan.all_done():
                payload["plan_all_done"] = True
        else:
            payload["plan_all_done"] = True

        existing_advice = str(payload.get("fast_path_advice") or "").strip()
        auto_advice = (
            "Auto-completed desktop drag plan item from verified controller drag "
            "evidence; "
            + ("plan is complete." if payload.get("plan_all_done") else "continue with next unfinished item.")
        )
        payload["fast_path_advice"] = (existing_advice + " " + auto_advice).strip()
        self._trace_debug("desktop_drag_plan_auto_completed", {
            "run_id": self.run_id,
            "tool": tool_name,
            "args": args,
            "completed": completed,
            "plan_all_done": payload.get("plan_all_done"),
        })
        return ToolResult(
            json.dumps(payload, ensure_ascii=False, default=str),
            is_observe=result.is_observe,
            terminal="done" if payload.get("plan_all_done") else result.terminal,
            summary=result.summary,
            replay=result.replay,
            image_b64=result.image_b64,
        )

    def _maybe_auto_complete_open_plan_item(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: ToolResult,
    ) -> ToolResult:
        if tool_name not in {
            "open_app", "launch", "focus_window", "execute_subgoal", "run_action_batch",
        }:
            return result
        plan = getattr(self.session, "plan", None)
        steps = getattr(plan, "steps", None) or []
        if not steps:
            return result
        try:
            payload = json.loads(result.text or "{}")
        except (json.JSONDecodeError, TypeError):
            return result
        if not isinstance(payload, dict) or not payload.get("ok"):
            return result
        if (
            tool_name in {"execute_subgoal", "run_action_batch"}
            and payload.get("subgoal_route") != "open_app_capability"
        ):
            return result
        if not (payload.get("verified") or payload.get("target_hwnd")):
            return result
        completed: list[dict[str, Any]] = []
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict) or step.get("done"):
                continue
            goal = str(step.get("goal") or "")
            if not _looks_like_pure_open_plan_goal(goal):
                break
            if plan.complete(index):
                item = {"index": index, "goal": goal[:160], "source": tool_name}
                completed.append(item)
                BUS.publish(
                    "agent_plan",
                    run_id=self.run_id,
                    action="auto_complete",
                    index=index,
                    source=f"{tool_name}.verified_target",
                    plan=plan.snapshot(),
                )
            break
        if not completed:
            return result
        payload["auto_completed_plan_items"] = completed
        payload["plan_after_auto_complete"] = plan.render()
        payload["fast_path_advice"] = (
            str(payload.get("fast_path_advice") or "").strip()
            + " Verified launch/focus already completed the matching open-window "
              "plan item; continue with the next unfinished item."
        ).strip()
        self._trace_debug("open_plan_auto_completed", {
            "run_id": self.run_id,
            "tool": tool_name,
            "args": args,
            "completed": completed,
        })
        return ToolResult(
            json.dumps(payload, ensure_ascii=False, default=str),
            is_observe=result.is_observe,
            terminal=result.terminal,
            summary=result.summary,
            replay=result.replay,
            image_b64=result.image_b64,
        )

    @staticmethod
    def _plan_text_key(value: object) -> str:
        text = re.sub(r"^\s*\d+[.)、]\s*", "", str(value or ""))
        text = re.sub(r"(?:已完成|完成后|然后|接着|最后)", "", text)
        return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", text).casefold()

    @classmethod
    def _subgoal_matches_plan_goal(cls, subgoal: str, goal: str) -> bool:
        left = cls._plan_text_key(subgoal)
        right = cls._plan_text_key(goal)
        if min(len(left), len(right)) < 3:
            return False
        if left in right or right in left:
            return True
        left_pairs = {left[index:index + 2] for index in range(len(left) - 1)}
        right_pairs = {right[index:index + 2] for index in range(len(right) - 1)}
        if not left_pairs or not right_pairs:
            return False
        overlap = len(left_pairs & right_pairs) / max(1, min(len(left_pairs), len(right_pairs)))
        return overlap >= 0.72

    def _plan_check_satisfied_by_subgoal(
        self,
        check: dict[str, Any] | None,
        payload: dict[str, Any],
    ) -> bool:
        if not check:
            return True
        if not isinstance(check, dict):
            return False
        file_path = check.get("file_exists")
        if isinstance(file_path, str) and file_path.strip():
            try:
                path = Path(file_path)
                if not path.is_file() or path.stat().st_size <= 0:
                    return False
                if path.stat().st_mtime < self._started_wall_time - 2.0:
                    return False
            except OSError:
                return False
        packet = payload.get("evidence_packet")
        matched = {
            self._plan_text_key(value)
            for value in ((packet or {}).get("matched_texts") or [])
            if isinstance(packet, dict)
        }
        for key in ("text_appears", "text_contains", "window_appears", "window_title"):
            value = check.get(key)
            if isinstance(value, str) and self._plan_text_key(value) not in matched:
                title = str(getattr(getattr(self.session, "snapshot", None), "title", "") or "")
                if self._plan_text_key(value) not in self._plan_text_key(title):
                    return False
        supported = {
            "file_exists", "text_appears", "text_contains", "window_appears", "window_title",
        }
        return all(key in supported for key in check)

    def _fresh_explicit_output_paths(self) -> list[Path]:
        paths: list[Path] = []
        for path in _explicit_saved_paths(self.task):
            try:
                if (
                    path.is_file()
                    and path.stat().st_size > 0
                    and path.stat().st_mtime >= self._started_wall_time - 2.0
                ):
                    paths.append(path)
            except OSError:
                continue
        return paths

    def _maybe_auto_complete_verified_subgoal_plan_items(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: ToolResult,
    ) -> ToolResult:
        try:
            payload = json.loads(result.text or "{}")
        except (json.JSONDecodeError, TypeError):
            return result
        if tool_name != "execute_subgoal" and not (
            isinstance(payload, dict) and payload.get("auto_triggered_after_focus")
        ):
            return result
        plan = getattr(self.session, "plan", None)
        steps = getattr(plan, "steps", None) or []
        if not steps:
            return result
        if not isinstance(payload, dict) or payload.get("verified") is not True:
            return result
        subgoal = str(
            payload.get("subgoal")
            if payload.get("auto_triggered_after_focus")
            else args.get("subgoal") or payload.get("subgoal") or ""
        )
        completed: list[dict[str, Any]] = []
        clipboard_token = str(payload.get("clipboard_replace_token") or "").strip()
        if payload.get("clipboard_replace_applied") and clipboard_token:
            for index, step in enumerate(steps, start=1):
                if not isinstance(step, dict) or step.get("done"):
                    continue
                goal = str(step.get("goal") or "")
                if clipboard_token not in goal and not re.search(
                    r"replace|paste|替换|粘贴",
                    goal,
                    re.IGNORECASE,
                ):
                    continue
                if plan.complete(index):
                    completed.append({
                        "index": index,
                        "goal": goal[:160],
                        "source": "verified_clipboard_delivery",
                    })
                    BUS.publish(
                        "agent_plan",
                        run_id=self.run_id,
                        action="auto_complete",
                        index=index,
                        source="controller.clipboard_replace_verified",
                        plan=plan.snapshot(),
                    )
                break
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict) or step.get("done"):
                continue
            goal = str(step.get("goal") or "")
            if not self._subgoal_matches_plan_goal(subgoal, goal):
                break
            if not self._plan_check_satisfied_by_subgoal(step.get("check"), payload):
                break
            if plan.complete(index):
                completed.append({"index": index, "goal": goal[:160], "source": "verified_subgoal"})
                BUS.publish(
                    "agent_plan",
                    run_id=self.run_id,
                    action="auto_complete",
                    index=index,
                    source="execute_subgoal.verified",
                    plan=plan.snapshot(),
                )
            break

        fresh_paths = self._fresh_explicit_output_paths()
        if fresh_paths:
            for index, step in enumerate(steps, start=1):
                if not isinstance(step, dict) or step.get("done"):
                    continue
                goal = str(step.get("goal") or "")
                key = self._plan_text_key(goal)
                if not any(term in key for term in (
                    "确认文件", "核验文件", "验证文件", "保存成功", "fileexists", "verifysaved",
                )):
                    continue
                if step.get("check") and not self._plan_check_satisfied_by_subgoal(step.get("check"), payload):
                    continue
                if plan.complete(index):
                    completed.append({
                        "index": index,
                        "goal": goal[:160],
                        "source": "fresh_output_file",
                        "path": str(fresh_paths[0]),
                    })
                    BUS.publish(
                        "agent_plan",
                        run_id=self.run_id,
                        action="auto_complete",
                        index=index,
                        source="execute_subgoal.fresh_output_file",
                        plan=plan.snapshot(),
                    )
        if not completed:
            return result
        payload["auto_completed_plan_items"] = completed
        payload["plan_after_auto_complete"] = plan.render()
        if plan.all_done():
            payload["plan_all_done"] = True
        payload["fast_path_advice"] = (
            str(payload.get("fast_path_advice") or "").strip()
            + " Verified controller evidence automatically advanced matching plan items; "
            + ("the plan is complete." if plan.all_done() else "continue with the next unfinished item.")
        ).strip()
        return ToolResult(
            json.dumps(payload, ensure_ascii=False, default=str),
            is_observe=result.is_observe,
            terminal="done" if plan.all_done() else result.terminal,
            summary=result.summary,
            replay=result.replay,
            image_b64=result.image_b64,
        )

    @classmethod
    def _looks_like_new_blank_plan_goal(cls, goal: str) -> bool:
        key = cls._plan_text_key(goal)
        wants_new = any(term in key for term in (
            "new", "create", "blank", "新建", "创建", "新增",
        ))
        document_kind = any(term in key for term in (
            "document", "doc", "workbook", "spreadsheet", "presentation",
            "文档", "文字", "工作簿", "表格", "演示文稿", "空白",
        ))
        return wants_new and document_kind

    @staticmethod
    def _looks_like_unsaved_document_title(title: str) -> bool:
        text = re.sub(r"\s+", "", str(title or "").casefold())
        if not text or any(term in text for term in (
            "新建文档", "newdocument", "template", "模板", "homepage", "首页",
        )):
            return False
        return bool(re.search(
            r"(?:untitled|document|book|workbook|presentation|"
            r"文字文稿|演示文稿|工作簿|无标题|未命名|文档)\s*\d*",
            text,
            re.IGNORECASE,
        ))

    def _current_new_document_plan_item(self) -> tuple[int, dict[str, Any]] | None:
        plan = getattr(self.session, "plan", None)
        for index, step in enumerate(getattr(plan, "steps", None) or [], start=1):
            if not isinstance(step, dict) or step.get("done"):
                continue
            if self._looks_like_new_blank_plan_goal(str(step.get("goal") or "")):
                return index, step
            break
        return None

    @staticmethod
    def _text_mutation_literals(tool_name: str, args: dict[str, Any]) -> list[str]:
        values: list[str] = []
        if tool_name in {"type_text", "type_into_element"}:
            values.append(str(args.get("text") or args.get("text_input") or args.get("value") or ""))
        elif tool_name == "execute_subgoal":
            input_spec = args.get("input") if isinstance(args.get("input"), dict) else {}
            values.append(str(
                input_spec.get("text")
                or input_spec.get("text_input")
                or input_spec.get("value")
                or ""
            ))
        elif tool_name == "run_action_batch":
            for action in args.get("actions") or []:
                if not isinstance(action, dict):
                    continue
                name = str(action.get("action") or action.get("tool") or "")
                action_args = action
                if not name and len(action) == 1:
                    name, nested = next(iter(action.items()))
                    action_args = nested if isinstance(nested, dict) else {"text": nested}
                if name in {"type_text", "type_into_element"}:
                    values.append(str(
                        action_args.get("text")
                        or action_args.get("text_input")
                        or action_args.get("value")
                        or ""
                    ))
        return [value for value in values if value]

    def _task_allows_repeated_text_mutation(self) -> bool:
        text = str(self.task or "").casefold()
        return bool(re.search(
            r"\b(?:twice|repeat|again|each|both|every)\b|"
            r"重复|两次|二次|两遍|分别|各自|每个|每一|都输入|再输入一次",
            text,
        ))

    def _duplicate_text_mutation_result(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> ToolResult | None:
        if self._task_allows_repeated_text_mutation():
            return None
        hwnd = int(getattr(self.session, "hwnd", 0) or 0)
        for literal in self._text_mutation_literals(tool_name, args):
            key = re.sub(r"\s+", " ", literal).strip()
            if len(key) < 8:
                continue
            previous = next((
                item
                for item in reversed(self._delivered_text_mutations)
                if item.get("text") == key
                and int(item.get("hwnd") or 0) == hwnd
                and self._tool_sequence - int(item.get("sequence") or 0) <= 12
                and time.monotonic() - float(item.get("at") or 0.0) <= 120.0
            ), None)
            if previous is None:
                continue
            payload = {
                "ok": True,
                "verified": False,
                "mutation_skipped": True,
                "text_already_delivered": True,
                "text_length": len(literal),
                "target_hwnd": hwnd or None,
                "original_tool": tool_name,
                "needs_observe": True,
                "controller_state": {
                    "phase": "needs_observe",
                    "verified": False,
                    "needs_observe": True,
                    "needs_llm_reason": False,
                },
                "fast_path_advice": (
                    "The exact text was already delivered successfully to this target window "
                    "during the current run. The duplicate mutation was skipped. Do not type it "
                    "again; use read_text/observe or continue with formatting/save."
                ),
            }
            return ToolResult(json.dumps(payload, ensure_ascii=False, default=str))
        return None

    @staticmethod
    def _format_mutation_key(args: dict[str, Any]) -> str:
        specs = args.get("formats")
        if not isinstance(specs, list):
            single = args.get("format")
            specs = [single] if isinstance(single, dict) else []
        normalized = [spec for spec in specs if isinstance(spec, dict)]
        if not normalized:
            return ""
        return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _duplicate_format_mutation_result(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> ToolResult | None:
        if tool_name not in {"execute_subgoal", "run_action_batch"}:
            return None
        key = self._format_mutation_key(args)
        if not key:
            return None
        hwnd = int(getattr(self.session, "hwnd", 0) or 0)
        previous = next((
            item
            for item in reversed(self._applied_format_mutations)
            if item.get("key") == key
            and int(item.get("hwnd") or 0) == hwnd
            and self._tool_sequence - int(item.get("sequence") or 0) <= 16
            and time.monotonic() - float(item.get("at") or 0.0) <= 180.0
        ), None)
        if previous is None:
            return None
        save_dispatched = bool(previous.get("save_dispatched"))
        payload = {
            "ok": True,
            "verified": False,
            "format_applied": True,
            "format_already_applied": True,
            "mutation_skipped": True,
            "save_dispatched": save_dispatched,
            "postcondition_pending": True,
            "needs_llm_reason": False,
            "target_hwnd": hwnd or None,
            "controller_state": {
                "phase": "needs_observe",
                "verified": False,
                "needs_observe": True,
                "needs_llm_reason": False,
            },
            "fast_path_advice": (
                "The identical rich-text formatting routine already ran successfully in this target "
                "window, so the duplicate toggle shortcuts were skipped. "
                + (
                    "Save was already dispatched; verify the saved artifact or call done."
                    if save_dispatched else
                    "Do not format again; issue only the requested Save action, then verify the artifact."
                )
            ),
        }
        return ToolResult(json.dumps(payload, ensure_ascii=False, default=str))

    def _remember_applied_format_mutation(
        self,
        tool_name: str,
        args: dict[str, Any],
        result_text: str,
    ) -> None:
        if tool_name not in {"execute_subgoal", "run_action_batch"}:
            return
        key = self._format_mutation_key(args)
        if not key:
            return
        try:
            payload = json.loads(result_text or "{}")
        except (json.JSONDecodeError, TypeError):
            return
        if (
            not isinstance(payload, dict)
            or payload.get("format_applied") is not True
            or payload.get("mutation_skipped") is True
        ):
            return
        self._applied_format_mutations.append({
            "key": key,
            "hwnd": int(getattr(self.session, "hwnd", 0) or 0),
            "sequence": self._tool_sequence,
            "at": time.monotonic(),
            "save_dispatched": bool(payload.get("save_dispatched")),
        })
        self._applied_format_mutations = self._applied_format_mutations[-12:]

    def _remember_delivered_text_mutations(
        self,
        tool_name: str,
        args: dict[str, Any],
        result_text: str,
    ) -> None:
        literals = self._text_mutation_literals(tool_name, args)
        if not literals:
            return
        try:
            payload = json.loads(result_text or "{}")
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(payload, dict) or payload.get("ok") is not True or payload.get("blocked") is True:
            return
        delivered = True
        if tool_name == "execute_subgoal":
            delivered = payload.get("input_applied") is True
        elif tool_name == "run_action_batch":
            delivered = any(
                isinstance(item, dict)
                and item.get("action") in {"type_text", "type_into_element"}
                and item.get("ok") is True
                for item in (payload.get("results") or [])
            )
        if not delivered:
            return
        hwnd = int(getattr(self.session, "hwnd", 0) or 0)
        now = time.monotonic()
        for literal in literals:
            key = re.sub(r"\s+", " ", literal).strip()
            if len(key) < 8:
                continue
            self._delivered_text_mutations.append({
                "text": key,
                "hwnd": hwnd,
                "sequence": self._tool_sequence,
                "at": now,
                "tool": tool_name,
            })
        self._delivered_text_mutations = self._delivered_text_mutations[-16:]

    def _track_new_document_plan_attempt(self, tool_name: str, args: dict[str, Any]) -> None:
        current = self._current_new_document_plan_item()
        if current is None:
            return
        index, step = current
        state = self._new_document_plan_state.setdefault(index, {
            "goal": str(step.get("goal") or ""),
            "baseline_title": "",
            "attempted": False,
        })
        snapshot = getattr(self.session, "snapshot", None)
        current_title = str(getattr(snapshot, "title", "") or "")
        if current_title and not state.get("baseline_title"):
            state["baseline_title"] = current_title

        if tool_name not in {"execute_subgoal", "run_action_batch", "click", "hotkey"}:
            return
        semantic = json.dumps(args, ensure_ascii=False, default=str)
        key = self._plan_text_key(semantic)
        explicit_new = any(term in key for term in (
            "new", "create", "blank", "新建", "创建", "空白文档",
        ))
        if tool_name == "hotkey":
            keys = {str(item).casefold() for item in (args.get("keys") or [])}
            explicit_new = explicit_new or (
                "n" in keys and bool(keys & {"ctrl", "control", "control_l", "control_r"})
            )
        if explicit_new:
            state["attempted"] = True

    def _maybe_auto_complete_new_document_plan_item(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: ToolResult,
    ) -> ToolResult:
        current = self._current_new_document_plan_item()
        if current is None:
            return result
        index, step = current
        state = self._new_document_plan_state.setdefault(index, {
            "goal": str(step.get("goal") or ""),
            "baseline_title": "",
            "attempted": False,
        })
        snapshot = getattr(self.session, "snapshot", None)
        current_title = str(getattr(snapshot, "title", "") or "")
        if current_title and not state.get("baseline_title"):
            state["baseline_title"] = current_title
            return result
        baseline = str(state.get("baseline_title") or "")
        if (
            not state.get("attempted")
            or not current_title
            or self._plan_text_key(current_title) == self._plan_text_key(baseline)
            or not self._looks_like_unsaved_document_title(current_title)
        ):
            return result

        plan = getattr(self.session, "plan", None)
        if plan is None or not plan.complete(index):
            return result
        completed = {
            "index": index,
            "goal": str(step.get("goal") or "")[:160],
            "source": "observed_new_document_title",
            "baseline_title": baseline,
            "current_title": current_title,
        }
        BUS.publish(
            "agent_plan",
            run_id=self.run_id,
            action="auto_complete",
            index=index,
            source="observed_new_document_title",
            plan=plan.snapshot(),
        )
        self._new_document_plan_state.pop(index, None)
        guidance = (
            f"Observed a newly created editable document title change from {baseline!r} "
            f"to {current_title!r}; plan item {index} is complete. Continue with the next "
            "unfinished item and do not click New again."
        )
        try:
            payload = json.loads(result.text or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = None
        if isinstance(payload, dict):
            prior = payload.get("auto_completed_plan_items")
            completed_items = list(prior) if isinstance(prior, list) else []
            completed_items.append(completed)
            payload["auto_completed_plan_items"] = completed_items
            payload["plan_after_auto_complete"] = plan.render()
            payload["fast_path_advice"] = (
                str(payload.get("fast_path_advice") or "").strip() + " " + guidance
            ).strip()
            text = json.dumps(payload, ensure_ascii=False, default=str)
        else:
            text = (
                (result.text or "").rstrip()
                + "\n\n[plan auto-complete] "
                + guidance
                + "\n"
                + plan.render()
            )
        self._trace_debug("new_document_plan_auto_completed", {
            "run_id": self.run_id,
            "tool": tool_name,
            "args": args,
            "completed": completed,
        })
        return ToolResult(
            text,
            is_observe=result.is_observe,
            terminal=result.terminal,
            summary=result.summary,
            replay=result.replay,
            image_b64=result.image_b64,
        )

    def _score_experience_feedback(self) -> None:
        """Score memory only when it was actually tried, not merely injected.

        Hints in the prompt are weak references; a successful run may have
        ignored them entirely. Positive feedback is therefore reserved for
        recipes whose reuse_skill replay succeeded and whose run also ended
        done. Failed replay attempts are negative even if live fallback later
        finishes the task. Failed/error runs with no replay still penalize
        injected auto-recipes, preserving the old poison-control behavior for
        bad hints.
        """
        if self.status not in ("done", "fail", "error"):
            return
        attempts = [
            attempt for attempt in getattr(self.session, "memory_recipe_attempts", [])
            if isinstance(attempt, dict) and str(attempt.get("id") or "")
        ]
        replay_ok_ids = list(dict.fromkeys(
            str(attempt["id"]) for attempt in attempts if attempt.get("replay_ok")
        ))
        replay_failed_ids = list(dict.fromkeys(
            str(attempt["id"]) for attempt in attempts
            if not attempt.get("replay_ok") and str(attempt["id"]) not in replay_ok_ids
        ))
        attempted_ids = list(dict.fromkeys(
            str(attempt["id"]) for attempt in attempts
        ))
        if self.status == "done":
            if replay_ok_ids:
                experience.feedback(replay_ok_ids, success=True)
            if replay_failed_ids:
                experience.feedback(replay_failed_ids, success=False)
        elif attempted_ids:
            experience.feedback(attempted_ids, success=False)
        else:
            experience.feedback(self._hint_ids, success=False)

    def _requires_run_attempt(self) -> bool:
        """True for coding clone/open-source tasks that must actually be run.

        This gate used to live in the old CodingRunner. It now belongs to the
        unified AgentRunner so coding tasks keep the same anti-fake-success
        behavior while sharing the same trace, planner, and done gate as GUI
        tasks.
        """
        if not self._is_coding:
            return False
        text = self.task.casefold()
        has_repo = bool(re.search(
            r"\b(?:clone|github|repo|repository|open[- ]?source)\b|"
            r"克隆|开源|仓库",
            text,
        ))
        wants_run = any(k in text for k in (
            "run", "start", "launch", "serve", "build", "test", "verify",
            "跑起来", "运行", "启动", "打开", "构建", "测试", "验证",
        ))
        return has_repo and wants_run

    def _has_run_attempt(self) -> bool:
        markers = (
            "npm", "pnpm", "yarn", "bun", "node ", "python", "pip", "pytest",
            "cargo", "go ", "dotnet", "mvn", "gradle", "make", "cmake",
            "get-command", "--version", "test-netconnection", "netstat",
            "get-process", "invoke-webrequest", "curl", "start-process",
        )
        for name, args, _out in self._tool_history:
            if name != "run_shell":
                continue
            cmd = str(args.get("command") or "").lower()
            if "git clone" in cmd and not any(m in cmd for m in markers if m != "get-command"):
                continue
            if any(marker in cmd for marker in markers):
                return True
        return False

    def _reject_incomplete_run_task(self) -> bool:
        if not self._requires_run_attempt() or self._has_run_attempt():
            return False
        if self._completion_rejects >= 2:
            return False
        self._completion_rejects += 1
        self.messages.append({
            "role": "user",
            "content": (
                "[completion check failed] The user asked to clone and run/start/verify the project. "
                "You have not actually attempted dependency/runtime checks, install/build/run, or "
                "deterministic verification. Do not just summarize README commands. Continue with "
                "run_shell checks such as Get-Command for the required runtime, then install/build/run "
                "or fail with the exact missing prerequisite/output."
            ),
        })
        BUS.publish(
            "done_check",
            run_id=self.run_id,
            passed=False,
            reason="clone/run task has no real run/build/runtime verification attempt",
        )
        return True

    def _checkpoint_visual_frame_before_window_switch(self, tool_name: str) -> None:
        """Retain a real post-action frame before another app takes focus.

        The final foreground frame is insufficient evidence for a task that
        mutates several windows.  Keep only a few JPEGs, replace older frames
        for the same hwnd, and never checkpoint an initial/observe-only frame
        as proof of a mutation.
        """
        if str(tool_name or "") not in {
            "open_app", "launch", "focus_window", "focus_app",
        }:
            return
        b64 = str(getattr(self, "_last_visual_feedback_b64", "") or "")
        action = str(getattr(self, "_last_visual_feedback_action", "") or "")
        hwnd = int(getattr(self, "_visual_frame_hwnd", 0) or 0)
        if not b64 or not hwnd or action in {"", "initial", "observe", "preflight"}:
            return
        if int(getattr(self, "_visual_frame_epoch", -1)) != int(
            getattr(self.session, "_screen_epoch", 0) or 0
        ):
            return
        snapshot = getattr(self.session, "snapshot", None)
        title = str(getattr(snapshot, "title", "") or "")
        record = {
            "image_b64": b64,
            "hwnd": hwnd,
            "title": title,
            "action": action,
            "frame_id": int(getattr(self, "_visual_frame_id", 0) or 0),
            "screen_epoch": int(getattr(self.session, "_screen_epoch", 0) or 0),
        }
        checkpoints = [
            item for item in getattr(self, "_historical_visual_checkpoints", [])
            if int(item.get("hwnd", 0) or 0) != hwnd
        ]
        checkpoints.append(record)
        self._historical_visual_checkpoints = checkpoints[-3:]
        BUS.publish(
            "visual_checkpoint",
            run_id=self.run_id,
            hwnd=hwnd,
            title=title,
            action=action,
            frame_id=record["frame_id"],
        )

    def _safe_stop_after_repeated_done_rejection(self) -> bool:
        """Fail honestly when another identical completion claim cannot help."""
        if not bool(getattr(self, "_done_reject_terminal", False)):
            return False
        reason = str(getattr(self, "_last_done_reject_reason", "") or "").strip()
        self.status = "fail"
        self.result_text = (
            "任务未能通过最终视觉验收，且在没有新证据的情况下重复声称完成。"
            + (f" 原因：{reason}" if reason else "")
        )
        self.run_state.terminal_rejected("done", reason)
        BUS.publish("run_state", run_id=self.run_id, **self.run_state.to_dict())
        BUS.publish(
            "agent_safe_stop",
            run_id=self.run_id,
            reason=reason,
            done_rejects=int(getattr(self, "_done_rejects", 0) or 0),
        )
        return True

    def _handle_tool_calls(self, tool_calls) -> str | None:
        """Dispatch each tool call; returns terminal status if any."""
        post_tool_hints: list[str] = []
        visual_transaction_seen = False
        visual_feedback_action = ""
        visual_feedback_result = ""
        visual_feedback_signature = ""
        deferred_observe_image = ""
        deferred_observe_phase = "observe"
        deferred_observe_region = None
        deferred_terminal: str | None = None
        for tc in tool_calls:
            if self.stop_event.is_set():
                return "stopped"
            self._tool_sequence += 1
            try:
                args = json.loads(tc.function.arguments or "{}")
                if not isinstance(args, dict):
                    raise ValueError("参数必须是 JSON 对象")
            except (json.JSONDecodeError, ValueError) as exc:
                error = f"工具参数 JSON 解析失败: {exc}。请重新调用并给出合法 JSON。"
                self._append_tool_result(tc.id, error)
                self.run_state.before_tool(tc.function.name, {})
                self.run_state.after_tool(
                    tc.function.name,
                    {},
                    json.dumps({"ok": False, "error": error}, ensure_ascii=False),
                )
                BUS.publish("run_state", run_id=self.run_id, **self.run_state.to_dict())
                self._trace_debug("tool_arg_error", {
                    "run_id": self.run_id,
                    "tool_call_id": tc.id,
                    "tool": tc.function.name,
                    "raw_arguments": tc.function.arguments,
                    "error": str(exc),
                })
                BUS.publish("agent_tool", run_id=self.run_id, action_id=tc.id,
                            tool=tc.function.name, error=str(exc))
                continue

            self._checkpoint_visual_frame_before_window_switch(tc.function.name)

            tool_epoch_before = int(getattr(self.session, "_screen_epoch", 0) or 0)
            tool_dispatched = False

            # Function-calling APIs may return several mutations in one model
            # message.  Executing all of them recreates an open-loop macro: the
            # model cannot see what the first action actually did.  A
            # run_action_batch/execute_subgoal call remains one explicitly
            # controller-managed transaction, but every later tool call in the
            # same model turn is acknowledged and deferred until the real
            # post-action frame has been inspected.
            closed_loop = bool(
                self._visual_closed_loop_active
                and CONFIG.get("controller", "visual_closed_loop", default=True)
            )
            terminal_barrier = deferred_terminal is not None
            turn_barrier = bool(closed_loop and visual_transaction_seen)
            needs_grounding = bool(
                closed_loop
                and _main_model_has_vision()
                and tc.function.name in _LOW_LEVEL_VISUAL_TOOLS
                and not self._has_fresh_visual_grounding()
            )
            preflight_changed_image = ""
            preflight_change_ratio: float | None = 0.0
            preflight_error = ""
            preflight_capture_region = None
            frame_coordinate_error = ""
            frame_coordinate_meta: dict[str, Any] = {}
            continuous_pointer_block = None
            no_progress_block = None
            partial_output_block = None
            semantic_progress_block = None
            semantic_progress_geometry = self._continuous_semantic_geometry(
                tc.function.name, args,
            )
            if not terminal_barrier and not turn_barrier:
                partial_output_block = self._partial_output_guard.evaluate(
                    tc.function.name,
                    args,
                    hwnd=int(getattr(self.session, "hwnd", 0) or 0),
                    screen_epoch=tool_epoch_before,
                    sequence=self._tool_sequence,
                    frame_id=int(getattr(self, "_visual_frame_id", 0) or 0),
                    frame_epoch=int(getattr(self, "_visual_frame_epoch", -1)),
                    frame_hwnd=int(getattr(self, "_visual_frame_hwnd", 0) or 0),
                    user_directives=tuple(self._native_visual_user_directives),
                )
                continuous_pointer_block = self._text_continuous_pointer_preparation_guard(
                    tc.function.name, args,
                )
                if not partial_output_block and not continuous_pointer_block:
                    semantic_progress_block = self._continuous_semantic_progress_guard(
                        tc.function.name,
                        args,
                        semantic_progress_geometry,
                    )
            if (
                closed_loop
                and _main_model_has_vision()
                and not needs_grounding
                and not turn_barrier
                and not terminal_barrier
                and not continuous_pointer_block
                and not semantic_progress_block
            ):
                continuous_pointer_block = self._continuous_pointer_click_guard(
                    tc.function.name, args,
                )
                if not continuous_pointer_block:
                    no_progress_block = self._native_no_progress_guard(
                        tc.function.name, args,
                    )
            runtime_guard_block = (
                partial_output_block
                or continuous_pointer_block
                or semantic_progress_block
                or no_progress_block
            )
            if (
                closed_loop
                and _main_model_has_vision()
                and not needs_grounding
                and not turn_barrier
                and not terminal_barrier
                and not runtime_guard_block
            ):
                try:
                    # Validate the model-authored coordinate contract early,
                    # but discard the physical mapping. The real mapping is
                    # recomputed only after preflight atomically confirms the
                    # capture geometry and pixels.
                    self._map_normalized_frame_coordinates(
                        tc.function.name, args,
                    )
                except ValueError as exc:
                    frame_coordinate_error = str(exc)
                    frame_coordinate_meta = dict(getattr(exc, "metadata", {}) or {})
            if (
                closed_loop
                and _main_model_has_vision()
                and tc.function.name in _LOW_LEVEL_VISUAL_TOOLS
                and not needs_grounding
                and not turn_barrier
                and not terminal_barrier
                and not frame_coordinate_error
                and not runtime_guard_block
            ):
                (
                    preflight_changed_image,
                    preflight_change_ratio,
                    preflight_error,
                    preflight_capture_region,
                ) = self._preflight_visual_frame(tc.function.name, args)
            if (
                closed_loop
                and _main_model_has_vision()
                and not needs_grounding
                and not turn_barrier
                and not terminal_barrier
                and not frame_coordinate_error
                and not preflight_changed_image
                and not preflight_error
                and not runtime_guard_block
            ):
                try:
                    args = self._map_normalized_frame_coordinates(tc.function.name, args)
                except ValueError as exc:
                    # Geometry may have changed immediately after preflight.
                    # Fail closed instead of executing coordinates mapped from
                    # a frame that is no longer current.
                    frame_coordinate_error = str(exc)
                    frame_coordinate_meta = dict(getattr(exc, "metadata", {}) or {})

            args = self._maybe_upgrade_exact_save_as(tc.function.name, args)
            args = self._maybe_upgrade_spreadsheet_action_batch(tc.function.name, args)
            args = self._maybe_upgrade_pending_clipboard_replace(tc.function.name, args)
            args = self._maybe_upgrade_execute_subgoal_document_input(tc.function.name, args)
            args = self._maybe_upgrade_execute_subgoal_formats(tc.function.name, args)
            # Some controller upgrades introduce a whole-document replacement
            # after the model-authored args were inspected above.  Re-evaluate
            # the final dispatch payload so document_surface clear cannot hide
            # a Ctrl+A/Backspace behind an otherwise harmless subgoal.
            if (
                partial_output_block is None
                and not terminal_barrier
                and not turn_barrier
            ):
                partial_output_block = self._partial_output_guard.evaluate(
                    tc.function.name,
                    args,
                    hwnd=int(getattr(self.session, "hwnd", 0) or 0),
                    screen_epoch=tool_epoch_before,
                    sequence=self._tool_sequence,
                    frame_id=int(getattr(self, "_visual_frame_id", 0) or 0),
                    frame_epoch=int(getattr(self, "_visual_frame_epoch", -1)),
                    frame_hwnd=int(getattr(self, "_visual_frame_hwnd", 0) or 0),
                    user_directives=tuple(self._native_visual_user_directives),
                )
            self._track_new_document_plan_attempt(tc.function.name, args)
            action_envelope = build_action_envelope(
                tc.function.name,
                args,
                action_id=tc.id,
                hwnd=int(getattr(self.session, "hwnd", 0) or 0) or None,
                frame_id=int(getattr(self, "_visual_frame_id", 0) or 0) or None,
                epoch=int(getattr(self, "_visual_frame_epoch", -1))
                if int(getattr(self, "_visual_frame_epoch", -1)) >= 0
                else None,
                region=getattr(self, "_visual_frame_region", None),
            ).to_dict()
            self.run_state.before_tool(tc.function.name, args)
            BUS.publish("run_state", run_id=self.run_id, **self.run_state.to_dict())
            BUS.publish("agent_tool_start", run_id=self.run_id,
                        event_name="action.started",
                        action_id=tc.id, action_envelope=action_envelope,
                        tool=tc.function.name, args=args)
            started = time.monotonic()
            blocked_store_action = _blocks_store_search_action(self.task, tc.function.name, args)
            blocked_upload_action = _blocks_upload_media_analysis(self.task, tc.function.name, args)
            blocked_gui_only_action = ""
            if _is_gui_only_task(self.task):
                strict_no_shell = _gui_only_forbids_shell(self.task)
                command = str(args.get("command", ""))
                condition = args.get("condition")
                if (
                    tc.function.name in {
                        "run_shell", "run_detached", "write_file", "read_file", "list_dir",
                        "apply_patch", "search_files", "search_text",
                    }
                    and not (
                        tc.function.name == "run_shell"
                        and not strict_no_shell
                        and _gui_only_shell_allowed(command)
                    )
                ):
                    blocked_gui_only_action = (
                        f"GUI-only mode blocked {tc.function.name}: use GUI tools "
                        "such as launch/focus_window/observe/click/type_text/hotkey "
                        "for the work. Shell is allowed only for final Test-Path/"
                        "Get-Content/Select-String verification when the user did not forbid shell."
                    )
                elif tc.function.name == "launch" and _gui_only_launch_uses_shell(command):
                    blocked_gui_only_action = (
                        "GUI-only mode blocked launching a shell command. Launch the target app/URL "
                        "directly, then use focus_window/observe/click/type_text/hotkey."
                    )
                elif (
                    tc.function.name == "wait"
                    and strict_no_shell
                    and isinstance(condition, dict)
                    and condition.get("shell_true")
                ):
                    blocked_gui_only_action = (
                        "GUI-only mode blocked wait(shell_true) because the user forbade shell. "
                        "Use observe/read_text or GUI waits instead."
                    )
            if terminal_barrier:
                result = ToolResult(json.dumps({
                    "ok": False,
                    "verified": False,
                    "blocked": True,
                    "terminal_turn_barrier": True,
                    "error": (
                        "Deferred because an earlier tool call in this model turn "
                        "already reached a terminal state. No later call was executed."
                    ),
                    "needs_replan": False,
                }, ensure_ascii=False))
            elif turn_barrier:
                result = ToolResult(json.dumps({
                    "ok": False,
                    "verified": False,
                    "blocked": True,
                    "visual_turn_barrier": True,
                    "error": (
                        "Deferred because this model turn already attempted a visual "
                        "transaction. Inspect the automatically attached real post-action "
                        "frame, then decide the next action in a new turn."
                    ),
                    "needs_replan": False,
                }, ensure_ascii=False))
            elif partial_output_block:
                if (
                    self._continuous_pointer_output_requested()
                    and str(partial_output_block.get("destructive_action") or "")
                    in {
                        "single_undo", "bulk_undo", "reset_or_clear",
                        "select_all_delete", "document_surface_clear",
                    }
                    and partial_output_block.get("safe_stop") is not True
                ):
                    self._continuous_pointer_forward_only_correction = True
                result = ToolResult(
                    json.dumps(partial_output_block, ensure_ascii=False),
                    terminal=(
                        "reply" if partial_output_block.get("safe_stop") is True else None
                    ),
                )
            elif semantic_progress_block:
                result = ToolResult(
                    json.dumps(semantic_progress_block, ensure_ascii=False),
                    terminal=(
                        "reply" if semantic_progress_block.get("safe_stop") is True else None
                    ),
                )
            elif needs_grounding:
                self._visual_grounding_block_count += 1
                if self._visual_grounding_block_count >= 2:
                    result = ToolResult(
                        "任务已安全暂停：模型连续两次试图在没有当前真实画面依据时操作界面。\n"
                        "需要用户确认：请确认目标窗口当前可见，或提供一张当前界面截图后再继续。",
                        terminal="reply",
                    )
                else:
                    result = ToolResult(json.dumps({
                        "ok": False,
                        "verified": False,
                        "blocked": True,
                        "visual_grounding_required": True,
                        "error": (
                            "Low-level GUI action blocked because no fresh real frame grounds "
                            "the current capture scope/epoch. Call observe first, or use "
                            "execute_subgoal/run_action_batch when the controller can ground "
                            "the complete transaction locally."
                        ),
                        "needs_replan": False,
                    }, ensure_ascii=False))
            elif continuous_pointer_block:
                stop_after_block = (
                    self._continuous_pointer_guard_blocks
                    >= _CONTINUOUS_POINTER_MAX_BLOCKS
                )
                if stop_after_block:
                    continuous_pointer_block["safe_stop"] = True
                    continuous_pointer_block["error"] += (
                        " The model ignored the required gesture and proposed another preparation mutation, "
                        "so the run is stopping instead of continuing without real output evidence."
                    )
                result = ToolResult(
                    json.dumps(continuous_pointer_block, ensure_ascii=False),
                    terminal="reply" if stop_after_block else None,
                )
            elif no_progress_block:
                stop_after_block = (
                    self._native_no_progress_block_count
                    >= int(CONFIG.get(
                        "controller", "native_no_progress_max_blocks",
                        default=_NATIVE_NO_PROGRESS_MAX_BLOCKS,
                    ) or _NATIVE_NO_PROGRESS_MAX_BLOCKS)
                )
                if stop_after_block:
                    no_progress_block["safe_stop"] = True
                    no_progress_block["error"] += (
                        " The model repeated the blocked action instead of changing strategy, "
                        "so the run is stopping rather than continuing confidently without evidence."
                    )
                result = ToolResult(
                    json.dumps(no_progress_block, ensure_ascii=False),
                    terminal="reply" if stop_after_block else None,
                )
            elif preflight_changed_image:
                # The replacement pixels and invalidation are one state
                # transition. Do this before plan/evidence bookkeeping so no
                # code later in this tool turn can consume element ids from
                # the superseded frame.
                self._invalidate_structured_grounding()
                deferred_observe_image = preflight_changed_image
                deferred_observe_phase = "preflight"
                deferred_observe_region = preflight_capture_region
                result = ToolResult(json.dumps({
                    "ok": False,
                    "verified": False,
                    "blocked": True,
                    "visual_frame_changed": True,
                    "change_ratio": round(float(preflight_change_ratio or 0.0), 6),
                    "error": (
                        "The real GUI-frame pixels or capture scope changed while the model was deciding. "
                        "The proposed action was not executed; inspect the newly attached frame."
                    ),
                    "needs_replan": False,
                }, ensure_ascii=False))
            elif preflight_error:
                result = ToolResult(json.dumps({
                    "ok": False,
                    "verified": False,
                    "blocked": True,
                    "action_executed": False,
                    "visual_preflight_failed": True,
                    "error": (
                        "The runtime could not confirm that the target pixels still match the "
                        f"authoritative frame ({preflight_error}). The action was not executed."
                    ),
                    "fast_path_advice": "Call observe to obtain a new real frame before retrying.",
                    "needs_replan": False,
                }, ensure_ascii=False))
            elif frame_coordinate_error:
                spatial_batch_limit = bool(
                    tc.function.name == "run_action_batch"
                    and "allows at most one spatial UI mutation" in frame_coordinate_error
                )
                coordinate_payload = {
                    "ok": False,
                    "verified": False,
                    "blocked": True,
                    "frame_coordinate_invalid": True,
                    "error": frame_coordinate_error,
                    "fast_path_advice": (
                        "Keep at most one spatial mutation in this batch; keyboard-only input, "
                        "wait, and verification may stay with it. Move every later spatial action "
                        "to a new turn after inspecting the next real post-action frame, which is "
                        "attached automatically."
                        if spatial_batch_limit else
                        (
                            "Keep frame_strokes in the existing whole-frame 0..1000 coordinate "
                            "system, but place every stroke start and point comfortably inside "
                            "the returned work_surface_frame_bounds. Do not trace the boundary "
                            "or reinterpret frame_strokes as surface-relative coordinates."
                        )
                        if frame_coordinate_meta.get("work_surface_containment_failed") else
                        "Inspect the attached authoritative frame and retry once with the "
                        "tool's frame_* coordinates in 0..1000. The rejected action was not executed."
                    ),
                    "needs_replan": False,
                }
                coordinate_payload.update(frame_coordinate_meta)
                if spatial_batch_limit:
                    coordinate_payload["native_batch_spatial_limit"] = True
                result = ToolResult(json.dumps(coordinate_payload, ensure_ascii=False))
            elif blocked_store_action or blocked_upload_action:
                result = ToolResult(blocked_store_action or blocked_upload_action)
            elif blocked_gui_only_action:
                result = ToolResult(blocked_gui_only_action)
            else:
                if (
                    closed_loop
                    and _main_model_has_vision()
                    and tc.function.name in {"execute_subgoal", "run_action_batch"}
                ):
                    # Controller transactions must not consume a same-hwnd
                    # snapshot from before model inference. Preflight above
                    # proves the pixels are still current; internal controller
                    # observe then rebuilds structured grounding explicitly.
                    self.session.snapshot = None
                result = None
                if result is None:
                    result = self._duplicate_clipboard_delivery_result(tc.function.name, args)
                if result is None:
                    result = self._duplicate_text_mutation_result(tc.function.name, args)
                if result is None:
                    result = self._duplicate_format_mutation_result(tc.function.name, args)
                if result is None:
                    tool_dispatched = True
                    result = self._dispatch_with_fast_visual_wrap(tc.function.name, args)
            result = self._maybe_deliver_clipboard_after_focus(tc.function.name, args, result)
            result = self._maybe_auto_complete_new_document_plan_item(tc.function.name, args, result)
            result = self._maybe_auto_complete_open_plan_item(tc.function.name, args, result)
            result = self._maybe_auto_complete_verified_subgoal_plan_items(tc.function.name, args, result)
            result = self._maybe_auto_complete_document_format_plan_items(tc.function.name, args, result)
            result = self._maybe_auto_complete_desktop_drag_plan_item(tc.function.name, args, result)
            self._record_text_continuous_pointer_outcome(
                tc.function.name, args, result,
            )
            if (
                closed_loop
                and tc.function.name in _VISUAL_MUTATION_TOOLS
                and not turn_barrier
                and not terminal_barrier
            ):
                visual_transaction_seen = True
                try:
                    visual_result_data = json.loads(result.text or "{}")
                except (json.JSONDecodeError, TypeError):
                    visual_result_data = {}
                if not isinstance(visual_result_data, dict):
                    visual_result_data = {}
                if (
                    not needs_grounding
                    and visual_result_data.get("blocked") is not True
                    and result.terminal not in {"fail", "error", "stopped"}
                ):
                    visual_feedback_action = tc.function.name
                    visual_feedback_signature = self._native_spatial_action_signature(
                        tc.function.name,
                        args,
                        frame_region=self._visual_frame_region,
                    )
                    if visual_result_data:
                        # The controller's text-mode contract asks callers to
                        # observe after an unverified mutation. Native vision
                        # already receives an authoritative real post-action
                        # frame below, so preserving needs_observe=true causes
                        # a redundant multi-second UIA/OCR pass and contradicts
                        # the pixels attached to the very next model turn.
                        visual_result_data["needs_observe"] = False
                        visual_result_data["native_post_action_frame_pending"] = True
                        if visual_result_data.get("ok") is True:
                            # Execution provenance only: the fresh frame below
                            # still decides whether the input achieved the goal.
                            visual_result_data["native_action_executed"] = True
                        visual_result_data["fast_path_advice"] = (
                            "Inspect the automatically attached real post-action frame directly. "
                            "Do not call observe merely to verify this action; call done when the "
                            "requested result is visibly clear, otherwise act once on that frame."
                        )
                        result.text = json.dumps(
                            visual_result_data, ensure_ascii=False, default=str,
                        )
                    visual_feedback_result = result.text or ""
            # Validation/policy rejections have no post-action frame, so the
            # visual and semantic no-progress guards cannot see them.  Count
            # their stable constraint category before generic recovery or
            # evidence bookkeeping can blur the original no-execution result.
            self._apply_constraint_rejection_guard(
                tc.function.name,
                result,
                dispatched=tool_dispatched,
            )
            failure_recovery = None
            raw_result_text = result.text
            self._record_continuous_semantic_result(
                tc.function.name,
                raw_result_text,
                geometry=semantic_progress_geometry,
                dispatched=tool_dispatched,
                terminal=result.terminal,
            )
            self._remember_delivered_text_mutations(
                tc.function.name,
                args,
                raw_result_text,
            )
            self._remember_applied_format_mutation(
                tc.function.name,
                args,
                raw_result_text,
            )
            try:
                # Recovery strategies are meaningful only after the operator
                # actually received the action.  Validation, grounding, and
                # policy barriers deliberately leave the GUI untouched; if
                # they enter the generic failure classifier it can claim that
                # an unexecuted action "possibly succeeded" and send the model
                # into a redundant re-verification loop.
                recovery_ineligible = bool(
                    not tool_dispatched
                    or _result_explicitly_blocked_before_execution(result.text or "")
                )
                failure_recovery = None if recovery_ineligible else self._failure_recovery_layer.handle_tool_result(
                    tool_name=tc.function.name,
                    args=args,
                    result_text=result.text or "",
                    task=self.task,
                    session=self.session,
                    recent_results=self._recent_results,
                    visible_evidence_ledger=self._visible_evidence_ledger,
                    tool_history=self._tool_history,
                )
                if failure_recovery is not None:
                    result.text = attach_recovery_metadata(result.text or "", failure_recovery)
                    post_tool_hints.extend(failure_recovery.post_tool_hints)
                    BUS.publish(
                        "agent_recovery",
                        run_id=self.run_id,
                        tool=tc.function.name,
                        failure_recovery=failure_recovery.to_dict(),
                    )
            except Exception as exc:
                self._trace_debug("failure_recovery_error", {
                    "run_id": self.run_id,
                    "tool_call_id": tc.id,
                    "tool": tc.function.name,
                    "args": args,
                    "error": str(exc),
                })
            epoch_after_tool_and_recovery = int(
                getattr(self.session, "_screen_epoch", 0) or 0
            )
            self._partial_output_guard.record_result(
                tc.function.name,
                args,
                raw_result_text,
                dispatched=tool_dispatched,
                terminal=result.terminal,
                hwnd=int(getattr(self.session, "hwnd", 0) or 0),
                screen_epoch=epoch_after_tool_and_recovery,
                sequence=self._tool_sequence,
                frame_id=int(getattr(self, "_visual_frame_id", 0) or 0),
                user_directives=tuple(self._native_visual_user_directives),
            )
            if (
                closed_loop
                and epoch_after_tool_and_recovery != tool_epoch_before
                and not visual_feedback_action
            ):
                # The recovery layer may focus/re-observe through ToolSession
                # after the original call was blocked.  That is still a visual
                # transaction: expose its real resulting frame instead of
                # silently invalidating grounding and forcing an observe loop.
                visual_transaction_seen = True
                visual_feedback_action = f"{tc.function.name}:local_recovery"
                visual_feedback_result = result.text or ""
            elapsed_ms = int((time.monotonic() - started) * 1000)
            state_terminal = None if result.terminal == "done" else result.terminal
            self.run_state.after_tool(tc.function.name, args, result.text, state_terminal)
            BUS.publish("run_state", run_id=self.run_id, **self.run_state.to_dict())
            self._trace_debug("tool_result", {
                "run_id": self.run_id,
                "tool_call_id": tc.id,
                "tool": tc.function.name,
                "args": args,
                "result_text": result.text,
                "raw_result_text": raw_result_text if raw_result_text != result.text else None,
                "summary": result.summary,
                "terminal": result.terminal,
                "replay": result.replay,
                "elapsed_ms": elapsed_ms,
                "failure_recovery": failure_recovery.to_dict() if failure_recovery else None,
            })
            ledger_len_before = len(self._visible_evidence_ledger)
            pending_controller_events = list(
                getattr(self.session, "_pending_controller_events", []) or []
            )
            self.session._pending_controller_events = []
            for event in pending_controller_events:
                if not isinstance(event, dict):
                    continue
                event_tool = str(event.get("tool") or "")
                event_args = event.get("args") if isinstance(event.get("args"), dict) else {}
                event_text = str(event.get("result_text") or "")
                if not event_tool:
                    continue
                self._record_step(event_tool, event_args, None)
                self._track_verification(event_tool, event_text)
                self._track_visible_evidence(event_tool, event_args, event_text)
                self._tool_history.append((event_tool, event_args, event_text))
                try:
                    event_data = json.loads(event_text or "{}")
                except (json.JSONDecodeError, TypeError):
                    event_data = {}
                packet = event_data.get("evidence_packet") if isinstance(event_data.get("evidence_packet"), dict) else {}
                visible_preview = str(packet.get("visible_text_preview") or "").strip()
                if visible_preview:
                    verify_texts = event_args.get("verify_texts")
                    query = str(verify_texts[0] or "") if isinstance(verify_texts, list) and verify_texts else ""
                    self._tool_history.append(("read_text", {"query": query}, visible_preview))
            self._record_step(tc.function.name, args, result.replay)
            self._track_verification(tc.function.name, result.text)
            self._track_visible_evidence(tc.function.name, args, result.text)
            result = self._maybe_auto_done_from_visible_evidence(
                tc.function.name,
                result,
                ledger_len_before,
            )
            self._tool_history.append((tc.function.name, args, result.text or ""))
            self._tool_history = self._tool_history[-40:]
            # Keep recent tool outputs as evidence for the done-gate judge
            # (esp. run_shell stdout / verify results — deterministic proof
            # the GUI screen can't show).
            evidence_tools = {
                "run_shell",
                "run_detached",
                "search_files",
                "list_dir",
                "read_file",
                "search_text",
                "github_search",
                "apply_patch",
                "write_file",
                "list_windows",
                "scan_windows",
                "read_text",
                "find_app",
                "find_program",
                "open_app",
                "run_action_batch",
                "execute_subgoal",
                "schedule_task",
                "list_scheduled_tasks",
                "cancel_scheduled_task",
                "coordinate_diagnostics",
                "coordinate_click_test",
                "zoom",
                "screenshot",
            }
            if (
                tc.function.name in evidence_tools
                or '"verified"' in (result.text or "")
                or "visual_change_detected" in (result.text or "")
                or '"native_action_executed": true' in (result.text or "")
            ):
                if tc.function.name in {"run_action_batch", "execute_subgoal", "zoom", "screenshot", "draw_strokes", "drag"}:
                    evidence_text = self._compact_tool_evidence(tc.function.name, result.text)
                else:
                    limit = 1200 if tc.function.name in {
                        "list_windows",
                        "scan_windows",
                        "read_text",
                        "schedule_task",
                        "cancel_scheduled_task",
                        "coordinate_diagnostics",
                        "coordinate_click_test",
                    } else 600
                    if tc.function.name == "list_scheduled_tasks":
                        limit = 2400
                    evidence_text = (result.text or "")[:limit]
                self._recent_results.append((tc.function.name, evidence_text))
                # Multi-window tasks routinely need more than six actions
                # between an earlier visible result and final completion.
                self._recent_results = self._recent_results[-20:]
            result_meta: dict[str, Any] = {}
            try:
                parsed_result = json.loads(result.text or "{}")
            except (json.JSONDecodeError, TypeError):
                parsed_result = {}
            if isinstance(parsed_result, dict):
                if tc.function.name == "execute_subgoal":
                    packet = parsed_result.get("evidence_packet")
                    if isinstance(packet, dict):
                        candidates = [
                            str(item)
                            for item in (packet.get("candidate_click_labels") or [])
                            if str(item or "").strip()
                        ][:8]
                        if parsed_result.get("needs_llm_reason") and candidates:
                            post_tool_hints.append(
                                "[controller recovery] execute_subgoal could not match the requested "
                                "anchor label, but the target region has visible candidate labels: "
                                f"{', '.join(candidates)}. Retry execute_subgoal with the best "
                                "semantically matching visible/localized label and the same verify_texts "
                                "before doing a full observe or any shell/source-code inspection."
                            )
                for key in (
                    "ok",
                    "verified",
                    "wrapped_by_fast_controller",
                    "visual_change_detected",
                    "visual_change_ratio",
                    "screen_delta",
                    "screen_watch",
                    "action_target",
                    "fast_path_advice",
                    "needs_observe",
                    "native_action_executed",
                    "native_post_action_frame_pending",
                    "last_action",
                    "metrics",
                    "subgoal",
                    "subgoal_route",
                    "controller_state",
                    "evidence_packet",
                    "auto_completed_plan_items",
                    "plan_after_auto_complete",
                    "needs_llm_reason",
                    "element_cache",
                    "auto_done",
                    "structured_scene_verified",
                    "format_applied",
                    "format_verified",
                    "format_already_applied",
                    "mutation_skipped",
                    "save_dispatched",
                    "saved_paths",
                    "artifact_format_errors",
                    "failure_classification",
                    "failure_recovery",
                    "cooperative_input",
                    "semantic_progress_guard",
                    "pixel_effect_only",
                    "materially_different_geometry_required",
                    "fresh_semantic_required",
                    "continuous_pointer_guard",
                    "text_only_continuous_setup_guard",
                    "continuous_gesture_required",
                    "setup_clicks_observed",
                    "setup_mutations_observed",
                    "low_change_click_streak",
                    "native_no_progress_guard",
                    "no_visual_progress",
                    "no_progress_streak",
                    "observed_change_ratio",
                    "strategy_change_required",
                    "repeated_guard_blocks",
                    "constraint_rejection_guard",
                    "constraint_category",
                    "constraint_rejection_streak",
                    "constraint_rejection_limit",
                    "partial_output_destructive_guard",
                    "guard_blocks",
                    "safe_stop",
                    "blocked",
                    "error",
                ):
                    if key in parsed_result:
                        result_meta[key] = parsed_result[key]
            if failure_recovery is not None:
                result_meta.setdefault(
                    "failure_classification",
                    failure_recovery.classification.to_dict(),
                )
                result_meta.setdefault(
                    "failure_recovery",
                    {
                        "status": failure_recovery.status.value,
                        "llm_required": failure_recovery.llm_required,
                        "retryable": failure_recovery.retryable,
                        "local_actions": failure_recovery.local_actions,
                        "post_tool_hint_count": len(failure_recovery.post_tool_hints),
                        "result_meta": failure_recovery.result_meta,
                    },
                )
            BUS.publish(
                "agent_tool",
                run_id=self.run_id,
                event_name="action.finished",
                action_id=tc.id,
                action_envelope=action_envelope,
                tool=tc.function.name,
                args=args,
                result_preview=result.text[:300],
                result_meta=result_meta,
                elapsed_ms=elapsed_ms,
                terminal=None if result.terminal == "done" else result.terminal,
                replay=result.replay,
            )
            self._append_tool_result(tc.id, result.text, summary=result.summary, tool_name=tc.function.name)
            if result.is_observe:
                # Defer the separate image message until every tool result for the
                # assistant message has been appended.  Interleaving a user image
                # between tool-role results violates function-calling message order.
                deferred_observe_image = result.image_b64 or deferred_observe_image
                if closed_loop:
                    visual_transaction_seen = True
            if result.terminal:
                if result.terminal == "done" and self._reject_incomplete_run_task():
                    continue
                if deferred_terminal is None:
                    self.result_text = result.text
                    deferred_terminal = result.terminal
        if visual_feedback_action:
            self._inject_visual_frame(
                phase="post_action",
                action=visual_feedback_action,
                result_preview=visual_feedback_result,
                action_signature=visual_feedback_signature,
            )
        elif deferred_observe_image:
            # Tool-role messages cannot carry multipart image content, so append
            # the authoritative frame as a user message after the tool-call group.
            if deferred_observe_phase == "preflight":
                self._inject_visual_frame(
                    phase="preflight",
                    action="visual_preflight_changed",
                    image_b64=deferred_observe_image,
                    frame_region=deferred_observe_region,
                )
            else:
                self._inject_observe_screenshot(deferred_observe_image)
        for hint in post_tool_hints[-2:]:
            self.messages.append({"role": "user", "content": hint})
        return deferred_terminal

    # ------------------------------------------------------------------ run
    def _prebind_initial_gui_target(self) -> None:
        """Open/focus a leading, explicit GUI application before LLM step 1."""
        if self._is_coding:
            return
        task = str(self.task or "").strip()
        if not re.match(
            r"^(?:严格.*?[:：]\s*)?(?:open|launch|start|run|打开|启动|运行|在|用)\s*",
            task,
            re.IGNORECASE,
        ):
            return
        from ..fast.subgoal import _initial_task_app_target

        target = _initial_task_app_target(task)
        if not target:
            return
        args = {"name": target}
        action_id = f"prebind-{uuid.uuid4().hex[:8]}"
        self.run_state.before_tool("open_app", args)
        BUS.publish(
            "agent_tool_start",
            run_id=self.run_id,
            action_id=action_id,
            tool="open_app",
            args=args,
            internal=True,
        )
        started = time.monotonic()
        result = self.session.dispatch("open_app", args)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        self.run_state.after_tool("open_app", args, result.text)
        BUS.publish("run_state", run_id=self.run_id, **self.run_state.to_dict())
        BUS.publish(
            "agent_tool",
            run_id=self.run_id,
            action_id=action_id,
            tool="open_app",
            result=result.text,
            elapsed_ms=elapsed_ms,
            internal=True,
        )
        self._record_step("open_app", args, result.replay)
        native_vision = _main_model_has_vision()
        model_result = self._prebind_result_for_model(
            result.text or "", native_vision=native_vision,
        )
        self._tool_history.append(("open_app", args, model_result))
        self.messages.append({
            "role": "user",
            "content": (
                f"[controller target prebind] Requested leading application {target!r}. "
                f"open_app result: {model_result}. "
                "Continue in that bound window; do not inspect or mutate unrelated foreground windows. "
                + (
                    "A fresh authoritative native frame is attached immediately after this message. "
                    "Inspect that image directly; do not call observe merely to reconfirm the prebind."
                    if native_vision
                    else "Use one structured observe to ground the target before acting."
                )
            ),
        })

    def run(self) -> dict[str, Any]:
        self.status = "running"
        max_steps = int(
            self.max_steps_override
            or self.runtime_profile.max_steps
            or CONFIG.get("agent", "max_steps", default=40)
        )
        plan_on = planning_enabled()
        # Coding tasks are excluded from action-replay reuse, but relevant
        # factual project notes (manual 'remember' entries) still help — inject
        # them read-only (empty hint_ids → never scored/evicted by this run).
        if self._is_coding:
            hints, self._hint_ids = experience.note_hints(self.task), []
        else:
            hints, self._hint_ids = experience.hints_with_ids(self.task)
        matched_books = playbooks.for_task(self.task)
        self._injected_playbooks = {b.name for b in matched_books}
        system = prompts.system_prompt(
            experience_hints=hints, vlm_enabled=vlm.is_configured(),
            planning_enabled=plan_on,
            playbook_text=playbooks.render_section(matched_books),
            style=str(CONFIG.get("agent", "prompt_style", default="compact")),
            main_model_has_vision=_main_model_has_vision(),
            continuous_pointer_output=_requests_continuous_pointer_output(self.task),
            runtime_section=self.runtime_profile.prompt_section())
        self.messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": self.task},
        ]
        if _is_communication_task(self.task):
            payloads = _quoted_payloads(self.task)
            communication_mode = _communication_mode(self.task)
            wants_decor = any(k in (self.task or "").lower() for k in (
                "表情", "emoji", "sticker", "表情包", "贴纸", "emoticon",
            ))
            if communication_mode == "draft":
                hint_body = (
                    "This is a draft-only communication task. Open the requested conversation, "
                    "put the requested text in the message input, and stop with it visibly drafted. "
                    "Do not click Send and do not press Enter."
                )
            elif payloads:
                core = payloads[0][:80]
                extra = "如果能清楚看到表情/贴纸入口，再额外尝试一次装饰。" if wants_decor else ""
                hint_body = (
                    f"先把要发送的正文 '{core}' 真正送达并重新 observe/read_text 验证。"
                    "如果界面里正文已经出现在输入框，就优先完成发送，不要为了装饰项在发送区附近反复试探模糊图标。"
                    f"{extra}"
                )
            elif communication_mode == "bounded_chat":
                hint_body = (
                    "这是普通聊天任务，按一轮往返有界执行：先打开目标聊天并发送一句自然开场，"
                    "等待对方出现一条新的可见回复，再基于对方回复自然回复一次。"
                    "第二条发送内容通过 read_text 出现在聊天记录后立即 done；"
                    "不要第一条发送后就结束，也不要无限等待。"
                )
            elif communication_mode == "continuous_chat":
                hint_body = (
                    "这是明确的持续聊天任务：等待可见新回复并基于最新内容继续回复。"
                    "每轮先用 read_text 确认新回复，再发送一条相关回复并重新验证。"
                    "只有用户实时要求停止或任务给定的结束条件成立时才结束；不要自行调用 done。"
                )
            else:
                hint_body = (
                    "先打开目标联系人的聊天窗口，确认输入框可用，输入消息并发送。"
                    "发送后调用 read_text 确认消息出现在聊天记录中，再调用 done。"
                    "done 前必须有 read_text 的输出证据。"
                )
            self.messages.append({
                "role": "user",
                "content": f"[通信任务提示] {hint_body}",
            })
        # Session continuity: let referential follow-ups ("再来一次" etc.) resolve
        # against prior tasks this session. Injected right after the task so it's
        # in the cache-friendly prefix; gated by config.
        if _is_upload_publish_task(self.task):
            self.messages.append({
                "role": "user",
                "content": (
                    "[Upload/publish task guidance] Treat the media path as a file to select in an "
                    "upload form, not content to play or analyze. Start by observing/focusing the "
                    "target app or creator/upload page. Use upload/post/publish-entry UI, choose the "
                    "file in the file picker, fill requested title/metadata, and stop before final "
                    "publish when requested. If login, QR, face, or phone verification blocks the "
                    "flow, report that exact blocker."
                ),
            })
        if _is_gui_only_task(self.task):
            self.messages.append({
                "role": "user",
                "content": (
                    "[GUI-only task guidance] The user explicitly asked for visible GUI operation. "
                    "Do not use shell/search_files/run_detached/read_file/write_file to perform or "
                    "prepare the work, and do not launch PowerShell/cmd as a workaround. "
                    "Use only exposed GUI tools such as open_app/list_windows/launch/focus_window/"
                    "observe/click/type_text/hotkey/drag/draw_strokes/scroll. Shell is only for final read-only "
                    "verification such as Test-Path/Get-Content/Select-String after the GUI work."
                ),
            })
        if _is_multi_source_research_task(self.task):
            self.messages.append({
                "role": "user",
                "content": (
                    "[Multi-source research guidance] Treat each page/source as a separate record. "
                    "After a source page exposes useful fields, call record_facts(subject, facts, "
                    "source_url, source_title, evidence) before navigating away. Use explicit "
                    "not-visible values instead of guessing. The per-run working set will be returned "
                    "after every update; use it to fill the destination in the requested entity order, "
                    "and never attach a field or URL from one source to another subject. Do not keep "
                    "observing a completed source once its required fields are recorded."
                ),
            })
            explicit_output_paths = _explicit_saved_paths(self.task)
            if explicit_output_paths:
                rendered_paths = "\n".join(
                    f"- {path}" for path in explicit_output_paths[:3]
                )
                self.messages.append({
                    "role": "user",
                    "content": (
                        "[Exact GUI output-path requirement] The task names exact output file paths:\n"
                        f"{rendered_paths}\n"
                        "Use the application's Save As/Export UI, focus the real filename field, "
                        "replace it with the exact full path, and submit the dialog. A different "
                        "auto-generated filename is not success. Do not complete the save plan item "
                        "until file_exists evidence for the exact path is true."
                    ),
                })
        if (
            self.session_context
            and CONFIG.get("agent", "session_memory", default=True)
            and session_mod.should_include_context(self.task)
        ):
            ctx = session_mod.render_context(self.session_context)
            if ctx:
                self.messages.append({"role": "user", "content": ctx})
        # Proactive reuse: when this task strongly matches a past run that left a
        # verified replay script, direct the agent to reuse it first (it's the
        # whole point of skills; replay failure auto-falls-back to live actions).
        # Skipped for coding tasks — they are never in the reusable scope.
        if not self._is_coding:
            try:
                script, recipe = experience.reusable_script_for(self.task)
                if script and recipe:
                    self.messages.append({
                        "role": "user",
                        "content": (
                            f"⚡ 本任务与上次成功完成的「{recipe['task']}」高度一致，已有可复用流程脚本。"
                            f"**第一步请直接调用 reuse_skill 复用**（0-token 重放上次步骤）；"
                            "若重放中途某步失败，它会告诉你卡在哪，你再从那步起实时操作。"),
                    })
                    BUS.publish("memory", action="reuse_suggested", task=recipe["task"])
            except Exception as exc:
                logger.debug("复用建议注入失败: %s", exc)
        is_gui_only_task = _is_gui_only_task(self.task)
        allow_shell_tools = self.allow_shell_tools
        if allow_shell_tools is None:
            allow_shell_tools = not (
                is_gui_only_task
                and _gui_only_forbids_shell(self.task)
            )
        # GUI-only runs may keep a tightly checked read-only shell verifier, but
        # file/search tools invite non-GUI shortcuts and hide fake success.
        allow_file_tools = (not is_gui_only_task) and allow_shell_tools
        schema_profile = _schema_profile_for_task(
            self.task,
            is_coding=self._is_coding,
            native_vision=_main_model_has_vision(),
        )
        tools = tool_schemas(
            planning=plan_on,
            coding_runtime=self._is_coding,
            allow_shell=allow_shell_tools,
            allow_files=allow_file_tools,
            profile=schema_profile,
            native_vision=_main_model_has_vision(),
        )
        model_tool_names = [
            str(item.get("function", {}).get("name") or "")
            for item in tools
            if isinstance(item, dict)
        ]
        BUS.publish("agent_start", run_id=self.run_id, task=self.task,
                    max_steps=max_steps, has_experience=bool(hints),
                    agent="unified", runtime=self.runtime_profile.to_dict(),
                    schema_profile=schema_profile,
                    model_tool_count=len(model_tool_names),
                    model_tool_names=model_tool_names,
                    service_runtime=runtime_fingerprint())
        try:
            raw_client = _client()
            client = build_model_adapter(raw_client, _main_agent_api_settings())
            self._model_adapter = client
            self._visual_closed_loop_active = bool(
                CONFIG.get("controller", "visual_closed_loop", default=True)
                and _main_model_has_vision()
            )
            if self._native_visual_context_enabled():
                self._freeze_native_visual_static_context()
            self._prebind_initial_gui_target()
            # The first decision must start from real pixels, not just the
            # open/focus result or a remembered element tree.
            self._inject_visual_frame(
                phase="initial",
                action="target_prebind" if self.session.hwnd else "",
            )
            self._maybe_plan_upfront(client)
            for step in range(1, max_steps + 1):
                if self.stop_event.is_set():
                    self.status = "stopped"
                    break
                self._drain_interjections()
                resp = self._chat(client, self._model_tools_for_step(tools, step))
                if resp is None:        # Stop requested mid LLM-call → abandon
                    self.status = "stopped"
                    break
                self._track_usage(resp)
                msg = resp.choices[0].message
                reasoning = getattr(msg, "reasoning_content", None) or ""
                self.messages.append(_sanitize_assistant(
                    msg,
                    preserve_reasoning=bool(
                        self._last_llm_thinking_enabled
                        and not self._native_visual_context_enabled()
                    ),
                ))

                if not msg.tool_calls:
                    finish_reason = str(getattr(resp.choices[0], "finish_reason", "") or "")
                    if finish_reason == "length":
                        if not self._is_coding:
                            if self._recover_truncated_gui_tool_call():
                                continue
                            break
                        if self._truncated_response_retries < 2:
                            self._truncated_response_retries += 1
                            # Keep the established coding behavior: large files
                            # may need two chunking corrections at the configured
                            # output budget.
                            self.messages[-1] = {
                                "role": "assistant",
                                "content": "[model output truncated before a valid tool call; partial content omitted]",
                            }
                            self.messages.append({
                                "role": "user",
                                "content": (
                                    "Your previous response hit the output limit before issuing a valid tool call. "
                                    "Do not resend the whole artifact as prose or XML. Call a real tool now. "
                                    "For a large file, write a concise working skeleton first, then use apply_patch "
                                    "in small chunks and verify the resulting file."
                                ),
                            })
                            BUS.publish(
                                "agent_recovery",
                                run_id=self.run_id,
                                kind="truncated_tool_call",
                                attempt=self._truncated_response_retries,
                            )
                            continue
                    # Plain-text reply — the final answer. Emit it ONCE via the
                    # chat event (carrying any reasoning). The step event stays
                    # content-less so the chat pane doesn't show it twice.
                    self.result_text = msg.content or ""
                    BUS.publish("agent_step", run_id=self.run_id, step=step,
                                reasoning="", content="", n_tool_calls=0)
                    if self._reject_incomplete_run_task():
                        BUS.publish("chat", run_id=self.run_id, role="assistant",
                                    content=self.result_text[:500], reasoning=reasoning[:800])
                        continue
                    if self._recent_results:
                        if not self._verify_done(client, steps_left=max_steps - step):
                            if self._continuous_pointer_output_requested():
                                self._continuous_pointer_done_repair_required = True
                            if self._safe_stop_after_repeated_done_rejection():
                                break
                            continue
                        self.status = "done"
                    else:
                        self.status = "reply"
                    BUS.publish("chat", run_id=self.run_id, role="assistant",
                                content=self.result_text, reasoning=reasoning[:800])
                    break

                if not self._is_coding:
                    # A genuine function call completed the one-shot recovery
                    # contract, so a future isolated truncation starts a new
                    # incident and ordinary GUI turns regain their full budget.
                    self._gui_truncated_tool_retries = 0
                    self._gui_tool_call_retry_pending = False
                BUS.publish(
                    "agent_step",
                    run_id=self.run_id,
                    step=step,
                    reasoning=reasoning[:800],
                    content=(msg.content or "")[:800],
                    n_tool_calls=len(msg.tool_calls),
                )

                terminal = self._handle_tool_calls(msg.tool_calls)
                # Track consecutive steps without a verified action for reflect.
                if self._last_verify_ok is True:
                    self._consecutive_unverified = 0
                elif self._last_verify_ok is False or self._last_verify_ok is None:
                    # None means no verify-capable tool was called this step.
                    # Only increment on action tools, not purely observe steps.
                    action_names = {tc.function.name for tc in msg.tool_calls}
                    if action_names & _ACTION_TOOLS:
                        self._consecutive_unverified += 1
                if terminal == "done" and not self._verify_done(client, steps_left=max_steps - step):
                    if self._continuous_pointer_output_requested():
                        self._continuous_pointer_done_repair_required = True
                    if self._safe_stop_after_repeated_done_rejection():
                        break
                    self.run_state.terminal_rejected("done", self._last_done_reject_reason)
                    BUS.publish("run_state", run_id=self.run_id, **self.run_state.to_dict())
                    continue  # done-gate rejected the claim → keep working
                if terminal:
                    self.status = terminal
                    break
                self._surface_world_advisory()
                self._maybe_inject_playbook()
                self._maybe_reflect(step)
            else:
                self.status = "fail"
                self.result_text = f"达到最大步数 {max_steps} 仍未完成"
        except Exception as exc:
            logger.exception("Agent 运行异常")
            self.status = "error"
            self.result_text = str(exc)
            BUS.publish("agent_error", run_id=self.run_id, error=str(exc))

        # Record a reusable recipe (+ export canonical Automation IR) ONLY for
        # non-coding successes. Coding tasks are excluded from the reusable
        # scope entirely — solved fresh next time, guided by the playbook.
        if self.status == "done" and not self._is_coding:
            app_hint = self.session.snapshot.title if self.session.snapshot else ""
            # Trusted (→ reusable skill + linked script) when EITHER the done-gate
            # judge independently confirmed success, OR an action passed external
            # verify; and never when done was force-accepted after max rejects.
            verified_run = (
                (self._done_verified
                 or (self._had_verified_action and self._last_verify_ok is not False))
                and not self._done_unverified)
            # A trusted success becomes replayable canonical Automation IR
            # linked to the recipe, so a future identical task can reuse_skill it.
            script_name = ""
            if verified_run:
                try:
                    from ..recorder import trace as _trace
                    script_path = _trace.export_automation_ir(self.run_id)
                    script_name = script_path.name
                except (FileNotFoundError, ValueError) as exc:
                    logger.debug("成功流程无可重放步骤，跳过导出: %s", exc)
                except OSError as exc:
                    logger.warning("成功流程导出脚本失败(IO): %s", exc)
            experience.record(self.task, app_hint, self._steps_log,
                              verified=verified_run, script=script_name)
        try:
            compiler.ingest_run(self)
        except Exception as exc:
            logger.debug("memory graph ingest failed: %s", exc)
        self._score_experience_feedback()

        try:
            self.session.close()
        except Exception:
            logger.debug("ToolSession close failed", exc_info=True)

        BUS.publish(
            "agent_done",
            run_id=self.run_id,
            status=self.status,
            result=self.result_text[:500],
            steps=len(self._steps_log),
            usage=dict(self.usage_total),
        )
        return {
            "run_id": self.run_id,
            "status": self.status,
            "result": self.result_text,
            "usage": dict(self.usage_total),
        }
