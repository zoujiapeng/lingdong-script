"""MCP bridge that lets a logged-in ChatGPT/Codex client drive WinPilot.

The bridge deliberately points in one direction: the desktop client is the
planner and invokes local WinPilot tools. It never reads ChatGPT credentials,
cookies, or private desktop-app protocols, and it does not turn a ChatGPT
subscription into an OpenAI API key.
"""
from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import win32gui

from ..action import executor
from ..agent.runtime_profile import build_runtime_profile
from ..agent.tools import ToolSession
from ..config import CONFIG
from ..fast.action_contract import BATCH_MUTATING_ACTIONS
from ..orchestrator.contracts import ArpGraph, ArpNodeKind, validate_graph
from ..recorder import trace as trace_mod
from ..tasking import TaskContractCompiler
from ..utils import image_capture, screenshot
from ..utils.image_capture import select_visual_capture_scope
from ..utils.log import BUS


JsonDict = dict[str, Any]


# Frame coordinates are valid only until the first operation that can change
# their pixels, geometry, foreground ownership, or temporal state.  Keep this
# policy tied to the Controller's canonical mutation set; explicit waits are
# included because they intentionally cross a visual-state boundary even
# though they do not inject input themselves.
_FRAME_INVALIDATING_PREFIX_ACTIONS = frozenset(BATCH_MUTATING_ACTIONS) | {
    "wait", "wait_change", "wait_for_visual_change", "wait_stable",
}


def _region_payload(region: screenshot.Region | None) -> JsonDict | None:
    if region is None:
        return None
    return {
        "x": int(region.left),
        "y": int(region.top),
        "width": int(region.width),
        "height": int(region.height),
    }


def _region_contains(outer: JsonDict | None, inner: JsonDict | None) -> bool:
    if not isinstance(outer, dict) or not isinstance(inner, dict):
        return False
    try:
        outer_left, outer_top = int(outer["x"]), int(outer["y"])
        outer_right = outer_left + int(outer["width"])
        outer_bottom = outer_top + int(outer["height"])
        inner_left, inner_top = int(inner["x"]), int(inner["y"])
        inner_right = inner_left + int(inner["width"])
        inner_bottom = inner_top + int(inner["height"])
    except (KeyError, TypeError, ValueError):
        return False
    return (
        int(outer.get("width") or 0) > 0
        and int(outer.get("height") or 0) > 0
        and int(inner.get("width") or 0) > 0
        and int(inner.get("height") or 0) > 0
        and outer_left <= inner_left < inner_right <= outer_right
        and outer_top <= inner_top < inner_bottom <= outer_bottom
    )


@dataclass(frozen=True)
class ChatGPTBridgeHooks:
    """Server-owned operations used by the protocol adapter."""

    status: Callable[[], JsonDict]
    start_graph: Callable[[ArpGraph, JsonDict], JsonDict]
    get_run: Callable[[str], JsonDict]
    cancel_run: Callable[[str], JsonDict]
    decide_gate: Callable[[str, str, bool, str], JsonDict]
    busy: Callable[[], bool]
    enabled: Callable[[], bool]
    mutation_allowed: Callable[[], bool]


def _json_object(text: str) -> JsonDict | None:
    try:
        value = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _compact_element(value: Any) -> JsonDict | None:
    if not isinstance(value, dict):
        return None
    return {
        key: value.get(key)
        for key in ("elem_id", "text", "role", "source", "bbox", "center", "score", "reason")
        if value.get(key) is not None
    }


def _compact_screen_delta(value: Any) -> JsonDict | None:
    if not isinstance(value, dict):
        return None
    return {
        key: value.get(key)
        for key in (
            "changed", "ratio", "regions", "elapsed_ms", "capture_latency_ms",
            "delta_source", "transient_observed", "changed_in_target_region",
            "foreground_matches_target",
        )
        if value.get(key) is not None
    }


def _compact_controller_item(item: Any) -> JsonDict:
    if not isinstance(item, dict):
        return {"preview": str(item)[:600]}
    compact = {
        key: item.get(key)
        for key in ("index", "action", "ok", "verified", "elapsed_ms", "error", "terminal")
        if item.get(key) is not None
    }
    data = item.get("data")
    if isinstance(data, dict):
        detail = {
            key: data.get(key)
            for key in ("via", "ok", "blocked", "error", "verified", "verify_detail", "needs_llm_reason")
            if data.get(key) is not None
        }
        screen_delta = _compact_screen_delta(data.get("screen_delta"))
        if screen_delta:
            detail["screen_delta"] = screen_delta
        element = _compact_element(data.get("resolved_element"))
        if element:
            detail["resolved_element"] = element
        locator = data.get("anchor_locator")
        if isinstance(locator, dict):
            detail["anchor_locator"] = {
                key: locator.get(key)
                for key in ("found", "score", "cache_hit", "reason")
                if locator.get(key) is not None
            }
        input_locator = data.get("input_locator")
        if isinstance(input_locator, dict):
            detail["input_locator"] = {
                "found": input_locator.get("found"),
                "score": input_locator.get("score"),
                "reason": input_locator.get("reason"),
                "candidate": _compact_element(input_locator.get("candidate")),
            }
        compact["detail"] = detail
    elif item.get("preview"):
        compact["preview"] = str(item["preview"])[:800]
    return compact


def _compact_controller_payload(payload: JsonDict) -> JsonDict:
    compact = {
        key: payload.get(key)
        for key in (
            "ok", "error", "blocked", "verified", "verify_detail", "subgoal",
            "subgoal_route", "missing", "missing_texts", "missing_visual_colors",
            "visible_colors", "needs_llm_reason", "controller_state", "failed_at", "phase",
        )
        if payload.get(key) is not None
    }
    if payload.get("visible_text"):
        compact["visible_text"] = str(payload["visible_text"])[:2000]
    metrics = payload.get("metrics")
    if isinstance(metrics, dict):
        compact["metrics"] = {
            key: metrics.get(key)
            for key in (
                "elapsed_ms", "action_count", "failed_at", "observe_count", "verify_count",
                "screen_wait_count", "auto_observe_count", "internal_action_count",
                "compound_round_trips_saved", "controller_round_trips_saved", "avg_tool_latency_ms",
            )
            if metrics.get(key) is not None
        }
    evidence = payload.get("evidence_packet")
    if isinstance(evidence, dict):
        compact["evidence_packet"] = {
            key: evidence.get(key)
            for key in (
                "matched_texts", "missing_texts", "verified_expect_texts", "visible_colors",
                "matched_visual_colors", "missing_visual_colors", "candidate_click_labels",
            )
            if evidence.get(key) is not None
        }
        if evidence.get("visible_text_preview"):
            compact["evidence_packet"]["visible_text_preview"] = str(
                evidence["visible_text_preview"]
            )[:1600]
    results = payload.get("results")
    if isinstance(results, list):
        compact["results"] = [_compact_controller_item(item) for item in results[:30]]
        if len(results) > 30:
            compact["results_truncated"] = len(results) - 30
    return compact


