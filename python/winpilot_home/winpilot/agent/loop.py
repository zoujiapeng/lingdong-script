"""Unified WinPilot Agent loop.

One AgentRunner now owns desktop GUI work, Coding Runtime work, tool dispatch,
trace, usage accounting, and done verification. Provider-specific chat details
are centralized in ``winpilot.model_adapter``; this loop keeps the stable
function-calling history, context folding, and tool-result repair behaviors.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import time
import uuid
from typing import Any

import win32gui

from ..config import CONFIG, TRACES_DIR
from ..memory import compiler, experience
from ..model_adapter import (
    accumulate_usage,
    build_model_adapter,
    empty_usage_total,
    model_profile_from_config,
)
from ..modeling import (
    ModelCallError,
    ModelCallRuntime,
    ModelRequestEnvelope,
    ModelRetryPolicy,
    create_openai_client,
)
from ..perception import fusion, intent, reducer, vlm
from ..runtime_info import runtime_fingerprint
from ..fast.action_contract import (
    build_action_envelope,
    iter_atomic_actions,
    parse_atomic_action,
)
from ..utils.image_capture import (
    capture_window_jpeg_b64,
    jpeg_diff_ratio,
    jpeg_diff_ratio_in_normalized_roi,
    normalized_detail_crop_jpeg_b64,
    select_visual_capture_scope,
)
from ..utils.log import BUS, logger
from ..utils.screenshot import Region, virtual_screen_region, window_region
from ..utils.trace_payload import sanitize_trace_payload
from ..visual_runtime.frame_observer import FrameObserver
from ..visual_runtime.normalized_frame_coordinates import NormalizedFrameCoordinateMapper
from ..visual_runtime.submitted_image_coordinates import SubmittedImageCoordinateMapper
from . import playbooks, prompts, session as session_mod
from .context_guidance import ContextGuidanceMixin
from .done_verifier import (
    DoneVerifierMixin,
    _explicit_saved_paths,
)
from .failure_recovery import (
    FailureRecoveryLayer,
    attach_recovery_metadata,
)
from .frame_authority import FrameAuthority, FrameState
from .gui_plan_helpers import (
    _schema_profile_for_task,
)
from .graphical_affordances import detect_graphical_affordances
from .planner import planning_enabled
from .reflection import ReflectionMixin
from .run_state import ACTION_TOOLS as _RUN_ACTION_TOOLS, RunStateMachine
from .runtime_profile import RuntimeProfile, build_runtime_profile
from .scene_coverage import SceneCoverageLedger
from .task_rules import (
    _communication_mode,
    _gui_only_forbids_shell,
    _gui_only_launch_uses_shell,
    _gui_only_shell_allowed,
    _is_coding_task,
    _is_communication_task,
    _is_gui_only_task,
    _is_multi_source_research_task,
    _is_upload_publish_task,
    _quoted_payloads,
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
    "open_app", "launch", "focus_window", "click", "double_click", "type_text", "hotkey",
    "scroll", "drag", "draw_strokes", "tray_click", "clipboard_paste",
    "paste_image_file", "mouse_down", "mouse_up", "move_mouse", "hold_key",
    "run_action_batch", "execute_subgoal", "coordinate_click_test", "wait",
}
_LOW_LEVEL_VISUAL_TOOLS = {
    "click", "double_click", "type_text", "hotkey", "scroll", "drag", "draw_strokes",
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
    "double_click",
    "click_element",
    "type_into_element",  # locates/focuses the field with a click before typing
    "scroll",
    "move_mouse",
    "mouse_down",
    "mouse_up",
    "drag",
    "draw_strokes",
    "select_color",
}

# Tiny controls, hover affordances, sliders and gesture endpoints can be hard to
# judge in a downscaled full-desktop frame.  Reuse pixels from that same
# authoritative frame for a magnified detail after every spatial primitive;
# this adds no second perception owner and never creates crop-local actions.
_NATIVE_DETAIL_CROP_ACTIONS = {
    "click",
    "double_click",
    "scroll",
    "move_mouse",
    "mouse_down",
    "mouse_up",
    "drag",
    "draw_strokes",
}


def _prompt_context_chars(messages: list[dict[str, Any]]) -> int:
    """Measure textual/structural context without walking image payloads.

    P0 telemetry must not serialize the full provider request on every model
    turn.  Image bytes are intentionally represented by a fixed marker; the
    metric is therefore a comparable context-size signal, not a claim about
    encoded JPEG length or token count.
    """
    total = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        total += len(str(message.get("role") or ""))
        content = message.get("content")
        if isinstance(content, str):
            total += len(content)
            continue
        if not isinstance(content, list):
            if content is not None:
                total += len(str(content))
            continue
        for part in content:
            if not isinstance(part, dict):
                total += len(str(part))
                continue
            part_type = str(part.get("type") or "")
            total += len(part_type)
            if part_type in {"image_url", "input_image"}:
                total += 16
            elif isinstance(part.get("text"), str):
                total += len(part["text"])
            elif part.get("content") is not None:
                total += len(str(part.get("content")))
    return total

# Native visual decisions receive controller facts, never controller tactics.
# These fields may remain in raw traces for diagnostics and legacy runtimes,
# but exposing them to the visual model recreates a second planner beside it.
_NATIVE_MODEL_TACTIC_KEYS = frozenset({
    "advice",
    "advisory",
    "fast_path_advice",
    "feedback",
    "guidance",
    "hint",
    "local_actions",
    "needs_observe",
    "needs_replan",
    "next_action",
    "next_tool",
    "patch_hint",
    "post_tool_hints",
    "recommendation",
    "recommended_tool",
    "recovery_directives",
    "strategy",
    "strategy_change_required",
    "suggested_action",
    "suggested_tool",
})

# Pixel drift can invalidate a point selected from a submitted image, but it
# cannot invalidate keyboard input by itself.  Keep this classification about
# physical targeting only: foreground ownership is enforced by Controller's
# scope guard immediately before dispatch, while FrameAuthority owns the
# immutable frame/epoch identity checked by AgentRunner.
_VISUAL_PREFLIGHT_SPATIAL_ACTIONS = {
    "click",
    "double_click",
    "click_element",
    "type_into_element",
    "scroll",
    "move_mouse",
    "mouse_down",
    "mouse_up",
    "drag",
    "draw_strokes",
    "ensure_input_mode",
    "select_color",
    "tray_click",
    "coordinate_click_test",
}

# Native visual turns are transactions, not a chat transcript.  Re-sending
# every prior function call/result makes the prompt grow once per click while
# the only authoritative GUI state is the newest frame.  These bounds keep a
# small rolling action bulletin without losing the original task or plan.
_NATIVE_CONTEXT_RECENT_ACTIONS = 8
_NATIVE_CONTEXT_RECENT_GUIDANCE = 4
_NATIVE_CONTEXT_MAX_DIRECTIVE_CHARS = 4000
_NATIVE_CONTEXT_MAX_SUMMARY_CHARS = 4200
_NATIVE_DRAG_MAX_POINTS = 32
_NATIVE_NO_PROGRESS_RATIO = 0.0005
_NATIVE_NO_PROGRESS_STREAK = 2
_NATIVE_NO_PROGRESS_MAX_BLOCKS = 4
_NATIVE_STAGNATION_SPATIAL_TOOLS = {
    "click", "double_click", "scroll", "drag", "draw_strokes", "move_mouse",
    "tray_click", "coordinate_click_test", "run_action_batch",
}
_NATIVE_DRAW_MAX_STROKES = 24
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
        "native_no_progress_guard",
        "scene_coverage_guard",
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
        ("native_point_scope_guard", "native_point_scope"),
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


def _client():
    api = CONFIG.get("api", default={})
    if not api.get("api_key"):
        raise RuntimeError("config.json 缺少 api.api_key")
    try:
        timeout_s = float(api.get("request_timeout_s", 75) or 75)
    except (TypeError, ValueError):
        timeout_s = 75.0
    # Keep one provider call bounded even when a stale config contains an
    # invalid or unexpectedly large value.  AgentRunner owns the total
    # request watchdog below and ignores late responses after a deadline.
    timeout_s = max(5.0, min(180.0, timeout_s))
    return create_openai_client(
        base_url=api["base_url"],
        api_key=api["api_key"],
        timeout=timeout_s,
    )


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
                 pause_controller: Any | None = None,
                 session_context: list | None = None,
                 max_steps_override: int | None = None,
                 runtime_profile: RuntimeProfile | None = None,
                 allow_shell_tools: bool | None = None,
                 task_intent: Any | None = None,
                 communication_context: dict[str, Any] | None = None) -> None:
        self.task = task
        self.run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.stop_event = stop_event or threading.Event()
        self.pause_controller = pause_controller
        self.runtime_profile = runtime_profile or build_runtime_profile(task)
        native_intent_free = bool(
            not self.runtime_profile.coding
            and str(self.runtime_profile.stage_type or "") == "ExecuteDesktopTask"
            and _main_model_has_vision()
        )
        if native_intent_free:
            self.task_intent = {"original_text": task}
        elif task_intent is None:
            from ..tasking.intent import TaskIntentParser

            parsed_task_intent = TaskIntentParser().parse(task)
            self.task_intent = parsed_task_intent.model_dump(mode="json")
        elif hasattr(task_intent, "model_dump"):
            self.task_intent = task_intent.model_dump(mode="json")
        elif isinstance(task_intent, dict):
            self.task_intent = dict(task_intent)
        else:
            raise TypeError("task_intent must be a TaskIntent or mapping")
        self.session = ToolSession(
            stop_event=self.stop_event,
            task=task,
            runtime_profile=self.runtime_profile,
            task_intent=self.task_intent,
            communication_context=communication_context,
        )
        self._frame_authority = FrameAuthority()
        # FastGuiController and the runner consume the same authoritative
        # reference.  The legacy scalar fields below remain compatibility
        # mirrors until ToolSession's public action envelope accepts FrameState.
        self.session._frame_authority = self._frame_authority
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
        self._candidate_reply_pending = False        # native no-tool answer awaiting the same judge
        self._fresh_done_image_b64 = ""              # authoritative frame for final judge
        self._fresh_done_capture_scope = ""
        self._fresh_done_capture_region = None
        self._fresh_done_capture_reason = ""
        self._pending_visual_payloads: list[str] = []
        self._pending_visual_communication_check = ""
        self._saved_artifact_verified = False
        self._recent_results: list[tuple[str, str]] = []  # tool outputs as done-gate evidence
        self._visible_evidence_ledger: list[dict[str, Any]] = []
        self.session.visible_evidence_ledger = self._visible_evidence_ledger
        self._last_done_reject_reason = ""
        self._last_done_reject_generation: tuple[int, int, int] | None = None
        self._escalate_next = False                  # use stronger model on next _chat
        self._consecutive_unverified = 0             # steps without a verified=true action
        self._last_reflect_step = 0                  # step number of last reflect injection
        self.status = "pending"                     # running/done/fail/reply/stopped/error
        self.result_text = ""
        self.usage_total = empty_usage_total()
        self.model_request_count = 0
        # Cumulative wall-clock time spent waiting for model-backed decisions
        # owned by this runner.  Keep this separate from request count: a
        # request can be retried internally, fail, or be abandoned after Stop,
        # but its elapsed wait still belongs to model time rather than local
        # Controller time.
        self.model_elapsed_ms = 0
        self._model_adapter = None
        self._last_llm_model = ""
        self._last_llm_thinking_enabled = False
        self._last_thinking_policy = ""
        self._decision_step = 0
        self._model_timeout_count = 0
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
        self.session.native_visual_runtime = native_intent_free
        if self.session.native_visual_runtime:
            # The compiler may retain typed metadata for tracing, but native
            # action handlers receive no inferred app/recipient/task mode.
            # Explicit model tool arguments and current pixels are the only
            # execution inputs.
            self.session.task_intent = {"original_text": self.task}
            self.session.communication_context = {
                "active": False,
                "recipient": "",
                "channel": "",
                "send_mode": "send",
            }
        self._tool_history: list[tuple[str, dict[str, Any], str]] = []
        self._completion_rejects = 0
        self._truncated_response_retries = 0
        self._gui_truncated_tool_retries = 0
        self._gui_tool_call_retry_pending = False
        self._failure_recovery_layer = FailureRecoveryLayer()
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
        self._visual_submitted_size: tuple[int, int] | None = None
        self._last_visual_feedback_b64 = ""
        self._last_visual_feedback_action = ""
        # A spatial action rejected only because its pixels moved receives one
        # fresh authoritative frame.  If the model then chooses an action from
        # that exact frame, continuous animation must not create an infinite
        # preflight loop; identity/foreground/geometry gates still apply.
        self._visual_preflight_refresh_frame_id = 0
        # Bounded, immutable post-action frames retained across app switches.
        # A multi-window task cannot be verified from the final foreground
        # window alone (for example, type in Notepad and then draw in Paint).
        self._historical_visual_checkpoints: list[dict[str, Any]] = []
        # Also retain one recent changed frame per low-level action kind. This
        # is bounded temporal evidence for ordered tasks in animated apps; it
        # lets the independent done judge inspect an earlier transient effect
        # without treating stale pixels as the current state.
        self._visual_effect_checkpoints: list[dict[str, Any]] = []
        self._native_visual_static_messages: list[dict[str, Any]] = []
        self._native_visual_user_directives: list[str] = []
        self._last_visual_change_ratio: float | None = None
        self._last_visual_change_comparable = False
        self._pixel_graphical_candidates: list[dict[str, Any]] = []
        self._pixel_graphical_frame_id = 0
        self._scene_coverage = SceneCoverageLedger(
            max_scenes=int(CONFIG.get(
                "controller", "scene_coverage_max_scenes", default=24,
            ) or 24),
            revisit_change_ratio=float(CONFIG.get(
                "controller", "scene_revisit_change_ratio", default=0.0012,
            ) or 0.0012),
        )
        self._visual_semantic_assist_count = 0

    def bind_target_window(self, hwnd: int, hint: str = "") -> None:
        """Carry a controller-resolved window into a bounded semantic stage.

        This is deliberately explicit: a new AgentRunner must not rediscover
        or lose the application state established by the preceding stage.
        """
        # StageRuntime reaches this method only after validating an app-open or
        # explicit desktop handoff. Preserve that trust even if the app swaps
        # its top-level HWND between the open result and this runner starting.
        self.session._target_binding_verified = True
        self.session._remember_target(int(hwnd), str(hint or ""))

    def _mirror_authoritative_frame(self, state: FrameState) -> None:
        """Keep the old scalar API as a read-compatible view of one state.

        New code must read ``_frame_authority.current``.  These assignments are
        deliberately centralized because older Controller/event code still
        accepts scalar frame fields and is being migrated independently.
        """

        self._visual_frame_id = state.frame_id
        self._visual_frame_epoch = state.epoch
        self._visual_frame_hwnd = state.hwnd
        self._visual_frame_region = state.region
        self._visual_frame_scope = state.scope
        self._visual_frame_scope_reason = state.reason
        self._visual_submitted_size = state.submitted_size
        self._last_visual_feedback_b64 = state.image_b64
        self.session._visual_frame_id = state.frame_id
        self.session._visual_frame_epoch = state.epoch
        self.session._visual_frame_scope = state.scope
        self.session._visual_frame_region = {
            "left": state.region[0],
            "top": state.region[1],
            "width": state.region[2],
            "height": state.region[3],
        }
        self.session._visual_submitted_size = list(state.submitted_size)

    def _structured_grounding_is_current(self) -> bool:
        """Allow UIA/OCR annotations only for the current pixel identity."""

        try:
            frame_id = int(getattr(
                self.session, "_semantic_grounding_frame_id", 0,
            ) or 0)
            epoch = int(getattr(
                self.session, "_semantic_grounding_epoch", -1,
            ))
        except (TypeError, ValueError):
            return False
        return self._frame_authority.matches(frame_id, epoch)

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
            "image_x", "image_y", "image_x1", "image_y1", "image_x2", "image_y2",
            "frame_x", "frame_y", "frame_x1", "frame_y1", "frame_x2", "frame_y2",
            "element_id", "label", "fallback_text", "direction", "amount", "keys",
            "name", "title", "subgoal", "index", "operation", "artifact_id",
            "app", "recipient", "scope", "source_hwnd",
        )
        image_source = args.get("source_image_coordinates")
        frame_source = args.get("source_frame_coordinates")
        if isinstance(image_source, dict):
            coordinate_args = {**args, **image_source}
            # The model-side contract is image pixels only. Do not reintroduce
            # internal normalized coordinates into its rolling action ledger.
            for key in (
                "frame_x", "frame_y", "frame_x1", "frame_y1", "frame_x2", "frame_y2",
                "frame_points", "frame_strokes", "constraint_frame_region",
            ):
                coordinate_args.pop(key, None)
        elif isinstance(frame_source, dict):
            coordinate_args = {**args, **frame_source}
        else:
            coordinate_args = args
        compact = {
            key: coordinate_args[key]
            for key in useful
            if key in coordinate_args
        }
        path_points = coordinate_args.get("image_points") or coordinate_args.get("frame_points")
        if isinstance(path_points, list) and path_points:
            compact["path"] = {
                "first": path_points[0],
                "last": path_points[-1],
                "points": len(path_points),
            }
        if "text" in args:
            text = str(args.get("text") or "")
            compact["text"] = text[:80]
            if len(text) > 80:
                compact["text_chars"] = len(text)
        if name == "draw_strokes":
            source = args.get("source_frame_coordinates")
            strokes = (
                args.get("image_strokes")
                or (
                    image_source.get("image_strokes")
                    if isinstance(image_source, dict)
                    else None
                )
                or args.get("frame_strokes")
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
            "error", "failure_type", "artifact_id", "requested_artifact_id",
            "artifact_resolution",
        )
        compact = {key: payload[key] for key in keys if key in payload}
        auto = payload.get("auto_visual_verify")
        if (
            isinstance(auto, dict)
            and auto.get("summary_kind") == "pixel_effect_only"
            and auto.get("semantic_verified") is False
            and auto.get("completion_evidence") is False
        ):
            dispatch_ok = bool(
                payload.get("native_action_executed") is True
                or payload.get("action_executed") is True
                or (
                    payload.get("ok") is True
                    and payload.get("action_executed") is not False
                )
            )
            compact.pop("ok", None)
            compact.pop("verified", None)
            compact.update({
                "device_action_dispatched": dispatch_ok,
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
            "Earlier assistant/tool exchanges were compacted. The immediately preceding "
            "complete exchange may be retained for continuity; use its result together "
            "with the newest authoritative frame below.",
        ]
        pointer_status_fn = getattr(self.session, "_pointer_hold_status", None)
        pointer_status = (
            pointer_status_fn() if callable(pointer_status_fn) else {"active": False}
        )
        if pointer_status.get("active"):
            held_move_count = 0
            gesture_start_found = False
            for history_name, history_args, _history_result in reversed(
                self._tool_history
            ):
                actions = list(iter_atomic_actions(history_name, history_args))
                for atomic in reversed(actions):
                    if atomic.name == "mouse_down":
                        gesture_start_found = True
                        break
                    if atomic.name == "move_mouse":
                        held_move_count += 1
                if gesture_start_found:
                    break
            lines.append(
                "Pointer lease state: "
                f"button={pointer_status.get('button') or 'left'}, "
                f"continuation_moves={held_move_count}, active=true."
            )
            pointer_constraint = pointer_status.get("constraint_frame_region")
            if isinstance(pointer_constraint, dict):
                lines.append(
                    "Pointer lease retains the hard boundary established by the original press."
                )
        # Scene fingerprints remain diagnostic telemetry only. Animated cards,
        # clocks, media and other incidental pixels can look like new scenes;
        # presenting that heuristic to a native-vision model as progress would
        # compete with the authoritative frame and the RunState owner.
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
        if self.run_state.last_visual_no_progress_streak:
            lines.append(
                "Runtime no-progress state: "
                "same spatial action low-change streak="
                f"{self.run_state.last_visual_no_progress_streak}."
            )
        if self.run_state.constraint_rejection_streak:
            lines.append(
                "Runtime unexecuted-constraint state: "
                f"category={self.run_state.constraint_rejection_category}, "
                f"consecutive_rejections={self.run_state.constraint_rejection_streak}."
            )
        return "\n".join(lines)[:_NATIVE_CONTEXT_MAX_SUMMARY_CHARS]

    def _latest_compact_tool_exchange(self) -> list[dict[str, Any]]:
        """Retain one valid assistant/tool pair across native context rolling.

        Dropping every pair made each visual decision look like the first turn:
        after a field-opening click, weaker models repeatedly planned the same
        click because their own immediately preceding intent was gone. Keeping
        exactly one protocol-valid exchange restores continuity without
        carrying an unbounded transcript or stale images.
        """

        for index in range(len(self.messages) - 1, -1, -1):
            assistant = self.messages[index]
            tool_calls = (
                assistant.get("tool_calls")
                if assistant.get("role") == "assistant"
                else None
            )
            if not isinstance(tool_calls, list) or not tool_calls:
                continue
            call_ids = {
                str(item.get("id") or "")
                for item in tool_calls if isinstance(item, dict)
            }
            call_ids.discard("")
            if not call_ids:
                continue
            tool_messages: list[dict[str, Any]] = []
            seen: set[str] = set()
            for candidate in self.messages[index + 1:]:
                if candidate.get("role") == "assistant":
                    break
                if candidate.get("role") != "tool":
                    continue
                call_id = str(candidate.get("tool_call_id") or "")
                if call_id not in call_ids:
                    continue
                seen.add(call_id)
                tool_messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": self._native_action_result_preview(
                        str(candidate.get("content") or "")
                    ),
                })
            if seen != call_ids:
                continue
            compact_assistant = {
                "role": "assistant",
                "content": re.sub(
                    r"\s+", " ", str(assistant.get("content") or "")
                ).strip()[:360],
                "tool_calls": tool_calls,
            }
            return [compact_assistant, *tool_messages]
        return []

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
        latest_exchange = self._latest_compact_tool_exchange()
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
        rolled.extend(latest_exchange)
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
        # StageRuntime has already supplied one fixed, task-independent model
        # tool contract for ExecuteDesktopTask.  Do not reinterpret the request
        # here to hide or add providers; only real runtime state (for example a
        # held pointer lease below) may temporarily constrain the next action.
        selected = tools
        pointer_status_fn = getattr(self.session, "_pointer_hold_status", None)
        pointer_status = (
            pointer_status_fn() if callable(pointer_status_fn) else {"active": False}
        )
        if pointer_status.get("active"):
            # A raw press is a short leased interaction session.  Until it is
            # released, expose only actions that can advance or safely finish
            # that same gesture.  Every move still returns a fresh frame, so
            # the model can visually steer rather than committing an open-loop
            # path from the pre-press image.
            held_order = ("mouse_up", "move_mouse", "wait", "blocked", "fail")
            held_by_name = {
                str(item.get("function", {}).get("name") or ""): item
                for item in selected
            }
            selected = [
                held_by_name[name] for name in held_order if name in held_by_name
            ]
        observer = getattr(self, "_vnext_broker_shadow_observer", None)
        observe_turn = getattr(observer, "observe_turn", None)
        if callable(observe_turn):
            try:
                observe_turn(
                    runner=self,
                    base_tools=tools,
                    legacy_selected_tools=selected,
                    step=step,
                    pointer_status=dict(pointer_status),
                )
            except Exception:
                # A shadow observer is trace-only. It must never change the
                # request, trigger another request, or fail the legacy run.
                logger.debug("vNext Broker shadow observer failed", exc_info=True)
        return selected

    def _is_native_execute_desktop_task(self) -> bool:
        """Whether the visual model owns intent and the first desktop decision."""

        return bool(
            not self._is_coding
            and str(self.runtime_profile.stage_type or "") == "ExecuteDesktopTask"
            and _main_model_has_vision()
        )

    @staticmethod
    def _native_fact_result(result_text: str) -> str:
        """Project a raw tool result to facts without prescribing a next move."""

        try:
            payload = json.loads(result_text or "{}")
        except (json.JSONDecodeError, TypeError, ValueError):
            return str(result_text or "")

        def factual(value: Any) -> Any:
            if isinstance(value, dict):
                return {
                    str(key): factual(item)
                    for key, item in value.items()
                    if str(key).casefold() not in _NATIVE_MODEL_TACTIC_KEYS
                }
            if isinstance(value, list):
                return [factual(item) for item in value]
            return value

        try:
            return json.dumps(
                factual(payload), ensure_ascii=False, default=str,
            )
        except (TypeError, ValueError):
            return "{}"

    def _append_tool_result(self, tool_call_id: str, result_text: str,
                            summary: str = "",
                            image_b64: str = "",
                            tool_name: str = "") -> None:
        # NOTE: mime API does not support multipart tool results (image_url in tool role).
        # Always use plain text; screenshot is injected as a separate user message via
        # _inject_observe_screenshot when image_b64 is empty (fallback path).
        content = (
            self._native_fact_result(result_text)
            if self._is_native_execute_desktop_task()
            else result_text
        )
        if tool_name and tool_name not in {"observe", "read_text"}:
            content = self._compact_tool_evidence(tool_name, content)
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
            "action_executed",
            "native_point_scope_guard",
            "scope_reason",
            "scene_coverage_guard",
            "current_scene_id",
            "current_scene_visit_count",
            "proposed_edge",
            "prior_target_scene_ids",
            "cycle_scene_path",
            "artifact_id",
            "requested_artifact_id",
            "artifact_resolution",
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
        plan = getattr(self.session, "plan", None)
        if plan is not None:
            try:
                rendered_plan = re.sub(r"\s+", " ", str(plan.render() or "")).strip()
            except Exception:
                rendered_plan = ""
            if rendered_plan:
                parts.append(f"current_plan: {rendered_plan[:400]}")
        snapshot = self.session.snapshot
        if snapshot is not None:
            elements = getattr(snapshot, "elements", []) or []
            parts.append(
                f'screen: title="{getattr(snapshot, "title", "")[:80]}" '
                f'hwnd={getattr(snapshot, "hwnd", "")} elements={len(elements)}'
            )
            key_elements = []
            for element in elements:
                text = re.sub(r"\s+", " ", str(getattr(element, "text", "") or "")).strip()
                if not text:
                    continue
                role = str(getattr(element, "role", "") or "element")
                states = ",".join(str(item)[:20] for item in (getattr(element, "states", []) or [])[:3])
                state_note = f" [{states}]" if states else ""
                key_elements.append(f'{role[:30]}:"{text[:50]}"{state_note}')
                if len(key_elements) >= 3:
                    break
            if key_elements:
                parts.append("screen_keys: " + " | ".join(key_elements))
        state = self.run_state.to_dict()
        if state.get("phase") and state.get("phase") != "idle":
            issue = str(state.get("last_issue") or "")[:140]
            parts.append(
                f'run_state: phase={state.get("phase")} '
                f'last_tool={str(state.get("last_tool") or "")[:60]} issue="{issue}"'
            )
        if self.session.world_enabled:
            world_state = self.session.world.state()
            if world_state.get("last_change") or world_state.get("screen_watch"):
                parts.append(
                    "world: "
                    f'last_change="{str(world_state.get("last_change") or "")[:120]}" '
                    f'repeated={world_state.get("repeated")} '
                    f'identical_observes={world_state.get("identical_observes")}'
                )
        if self._recent_results:
            parts.append("recent_tool_evidence:")
            for recency, (name, text) in enumerate(
                reversed(self._recent_results[-3:])
            ):
                # The newest completed tool outcome is the authoritative
                # continuation checkpoint.  Give its structured conclusions
                # enough room to survive the second compaction pass; older
                # results remain useful for chronology but receive a smaller
                # budget.  A uniform 180-char cap used to keep early booleans
                # while silently dropping later semantic conclusions,
                # recovery advice, artifact identities, and other verified
                # evidence fields.
                preview_limit = 520 if recency == 0 else 180
                one_line = self._folded_tool_evidence_preview(
                    str(text), limit=preview_limit,
                )
                parts.append(f"- {name}: {one_line}")
        parts.append(f"folded_item: {folded_summary[:180]}")
        return "\n".join(parts)[:2400]

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
        submitted_size: tuple[int, int] | None = None,
    ) -> str:
        """Stable coarse identity for one model-grounded spatial mutation."""
        name = str(tool_name or "").strip().casefold()
        payload = dict(args or {})
        if name == "double_click":
            # Keep the explicit primitive and click(double=true) equivalent,
            # while still distinguishing both from an ordinary single click.
            name = "click"
            payload["double"] = True
        if name == "click":
            payload["button"] = str(payload.get("button") or "left").casefold()
            payload["double"] = bool(payload.get("double", False))
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
        else:
            image_source = payload.get("source_image_coordinates")
            if isinstance(image_source, dict):
                payload = {**payload, **image_source}
                raw_size = image_source.get("submitted_image_size")
                if (
                    isinstance(raw_size, (list, tuple))
                    and len(raw_size) == 2
                ):
                    try:
                        submitted_size = (int(raw_size[0]), int(raw_size[1]))
                    except (TypeError, ValueError):
                        pass

        # A model may express the same target with exact submitted-image
        # pixels on one turn and normalized frame coordinates on the next.
        # Canonicalize both to frame coordinates before hashing, otherwise a
        # harmless representation switch bypasses stagnation/oscillation
        # guards and can repeatedly toggle or miss the same visual control.
        if (
            isinstance(submitted_size, (list, tuple))
            and len(submitted_size) == 2
        ):
            try:
                image_width = max(1, int(submitted_size[0]))
                image_height = max(1, int(submitted_size[1]))
            except (TypeError, ValueError):
                image_width = image_height = 0
            if image_width and image_height:
                def image_to_frame(value: Any, extent: int) -> float:
                    return float(value) * 1000.0 / max(1, extent - 1)

                image_pairs = (
                    ("image_x", "image_y", "frame_x", "frame_y"),
                    ("image_x1", "image_y1", "frame_x1", "frame_y1"),
                    ("image_x2", "image_y2", "frame_x2", "frame_y2"),
                )
                for image_x, image_y, frame_x, frame_y in image_pairs:
                    if (
                        frame_x not in payload
                        and frame_y not in payload
                        and image_x in payload
                        and image_y in payload
                    ):
                        try:
                            payload[frame_x] = image_to_frame(
                                payload[image_x], image_width,
                            )
                            payload[frame_y] = image_to_frame(
                                payload[image_y], image_height,
                            )
                        except (TypeError, ValueError):
                            pass
                if "frame_points" not in payload and isinstance(
                    payload.get("image_points"), list,
                ):
                    frame_points: list[list[float]] = []
                    for point in payload["image_points"]:
                        if not isinstance(point, (list, tuple)) or len(point) != 2:
                            continue
                        try:
                            frame_points.append([
                                image_to_frame(point[0], image_width),
                                image_to_frame(point[1], image_height),
                            ])
                        except (TypeError, ValueError):
                            continue
                    if frame_points:
                        payload["frame_points"] = frame_points
                if "frame_strokes" not in payload and isinstance(
                    payload.get("image_strokes"), list,
                ):
                    frame_strokes: list[list[list[float]]] = []
                    for stroke in payload["image_strokes"]:
                        if not isinstance(stroke, list):
                            continue
                        frame_stroke: list[list[float]] = []
                        for point in stroke:
                            if not isinstance(point, (list, tuple)) or len(point) != 2:
                                continue
                            try:
                                frame_stroke.append([
                                    image_to_frame(point[0], image_width),
                                    image_to_frame(point[1], image_height),
                                ])
                            except (TypeError, ValueError):
                                continue
                        if frame_stroke:
                            frame_strokes.append(frame_stroke)
                    if frame_strokes:
                        payload["frame_strokes"] = frame_strokes

        def normalize(value: Any, key: str = "") -> Any:
            if isinstance(value, dict):
                return {
                    str(child_key): normalize(child, str(child_key))
                    for child_key, child in sorted(value.items())
                }
            if isinstance(value, (list, tuple)):
                return [normalize(child, key) for child in value]
            if isinstance(value, (int, float)) and (
                key.startswith(("frame_", "image_"))
                or key in {"x", "y", "x1", "y1", "x2", "y2"}
            ):
                # Small coordinate jitter should not bypass the repeat guard.
                return int(round(float(value) / 25.0) * 25)
            if isinstance(value, str):
                return value.strip().casefold()[:160]
            return value if isinstance(value, (int, float, bool)) or value is None else str(value)[:160]

        has_frame_geometry = any(
            key in payload for key in (
                "frame_x", "frame_y", "frame_x1", "frame_y1",
                "frame_x2", "frame_y2", "frame_points", "frame_strokes",
            )
        )
        keys = (
            "frame_x", "frame_y", "frame_x1", "frame_y1", "frame_x2", "frame_y2",
            "frame_points", "frame_strokes", "constraint_frame_region",
            "image_x", "image_y", "image_x1", "image_y1", "image_x2", "image_y2",
            "image_points", "image_strokes", "constraint_image_region",
            "normalized_strokes", "element_id", "label",
            "fallback_text", "role", "expected_region", "direction", "amount", "name",
            "button", "double",
        )
        canonical = {
            "tool": name,
            "frame_region": list(frame_region or ()),
            "target": {
                key: normalize(payload[key], key)
                for key in keys
                if key in payload
                and not (has_frame_geometry and key.startswith("image_"))
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

    def _reset_constraint_rejection_guard(self) -> None:
        self.run_state.clear_constraint_rejections()

    def _apply_constraint_rejection_guard(
        self,
        tool_name: str,
        result: ToolResult,
        *,
        dispatched: bool,
    ) -> None:
        """Attach factual repetition counts for actions that never executed."""
        category = _unexecuted_constraint_category(result.text or "")
        if not category:
            try:
                payload = json.loads(result.text or "{}")
            except (json.JSONDecodeError, TypeError, ValueError):
                payload = {}
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

        try:
            payload = json.loads(result.text or "{}")
        except (json.JSONDecodeError, TypeError, ValueError):
            return
        if not isinstance(payload, dict):
            return

        streak = self.run_state.record_constraint_rejection(category)
        payload.update({
            "constraint_category": category,
            "constraint_rejection_streak": streak,
            "action_executed": False,
        })
        result.text = json.dumps(payload, ensure_ascii=False, default=str)
        BUS.publish(
            "constraint_rejection",
            run_id=self.run_id,
            tool=tool_name,
            category=category,
            streak=streak,
        )

    def _record_native_visual_progress(
        self,
        action_signature: str,
        change_ratio: float | None,
        *,
        comparable: bool,
    ) -> None:
        self.run_state.record_visual_change(
            action_signature,
            change_ratio,
            comparable=comparable,
            threshold=self._native_no_progress_threshold(),
        )

    def _native_no_progress_guard(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any] | None:
        signature = self._native_spatial_action_signature(
            tool_name,
            args,
            frame_region=self._visual_frame_region,
            submitted_size=self._visual_submitted_size,
        )
        streak = self.run_state.visual_no_progress_streak(signature)
        if (
            not signature
            or streak < int(CONFIG.get(
                "controller", "native_no_progress_streak",
                default=_NATIVE_NO_PROGRESS_STREAK,
            ) or _NATIVE_NO_PROGRESS_STREAK)
        ):
            return None
        return {
            "ok": False,
            "verified": False,
            "blocked": True,
            "native_no_progress_guard": True,
            "no_visual_progress": True,
            "repeat_signature": signature,
            "no_progress_streak": streak,
            "observed_change_ratio": round(float(self._last_visual_change_ratio or 0.0), 6),
            "error": (
                "This exact spatial action was not executed again because its preceding "
                "executions produced consecutive same-region frames with negligible visible "
                "change. The latest authoritative frame remains available for the next decision."
            ),
        }

    def _refresh_visual_target_binding(
        self,
        *,
        reason: str,
    ) -> tuple[int, bool]:
        """Refresh a verified app binding without adopting unrelated windows."""
        previous = int(getattr(self.session, "hwnd", 0) or 0)
        refresh = getattr(self.session, "_refresh_bound_target_window", None)
        recovered: int | None = None
        if callable(refresh):
            try:
                recovered = refresh()
            except Exception:
                recovered = None
        else:
            alive = getattr(self.session, "_window_alive", None)
            recover = getattr(self.session, "_recover_bound_target_window", None)
            try:
                stale = bool(previous and callable(alive) and not alive(previous))
            except Exception:
                stale = False
            if stale and callable(recover):
                try:
                    recovered = recover()
                except Exception:
                    recovered = None
        current = int(getattr(self.session, "hwnd", 0) or recovered or previous)
        if recovered and current != int(recovered):
            remember = getattr(self.session, "_remember_target", None)
            if callable(remember):
                try:
                    remember(int(recovered), str(
                        getattr(self.session, "target_hint", "") or ""
                    ))
                    current = int(getattr(self.session, "hwnd", 0) or recovered)
                except Exception:
                    current = int(recovered)
        changed = bool(current and current != previous)
        if changed:
            BUS.publish(
                "visual_target_rebound",
                run_id=self.run_id,
                reason=str(reason or "visual_binding_refresh"),
                previous_target_hwnd=previous,
                target_hwnd=current,
                recovery=(
                    dict(getattr(self.session, "_last_scope_recovery", {}) or {})
                    if isinstance(
                        getattr(self.session, "_last_scope_recovery", None), dict,
                    )
                    else None
                ),
                authoritative_frame_required=True,
            )
        return current, changed

    @staticmethod
    def _mapped_spatial_points(
        tool_name: str,
        args: dict[str, Any],
    ) -> list[tuple[str, int, int]]:
        """Return physical points that a mapped native action will touch."""
        name = str(tool_name or "").strip().casefold()
        if name == "run_action_batch":
            points: list[tuple[str, int, int]] = []
            for action in args.get("actions") or []:
                if not isinstance(action, dict):
                    continue
                child_name = str(
                    action.get("action") or action.get("tool") or ""
                ).strip().casefold()
                if child_name:
                    child_args = {
                        key: value for key, value in action.items()
                        if key not in {"action", "tool"}
                    }
                    points.extend(AgentRunner._mapped_spatial_points(
                        child_name, child_args,
                    ))
            return points

        raw_points: list[tuple[Any, Any]] = []
        if name in {"click", "double_click", "scroll", "move_mouse", "mouse_down", "mouse_up"}:
            raw_points.append((args.get("x"), args.get("y")))
        elif name == "drag":
            raw_points.extend([
                (args.get("x1"), args.get("y1")),
                *((point[0], point[1]) for point in (args.get("points") or [])
                  if isinstance(point, (list, tuple)) and len(point) == 2),
                (args.get("x2"), args.get("y2")),
            ])
        elif name == "draw_strokes":
            raw_points.extend(
                (point[0], point[1])
                for stroke in (args.get("strokes") or [])
                if isinstance(stroke, list)
                for point in stroke
                if isinstance(point, (list, tuple)) and len(point) == 2
            )
        points = []
        for x, y in raw_points:
            try:
                points.append((name, int(x), int(y)))
            except (TypeError, ValueError):
                continue
        return points

    def _native_point_scope_guard(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Keep raw native points off system UI and unrelated windows."""
        # Synthetic/legacy callers use short placeholders rather than a real
        # authoritative JPEG.  There is no trustworthy pixel-to-window point
        # contract to validate in that mode; production frames are far larger.
        if len(str(getattr(self, "_last_visual_feedback_b64", "") or "")) < 128:
            return None
        points = self._mapped_spatial_points(tool_name, args)
        if not points:
            return None
        if self._visual_frame_scope == "full_desktop":
            # The model selected a point from the authoritative complete
            # desktop frame.  Do not reinterpret that visible choice through
            # taskbar or application-membership heuristics.
            return None
        target, target_rebound = self._refresh_visual_target_binding(
            reason="native_point_scope_guard",
        )
        if (
            target_rebound
            and self._visual_frame_scope == "target_window"
            and int(self._visual_frame_hwnd or 0) != int(target or 0)
        ):
            return {
                "ok": False,
                "verified": False,
                "blocked": True,
                "action_executed": False,
                "native_point_scope_guard": True,
                "scope_reason": "target_rebound_requires_fresh_frame",
                "target_hwnd": int(target or 0),
                "previous_frame_hwnd": int(self._visual_frame_hwnd or 0),
                "needs_replan": False,
                "error": (
                    "The verified application replaced its target window after the "
                    "authoritative crop was captured. No input was sent; consume a fresh "
                    "frame from the rebound target before retrying the spatial action."
                ),
            }
        related = getattr(self.session, "_window_related_to_target", None)
        same_process = getattr(self.session, "_same_window_process", None)
        strict_actions = {
            "click", "double_click", "scroll", "mouse_down", "draw_strokes",
        }
        taskbar_classes = {"shell_traywnd", "shell_secondarytraywnd"}
        for action, x, y in points:
            try:
                point_hwnd = int(win32gui.WindowFromPoint((x, y)) or 0)
                root = int(
                    win32gui.GetAncestor(point_hwnd, 2) or point_hwnd
                ) if point_hwnd else 0
                class_name = str(
                    win32gui.GetClassName(root) if root else ""
                ).strip()
                title = str(
                    win32gui.GetWindowText(root) if root else ""
                ).strip()
            except Exception:
                point_hwnd = root = 0
                class_name = title = ""
            reason = ""
            if class_name.casefold() in taskbar_classes:
                reason = "system_taskbar_surface"
            elif action in strict_actions and target:
                belongs = bool(
                    root
                    and (
                        (callable(related) and related(root, target))
                        or (callable(same_process) and same_process(root, target))
                    )
                )
                if not belongs:
                    reason = "unrelated_window_surface" if root else "no_window_at_point"
            if not reason:
                continue
            return {
                "ok": False,
                "verified": False,
                "blocked": True,
                "action_executed": False,
                "native_point_scope_guard": True,
                "scope_reason": reason,
                "action": action,
                "point": [x, y],
                "point_hwnd": root,
                "point_window_class": class_name[:80],
                "point_window_title": title[:120],
                "target_hwnd": target,
                "needs_replan": False,
                "error": (
                    "The mapped point belongs to the Windows taskbar or an unrelated "
                    "top-level window, not the bound target surface. No input was sent. "
                    "Inspect the current authoritative frame, refocus/rebind the intended "
                    "window, and choose a point inside its visible client content. Use a "
                    "dedicated tray/taskbar tool for intentional system-shell interaction."
                ),
            }
        return None

    def _visual_semantic_grounding_assist(
        self,
        *,
        hwnd: int,
        frame_region: Any,
        force_refresh: bool,
        focus_frame_roi: tuple[float, float, float, float] | None = None,
    ) -> tuple[str, Any | None, dict[str, Any]]:
        """Return compact UIA/OCR anchors aligned to a native visual frame.

        Native pixels remain authoritative, but a vision model should not have
        to estimate the center of text-rendered controls from scratch after a
        miss.  This path is deliberately application-agnostic: it reuses a
        recent daemon snapshot when possible and otherwise performs one bounded
        pixel-text fusion pass.  The resulting element ids stay usable by the
        same semantic click controller, while every physical action still goes
        through fresh-frame and visual-servo checks.
        """
        if not bool(CONFIG.get(
            "controller", "semantic_grounding_assist", default=False,
        )):
            return "", None, {"enabled": False}
        max_refreshes = max(0, int(CONFIG.get(
            "controller", "semantic_grounding_assist_max_refreshes", default=12,
        ) or 12))
        snapshot = getattr(self.session, "snapshot", None)
        try:
            snapshot_matches = bool(
                snapshot is not None
                and int(getattr(snapshot, "hwnd", 0) or 0) == int(hwnd)
                and getattr(snapshot, "elements", None)
            )
        except (TypeError, ValueError):
            snapshot_matches = False
        source = "session_snapshot" if snapshot_matches else ""

        if not snapshot_matches and not force_refresh:
            try:
                from ..fast.perception_daemon import PERCEPTION_DAEMON

                daemon_snapshot = PERCEPTION_DAEMON.latest_snapshot(
                    int(hwnd), max_age_ms=5_000,
                )
                if daemon_snapshot is not None and getattr(
                    daemon_snapshot, "elements", None,
                ):
                    snapshot = daemon_snapshot
                    snapshot_matches = True
                    source = "perception_daemon"
            except Exception:
                logger.debug("semantic grounding daemon lookup failed", exc_info=True)

        refresh_allowed = self._visual_semantic_assist_count < max_refreshes
        if force_refresh or not snapshot_matches:
            if not refresh_allowed:
                return "", None, {
                    "enabled": True,
                    "source": source or "none",
                    "refresh_budget_exhausted": True,
                    "refresh_count": self._visual_semantic_assist_count,
                }
            self._visual_semantic_assist_count += 1
            try:
                snapshot = fusion.observe(
                    int(hwnd),
                    include_icons=False,
                    perception_mode="pixel_text",
                    stop_event=self.stop_event,
                )
                snapshot_matches = bool(getattr(snapshot, "elements", None))
                source = "fresh_pixel_text_fusion"
            except Exception as exc:
                logger.debug("semantic grounding refresh failed: %s", exc)
                return "", None, {
                    "enabled": True,
                    "source": "fresh_pixel_text_fusion",
                    "error": str(exc)[:240],
                    "refresh_count": self._visual_semantic_assist_count,
                }

        if snapshot is None or not snapshot_matches:
            return "", None, {
                "enabled": True,
                "source": source or "none",
                "candidate_count": 0,
                "refresh_count": self._visual_semantic_assist_count,
            }

        # Element ids are only turn-local, but must be unique and positive for
        # the semantic controller. Incremental OCR snapshots do not always pass
        # through the final fusion id assignment.
        seen_ids: set[int] = set()
        next_id = 1
        for element in list(getattr(snapshot, "elements", None) or []):
            try:
                elem_id = int(getattr(element, "elem_id", -1) or -1)
            except (TypeError, ValueError):
                elem_id = -1
            if elem_id <= 0 or elem_id in seen_ids:
                while next_id in seen_ids:
                    next_id += 1
                element.elem_id = next_id
                elem_id = next_id
            seen_ids.add(elem_id)
            next_id = max(next_id, elem_id + 1)

        try:
            recent_changes = self.session._fast_screen_status()
        except Exception:
            recent_changes = {}
        reduced = reducer.reduce_snapshot(
            snapshot,
            task=self.task,
            recent_changes=recent_changes,
            # Keep enough ranked structure to recover a small unlabeled
            # control near a failed visual target. The final model payload is
            # still compacted below; this does not expose a full accessibility
            # tree or favor any particular application.
            max_candidates=36,
            communication_context=getattr(
                self.session, "communication_context", None,
            ),
        )
        all_candidates: list[dict[str, Any]] = []
        frame_left = int(frame_region.left)
        frame_top = int(frame_region.top)
        frame_width = max(1, int(frame_region.width))
        frame_height = max(1, int(frame_region.height))
        for item in reduced.get("top_candidates") or []:
            if not isinstance(item, dict):
                continue
            bbox = item.get("bbox")
            center = item.get("center")
            if not (
                isinstance(bbox, (list, tuple)) and len(bbox) == 4
                and isinstance(center, (list, tuple)) and len(center) == 2
            ):
                continue
            try:
                left = float(bbox[0])
                top = float(bbox[1])
                width = max(1.0, float(bbox[2]))
                height = max(1.0, float(bbox[3]))
                cx = float(center[0])
                cy = float(center[1])
            except (TypeError, ValueError):
                continue
            right = left + width
            bottom = top + height
            if (
                right <= frame_left
                or bottom <= frame_top
                or left >= frame_left + frame_width
                or top >= frame_top + frame_height
            ):
                continue

            def frame_value(value: float, origin: int, extent: int) -> int:
                return max(0, min(1000, int(round(
                    (value - origin) * 1000.0 / max(1, extent - 1)
                ))))

            all_candidates.append({
                "element_id": item.get("elem_id"),
                "label": re.sub(r"\s+", " ", str(item.get("label") or "")).strip()[:80],
                "role": str(item.get("role") or "element")[:32],
                "center_frame": [
                    frame_value(cx, frame_left, frame_width),
                    frame_value(cy, frame_top, frame_height),
                ],
                "bbox_frame": [
                    frame_value(left, frame_left, frame_width),
                    frame_value(top, frame_top, frame_height),
                    frame_value(right, frame_left, frame_width),
                    frame_value(bottom, frame_top, frame_height),
                ],
                "source": str(item.get("source") or "unknown")[:20],
            })
        if not all_candidates:
            return "", snapshot, {
                "enabled": True,
                "source": source,
                "candidate_count": 0,
                "refresh_count": self._visual_semantic_assist_count,
            }

        focus_candidates: list[dict[str, Any]] = []
        if isinstance(focus_frame_roi, (list, tuple)) and len(focus_frame_roi) == 4:
            try:
                focus_left, focus_top, focus_right, focus_bottom = (
                    max(0.0, min(1000.0, float(value)))
                    for value in focus_frame_roi
                )
            except (TypeError, ValueError):
                focus_left = focus_top = focus_right = focus_bottom = 0.0
            if focus_right > focus_left and focus_bottom > focus_top:
                padding = 35.0
                focus_left = max(0.0, focus_left - padding)
                focus_top = max(0.0, focus_top - padding)
                focus_right = min(1000.0, focus_right + padding)
                focus_bottom = min(1000.0, focus_bottom + padding)
                for candidate in all_candidates:
                    box = candidate.get("bbox_frame")
                    if not isinstance(box, list) or len(box) != 4:
                        continue
                    if (
                        float(box[2]) < focus_left
                        or float(box[3]) < focus_top
                        or float(box[0]) > focus_right
                        or float(box[1]) > focus_bottom
                    ):
                        continue
                    focused = dict(candidate)
                    focused["near_failed_action"] = True
                    focus_candidates.append(focused)
                    if len(focus_candidates) >= 10:
                        break

        candidates: list[dict[str, Any]] = []
        seen_candidate_ids: set[Any] = set()
        for candidate in [*focus_candidates, *all_candidates[:12]]:
            identity = candidate.get("element_id")
            if identity in seen_candidate_ids:
                # Preserve the recovery annotation if this candidate was also
                # one of the task-ranked defaults.
                continue
            seen_candidate_ids.add(identity)
            candidates.append(candidate)
            if len(candidates) >= 18:
                break

        # Do not publish the snapshot here.  The caller first atomically commits
        # the pixels and then promotes this annotation with that exact
        # frame_id/epoch; a detached UIA/OCR result must never become current.
        line = (
            "Fresh text/structure anchors (advisory localization only; current pixels decide meaning and state): "
            + json.dumps(candidates, ensure_ascii=False, separators=(",", ":"))
            + ". Ground any native spatial action in exact pixels of the current full image. "
            + (
                "Candidates marked near_failed_action are fresh structured affordances in or "
                "beside the last ineffective target. Use an exact element_id if one matches the "
                "visible control; otherwise leave that failed region or change the operation, "
                "rather than jittering the old coordinates. "
                if focus_candidates else ""
            )
        )
        return line, snapshot, {
            "enabled": True,
            "source": source,
            "candidate_count": len(candidates),
            "focus_candidate_count": len(focus_candidates),
            "refresh_count": self._visual_semantic_assist_count,
        }

    def _visual_graphical_grounding_assist(
        self,
        image_b64: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Measure pixel-only hypotheses for diagnostics, never model policy.

        These boxes have no application meaning.  Injecting them into every
        visual turn previously ended with an imperative to click one of their
        centres, which biased the model toward repeated clicks even after a
        field was visibly focused.  Keep the measurements on the event bus for
        debugging, while leaving the native frame itself as model authority.
        """
        candidates = detect_graphical_affordances(
            image_b64,
            max_candidates=int(CONFIG.get(
                "controller", "pixel_graphical_max_candidates", default=16,
            ) or 16),
        )
        return "", candidates

    def _inject_visual_frame(
        self,
        *,
        phase: str,
        action: str = "",
        action_args: dict[str, Any] | None = None,
        action_executed: bool = True,
        image_b64: str = "",
        frame_region: Any | None = None,
        result_preview: str = "",
        action_signature: str = "",
        action_roi: tuple[float, float, float, float] | None = None,
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
        self._refresh_visual_target_binding(
            reason=f"{str(phase or 'visual')}_frame_capture",
        )
        hwnd = int(getattr(self.session, "hwnd", 0) or 0)
        previous_state = self._frame_authority.current
        previous_frame_id = (
            previous_state.frame_id
            if previous_state is not None else int(self._visual_frame_id or 0)
        )
        previous_b64 = (
            previous_state.image_b64
            if previous_state is not None else self._last_visual_feedback_b64
        )
        previous_hwnd = (
            previous_state.hwnd
            if previous_state is not None else int(self._visual_frame_hwnd or 0)
        )
        previous_region = (
            previous_state.region
            if previous_state is not None else self._visual_frame_region
        )
        previous_scope = (
            previous_state.scope
            if previous_state is not None else self._visual_frame_scope
        )
        started = time.monotonic()
        try:
            # Construct with this module's current dependencies on every call.
            # Besides making the seam injectable for vNext, this preserves the
            # legacy test/runtime ability to replace capture and scope owners.
            observer = FrameObserver(
                self._frame_authority,
                capture=capture_window_jpeg_b64,
                select_scope=select_visual_capture_scope,
                settled_initial_capture=self._capture_settled_initial_frame,
                virtual_desktop=virtual_screen_region,
            )
            epoch = int(getattr(self.session, "_screen_epoch", 0) or 0)
            captured = observer.capture(
                epoch=epoch,
                hwnd=hwnd,
                phase=phase,
                action=action,
                provided_image_b64=image_b64,
                provided_region=frame_region,
                cached_capture=getattr(
                    self.session, "_last_native_frame_capture", None,
                ),
                related_window=getattr(
                    self.session, "_window_related_to_target", None,
                ),
                expected_previous_frame_id=(
                    previous_state.frame_id if previous_state is not None else 0
                ),
            )
            if captured is None:
                return False
            region = captured.region
            scope = captured.scope
            scope_reason = captured.reason
            b64 = captured.image_b64
            region_tuple = (
                int(region.left), int(region.top), int(region.width), int(region.height),
            )
            change_comparable = bool(
                phase == "post_action"
                and previous_b64
                and previous_hwnd == hwnd
                and previous_region == region_tuple
                and previous_scope == scope
            )
            global_change_ratio = (
                jpeg_diff_ratio(previous_b64, b64) if change_comparable else None
            )
            roi_change_ratio = (
                jpeg_diff_ratio_in_normalized_roi(previous_b64, b64, action_roi)
                if change_comparable and action_roi is not None else None
            )
            change_ratio = (
                roi_change_ratio
                if roi_change_ratio is not None
                else global_change_ratio
            )
            no_visual_progress = bool(
                change_comparable
                and change_ratio is not None
                and float(change_ratio) <= self._native_no_progress_threshold()
            )
            semantic_grounding_line = ""
            semantic_grounding_snapshot = None
            semantic_grounding_meta: dict[str, Any] = {}
            needs_semantic_reanchor = bool(
                hwnd
                and not self._is_native_execute_desktop_task()
                and (
                    phase == "initial"
                    or no_visual_progress
                    or (
                        phase == "post_action"
                        and (
                            not change_comparable
                            or (previous_hwnd and previous_hwnd != hwnd)
                        )
                    )
                )
            )
            if needs_semantic_reanchor:
                (
                    semantic_grounding_line,
                    semantic_grounding_snapshot,
                    semantic_grounding_meta,
                ) = self._visual_semantic_grounding_assist(
                    hwnd=hwnd,
                    frame_region=region,
                    force_refresh=True,
                    focus_frame_roi=(action_roi if no_visual_progress else None),
                )
            if self._is_native_execute_desktop_task():
                graphical_grounding_line = ""
                pixel_graphical_candidates = []
            else:
                (
                    graphical_grounding_line,
                    pixel_graphical_candidates,
                ) = self._visual_graphical_grounding_assist(b64)
            frame_state = observer.commit(captured)
            region = captured.region
            submitted_width, submitted_height = frame_state.submitted_size
            epoch = frame_state.epoch
            frame_id = frame_state.frame_id
            self._mirror_authoritative_frame(frame_state)
            self._record_visual_effect_checkpoint(
                phase=phase,
                action=action,
                action_executed=bool(action_executed),
                image_b64=b64,
                hwnd=hwnd,
                frame_id=frame_id,
                screen_epoch=epoch,
                change_comparable=change_comparable,
                change_ratio=change_ratio,
            )
            if semantic_grounding_snapshot is not None:
                semantic_grounding_meta = {
                    **semantic_grounding_meta,
                    "frame_id": frame_id,
                    "epoch": epoch,
                }
            self._pixel_graphical_candidates = [
                dict(item) for item in pixel_graphical_candidates
            ]
            self._pixel_graphical_frame_id = frame_id
            BUS.publish(
                "pixel_graphical_affordances",
                run_id=self.run_id,
                frame_id=frame_id,
                hwnd=hwnd,
                candidate_count=len(pixel_graphical_candidates),
                candidates=pixel_graphical_candidates,
            )
            scene_coverage_state = self._scene_coverage.record_frame(
                image_b64=b64,
                hwnd=hwnd,
                frame_region=region_tuple,
                frame_id=frame_id,
                phase=phase,
                action=action,
                action_args=action_args,
                action_signature=action_signature,
                action_executed=bool(action_executed),
            )
            if scene_coverage_state:
                BUS.publish(
                    "scene_coverage",
                    run_id=self.run_id,
                    **scene_coverage_state,
                )
            action_line = f" after action {action!r}" if action else ""
            # Native rolling context already carries a compact executed/effect/
            # semantic outcome.  Repeating the raw result beside the newest
            # pixels used to expose an early ``ok:true`` prefix while truncating
            # the later semantic=false fields, contradicting the real frame.
            preview_line = ""
            scope_line = (
                "This image is a verified foreground target-window crop. "
                if scope == "target_window"
                else "This image is the complete virtual desktop, including all monitors. "
            )
            if phase == "post_action" and change_comparable and change_ratio is not None:
                progress_line = (
                    f"Same-region pixel change ratio since frame #{previous_frame_id}: "
                    f"{float(change_ratio):.6f}; this measurement is diagnostic, not a "
                    "success or failure verdict. "
                )
            elif phase == "post_action":
                progress_line = "No comparable same-region prior frame is available. "
            else:
                progress_line = ""
            frame_content: list[dict[str, Any]] = [
                {
                    "type": "text",
                    "text": (
                        f"[Visual closed-loop frame #{frame_id}; phase={phase}{action_line}. "
                        "This real image is the authoritative current state. "
                        f"Frame scope={scope}; selection_reason={scope_reason}. "
                        f"{scope_line}{progress_line}"
                        f"{semantic_grounding_line}{graphical_grounding_line}"
                        f"Exact authoritative full-image size={submitted_width}x{submitted_height} pixels. "
                        "All native spatial tool coordinates use image_* pixel values in this "
                        f"full image: x=0..{submitted_width - 1}, y=0..{submitted_height - 1}. "
                        "Choose the next response from the original request and these pixels. "
                        f"{preview_line}]"
                    ),
                },
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ]
            if (
                phase == "post_action"
                and action in _NATIVE_DETAIL_CROP_ACTIONS
                and action_roi is not None
            ):
                detail_b64, detail_bounds = normalized_detail_crop_jpeg_b64(
                    b64, action_roi,
                )
                if detail_b64 and detail_bounds:
                    detail_image_bounds = [
                        int(round(float(detail_bounds[0]) * max(0, submitted_width - 1) / 1000.0)),
                        int(round(float(detail_bounds[1]) * max(0, submitted_height - 1) / 1000.0)),
                        int(round(float(detail_bounds[2]) * max(0, submitted_width - 1) / 1000.0)),
                        int(round(float(detail_bounds[3]) * max(0, submitted_height - 1) / 1000.0)),
                    ]
                    frame_content.extend([
                        {
                            "type": "text",
                            "text": (
                                "[Same-frame magnified detail around the last spatial action; "
                                f"source full-image pixel bounds={detail_image_bounds}. This crop is only "
                                "for visual inspection. Do not use crop-local coordinates; "
                                "all actions still use pixels of the full authoritative image.]"
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{detail_b64}",
                            },
                        },
                    ])
            self.messages.append({
                "role": "user",
                "content": frame_content,
            })
            self._foldable.append((len(self.messages) - 1, "[已折叠 screenshot]"))
            self._fold_old()
            self._last_visual_feedback_action = str(action or phase)
            if phase == "preflight" and action == "visual_preflight_changed":
                self._visual_preflight_refresh_frame_id = frame_id
            self._last_visual_change_ratio = change_ratio
            self._last_visual_change_comparable = change_comparable
            if phase == "post_action":
                self._record_native_visual_progress(
                    action_signature,
                    change_ratio,
                    comparable=change_comparable,
                )
            # Every newly committed pixel frame invalidates older UIA/OCR.
            # Promote a fresh annotation only when it binds to this exact
            # immutable frame identity.
            self._invalidate_structured_grounding()
            semantic_grounding_promoted = bool(
                semantic_grounding_snapshot is not None
                and int(getattr(semantic_grounding_snapshot, "hwnd", 0) or 0) == hwnd
                and self._frame_authority.matches(frame_id, epoch)
            )
            if semantic_grounding_promoted:
                self.session.snapshot = semantic_grounding_snapshot
                self.session._semantic_grounding_frame_id = frame_id
                self.session._semantic_grounding_epoch = epoch
                self.session._observe_cache = None
            if semantic_grounding_snapshot is not None:
                semantic_grounding_meta["promoted"] = semantic_grounding_promoted
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
                global_pixel_change_ratio=(
                    round(float(global_change_ratio), 8)
                    if global_change_ratio is not None else None
                ),
                action_roi=(
                    [round(float(value), 2) for value in action_roi]
                    if action_roi is not None else None
                ),
                progress_scope=(
                    "action_target_roi" if roi_change_ratio is not None else "whole_frame"
                ),
                no_visual_progress=no_visual_progress,
                no_progress_streak=int(self.run_state.last_visual_no_progress_streak),
                semantic_grounding=semantic_grounding_meta,
                pixel_graphical_candidate_count=len(
                    pixel_graphical_candidates
                ),
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
        self.session._semantic_grounding_frame_id = 0
        self.session._semantic_grounding_epoch = -1
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

        def submitted_image_point(
            payload: dict[str, Any],
            x_key: str,
            y_key: str,
        ) -> tuple[float, float] | None:
            submitted = self._visual_submitted_size
            if not submitted or x_key not in payload or y_key not in payload:
                return None
            try:
                image_x = float(payload[x_key])
                image_y = float(payload[y_key])
            except (TypeError, ValueError):
                return None
            image_width, image_height = submitted
            if not (
                0.0 <= image_x <= max(0, image_width - 1)
                and 0.0 <= image_y <= max(0, image_height - 1)
            ):
                return None
            return (
                image_x * 1000.0 / max(1, image_width - 1),
                image_y * 1000.0 / max(1, image_height - 1),
            )

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
            if not self._structured_grounding_is_current():
                return False
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
            source_frame = payload.get("source_frame_coordinates")
            if isinstance(source_frame, dict) and source_frame:
                # Mapped execution payloads retain their authoritative source
                # coordinates.  Reuse those for a later semantic/post-OCR
                # preflight instead of expanding back to the whole desktop.
                collect(
                    name,
                    {
                        **{
                            key: value for key, value in payload.items()
                            if key != "source_frame_coordinates"
                        },
                        **source_frame,
                    },
                )
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
            if name in {"click", "double_click", "scroll", "move_mouse", "mouse_down", "mouse_up"}:
                x = number(payload.get("frame_x"))
                y = number(payload.get("frame_y"))
                if x is not None and y is not None:
                    add_points([(x, y)])
                    return
                image_point = submitted_image_point(payload, "image_x", "image_y")
                if image_point is not None:
                    add_points([image_point])
                    return
            elif name == "drag":
                points: list[tuple[float, float]] = []
                for x_key, y_key in (("frame_x1", "frame_y1"), ("frame_x2", "frame_y2")):
                    x = number(payload.get(x_key))
                    y = number(payload.get(y_key))
                    if x is not None and y is not None:
                        points.append((x, y))
                for x_key, y_key in (
                    ("image_x1", "image_y1"), ("image_x2", "image_y2"),
                ):
                    image_point = submitted_image_point(payload, x_key, y_key)
                    if image_point is not None:
                        points.append(image_point)
                for raw in payload.get("frame_points") or []:
                    if isinstance(raw, (list, tuple)) and len(raw) == 2:
                        x, y = number(raw[0]), number(raw[1])
                        if x is not None and y is not None:
                            points.append((x, y))
                for raw in payload.get("image_points") or []:
                    if isinstance(raw, (list, tuple)) and len(raw) == 2:
                        image_point = submitted_image_point(
                            {"image_x": raw[0], "image_y": raw[1]},
                            "image_x",
                            "image_y",
                        )
                        if image_point is not None:
                            points.append(image_point)
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
                for stroke in payload.get("image_strokes") or []:
                    for raw in stroke if isinstance(stroke, list) else []:
                        if isinstance(raw, (list, tuple)) and len(raw) == 2:
                            image_point = submitted_image_point(
                                {"image_x": raw[0], "image_y": raw[1]},
                                "image_x",
                                "image_y",
                            )
                            if image_point is not None:
                                points.append(image_point)
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

    @staticmethod
    def _visual_preflight_requires_pixel_stability(
        tool_name: str,
        args: dict[str, Any],
    ) -> bool:
        """Whether the action selects a physical target from submitted pixels.

        Keyboard and clipboard actions depend on the bound foreground identity,
        not on every pixel in that window remaining static.  Containers inherit
        pixel sensitivity only from their declared atomic children; an opaque
        container remains conservative because its local controller may still
        select a visual target.
        """

        name = str(tool_name or "").strip().casefold()
        if name == "type_text" and args.get("element_id") is not None:
            # Compatibility calls with an explicit element resolve/focus that
            # element and may click it before typing; they are spatial even
            # though ordinary type_text is keyboard-only.
            return True
        actions = list(iter_atomic_actions(name, args if isinstance(args, dict) else {}))
        if name in {"run_action_batch", "execute_subgoal"} and not actions:
            return True
        return any(action.name in _VISUAL_PREFLIGHT_SPATIAL_ACTIONS for action in actions)

    def _has_fresh_visual_identity(self) -> bool:
        """Check immutable frame/target identity without requiring static geometry.

        This is the admission contract for non-spatial actions.  Controller's
        scope guard separately proves foreground ownership immediately before
        input; keeping that check out of FrameAuthority preserves the single
        action-owner boundary.
        """

        hwnd = int(getattr(self.session, "hwnd", 0) or 0)
        epoch = int(getattr(self.session, "_screen_epoch", 0) or 0)
        if not self._last_visual_feedback_b64:
            return False
        authoritative = self._frame_authority.current
        if authoritative is None:
            # Legacy synthetic/replay callers hydrate only the scalar mirrors.
            return bool(
                (
                    self._visual_frame_hwnd == hwnd
                    and self._visual_frame_epoch == epoch
                )
                or (
                    self._visual_frame_hwnd == 0
                    and self._visual_frame_epoch == -1
                )
            )
        return bool(
            self._visual_frame_hwnd == hwnd
            and self._visual_frame_epoch == epoch
            and authoritative.frame_id == int(self._visual_frame_id or 0)
            and authoritative.hwnd == hwnd
            and authoritative.is_fresh_for(epoch=epoch)
        )

    def _has_fresh_visual_grounding(self) -> bool:
        hwnd = int(getattr(self.session, "hwnd", 0) or 0)
        epoch = int(getattr(self.session, "_screen_epoch", 0) or 0)
        authoritative = self._frame_authority.current
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
                    enabled=None,
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
        authority_matches = bool(
            authoritative is None  # legacy synthetic/replay compatibility
            or (
                authoritative.frame_id == int(self._visual_frame_id or 0)
                and authoritative.epoch == self._visual_frame_epoch
                and authoritative.hwnd == int(self._visual_frame_hwnd or 0)
                and authoritative.region == self._visual_frame_region
                and authoritative.scope == self._visual_frame_scope
                and authoritative.submitted_size == self._visual_submitted_size
                and authoritative.image_b64 == self._last_visual_feedback_b64
                and authoritative.is_fresh_for(epoch=epoch)
            )
        )
        return bool(
            hwnd_matches
            and self._visual_frame_epoch == epoch
            and self._last_visual_feedback_b64
            and region_matches
            and scope_matches
            and authority_matches
        )

    def _preflight_visual_frame(
        self,
        tool_name: str = "",
        args: dict[str, Any] | None = None,
    ) -> tuple[str, float | None, str, Any | None]:
        """Validate action identity and, when spatial, its selected pixels.

        Internal screen epochs cannot see a webpage auto-refresh, popup, user
        occlusion, or animation. A cheap fresh capture before low-level input
        closes that gap. Spatial target changes return a fresh frame once;
        keyboard/clipboard actions ignore unrelated pixel motion and rely on
        immutable target identity plus Controller's foreground scope guard.
        """
        action_args = args if isinstance(args, dict) else {}
        pixel_stability_required = self._visual_preflight_requires_pixel_stability(
            tool_name, action_args,
        )
        previous = self._last_visual_feedback_b64
        # Unit tests and legacy synthetic callers use short placeholders, not
        # real JPEGs. Production frames are substantially larger.
        if len(previous) < 128:
            return "", 0.0, "", None
        previous_target = int(getattr(self.session, "hwnd", 0) or 0)
        hwnd, target_rebound = self._refresh_visual_target_binding(
            reason="visual_preflight",
        )
        if (
            target_rebound
            and int(self._visual_frame_hwnd or 0) != int(hwnd or 0)
        ):
            started = time.monotonic()
            try:
                if self._visual_frame_scope == "full_desktop":
                    region = virtual_screen_region()
                else:
                    decision = select_visual_capture_scope(
                        hwnd or None,
                        enabled=None,
                        related_window=getattr(
                            self.session, "_window_related_to_target", None,
                        ),
                    )
                    region = decision.region
                current = capture_window_jpeg_b64(hwnd, region=region)
                error = "" if current else "fresh rebound target capture returned empty"
            except Exception as exc:
                current = ""
                region = None
                error = str(exc)
            BUS.publish(
                "visual_preflight",
                run_id=self.run_id,
                ok=bool(current),
                changed=bool(current),
                target_rebound=True,
                previous_target_hwnd=previous_target,
                target_hwnd=int(hwnd or 0),
                capture_region=(
                    [int(region.left), int(region.top), int(region.width), int(region.height)]
                    if region is not None else None
                ),
                decision_scope="target_rebound",
                capture_latency_ms=int((time.monotonic() - started) * 1000),
                error=error,
            )
            if current:
                # A successor target is a new identity even when a full-desktop
                # capture has the same geometry. Returning its fresh pixels
                # cancels this action and attaches the new binding in one turn.
                return current, 1.0, "", region
            return "", None, error, region
        identity_is_current = (
            self._has_fresh_visual_grounding()
            if pixel_stability_required
            else self._has_fresh_visual_identity()
        )
        if not identity_is_current:
            # Freshness is checked once before entering preflight, but the
            # target binding or epoch can change in that small interval.  A
            # real authoritative frame becoming stale remains a hard barrier;
            # pixel and geometry stability are intentionally excluded here for
            # keyboard/clipboard input.
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
                    enabled=None,
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
                    enabled=None,
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
        # Close the epoch/HWND race across the capture itself. Geometry is
        # deliberately not part of identity-only freshness.
        if not self._has_fresh_visual_identity():
            return (
                "",
                None,
                "authoritative visual identity changed during preflight capture",
                region,
            )
        if geometry_changed_before_capture or geometry_changed_during_capture:
            if not pixel_stability_required:
                BUS.publish(
                    "visual_preflight",
                    run_id=self.run_id,
                    ok=True,
                    changed=False,
                    geometry_changed=True,
                    pixel_change_ignored=True,
                    pixel_stability_evaluated=False,
                    frame_identity_validated=True,
                    foreground_validation_owner="controller_scope_guard",
                    capture_region=list(capture_region_tuple or ()),
                    post_capture_region=list(after_region_tuple or ()),
                    capture_scope=capture_scope,
                    change_ratio=None,
                    decision_scope="identity_only",
                    capture_latency_ms=latency_ms,
                )
                # Non-spatial input has no coordinate mapping to corrupt. The
                # Controller will prove that this same target owns foreground
                # immediately before dispatch.
                return "", None, "", region
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
        if not pixel_stability_required:
            BUS.publish(
                "visual_preflight",
                run_id=self.run_id,
                ok=True,
                changed=False,
                change_ratio=round(float(global_ratio), 6),
                global_change_ratio=round(float(global_ratio), 6),
                target_change_ratio=None,
                target_roi=None,
                pixel_change_ignored=True,
                pixel_stability_evaluated=False,
                frame_identity_validated=True,
                foreground_validation_owner="controller_scope_guard",
                decision_scope="identity_only",
                capture_scope=capture_scope,
                capture_scope_reason=capture_scope_reason,
                capture_region=list(capture_region_tuple or ()),
                threshold=None,
                capture_latency_ms=latency_ms,
            )
            return "", float(global_ratio), "", region
        target_roi = self._visual_preflight_target_roi(tool_name, action_args)
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
        authoritative = self._frame_authority.current
        current_frame_id = (
            authoritative.frame_id
            if authoritative is not None
            else int(self._visual_frame_id or 0)
        )
        model_acknowledged_refresh = bool(
            changed
            and current_frame_id
            and current_frame_id == self._visual_preflight_refresh_frame_id
        )
        if model_acknowledged_refresh:
            # The preceding rejection already attached a fresh authoritative
            # frame. This action is the model's decision from those pixels, so
            # continuous motion must not reject it forever. This is a one-shot
            # exemption from pixel stability only; identity and geometry have
            # already passed above and Controller still owns foreground scope.
            changed = False
            self._visual_preflight_refresh_frame_id = 0
            decision_scope = "model_acknowledged_dynamic_pixels"
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
            pixel_change_ignored=bool(model_acknowledged_refresh),
            pixel_change_observed=bool(decision_ratio > threshold),
            pixel_stability_evaluated=True,
            model_acknowledged_refresh=bool(model_acknowledged_refresh),
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
        """Map submitted-image or legacy frame coordinates to physical pixels.

        Native multimodal models naturally ground within the submitted image.
        Asking them to also infer JPEG resize and DPI/window offsets caused
        repeatable misses.  The model schema therefore exposes only exact
        ``image_*`` pixels.  Legacy ``frame_*`` values remain a non-model
        compatibility input; physical ``x/y`` are controller-owned.
        """
        def contains_frame_coordinate(value: Any) -> bool:
            if isinstance(value, dict):
                return any(
                    str(key).startswith(("frame_", "image_"))
                    or contains_frame_coordinate(child)
                    for key, child in value.items()
                )
            if isinstance(value, list):
                return any(contains_frame_coordinate(child) for child in value)
            return False

        def frame_dependent_batch_action(action: Any) -> tuple[str, bool]:
            """Return whether a child consumes coordinates from this exact frame.

            Semantic labels/anchors are intentionally not frame-dependent: the
            local Controller re-observes and rebinds them after an earlier
            mutation.  Raw frame coordinates and element ids cannot be safely
            rebound and therefore keep the one-per-authoritative-frame limit.
            """
            parsed = parse_atomic_action(action)
            if parsed is None:
                return "", False
            name, values = parsed.name, parsed.inputs
            frame_keys = {
                "frame_x", "frame_y", "frame_x1", "frame_y1",
                "frame_x2", "frame_y2", "frame_points", "frame_strokes",
                "image_x", "image_y", "image_x1", "image_y1",
                "image_x2", "image_y2", "image_points", "image_strokes",
                "constraint_image_region", "image_region", "effect_image_region",
            }
            physical_keys = {"x", "y", "x1", "y1", "x2", "y2", "points", "strokes"}
            depends = bool(
                values.get("element_id") is not None
                or frame_keys.intersection(values)
                or (
                    name in {
                        "click", "double_click", "scroll", "move_mouse", "mouse_down", "mouse_up",
                        "drag", "draw_strokes",
                    }
                    and physical_keys.intersection(values)
                )
            )
            return name, depends

        if tool_name == "run_action_batch":
            # Validate before frame lookup, preflight, coordinate mapping, or
            # dispatch. Only raw frame/physical coordinates and element ids
            # consume the submitted pixels. Semantic labels/anchors are
            # re-observed and rebound by the local Controller between child
            # mutations, so deterministic setup can stay in one transaction.
            actions = args.get("actions")
            if isinstance(actions, list):
                spatial = [
                    (index, name)
                    for index, action in enumerate(actions)
                    if (parts := frame_dependent_batch_action(action))[1]
                    and (name := parts[0]) in _NATIVE_BATCH_SPATIAL_MUTATIONS
                ]
                spatial_names = [name for _index, name in spatial]
                down_positions = [
                    position for position, name in enumerate(spatial_names)
                    if name == "mouse_down"
                ]
                up_positions = [
                    position for position, name in enumerate(spatial_names)
                    if name == "mouse_up"
                ]

                def pointer_button(action_index: int) -> str:
                    action = actions[action_index]
                    if not isinstance(action, dict):
                        return "left"
                    params = action.get("params") if isinstance(action.get("params"), dict) else {}
                    return str(action.get("button") or params.get("button") or "left").casefold()

                balanced_pointer_transaction = bool(
                    len(down_positions) == 1
                    and len(up_positions) == 1
                    and down_positions[0] < up_positions[0]
                    and all(
                        name == "move_mouse"
                        for position, name in enumerate(spatial_names)
                        if position not in {down_positions[0], up_positions[0]}
                    )
                    and not any(
                        name == "move_mouse"
                        for name in spatial_names[up_positions[0] + 1:]
                    )
                    and pointer_button(spatial[down_positions[0]][0])
                    == pointer_button(spatial[up_positions[0]][0])
                )
                if len(spatial) > 1 and not balanced_pointer_transaction:
                    locations = ", ".join(
                        f"actions[{index}]={name}" for index, name in spatial
                    )
                    raise ValueError(
                        "native run_action_batch allows at most one spatial UI mutation "
                        "that depends on raw frame/physical coordinates or element_id "
                        f"per authoritative frame ({locations}). No batch action was executed. "
                        "Keep Controller-rebound label/anchor setup, keyboard input, and ROI waits "
                        "with the gesture; put every later raw coordinate/element-id gesture in a "
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
        frame_mapper = NormalizedFrameCoordinateMapper((left, top, width, height))
        mapped = dict(args)
        if (
            tool_name in {"move_mouse", "mouse_up"}
            and "constraint_frame_region" not in mapped
            and "constraint_region" not in mapped
            and "_constraint_region" not in mapped
        ):
            pointer_status_fn = getattr(self.session, "_pointer_hold_status", None)
            pointer_status = (
                pointer_status_fn()
                if callable(pointer_status_fn)
                else {"active": False}
            )
            inherited_constraint = pointer_status.get("constraint_frame_region")
            if pointer_status.get("active") and isinstance(
                inherited_constraint, dict
            ):
                mapped["constraint_frame_region"] = dict(inherited_constraint)
        image_source: dict[str, Any] = {}
        image_mapper = SubmittedImageCoordinateMapper(self._visual_submitted_size)

        def convert_image_pair(
            image_x_key: str,
            image_y_key: str,
            frame_x_key: str,
            frame_y_key: str,
        ) -> bool:
            nonlocal mapped
            mapped, point_source = image_mapper.map_point_pair(
                mapped,
                image_keys=(image_x_key, image_y_key),
                frame_keys=(frame_x_key, frame_y_key),
                context=tool_name,
            )
            image_source.update(point_source)
            return bool(point_source)

        if tool_name in {"click", "double_click", "scroll", "move_mouse", "mouse_down", "mouse_up"}:
            convert_image_pair("image_x", "image_y", "frame_x", "frame_y")
        elif tool_name == "drag":
            mapped, drag_source = image_mapper.map_drag_image_coordinates(
                mapped, context=tool_name, max_points=_NATIVE_DRAG_MAX_POINTS,
            )
            image_source.update(drag_source)
        elif tool_name == "draw_strokes":
            mapped, stroke_source = image_mapper.map_image_strokes(
                mapped, context=tool_name, max_strokes=16, max_points_per_stroke=16,
            )
            image_source.update(stroke_source)

        if "constraint_image_region" in mapped:
            mapped, constraint_source = image_mapper.map_constraint_image_region(
                mapped,
                context=tool_name,
            )
            image_source["constraint_image_region"] = constraint_source
        constraint_frame_region: dict[str, float] | None = None
        if "constraint_frame_region" in mapped:
            if "constraint_region" in mapped or "_constraint_region" in mapped:
                raise ValueError(
                    f"{tool_name} received both frame and physical action constraints"
                )
            raw_constraint = mapped.pop("constraint_frame_region")
            try:
                if isinstance(raw_constraint, (list, tuple)) and len(raw_constraint) == 4:
                    constraint_x = float(raw_constraint[0])
                    constraint_y = float(raw_constraint[1])
                    constraint_right = float(raw_constraint[2])
                    constraint_bottom = float(raw_constraint[3])
                    constraint_width = constraint_right - constraint_x
                    constraint_height = constraint_bottom - constraint_y
                elif isinstance(raw_constraint, dict):
                    constraint_x = float(raw_constraint["x"])
                    constraint_y = float(raw_constraint["y"])
                    constraint_width = float(raw_constraint["width"])
                    constraint_height = float(raw_constraint["height"])
                else:
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                raise ValueError(
                    "constraint_frame_region requires [left,top,right,bottom] "
                    "numeric bounds"
                ) from None
            if (
                constraint_x < 0
                or constraint_y < 0
                or constraint_width <= 0
                or constraint_height <= 0
                or constraint_x + constraint_width > 1000
                or constraint_y + constraint_height > 1000
            ):
                raise ValueError(
                    "constraint_frame_region must be a positive rectangle fully inside "
                    "the current whole-frame 0..1000 bounds"
                )
            constraint_frame_region = {
                "x": constraint_x,
                "y": constraint_y,
                "width": constraint_width,
                "height": constraint_height,
            }
            physical_left = left + int(round(
                constraint_x * max(1, width - 1) / 1000.0
            ))
            physical_top = top + int(round(
                constraint_y * max(1, height - 1) / 1000.0
            ))
            physical_right = left + int(round(
                (constraint_x + constraint_width) * max(1, width - 1) / 1000.0
            ))
            physical_bottom = top + int(round(
                (constraint_y + constraint_height) * max(1, height - 1) / 1000.0
            ))
            mapped["_constraint_region"] = {
                "x": physical_left,
                "y": physical_top,
                "width": max(1, physical_right - physical_left),
                "height": max(1, physical_bottom - physical_top),
            }

        def require_points_inside_constraint(
            points: list[tuple[float, float]],
            *,
            source_name: str,
        ) -> None:
            if constraint_frame_region is None:
                return
            boundary_left = constraint_frame_region["x"]
            boundary_top = constraint_frame_region["y"]
            boundary_right = boundary_left + constraint_frame_region["width"]
            boundary_bottom = boundary_top + constraint_frame_region["height"]
            for point_index, (point_x, point_y) in enumerate(points):
                if not (
                    boundary_left <= float(point_x) <= boundary_right
                    and boundary_top <= float(point_y) <= boundary_bottom
                ):
                    raise NativeFrameCoordinateError(
                        (
                            f"{source_name}[{point_index}] leaves the declared "
                            "constraint_frame_region; the action was not executed"
                        ),
                        action_constraint_failed=True,
                        constraint_frame_region=dict(constraint_frame_region),
                        rejected_point=[float(point_x), float(point_y)],
                        rejected_point_index=point_index,
                    )

        if tool_name == "run_action_batch":
            actions = mapped.get("actions")
            if not isinstance(actions, list):
                return mapped
            mapped_actions: list[Any] = []
            coordinate_actions = {
                "click", "double_click", "scroll", "drag", "draw_strokes", "move_mouse",
                "mouse_down", "mouse_up",
            }

            def map_frame_region(value: Any, field: str) -> dict[str, int]:
                if not isinstance(value, dict):
                    raise ValueError(f"{field} must be an object with x/y/width/height")
                try:
                    nx = float(value["x"])
                    ny = float(value["y"])
                    nw = float(value["width"])
                    nh = float(value["height"])
                except (KeyError, TypeError, ValueError):
                    raise ValueError(f"{field} requires numeric x/y/width/height") from None
                if (
                    nx < 0 or ny < 0 or nw <= 0 or nh <= 0
                    or nx + nw > 1000 or ny + nh > 1000
                ):
                    raise ValueError(
                        f"{field} must be a positive rectangle fully inside whole-frame 0..1000 bounds"
                    )
                physical_left = left + int(round(nx * max(1, width - 1) / 1000.0))
                physical_top = top + int(round(ny * max(1, height - 1) / 1000.0))
                physical_right = left + int(round((nx + nw) * max(1, width - 1) / 1000.0))
                physical_bottom = top + int(round((ny + nh) * max(1, height - 1) / 1000.0))
                return {
                    "x": physical_left,
                    "y": physical_top,
                    "width": max(1, physical_right - physical_left),
                    "height": max(1, physical_bottom - physical_top),
                }

            def map_image_region(value: Any, field: str) -> dict[str, int]:
                if not isinstance(value, dict):
                    raise ValueError(f"{field} must be an object with x/y/width/height")
                submitted = self._visual_submitted_size
                if not submitted or submitted[0] <= 0 or submitted[1] <= 0:
                    raise ValueError(
                        f"{field} requires the exact current submitted-image dimensions"
                    )
                try:
                    image_x = float(value["x"])
                    image_y = float(value["y"])
                    image_region_width = float(value["width"])
                    image_region_height = float(value["height"])
                except (KeyError, TypeError, ValueError):
                    raise ValueError(f"{field} requires numeric x/y/width/height") from None
                image_width, image_height = submitted
                if (
                    image_x < 0
                    or image_y < 0
                    or image_region_width <= 0
                    or image_region_height <= 0
                    or image_x + image_region_width > image_width
                    or image_y + image_region_height > image_height
                ):
                    raise ValueError(
                        f"{field} must be a positive rectangle inside the exact submitted "
                        f"image size {image_width}x{image_height}"
                    )
                physical_left = left + int(round(image_x * width / image_width))
                physical_top = top + int(round(image_y * height / image_height))
                physical_right = left + int(round(
                    (image_x + image_region_width) * width / image_width
                ))
                physical_bottom = top + int(round(
                    (image_y + image_region_height) * height / image_height
                ))
                return {
                    "x": physical_left,
                    "y": physical_top,
                    "width": max(1, physical_right - physical_left),
                    "height": max(1, physical_bottom - physical_top),
                }

            local_mutation_seen = False
            for index, action in enumerate(actions):
                parsed = parse_atomic_action(action)
                if parsed is None:
                    mapped_actions.append(action)
                    continue
                name, action_args = parsed.name, dict(parsed.inputs)
                if local_mutation_seen and name in {"click_element", "type_into_element"}:
                    action_args["_rebind_after_mutation"] = True
                try:
                    if "image_region" in action_args:
                        if "frame_region" in action_args or "region" in action_args:
                            raise ValueError(
                                "image_region cannot be mixed with frame/physical region"
                            )
                        action_args["region"] = map_image_region(
                            action_args.pop("image_region"), "image_region",
                        )
                    if "frame_region" in action_args:
                        action_args["region"] = map_frame_region(
                            action_args.pop("frame_region"), "frame_region",
                        )
                    if "effect_image_region" in action_args:
                        if (
                            "effect_frame_region" in action_args
                            or "effect_region" in action_args
                        ):
                            raise ValueError(
                                "effect_image_region cannot be mixed with frame/physical effect region"
                            )
                        action_args["effect_region"] = map_image_region(
                            action_args.pop("effect_image_region"),
                            "effect_image_region",
                        )
                    if "effect_frame_region" in action_args:
                        action_args["effect_region"] = map_frame_region(
                            action_args.pop("effect_frame_region"), "effect_frame_region",
                        )
                except ValueError as exc:
                    raise NativeFrameCoordinateError(
                        f"run_action_batch.actions[{index}]: {exc}",
                    ) from None
                if name not in coordinate_actions:
                    mapped_actions.append({"action": name, **action_args})
                    if name in _NATIVE_BATCH_SPATIAL_MUTATIONS:
                        local_mutation_seen = True
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
                if name in _NATIVE_BATCH_SPATIAL_MUTATIONS:
                    local_mutation_seen = True
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
            return frame_mapper.map_point(nx, ny)

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
                    "use image_* pixels from the exact current authoritative full image"
                )

        used = False
        source: dict[str, Any] = {}
        if tool_name in {"click", "double_click", "scroll", "move_mouse", "mouse_down", "mouse_up"}:
            has_frame_point = "frame_x" in mapped or "frame_y" in mapped
            if has_frame_point and any(key in mapped for key in ("x", "y")):
                raise ValueError(
                    f"{tool_name} received mixed frame and physical coordinate sources"
                )
            if tool_name in {"click", "double_click"} and has_frame_point and mapped.get("element_id") is not None:
                raise ValueError(
                    f"{tool_name} received both element_id and frame coordinates; "
                    "choose one grounded target"
                )
            mapped_point = point("frame_x", "frame_y")
            if mapped_point is not None:
                mapped["x"], mapped["y"] = mapped_point
                source = {"frame_x": mapped.pop("frame_x"), "frame_y": mapped.pop("frame_y")}
                require_points_inside_constraint(
                    [(float(source["frame_x"]), float(source["frame_y"]))],
                    source_name="click_point",
                )
                used = True
            else:
                # This method only processes model-authored tool arguments.
                # Semantic hints do not make raw x/y safe: direct click gives
                # physical coordinates precedence and would silently ignore a
                # label. Controller-generated physical coordinates are added
                # later, after this boundary, so no model x/y needs a bypass.
                require_normalized_instead_of_physical(("x", "y"))
                pointer_status_fn = getattr(self.session, "_pointer_hold_status", None)
                pointer_status = (
                    pointer_status_fn()
                    if callable(pointer_status_fn)
                    else {"active": False}
                )
                if tool_name == "mouse_up" and pointer_status.get("active"):
                    pass
                elif tool_name not in {"click", "double_click"} or not (
                    mapped.get("element_id") is not None
                    or any(mapped.get(key) for key in (
                        "label", "fallback_text", "role", "expected_region", "visual_anchor",
                    ))
                ):
                    raise ValueError(
                        f"{tool_name} requires image_x/image_y pixels from the exact current image"
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
                    compact_points: list[Any] = []
                    for raw_point in raw_points:
                        if not compact_points or raw_point != compact_points[-1]:
                            compact_points.append(raw_point)
                    if len(compact_points) > _NATIVE_DRAG_MAX_POINTS:
                        raise ValueError(
                            f"frame_points accepts at most {_NATIVE_DRAG_MAX_POINTS} meaningful points"
                        )
                    physical_points: list[list[int]] = []
                    for index, raw_point in enumerate(compact_points):
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
                    source["frame_points"] = compact_points
                constraint_points = [
                    (float(source["frame_x1"]), float(source["frame_y1"])),
                    *[
                        (float(raw_point[0]), float(raw_point[1]))
                        for raw_point in (source.get("frame_points") or [])
                    ],
                    (float(source["frame_x2"]), float(source["frame_y2"])),
                ]
                require_points_inside_constraint(
                    constraint_points,
                    source_name="drag_path",
                )
                used = True
            else:
                require_normalized_instead_of_physical(("x1", "y1", "x2", "y2", "points"))
                raise ValueError(
                    "drag requires image_x1/image_y1 and image_x2/image_y2 pixels "
                    "from the exact current image"
                )
        elif tool_name == "draw_strokes":
            if "normalized_strokes" in mapped:
                raise ValueError(
                    "native visual draw_strokes rejected normalized_strokes; use image_strokes "
                    "pixels from the exact current authoritative image"
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
                require_points_inside_constraint(
                    [point for stroke in normalized_strokes for point in stroke],
                    source_name="frame_strokes",
                )
                input_point_count = sum(len(stroke) for stroke in normalized_strokes)
                frame_preparation.update({
                    "input_point_count": input_point_count,
                    "execution_point_count": input_point_count,
                    "points_simplified": 0,
                    "simplification": "disabled_model_authored_path_preserved",
                })
                physical_strokes: list[list[list[int]]] = []
                for normalized_stroke in normalized_strokes:
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
                raise ValueError(
                    "draw_strokes requires image_strokes pixels from the exact current image"
                )
        if used:
            if constraint_frame_region is not None:
                source["constraint_frame_region"] = dict(
                    constraint_frame_region
                )
            mapped["coordinate_space"] = "current_frame_normalized_0_1000"
            mapped["visual_frame_id"] = self._visual_frame_id
            mapped["source_frame_coordinates"] = source
            if image_source:
                mapped["source_image_coordinates"] = {
                    **image_source,
                    "submitted_image_size": list(self._visual_submitted_size or ()),
                }
        return mapped

    # ----------------------------------------------------------------- api
    def _accumulate_model_elapsed(self, started: float) -> int:
        """Account one model-backed wait exactly once and return its duration."""
        elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
        self.model_elapsed_ms += elapsed_ms
        return elapsed_ms

    def _native_visual_thinking_policy(
        self,
        *,
        model_profile: Any,
        configured_thinking_enabled: bool,
        gui_tool_recovery_request: bool,
    ) -> tuple[str | None, str]:
        """Choose provider reasoning from live execution evidence.

        This is intentionally independent of task words, application names,
        or a precompiled workflow.  A first/uncertain/recovery decision gets
        room to reason; a turn immediately following a visibly verified action
        can use the provider's cheap path.  Explicit ``api.thinking=true`` is
        always respected because it is an operator choice, not a hidden task
        heuristic.
        """
        if gui_tool_recovery_request:
            return "disabled", "structured_tool_recovery"
        if configured_thinking_enabled:
            return None, "provider_configured"
        if not self._native_visual_context_enabled():
            return None, "non_visual"
        controller = CONFIG.get("controller", default={}) or {}
        if not bool(controller.get("native_visual_thinking", True)):
            return None, "disabled_by_config"
        mode = str(controller.get("native_visual_thinking_mode", "adaptive") or "adaptive").strip().casefold()
        if mode in {"off", "disabled", "false", "0"}:
            return None, "disabled_by_config"
        if mode in {"always", "on", "enabled", "true", "1"}:
            return "enabled", "always_by_config"
        # Models which already reason by default do not need an extra request
        # override; this keeps the policy provider-neutral.
        if not bool(getattr(model_profile, "disables_thinking_by_default", False)):
            return None, "provider_default"

        recent_tool = ""
        recent_data: dict[str, Any] = {}
        if self._tool_history:
            recent_tool = str(self._tool_history[-1][0] or "")
            try:
                parsed = json.loads(self._tool_history[-1][2] or "{}")
                if isinstance(parsed, dict):
                    recent_data = parsed
            except (TypeError, ValueError, json.JSONDecodeError):
                recent_data = {}
        if self._decision_step <= 1 or not self._visual_frame_id:
            return "enabled", "initial_grounding"
        if (
            self._done_rejects
            or self._last_done_reject_reason
            or self._consecutive_unverified >= 2
            or self._last_verify_ok is False
            or recent_data.get("blocked") is True
            or recent_data.get("needs_replan") is True
            or recent_data.get("no_visual_progress") is True
            or recent_data.get("failure_classification")
        ):
            return "enabled", "recovery_or_uncertainty"
        if (
            self._last_visual_change_comparable
            and self._last_visual_change_ratio is not None
            and float(self._last_visual_change_ratio) <= self._native_no_progress_threshold()
        ):
            return "enabled", "unchanged_pixels"
        # A verified mutation plus a changed same-region frame is the only
        # condition that earns the lightweight path.  Observations alone do
        # not count as progress: the model still has to understand the scene.
        if (
            self._last_verify_ok is True
            and recent_tool in _LOW_LEVEL_VISUAL_TOOLS
            and self._last_visual_change_comparable
            and self._last_visual_change_ratio is not None
            and float(self._last_visual_change_ratio) > self._native_no_progress_threshold()
        ):
            return "disabled", "verified_routine"
        return "enabled", "default_deep"

    def _chat(self, client, tools: list[dict]):
        """Time one complete model decision, including retries and Stop."""
        self._roll_native_visual_context()
        self.model_request_count += 1
        decision_started = time.monotonic()
        try:
            return self._chat_request(
                client,
                tools,
                decision_started=decision_started,
            )
        finally:
            # The wrapper owns the timer so every return/raise path is covered
            # and the worker's eventual completion after cancellation cannot
            # charge the same request a second time.
            self._accumulate_model_elapsed(decision_started)

    def _model_role(self) -> str:
        """Stable role label for telemetry ownership, not model routing."""
        return "legacy_coding_agent" if self._is_coding else "legacy_visual_agent"

    def _chat_request(
        self,
        client,
        tools: list[dict],
        *,
        decision_started: float,
    ):
        """Run one completion with Stop and total-deadline cancellation.

        A late provider response is abandoned before it can be converted into
        a desktop action, which keeps retries safe for GUI tasks.
        """
        api = CONFIG.get("api", default={})
        model = api.get("model", "deepseek-v4-flash")
        configured_max_tokens = int(api.get("max_tokens", 4096))
        try:
            request_timeout_s = max(5.0, min(180.0, float(
                api.get("request_timeout_s", 75) or 75,
            )))
        except (TypeError, ValueError):
            request_timeout_s = 75.0
        try:
            total_timeout_s = max(10.0, min(300.0, float(
                api.get("request_total_timeout_s", 120) or 120,
            )))
        except (TypeError, ValueError):
            total_timeout_s = 120.0
        try:
            configured_retries = int(api.get("max_retries", _API_RETRIES))
        except (TypeError, ValueError):
            configured_retries = _API_RETRIES
        max_attempts = max(1, min(_API_RETRIES, configured_retries + 1))
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
                escalation_profile = model_profile_from_config({
                    **api,
                    "model": esc,
                })
                if (
                    self._native_visual_context_enabled()
                    and not escalation_profile.supports_vision
                ):
                    logger.warning(
                        "跳过不支持视觉的升级模型 %s；当前决策包含权威 GUI 图像",
                        esc,
                    )
                else:
                    logger.info("本步升级到更强模型: %s", esc)
                    model = esc
            else:
                logger.warning("卡住需升级模型，但 agent.escalation_model 未配置，仍用 %s", model)

        configured_thinking = api.get("thinking")
        configured_thinking_enabled = bool(
            configured_thinking is True
            or (
                isinstance(configured_thinking, dict)
                and str(configured_thinking.get("type") or "").casefold() == "enabled"
            )
        )
        thinking_mode, thinking_policy = self._native_visual_thinking_policy(
            model_profile=model_profile_from_config({**api, "model": model}),
            configured_thinking_enabled=configured_thinking_enabled,
            gui_tool_recovery_request=gui_tool_recovery_request,
        )
        thinking_enabled = bool(
            thinking_mode == "enabled"
            or (thinking_mode is None and configured_thinking_enabled)
        )
        # P0 observation only: measure textual/structural context without
        # serializing image-bearing ``messages`` on the model hot path.  These
        # values never feed back into prompt construction, model selection, or
        # action dispatch.
        prompt_context_chars = _prompt_context_chars(self.messages)
        model_tool_schema_chars = len(json.dumps(
            tools, ensure_ascii=False, separators=(",", ":"), default=str,
        ))
        model_tool_count = len(tools)
        model_role = self._model_role()
        decision_step = int(self._decision_step or 0)
        box: dict[str, Any] = {}
        self._last_llm_model = model
        self._last_llm_thinking_enabled = thinking_enabled
        self._last_thinking_policy = thinking_policy
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
            "thinking_policy": thinking_policy,
            "request_timeout_s": request_timeout_s,
            "request_total_timeout_s": total_timeout_s,
            "max_attempts": max_attempts,
            "gui_tool_call_recovery": gui_tool_recovery_request,
            "messages": self.messages,
            "tools": tools,
        })

        cancel_event = threading.Event()

        request_params: dict[str, Any] = {
            "model": model,
            "messages": self.messages,
            "tools": tools,
        }
        if gui_tool_recovery_request and tools:
            request_params["tool_choice"] = "required"
        if thinking_enabled:
            request_params["max_completion_tokens"] = request_max_tokens
        else:
            request_params["temperature"] = float(api.get("temperature", 0.3))
            request_params["max_tokens"] = request_max_tokens
        if thinking_mode is not None:
            request_params["extra_body"] = {
                "thinking": {"type": thinking_mode},
            }

        prepare_request = getattr(client, "prepare_chat_completion", None)
        send_prepared = getattr(client, "send_prepared_chat_completion", None)
        if callable(prepare_request) and callable(send_prepared):
            provider_request = prepare_request(**request_params)
            send_once = send_prepared
        else:
            provider_request = request_params

            def send_once(params: dict[str, Any]):
                return client.chat.completions.create(**params)

        request_envelope = ModelRequestEnvelope.bind(
            decision_id=f"{self.run_id}:{decision_step}:{self.model_request_count}",
            model_role=model_role,
            purpose="legacy_agent_decision",
            provider_request=provider_request,
        )
        retry_policy = ModelRetryPolicy(
            max_attempts=max_attempts,
            total_timeout_s=total_timeout_s,
            initial_backoff_s=1.5,
            backoff_multiplier=2.0,
            max_backoff_s=20.0,
        )

        def record_attempt(attempt) -> None:
            self._trace_debug("llm_request_attempt", {
                "run_id": self.run_id,
                "decision_id": request_envelope.decision_id,
                "request_fingerprint": request_envelope.request_fingerprint,
                "attempt": attempt.attempt,
                "retry_kind": attempt.kind.value,
                "ok": attempt.ok,
                "error_kind": attempt.error_kind,
                "retryable": attempt.retryable,
                "elapsed_ms": attempt.elapsed_ms,
            })

        def worker() -> None:
            try:
                outcome = ModelCallRuntime().execute(
                    request_envelope,
                    send_once,
                    policy=retry_policy,
                    stop_event=cancel_event,
                    on_attempt=record_attempt,
                )
                box["resp"] = outcome.response
                box["model_attempts"] = outcome.attempts
                box["request_retry_count"] = outcome.request_retry_count
            except ModelCallError as exc:
                box["exc"] = exc
                box["model_attempts"] = exc.attempts
                box["request_retry_count"] = exc.request_retry_count

        thread = threading.Thread(target=worker, daemon=True, name="llm-call")
        thread.start()
        request_deadline = time.monotonic() + total_timeout_s
        while thread.is_alive():
            if self.stop_event.is_set():
                cancel_event.set()
                logger.info("Stop 请求：放弃进行中的 LLM 调用")
                self._trace_debug("llm_abandoned", {
                    "run_id": self.run_id,
                    "model": model,
                    "reason": "stop_event",
                })
                BUS.publish(
                    "agent_decision",
                    run_id=self.run_id,
                    step=decision_step,
                    model_role=model_role,
                    model=model,
                    ok=False,
                    canceled=True,
                    elapsed_ms=int((time.monotonic() - decision_started) * 1000),
                    runtime_version="legacy",
                    model_tool_count=model_tool_count,
                    model_tool_schema_chars=model_tool_schema_chars,
                    prompt_context_chars=prompt_context_chars,
                    mission_card_chars=None,
                    prompt_context_measurement="text_structure_without_binary",
                    thinking_policy=thinking_policy,
                    request_retry_count=int(box.get("request_retry_count") or 0),
                    request_fingerprint=request_envelope.request_fingerprint,
                )
                return None  # abandon the in-flight request (it finishes unused)
            if time.monotonic() >= request_deadline:
                cancel_event.set()
                self._model_timeout_count += 1
                message = (
                    f"model request exceeded total timeout ({total_timeout_s:g}s); "
                    "the in-flight response was abandoned before any tool call"
                )
                logger.warning(message)
                self._trace_debug("llm_timeout", {
                    "run_id": self.run_id,
                    "model": model,
                    "request_timeout_s": request_timeout_s,
                    "request_total_timeout_s": total_timeout_s,
                    "attempts": max_attempts,
                    "thinking_policy": thinking_policy,
                })
                BUS.publish(
                    "agent_decision",
                    run_id=self.run_id,
                    step=decision_step,
                    model_role=model_role,
                    model=model,
                    ok=False,
                    timeout=True,
                    elapsed_ms=int((time.monotonic() - decision_started) * 1000),
                    runtime_version="legacy",
                    model_tool_count=model_tool_count,
                    model_tool_schema_chars=model_tool_schema_chars,
                    prompt_context_chars=prompt_context_chars,
                    mission_card_chars=None,
                    prompt_context_measurement="text_structure_without_binary",
                    thinking_policy=thinking_policy,
                    request_retry_count=int(box.get("request_retry_count") or 0),
                    request_fingerprint=request_envelope.request_fingerprint,
                )
                raise TimeoutError(message)
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
                step=decision_step,
                model_role=model_role,
                model=model,
                ok=False,
                elapsed_ms=int((time.monotonic() - decision_started) * 1000),
                runtime_version="legacy",
                model_tool_count=model_tool_count,
                model_tool_schema_chars=model_tool_schema_chars,
                prompt_context_chars=prompt_context_chars,
                mission_card_chars=None,
                prompt_context_measurement="text_structure_without_binary",
                thinking_policy=thinking_policy,
                request_retry_count=int(box.get("request_retry_count") or 0),
                request_fingerprint=request_envelope.request_fingerprint,
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
            step=decision_step,
            model_role=model_role,
            model=model,
            ok=box.get("resp") is not None,
            elapsed_ms=int((time.monotonic() - decision_started) * 1000),
            runtime_version="legacy",
            model_tool_count=model_tool_count,
            model_tool_schema_chars=model_tool_schema_chars,
            prompt_context_chars=prompt_context_chars,
            mission_card_chars=None,
            prompt_context_measurement="text_structure_without_binary",
            thinking_policy=thinking_policy,
            request_retry_count=int(box.get("request_retry_count") or 0),
            request_fingerprint=request_envelope.request_fingerprint,
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
                    "The previous response was truncated before a valid action. "
                    "Issue one available function call for the current frame."
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

    @staticmethod
    def _tool_calls_have_complete_arguments(tool_calls) -> bool:
        """Whether every emitted function call has one complete JSON object."""

        if not tool_calls:
            return False
        for tool_call in tool_calls:
            try:
                arguments = json.loads(tool_call.function.arguments or "{}")
            except (json.JSONDecodeError, TypeError, AttributeError):
                return False
            if not isinstance(arguments, dict):
                return False
        return True

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
            for action in iter_atomic_actions("run_action_batch", args):
                values.extend(AgentRunner._expect_visible_values(action.inputs))
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
                "fast_path_advice", "metrics", "failure_type",
                "action_executed",
                "native_point_scope_guard", "scope_reason",
                "scene_coverage_guard", "current_scene_id",
                "current_scene_visit_count", "proposed_edge",
                "prior_target_scene_ids", "cycle_scene_path",
                "artifact_id", "requested_artifact_id", "artifact_resolution",
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
        # Every model-authored GUI mutation enters through the same Controller
        # transaction, including actions that carry an explicit expectation.
        # ToolSession remains the raw device adapter used *by* Controller; it
        # must not be a second model-facing action path.
        return tool_name in {
            "click",
            "double_click",
            "click_element",
            "type_text",
            "type_into_element",
            "hotkey",
            "scroll",
            "move_mouse",
            "mouse_down",
            "mouse_up",
            "hold_key",
            "focus_window",
            "clipboard_paste",
            "draw_strokes",
            "drag",
        }

    @staticmethod
    def _normalise_fast_wrapped_action(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        payload = dict(args)
        if tool_name == "double_click":
            payload["action"] = "click"
            payload["double"] = True
        else:
            payload["action"] = tool_name
        return payload

    @staticmethod
    def _fast_wrapped_result(tool_name: str, args: dict[str, Any], batch_result: ToolResult) -> ToolResult:
        try:
            data = json.loads(batch_result.text or "{}")
        except (json.JSONDecodeError, TypeError):
            data = {
                "ok": False,
                "verified": False,
                "blocked": True,
                "failure_type": "INVALID_CONTROLLER_RESULT",
                "error": str(batch_result.text or "Controller returned an empty result"),
            }
        if not isinstance(data, dict):
            data = {}

        children = [item for item in (data.get("results") or []) if isinstance(item, dict)]
        action_aliases = {tool_name}
        if tool_name == "click":
            action_aliases.add("click_element")
        elif tool_name == "double_click":
            action_aliases.add("click")
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
        for key in (
            "error",
            "blocked",
            "failure_type",
            "needs_llm_reason",
            "missing_live_target",
            "scope_guard",
            "input_cleanup",
            "validation_errors",
            "controller_state",
            "native_action_executed",
        ):
            if key in data:
                wrapped[key] = data.get(key)
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
                "semantic_hit_snap",
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
        batch_args = {
            "actions": [self._normalise_fast_wrapped_action(tool_name, args)],
        }
        if tool_name in {"draw_strokes", "drag"}:
            batch_args["sync"] = False
        batch_result = self.session.dispatch("run_action_batch", batch_args)
        return self._fast_wrapped_result(tool_name, args, batch_result)





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

    def _record_visual_effect_checkpoint(
        self,
        *,
        phase: str,
        action: str,
        action_executed: bool,
        image_b64: str,
        hwnd: int,
        frame_id: int,
        screen_epoch: int,
        change_comparable: bool,
        change_ratio: float | None,
    ) -> None:
        """Retain bounded real pixels for transient/ordered GUI effects."""

        action_name = str(action or "").strip()
        if (
            phase != "post_action"
            or not action_executed
            or not image_b64
            or action_name not in _LOW_LEVEL_VISUAL_TOOLS
        ):
            return
        if (
            change_comparable
            and change_ratio is not None
            and float(change_ratio) <= self._native_no_progress_threshold()
        ):
            return
        snapshot = getattr(self.session, "snapshot", None)
        record = {
            "image_b64": image_b64,
            "hwnd": int(hwnd or 0),
            "title": str(getattr(snapshot, "title", "") or ""),
            "action": action_name,
            "frame_id": int(frame_id or 0),
            "screen_epoch": int(screen_epoch or 0),
        }
        checkpoints = [
            item for item in self._visual_effect_checkpoints
            if not (
                int(item.get("hwnd", 0) or 0) == record["hwnd"]
                and str(item.get("action") or "") == action_name
            )
        ]
        checkpoints.append(record)
        self._visual_effect_checkpoints = checkpoints[-8:]
        BUS.publish(
            "visual_effect_checkpoint",
            run_id=self.run_id,
            hwnd=record["hwnd"],
            action=action_name,
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

    def _wait_if_paused(self) -> bool:
        controller = self.pause_controller
        waiter = getattr(controller, "wait_if_paused", None)
        if callable(waiter):
            return bool(waiter(self.stop_event))
        return not self.stop_event.is_set()

    def _handle_tool_calls(self, tool_calls) -> str | None:
        """Dispatch each tool call; returns terminal status if any."""
        post_tool_hints: list[str] = []
        visual_transaction_seen = False
        visual_feedback_action = ""
        visual_feedback_args: dict[str, Any] = {}
        visual_feedback_action_executed = True
        visual_feedback_result = ""
        visual_feedback_signature = ""
        visual_feedback_roi: tuple[float, float, float, float] | None = None
        visual_feedback_epoch: int | None = None
        deferred_observe_image = ""
        deferred_observe_phase = "observe"
        deferred_observe_region = None
        refresh_authoritative_frame = False
        deferred_terminal: str | None = None
        for tc in tool_calls:
            if self.stop_event.is_set():
                return "stopped"
            if not self._wait_if_paused():
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
                BUS.publish("agent_tool", run_id=self.run_id, step=self._decision_step,
                            model_role=self._model_role(), action_id=tc.id,
                            tool=tc.function.name, error=str(exc))
                continue

            self._checkpoint_visual_frame_before_window_switch(tc.function.name)

            authored_target_roi = (
                self._visual_preflight_target_roi(tc.function.name, args)
                if self._visual_closed_loop_active and _main_model_has_vision()
                else None
            )

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
            preflight_pixel_stability = self._visual_preflight_requires_pixel_stability(
                tc.function.name, args,
            )
            needs_grounding = bool(
                closed_loop
                and _main_model_has_vision()
                and tc.function.name in _LOW_LEVEL_VISUAL_TOOLS
                and not (
                    self._has_fresh_visual_grounding()
                    if preflight_pixel_stability
                    else self._has_fresh_visual_identity()
                )
            )
            preflight_changed_image = ""
            preflight_change_ratio: float | None = 0.0
            preflight_error = ""
            preflight_capture_region = None
            frame_coordinate_error = ""
            frame_coordinate_meta: dict[str, Any] = {}
            pointer_status_fn = getattr(self.session, "_pointer_hold_status", None)
            pointer_status = (
                pointer_status_fn()
                if callable(pointer_status_fn)
                else {"active": False}
            )
            pointer_continuation = bool(
                pointer_status.get("active")
                and tc.function.name in {"move_mouse", "mouse_up"}
            )
            no_progress_block = None
            native_point_scope_block = None
            if (
                closed_loop
                and _main_model_has_vision()
                and not needs_grounding
                and not turn_barrier
                and not terminal_barrier
            ):
                no_progress_block = self._native_no_progress_guard(
                    tc.function.name, args,
                )
            runtime_guard_block = no_progress_block
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
                and not pointer_continuation
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
                native_point_scope_block = self._native_point_scope_guard(
                    tc.function.name,
                    args,
                )
                runtime_guard_block = (
                    native_point_scope_block
                    or runtime_guard_block
                )

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
                        step=self._decision_step, model_role=self._model_role(),
                        event_name="action.started",
                        action_id=tc.id, action_envelope=action_envelope,
                        tool=tc.function.name, args=args)
            started = time.monotonic()
            blocked_gui_only_action = ""
            if (
                not self._is_native_execute_desktop_task()
                and _is_gui_only_task(self.task)
            ):
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
            elif native_point_scope_block:
                result = ToolResult(json.dumps(
                    native_point_scope_block,
                    ensure_ascii=False,
                ))
            elif needs_grounding:
                refresh_authoritative_frame = True
                result = ToolResult(json.dumps({
                    "ok": False,
                    "verified": False,
                    "blocked": True,
                    "action_executed": False,
                    "visual_grounding_required": True,
                    "error": (
                        "The proposed GUI action had no current authoritative frame for "
                        "the target window and screen epoch, so it was not executed."
                    ),
                    "needs_replan": False,
                }, ensure_ascii=False))
            elif no_progress_block:
                block_count = self.run_state.register_no_progress_block(
                    str(no_progress_block.get("repeat_signature") or ""),
                )
                no_progress_block.update({
                    "repeated_guard_blocks": block_count,
                })
                stop_after_block = self.run_state.strategy_block_limit_reached(
                    str(no_progress_block.get("repeat_signature") or ""),
                    int(CONFIG.get(
                        "controller", "native_no_progress_max_blocks",
                        default=_NATIVE_NO_PROGRESS_MAX_BLOCKS,
                    ) or _NATIVE_NO_PROGRESS_MAX_BLOCKS),
                )
                if stop_after_block:
                    no_progress_block["safe_stop"] = True
                    no_progress_block["error"] += (
                        " The identical no-effect action reached the bounded repeat limit, so the "
                        "run is stopping rather than continuing without new evidence."
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
                refresh_authoritative_frame = True
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
                    "needs_replan": False,
                }
                coordinate_payload.update(frame_coordinate_meta)
                if spatial_batch_limit:
                    coordinate_payload["native_batch_spatial_limit"] = True
                result = ToolResult(json.dumps(coordinate_payload, ensure_ascii=False))
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
                tool_dispatched = True
                result = self._dispatch_with_fast_visual_wrap(tc.function.name, args)
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
                if visual_result_data.get("recovery_action_executed") is True:
                    # The requested click/drag was withheld, but scope repair
                    # changed foreground pixels. Give the model those pixels
                    # immediately instead of making it spend another turn on
                    # a now-stale FrameAuthority rejection.
                    refresh_authoritative_frame = True
                if (
                    not needs_grounding
                    and tool_dispatched
                    and not _result_explicitly_blocked_before_execution(result.text or "")
                    and (
                        visual_result_data.get("blocked") is not True
                        or _main_model_has_vision()
                    )
                    and result.terminal != "stopped"
                ):
                    visual_feedback_action = tc.function.name
                    visual_feedback_args = dict(args)
                    visual_feedback_action_executed = bool(
                        visual_result_data.get("action_executed") is not False
                        and visual_result_data.get("state_transition_skipped") is not True
                        and visual_result_data.get("skipped") is not True
                    )
                    visual_feedback_signature = self._native_spatial_action_signature(
                        tc.function.name,
                        args,
                        frame_region=self._visual_frame_region,
                        submitted_size=self._visual_submitted_size,
                    )
                    visual_feedback_roi = authored_target_roi
                    visual_feedback_epoch = int(
                        getattr(self.session, "_screen_epoch", 0) or 0
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
                        if (
                            visual_result_data.get("action_executed") is not False
                            and visual_result_data.get("state_transition_skipped") is not True
                            and visual_result_data.get("skipped") is not True
                        ):
                            # Execution provenance only: the fresh frame below
                            # still decides whether the input achieved the goal.
                            visual_result_data["native_action_executed"] = True
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
                )
                if failure_recovery is not None:
                    result.text = attach_recovery_metadata(result.text or "", failure_recovery)
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
                    "native_no_progress_guard",
                    "no_visual_progress",
                    "no_progress_streak",
                    "observed_change_ratio",
                    "repeated_guard_blocks",
                    "constraint_rejection_guard",
                    "constraint_category",
                    "constraint_rejection_streak",
                    "constraint_rejection_limit",
                    "partial_output_destructive_guard",
                    "action_executed",
                    "native_point_scope_guard",
                    "scope_reason",
                    "scene_coverage_guard",
                    "scene_coverage_advisory",
                    "current_scene_id",
                    "current_scene_visit_count",
                    "proposed_edge",
                    "prior_target_scene_ids",
                    "cycle_scene_path",
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
                        "executed": False,
                        "result_meta": failure_recovery.result_meta,
                    },
                )
            BUS.publish(
                "agent_tool",
                run_id=self.run_id,
                step=self._decision_step,
                model_role=self._model_role(),
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
        if (
            visual_feedback_action
            and (
                visual_feedback_epoch is None
                or int(getattr(self.session, "_screen_epoch", 0) or 0)
                == visual_feedback_epoch
            )
        ):
            self._inject_visual_frame(
                phase="post_action",
                action=visual_feedback_action,
                action_args=visual_feedback_args,
                action_executed=visual_feedback_action_executed,
                result_preview=visual_feedback_result,
                action_signature=visual_feedback_signature,
                action_roi=visual_feedback_roi,
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
        elif refresh_authoritative_frame:
            self._inject_visual_frame(
                phase="refresh",
                action_executed=False,
                result_preview="No action was executed because the prior frame identity was unavailable.",
            )
        if not self._is_native_execute_desktop_task():
            for hint in post_tool_hints[-2:]:
                self.messages.append({"role": "user", "content": hint})
        return deferred_terminal

    # ------------------------------------------------------------------ run
    def _prebind_initial_gui_target(self) -> None:
        """Open/focus a leading, explicit GUI application before LLM step 1."""
        if self._is_coding:
            return
        if self._is_native_execute_desktop_task():
            # The initial authoritative frame must reach the model before any
            # hidden launch/focus mutation.  The model can explicitly choose an
            # exposed app/window tool when the complete request actually calls
            # for a target change.
            return
        task = str(self.task or "").strip()
        if not re.match(
            r"^(?:严格.*?[:：]\s*)?(?:open|launch|start|run|打开|启动|运行|在|用)\s*",
            task,
            re.IGNORECASE,
        ):
            return
        # A typed stage has already validated and bound its requested app. Its
        # top-level HWND may nevertheless be replaced during startup. Reuse or
        # reopen that proven application identity instead of reparsing the
        # complete natural-language goal as an app name.
        if self.session._window_alive(self.session.hwnd):
            return
        recovered = self.session._recover_bound_target_window()
        if recovered:
            return

        from ..fast.subgoal import _initial_task_app_target

        target = (
            str(self.session.target_hint or "").strip()
            if self.session._target_binding_verified
            else _initial_task_app_target(task)
        )
        if not target:
            return
        args = {"name": target}
        action_id = f"prebind-{uuid.uuid4().hex[:8]}"
        self.run_state.before_tool("open_app", args)
        BUS.publish(
            "agent_tool_start",
            run_id=self.run_id,
            step=self._decision_step,
            model_role=self._model_role(),
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
            step=self._decision_step,
            model_role=self._model_role(),
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
        if not self.runtime_profile.experience_enabled:
            hints, self._hint_ids = "", []
        elif self._is_coding:
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
            runtime_section=self.runtime_profile.prompt_section())
        self.messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": self.task},
        ]
        native_desktop_stage = self._is_native_execute_desktop_task()
        if (
            _is_communication_task(self.task_intent)
            and str(self.runtime_profile.stage_type or "") != "ExecuteDesktopTask"
        ):
            payloads = _quoted_payloads(self.task)
            communication_mode = _communication_mode(self.task_intent)
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
        if not native_desktop_stage and _is_upload_publish_task(self.task):
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
        if not native_desktop_stage and _is_gui_only_task(self.task):
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
        if not native_desktop_stage and _is_multi_source_research_task(self.task):
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
        ):
            ctx = session_mod.render_context(self.session_context)
            if ctx:
                self.messages.append({"role": "user", "content": ctx})
        typed_gui_stage = bool(self.runtime_profile.stage_type) and not self._is_coding
        is_gui_only_task = typed_gui_stage or _is_gui_only_task(self.task)
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
            task_intent=self.task_intent,
            stage_type=self.runtime_profile.stage_type,
        )
        tools = tool_schemas(
            planning=plan_on,
            coding_runtime=self._is_coding,
            allow_shell=allow_shell_tools,
            allow_files=allow_file_tools,
            profile=schema_profile,
            native_vision=_main_model_has_vision(),
            required_capabilities=self.runtime_profile.required_capabilities,
            stage_tools=self.runtime_profile.stage_tools,
            stage_type=self.runtime_profile.stage_type,
        )
        if self.runtime_profile.model_tool_allowlist:
            allowed_stage_tools = set(self.runtime_profile.model_tool_allowlist)
            tools = [
                item for item in tools
                if str(item.get("function", {}).get("name") or "") in allowed_stage_tools
            ]
        model_tool_names = [
            str(item.get("function", {}).get("name") or "")
            for item in tools
            if isinstance(item, dict)
        ]
        BUS.publish("agent_start", run_id=self.run_id, task=self.task,
                    max_steps=max_steps, has_experience=bool(hints),
                    agent="unified", model_role=self._model_role(),
                    runtime=self.runtime_profile.to_dict(),
                    schema_profile=schema_profile,
                    model_tool_count=len(model_tool_names),
                    model_tool_names=model_tool_names,
                    runtime_version="legacy",
                    model_tool_schema_chars=len(json.dumps(
                        tools, ensure_ascii=False, separators=(",", ":"), default=str,
                    )),
                    prompt_context_chars=_prompt_context_chars(self.messages),
                    prompt_context_measurement="text_structure_without_binary",
                    mission_card_chars=None,
                    task_contract_id=self.runtime_profile.task_contract_id,
                    stage_type=self.runtime_profile.stage_type,
                    experience_enabled=self.runtime_profile.experience_enabled,
                    required_capabilities=list(self.runtime_profile.required_capabilities),
                    exposed_capabilities=list(self.runtime_profile.exposed_capabilities),
                    domain_adapter=self.runtime_profile.domain_adapter,
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
                if not self._wait_if_paused():
                    self.status = "stopped"
                    break
                self._drain_interjections()
                self._decision_step = step
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

                finish_reason = str(
                    getattr(resp.choices[0], "finish_reason", "") or ""
                )
                if (
                    msg.tool_calls
                    and finish_reason == "length"
                    and not self._tool_calls_have_complete_arguments(msg.tool_calls)
                    and not self._is_coding
                ):
                    if self._recover_truncated_gui_tool_call():
                        continue
                    break

                if not msg.tool_calls:
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
                    candidate_reply = self._is_native_execute_desktop_task()
                    if self._recent_results or candidate_reply:
                        self._candidate_reply_pending = candidate_reply
                        try:
                            reply_verified = self._verify_done(
                                client,
                                steps_left=max_steps - step,
                            )
                        finally:
                            self._candidate_reply_pending = False
                        if not reply_verified:
                            if self._safe_stop_after_repeated_done_rejection():
                                break
                            continue
                        if candidate_reply and not self._done_verified:
                            # A disabled/bypassed judge must not turn an
                            # unverified plain reply into stage success.
                            self.messages.append({
                                "role": "user",
                                "content": (
                                    "[completion check failed] The candidate final response was not "
                                    "independently verified against the original request and latest frame."
                                ),
                            })
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
                if (
                    terminal == "done"
                    and not self._done_verified
                    and not self._verify_done(client, steps_left=max_steps - step)
                ):
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

        if self.runtime_profile.experience_enabled:
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
