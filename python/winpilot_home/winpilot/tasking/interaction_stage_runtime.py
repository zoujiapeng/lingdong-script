"""Deterministic runtime for typed visual-interaction stages."""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
from pathlib import Path
import threading
import time
from typing import Any, Callable

import cv2
import numpy as np

from ..agent.runtime_profile import RuntimeProfile
from ..agent.tools import ToolSession
from ..fast.watcher import ScreenWatcher
from ..orchestrator.runtime import NodeResult
from ..perception import win32
from ..perception.surface_binding import InteractionRegion, SurfaceBinding
from ..utils import image_capture
from ..utils.log import BUS
from ..utils.screenshot import Region, window_region
from .artifacts import Artifact, ArtifactStore
from .contracts import StageType, TaskContract, TaskStage
from .evidence import EvidenceRegistry
from .execution_context import (
    foreground_handoff_conflict,
    normalize_execution_context,
    validated_handoff_target,
)
from .interaction import (
    InteractionDecision,
    InteractionPlanOutputError,
    plan_interaction,
    verify_interaction_completion,
    verify_interaction_effect,
)
from .interaction_controller import (
    controller_actions,
    controller_preparation_actions,
)
from .visual_effects import (
    verify_direct_manipulation_transition,
    verify_pointer_gesture_realization,
    verify_typed_direct_manipulation_completion,
    verify_typed_freehand_turn_completion,
)
from .visual_geometry import VisualGeometryError, refine_interaction_geometry


INTERACTION_STAGE_TYPES = {
    StageType.OBSERVE_SCENE,
    StageType.INFER_INTERACTION,
    StageType.PERFORM_TURN,
    StageType.VERIFY_LOCAL_EFFECT,
    StageType.WAIT_FOR_EXTERNAL_CHANGE,
}

_MAX_LOCAL_CAUSAL_CHANGE_RATIO = 0.05
_MAX_SAME_FRAME_PLAN_ATTEMPTS = 2
_POINTER_SESSION_LOCK = threading.RLock()
_POINTER_SESSIONS: dict[str, ToolSession] = {}


def _pointer_session_key(context: dict[str, Any]) -> str:
    return str(context.get("run_id") or "").strip()


def _registered_pointer_session(
    context: dict[str, Any],
) -> ToolSession | None:
    key = _pointer_session_key(context)
    if not key:
        return None
    with _POINTER_SESSION_LOCK:
        return _POINTER_SESSIONS.get(key)


def _register_pointer_session(
    context: dict[str, Any], session: ToolSession,
) -> None:
    key = _pointer_session_key(context)
    if not key:
        return
    with _POINTER_SESSION_LOCK:
        _POINTER_SESSIONS[key] = session


def _discard_registered_pointer_session(
    context: dict[str, Any], session: Any | None = None,
) -> ToolSession | None:
    key = _pointer_session_key(context)
    if not key:
        return None
    with _POINTER_SESSION_LOCK:
        registered = _POINTER_SESSIONS.get(key)
        if session is not None and registered is not session:
            return None
        return _POINTER_SESSIONS.pop(key, None)


def _local_causal_change_ratio(
    decision: InteractionDecision,
    *,
    remote_feedback: bool,
) -> float:
    """Bound model sensitivity to the causal pixel-detection contract.

    Geometry and semantic verifiers decide whether the requested result is
    correct.  This gate only proves that the current transaction caused a
    visible local change, so a model-provided large percentage cannot make a
    small but valid object/control transition logically unreachable.
    """
    requested = max(
        0.0,
        min(1.0, float(decision.expected_effect.minimum_change_ratio)),
    )
    if remote_feedback:
        return min(requested, 0.0025)
    return min(requested, _MAX_LOCAL_CAUSAL_CHANGE_RATIO)


def _controller_visual_no_effect(result: dict[str, Any]) -> bool:
    """Recognize a causal no-effect failure anywhere in a batch result."""
    pending: list[Any] = [result]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            failure_type = str(value.get("failure_type") or "").upper()
            if failure_type in {
                "VISUAL_ACTION_NO_EFFECT", "DRAW_NO_VISIBLE_CHANGE",
            }:
                return True
            if (
                failure_type == "DRAW_PATH_NOT_REALIZED"
                and value.get("mutation_applied") is False
            ):
                # A cursor/hover delta without attributable path pixels is a
                # recoverable no-effect, not an already-applied mutation.  If
                # the provider reports partial mutation evidence, fail closed
                # and do not permit an automatic replay.
                return True
            if value.get("visual_change_detected") is False:
                return True
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return False


def _local_resolution_requirement_proofs(
    decision: InteractionDecision,
    geometry_grounding: dict[str, Any],
) -> list[dict[str, Any]]:
    """Bind every typed local-resolution precondition to current-pixel evidence."""
    requirements = list(decision.resolution_requirements)
    if not requirements:
        return []
    refinements = [
        item for item in geometry_grounding.get("refinements") or []
        if isinstance(item, dict)
    ]

    def provider_names(value: Any) -> list[str]:
        found: list[str] = []
        if isinstance(value, dict):
            provider = str(value.get("provider") or "").strip()
            if provider:
                found.append(provider)
            for nested in value.values():
                found.extend(provider_names(nested))
        elif isinstance(value, list):
            for nested in value:
                found.extend(provider_names(nested))
        return found

    proofs: list[dict[str, Any]] = []
    for requirement in requirements:
        index = int(requirement.action_index)
        candidates = [
            item for item in refinements
            if int(item.get("action_index", -1)) == index
        ]
        accepted: dict[str, Any] | None = None
        accepted_providers: list[str] = []
        for candidate in candidates:
            providers = list(dict.fromkeys(provider_names(candidate)))
            current_pixel_providers = [
                provider for provider in providers
                if provider not in {
                    "typed_spatial_policy", "typed_semantic_free_path",
                }
            ]
            if (
                candidate.get("applied", True) is True
                and current_pixel_providers
            ):
                accepted = candidate
                accepted_providers = current_pixel_providers
                break
        if accepted is None:
            raise VisualGeometryError(
                "a declared current_frame_geometry resolution requirement "
                f"for action {index} has no unique current-pixel provider proof"
            )
        proofs.append({
            **requirement.model_dump(mode="json"),
            "verified": True,
            "evidence_providers": accepted_providers,
            "refinement": accepted,
        })
    return proofs