class ChatGPTDesktopBridge:
    """Stateful, single-desktop adapter behind the MCP tool surface."""

    def __init__(self, hooks: ChatGPTBridgeHooks) -> None:
        self.hooks = hooks
        self._lock = threading.RLock()
        self._session: ToolSession | None = None
        self._session_id = ""
        self._bound_task = ""
        self._task_contract = None
        self._frame_sequence = 0
        self._authoritative_frame: JsonDict | None = None
        self._authoritative_frame_raw: bytes | None = None
        self._last_post_frame_raw: bytes | None = None

    def _new_session(self, task: str = "ChatGPT desktop control") -> ToolSession:
        contract = None
        stage = None
        try:
            contract = TaskContractCompiler().compile(task, cold_start=True)
            stage = contract.stages[0] if contract.stages else None
        except Exception:
            # The bridge must stay available for diagnostics if a new contract
            # parser revision rejects an incomplete interactive phrase.
            contract = None
        session = ToolSession(
            task=task,
            runtime_profile=build_runtime_profile(
                task, task_contract=contract, stage=stage,
            ),
            task_intent=(contract.intent if contract is not None else None),
        )
        session._mcp_task_contract = contract
        self._task_contract = contract
        self._session_id = f"mcp-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        session.run_id = None
        self._frame_sequence = 0
        self._authoritative_frame = None
        self._authoritative_frame_raw = None
        self._last_post_frame_raw = None
        return session

    def _get_session(self, task: str = "") -> ToolSession:
        if self._session is None:
            self._session = self._new_session(task or "ChatGPT desktop control")
        elif task and not self._bound_task:
            self._session.task = task
        return self._session

    def reset(self) -> JsonDict:
        with self._lock:
            if self._session is not None:
                try:
                    self._session.close()
                except Exception:
                    pass
            self._bound_task = ""
            self._session = self._new_session()
            return {"ok": True, "session_id": self._session_id}

    def bind_task(self, task: str) -> JsonDict:
        """Start one MCP desktop task on the same typed contract as the UI path."""
        task = str(task or "").strip()
        if not task:
            return {"ok": False, "error": "task is required"}
        with self._lock:
            if self._session is not None:
                try:
                    self._session.close()
                except Exception:
                    pass
            self._bound_task = task
            self._session = self._new_session(task)
            contract = self._task_contract
            return {
                "ok": True,
                "session_id": self._session_id,
                "task": task,
                "task_contract": (
                    contract.model_dump(mode="json") if contract is not None else None
                ),
                "runtime_profile": self._session.runtime_profile.to_dict(),
            }

    def _guard(self, *, mutating: bool) -> JsonDict | None:
        if not self.hooks.enabled():
            return {"ok": False, "error": "ChatGPT desktop bridge is disabled in WinPilot settings."}
        if mutating and not self.hooks.mutation_allowed():
            return {"ok": False, "error": "Desktop mutation is disabled for the ChatGPT bridge."}
        if mutating and self.hooks.busy():
            return {
                "ok": False,
                "error": "WinPilot already has an active agent, replay, recording, or ARP run.",
                "retryable": True,
            }
        return None

    @staticmethod
    def _live_authority_state(hwnd: int) -> JsonDict:
        """Read the OS-owned state that makes one captured frame actionable.

        ``ToolSession._screen_epoch`` only describes mutations made through
        that session.  It cannot see a human click, another process repainting
        the UI, or a window being moved.  This snapshot is therefore persisted
        with every visual frame and compared again immediately before mapping
        frame coordinates to physical input.
        """
        try:
            foreground = int(win32gui.GetForegroundWindow() or 0)
        except Exception:
            foreground = 0
        try:
            desktop_region = _region_payload(screenshot.virtual_screen_region())
        except Exception:
            desktop_region = None
        state: JsonDict = {
            "hwnd": int(hwnd or 0),
            "foreground_hwnd": foreground,
            "desktop_region": desktop_region,
            "window_region": None,
            "client_region": None,
            "valid": not bool(hwnd),
        }
        if not hwnd:
            return state
        try:
            target = int(hwnd)
            if not win32gui.IsWindow(target):
                state["error"] = "target window no longer exists"
                return state
            if not win32gui.IsWindowVisible(target) or win32gui.IsIconic(target):
                state["error"] = "target window is not visible and restored"
                return state
            state["window_region"] = _region_payload(screenshot.window_region(target))
            state["client_region"] = _region_payload(image_capture.window_client_region(target))
            state["valid"] = bool(state["window_region"])
            if not state["valid"]:
                state["error"] = "target window geometry is unavailable"
        except Exception as exc:
            state["error"] = f"target window authority could not be read: {exc}"
        return state

    @staticmethod
    def _capture_frame_region(frame: JsonDict) -> tuple[bytes | None, str | None]:
        region = frame.get("region") if isinstance(frame.get("region"), dict) else {}
        try:
            left = int(region["x"])
            top = int(region["y"])
            width = int(region["width"])
            height = int(region["height"])
            quality = int(frame.get("capture_quality") or 70)
        except (KeyError, TypeError, ValueError):
            return None, "authoritative frame has invalid capture geometry"
        result = executor.zoom_region(left, top, width, height, quality=quality)
        if not result.get("ok"):
            return None, str(result.get("error") or "live frame recapture failed")
        encoded = str(result.get("base64") or "")
        try:
            return base64.b64decode(encoded, validate=True), None
        except ValueError as exc:
            return None, f"live frame recapture returned invalid image bytes: {exc}"

    @staticmethod
    def _frame_action_target_rois(
        actions: list[Any],
    ) -> list[tuple[float, float, float, float]]:
        """Return bounded local preflight ROIs along frame-bound gestures.

        A single bounding rectangle is unsafe for long drags: a small object
        can move at one endpoint while its changed pixels are diluted by a
        mostly static path-sized rectangle.  Sampling local neighborhoods
        preserves sensitivity without treating unrelated animation elsewhere
        in the window as action-relevant drift.
        """
        margin = float(CONFIG.get(
            "controller", "visual_preflight_target_margin", default=60.0,
        ) or 60.0)
        margin = max(8.0, min(200.0, margin))
        points: list[tuple[float, float]] = []
        explicit_rois: list[tuple[float, float, float, float]] = []

        def point(x: Any, y: Any) -> None:
            try:
                px, py = float(x), float(y)
            except (TypeError, ValueError):
                return
            if 0.0 <= px <= 1000.0 and 0.0 <= py <= 1000.0:
                points.append((px, py))

        def region(value: Any) -> None:
            if not isinstance(value, dict):
                return
            try:
                left = float(value["x"])
                top = float(value["y"])
                right = left + float(value["width"])
                bottom = top + float(value["height"])
            except (KeyError, TypeError, ValueError):
                return
            if (
                0.0 <= left < right <= 1000.0
                and 0.0 <= top < bottom <= 1000.0
            ):
                explicit_rois.append((left, top, right, bottom))

        for action in actions:
            if not isinstance(action, dict):
                continue
            if "frame_x" in action and "frame_y" in action:
                point(action.get("frame_x"), action.get("frame_y"))
            for x_key, y_key in (("frame_x1", "frame_y1"), ("frame_x2", "frame_y2")):
                if x_key in action and y_key in action:
                    point(action.get(x_key), action.get(y_key))
            for raw in action.get("frame_points") or []:
                if isinstance(raw, (list, tuple)) and len(raw) == 2:
                    point(raw[0], raw[1])
            for stroke in action.get("frame_strokes") or []:
                if not isinstance(stroke, list):
                    continue
                for raw in stroke:
                    if isinstance(raw, (list, tuple)) and len(raw) == 2:
                        point(raw[0], raw[1])
            region(action.get("frame_region"))
            region(action.get("effect_frame_region"))

        # Bound preflight cost for long freehand paths while retaining both
        # endpoints and evenly distributed interior authority checks.
        max_samples = 12
        if len(points) > max_samples:
            indices = {
                round(index * (len(points) - 1) / (max_samples - 1))
                for index in range(max_samples)
            }
            points = [points[index] for index in sorted(indices)]
        rois = [
            (
                max(0.0, x - margin),
                max(0.0, y - margin),
                min(1000.0, x + margin),
                min(1000.0, y + margin),
            )
            for x, y in points
        ]
        rois.extend(explicit_rois)
        # Preserve order for trace readability while removing duplicate points
        # that are common at adjoining stroke boundaries.
        return list(dict.fromkeys(rois))

    @classmethod
    def _frame_action_target_roi(
        cls, actions: list[Any],
    ) -> tuple[float, float, float, float] | None:
        """Return the union ROI for compatibility and compact telemetry."""
        rois = cls._frame_action_target_rois(actions)
        if not rois:
            return None
        return (
            min(roi[0] for roi in rois),
            min(roi[1] for roi in rois),
            max(roi[2] for roi in rois),
            max(roi[3] for roi in rois),
        )

    def _replacement_frame_rejection(
        self,
        session: ToolSession,
        rejected_frame: JsonDict,
        *,
        error: str,
        stale_reason: str,
        **details: Any,
    ) -> JsonDict:
        """Fail closed and bind a current frame for the planner's recovery.

        When the target still owns the foreground this produces a new target
        crop.  If ownership was lost, ``select_visual_capture_scope`` falls
        back to a hwnd-free full-desktop frame, so recovery never receives a
        desktop image falsely labelled as belonging to the old target.
        """
        metadata, raw = self._capture_authoritative_locked(
            session,
            x=None, y=None, width=None, height=None,
            quality=int(rejected_frame.get("capture_quality") or 70),
        )
        replacement = (
            metadata.get("authoritative_frame")
            if raw is not None and isinstance(metadata, dict)
            else None
        )
        self._last_post_frame_raw = raw
        payload: JsonDict = {
            "ok": False,
            "blocked": True,
            "stale_authoritative_frame": True,
            "needs_visual_capture": raw is None,
            "error": error,
            "stale_reason": stale_reason,
            "rejected_frame": rejected_frame,
            "replacement_frame_available": raw is not None,
        }
        if replacement is not None:
            payload["authoritative_frame"] = replacement
            payload["replacement_frame"] = replacement
        else:
            payload["replacement_frame_error"] = metadata.get("error")
        payload.update(details)
        return payload

    def _validate_live_frame_authority(
        self,
        session: ToolSession,
        frame: JsonDict,
        actions: list[Any],
    ) -> tuple[str, str, JsonDict] | None:
        """Return a fail-closed mismatch, or prove the frame is still live."""
        if str(frame.get("bridge_session_id") or "") != self._session_id:
            return (
                "bridge_session_changed",
                "The visual frame belongs to a different MCP task session.",
                {},
            )
        frame_hwnd = int(frame.get("hwnd") or 0)
        current_target = int(getattr(session, "hwnd", 0) or 0)
        captured_target = int(frame.get("session_target_hwnd") or 0)
        if current_target != captured_target:
            return (
                "session_target_changed",
                "The bound target window changed after this MCP frame.",
                {"current_target_hwnd": current_target},
            )
        if frame_hwnd and current_target != frame_hwnd:
            return (
                "frame_target_changed",
                "The frame target is no longer the Controller target.",
                {"current_target_hwnd": current_target},
            )

        live = self._live_authority_state(frame_hwnd)
        if frame_hwnd and live.get("valid") is not True:
            return (
                "target_window_invalid",
                str(live.get("error") or "The target window is no longer actionable."),
                {"live_authority": live},
            )
        captured_foreground = int(frame.get("foreground_hwnd") or 0)
        live_foreground = int(live.get("foreground_hwnd") or 0)
        if live_foreground != captured_foreground or (frame_hwnd and live_foreground != frame_hwnd):
            return (
                "foreground_changed",
                "Foreground ownership changed after this MCP frame.",
                {"captured_foreground_hwnd": captured_foreground,
                 "current_foreground_hwnd": live_foreground},
            )

        captured_authority = (
            frame.get("authority_snapshot")
            if isinstance(frame.get("authority_snapshot"), dict)
            else {}
        )
        geometry_fields = (
            ("desktop_region",) if not frame_hwnd
            else ("desktop_region", "window_region", "client_region")
        )
        for field in geometry_fields:
            if captured_authority.get(field) != live.get(field):
                return (
                    "window_geometry_changed" if frame_hwnd else "desktop_geometry_changed",
                    "Window/client geometry changed after this MCP frame."
                    if frame_hwnd else "Desktop geometry changed after this MCP frame.",
                    {"geometry_field": field,
                     "captured_geometry": captured_authority.get(field),
                     "current_geometry": live.get(field)},
                )
        authority_region = frame.get("authority_region")
        if not _region_contains(authority_region, frame.get("region")):
            return (
                "frame_outside_authority",
                "The captured frame is not contained by its declared window/desktop authority.",
                {},
            )

        expected_sha = str(frame.get("sha256") or "")
        if not expected_sha:
            return (
                "missing_frame_fingerprint",
                "The authoritative frame has no visual fingerprint.",
                {},
            )
        captured_raw = self._authoritative_frame_raw
        if captured_raw is None or hashlib.sha256(captured_raw).hexdigest() != expected_sha:
            return (
                "frame_identity_unavailable",
                "The exact pixels for this authoritative frame are no longer available.",
                {},
            )
        current_raw, capture_error = self._capture_frame_region(frame)
        if current_raw is None:
            return (
                "frame_recapture_failed",
                str(capture_error or "The authoritative frame could not be recaptured."),
                {},
            )
        previous_b64 = base64.b64encode(captured_raw).decode("ascii")
        current_b64 = base64.b64encode(current_raw).decode("ascii")
        global_ratio = image_capture.jpeg_diff_ratio(previous_b64, current_b64)
        if global_ratio is None:
            return (
                "visual_comparison_failed",
                "The current pixels could not be compared with the authoritative frame.",
                {},
            )
        target_rois = self._frame_action_target_rois(actions)
        target_roi: tuple[float, float, float, float] | None = None
        target_ratios: list[float] | None = None
        target_ratio: float | None = None
        decision_ratio = float(global_ratio)
        threshold = max(0.0, float(CONFIG.get(
            "controller", "visual_preflight_change_ratio", default=0.0025,
        )))
        decision_scope = "whole_frame"
        if target_rois:
            target_ratios = image_capture.jpeg_diff_ratios_in_normalized_rois(
                previous_b64, current_b64, target_rois,
            )
            if target_ratios is None or len(target_ratios) != len(target_rois):
                return (
                    "visual_comparison_failed",
                    "Action-local pixels could not be compared with the authoritative frame.",
                    {"target_rois": [list(roi) for roi in target_rois]},
                )
            target_index = max(range(len(target_ratios)), key=target_ratios.__getitem__)
            target_ratio = float(target_ratios[target_index])
            target_roi = target_rois[target_index]
            decision_ratio = target_ratio
            threshold = max(0.0, float(CONFIG.get(
                "controller", "visual_preflight_target_change_ratio", default=0.01,
            )))
            decision_scope = "action_target_roi"
        if decision_ratio > threshold:
            return (
                "visual_preflight_changed",
                "Action-relevant pixels changed after this MCP frame; coordinate input requires a fresh frame.",
                {
                    "change_ratio": round(decision_ratio, 6),
                    "global_change_ratio": round(float(global_ratio), 6),
                    "target_change_ratio": (
                        round(float(target_ratio), 6) if target_ratio is not None else None
                    ),
                    "target_roi": list(target_roi) if target_roi is not None else None,
                    "target_rois": [list(roi) for roi in target_rois],
                    "target_change_ratios": (
                        [round(float(ratio), 6) for ratio in target_ratios]
                        if target_ratios is not None else None
                    ),
                    "decision_scope": decision_scope,
                    "threshold": threshold,
                    "captured_sha256": expected_sha,
                    "current_sha256": hashlib.sha256(current_raw).hexdigest(),
                },
            )
        return None

    @staticmethod
    def _frame_coordinate_action(action: JsonDict) -> bool:
        return any(key in action for key in (
            "frame_x", "frame_y", "frame_x1", "frame_y1",
            "frame_x2", "frame_y2", "frame_points", "frame_strokes",
            "frame_region", "effect_frame_region",
        ))

    @staticmethod
    def _raw_frame_gesture_action(action: JsonDict) -> bool:
        return any(key in action for key in (
            "frame_x", "frame_y", "frame_x1", "frame_y1",
            "frame_x2", "frame_y2", "frame_points", "frame_strokes",
        ))

    @classmethod
    def _frame_coordinate_gesture_count(cls, actions: list[Any]) -> int:
        """Count logical gestures rather than individual pointer events.

        A balanced down/move/up path is one atomic Controller gesture even
        though each event has its own frame coordinate.  It is safe to bind
        the whole path to one authoritative frame because no model round-trip
        or visual-state decision occurs between those local events.
        """
        coordinate = [
            action for action in actions
            if isinstance(action, dict) and cls._raw_frame_gesture_action(action)
        ]
        if len(coordinate) <= 1:
            return len(coordinate)
        names = [str(item.get("action") or item.get("tool") or "") for item in coordinate]
        down_indices = [index for index, name in enumerate(names) if name == "mouse_down"]
        up_indices = [index for index, name in enumerate(names) if name == "mouse_up"]
        if len(down_indices) != 1 or len(up_indices) != 1:
            return len(coordinate)
        down_index, up_index = down_indices[0], up_indices[0]
        if down_index >= up_index or any(
            name != "move_mouse"
            for index, name in enumerate(names)
            if index not in {down_index, up_index}
        ):
            return len(coordinate)
        if any(name == "move_mouse" for name in names[up_index + 1:]):
            return len(coordinate)
        down_button = str(coordinate[down_index].get("button") or "left").casefold()
        up_button = str(coordinate[up_index].get("button") or "left").casefold()
        return 1 if down_button == up_button else len(coordinate)

    def _prepare_frame_bound_batch(self, session: ToolSession, args: JsonDict) -> JsonDict | None:
        actions = args.get("actions")
        if not isinstance(actions, list):
            return None
        frame_bound_actions = [
            action for action in actions
            if isinstance(action, dict) and self._frame_coordinate_action(action)
        ]
        coordinate_actions = [
            action for action in actions
            if isinstance(action, dict) and self._raw_frame_gesture_action(action)
        ]
        source_frame_id = args.pop("source_frame_id", None)
        if not frame_bound_actions and source_frame_id is None:
            return None
        frame = self._authoritative_frame
        if not isinstance(frame, dict):
            return {
                "ok": False, "blocked": True, "needs_visual_capture": True,
                "error": "No authoritative MCP frame is bound. Capture a visual frame before using frame_* coordinates.",
            }
        if source_frame_id is None:
            return {
                "ok": False, "blocked": True, "needs_visual_capture": True,
                "error": "frame_* actions require source_frame_id from the exact visual capture.",
                "authoritative_frame": frame,
            }
        try:
            requested_id = int(source_frame_id)
        except (TypeError, ValueError):
            requested_id = -1
        if requested_id != int(frame.get("frame_id") or 0):
            return {
                "ok": False, "blocked": True, "stale_authoritative_frame": True,
                "error": "source_frame_id does not match the latest authoritative MCP frame.",
                "requested_frame_id": source_frame_id,
                "authoritative_frame": frame,
            }
        epoch = int(getattr(session, "_screen_epoch", 0) or 0)
        if epoch != int(frame.get("screen_epoch") or 0):
            return self._replacement_frame_rejection(
                session,
                frame,
                error="The Controller changed the desktop after this MCP frame.",
                stale_reason="controller_screen_epoch_changed",
                current_screen_epoch=epoch,
            )
        live_mismatch = self._validate_live_frame_authority(session, frame, actions)
        if live_mismatch is not None:
            stale_reason, message, details = live_mismatch
            return self._replacement_frame_rejection(
                session,
                frame,
                error=message,
                stale_reason=stale_reason,
                **details,
            )
        frame_hwnd = int(frame.get("hwnd") or 0)
        session_target_hwnd = int(getattr(session, "hwnd", 0) or 0)
        if self._frame_coordinate_gesture_count(actions) > 1:
            return {
                "ok": False, "blocked": True,
                "error": (
                    "One authoritative frame may ground at most one raw coordinate gesture. "
                    "Use semantic Controller actions in the same transaction, then consume the post-frame."
                ),
            }
        if coordinate_actions and not frame_hwnd and session_target_hwnd:
            return {
                "ok": False,
                "blocked": True,
                "needs_visual_capture": True,
                "needs_target_frame": True,
                "fresh_target_frame_required": True,
                "error": (
                    "Raw frame coordinates cannot be executed from a full-desktop recovery frame "
                    "while a target window remains bound. Focus the intended target, capture a fresh "
                    "target-window frame, and bind the gesture to that new frame_id."
                ),
                "authoritative_frame": frame,
                "session_target_hwnd": session_target_hwnd,
            }

        raw_gesture_index = next(
            (
                index
                for index, action in enumerate(actions)
                if isinstance(action, dict) and self._raw_frame_gesture_action(action)
            ),
            None,
        )
        invalidating_prefixes: list[JsonDict] = []
        if raw_gesture_index is not None:
            for index, action in enumerate(actions[:raw_gesture_index]):
                if not isinstance(action, dict):
                    continue
                name = str(action.get("action") or action.get("tool") or "").strip()
                if not name and len(action) == 1:
                    name = str(next(iter(action))).strip()
                if name in _FRAME_INVALIDATING_PREFIX_ACTIONS:
                    invalidating_prefixes.append({"index": index, "action": name})
        if invalidating_prefixes:
            return {
                "ok": False,
                "blocked": True,
                "needs_visual_capture": True,
                "needs_target_frame": bool(session_target_hwnd),
                "fresh_target_frame_required": True,
                "error": (
                    "A raw frame-coordinate gesture cannot follow an action or wait that may change "
                    "the visual/foreground state in the same batch. Execute the prefix transaction, "
                    "consume its post-frame (or capture a fresh target frame), then issue the gesture "
                    "with the new frame_id."
                ),
                "authoritative_frame": frame,
                "invalidating_prefix_actions": invalidating_prefixes,
            }
        region = frame.get("region") if isinstance(frame.get("region"), dict) else {}
        try:
            left = int(region["x"])
            top = int(region["y"])
            width = int(region["width"])
            height = int(region["height"])
        except (KeyError, TypeError, ValueError):
            return {"ok": False, "blocked": True, "error": "Authoritative MCP frame has invalid geometry."}

        def normalized(value: Any, field: str) -> float:
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"{field} must be numeric") from None
            if parsed < 0 or parsed > 1000:
                raise ValueError(f"{field} must be inside 0..1000")
            return parsed

        def x(value: Any, field: str = "frame x") -> int:
            return left + int(round(normalized(value, field) * max(1, width - 1) / 1000.0))

        def y(value: Any, field: str = "frame y") -> int:
            return top + int(round(normalized(value, field) * max(1, height - 1) / 1000.0))

        def map_region(value: Any, field: str) -> JsonDict:
            if not isinstance(value, dict):
                raise ValueError(f"{field} must be an object")
            try:
                nx, ny = float(value["x"]), float(value["y"])
                nw, nh = float(value["width"]), float(value["height"])
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"{field} requires numeric x/y/width/height") from None
            if nx < 0 or ny < 0 or nw <= 0 or nh <= 0 or nx + nw > 1000 or ny + nh > 1000:
                raise ValueError(f"{field} must be fully inside 0..1000")
            x1, y1 = x(nx, f"{field}.x"), y(ny, f"{field}.y")
            x2, y2 = x(nx + nw, f"{field}.right"), y(ny + nh, f"{field}.bottom")
            return {"x": x1, "y": y1, "width": max(1, x2 - x1), "height": max(1, y2 - y1)}

        mapped_actions: list[JsonDict] = []
        local_mutation_seen = False
        for original in actions:
            if not isinstance(original, dict):
                mapped_actions.append(original)
                continue
            action = dict(original)
            name = str(action.get("action") or action.get("tool") or "")
            if local_mutation_seen and name in {"click_element", "type_into_element"}:
                action["_rebind_after_mutation"] = True
            provenance = {
                "frame_id": requested_id,
                "screen_epoch": epoch,
                "hwnd": frame_hwnd,
                "capture_scope": frame.get("capture_scope"),
            }
            try:
                if "frame_x" in action or "frame_y" in action:
                    if not all(key in action for key in ("frame_x", "frame_y")):
                        raise ValueError("frame_x and frame_y must be provided together")
                    action["x"], action["y"] = (
                        x(action.pop("frame_x"), "frame_x"),
                        y(action.pop("frame_y"), "frame_y"),
                    )
                gesture_keys = ("frame_x1", "frame_y1", "frame_x2", "frame_y2")
                if any(key in action for key in gesture_keys):
                    if not all(key in action for key in gesture_keys):
                        raise ValueError("frame_x1/frame_y1/frame_x2/frame_y2 must be provided together")
                    action["x1"], action["y1"] = (
                        x(action.pop("frame_x1"), "frame_x1"),
                        y(action.pop("frame_y1"), "frame_y1"),
                    )
                    action["x2"], action["y2"] = (
                        x(action.pop("frame_x2"), "frame_x2"),
                        y(action.pop("frame_y2"), "frame_y2"),
                    )
                if "frame_points" in action:
                    points = action.pop("frame_points")
                    if not isinstance(points, list) or not all(
                        isinstance(point, (list, tuple)) and len(point) == 2 for point in points
                    ):
                        raise ValueError("frame_points must contain [x, y] pairs")
                    action["points"] = [
                        [x(point[0], "frame_points.x"), y(point[1], "frame_points.y")]
                        for point in points
                    ]
                if "frame_strokes" in action:
                    strokes = action.pop("frame_strokes")
                    if not isinstance(strokes, list) or not all(
                        isinstance(stroke, list) and all(
                            isinstance(point, (list, tuple)) and len(point) == 2
                            for point in stroke
                        )
                        for stroke in strokes
                    ):
                        raise ValueError("frame_strokes must contain lists of [x, y] pairs")
                    action["strokes"] = [
                        [
                            [x(point[0], "frame_strokes.x"), y(point[1], "frame_strokes.y")]
                            for point in stroke
                        ]
                        for stroke in strokes
                    ]
                if "frame_region" in action:
                    action["region"] = map_region(action.pop("frame_region"), "frame_region")
                if "effect_frame_region" in action:
                    action["effect_region"] = map_region(
                        action.pop("effect_frame_region"), "effect_frame_region",
                    )
            except ValueError as exc:
                return {"ok": False, "blocked": True, "error": str(exc)}
            if self._raw_frame_gesture_action(original):
                action["source_frame_coordinates"] = provenance
            mapped_actions.append(action)
            if name in BATCH_MUTATING_ACTIONS:
                local_mutation_seen = True
        args["actions"] = mapped_actions
        args["source_frame_binding"] = frame
        return None

    def _capture_authoritative_locked(
        self,
        session: ToolSession,
        *,
        x: int | None,
        y: int | None,
        width: int | None,
        height: int | None,
        quality: int,
    ) -> tuple[JsonDict, bytes | None]:
        quality = max(35, min(int(quality), 90))
        roi_values = (x, y, width, height)
        session_hwnd = int(getattr(session, "hwnd", 0) or 0)
        frame_hwnd = 0
        scope = "full_desktop"
        authority_kind = "virtual_desktop"
        authority_region: JsonDict | None = None
        capture_authority: JsonDict
        if any(value is not None for value in roi_values):
            if not all(value is not None for value in roi_values):
                return {"ok": False, "error": "ROI capture requires x, y, width, and height together."}, None
            assert x is not None and y is not None and width is not None and height is not None
            if width <= 0 or height <= 0 or width * height > 8_500_000:
                return {"ok": False, "error": "ROI dimensions are invalid or exceed 8.5 megapixels."}, None
            requested_region = {"x": int(x), "y": int(y), "width": int(width), "height": int(height)}
            frame_hwnd = session_hwnd
            capture_authority = self._live_authority_state(frame_hwnd)
            if frame_hwnd:
                if capture_authority.get("valid") is not True:
                    return {
                        "ok": False,
                        "blocked": True,
                        "error": str(capture_authority.get("error") or "bound target is not actionable"),
                    }, None
                if int(capture_authority.get("foreground_hwnd") or 0) != frame_hwnd:
                    return {
                        "ok": False,
                        "blocked": True,
                        "error": "ROI capture requires the bound target to own the foreground.",
                        "target_hwnd": frame_hwnd,
                        "foreground_hwnd": capture_authority.get("foreground_hwnd"),
                    }, None
                window_authority = capture_authority.get("window_region")
                if not _region_contains(window_authority, requested_region):
                    return {
                        "ok": False,
                        "blocked": True,
                        "error": "Explicit ROI is outside the bound target window authority.",
                        "target_hwnd": frame_hwnd,
                        "requested_region": requested_region,
                        "window_authority": window_authority,
                    }, None
                client_authority = capture_authority.get("client_region")
                if _region_contains(client_authority, requested_region):
                    scope = "explicit_target_client_roi"
                    authority_kind = "window_client"
                    authority_region = client_authority
                else:
                    scope = "explicit_target_window_roi"
                    authority_kind = "target_window"
                    authority_region = window_authority
            else:
                scope = "explicit_desktop_roi"
                authority_region = capture_authority.get("desktop_region")
                if not _region_contains(authority_region, requested_region):
                    return {
                        "ok": False,
                        "blocked": True,
                        "error": "Explicit ROI is outside the virtual desktop authority.",
                        "requested_region": requested_region,
                        "desktop_authority": authority_region,
                    }, None
            result = executor.zoom_region(x, y, width, height, quality=quality)
        else:
            try:
                decision = select_visual_capture_scope(
                    session_hwnd or None,
                    task=str(getattr(session, "task", "") or ""),
                    related_window=getattr(session, "_window_related_to_target", None),
                )
            except Exception:
                decision = None
            region = getattr(decision, "region", None)
            if region is not None:
                x, y, width, height = region.left, region.top, region.width, region.height
                scope = str(getattr(decision, "scope", "target_window") or "target_window")
                target_verified = bool(getattr(decision, "target_verified", False))
                frame_hwnd = (
                    int(getattr(decision, "target_hwnd", 0) or 0)
                    if scope == "target_window" and target_verified
                    else 0
                )
                result = executor.zoom_region(x, y, width, height, quality=quality)
            else:
                scope = "full_desktop"
                frame_hwnd = 0
                result = executor.screenshot_base64(quality=quality)
            capture_authority = self._live_authority_state(frame_hwnd)
            if frame_hwnd:
                if (
                    capture_authority.get("valid") is not True
                    or int(capture_authority.get("foreground_hwnd") or 0) != frame_hwnd
                ):
                    return {
                        "ok": False,
                        "blocked": True,
                        "error": "Target authority changed while preparing its visual frame.",
                        "live_authority": capture_authority,
                    }, None
                authority_kind = "target_window"
                authority_region = capture_authority.get("window_region")
            else:
                scope = "full_desktop"
                authority_kind = "virtual_desktop"
                authority_region = capture_authority.get("desktop_region")
        if not result.get("ok"):
            return {"ok": False, "error": str(result.get("error") or "screen capture failed")}, None
        encoded = str(result.pop("base64", ""))
        if not encoded:
            return {**result, "ok": False, "error": "screen capture returned no image bytes"}, None
        try:
            raw = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            return {"ok": False, "error": f"invalid screenshot payload: {exc}"}, None
        source_region = result.get("region") if isinstance(result.get("region"), dict) else {}
        frame_region = {
            "x": int(source_region.get("x", result.get("left", x or 0)) or 0),
            "y": int(source_region.get("y", result.get("top", y or 0)) or 0),
            "width": int(source_region.get("width", result.get("width", width or 0)) or 0),
            "height": int(source_region.get("height", result.get("height", height or 0)) or 0),
        }
        post_capture_authority = self._live_authority_state(frame_hwnd)
        if post_capture_authority != capture_authority:
            return {
                "ok": False,
                "blocked": True,
                "error": "Window, client, foreground, or desktop authority changed during capture.",
                "capture_authority": capture_authority,
                "post_capture_authority": post_capture_authority,
            }, None
        if not _region_contains(authority_region, frame_region):
            return {
                "ok": False,
                "blocked": True,
                "error": "Captured pixels fall outside their declared window/desktop authority.",
                "capture_scope": scope,
                "frame_region": frame_region,
                "authority_region": authority_region,
            }, None
        self._frame_sequence += 1
        binding = {
            "frame_id": self._frame_sequence,
            "bridge_session_id": self._session_id,
            "screen_epoch": int(getattr(session, "_screen_epoch", 0) or 0),
            "hwnd": frame_hwnd,
            "session_target_hwnd": session_hwnd,
            "foreground_hwnd": int(capture_authority.get("foreground_hwnd") or 0),
            "region": frame_region,
            "capture_scope": scope,
            "authority_kind": authority_kind,
            "authority_region": authority_region,
            "authority_snapshot": capture_authority,
            "coordinate_space": "frame_normalized_0_1000",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "capture_quality": quality,
            "created_at_ms": int(time.time() * 1000),
        }
        self._authoritative_frame = binding
        self._authoritative_frame_raw = raw
        session._visual_frame_id = self._frame_sequence
        session._visual_frame_region = dict(frame_region)
        session._visual_frame_epoch = binding["screen_epoch"]
        return {
            **result,
            "ok": True,
            "authoritative_frame": binding,
            "purpose": "Authoritative visual frame; bind frame_* actions to its frame_id.",
        }, raw

    def dispatch(self, name: str, args: JsonDict, *, task: str = "", mutating: bool = False) -> JsonDict:
        guard = self._guard(mutating=mutating)
        if guard:
            return guard
        with self._lock:
            session = self._get_session(task)
            if name == "run_action_batch":
                frame_block = self._prepare_frame_bound_batch(session, args)
                if frame_block is not None:
                    return {
                        **frame_block,
                        "bridge_session_id": self._session_id,
                        "tool": name,
                    }
            run_id = None
            started = time.monotonic()
            if mutating:
                run_id = f"mcp-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
                session.run_id = run_id
                BUS.publish(
                    "agent_start",
                    run_id=run_id,
                    task=task or f"ChatGPT desktop: {name}",
                    source="chatgpt_mcp",
                )
                BUS.publish("agent_tool_start", run_id=run_id, tool=name, source="chatgpt_mcp")
            result = session.dispatch(name, args)
            parsed = _json_object(result.text)
            if parsed is not None:
                payload = dict(parsed)
                payload.setdefault("ok", result.terminal != "fail")
            else:
                payload = {
                    "ok": result.terminal != "fail" and not result.text.startswith("Unknown tool:"),
                    "text": result.text,
                }
            if name in {"execute_subgoal", "run_action_batch"}:
                payload = _compact_controller_payload(payload)
            elif isinstance(payload.get("text"), str) and len(payload["text"]) > 32_000:
                payload["text"] = payload["text"][:32_000]
                payload["text_truncated"] = True
            payload["bridge_session_id"] = self._session_id
            payload["tool"] = name
            if result.summary:
                payload["summary"] = result.summary
            if isinstance(result.replay, dict):
                payload["replay"] = {
                    key: result.replay.get(key)
                    for key in ("tool", "action", "subgoal", "elapsed_ms", "verified")
                    if result.replay.get(key) is not None
                }
            if mutating:
                post_metadata, post_raw = self._capture_authoritative_locked(
                    session,
                    x=None, y=None, width=None, height=None,
                    quality=65,
                )
                self._last_post_frame_raw = post_raw
                if post_raw is not None:
                    payload["post_frame"] = post_metadata.get("authoritative_frame")
                    payload["post_frame_available"] = True
                else:
                    payload["post_frame_available"] = False
                    payload["post_frame_error"] = post_metadata.get("error")
            if run_id:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                status = "done" if payload.get("ok") else "failed"
                BUS.publish(
                    "agent_tool",
                    run_id=run_id,
                    tool=name,
                    elapsed_ms=elapsed_ms,
                    result_preview=json.dumps(payload, ensure_ascii=False, default=str)[:800],
                    result_meta={
                        "verified": payload.get("verified"),
                        "needs_llm_reason": payload.get("needs_llm_reason"),
                    },
                    source="chatgpt_mcp",
                )
                BUS.publish(
                    "agent_done",
                    run_id=run_id,
                    status=status,
                    result={
                        "tool": name,
                        "ok": bool(payload.get("ok")),
                        "verified": payload.get("verified"),
                    },
                    source="chatgpt_mcp",
                )
                session.run_id = None
                payload["run_id"] = run_id
            return payload

    def visual_capture(
        self,
        *,
        x: int | None,
        y: int | None,
        width: int | None,
        height: int | None,
        quality: int,
    ) -> tuple[JsonDict, bytes | None]:
        guard = self._guard(mutating=False)
        if guard:
            return guard, None
        with self._lock:
            session = self._get_session()
            return self._capture_authoritative_locked(
                session,
                x=x, y=y, width=width, height=height,
                quality=quality,
            )

    def take_last_post_frame(self) -> bytes | None:
        with self._lock:
            raw = self._last_post_frame_raw
            self._last_post_frame_raw = None
            return raw

    def validate_graph(self, graph: JsonDict, *, reject_api_agent: bool = True) -> JsonDict:
        try:
            parsed = ArpGraph.model_validate(graph)
        except Exception as exc:
            return {"valid": False, "errors": [str(exc)], "warnings": []}
        validation = validate_graph(parsed).model_dump(mode="json")
        if reject_api_agent:
            api_nodes = [node.id for node in parsed.nodes if node.kind == ArpNodeKind.AGENT_GOAL]
            if api_nodes:
                validation["valid"] = False
                validation["errors"].append(
                    "ChatGPT-hosted runs cannot contain agent_goal nodes because ChatGPT is already the planner: "
                    + ", ".join(api_nodes)
                )
        return validation

    def start_graph(self, graph: JsonDict, inputs: JsonDict | None = None) -> JsonDict:
        guard = self._guard(mutating=True)
        if guard:
            return guard
        validation = self.validate_graph(graph, reject_api_agent=True)
        if not validation.get("valid"):
            return {"ok": False, "validation": validation, "error": "; ".join(validation.get("errors") or [])}
        parsed = ArpGraph.model_validate(graph)
        return self.hooks.start_graph(parsed, dict(inputs or {}))

    def list_skills(self) -> JsonDict:
        guard = self._guard(mutating=False)
        if guard:
            return guard
        return {"ok": True, "skills": trace_mod.list_scripts()}

    def replay_skill(self, script: str) -> JsonDict:
        script = script.strip()
        if not script:
            return {"ok": False, "error": "script is required"}
        stamp = uuid.uuid4().hex[:8]
        graph = {
            "format": "arp/1",
            "id": f"chatgpt-skill-{stamp}",
            "name": f"Replay {script}",
            "nodes": [
                {"id": "start", "kind": "start"},
                {"id": "skill", "kind": "skill_replay", "config": {"script": script}},
                {"id": "end", "kind": "end"},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "skill"},
                {"id": "e2", "source": "skill", "target": "end"},
            ],
        }
        return self.start_graph(graph)


