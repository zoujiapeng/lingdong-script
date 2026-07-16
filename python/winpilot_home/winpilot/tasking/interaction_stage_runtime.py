"""Deterministic runtime for typed visual-interaction stages."""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
from pathlib import Path
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
from .interaction import (
    InteractionDecision,
    InteractionPlanOutputError,
    controller_actions,
    plan_interaction,
    verify_interaction_completion,
    verify_interaction_effect,
)
from .visual_geometry import VisualGeometryError, refine_interaction_geometry


INTERACTION_STAGE_TYPES = {
    StageType.OBSERVE_SCENE,
    StageType.INFER_INTERACTION,
    StageType.PERFORM_TURN,
    StageType.VERIFY_LOCAL_EFFECT,
    StageType.WAIT_FOR_EXTERNAL_CHANGE,
}


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
            if value.get("visual_change_detected") is False:
                return True
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return False


def _mutation_signature(decision: InteractionDecision) -> str:
    payload = [item.model_dump(mode="json") for item in decision.actions]
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
    """Return action/global postcondition ROIs, never grounding ROIs."""
    regions = [
        list(action.expected_effect_region)
        for action in decision.actions
        if len(action.expected_effect_region) == 4
    ]
    if len(decision.expected_effect.region) == 4:
        regions.append(list(decision.expected_effect.region))
    if not regions:
        regions.append(list(decision.interaction_region))
    unique = dict.fromkeys(
        tuple(float(value) for value in region)
        for region in regions
    )
    return [list(region) for region in unique]


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
        state.setdefault("replan_count", 0)
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
        if not hwnd:
            hwnd = self._resolve_hwnd(source)
        if not hwnd:
            raise _failure(f"interaction target window was not found: {source}", "window_not_found")
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
        details = failure_details if isinstance(failure_details, dict) else {}
        self._reserve_replan(
            state,
            contract,
            reason=f"{stage.type.value}:{failure_type}",
            activity=details,
        )
        self._binding(context).reset()
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
                    "turn_index", "local_verification", "external_change",
                    "detected_surface", "surface_region", "interaction_region",
                )
                if state.get(key) is not None
            }
            client_authority = (
                state.get("target_binding", {}).get("client_frame_bounds")
                if isinstance(state.get("target_binding"), dict) else None
            )
            if isinstance(client_authority, list) and len(client_authority) == 4:
                prior["window_client_authority"] = client_authority
            self._reserve_model_call(
                state,
                contract,
                stage=StageType.INFER_INTERACTION.value,
            )
            model_started = time.monotonic()
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
                )
            except InteractionPlanOutputError as exc:
                model_elapsed_ms = int((time.monotonic() - model_started) * 1000)
                raise _failure(
                    str(exc),
                    exc.failure_type,
                    plan_error_code=exc.code,
                    canonicalization_repairs=list(exc.repairs),
                    frame_id=state.get("frame_id"),
                    artifact_id=artifact.artifact_id,
                    frame_readiness=frame_readiness,
                    model_calls=1,
                    model_elapsed_ms=model_elapsed_ms,
                    controller_elapsed_ms=readiness_elapsed_ms,
                    interaction_state=dict(state),
                ) from exc
            except Exception as exc:
                model_elapsed_ms = int((time.monotonic() - model_started) * 1000)
                raise _failure(
                    f"interaction protocol inference failed: {exc}",
                    "interaction_inference_failed",
                    frame_id=state.get("frame_id"),
                    artifact_id=artifact.artifact_id,
                    frame_readiness=frame_readiness,
                    model_calls=1,
                    model_elapsed_ms=model_elapsed_ms,
                    controller_elapsed_ms=readiness_elapsed_ms,
                    interaction_state=dict(state),
                ) from exc
            model_elapsed_ms = int((time.monotonic() - model_started) * 1000)
            canonicalization_repairs = list(decision.canonicalization_repairs)
            if decision.continuation == "blocked":
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
                    model_calls=1,
                    model_elapsed_ms=model_elapsed_ms,
                    controller_elapsed_ms=readiness_elapsed_ms,
                )
            expects_external_watch = bool(
                contract.session is not None
                and contract.session.wait_for_external_change
            )
            if expects_external_watch and decision.continuation not in {
                "wait_external", "finish",
            }:
                raise _failure(
                    "persistent interaction plan must wait for external change or declare a visible terminal state",
                    "interaction_plan_invalid",
                    canonicalization_repairs=canonicalization_repairs,
                    model_calls=1,
                    model_elapsed_ms=model_elapsed_ms,
                    controller_elapsed_ms=readiness_elapsed_ms,
                )
            if not expects_external_watch and decision.continuation == "wait_external":
                raise _failure(
                    "bounded interaction plan cannot request an absent external-change watcher",
                    "interaction_plan_invalid",
                    canonicalization_repairs=canonicalization_repairs,
                    model_calls=1,
                    model_elapsed_ms=model_elapsed_ms,
                    controller_elapsed_ms=readiness_elapsed_ms,
                )
            client_bounds = state.get("target_binding", {}).get("client_frame_bounds")
            if not isinstance(client_bounds, list) or len(client_bounds) != 4:
                raise _failure(
                    "target client authority was lost",
                    "target_binding_failed",
                    model_calls=1,
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
                    model_calls=1,
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
                        "model_calls": 1,
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
                        model_calls=1,
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
                    model_calls=1,
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
                    model_calls=1,
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
            })
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
            )
            return NodeResult(output=self.stage_output(
                stage, started, model_calls=1,
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
            )
            if not any(item.get("action") != "wait_stable" for item in actions):
                raise _failure("semantic turn contained no mutation", "interaction_action_empty")
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
                session._remember_target(hwnd, str(target.get("title") or ""))
                session._visual_frame_id = int(state["frame_id"])
                session._visual_frame_epoch = int(state["frame_id"])
                session._screen_epoch = int(state["frame_id"])
                session._visual_frame_scope = "target_window"
                session._visual_frame_region = dict(target["frame_region"])
                result = _json(session.dispatch("run_action_batch", {
                    "actions": actions,
                    "stop_on_error": True,
                    "subgoal": decision.expected_effect.description,
                    "sync": {"wait_stable": True, "timeout_s": 5.0},
                }).text)
            finally:
                session.close()
            controller_elapsed_ms = int((time.monotonic() - controller_started) * 1000)
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
            state.update({
                "status": "turn_executed",
                "executed_action": {
                    "deferred": False,
                    "frame_id": decision.frame_id,
                    "actions": [item.model_dump(mode="json") for item in decision.actions],
                    "controller_result": result,
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
                    "action_count": len(actions),
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
            changes = [
                store.frame_change(
                    baseline.artifact_id,
                    current.artifact_id,
                    target_region=_normalized_roi_pixels(bounds, current),
                    min_change_ratio=decision.expected_effect.minimum_change_ratio,
                    min_mean_delta=1.0,
                )
                for bounds in effect_regions
            ]
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
                raise _failure(
                    "interaction transaction produced no causal change in its target ROI",
                    "local_effect_missing",
                    local_change=change,
                    local_changes=changes,
                    effect_regions=effect_regions,
                    expected_effect=decision.expected_effect.model_dump(mode="json"),
                )
            semantic = {
                "ok": True,
                "verified": True,
                "semantic_visual_verified": True,
                "verify_method": "causal-roi-change",
            }
            model_calls = 0
            model_elapsed_ms = 0
            if decision.expected_effect.semantic_verification_required:
                self._reserve_model_call(
                    state,
                    contract,
                    stage=StageType.VERIFY_LOCAL_EFFECT.value,
                )
                model_started = time.monotonic()
                try:
                    semantic = verify_interaction_effect(
                        current.managed_path, task=contract.task, decision=decision,
                    )
                except Exception as exc:
                    model_elapsed_ms = int(
                        (time.monotonic() - model_started) * 1000
                    )
                    raise _failure(
                        f"semantic local-effect verification failed: {exc}",
                        "semantic_verification_failed",
                        local_change=change,
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
                raise _failure(
                    "post-action pixels do not prove the intended semantic effect",
                    "semantic_verification_failed",
                    local_change=change,
                    semantic_verification=semantic,
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
                "semantic": semantic,
            }
            state.update({
                "status": "turn_verified",
                "local_verification": verification,
                "last_local_verification": verification,
                "baseline_artifact_id": current.artifact_id,
                "current_artifact_id": current.artifact_id,
                "turn_index": int(state.get("turn_index") or 0) + 1,
            })
            state["budget"] = self._budget_snapshot(state, contract)
            record = registry.record(
                "interaction.local_effect_verified",
                provider="local_semantic_effect_verifier",
                verified=True,
                stage_id=stage.id,
                artifact_id=current.artifact_id,
                metadata=verification,
            )
            BUS.publish(
                "interaction_verify",
                run_id=context["run_id"],
                session_id=state.get("session_id"),
                local_verification=verification,
            )
            if semantic.get("task_complete") is True:
                state["status"] = "completed"
                branch = "complete"
            elif contract.session is not None and contract.session.lifecycle == "persistent":
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
                    provider="interaction_terminal_verifier",
                    verified=True,
                    stage_id=stage.id,
                    artifact_id=current.artifact_id,
                    metadata={
                        "proof": "post_action_terminal_visual_verification",
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
            bounds = decision.surface_region
            watch_region = _normalized_roi_screen(bounds, target["frame_region"])
            timeout_s = float(
                contract.session.external_change_timeout_s
                if contract.session is not None
                else 300.0
            )
            source = str(target.get("source") or contract.intent.source or "active_window")
            baseline = store.get(
                str(state.get("current_artifact_id") or decision.observation_artifact_id)
            )
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
                    session._remember_target(hwnd, str(target.get("title") or ""))
                    result = _json(session.dispatch("run_action_batch", {
                        "actions": [{
                            "action": "wait_for_visual_change",
                            "region": _rect(watch_region),
                            "timeout_s": timeout_s,
                            "min_ratio": 0.0015,
                            "require_persistent": True,
                            "stable_ms": 400,
                            "stable_max_ratio": 0.0008,
                        }],
                        "stop_on_error": True,
                        "subgoal": "wait for a verified external change in the bound interaction region",
                    }).text)
                finally:
                    session.close()
                controller_elapsed_ms = int(
                    (time.monotonic() - controller_started) * 1000
                )
                if stop_event.is_set():
                    return NodeResult(ok=False, branch="canceled", error="run canceled")
                changed = bool(
                    result.get("ok") is True
                    and (
                        result.get("changed") is True
                        or any(
                            isinstance(item, dict)
                            and (
                                item.get("data", {}).get("changed") is True
                                or item.get("data", {}).get("visual_change_detected") is True
                            )
                            for item in result.get("results") or []
                        )
                    )
                )
                if not changed:
                    raise _failure(
                        str(result.get("error") or "external visual change was not observed"),
                        "external_change_timeout",
                        wait_result=result,
                        watch_region=_rect(watch_region),
                        baseline_delta=baseline_delta,
                        capture_elapsed_ms=capture_elapsed_ms,
                        controller_elapsed_ms=controller_elapsed_ms,
                    )
                detection_source = "bound_visual_change_watcher"
                post = self._capture_scene(
                    context=context, contract=contract, store=store,
                    stop_event=stop_event, source=source, focus=False,
                )
                capture_elapsed_ms += int(
                    (state.get("last_capture_timing") or {}).get(
                        "capture_elapsed_ms", 0,
                    )
                )
                artifact = post
            external_change = {
                "verified": True,
                "watch_region": _rect(watch_region),
                "wait_result": result,
                "detection_source": detection_source,
                "baseline_delta": baseline_delta,
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