def _mutation_signature(decision: InteractionDecision) -> str:
    payload = [item.model_dump(mode="json") for item in decision.actions]
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _failed_visible_mutation(
    decision: InteractionDecision,
    artifact: Artifact,
    *,
    failure_type: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Describe an already-applied mutation that must never be blind-replayed."""
    return {
        "frame_id": int(decision.frame_id),
        "artifact_id": artifact.artifact_id,
        "frame_sha256": artifact.sha256,
        "action_signature": _mutation_signature(decision),
        "failure_type": str(failure_type),
        "relevant_regions": [
            list(bounds) for bounds in dict.fromkeys(
                tuple(float(value) for value in bounds)
                for bounds in (
                    _action_grounding_regions(decision)
                    + _effect_regions(decision)
                )
            )
        ],
        "evidence": dict(evidence),
        "replay_policy": "forbidden_without_explicit_new_user_or_external_state",
    }


def _failed_no_effect_evidence(
    state: dict[str, Any],
    artifact: Artifact,
    *,
    store: ArtifactStore | None = None,
) -> dict[str, Any]:
    """Decide whether a failed mutation has any new causal pixel evidence.

    A whole-window hash is only a fast path.  Cursor blink, clocks, animation,
    and unrelated chrome must not purchase another model call.  When the frame
    differs, compare only the source/target/path/effect regions saved with the
    failed typed action.  Missing or unavailable comparisons are not proof of
    new evidence and therefore fail closed.
    """
    failed = state.get("failed_no_effect_mutation")
    if not isinstance(failed, dict):
        return {"repeats": False, "method": "no_failed_mutation"}
    prior_sha = str(failed.get("frame_sha256") or "")
    if prior_sha and prior_sha == artifact.sha256:
        return {
            "repeats": True,
            "method": "byte_identical_authoritative_frame",
            "failed_artifact_id": str(failed.get("artifact_id") or ""),
            "current_artifact_id": str(getattr(artifact, "artifact_id", "") or ""),
            "action_signature": str(failed.get("action_signature") or ""),
            "region_changes": [],
        }

    regions = [
        [float(value) for value in bounds]
        for bounds in (failed.get("relevant_regions") or [])
        if isinstance(bounds, (list, tuple)) and len(bounds) == 4
    ]
    failed_artifact_id = str(failed.get("artifact_id") or "")
    current_artifact_id = str(getattr(artifact, "artifact_id", "") or "")
    if store is None or not failed_artifact_id or not current_artifact_id or not regions:
        return {
            "repeats": False,
            "method": "whole_frame_hash_only_compatibility",
            "failed_artifact_id": failed_artifact_id,
            "current_artifact_id": current_artifact_id,
            "action_signature": str(failed.get("action_signature") or ""),
            "region_changes": [],
        }

    changes: list[dict[str, Any]] = []
    for bounds in regions:
        try:
            change = store.frame_change(
                failed_artifact_id,
                current_artifact_id,
                target_region=_normalized_roi_pixels(bounds, artifact),
                pixel_threshold=18,
                min_change_ratio=0.025,
                min_mean_delta=2.0,
            )
        except Exception as exc:
            change = {
                "method": "unavailable",
                "changed": None,
                "reason": str(exc),
            }
        changes.append({"normalized_region": bounds, **change})
    changed = any(item.get("changed") is True for item in changes)
    comparison_complete = all(
        item.get("method") != "unavailable" and item.get("changed") is not None
        for item in changes
    )
    return {
        # Only a causal-region delta is positive evidence.  An unavailable
        # comparison is not permission to repeat a failed pointer mutation.
        "repeats": not changed,
        "method": (
            "causal_regions_changed"
            if changed else (
                "causal_regions_unchanged"
                if comparison_complete else "causal_region_comparison_unavailable"
            )
        ),
        "comparison_complete": comparison_complete,
        "failed_artifact_id": failed_artifact_id,
        "current_artifact_id": current_artifact_id,
        "action_signature": str(failed.get("action_signature") or ""),
        "region_changes": changes,
    }


def _failed_no_effect_repeats_frame(
    state: dict[str, Any], artifact: Artifact, *, store: ArtifactStore | None = None,
) -> bool:
    """Compatibility boolean wrapper around causal-region evidence audit."""
    result = _failed_no_effect_evidence(state, artifact, store=store)
    state["failed_no_effect_evidence_check"] = result
    return bool(result["repeats"])


def _prove_artifact_frame_lineage(
    artifact: Artifact,
    *,
    target: dict[str, Any],
    state: dict[str, Any],
    run_id: str,
) -> dict[str, Any]:
    """Prove that artifact pixels and the bound physical frame share origin."""
    expected_region = {
        key: int(target.get("frame_region", {}).get(key) or 0)
        for key in ("left", "top", "width", "height")
    }
    metadata = artifact.metadata if isinstance(artifact.metadata, dict) else {}
    actual_region_raw = metadata.get("region")
    declared_region_raw = metadata.get("frame_region")
    actual_region = (
        {
            key: int(actual_region_raw.get(key) or 0)
            for key in ("left", "top", "width", "height")
        }
        if isinstance(actual_region_raw, dict) else {}
    )
    declared_region = (
        {
            key: int(declared_region_raw.get(key) or 0)
            for key in ("left", "top", "width", "height")
        }
        if isinstance(declared_region_raw, dict) else {}
    )
    errors: list[str] = []
    if artifact.run_id != str(run_id):
        errors.append("run_id")
    if artifact.purpose != "observation" or artifact.sendable:
        errors.append("observation_purpose")
    if artifact.source_scope != "window":
        errors.append("capture_scope")
    if int(artifact.source_hwnd or 0) != int(target.get("hwnd") or 0):
        errors.append("source_hwnd")
    if actual_region != expected_region:
        errors.append("actual_capture_region")
    if declared_region != expected_region:
        errors.append("declared_frame_region")
    if (
        int(artifact.width) != expected_region["width"]
        or int(artifact.height) != expected_region["height"]
    ):
        errors.append("artifact_dimensions")
    if str(metadata.get("session_id") or "") != str(state.get("session_id") or ""):
        errors.append("session_id")
    if int(metadata.get("frame_id") or 0) != int(state.get("frame_id") or 0):
        errors.append("frame_id")
    if errors:
        raise ValueError("artifact/frame lineage mismatch: " + ", ".join(errors))
    return {
        "verified": True,
        "run_id": artifact.run_id,
        "session_id": str(metadata.get("session_id") or ""),
        "frame_id": int(metadata.get("frame_id") or 0),
        "source_hwnd": int(artifact.source_hwnd),
        "capture_region": actual_region,
        "declared_frame_region": declared_region,
        "artifact_dimensions": {
            "width": int(artifact.width), "height": int(artifact.height),
        },
    }


def _json(text: str) -> dict[str, Any]:
    try:
        value = json.loads(str(text or "{}"))
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _failure(message: str, kind: str, **details: Any) -> RuntimeError:
    error = RuntimeError(message)
    error.failure_type = kind
    error.stage_details = details
    return error


def _rect(region: Region) -> dict[str, int]:
    return {
        "left": int(region.left),
        "top": int(region.top),
        "width": int(region.width),
        "height": int(region.height),
    }


def _action_rect(region: Region) -> dict[str, int]:
    """Controller action spelling for a physical screen rectangle."""
    return {
        "x": int(region.left),
        "y": int(region.top),
        "width": int(region.width),
        "height": int(region.height),
    }


def _normalized_roi_pixels(
    bounds: list[float] | tuple[float, float, float, float],
    artifact: Artifact,
) -> dict[str, int]:
    left, top, right, bottom = [float(value) for value in bounds]
    x1 = max(0, min(artifact.width - 1, int(left / 1000.0 * artifact.width)))
    y1 = max(0, min(artifact.height - 1, int(top / 1000.0 * artifact.height)))
    x2 = max(x1 + 1, min(artifact.width, int(round(right / 1000.0 * artifact.width))))
    y2 = max(y1 + 1, min(artifact.height, int(round(bottom / 1000.0 * artifact.height))))
    return {"left": x1, "top": y1, "width": x2 - x1, "height": y2 - y1}


def _normalized_roi_screen(
    bounds: list[float] | tuple[float, float, float, float],
    frame_region: dict[str, int],
) -> Region:
    left, top, right, bottom = [float(value) for value in bounds]
    x1 = int(frame_region["left"] + left / 1000.0 * frame_region["width"])
    y1 = int(frame_region["top"] + top / 1000.0 * frame_region["height"])
    x2 = int(frame_region["left"] + right / 1000.0 * frame_region["width"])
    y2 = int(frame_region["top"] + bottom / 1000.0 * frame_region["height"])
    return Region(x1, y1, max(1, x2 - x1), max(1, y2 - y1)).clamp_to_screen()


def _authoritative_frame_quality(
    artifact: Artifact,
    client_bounds: list[float] | tuple[float, float, float, float] | None,
) -> dict[str, Any]:
    """Detect only extreme compositor/readiness frames before using the VLM.

    Dark applications are valid GUI surfaces, so no single brightness or
    contrast threshold is sufficient.  A frame is unusable only when its
    client area is simultaneously near-monochrome, extremely low entropy and
    almost edgeless.  This catches blank compositor surfaces without encoding
    any application, text, color, or task-specific expectation.
    """
    image = cv2.imread(str(artifact.managed_path), cv2.IMREAD_GRAYSCALE)
    if image is None or image.size == 0:
        return {"usable": False, "reason": "decode_failed"}
    height, width = image.shape[:2]
    roi = image
    if isinstance(client_bounds, (list, tuple)) and len(client_bounds) == 4:
        left, top, right, bottom = [float(value) for value in client_bounds]
        x1 = max(0, min(width - 1, int(left / 1000.0 * width)))
        y1 = max(0, min(height - 1, int(top / 1000.0 * height)))
        x2 = max(x1 + 1, min(width, int(round(right / 1000.0 * width))))
        y2 = max(y1 + 1, min(height, int(round(bottom / 1000.0 * height))))
        roi = image[y1:y2, x1:x2]
    if roi.size < 400:
        return {"usable": False, "reason": "client_roi_too_small"}
    histogram = np.bincount(roi.reshape(-1), minlength=256).astype(np.float64)
    probabilities = histogram[histogram > 0] / float(roi.size)
    entropy = float(-(probabilities * np.log2(probabilities)).sum())
    standard_deviation = float(np.std(roi))
    dominant_ratio = float(histogram.max() / float(roi.size))
    edges = cv2.Canny(roi, 24, 72)
    edge_density = float(np.count_nonzero(edges) / float(edges.size))
    extreme_uniform = bool(
        dominant_ratio >= 0.995
        and standard_deviation <= 3.0
        and entropy <= 0.25
        and edge_density <= 0.0015
    )
    return {
        "usable": not extreme_uniform,
        "reason": "extreme_uniform_client" if extreme_uniform else "content_present",
        "dominant_ratio": round(dominant_ratio, 6),
        "stddev": round(standard_deviation, 6),
        "entropy": round(entropy, 6),
        "edge_density": round(edge_density, 6),
        "width": int(roi.shape[1]),
        "height": int(roi.shape[0]),
    }


def _action_grounding_regions(decision: InteractionDecision) -> list[list[float]]:
    """Return only pixels that ground source, target, or pointer path.

    Long drags and strokes must not use one path-sized bounding rectangle: a
    small source can move while its delta is diluted by the static pixels
    between endpoints.  Expected effects are deliberately excluded because a
    success banner or animation is post-action evidence, not preflight
    grounding for coordinates chosen from the prior frame.
    """
    regions: list[list[float]] = []
    for action in decision.actions:
        for bounds in (action.source_region, action.target_region):
            if len(bounds) == 4:
                regions.append(list(bounds))

    points = [
        point
        for action in decision.actions
        for point in action.normalized_points()
    ]
    max_samples = 12
    if len(points) > max_samples:
        indices = {
            round(index * (len(points) - 1) / (max_samples - 1))
            for index in range(max_samples)
        }
        points = [points[index] for index in sorted(indices)]
    outer_left, outer_top, outer_right, outer_bottom = decision.interaction_region
    padding = 48.0
    regions.extend([
        [
            max(float(outer_left), float(x) - padding),
            max(float(outer_top), float(y) - padding),
            min(float(outer_right), float(x) + padding),
            min(float(outer_bottom), float(y) + padding),
        ]
        for x, y in points
    ])
    unique = dict.fromkeys(
        tuple(float(value) for value in region)
        for region in regions
    )
    return [list(region) for region in unique]


def _effect_regions(decision: InteractionDecision) -> list[list[float]]:
    """Return action/global postcondition ROIs, never grounding ROIs.

    A point-selection/control activation can acknowledge success in a remote
    banner, dialog, selection panel, or navigation surface while the clicked
    object itself remains pixel-identical.  For only those visible mechanisms,
    include the already-bound stable surface as a *secondary causal search
    region*.  Semantic verification still has to prove that the remote change
    satisfies the task; a whole-surface delta alone never completes it.
    """
    regions = [
        list(action.expected_effect_region)
        for action in decision.actions
        if len(action.expected_effect_region) == 4
    ]
    if len(decision.expected_effect.region) == 4:
        regions.append(list(decision.expected_effect.region))
    if not regions:
        regions.append(list(decision.interaction_region))
    remote_feedback_mechanisms = {
        "control_activation", "point_selection", "click_to_place",
        "text_entry", "keyboard_command",
    }
    if any(
        action.interaction_mechanism in remote_feedback_mechanisms
        for action in decision.actions
    ) and len(decision.surface_region) == 4:
        regions.append(list(decision.surface_region))
    unique = dict.fromkeys(
        tuple(float(value) for value in region)
        for region in regions
    )
    return [list(region) for region in unique]


def _external_watch_bounds(
    state: dict[str, Any],
    decision: InteractionDecision,
    baseline: Artifact,
) -> tuple[list[float], dict[str, Any]]:
    """Bind an external-turn watcher to proven shared-state pixels.

    A model may conservatively describe the whole client as its surface.  That
    is safe for observation, but unsafe for causal waiting: tool palettes,
    hover chrome, and transient menus are not another participant's move.  If
    current pixels expose one credible work surface and the last verified
    agent action was grounded inside it, narrow the watcher to that surface.
    Otherwise retain the semantic interaction region and fail neither open nor
    by application-specific coordinates.
    """
    fallback = list(
        decision.interaction_region
        if len(decision.interaction_region) == 4
        else decision.surface_region
    )
    target = state.get("target_binding") or {}
    try:
        detected = image_capture.credible_work_surface_frame_bounds(
            base64.b64encode(Path(baseline.managed_path).read_bytes()).decode(),
            client_frame_bounds=target.get("client_frame_bounds"),
        )
    except Exception as exc:
        return fallback, {
            "source": "semantic_interaction_region",
            "reason": "current_pixel_surface_detection_failed",
            "error": str(exc),
        }
    if not isinstance(detected, dict) or detected.get("ok") is not True:
        return fallback, {
            "source": "semantic_interaction_region",
            "reason": "no_unique_current_pixel_work_surface",
            "detected_surface": detected,
        }
    surface = detected.get("detected_work_surface_frame_bounds")
    if not isinstance(surface, (list, tuple)) or len(surface) != 4:
        return fallback, {
            "source": "semantic_interaction_region",
            "reason": "detected_surface_geometry_invalid",
            "detected_surface": detected,
        }
    outer_left, outer_top, outer_right, outer_bottom = [
        float(value) for value in surface
    ]
    previous = state.get("last_executed_action") or state.get("executed_action") or {}
    actions = list(previous.get("actions") or []) if isinstance(previous, dict) else []
    grounding_centres: list[tuple[float, float]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        anchor = action.get("target_anchor")
        if isinstance(anchor, (list, tuple)) and len(anchor) == 2:
            try:
                grounding_centres.append((float(anchor[0]), float(anchor[1])))
            except (TypeError, ValueError, OverflowError):
                pass
        for key in (
            "source_region", "target_region", "expected_effect_region",
        ):
            bounds = action.get(key)
            if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
                continue
            try:
                left, top, right, bottom = [float(value) for value in bounds]
            except (TypeError, ValueError, OverflowError):
                continue
            grounding_centres.append(((left + right) / 2.0, (top + bottom) / 2.0))
    grounded_inside = any(
        outer_left <= x <= outer_right and outer_top <= y <= outer_bottom
        for x, y in grounding_centres
    )
    if not grounded_inside:
        return fallback, {
            "source": "semantic_interaction_region",
            "reason": "detected_surface_not_bound_by_last_verified_action",
            "detected_surface": detected,
        }
    bounds = [outer_left, outer_top, outer_right, outer_bottom]
    state["detected_surface"] = detected
    state["external_watch_region"] = {
        "bounds": bounds,
        "coordinate_space": "frame_0_1000",
        "source": "current_pixel_surface_bound_by_verified_action",
        "confidence": float(detected.get("light_ratio") or 0.0),
    }
    return bounds, {
        "source": "current_pixel_surface_bound_by_verified_action",
        "reason": "last_verified_action_grounded_inside_unique_surface",
        "detected_surface": detected,
    }


def _is_remote_feedback_region(
    decision: InteractionDecision,
    bounds: list[float],
) -> bool:
    remote_feedback_mechanisms = {
        "control_activation", "point_selection", "click_to_place",
        "text_entry", "keyboard_command",
    }
    return bool(
        len(decision.surface_region) == 4
        and all(
            abs(float(left) - float(right)) <= 1e-6
            for left, right in zip(bounds, decision.surface_region)
        )
        and any(
            action.interaction_mechanism in remote_feedback_mechanisms
            for action in decision.actions
        )
    )


def _persistent_plan_has_causal_continuation(
    decision: InteractionDecision,
) -> bool:
    """Let the typed stage graph own post-action verify/wait sequencing."""
    if decision.continuation in {"wait_external", "finish"}:
        return True
    return bool(
        decision.continuation == "reobserve"
        and decision.agent_should_act
        and decision.actions
    )


def _deferred_program(
    decision: InteractionDecision,
) -> dict[str, Any] | None:
    if not decision.deferred_actions:
        return None
    serialized = [
        item.model_dump(mode="json") for item in decision.deferred_actions
    ]
    digest = hashlib.sha256(json.dumps(
        serialized, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()[:16]
    return {
        "program_id": f"interaction-program-{digest}",
        "origin_frame_id": int(decision.frame_id),
        "next_step_index": len(decision.actions),
        "remaining_actions": serialized,
        "terminal_continuation": decision.program_continuation,
    }


def _next_deferred_decision(
    decision: InteractionDecision,
    program: dict[str, Any],
    *,
    frame_id: int,
    artifact_id: str,
) -> tuple[InteractionDecision, dict[str, Any]]:
    remaining = list(program.get("remaining_actions") or [])
    if not remaining:
        raise ValueError("deferred interaction program has no remaining action")
    action = dict(remaining[0])
    future = remaining[1:]
    continuation = (
        "reobserve"
        if future
        else str(program.get("terminal_continuation") or "finish")
    )
    if continuation not in {"finish", "reobserve", "wait_external"}:
        continuation = "reobserve"
    effect_region = list(action.get("expected_effect_region") or [])
    payload = decision.model_dump(mode="python")
    payload.update({
        "actions": [action],
        "expected_effect": {
            "description": (
                "the next controller-grounded action in the verified local "
                "program produces its declared visible effect"
            ),
            "region": effect_region,
            "region_binding": "independent_region",
            "binding_action_index": -1,
            "minimum_change_ratio": 0.001,
            "semantic_verification_required": True,
        },
        "continuation": continuation,
        "blocked_reason": "",
        "wait_for_external_change": continuation == "wait_external",
        "frame_id": int(frame_id),
        "observation_artifact_id": str(artifact_id),
    })
    next_decision = InteractionDecision.model_validate(payload)
    updated = dict(program)
    updated["remaining_actions"] = future
    updated["next_step_index"] = int(program.get("next_step_index") or 0) + 1
    return next_decision, updated


def _action_guard_bounds(decision: InteractionDecision) -> list[float]:
    """Return the union guard for compatibility with older trace consumers."""
    regions = _action_grounding_regions(decision) or [
        list(decision.interaction_region),
    ]
    return [
        min(item[0] for item in regions),
        min(item[1] for item in regions),
        max(item[2] for item in regions),
        max(item[3] for item in regions),
    ]


def _authority_signature(target: dict[str, Any]) -> dict[str, Any]:
    """Return the geometry/identity fields that make frame coordinates valid."""
    return {
        "hwnd": int(target.get("hwnd") or 0),
        "frame_region": dict(target.get("frame_region") or {}),
        "client_region": dict(target.get("client_region") or {}),
        "client_frame_bounds": list(target.get("client_frame_bounds") or []),
        "capture_scope": str(target.get("capture_scope") or ""),
    }


class InteractionStageEngine:
    """Own live state across Observe/Infer/Perform/Verify/Wait stages."""

    def __init__(
        self,
        *,
        stage_output: Callable[..., dict[str, Any]],
    ) -> None:
        self.stage_output = stage_output

    @staticmethod
    def _state(context: dict[str, Any], contract: TaskContract) -> dict[str, Any]:
        state = context["runtime_state"].setdefault("interaction_state", {})
        state.setdefault("session_id", contract.session.session_id if contract.session else "")
        state.setdefault("status", "initializing")
        state.setdefault("frame_id", 0)
        state.setdefault("model_calls", 0)
        state.setdefault("turn_index", 0)
        state.setdefault("agent_turns_since_external_change", 0)
        state.setdefault("replan_count", 0)
        state.setdefault("verified_turn_history", [])
        execution_context = normalize_execution_context(context.get("inputs"))
        if execution_context:
            state.setdefault("execution_context", execution_context)
        return state

    @staticmethod
    def _budget_snapshot(state: dict[str, Any], contract: TaskContract) -> dict[str, int | float]:
        session = contract.session
        if session is None:
            return {}
        return {
            "semantic_turns_used": int(state.get("turn_index") or 0),
            "semantic_turns_limit": int(session.max_semantic_turns),
            "replans_used": int(state.get("replan_count") or 0),
            "replans_limit": int(session.max_replans),
            "model_calls_used": int(state.get("model_calls") or 0),
            "model_calls_limit": int(session.max_model_calls),
            "external_change_timeout_s": float(session.external_change_timeout_s),
        }

    @classmethod
    def _reserve_model_call(
        cls,
        state: dict[str, Any],
        contract: TaskContract,
        *,
        stage: str,
    ) -> None:
        session = contract.session
        if session is None:
            raise _failure("interaction session contract is missing", "contract_unsatisfied")
        attempted = int(state.get("model_calls") or 0) + 1
        if attempted > int(session.max_model_calls):
            state["budget"] = cls._budget_snapshot(state, contract)
            raise _failure(
                f"interaction model-call budget exhausted before {stage}",
                "interaction_budget_exhausted",
                budget=dict(state["budget"]),
                budget_dimension="model_calls",
                model_calls=0,
                model_elapsed_ms=0,
                controller_elapsed_ms=0,
            )
        state["model_calls"] = attempted
        state["budget"] = cls._budget_snapshot(state, contract)

    @classmethod
    def _reserve_replan(
        cls,
        state: dict[str, Any],
        contract: TaskContract,
        *,
        reason: str,
        activity: dict[str, Any] | None = None,
    ) -> None:
        session = contract.session
        if session is None:
            raise _failure("interaction session contract is missing", "contract_unsatisfied")
        attempted = int(state.get("replan_count") or 0) + 1
        if attempted > int(session.max_replans):
            state["budget"] = cls._budget_snapshot(state, contract)
            consumed = activity if isinstance(activity, dict) else {}
            raise _failure(
                f"interaction replan budget exhausted after {reason}",
                "interaction_budget_exhausted",
                budget=dict(state["budget"]),
                budget_dimension="replans",
                replan_reason=reason,
                model_calls=int(consumed.get("model_calls") or 0),
                model_elapsed_ms=int(consumed.get("model_elapsed_ms") or 0),
                controller_elapsed_ms=int(
                    consumed.get("controller_elapsed_ms") or 0
                ),
                capture_elapsed_ms=int(consumed.get("capture_elapsed_ms") or 0),
            )
        state["replan_count"] = attempted
        state["budget"] = cls._budget_snapshot(state, contract)

    @classmethod
    def _assert_mutation_budget(
        cls,
        state: dict[str, Any],
        contract: TaskContract,
    ) -> None:
        session = contract.session
        if session is None:
            raise _failure("interaction session contract is missing", "contract_unsatisfied")
        if int(state.get("turn_index") or 0) >= int(session.max_semantic_turns):
            state["budget"] = cls._budget_snapshot(state, contract)
            raise _failure(
                "interaction semantic-turn budget exhausted before mutation",
                "interaction_budget_exhausted",
                budget=dict(state["budget"]),
                budget_dimension="semantic_turns",
                model_calls=0,
                model_elapsed_ms=0,
                controller_elapsed_ms=0,
            )

    @staticmethod
    def _binding(context: dict[str, Any]) -> SurfaceBinding:
        binding = context["runtime_state"].get("interaction_surface_binding")
        if not isinstance(binding, SurfaceBinding):
            binding = SurfaceBinding()
            context["runtime_state"]["interaction_surface_binding"] = binding
        return binding

    @staticmethod
    def _release_pointer_session(
        context: dict[str, Any],
        state: dict[str, Any],
        *,
        reason: str,
    ) -> None:
        local_session = context.pop("_interaction_pointer_session", None)
        registered_session = _discard_registered_pointer_session(context)
        sessions: list[Any] = []
        for candidate in (local_session, registered_session):
            if candidate is not None and all(
                candidate is not item for item in sessions
            ):
                sessions.append(candidate)
        for session in sessions:
            try:
                close = getattr(session, "close", None)
                if callable(close):
                    close()
            except Exception:
                pass
        state["pointer_hold"] = {
            "active": False,
            "button": "",
            "since_frame_id": 0,
            "release_reason": reason,
        }

    @staticmethod
    def _resolve_hwnd(source: str) -> int:
        source = str(source or "").strip()
        if source and source not in {"active_window", "foreground", "current"}:
            found = win32.find_window(source)
            return int(found.get("hwnd") or 0) if found else 0
        try:
            return int(ctypes.windll.user32.GetForegroundWindow() or 0)
        except Exception:
            return 0

    @staticmethod
    def _foreground() -> int:
        try:
            return int(ctypes.windll.user32.GetForegroundWindow() or 0)
        except Exception:
            return 0

    def _focus_target(
        self,
        *,
        hwnd: int,
        title: str,
        task: str,
        task_intent: Any,
        run_id: str,
        stop_event: Any,
    ) -> None:
        session = ToolSession(
            stop_event=stop_event,
            task=task,
            runtime_profile=RuntimeProfile(desktop=True, coding=False),
            task_intent=task_intent,
            communication_context={"active": False},
        )
        session.run_id = run_id
        try:
            session._remember_target(hwnd, title)
            result = _json(session.dispatch("focus_window", {"hwnd": hwnd}).text)
        finally:
            session.close()
        if self._foreground() != int(hwnd):
            raise _failure(
                str(result.get("error") or "target window could not be focused"),
                "focus_lost",
                target_hwnd=hwnd,
                foreground_hwnd=self._foreground(),
                controller_result=result,
            )

    def _capture_scene(
        self,
        *,
        context: dict[str, Any],
        contract: TaskContract,
        store: ArtifactStore,
        stop_event: Any,
        source: str,
        focus: bool,
    ) -> Artifact:
        state = self._state(context, contract)
        operation_started = time.monotonic()
        controller_elapsed_ms = 0
        binding = state.get("target_binding") if isinstance(state.get("target_binding"), dict) else {}
        hwnd = int(binding.get("hwnd") or 0)
        execution_context = normalize_execution_context(context.get("inputs"))
        active_source = str(source or "").strip().casefold() in {
            "", "active_window", "foreground", "current",
        }
        if not hwnd and active_source:
            hwnd, target_issue = validated_handoff_target(execution_context)
            if target_issue:
                raise _failure(
                    str(target_issue.get("reason") or "desktop target handoff failed"),
                    str(target_issue.get("failure_type") or "target_binding_missing"),
                    execution_context=execution_context,
                    **{
                        key: value for key, value in target_issue.items()
                        if key not in {"reason", "failure_type"}
                    },
                )
        if not hwnd:
            hwnd = self._resolve_hwnd(source)
        if not hwnd:
            raise _failure(f"interaction target window was not found: {source}", "window_not_found")
        conflict = foreground_handoff_conflict(
            execution_context,
            hwnd,
        ) if active_source else None
        if conflict:
            raise _failure(
                str(conflict.get("reason") or "control surface handoff failed"),
                str(conflict.get("failure_type") or "control_surface_handoff_failed"),
                execution_context=execution_context,
                **{
                    key: value for key, value in conflict.items()
                    if key not in {"reason", "failure_type"}
                },
            )
        title = next(
            (
                str(item.get("title") or "")
                for item in win32.list_windows()
                if int(item.get("hwnd") or 0) == hwnd
            ),
            source,
        )
        if focus:
            focus_started = time.monotonic()
            self._focus_target(
                hwnd=hwnd, title=title, task=contract.task,
                task_intent=contract.intent,
                run_id=context["run_id"], stop_event=stop_event,
            )
            controller_elapsed_ms = int(
                (time.monotonic() - focus_started) * 1000
            )
        elif self._foreground() != hwnd:
            # A window-region desktop capture is not authoritative when another
            # window can cover it.  Do not turn occluder pixels into task state.
            raise _failure(
                "target lost foreground ownership before authoritative capture",
                "focus_lost",
                target_hwnd=hwnd,
                foreground_hwnd=self._foreground(),
            )
        capture_started = time.monotonic()
        frame_region = window_region(hwnd).clamp_to_screen()
        if frame_region.width < 80 or frame_region.height < 60:
            raise _failure("target window has no usable visible geometry", "window_not_found")
        settle_result: dict[str, Any] = {}
        settle_elapsed_ms = 0
        if focus and not state.get("baseline_artifact_id") and not stop_event.is_set():
            # The first title/foreground signal can precede the final client
            # repaint.  A short, non-blocking local settle gate costs hundreds
            # of milliseconds but prevents spending another full model call on
            # a plan whose source pixels were still loading.  Animated/live
            # surfaces are allowed through after the bounded timeout.
            settle_started = time.monotonic()
            settle_result = ScreenWatcher(
                fps=12.0,
                pixel_thresh=18,
            ).wait_for_region_stable(
                frame_region,
                stable_ms=150,
                timeout_s=0.8,
                max_ratio=0.0025,
            )
            settle_elapsed_ms = int((time.monotonic() - settle_started) * 1000)
        client_region = image_capture.window_client_region(hwnd)
        client_bounds = (
            image_capture.region_frame_bounds(client_region, frame_region)
            if client_region is not None else None
        )
        frame_id = int(state.get("frame_id") or 0) + 1
        artifact = store.capture_observation(
            scope="window",
            source_hwnd=hwnd,
            source_window=title,
            metadata={
                "session_id": state.get("session_id"),
                "frame_id": frame_id,
                "frame_region": _rect(frame_region),
                "window_client_frame_bounds": client_bounds,
            },
        )
        primary_modality = str(
            contract.session.primary_action_modality
            if contract.session is not None
            else contract.intent.interaction.primary_action_modality
        ).casefold()
        detected_surface = None
        if primary_modality == "draw":
            detected_surface = image_capture.credible_work_surface_frame_bounds(
                base64.b64encode(Path(artifact.managed_path).read_bytes()).decode(),
                client_frame_bounds=client_bounds,
            )
        target_binding = {
            "hwnd": hwnd,
            "title": title,
            "source": source,
            "capture_scope": "target_window",
            "frame_region": _rect(frame_region),
            "client_region": _rect(client_region) if client_region is not None else None,
            "client_frame_bounds": client_bounds,
            "foreground_verified": self._foreground() == hwnd,
            "binding_source": (
                "execution_context_target"
                if active_source and int(
                    execution_context.get("target_window", {}).get("hwnd") or 0
                ) == hwnd
                else "runtime_resolution"
            ),
        }
        state.update({
            "status": "observed",
            "frame_id": frame_id,
            "target_binding": target_binding,
            "baseline_artifact_id": artifact.artifact_id,
            "current_artifact_id": artifact.artifact_id,
            "detected_surface": detected_surface,
            "last_capture_timing": {
                "controller_elapsed_ms": controller_elapsed_ms + settle_elapsed_ms,
                "capture_elapsed_ms": int(
                    (time.monotonic() - capture_started) * 1000
                ),
                "initial_settle_elapsed_ms": settle_elapsed_ms,
                "initial_settle_stable": settle_result.get("stable"),
                "total_elapsed_ms": int(
                    (time.monotonic() - operation_started) * 1000
                ),
            },
        })
        if not isinstance(state.get("surface_region"), dict):
            authority = client_bounds or [0, 0, 1000, 1000]
            state["surface_region"] = {
                "bounds": list(authority),
                "coordinate_space": "frame_0_1000",
                "label": "window_client_surface_authority",
                "source": "win32_client_rect" if client_bounds else "target_window",
                "confidence": 1.0 if client_bounds else 0.7,
                "mutation_authorized": False,
            }
        if not isinstance(state.get("interaction_region"), dict):
            state["interaction_region"] = {
                **dict(state["surface_region"]),
                "label": "legacy_initial_interaction_authority",
            }
        BUS.publish(
            "interaction_frame",
            run_id=context["run_id"],
            session_id=state.get("session_id"),
            frame_id=frame_id,
            artifact_id=artifact.artifact_id,
            target_binding=target_binding,
            surface_region=state.get("surface_region"),
            interaction_region=state.get("interaction_region"),
        )
        return artifact

    def _capture_authoritative_delta(
        self,
        *,
        context: dict[str, Any],
        contract: TaskContract,
        store: ArtifactStore,
        stop_event: Any,
        baseline: Artifact,
        source: str,
        bounds: list[float] | tuple[float, float, float, float],
    ) -> tuple[Artifact, dict[str, Any]]:
        """Capture once and compare against the prior coordinate authority.

        This gate is shared by no-action plans and external waits.  A no-op is
        safe only when both the target authority and the relevant pixels are
        still equivalent to the frame on which it was planned.  Comparison
        failures are treated as stale instead of silently accepting an
        unproved frame.
        """
        state = self._state(context, contract)
        baseline_target = _authority_signature(state.get("target_binding") or {})
        fresh = self._capture_scene(
            context=context,
            contract=contract,
            store=store,
            stop_event=stop_event,
            source=source,
            focus=False,
        )
        fresh_target = _authority_signature(state.get("target_binding") or {})
        authority_changed = baseline_target != fresh_target
        byte_identical = baseline.sha256 == fresh.sha256
        pixel_change: dict[str, Any] = {
            "changed": False,
            "byte_identical": byte_identical,
            "method": "sha256_identity" if byte_identical else "pending",
        }
        comparison_unavailable = False
        if not byte_identical:
            pixel_change = store.frame_change(
                baseline.artifact_id,
                fresh.artifact_id,
                target_region=_normalized_roi_pixels(bounds, fresh),
                pixel_threshold=18,
                min_change_ratio=0.0015,
                min_mean_delta=1.0,
            )
            comparison_unavailable = pixel_change.get("method") == "unavailable"
        delta = {
            "changed": bool(
                authority_changed
                or (
                    not byte_identical
                    and (
                        pixel_change.get("changed") is True
                        or comparison_unavailable
                    )
                )
            ),
            "baseline_artifact_id": baseline.artifact_id,
            "fresh_artifact_id": fresh.artifact_id,
            "baseline_sha256": baseline.sha256,
            "fresh_sha256": fresh.sha256,
            "baseline_authority": baseline_target,
            "fresh_authority": fresh_target,
            "authority_changed": authority_changed,
            "comparison_unavailable": comparison_unavailable,
            "pixel_change": pixel_change,
            "capture_elapsed_ms": int(
                (state.get("last_capture_timing") or {}).get(
                    "capture_elapsed_ms", 0,
                )
            ),
        }
        return fresh, delta

    def _ensure_usable_inference_frame(
        self,
        *,
        artifact: Artifact,
        context: dict[str, Any],
        contract: TaskContract,
        store: ArtifactStore,
        stop_event: Any,
    ) -> tuple[Artifact, dict[str, Any], int]:
        """Spend local capture time, never a model call, on a blank frame."""
        state = self._state(context, contract)
        target = state.get("target_binding") or {}
        client_bounds = target.get("client_frame_bounds")
        quality = _authoritative_frame_quality(artifact, client_bounds)
        if quality.get("usable") is True:
            return artifact, quality, 0

        started = time.monotonic()
        frame_region = target.get("frame_region") or {}
        if not all(key in frame_region for key in ("left", "top", "width", "height")):
            raise _failure(
                "authoritative inference frame has no usable client geometry",
                "interaction_frame_unusable",
                frame_quality=quality,
                model_calls=0,
                model_elapsed_ms=0,
                controller_elapsed_ms=0,
            )
        watch_bounds = (
            client_bounds
            if isinstance(client_bounds, list) and len(client_bounds) == 4
            else [0, 0, 1000, 1000]
        )
        watch_region = _normalized_roi_screen(watch_bounds, frame_region)
        delta = ScreenWatcher(fps=12.0, pixel_thresh=12).wait_for_region_change(
            watch_region,
            timeout_s=1.8,
            min_ratio=0.0015,
            stop_event=stop_event,
            require_persistent=True,
            stable_ms=140,
            stable_max_ratio=0.0015,
        )
        controller_elapsed_ms = int((time.monotonic() - started) * 1000)
        if stop_event.is_set():
            raise _failure(
                "run canceled while waiting for an authoritative visual frame",
                "interaction_canceled",
                model_calls=0,
                model_elapsed_ms=0,
                controller_elapsed_ms=controller_elapsed_ms,
            )
        refreshed = self._capture_scene(
            context=context,
            contract=contract,
            store=store,
            stop_event=stop_event,
            source=str(target.get("source") or contract.intent.source or "active_window"),
            focus=False,
        )
        refreshed_client_bounds = (
            (state.get("target_binding") or {}).get("client_frame_bounds")
            or client_bounds
        )
        refreshed_quality = _authoritative_frame_quality(
            refreshed, refreshed_client_bounds,
        )
        readiness = {
            "initial": quality,
            "refreshed": refreshed_quality,
            "visual_change_observed": bool(delta.changed),
            "visual_change_ratio": float(delta.ratio),
            "bounded_wait_ms": controller_elapsed_ms,
        }
        BUS.publish(
            "interaction_frame_readiness",
            run_id=context["run_id"],
            session_id=state.get("session_id"),
            artifact_id=refreshed.artifact_id,
            readiness=readiness,
        )
        if refreshed_quality.get("usable") is not True:
            raise _failure(
                "authoritative frame remained visually unusable after one bounded local recapture",
                "interaction_frame_unusable",
                frame_quality=readiness,
                artifact_id=refreshed.artifact_id,
                model_calls=0,
                model_elapsed_ms=0,
                controller_elapsed_ms=controller_elapsed_ms,
                capture_elapsed_ms=int(
                    (state.get("last_capture_timing") or {}).get("capture_elapsed_ms") or 0
                ),
            )
        return refreshed, readiness, controller_elapsed_ms

    def recover_failure(
        self,
        stage: TaskStage,
        contract: TaskContract,
        context: dict[str, Any],
        started: float,
        *,
        failure_type: str,
        recovery_action: str,
        recovery_reason: str,
        retryable: bool,
        failure_details: dict[str, Any] | None = None,
    ) -> NodeResult | None:
        """Turn a policy decision into a safe graph transition.

        Only recovery policies that explicitly require a new authoritative
        observation are executable here.  The old plan and uncertain action
        state are discarded before routing to ObserveScene, so recovery can
        never blindly replay a pointer mutation.
        """
        if not retryable or recovery_action not in {
            "llm_stage_fallback",
            "rebind_and_retry",
            "reobserve_then_replan_current_turn",
        }:
            return None
        if stage.type not in {
            StageType.OBSERVE_SCENE,
            StageType.INFER_INTERACTION,
            StageType.PERFORM_TURN,
            StageType.VERIFY_LOCAL_EFFECT,
        }:
            return None
        state = self._state(context, contract)
        self._release_pointer_session(
            context,
            state,
            reason=f"recovery:{failure_type}",
        )
        details = failure_details if isinstance(failure_details, dict) else {}
        audit_details = {
            key: details[key]
            for key in (
                "error", "local_change", "local_changes", "effect_regions",
                "semantic_verification", "gesture_realization",
                "expected_effect", "planned_actions", "failed_mutation",
            )
            if key in details
        }
        self._reserve_replan(
            state,
            contract,
            reason=f"{stage.type.value}:{failure_type}",
            activity=details,
        )
        self._binding(context).reset()
        rejected_plan = state.get("planned_action")
        rejected_actions = (
            list(rejected_plan.get("actions") or [])
            if isinstance(rejected_plan, dict) else []
        )
        state.update({
            "status": "recovery_reobserve_pending",
            "failure_stage": stage.id,
            "failure_type": failure_type,
            "recovery": {
                "action": recovery_action,
                "reason": recovery_reason,
                "resume_branch": "recover_reobserve",
            },
            "planned_action": None,
            "executed_action": None,
            "local_verification": None,
            "deferred_action_program": None,
            "last_failure": {
                "stage_type": stage.type.value,
                "failure_type": failure_type,
                "reason": str(details.get("error") or recovery_reason),
                "rejected_actions": rejected_actions[:4],
                "details": audit_details,
            },
            "budget": self._budget_snapshot(state, contract),
        })
        return NodeResult(
            ok=True,
            branch="recover_reobserve",
            output=self.stage_output(
                stage,
                started,
                status="recovering",
                failure_type=failure_type,
                recovery=dict(state["recovery"]),
                model_calls=int(details.get("model_calls") or 0),
                model_elapsed_ms=int(details.get("model_elapsed_ms") or 0),
                controller_elapsed_ms=int(
                    details.get("controller_elapsed_ms") or 0
                ),
                capture_elapsed_ms=int(details.get("capture_elapsed_ms") or 0),
                failure_details=audit_details,
                interaction_state=dict(state),
            ),
        )

    @staticmethod
    def _decision(state: dict[str, Any]) -> InteractionDecision:
        raw = state.get("planned_action")
        if not isinstance(raw, dict):
            raise _failure("interaction stage has no typed plan", "semantic_ambiguity")
        return InteractionDecision.model_validate(raw)

    def execute(
        self,
        stage: TaskStage,
        contract: TaskContract,
        context: dict[str, Any],
        stop_event: Any,
        started: float,
        *,
        store: ArtifactStore,
        registry: EvidenceRegistry,
    ) -> NodeResult:
        state = self._state(context, contract)
        params = stage.parameters

        if stage.type == StageType.OBSERVE_SCENE:
            source = str(params.get("source") or params.get("app") or contract.intent.source or "active_window")
            artifact = self._capture_scene(
                context=context, contract=contract, store=store,
                stop_event=stop_event, source=source, focus=True,
            )
            capture_timing = dict(state.get("last_capture_timing") or {})
            record = registry.record(
                "interaction.scene_bound",
                provider="bound_scene_observer",
                verified=bool(
                    state.get("target_binding", {}).get("foreground_verified")
                    and state.get("target_binding", {}).get("client_frame_bounds")
                ),
                stage_id=stage.id,
                artifact_id=artifact.artifact_id,
                metadata={
                    "frame_id": state["frame_id"],
                    "target_binding": state["target_binding"],
                    "surface_region": state["surface_region"],
                    "interaction_region": state["interaction_region"],
                    "sendable": False,
                },
            )
            if not record["verified"]:
                raise _failure(
                    "target window client authority could not be proved",
                    "target_binding_failed",
                    interaction_state=dict(state),
                )
            return NodeResult(output=self.stage_output(
                stage, started, artifact_id=artifact.artifact_id,
                model_calls=0,
                model_elapsed_ms=0,
                controller_elapsed_ms=int(
                    capture_timing.get("controller_elapsed_ms") or 0
                ),
                capture_elapsed_ms=int(
                    capture_timing.get("capture_elapsed_ms") or 0
                ),
                interaction_state=dict(state), evidence=record,
            ))

        if stage.type == StageType.INFER_INTERACTION:
            artifact = store.get(str(state.get("current_artifact_id") or ""))
            artifact, frame_readiness, readiness_elapsed_ms = (
                self._ensure_usable_inference_frame(
                    artifact=artifact,
                    context=context,
                    contract=contract,
                    store=store,
                    stop_event=stop_event,
                )
            )
            state["inference_frame_readiness"] = frame_readiness
            failed_no_effect = state.get("failed_no_effect_mutation")
            failed_evidence = _failed_no_effect_evidence(
                state, artifact, store=store,
            )
            state["failed_no_effect_evidence_check"] = failed_evidence
            if failed_evidence["repeats"]:
                # Re-observing an identical authoritative frame provides no
                # new information.  Asking the model for another plan here
                # only repeats latency and often repeats the same unsafe
                # pointer mutation.  Fail with the precise stage/evidence
                # reason before reserving a model call.
                raise _failure(
                    "the failed visual action was re-observed on an identical "
                    "authoritative frame; no new evidence permits another mutation",
                    "interaction_no_new_evidence",
                    failed_mutation=failed_no_effect,
                    failed_evidence=failed_evidence,
                    artifact_id=artifact.artifact_id,
                    frame_sha256=artifact.sha256,
                    frame_readiness=frame_readiness,
                    model_calls=0,
                    model_elapsed_ms=0,
                    controller_elapsed_ms=readiness_elapsed_ms,
                )
            prior = {
                key: state.get(key)
                for key in (
                    "turn_index", "verified_turn_history", "external_change",
                    "detected_surface", "surface_region", "interaction_region",
                    "last_failure", "pointer_hold",
                )
                if state.get(key) is not None
            }
            client_authority = (
                state.get("target_binding", {}).get("client_frame_bounds")
                if isinstance(state.get("target_binding"), dict) else None
            )
            if isinstance(client_authority, list) and len(client_authority) == 4:
                prior["window_client_authority"] = client_authority
            model_started = time.monotonic()
            model_calls = 0
            validation_attempts: list[dict[str, Any]] = []
            validation_feedback: dict[str, Any] | None = None
            while True:
                self._reserve_model_call(
                    state,
                    contract,
                    stage=StageType.INFER_INTERACTION.value,
                )
                model_calls += 1
                try:
                    decision = plan_interaction(
                        artifact.managed_path,
                        task=contract.task,
                        intent=contract.intent.interaction.model_dump(mode="json"),
                        frame_id=int(state["frame_id"]),
                        observation_artifact_id=artifact.artifact_id,
                        prior_state=prior,
                        immutable_roles=(
                            contract.session.role_assignments
                            if contract.session is not None
                            else contract.intent.interaction.role_assignments
                        ),
                        primary_action_modality=(
                            contract.session.primary_action_modality
                            if contract.session is not None
                            else contract.intent.interaction.primary_action_modality
                        ),
                        validation_feedback=validation_feedback,
                    )
                    # A typed requirement is part of the execution contract,
                    # so prove that a local current-pixel provider can satisfy
                    # it on this exact frame before advancing to PerformTurn.
                    # This read-only preflight turns planner/provider mismatch
                    # into one bounded same-frame schema correction instead of
                    # a late execution failure and a redundant re-observation.
                    if decision.resolution_requirements:
                        preview_target = state.get("target_binding") or {}
                        try:
                            _preview_decision, preview_grounding = (
                                refine_interaction_geometry(
                                    decision,
                                    artifact.managed_path,
                                    expected_size=(
                                        int(preview_target["frame_region"]["width"]),
                                        int(preview_target["frame_region"]["height"]),
                                    ),
                                    frame_region=dict(preview_target["frame_region"]),
                                )
                            )
                            preview_proofs = _local_resolution_requirement_proofs(
                                _preview_decision,
                                preview_grounding,
                            )
                            state["planning_geometry_preview"] = {
                                "verified": True,
                                "frame_id": int(state.get("frame_id") or 0),
                                "artifact_id": artifact.artifact_id,
                                "requirements": preview_proofs,
                                "refinements": list(
                                    preview_grounding.get("refinements") or []
                                ),
                            }
                        except (KeyError, TypeError, ValueError, VisualGeometryError) as exc:
                            raise InteractionPlanOutputError(
                                "current-frame geometry cannot satisfy the "
                                f"declared resolution requirement: {exc}. "
                                "Select an applicable current_frame_region_inventory "
                                "candidate or emit the typed locator supported by "
                                "the visible pixels; do not repeat coarse coordinates.",
                                code="local_geometry_requirement_unresolved",
                                raw_payload=decision.model_dump(mode="json"),
                                validation_errors=[{
                                    "location": ["resolution_requirements"],
                                    "type": "local_geometry_unresolved",
                                    "message": str(exc),
                                }],
                            ) from exc
                    break
                except InteractionPlanOutputError as exc:
                    feedback = exc.correction_feedback()
                    budget_remaining = bool(
                        contract.session is not None
                        and int(state.get("model_calls") or 0)
                        < int(contract.session.max_model_calls)
                    )
                    will_retry = bool(
                        exc.retryable
                        and model_calls < _MAX_SAME_FRAME_PLAN_ATTEMPTS
                        and budget_remaining
                    )
                    diagnostic = {
                        "attempt": model_calls,
                        "will_retry_same_frame": will_retry,
                        "frame_id": int(state.get("frame_id") or 0),
                        "artifact_id": artifact.artifact_id,
                        **feedback,
                    }
                    validation_attempts.append(diagnostic)
                    state["inference_validation_attempts"] = list(
                        validation_attempts
                    )
                    BUS.publish(
                        "interaction_plan_validation",
                        run_id=context["run_id"],
                        session_id=state.get("session_id"),
                        **diagnostic,
                    )
                    if will_retry:
                        validation_feedback = feedback
                        continue
                    if state.get("pointer_hold", {}).get("active") is True:
                        self._release_pointer_session(
                            context,
                            state,
                            reason="interaction_plan_invalid_while_held",
                        )
                    model_elapsed_ms = int(
                        (time.monotonic() - model_started) * 1000
                    )
                    raise _failure(
                        str(exc),
                        exc.failure_type,
                        plan_error_code=exc.code,
                        canonicalization_repairs=list(exc.repairs),
                        validation_attempts=validation_attempts,
                        frame_id=state.get("frame_id"),
                        artifact_id=artifact.artifact_id,
                        frame_readiness=frame_readiness,
                        model_calls=model_calls,
                        model_elapsed_ms=model_elapsed_ms,
                        controller_elapsed_ms=readiness_elapsed_ms,
                        interaction_state=dict(state),
                    ) from exc
                except Exception as exc:
                    if state.get("pointer_hold", {}).get("active") is True:
                        self._release_pointer_session(
                            context,
                            state,
                            reason="interaction_inference_failed_while_held",
                        )
                    model_elapsed_ms = int(
                        (time.monotonic() - model_started) * 1000
                    )
                    raise _failure(
                        f"interaction protocol inference failed: {exc}",
                        "interaction_inference_failed",
                        frame_id=state.get("frame_id"),
                        artifact_id=artifact.artifact_id,
                        frame_readiness=frame_readiness,
                        validation_attempts=validation_attempts,
                        model_calls=model_calls,
                        model_elapsed_ms=model_elapsed_ms,
                        controller_elapsed_ms=readiness_elapsed_ms,
                        interaction_state=dict(state),
                    ) from exc
            model_elapsed_ms = int((time.monotonic() - model_started) * 1000)
            canonicalization_repairs = list(decision.canonicalization_repairs)
            if validation_attempts:
                canonicalization_repairs.append(
                    "schema_validation_feedback_retry:accepted"
                )
            if decision.continuation == "blocked":
                if state.get("pointer_hold", {}).get("active") is True:
                    self._release_pointer_session(
                        context,
                        state,
                        reason="interaction_blocked_while_held",
                    )
                raise _failure(
                    "the authoritative frame did not ground a causal interaction: "
                    + (decision.blocked_reason or "; ".join(decision.ambiguity)),
                    "interaction_scene_blocked",
                    blocked_reason=decision.blocked_reason,
                    ambiguity=list(decision.ambiguity),
                    canonicalization_repairs=canonicalization_repairs,
                    frame_id=state.get("frame_id"),
                    artifact_id=artifact.artifact_id,
                    frame_readiness=frame_readiness,
                    model_calls=model_calls,
                    model_elapsed_ms=model_elapsed_ms,
                    controller_elapsed_ms=readiness_elapsed_ms,
                )
            expects_external_watch = bool(
                contract.session is not None
                and contract.session.wait_for_external_change
            )
            if expects_external_watch and not _persistent_plan_has_causal_continuation(
                decision,
            ):
                raise _failure(
                    "persistent interaction plan must wait for external change or declare a visible terminal state",
                    "interaction_plan_invalid",
                    canonicalization_repairs=canonicalization_repairs,
                    model_calls=model_calls,
                    model_elapsed_ms=model_elapsed_ms,
                    controller_elapsed_ms=readiness_elapsed_ms,
                )
            if (
                expects_external_watch
                and decision.continuation == "reobserve"
                and decision.agent_should_act
                and decision.actions
            ):
                canonicalization_repairs.append(
                    "continuation:post_action_reobserve_owned_by_stage_graph"
                )
            if not expects_external_watch and decision.continuation == "wait_external":
                raise _failure(
                    "bounded interaction plan cannot request an absent external-change watcher",
                    "interaction_plan_invalid",
                    canonicalization_repairs=canonicalization_repairs,
                    model_calls=model_calls,
                    model_elapsed_ms=model_elapsed_ms,
                    controller_elapsed_ms=readiness_elapsed_ms,
                )
            client_bounds = state.get("target_binding", {}).get("client_frame_bounds")
            if not isinstance(client_bounds, list) or len(client_bounds) != 4:
                raise _failure(
                    "target client authority was lost",
                    "target_binding_failed",
                    model_calls=model_calls,
                    model_elapsed_ms=model_elapsed_ms,
                    controller_elapsed_ms=readiness_elapsed_ms,
                )
            client = InteractionRegion(
                tuple(int(value) for value in client_bounds),
                coordinate_space="frame_0_1000",
                label="window_client_authority",
                source="win32_client_rect",
            )
            surface_candidate = decision.surface_object()
            if not client.contains(surface_candidate, tolerance=4):
                raise _failure(
                    "model-proposed stable surface escaped the target client area",
                    "interaction_region_invalid",
                    proposed_region=surface_candidate.to_dict(),
                    client_region=client.to_dict(),
                    model_calls=model_calls,
                    model_elapsed_ms=model_elapsed_ms,
                    controller_elapsed_ms=readiness_elapsed_ms,
                )
            target = state["target_binding"]
            binding_result = self._binding(context).update(
                surface_candidate,
                hwnd=int(target["hwnd"]),
                frame_region=(
                    int(target["frame_region"]["left"]),
                    int(target["frame_region"]["top"]),
                    int(target["frame_region"]["width"]),
                    int(target["frame_region"]["height"]),
                ),
                capture_scope="target_window",
                client_region=client,
                frame_id=int(state["frame_id"]),
                initial=self._binding(context).anchor is None,
            )
            if binding_result.policy == "layout_displacement_pending_confirmation":
                self._reserve_replan(
                    state,
                    contract,
                    reason="surface_layout_displacement_confirmation",
                    activity={
                        "model_calls": model_calls,
                        "model_elapsed_ms": model_elapsed_ms,
                        "controller_elapsed_ms": readiness_elapsed_ms,
                        "capture_elapsed_ms": int(
                            (state.get("last_capture_timing") or {}).get(
                                "capture_elapsed_ms", 0,
                            )
                        ),
                    },
                )
                state.update({
                    "status": "surface_rebind_pending_confirmation",
                    "planned_action": None,
                    "executed_action": None,
                    "failure_stage": "",
                    "surface_binding": binding_result.to_dict(),
                })
                record = registry.record(
                    "interaction.surface_rebind_pending",
                    provider="temporal_surface_binding",
                    verified=True,
                    stage_id=stage.id,
                    artifact_id=artifact.artifact_id,
                    metadata=binding_result.to_dict(),
                )
                return NodeResult(
                    ok=True,
                    branch="reobserve",
                    output=self.stage_output(
                        stage,
                        started,
                        model_calls=model_calls,
                        model_elapsed_ms=model_elapsed_ms,
                        controller_elapsed_ms=readiness_elapsed_ms,
                        interaction_state=dict(state),
                        evidence=record,
                    ),
                )
            if not binding_result.ok:
                raise _failure(
                    binding_result.blocked_reason or "interaction region could not be bound",
                    "interaction_region_invalid",
                    surface_binding=binding_result.to_dict(),
                    model_calls=model_calls,
                    model_elapsed_ms=model_elapsed_ms,
                    controller_elapsed_ms=readiness_elapsed_ms,
                )
            try:
                action_region = self._binding(context).declare_action_roi(
                    f"turn-{int(state.get('turn_index') or 0)}",
                    decision.region_object(),
                )
            except (RuntimeError, ValueError) as exc:
                raise _failure(
                    f"per-turn interaction ROI could not be bound: {exc}",
                    "interaction_region_invalid",
                    surface_binding=binding_result.to_dict(),
                    proposed_action_region=decision.region_object().to_dict(),
                    model_calls=model_calls,
                    model_elapsed_ms=model_elapsed_ms,
                    controller_elapsed_ms=readiness_elapsed_ms,
                ) from exc
            state.update({
                "status": "planned" if decision.agent_should_act else "deferred_external_turn",
                "turn_owner": decision.turn_owner,
                "planned_action": decision.model_dump(mode="json"),
                "surface_region": {
                    **binding_result.region.to_dict(),
                    "mutation_authorized": False,
                    "binding_policy": binding_result.policy,
                },
                "interaction_region": {
                    **action_region.to_dict(),
                    "mutation_authorized": decision.agent_should_act,
                    "surface_generation": binding_result.generation,
                },
                "budget": self._budget_snapshot(state, contract),
                "failure_stage": "",
                "inference_canonicalization_repairs": canonicalization_repairs,
                "inference_validation_attempts": list(validation_attempts),
                "visual_region_inventory": [
                    dict(item) for item in decision.region_inventory
                ],
            })
            state["deferred_action_program"] = _deferred_program(decision)
            if decision.agent_should_act:
                state["last_mutating_plan"] = decision.model_dump(mode="json")
            record = registry.record(
                "interaction.protocol_inferred",
                provider="interaction_protocol_inference",
                verified=True,
                stage_id=stage.id,
                artifact_id=artifact.artifact_id,
                metadata={
                    "frame_id": decision.frame_id,
                    "turn_owner": decision.turn_owner,
                    "agent_should_act": decision.agent_should_act,
                    "surface_region": state["surface_region"],
                    "interaction_region": state["interaction_region"],
                    "confidence": decision.confidence,
                    "rules": decision.rules,
                    "roles": decision.roles,
                    "primary_action_modality": (
                        contract.session.primary_action_modality
                        if contract.session is not None
                        else contract.intent.interaction.primary_action_modality
                    ),
                    "canonicalization_repairs": canonicalization_repairs,
                    "validation_attempts": list(validation_attempts),
                    "visual_region_inventory": [
                        dict(item) for item in decision.region_inventory
                    ],
                    "deferred_action_program": state.get(
                        "deferred_action_program"
                    ),
                    "frame_readiness": frame_readiness,
                },
            )
            BUS.publish(
                "interaction_plan",
                run_id=context["run_id"],
                session_id=state.get("session_id"),
                frame_id=decision.frame_id,
                turn_owner=decision.turn_owner,
                planned_action=decision.model_dump(mode="json"),
                validation_attempts=list(validation_attempts),
                visual_region_inventory=state.get("visual_region_inventory"),
                deferred_action_program=state.get("deferred_action_program"),
            )
            return NodeResult(output=self.stage_output(
                stage, started, model_calls=model_calls,
                model_elapsed_ms=model_elapsed_ms,
                controller_elapsed_ms=readiness_elapsed_ms,
                interaction_state=dict(state), evidence=record,
            ))

        if stage.type == StageType.PERFORM_TURN:
            decision = self._decision(state)
            if not decision.agent_should_act:
                if decision.continuation in {"reobserve", "blocked"}:
                    raise _failure(
                        "no-mutation plan has no causal executable continuation",
                        "interaction_plan_invalid",
                    )
                planned_frame_id = int(state.get("frame_id") or 0)
                planned_artifact_id = str(state.get("current_artifact_id") or "")
                try:
                    planned_artifact = store.get(decision.observation_artifact_id)
                except Exception as exc:
                    raise _failure(
                        "the no-action plan's observation artifact is unavailable",
                        "stale_authoritative_frame",
                        observation_artifact_id=decision.observation_artifact_id,
                    ) from exc
                target = state.get("target_binding") or {}
                source = str(
                    target.get("source")
                    or contract.intent.source
                    or "active_window"
                )
                fresh_artifact, revalidation = self._capture_authoritative_delta(
                    context=context,
                    contract=contract,
                    store=store,
                    stop_event=stop_event,
                    baseline=planned_artifact,
                    source=source,
                    bounds=decision.interaction_region,
                )
                stale_reasons: list[str] = []
                if decision.frame_id != planned_frame_id:
                    stale_reasons.append("state_frame_advanced_before_revalidation")
                if planned_artifact_id != decision.observation_artifact_id:
                    stale_reasons.append("state_artifact_changed_before_revalidation")
                if revalidation["authority_changed"]:
                    stale_reasons.append("window_or_client_geometry_changed")
                if revalidation["pixel_change"].get("changed") is True:
                    stale_reasons.append("authoritative_pixels_changed")
                if revalidation["comparison_unavailable"]:
                    stale_reasons.append("pixel_revalidation_unavailable")
                revalidation["stale_reasons"] = stale_reasons
                state["no_action_revalidation"] = revalidation
                if stale_reasons:
                    state.update({
                        "status": "no_action_plan_stale",
                        "stale_plan": decision.model_dump(mode="json"),
                        "planned_action": None,
                        "executed_action": None,
                        "failure_stage": "PerformTurn.revalidation",
                    })
                    if revalidation["authority_changed"]:
                        self._binding(context).reset()
                    record = registry.record(
                        "interaction.plan_revalidated",
                        provider="authoritative_no_action_preflight",
                        verified=True,
                        stage_id=stage.id,
                        artifact_id=fresh_artifact.artifact_id,
                        metadata={
                            **revalidation,
                            "recovery": "infer_from_fresh_authoritative_frame",
                        },
                    )
                    return NodeResult(
                        ok=True,
                        branch="reobserve",
                        output=self.stage_output(
                            stage,
                            started,
                            artifact_id=fresh_artifact.artifact_id,
                            model_calls=0,
                            model_elapsed_ms=0,
                            controller_elapsed_ms=0,
                            capture_elapsed_ms=revalidation["capture_elapsed_ms"],
                            interaction_state=dict(state),
                            evidence=record,
                        ),
                    )

                # Bind the no-action decision to the just-captured frame.  The
                # subsequent completion verifier or external watcher now has
                # a truthful authoritative baseline instead of the frame that
                # may have been several seconds old during inference.
                decision = decision.model_copy(update={
                    "frame_id": int(state.get("frame_id") or planned_frame_id),
                    "observation_artifact_id": fresh_artifact.artifact_id,
                })
                state["planned_action"] = decision.model_dump(mode="json")
                reason = (
                    "bounded goal is claimed complete without further mutation"
                    if decision.continuation == "finish"
                    else "external participant owns the current turn"
                )
                state.update({
                    "status": (
                        "completion_pending_verification"
                        if decision.continuation == "finish"
                        else "deferred_external_turn"
                    ),
                    "executed_action": {
                        "deferred": True,
                        "reason": reason,
                        "frame_id": decision.frame_id,
                        "artifact_id": fresh_artifact.artifact_id,
                        "continuation": decision.continuation,
                    },
                })
                record = registry.record(
                    "interaction.turn_resolved",
                    provider="bound_interaction_transaction",
                    verified=True,
                    stage_id=stage.id,
                    metadata=state["executed_action"],
                )
                return NodeResult(output=self.stage_output(
                    stage, started, controller_elapsed_ms=0,
                    capture_elapsed_ms=revalidation["capture_elapsed_ms"],
                    artifact_id=fresh_artifact.artifact_id,
                    interaction_state=dict(state), evidence=record,
                ))

            if (
                contract.session is not None
                and contract.session.lifecycle == "persistent"
                and int(state.get("agent_turns_since_external_change") or 0)
                >= int(contract.session.max_agent_turns_per_activation)
            ):
                raise _failure(
                    "another agent mutation is forbidden until an independent external change is proved",
                    "interaction_external_change_required",
                    agent_turns_since_external_change=int(
                        state.get("agent_turns_since_external_change") or 0
                    ),
                    max_agent_turns_between_external_changes=int(
                        contract.session.max_agent_turns_per_activation
                    ),
                )
            self._assert_mutation_budget(state, contract)
            target = state.get("target_binding") or {}
            hwnd = int(target.get("hwnd") or 0)
            if not hwnd or self._foreground() != hwnd:
                raise _failure(
                    "target lost foreground ownership before interaction transaction",
                    "focus_lost",
                    target_hwnd=hwnd,
                    foreground_hwnd=self._foreground(),
                )

            # Re-prove the exact coordinate authority immediately before any
            # mutation.  Inference may take seconds; a same-hwnd window can be
            # moved/resized or its pixels can change during that time.  Raw
            # coordinates from the older frame are never mapped onto the new
            # state.  A changed target is recovered by capturing the fresh
            # managed observation and returning to typed inference.
            planned_state_frame_id = int(state.get("frame_id") or 0)
            planned_state_artifact_id = str(state.get("current_artifact_id") or "")
            planned_target = _authority_signature(target)
            preflight_started = time.monotonic()
            try:
                planned_artifact = store.get(decision.observation_artifact_id)
            except Exception as exc:
                raise _failure(
                    "the interaction plan's observation artifact is unavailable",
                    "stale_authoritative_frame",
                    observation_artifact_id=decision.observation_artifact_id,
                ) from exc
            source = str(target.get("source") or contract.intent.source or "active_window")
            fresh_artifact = self._capture_scene(
                context=context,
                contract=contract,
                store=store,
                stop_event=stop_event,
                source=source,
                focus=False,
            )
            capture_elapsed_ms = int(
                (state.get("last_capture_timing") or {}).get(
                    "capture_elapsed_ms", 0,
                )
            )
            fresh_target = state.get("target_binding") or {}
            fresh_authority = _authority_signature(fresh_target)
            stale_reasons: list[str] = []
            if decision.frame_id != planned_state_frame_id:
                stale_reasons.append("state_frame_advanced_before_preflight")
            if planned_state_artifact_id != decision.observation_artifact_id:
                stale_reasons.append("state_artifact_changed_before_preflight")
            if planned_target != fresh_authority:
                stale_reasons.append("window_or_client_geometry_changed")
            pixel_change: dict[str, Any] = {
                "byte_identical": planned_artifact.sha256 == fresh_artifact.sha256,
                "policy": "source-target-path-grounding-delta",
                # Compatibility keys remain trace-readable, but the broad
                # per-turn/effect surface is intentionally not a stale-plan
                # predicate.
                "interaction_layout": {
                    "method": "not_checked",
                    "changed": False,
                    "change_ratio": 0.0,
                    "mean_pixel_delta": 0.0,
                    "reason": "preflight is restricted to action grounding",
                },
            }
            if not pixel_change["byte_identical"]:
                grounding_regions = _action_grounding_regions(decision)
                action_changes = [
                    store.frame_change(
                        planned_artifact.artifact_id,
                        fresh_artifact.artifact_id,
                        target_region=_normalized_roi_pixels(bounds, fresh_artifact),
                        pixel_threshold=18,
                        min_change_ratio=0.035,
                        min_mean_delta=2.0,
                    )
                    for bounds in grounding_regions
                ]
                action_change = (
                    max(
                        action_changes,
                        key=lambda item: (
                            item.get("method") != "unavailable",
                            bool(item.get("changed")),
                            float(item.get("change_ratio") or 0.0),
                            float(item.get("mean_pixel_delta") or 0.0),
                        ),
                    )
                    if action_changes else {
                        "method": "authority_only",
                        "changed": False,
                        "change_ratio": 0.0,
                        "mean_pixel_delta": 0.0,
                        "reason": "planned actions declared no spatial grounding ROI",
                    }
                )
                pixel_change.update({
                    "action_guard": action_change,
                    "action_guards": action_changes,
                    "grounding_regions": grounding_regions,
                })
                if any(
                    item.get("method") == "unavailable"
                    for item in action_changes
                ):
                    stale_reasons.append("pixel_preflight_unavailable")
                elif any(item.get("changed") is True for item in action_changes):
                    stale_reasons.append("authoritative_pixels_changed")
            else:
                pixel_change.update({
                    "action_guard": {
                        "method": "byte_identical",
                        "changed": False,
                        "change_ratio": 0.0,
                        "mean_pixel_delta": 0.0,
                    },
                    "action_guards": [],
                    "grounding_regions": _action_grounding_regions(decision),
                })
            preflight = {
                "verified": not stale_reasons,
                "planned_frame_id": decision.frame_id,
                "fresh_frame_id": int(state.get("frame_id") or 0),
                "planned_artifact_id": decision.observation_artifact_id,
                "fresh_artifact_id": fresh_artifact.artifact_id,
                "planned_sha256": planned_artifact.sha256,
                "fresh_sha256": fresh_artifact.sha256,
                "pixel_change": pixel_change,
                "planned_authority": planned_target,
                "fresh_authority": fresh_authority,
                "stale_reasons": stale_reasons,
            }
            state["action_preflight"] = preflight
            if stale_reasons:
                self._reserve_replan(
                    state,
                    contract,
                    reason="authoritative_action_preflight_stale",
                )
                stale_plan = decision.model_dump(mode="json")
                state.update({
                    "status": "stale_plan_reobserve",
                    "stale_plan": stale_plan,
                    "planned_action": None,
                    "executed_action": None,
                    "failure_stage": "PerformTurn.preflight",
                })
                if "window_or_client_geometry_changed" in stale_reasons:
                    self._binding(context).reset()
                record = registry.record(
                    "interaction.plan_revalidated",
                    provider="authoritative_action_preflight",
                    verified=True,
                    stage_id=stage.id,
                    artifact_id=fresh_artifact.artifact_id,
                    metadata={
                        **preflight,
                        "recovery": "reobserve_from_fresh_authoritative_frame",
                    },
                )
                BUS.publish(
                    "interaction_plan_stale",
                    run_id=context["run_id"],
                    session_id=state.get("session_id"),
                    action_preflight=preflight,
                )
                return NodeResult(
                    ok=True,
                    branch="reobserve",
                    output=self.stage_output(
                        stage,
                        started,
                        artifact_id=fresh_artifact.artifact_id,
                        controller_elapsed_ms=0,
                        capture_elapsed_ms=capture_elapsed_ms,
                        preflight_elapsed_ms=int(
                            (time.monotonic() - preflight_started) * 1000
                        ),
                        interaction_state=dict(state),
                        evidence=record,
                    ),
                )

            # The new managed frame is equivalent under the bounded drift
            # policy and has the same window/client authority.  Rebind the
            # typed plan so the controller's epoch guard remains truthful.
            decision = decision.model_copy(update={
                "frame_id": int(state["frame_id"]),
                "observation_artifact_id": fresh_artifact.artifact_id,
            })
            preparation_actions = controller_preparation_actions(decision)
            preparation_result: dict[str, Any] = {}
            preparation_elapsed_ms = 0
            preparation_model_calls = 0
            preparation_model_elapsed_ms = 0
            input_mode_evidence: list[dict[str, Any]] = []
            color_preparation_evidence: list[dict[str, Any]] = []
            if preparation_actions:
                preparation_session = ToolSession(
                    stop_event=stop_event,
                    task=contract.task,
                    runtime_profile=RuntimeProfile(desktop=True, coding=False),
                    task_intent=contract.intent,
                    communication_context={"active": False},
                )
                preparation_session.run_id = context["run_id"]
                preparation_started = time.monotonic()
                try:
                    preparation_session._remember_target(
                        hwnd, str(target.get("title") or ""),
                    )
                    preparation_session._visual_frame_id = int(state["frame_id"])
                    preparation_session._visual_frame_epoch = int(state["frame_id"])
                    preparation_session._screen_epoch = int(state["frame_id"])
                    preparation_session._visual_frame_scope = "target_window"
                    preparation_session._visual_frame_region = dict(
                        target["frame_region"],
                    )
                    preparation_result = _json(
                        preparation_session.dispatch("run_action_batch", {
                            "actions": preparation_actions,
                            "stop_on_error": True,
                            "subgoal": (
                                "establish typed input preconditions before "
                                "mapping any spatial mutation"
                            ),
                            "sync": {
                                "wait_stable": True,
                                "timeout_s": 3.0,
                            },
                        }).text,
                    )
                finally:
                    preparation_session.close()
                preparation_elapsed_ms = int(
                    (time.monotonic() - preparation_started) * 1000,
                )
                for item in preparation_result.get("results") or []:
                    if not isinstance(item, dict) or not isinstance(
                        item.get("data"), dict,
                    ):
                        continue
                    data = dict(item["data"])
                    try:
                        preparation_model_calls += int(
                            data.get("model_calls") or 0,
                        )
                        preparation_model_elapsed_ms += int(
                            data.get("model_elapsed_ms") or 0,
                        )
                    except (TypeError, ValueError):
                        pass
                    if item.get("action") == "ensure_input_mode":
                        input_mode_evidence.append(data)
                    elif item.get("action") == "select_color":
                        color_preparation_evidence.append(data)
                if preparation_result.get("ok") is not True:
                    raise _failure(
                        str(
                            preparation_result.get("error")
                            or "input preparation transaction failed"
                        ),
                        str(
                            preparation_result.get("failure_type")
                            or "input_preparation_failed"
                        ),
                        preparation_actions=preparation_actions,
                        preparation_result=preparation_result,
                        model_calls=preparation_model_calls,
                        model_elapsed_ms=preparation_model_elapsed_ms,
                        controller_elapsed_ms=preparation_elapsed_ms,
                        capture_elapsed_ms=capture_elapsed_ms,
                    )
                required_input_modes = {
                    "continuous_freehand"
                    for item in decision.actions
                    if item.action == "draw_strokes"
                    and item.interaction_mechanism == "freehand_strokes"
                }
                proven_input_modes = {
                    str(item.get("input_mode") or "")
                    for item in input_mode_evidence
                    if item.get("verified") is True
                }
                if not required_input_modes <= proven_input_modes:
                    raise _failure(
                        "input preparation did not prove its typed mode precondition",
                        "input_mode_precondition_unproven",
                        required_input_modes=sorted(required_input_modes),
                        proven_input_modes=sorted(proven_input_modes),
                        input_mode_evidence=input_mode_evidence,
                        preparation_result=preparation_result,
                        model_calls=preparation_model_calls,
                        model_elapsed_ms=preparation_model_elapsed_ms,
                        controller_elapsed_ms=preparation_elapsed_ms,
                        capture_elapsed_ms=capture_elapsed_ms,
                    )

                # Mode/colour setup is itself a UI mutation.  Never reuse the
                # pre-setup frame's physical geometry for the subsequent
                # gesture.  Capture a managed post-setup frame, prove the same
                # target authority and unchanged action-grounding pixels, then
                # rebind local geometry without another planner call.
                preparation_baseline = fresh_artifact
                preparation_authority = _authority_signature(
                    state.get("target_binding") or {},
                )
                prepared_artifact = self._capture_scene(
                    context=context,
                    contract=contract,
                    store=store,
                    stop_event=stop_event,
                    source=source,
                    focus=False,
                )
                preparation_capture_elapsed_ms = int(
                    (state.get("last_capture_timing") or {}).get(
                        "capture_elapsed_ms", 0,
                    )
                )
                capture_elapsed_ms += preparation_capture_elapsed_ms
                prepared_target = state.get("target_binding") or {}
                prepared_authority = _authority_signature(prepared_target)
                preparation_regions = _action_grounding_regions(decision)
                preparation_changes = [
                    store.frame_change(
                        preparation_baseline.artifact_id,
                        prepared_artifact.artifact_id,
                        target_region=_normalized_roi_pixels(
                            bounds, prepared_artifact,
                        ),
                        pixel_threshold=18,
                        min_change_ratio=0.035,
                        min_mean_delta=2.0,
                    )
                    for bounds in preparation_regions
                ]
                preparation_stale_reasons: list[str] = []
                if preparation_authority != prepared_authority:
                    preparation_stale_reasons.append(
                        "window_or_client_geometry_changed",
                    )
                if any(
                    item.get("method") == "unavailable"
                    for item in preparation_changes
                ):
                    preparation_stale_reasons.append(
                        "post_preparation_pixel_check_unavailable",
                    )
                elif any(
                    item.get("changed") is True
                    for item in preparation_changes
                ):
                    preparation_stale_reasons.append(
                        "action_grounding_changed_during_preparation",
                    )
                preparation_transition = {
                    "verified": not preparation_stale_reasons,
                    "preparation_actions": preparation_actions,
                    "controller_result": preparation_result,
                    "planned_frame_id": decision.frame_id,
                    "prepared_frame_id": int(state.get("frame_id") or 0),
                    "planned_artifact_id": preparation_baseline.artifact_id,
                    "prepared_artifact_id": prepared_artifact.artifact_id,
                    "planned_authority": preparation_authority,
                    "prepared_authority": prepared_authority,
                    "grounding_regions": preparation_regions,
                    "grounding_changes": preparation_changes,
                    "stale_reasons": preparation_stale_reasons,
                    "model_calls": preparation_model_calls,
                    "model_elapsed_ms": preparation_model_elapsed_ms,
                    "controller_elapsed_ms": preparation_elapsed_ms,
                    "capture_elapsed_ms": preparation_capture_elapsed_ms,
                }
                state["input_preparation"] = preparation_transition
                if preparation_stale_reasons:
                    self._reserve_replan(
                        state,
                        contract,
                        reason="post_input_preparation_frame_stale",
                        activity={
                            "model_calls": preparation_model_calls,
                            "model_elapsed_ms": preparation_model_elapsed_ms,
                            "controller_elapsed_ms": preparation_elapsed_ms,
                            "capture_elapsed_ms": capture_elapsed_ms,
                        },
                    )
                    state.update({
                        "status": "input_prepared_reobserve",
                        "stale_plan": decision.model_dump(mode="json"),
                        "planned_action": None,
                        "executed_action": None,
                        "failure_stage": "PerformTurn.input_preparation",
                    })
                    if "window_or_client_geometry_changed" in preparation_stale_reasons:
                        self._binding(context).reset()
                    record = registry.record(
                        "interaction.plan_revalidated",
                        provider="post_input_preparation_frame",
                        verified=True,
                        stage_id=stage.id,
                        artifact_id=prepared_artifact.artifact_id,
                        metadata={
                            **preparation_transition,
                            "recovery": "infer_from_fresh_authoritative_frame",
                        },
                    )
                    return NodeResult(
                        ok=True,
                        branch="reobserve",
                        output=self.stage_output(
                            stage,
                            started,
                            artifact_id=prepared_artifact.artifact_id,
                            model_calls=preparation_model_calls,
                            model_elapsed_ms=preparation_model_elapsed_ms,
                            controller_elapsed_ms=preparation_elapsed_ms,
                            capture_elapsed_ms=capture_elapsed_ms,
                            interaction_state=dict(state),
                            evidence=record,
                        ),
                    )
                fresh_artifact = prepared_artifact
                target = prepared_target
                decision = decision.model_copy(update={
                    "frame_id": int(state["frame_id"]),
                    "observation_artifact_id": prepared_artifact.artifact_id,
                })
            requires_spatial_lineage = any(
                bool(action.normalized_points()) for action in decision.actions
            )
            frame_lineage: dict[str, Any] | None = None
            if requires_spatial_lineage:
                try:
                    # Re-open through ArtifactStore immediately before geometry
                    # decoding so hash, byte size, and dimensions are checked
                    # against the immutable registration record.
                    fresh_artifact = store.get(fresh_artifact.artifact_id)
                    frame_lineage = _prove_artifact_frame_lineage(
                        fresh_artifact,
                        target=target,
                        state=state,
                        run_id=context["run_id"],
                    )
                except ValueError as exc:
                    raise _failure(
                        str(exc),
                        "visual_frame_lineage_invalid",
                        artifact_id=fresh_artifact.artifact_id,
                        frame_id=int(state["frame_id"]),
                        model_calls=0,
                        model_elapsed_ms=0,
                        controller_elapsed_ms=0,
                        capture_elapsed_ms=capture_elapsed_ms,
                    ) from exc
            try:
                decision, geometry_grounding = refine_interaction_geometry(
                    decision,
                    fresh_artifact.managed_path,
                    expected_size=(
                        int(target["frame_region"]["width"]),
                        int(target["frame_region"]["height"]),
                    ),
                    frame_region=dict(target["frame_region"]),
                )
                resolution_proofs = _local_resolution_requirement_proofs(
                    decision,
                    geometry_grounding,
                )
                geometry_grounding["resolution_requirements"] = resolution_proofs
            except VisualGeometryError as exc:
                raise _failure(
                    f"typed spatial plan has no unique current-pixel grounding: {exc}",
                    "visual_geometry_ungrounded",
                    artifact_id=fresh_artifact.artifact_id,
                    frame_id=int(state["frame_id"]),
                    planned_actions=[
                        item.model_dump(mode="json") for item in decision.actions
                    ],
                    action_preflight=preflight,
                    model_calls=0,
                    model_elapsed_ms=0,
                    controller_elapsed_ms=0,
                    capture_elapsed_ms=capture_elapsed_ms,
                ) from exc
            geometry_grounding["frame_lineage"] = frame_lineage
            preflight["grounding_proof"] = geometry_grounding
            state.update({
                "status": "plan_revalidated",
                "planned_action": decision.model_dump(mode="json"),
                "last_mutating_plan": decision.model_dump(mode="json"),
                "visual_geometry_grounding": geometry_grounding,
                "failure_stage": "",
            })
            if geometry_grounding.get("applied") is True:
                grounding_record = registry.record(
                    "interaction.spatial_grounding_resolved",
                    provider="local_visual_geometry",
                    verified=True,
                    stage_id=stage.id,
                    artifact_id=fresh_artifact.artifact_id,
                    metadata=geometry_grounding,
                )
                BUS.publish(
                    "interaction_spatial_grounding",
                    run_id=context["run_id"],
                    session_id=state.get("session_id"),
                    frame_id=int(state["frame_id"]),
                    grounding=geometry_grounding,
                    evidence_id=grounding_record["id"],
                )
            bound = self._binding(context).anchor
            if bound is None:
                raise _failure("interaction surface binding was lost", "interaction_region_invalid")
            actions = controller_actions(
                decision,
                frame_region=target["frame_region"],
                bound_region=bound,
                frame_epoch=int(state["frame_id"]),
                include_preparation=False,
            )
            if not any(item.get("action") != "wait_stable" for item in actions):
                raise _failure("semantic turn contained no mutation", "interaction_action_empty")
            existing_pointer_session = context.get(
                "_interaction_pointer_session"
            )
            if not isinstance(existing_pointer_session, ToolSession):
                existing_pointer_session = _registered_pointer_session(
                    context,
                )
            session = (
                existing_pointer_session
                if isinstance(existing_pointer_session, ToolSession)
                else ToolSession(
                    stop_event=stop_event,
                    task=contract.task,
                    runtime_profile=RuntimeProfile(
                        desktop=True, coding=False,
                    ),
                    task_intent=contract.intent,
                    communication_context={"active": False},
                )
            )
            session.run_id = context["run_id"]
            controller_started = time.monotonic()
            keep_pointer_session = False
            result: dict[str, Any] = {}
            try:
                session._remember_target(hwnd, str(target.get("title") or ""))
                session._visual_frame_id = int(state["frame_id"])
                session._visual_frame_epoch = int(state["frame_id"])
                session._screen_epoch = int(state["frame_id"])
                session._visual_frame_scope = "target_window"
                session._visual_frame_region = dict(target["frame_region"])
                batch_args = {
                    "actions": actions,
                    "stop_on_error": True,
                    "subgoal": decision.expected_effect.description,
                    "sync": {"wait_stable": True, "timeout_s": 5.0},
                }
                if decision.pointer_hold_before or decision.pointer_hold_after:
                    batch_args["pointer_session"] = {
                        "before": decision.pointer_hold_before,
                        "after": decision.pointer_hold_after,
                    }
                result = _json(session.dispatch(
                    "run_action_batch", batch_args,
                ).text)
                # Production ToolSession exposes the physical pointer state.
                # Lightweight test/embedding shims created before held-pointer
                # support may not; treating those as released preserves strict
                # failure for a contracted hold without breaking ordinary
                # click/drag transactions.
                hold_status_reader = getattr(
                    session, "_pointer_hold_status", None,
                )
                hold_status = (
                    hold_status_reader()
                    if callable(hold_status_reader)
                    else {"active": False, "button": ""}
                )
                expected_hold = decision.pointer_hold_after
                if expected_hold:
                    if not (
                        hold_status.get("active") is True
                        and hold_status.get("button") == expected_hold
                    ):
                        result = {
                            **result,
                            "ok": False,
                            "failure_type": "pointer_hold_lost",
                            "error": (
                                "Controller did not retain the contracted "
                                "held pointer across the fresh-frame boundary"
                            ),
                        }
                    elif result.get("ok") is True:
                        keep_pointer_session = True
                        context["_interaction_pointer_session"] = session
                        _register_pointer_session(context, session)
                elif hold_status.get("active") is True:
                    result = {
                        **result,
                        "ok": False,
                        "failure_type": "pointer_release_missing",
                        "error": (
                            "Controller left a mouse button held after the "
                            "typed decision required release"
                        ),
                    }
            finally:
                if not keep_pointer_session:
                    context.pop("_interaction_pointer_session", None)
                    _discard_registered_pointer_session(context, session)
                    session.close()
            state["pointer_hold"] = {
                "active": bool(keep_pointer_session),
                "button": decision.pointer_hold_after if keep_pointer_session else "",
                "since_frame_id": (
                    int(state.get("frame_id") or 0)
                    if keep_pointer_session else 0
                ),
            }
            controller_elapsed_ms = preparation_elapsed_ms + int(
                (time.monotonic() - controller_started) * 1000,
            )
            if result.get("ok") is not True:
                failure_type = str(
                    result.get("failure_type") or "interaction_action_failed"
                )
                if _controller_visual_no_effect(result):
                    failure_type = "interaction_action_no_effect"
                    state["failed_no_effect_mutation"] = {
                        "frame_id": int(state["frame_id"]),
                        "artifact_id": fresh_artifact.artifact_id,
                        "frame_sha256": fresh_artifact.sha256,
                        "action_signature": _mutation_signature(decision),
                        "relevant_regions": [
                            list(bounds) for bounds in dict.fromkeys(
                                tuple(float(value) for value in bounds)
                                for bounds in (
                                    _action_grounding_regions(decision)
                                    + _effect_regions(decision)
                                )
                            )
                        ],
                        "grounding_provider": geometry_grounding.get("provider"),
                        "failure_type": failure_type,
                    }
                raise _failure(
                    str(result.get("error") or "interaction transaction failed"),
                    failure_type,
                    controller_result=result,
                    planned_actions=[item.model_dump(mode="json") for item in decision.actions],
                    model_calls=0,
                    model_elapsed_ms=0,
                    controller_elapsed_ms=controller_elapsed_ms,
                    capture_elapsed_ms=capture_elapsed_ms,
                )
            if input_mode_evidence:
                state["input_mode_evidence"] = input_mode_evidence
                for evidence in input_mode_evidence:
                    mode_record = registry.record(
                        "interaction.input_mode_prepared",
                        provider=str(evidence.get("provider") or "controller_input_mode"),
                        verified=evidence.get("verified") is True,
                        stage_id=stage.id,
                        artifact_id=fresh_artifact.artifact_id,
                        metadata=evidence,
                    )
                    BUS.publish(
                        "interaction_input_mode_prepared",
                        run_id=context["run_id"],
                        session_id=state.get("session_id"),
                        frame_id=int(state["frame_id"]),
                        input_mode=evidence,
                        evidence_id=mode_record["id"],
                    )
            state.update({
                "status": "turn_executed",
                "executed_action": {
                    "deferred": False,
                    "frame_id": decision.frame_id,
                    "actions": [item.model_dump(mode="json") for item in decision.actions],
                    "input_preparation": preparation_result,
                    "color_preparation_evidence": color_preparation_evidence,
                    "controller_result": result,
                    "pointer_hold": dict(state.get("pointer_hold") or {}),
                    "expected_effect": decision.expected_effect.model_dump(mode="json"),
                },
            })
            state.pop("failed_no_effect_mutation", None)
            state["last_executed_action"] = dict(state["executed_action"])
            record = registry.record(
                "interaction.turn_resolved",
                provider="bound_interaction_transaction",
                verified=True,
                stage_id=stage.id,
                artifact_id=decision.observation_artifact_id,
                metadata={
                    "frame_id": decision.frame_id,
                    "action_count": len(preparation_actions) + len(actions),
                    "preparation_action_count": len(preparation_actions),
                    "preparation_result": preparation_result,
                    "controller_result": result,
                    "expected_effect": decision.expected_effect.model_dump(mode="json"),
                    "action_preflight": preflight,
                },
            )
            BUS.publish(
                "interaction_action",
                run_id=context["run_id"],
                session_id=state.get("session_id"),
                executed_action=state["executed_action"],
            )
            return NodeResult(output=self.stage_output(
                stage, started,
                model_calls=preparation_model_calls,
                model_elapsed_ms=preparation_model_elapsed_ms,
                controller_elapsed_ms=controller_elapsed_ms,
                capture_elapsed_ms=capture_elapsed_ms,
                preflight_elapsed_ms=int(
                    (controller_started - preflight_started) * 1000
                ),
                interaction_state=dict(state), evidence=record,
            ))

        if stage.type == StageType.VERIFY_LOCAL_EFFECT:
            decision = self._decision(state)
            executed = state.get("executed_action") or {}
            if executed.get("deferred") is True:
                model_calls = 0
                model_elapsed_ms = 0
                capture_elapsed_ms = 0
                if decision.continuation == "finish":
                    baseline = store.get(decision.observation_artifact_id)
                    target = state.get("target_binding") or {}
                    source = str(
                        target.get("source")
                        or contract.intent.source
                        or "active_window"
                    )
                    artifact, revalidation = self._capture_authoritative_delta(
                        context=context,
                        contract=contract,
                        store=store,
                        stop_event=stop_event,
                        baseline=baseline,
                        source=source,
                        bounds=decision.interaction_region,
                    )
                    capture_elapsed_ms = int(
                        revalidation.get("capture_elapsed_ms") or 0
                    )
                    state["completion_revalidation"] = revalidation
                    if revalidation["changed"]:
                        state.update({
                            "status": "completion_claim_stale",
                            "stale_plan": decision.model_dump(mode="json"),
                            "planned_action": None,
                            "executed_action": None,
                            "local_verification": None,
                            "failure_stage": "VerifyLocalEffect.revalidation",
                        })
                        if revalidation["authority_changed"]:
                            self._binding(context).reset()
                        record = registry.record(
                            "interaction.plan_revalidated",
                            provider="authoritative_completion_preflight",
                            verified=True,
                            stage_id=stage.id,
                            artifact_id=artifact.artifact_id,
                            metadata={
                                **revalidation,
                                "recovery": "infer_from_fresh_authoritative_frame",
                            },
                        )
                        return NodeResult(
                            ok=True,
                            branch="continue",
                            output=self.stage_output(
                                stage,
                                started,
                                artifact_id=artifact.artifact_id,
                                model_calls=0,
                                model_elapsed_ms=0,
                                controller_elapsed_ms=0,
                                capture_elapsed_ms=capture_elapsed_ms,
                                interaction_state=dict(state),
                                evidence=record,
                            ),
                        )
                    decision = decision.model_copy(update={
                        "frame_id": int(state.get("frame_id") or decision.frame_id),
                        "observation_artifact_id": artifact.artifact_id,
                    })
                    state["planned_action"] = decision.model_dump(mode="json")
                    self._reserve_model_call(
                        state,
                        contract,
                        stage="VerifyInteractionCompletion",
                    )
                    model_started = time.monotonic()
                    try:
                        completion = verify_interaction_completion(
                            artifact.managed_path,
                            task=contract.task,
                            decision=decision,
                        )
                    except Exception as exc:
                        model_elapsed_ms = int(
                            (time.monotonic() - model_started) * 1000
                        )
                        raise _failure(
                            f"bounded no-action completion verification failed: {exc}",
                            "semantic_verification_failed",
                            artifact_id=artifact.artifact_id,
                            model_calls=1,
                            model_elapsed_ms=model_elapsed_ms,
                            controller_elapsed_ms=0,
                            capture_elapsed_ms=capture_elapsed_ms,
                        ) from exc
                    model_elapsed_ms = int(
                        (time.monotonic() - model_started) * 1000
                    )
                    model_calls = 1
                    if completion.get("verified") is not True:
                        raise _failure(
                            "a no-action planner decision did not prove task completion",
                            "semantic_verification_failed",
                            semantic_verification=completion,
                            model_calls=1,
                            model_elapsed_ms=model_elapsed_ms,
                            controller_elapsed_ms=0,
                            capture_elapsed_ms=capture_elapsed_ms,
                        )
                    verification = {
                        "verified": True,
                        "deferred": True,
                        "reason": "bounded goal was already visibly complete",
                        "semantic": completion,
                        "artifact_id": artifact.artifact_id,
                    }
                    status = "completed"
                    branch = "complete"
                elif decision.continuation == "wait_external":
                    if not (
                        contract.session is not None
                        and contract.session.wait_for_external_change
                    ):
                        raise _failure(
                            "external defer has no contract-authorized watcher",
                            "semantic_ambiguity",
                        )
                    verification = {
                        "verified": True,
                        "deferred": True,
                        "reason": "no mutation occurred because an external participant owns the turn",
                    }
                    status = "waiting_external"
                    branch = "next"
                else:
                    raise _failure(
                        "no-mutation decision has no verifiable continuation",
                        "semantic_ambiguity",
                    )
                state.update({"status": status, "local_verification": verification})
                record = registry.record(
                    "interaction.local_effect_verified",
                    provider="local_semantic_effect_verifier",
                    verified=True,
                    stage_id=stage.id,
                    metadata=verification,
                )
                completion_record = None
                if branch == "complete":
                    completion_record = registry.record(
                        "interaction.task_completed",
                        provider="interaction_terminal_verifier",
                        verified=True,
                        stage_id=stage.id,
                        artifact_id=artifact.artifact_id,
                        metadata={
                            "proof": "independent_terminal_visual_verification",
                            "local_effect_evidence_id": record["id"],
                            "semantic": completion,
                        },
                    )
                    state["completion_evidence_id"] = completion_record["id"]
                return NodeResult(
                    branch=branch,
                    output=self.stage_output(
                        stage, started, model_calls=model_calls,
                        model_elapsed_ms=model_elapsed_ms,
                        controller_elapsed_ms=0,
                        capture_elapsed_ms=capture_elapsed_ms,
                        interaction_state=dict(state), evidence=record,
                        completion_evidence=completion_record,
                    ),
                )

            source = str(state.get("target_binding", {}).get("source") or contract.intent.source or "active_window")
            baseline = store.get(decision.observation_artifact_id)
            current = self._capture_scene(
                context=context, contract=contract, store=store,
                stop_event=stop_event, source=source, focus=False,
            )
            capture_elapsed_ms = int(
                (state.get("last_capture_timing") or {}).get(
                    "capture_elapsed_ms", 0,
                )
            )
            effect_regions = _effect_regions(decision)
            changes = []
            for bounds in effect_regions:
                remote_feedback = _is_remote_feedback_region(decision, bounds)
                item = store.frame_change(
                    baseline.artifact_id,
                    current.artifact_id,
                    target_region=_normalized_roi_pixels(bounds, current),
                    min_change_ratio=_local_causal_change_ratio(
                        decision,
                        remote_feedback=remote_feedback,
                    ),
                    min_mean_delta=0.45 if remote_feedback else 1.0,
                    allow_coherent_region=remote_feedback,
                )
                changes.append({
                    **item,
                    "verification_scope": (
                        "bound_surface_remote_feedback"
                        if remote_feedback else "declared_effect_region"
                    ),
                    "normalized_region": list(bounds),
                })
            change = max(
                changes,
                key=lambda item: (
                    item.get("method") != "unavailable",
                    bool(item.get("changed")),
                    float(item.get("change_ratio") or 0.0),
                    float(item.get("mean_pixel_delta") or 0.0),
                ),
            )
            if change.get("changed") is not True:
                if state.get("pointer_hold", {}).get("active") is True:
                    self._release_pointer_session(
                        context,
                        state,
                        reason="held_pointer_produced_no_causal_change",
                    )
                raise _failure(
                    "interaction transaction produced no causal change in its "
                    "declared ROI or contract-authorized remote feedback region",
                    "local_effect_missing",
                    local_change=change,
                    local_changes=changes,
                    effect_regions=effect_regions,
                    expected_effect=decision.expected_effect.model_dump(mode="json"),
                )
            gesture_realization = verify_pointer_gesture_realization(
                baseline.managed_path,
                current.managed_path,
                decision,
            )
            if gesture_realization.get("applicable") is True:
                gesture_record = registry.record(
                    "interaction.gesture_realized",
                    provider="local_gesture_realization",
                    verified=gesture_realization.get("verified") is True,
                    stage_id=stage.id,
                    artifact_id=current.artifact_id,
                    metadata=gesture_realization,
                )
                BUS.publish(
                    "interaction_gesture_verify",
                    run_id=context["run_id"],
                    session_id=state.get("session_id"),
                    artifact_id=current.artifact_id,
                    gesture_realization=gesture_realization,
                    evidence_id=gesture_record["id"],
                )
                if gesture_realization.get("verified") is not True:
                    failed_mutation = _failed_visible_mutation(
                        decision,
                        current,
                        failure_type="gesture_realization_missing",
                        evidence=gesture_realization,
                    )
                    state["failed_visible_mutation"] = failed_mutation
                    raise _failure(
                        "post-action pixels changed, but they do not realize all "
                        "declared pointer paths",
                        "gesture_realization_missing",
                        local_change=change,
                        gesture_realization=gesture_realization,
                        failed_mutation=failed_mutation,
                        model_calls=0,
                        model_elapsed_ms=0,
                        controller_elapsed_ms=0,
                        capture_elapsed_ms=capture_elapsed_ms,
                    )
            manipulation_transition = verify_direct_manipulation_transition(
                baseline.managed_path,
                current.managed_path,
                decision,
            )
            if manipulation_transition.get("applicable") is True:
                transition_record = registry.record(
                    "interaction.direct_manipulation_transition_verified",
                    provider="local_direct_manipulation_transition",
                    verified=manipulation_transition.get("verified") is True,
                    stage_id=stage.id,
                    artifact_id=current.artifact_id,
                    metadata=manipulation_transition,
                )
                BUS.publish(
                    "interaction_transition_verify",
                    run_id=context["run_id"],
                    session_id=state.get("session_id"),
                    artifact_id=current.artifact_id,
                    manipulation_transition=manipulation_transition,
                    evidence_id=transition_record["id"],
                )
            selected_providers = (
                dict(stage.execution.selected_evidence_providers)
                if stage.execution is not None
                else {}
            )
            selected_effect_provider = str(
                selected_providers.get("interaction.local_effect_verified") or ""
            )
            selected_terminal_provider = str(
                selected_providers.get("interaction.task_completed") or ""
            )
            deferred_program = (
                dict(state.get("deferred_action_program") or {})
                if isinstance(state.get("deferred_action_program"), dict)
                else {}
            )
            has_deferred_program = bool(
                deferred_program.get("remaining_actions")
            )
            held_pointer_progress = bool(
                decision.continuation == "reobserve"
                and decision.pointer_hold_after
                and isinstance(state.get("pointer_hold"), dict)
                and state["pointer_hold"].get("active") is True
                and state["pointer_hold"].get("button")
                == decision.pointer_hold_after
            )
            use_typed_transition_provider = bool(
                selected_effect_provider
                == "typed_direct_manipulation_completion"
                and selected_terminal_provider
                == "typed_direct_manipulation_completion"
                and any(item.action == "drag" for item in decision.actions)
            )
            typed_completion = verify_typed_direct_manipulation_completion(
                decision,
                transition_evidence=manipulation_transition,
                geometry_evidence=state.get("visual_geometry_grounding") or {},
            )
            typed_freehand_completion = verify_typed_freehand_turn_completion(
                decision,
                gesture_evidence=gesture_realization,
                geometry_evidence=state.get("visual_geometry_grounding") or {},
                input_mode_evidence=list(state.get("input_mode_evidence") or []),
                persistent_session=bool(
                    contract.session is not None
                    and contract.session.lifecycle == "persistent"
                ),
            )
            allowed_effect_providers = {
                provider.id
                for obligation in stage.obligations
                if obligation.id == "interaction.local_effect_verified"
                for provider in obligation.evidence_providers
            }
            use_typed_freehand_provider = bool(
                typed_freehand_completion.get("verified") is True
                and "typed_freehand_turn_completion" in allowed_effect_providers
            )
            if use_typed_transition_provider:
                typed_record = registry.record(
                    "interaction.typed_direct_manipulation_completed",
                    provider="typed_direct_manipulation_completion",
                    verified=typed_completion.get("verified") is True,
                    stage_id=stage.id,
                    artifact_id=current.artifact_id,
                    metadata=typed_completion,
                )
                BUS.publish(
                    "interaction_typed_completion_verify",
                    run_id=context["run_id"],
                    session_id=state.get("session_id"),
                    artifact_id=current.artifact_id,
                    completion=typed_completion,
                    evidence_id=typed_record["id"],
                )
            if typed_freehand_completion.get("applicable") is True:
                typed_freehand_record = registry.record(
                    "interaction.typed_freehand_turn_completed",
                    provider="typed_freehand_turn_completion",
                    verified=typed_freehand_completion.get("verified") is True,
                    stage_id=stage.id,
                    artifact_id=current.artifact_id,
                    metadata=typed_freehand_completion,
                )
                BUS.publish(
                    "interaction_typed_freehand_verify",
                    run_id=context["run_id"],
                    session_id=state.get("session_id"),
                    artifact_id=current.artifact_id,
                    completion=typed_freehand_completion,
                    evidence_id=typed_freehand_record["id"],
                )
            semantic = {
                "ok": True,
                "verified": True,
                "semantic_visual_verified": True,
                "verify_method": "causal-roi-change",
            }
            model_calls = 0
            model_elapsed_ms = 0
            if held_pointer_progress:
                semantic = {
                    "ok": True,
                    "verified": True,
                    "semantic_visual_verified": False,
                    "task_complete": False,
                    "observed": [
                        "the held pointer remained active while the current "
                        "typed prefix caused a fresh-frame change in its "
                        "declared effect region"
                    ],
                    "issues": [],
                    "local_effect_issues": [],
                    "completion_issues": [
                        "the dynamic interaction remains held and requires "
                        "fresh-frame semantic planning before release"
                    ],
                    "verify_method": "held-pointer-causal-progress",
                    "expected_effect": decision.expected_effect.model_dump(
                        mode="json"
                    ),
                    "verifier_contract": {
                        "selected_provider": "grounded_transaction_progress",
                        "pointer_hold": dict(state.get("pointer_hold") or {}),
                        "causal_roi_change": change,
                        "model_call_skipped": True,
                    },
                }
            elif use_typed_transition_provider:
                semantic = {
                    "ok": True,
                    "verified": typed_completion.get("verified") is True,
                    "semantic_visual_verified": False,
                    "task_complete": typed_completion.get("verified") is True,
                    "verify_method": "typed-direct-manipulation-completion",
                    "expected_effect": decision.expected_effect.model_dump(
                        mode="json"
                    ),
                    "verifier_contract": {
                        "selected_provider": (
                            "typed_direct_manipulation_completion"
                        ),
                        "typed_completion": typed_completion,
                        "model_call_skipped": True,
                    },
                }
            elif use_typed_freehand_provider:
                semantic = {
                    "ok": True,
                    "verified": True,
                    "semantic_visual_verified": False,
                    "task_complete": False,
                    "observed": [
                        "all declared continuous paths, requested colour, "
                        "current-pixel geometry, and input mode were locally proven"
                    ],
                    "issues": [],
                    "local_effect_issues": [],
                    "completion_issues": [
                        "the persistent interaction remains open for the external actor"
                    ],
                    "verify_method": "typed-freehand-turn-completion",
                    "expected_effect": decision.expected_effect.model_dump(
                        mode="json"
                    ),
                    "verifier_contract": {
                        "selected_provider": "typed_freehand_turn_completion",
                        "typed_completion": typed_freehand_completion,
                        "model_call_skipped": True,
                    },
                }
            elif has_deferred_program:
                semantic = {
                    "ok": True,
                    "verified": True,
                    "semantic_visual_verified": False,
                    "task_complete": False,
                    "observed": [
                        "the controller committed the current typed action "
                        "and the fresh authoritative frame changed inside "
                        "its declared effect region"
                    ],
                    "issues": [],
                    "local_effect_issues": [],
                    "completion_issues": [
                        f"{len(deferred_program.get('remaining_actions') or [])} "
                        "controller-grounded action(s) remain in the local program"
                    ],
                    "verify_method": "grounded-local-program-progress",
                    "expected_effect": decision.expected_effect.model_dump(
                        mode="json"
                    ),
                    "verifier_contract": {
                        "selected_provider": "grounded_transaction_progress",
                        "program_id": deferred_program.get("program_id"),
                        "program_step_index": deferred_program.get(
                            "next_step_index"
                        ),
                        "causal_roi_change": change,
                        "geometry_evidence": state.get(
                            "visual_geometry_grounding"
                        ) or {},
                        "model_call_skipped": True,
                    },
                }
            elif decision.expected_effect.semantic_verification_required:
                self._reserve_model_call(
                    state,
                    contract,
                    stage=StageType.VERIFY_LOCAL_EFFECT.value,
                )
                model_started = time.monotonic()
                try:
                    semantic = verify_interaction_effect(
                        current.managed_path,
                        task=contract.task,
                        decision=decision,
                        authoritative_effect_regions=effect_regions,
                        transition_evidence=manipulation_transition,
                        gesture_evidence=gesture_realization,
                        verified_turn_history=list(
                            state.get("verified_turn_history") or []
                        ),
                    )
                except Exception as exc:
                    model_elapsed_ms = int(
                        (time.monotonic() - model_started) * 1000
                    )
                    failed_mutation = _failed_visible_mutation(
                        decision,
                        current,
                        failure_type="semantic_verification_failed",
                        evidence={"error": str(exc)},
                    )
                    state["failed_visible_mutation"] = failed_mutation
                    raise _failure(
                        f"semantic local-effect verification failed: {exc}",
                        "semantic_verification_failed",
                        local_change=change,
                        gesture_realization=gesture_realization,
                        manipulation_transition=manipulation_transition,
                        failed_mutation=failed_mutation,
                        model_calls=1,
                        model_elapsed_ms=model_elapsed_ms,
                        controller_elapsed_ms=0,
                        capture_elapsed_ms=capture_elapsed_ms,
                    ) from exc
                model_elapsed_ms = int(
                    (time.monotonic() - model_started) * 1000
                )
                model_calls = 1
            if semantic.get("verified") is not True:
                verification_failure_type = (
                    "direct_manipulation_completion_unverified"
                    if use_typed_transition_provider
                    else "semantic_verification_failed"
                )
                failed_mutation = _failed_visible_mutation(
                    decision,
                    current,
                    failure_type=verification_failure_type,
                    evidence=semantic,
                )
                state["failed_visible_mutation"] = failed_mutation
                raise _failure(
                    (
                        "typed direct-manipulation evidence does not prove the "
                        "contracted completion"
                        if use_typed_transition_provider
                        else "post-action pixels do not prove the intended semantic effect"
                    ),
                    verification_failure_type,
                    local_change=change,
                    gesture_realization=gesture_realization,
                    manipulation_transition=manipulation_transition,
                    semantic_verification=semantic,
                    failed_mutation=failed_mutation,
                    model_calls=model_calls,
                    model_elapsed_ms=model_elapsed_ms,
                    controller_elapsed_ms=0,
                    capture_elapsed_ms=capture_elapsed_ms,
                )
            verification = {
                "verified": True,
                "deferred": False,
                "baseline_artifact_id": baseline.artifact_id,
                "post_artifact_id": current.artifact_id,
                "local_change": change,
                "local_changes": changes,
                "effect_regions": effect_regions,
                "gesture_realization": gesture_realization,
                "manipulation_transition": manipulation_transition,
                "semantic": semantic,
            }
            state.pop("failed_visible_mutation", None)
            state.update({
                "status": "turn_verified",
                "local_verification": verification,
                "last_local_verification": verification,
                "baseline_artifact_id": current.artifact_id,
                "current_artifact_id": current.artifact_id,
                "turn_index": int(state.get("turn_index") or 0) + 1,
                "agent_turns_since_external_change": int(
                    state.get("agent_turns_since_external_change") or 0
                ) + 1,
            })
            state["budget"] = self._budget_snapshot(state, contract)
            record = registry.record(
                "interaction.local_effect_verified",
                provider=(
                    "grounded_transaction_progress"
                    if has_deferred_program or held_pointer_progress
                    else (
                        "typed_direct_manipulation_completion"
                        if use_typed_transition_provider
                        else (
                            "typed_freehand_turn_completion"
                            if use_typed_freehand_provider
                            else "local_semantic_effect_verifier"
                        )
                    )
                ),
                verified=True,
                stage_id=stage.id,
                artifact_id=current.artifact_id,
                metadata=verification,
            )
            history = (
                list(state.get("verified_turn_history") or [])
                if isinstance(state.get("verified_turn_history"), list)
                else []
            )
            history.append({
                "turn_index": int(state.get("turn_index") or 0),
                "artifact_id": current.artifact_id,
                "evidence_id": record["id"],
                "actions": [
                    {
                        key: value
                        for key, value in item.model_dump(mode="json").items()
                        if key in {
                            "action", "target_region", "target_anchor",
                            "interaction_mechanism", "affordance_evidence",
                            "color", "text",
                        }
                        and value is not None
                        and value != ""
                        and value != []
                    }
                    for item in decision.actions
                ],
                "observed": list(semantic.get("observed") or [])[:8],
                "completion_issues": list(
                    semantic.get("completion_issues") or []
                )[:8],
                "task_complete": semantic.get("task_complete") is True,
            })
            # This is run-scoped progress memory, not reusable Skill/Memory.
            # Keep it compact so recovery retains causal history without
            # inflating every later visual-model request.
            state["verified_turn_history"] = history[-12:]
            BUS.publish(
                "interaction_verify",
                run_id=context["run_id"],
                session_id=state.get("session_id"),
                local_verification=verification,
            )
            if semantic.get("task_complete") is True:
                state["status"] = "completed"
                branch = "complete"
            elif has_deferred_program:
                try:
                    next_decision, deferred_program = _next_deferred_decision(
                        decision,
                        deferred_program,
                        frame_id=int(state.get("frame_id") or 0),
                        artifact_id=current.artifact_id,
                    )
                except (TypeError, ValueError) as exc:
                    raise _failure(
                        f"deferred local interaction program became invalid: {exc}",
                        "interaction_plan_invalid",
                        deferred_action_program=deferred_program,
                    ) from exc
                state.update({
                    "status": "local_program_followup",
                    "planned_action": next_decision.model_dump(mode="json"),
                    "last_mutating_plan": next_decision.model_dump(mode="json"),
                    "executed_action": None,
                    "local_verification": None,
                    "deferred_action_program": (
                        deferred_program
                        if deferred_program.get("remaining_actions")
                        else None
                    ),
                })
                BUS.publish(
                    "interaction_program_continue",
                    run_id=context["run_id"],
                    session_id=state.get("session_id"),
                    program_id=deferred_program.get("program_id"),
                    next_step_index=deferred_program.get("next_step_index"),
                    remaining_action_count=len(
                        deferred_program.get("remaining_actions") or []
                    ),
                    planned_action=state["planned_action"],
                )
                branch = "continue_local"
            elif contract.session is not None and contract.session.lifecycle == "persistent":
                # A persistent run stays alive, but the consecutive-turn guard
                # above prevents it from taking a second turn until the bound
                # watcher proves an independent external change.
                state["status"] = "waiting_external"
                branch = "next"
            elif decision.continuation == "finish":
                # The action itself was valid, but its terminal claim was not.
                # Replan on the already captured authoritative frame instead
                # of failing or making another blind mutation.
                state["status"] = "turn_verified_replan_required"
                self._reserve_replan(
                    state,
                    contract,
                    reason="terminal_claim_not_verified",
                    activity={
                        "model_calls": model_calls,
                        "model_elapsed_ms": model_elapsed_ms,
                        "controller_elapsed_ms": 0,
                        "capture_elapsed_ms": capture_elapsed_ms,
                    },
                )
                branch = "continue"
            elif decision.continuation == "reobserve":
                # This is the normal successful continuation of a multi-turn
                # task, not recovery from a failed plan.  Charging the replan
                # budget here made valid interactions terminate after one or
                # two verified turns.
                state["status"] = "turn_verified_followup"
                branch = "continue"
            else:
                raise _failure(
                    "bounded interaction cannot wait without an external watcher stage",
                    "semantic_ambiguity",
                )
            completion_record = None
            if branch == "complete":
                completion_record = registry.record(
                    "interaction.task_completed",
                    provider=(
                        selected_terminal_provider
                        or "interaction_terminal_verifier"
                    ),
                    verified=True,
                    stage_id=stage.id,
                    artifact_id=current.artifact_id,
                    metadata={
                        "proof": (
                            "typed_direct_manipulation_completion"
                            if use_typed_transition_provider
                            else "post_action_terminal_visual_verification"
                        ),
                        "local_effect_evidence_id": record["id"],
                        "semantic": semantic,
                    },
                )
                state["completion_evidence_id"] = completion_record["id"]
            return NodeResult(
                branch=branch,
                output=self.stage_output(
                    stage, started, model_calls=model_calls,
                    model_elapsed_ms=model_elapsed_ms,
                    controller_elapsed_ms=0,
                    capture_elapsed_ms=capture_elapsed_ms,
                    artifact_id=current.artifact_id,
                    interaction_state=dict(state), evidence=record,
                    completion_evidence=completion_record,
                ),
            )

        if stage.type == StageType.WAIT_FOR_EXTERNAL_CHANGE:
            decision = self._decision(state)
            target = state.get("target_binding") or {}
            hwnd = int(target.get("hwnd") or 0)
            baseline = store.get(
                str(state.get("current_artifact_id") or decision.observation_artifact_id)
            )
            bounds, watch_binding = _external_watch_bounds(
                state,
                decision,
                baseline,
            )
            watch_region = _normalized_roi_screen(bounds, target["frame_region"])
            timeout_s = float(
                contract.session.external_change_timeout_s
                if contract.session is not None
                else 300.0
            )
            waiting_external = {
                "verified": False,
                "armed": True,
                "waiting": True,
                "changed": False,
                "status": "waiting_external",
                "watch_scope": str(
                    params.get("watch_scope") or "bound_interaction_surface"
                ),
                "watch_bounds": list(bounds),
                "watch_binding": watch_binding,
                "watch_region": _rect(watch_region),
                "baseline_artifact_id": baseline.artifact_id,
                "timeout_s": timeout_s,
                "requires_external_participant": True,
                "agent_turn_verified": bool(
                    (state.get("last_local_verification")
                     or state.get("local_verification")
                     or {}).get("verified") is True
                ),
            }
            state.update({
                "status": "waiting_external",
                "external_change": waiting_external,
            })
            BUS.publish(
                "interaction_wait_external_change",
                run_id=context["run_id"],
                session_id=state.get("session_id"),
                waiting=True,
                armed=True,
                changed=False,
                watch_scope=waiting_external["watch_scope"],
                external_change=waiting_external,
            )
            source = str(target.get("source") or contract.intent.source or "active_window")
            artifact, baseline_delta = self._capture_authoritative_delta(
                context=context, contract=contract, store=store,
                stop_event=stop_event, baseline=baseline, source=source,
                bounds=bounds,
            )
            capture_elapsed_ms = int(
                baseline_delta.get("capture_elapsed_ms") or 0
            )
            controller_elapsed_ms = 0
            detection_source = "authoritative_baseline_delta"
            result: dict[str, Any] = {
                "ok": True,
                "changed": True,
                "source": detection_source,
                "baseline_delta": baseline_delta,
            }
            changed = bool(baseline_delta["changed"])
            watcher_false_positives: list[dict[str, Any]] = []
            authoritative_watch_delta: dict[str, Any] | None = (
                baseline_delta if changed else None
            )
            if not changed:
                # The first fresh frame is now the actual watcher baseline.
                # This closes the former gap where the wait tool established a
                # new private baseline and could miss a change that happened
                # after the previous authoritative observation.
                decision = decision.model_copy(update={
                    "frame_id": int(state.get("frame_id") or decision.frame_id),
                    "observation_artifact_id": artifact.artifact_id,
                })
                state["planned_action"] = decision.model_dump(mode="json")
                target = state.get("target_binding") or target
                remaining_timeout_s = timeout_s
                while remaining_timeout_s > 0.0:
                    session = ToolSession(
                        stop_event=stop_event,
                        task=contract.task,
                        runtime_profile=RuntimeProfile(desktop=True, coding=False),
                        task_intent=contract.intent,
                        communication_context={"active": False},
                    )
                    session.run_id = context["run_id"]
                    controller_started = time.monotonic()
                    try:
                        session._remember_target(
                            hwnd, str(target.get("title") or ""),
                        )
                        result = _json(session.dispatch("run_action_batch", {
                            "actions": [{
                                "action": "wait_for_visual_change",
                                "region": _action_rect(watch_region),
                                "timeout_s": remaining_timeout_s,
                                "min_ratio": 0.0015,
                                "require_persistent": True,
                                "stable_ms": 400,
                                "stable_max_ratio": 0.0008,
                            }],
                            "stop_on_error": True,
                            "subgoal": (
                                "wait for a verified external change in the "
                                "bound interaction region"
                            ),
                        }).text)
                    finally:
                        session.close()
                    attempt_elapsed_ms = int(
                        (time.monotonic() - controller_started) * 1000
                    )
                    controller_elapsed_ms += attempt_elapsed_ms
                    remaining_timeout_s = max(
                        0.0,
                        remaining_timeout_s - attempt_elapsed_ms / 1000.0,
                    )
                    if stop_event.is_set():
                        return NodeResult(
                            ok=False, branch="canceled", error="run canceled",
                        )
                    watcher_changed = bool(
                        result.get("ok") is True
                        and (
                            result.get("changed") is True
                            or any(
                                isinstance(item, dict)
                                and (
                                    item.get("data", {}).get("changed") is True
                                    or item.get("data", {}).get(
                                        "visual_change_detected"
                                    ) is True
                                )
                                for item in result.get("results") or []
                            )
                        )
                    )
                    if not watcher_changed:
                        raise _failure(
                            str(result.get("error") or (
                                "external visual change was not observed"
                            )),
                            "external_change_timeout",
                            requires_external_participant=True,
                            agent_turn_verified=waiting_external[
                                "agent_turn_verified"
                            ],
                            wait_outcome="external_participant_absent",
                            wait_result=result,
                            watch_region=_rect(watch_region),
                            baseline_delta=baseline_delta,
                            watcher_false_positives=watcher_false_positives,
                            capture_elapsed_ms=capture_elapsed_ms,
                            controller_elapsed_ms=controller_elapsed_ms,
                        )

                    # A watcher notification is only a cheap wake-up signal.
                    # It may be a cursor, tooltip, hover popup, compositor
                    # repaint, or transient overlay.  Require a second managed
                    # target-window artifact to differ persistently inside the
                    # contract watch bounds before granting the user/external
                    # turn.  False wakes are re-armed locally without an LLM.
                    post, post_delta = self._capture_authoritative_delta(
                        context=context,
                        contract=contract,
                        store=store,
                        stop_event=stop_event,
                        baseline=artifact,
                        source=source,
                        bounds=bounds,
                    )
                    capture_elapsed_ms += int(
                        post_delta.get("capture_elapsed_ms") or 0
                    )
                    artifact = post
                    if post_delta.get("changed") is True:
                        authoritative_watch_delta = post_delta
                        changed = True
                        detection_source = (
                            "bound_visual_change_watcher+"
                            "authoritative_post_delta"
                        )
                        break
                    watcher_false_positives.append({
                        "attempt": len(watcher_false_positives) + 1,
                        "wait_result": result,
                        "authoritative_post_delta": post_delta,
                    })
                    decision = decision.model_copy(update={
                        "frame_id": int(
                            state.get("frame_id") or decision.frame_id
                        ),
                        "observation_artifact_id": artifact.artifact_id,
                    })
                    state["planned_action"] = decision.model_dump(mode="json")
                if authoritative_watch_delta is None:
                    raise _failure(
                        "external visual change timeout expired after transient "
                        "watcher notifications produced no authoritative pixel delta",
                        "external_change_timeout",
                        requires_external_participant=True,
                        agent_turn_verified=waiting_external[
                            "agent_turn_verified"
                        ],
                        wait_outcome="external_participant_absent",
                        wait_result=result,
                        watch_region=_rect(watch_region),
                        baseline_delta=baseline_delta,
                        watcher_false_positives=watcher_false_positives,
                        capture_elapsed_ms=capture_elapsed_ms,
                        controller_elapsed_ms=controller_elapsed_ms,
                    )
                result = {
                    **result,
                    "watcher_false_positive_count": len(
                        watcher_false_positives
                    ),
                    "authoritative_post_delta": authoritative_watch_delta,
                }
            external_change = {
                "verified": True,
                "baseline_artifact_id": baseline.artifact_id,
                "watch_bounds": list(bounds),
                "watch_binding": watch_binding,
                "watch_region": _rect(watch_region),
                "wait_result": result,
                "detection_source": detection_source,
                "baseline_delta": baseline_delta,
                "authoritative_watch_delta": (
                    authoritative_watch_delta
                    if not baseline_delta["changed"]
                    else baseline_delta
                ),
                "artifact_id": artifact.artifact_id,
                "frame_id": state["frame_id"],
            }
            state.update({
                "status": "external_change_observed",
                "external_change": external_change,
                "planned_action": None,
                "executed_action": None,
                "local_verification": None,
                "failure_stage": "",
                "agent_turns_since_external_change": 0,
            })
            record = registry.record(
                "interaction.external_change_observed",
                provider="external_change_observer",
                verified=True,
                stage_id=stage.id,
                artifact_id=artifact.artifact_id,
                metadata=external_change,
            )
            BUS.publish(
                "interaction_external_change",
                run_id=context["run_id"],
                session_id=state.get("session_id"),
                external_change=external_change,
            )
            return NodeResult(
                ok=True,
                branch="changed",
                output=self.stage_output(
                    stage, started, artifact_id=artifact.artifact_id,
                    model_calls=0,
                    model_elapsed_ms=0,
                    controller_elapsed_ms=controller_elapsed_ms,
                    capture_elapsed_ms=capture_elapsed_ms,
                    interaction_state=dict(state), evidence=record,
                ),
            )

        raise RuntimeError(f"interaction runtime does not implement {stage.type.value}")