def build_chatgpt_mcp(hooks: ChatGPTBridgeHooks):
    """Create the official MCP SDK server and its Streamable HTTP ASGI app."""
    from mcp.server.fastmcp import FastMCP, Image
    from mcp.server.transport_security import TransportSecuritySettings
    from mcp.types import ToolAnnotations

    bridge = ChatGPTDesktopBridge(hooks)
    mcp = FastMCP(
        name="WinPilot GUI Agent + ARP",
        instructions=(
            "You are the high-level planner. Prefer get_gui_agent_state and observe_desktop, then send a "
            "short concrete routine to execute_gui_subgoal or a small deterministic graph to run_arp_graph. "
            "Use capture_visual_fallback only for complex or unknown visual states. Do not repeat successful "
            "actions when controller evidence says verified. This server never requires an OpenAI API key."
        ),
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*", "[::1]", "[::1]:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
        ),
    )

    read_only = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
    focus_hint = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
    mutate = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)

    @mcp.tool(
        name="get_gui_agent_state",
        title="Get GUI agent state",
        description="Read WinPilot runtime, current run, world/perception status, and controller capabilities.",
        annotations=read_only,
    )
    def get_gui_agent_state() -> JsonDict:
        return hooks.status()

    @mcp.tool(
        name="begin_gui_task",
        title="Begin typed GUI task",
        description=(
            "Bind one complete user task to WinPilot's Task Contract, RuntimeProfile, target/frame state, "
            "and MCP session before observing or mutating the desktop."
        ),
        annotations=focus_hint,
    )
    def begin_gui_task(task: str) -> JsonDict:
        return bridge.bind_task(task)

    @mcp.tool(
        name="list_desktop_windows",
        title="List desktop windows",
        description="List visible top-level Windows windows before choosing a target.",
        annotations=read_only,
    )
    def list_desktop_windows() -> JsonDict:
        return bridge.dispatch("list_windows", {}, mutating=False)

    @mcp.tool(
        name="focus_desktop_window",
        title="Focus desktop window",
        description="Focus a window by hwnd or title and retain it as the controller target.",
        annotations=focus_hint,
    )
    def focus_desktop_window(hwnd: int | None = None, title: str | None = None) -> JsonDict:
        return bridge.dispatch(
            "focus_window",
            {key: value for key, value in {"hwnd": hwnd, "title": title}.items() if value is not None},
            task=f"Focus {title or hwnd or 'window'}",
            mutating=True,
        )

    @mcp.tool(
        name="launch_app",
        title="Launch app or URL",
        description="Launch a local application, installed program, or URL before focusing and observing it.",
        annotations=mutate,
    )
    def launch_app(command: str) -> JsonDict:
        return bridge.dispatch("launch", {"command": command}, task=f"Launch {command}", mutating=True)

    @mcp.tool(
        name="observe_desktop",
        title="Observe desktop UI",
        description=(
            "Run the low-cost UIA/OCR/CV perception ladder on the target window. Prefer changed_only after an "
            "action and query for long element lists; this is the normal perception loop."
        ),
        annotations=read_only,
    )
    def observe_desktop(
        hwnd: int | None = None,
        query: str | None = None,
        max_elements: int = 120,
        full: bool = False,
        changed_only: bool = False,
        include_icons: bool = False,
    ) -> JsonDict:
        args = {
            "max_elements": max(1, min(int(max_elements), 240)),
            "full": bool(full),
            "changed_only": bool(changed_only),
            "include_icons": bool(include_icons),
        }
        if hwnd is not None:
            args["hwnd"] = hwnd
        if query:
            args["query"] = query
        return bridge.dispatch("observe", args, mutating=False)

    @mcp.tool(
        name="capture_visual_fallback",
        title="Capture visual fallback",
        description=(
            "Return an authoritative JPEG and frame_id to ChatGPT vision. With a bound target, omitted ROI fields "
            "capture that target scope; otherwise they capture the virtual desktop. Provide x/y/width/height together "
            "for an exact ROI. Any later frame_* action must echo this frame_id."
        ),
        annotations=read_only,
        structured_output=False,
    )
    def capture_visual_fallback(
        x: int | None = None,
        y: int | None = None,
        width: int | None = None,
        height: int | None = None,
        quality: int = 70,
    ) -> list[Any]:
        metadata, raw = bridge.visual_capture(
            x=x, y=y, width=width, height=height, quality=quality,
        )
        return [metadata] if raw is None else [metadata, Image(data=raw, format="jpeg")]

    @mcp.tool(
        name="execute_gui_subgoal",
        title="Execute GUI subgoal",
        description=(
            "Delegate one short concrete routine to the low-latency controller. Supply input, click/clicks, "
            "draw/draws, or verify_texts; the controller locates visual anchors, batches actions, watches "
            "transient changes, and verifies locally without an LLM/API round trip."
        ),
        annotations=mutate,
    )
    def execute_gui_subgoal(
        subgoal: str,
        target_window: JsonDict | None = None,
        input: JsonDict | None = None,
        click: JsonDict | None = None,
        clicks: list[JsonDict] | None = None,
        draw: JsonDict | None = None,
        draws: list[JsonDict] | None = None,
        verify_texts: list[str] | None = None,
        constraints: JsonDict | None = None,
    ) -> JsonDict:
        args: JsonDict = {"subgoal": subgoal}
        optional = {
            "target_window": target_window,
            "input": input,
            "click": click,
            "clicks": clicks,
            "draw": draw,
            "draws": draws,
            "verify_texts": verify_texts,
            "constraints": constraints,
        }
        args.update({key: value for key, value in optional.items() if value is not None})
        return bridge.dispatch("execute_subgoal", args, task=subgoal, mutating=True)

    @mcp.tool(
        name="execute_action_batch",
        title="Execute action batch",
        description=(
            "Execute a short grounded batch inside one controller round trip. Actions may include observe, "
            "click_element, type_into_element, hotkey, wait_change, wait_stable, read_text, and visual checks."
        ),
        annotations=mutate,
    )
    def execute_action_batch(
        actions: list[JsonDict],
        subgoal: str = "ChatGPT controller batch",
        stop_on_error: bool = True,
        sync: bool | JsonDict = True,
        source_frame_id: int | None = None,
    ) -> JsonDict:
        args: JsonDict = {
            "actions": actions, "subgoal": subgoal,
            "stop_on_error": stop_on_error, "sync": sync,
        }
        if source_frame_id is not None:
            args["source_frame_id"] = source_frame_id
        return bridge.dispatch(
            "run_action_batch",
            args,
            task=subgoal,
            mutating=True,
        )

    @mcp.tool(
        name="execute_visual_transaction",
        title="Execute GUI transaction with post-frame",
        description=(
            "Execute one typed local Controller transaction and return its real authoritative post-action JPEG in the "
            "same MCP round trip. Use source_frame_id whenever an action contains frame_* coordinates."
        ),
        annotations=mutate,
        structured_output=False,
    )
    def execute_visual_transaction(
        actions: list[JsonDict],
        subgoal: str = "ChatGPT visual transaction",
        stop_on_error: bool = True,
        sync: bool | JsonDict = True,
        source_frame_id: int | None = None,
    ) -> list[Any]:
        args: JsonDict = {
            "actions": actions, "subgoal": subgoal,
            "stop_on_error": stop_on_error, "sync": sync,
        }
        if source_frame_id is not None:
            args["source_frame_id"] = source_frame_id
        payload = bridge.dispatch(
            "run_action_batch", args,
            task=subgoal, mutating=True,
        )
        raw = bridge.take_last_post_frame()
        return [payload] if raw is None else [payload, Image(data=raw, format="jpeg")]

    @mcp.tool(
        name="validate_arp_graph",
        title="Validate ARP graph",
        description="Validate an arp/1 graph intended for ChatGPT-hosted planning without executing it.",
        annotations=read_only,
    )
    def validate_arp_graph(graph: JsonDict) -> JsonDict:
        return bridge.validate_graph(graph, reject_api_agent=True)

    @mcp.tool(
        name="run_arp_graph",
        title="Run ARP graph",
        description=(
            "Start a deterministic arp/1 workflow. Use action_batch, skill_replay, condition, delay, gates, "
            "and variables. agent_goal is rejected because this ChatGPT conversation is the planner."
        ),
        annotations=mutate,
    )
    def run_arp_graph(graph: JsonDict, inputs: JsonDict | None = None) -> JsonDict:
        return bridge.start_graph(graph, inputs)

    @mcp.tool(
        name="get_gui_run",
        title="Get GUI run",
        description="Read one Agent or ARP run projection, evidence, timeline, and latency metrics.",
        annotations=read_only,
    )
    def get_gui_run(run_id: str) -> JsonDict:
        return hooks.get_run(run_id)

    @mcp.tool(
        name="cancel_gui_run",
        title="Cancel GUI run",
        description="Cancel an active ARP run by id.",
        annotations=mutate,
    )
    def cancel_gui_run(run_id: str) -> JsonDict:
        return hooks.cancel_run(run_id)

    @mcp.tool(
        name="decide_human_gate",
        title="Decide human gate",
        description="Approve or reject the exact human-gate node where an ARP run is paused.",
        annotations=mutate,
    )
    def decide_human_gate(run_id: str, node_id: str, approved: bool, comment: str = "") -> JsonDict:
        return hooks.decide_gate(run_id, node_id, approved, comment)

    @mcp.tool(
        name="list_gui_skills",
        title="List GUI skills",
        description="List locally recorded reusable GUI skills and their step counts.",
        annotations=read_only,
    )
    def list_gui_skills() -> JsonDict:
        return bridge.list_skills()

    @mcp.tool(
        name="replay_gui_skill",
        title="Replay GUI skill",
        description="Replay one verified local skill through the ARP runtime without an LLM round trip.",
        annotations=mutate,
    )
    def replay_gui_skill(script: str) -> JsonDict:
        return bridge.replay_skill(script)

    @mcp.tool(
        name="reset_gui_context",
        title="Reset GUI context",
        description="Clear cached target window, elements, and world state before starting an unrelated desktop task.",
        annotations=focus_hint,
    )
    def reset_gui_context() -> JsonDict:
        return bridge.reset()

    return mcp, mcp.streamable_http_app(), bridge
