"""Agent tool dispatcher and per-run tool session state.

Function-calling schemas live in ``tool_schemas.py``. This module keeps the
runtime state: latest Snapshot, current target hwnd, world model, undo stack,
dispatch, and shared target/element resolution. Concrete tool handlers live in
the sibling ``*_tools.py`` mixins. Every action tool can accept an ``expect``
condition (see action/verify.py DSL) and runs through Action→Verify→Retry.
"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

import win32gui
import win32process

_GA_ROOT = 2

from ..action import executor, shell, verify
from ..config import CONFIG
from ..fast.vision import changed_regions
from ..perception import apps, fusion, ocr, search, tray, vision_cv, win32, world
from ..perception.model import UIElement
from ..perception.app_aliases import expand as expand_app_aliases
from ..utils.log import BUS, logger
from ..utils.screenshot import Region, capture, window_region
from . import planner, scope_guard
from .task_rules import (
    _communication_mode,
    _gui_only_blocked_hotkey,
    _is_multi_source_research_task,
)

# Several imports above are retained as stable monkeypatch targets for older
# tests and scripts that patch winpilot.agent.tools.<module>. The mixins read
# them through their _tools_attr compatibility helper.
from .coding_tools import CodingSession
from .desktop_tools import DesktopToolMixin
from .file_tools import FileToolMixin
from .fast_tools import FastToolMixin
from .interaction_tools import (
    InteractionToolMixin,
    _call_with_optional_hwnd,
    normalize_key_sequence,
)
from .memory_tools import MemoryToolMixin
from .perception_tools import PerceptionToolMixin
from .plan_tools import PlanToolMixin
from .system_tools import SystemToolMixin
from .tool_schemas import tool_schemas
from .tool_result import ToolResult
from .runtime_profile import RuntimeProfile, build_runtime_profile
from .window_tools import WindowToolMixin
from .working_set_tools import WorkingSetToolMixin

_MUTATING_TOOLS = {
    "open_app", "launch", "click", "type_text", "hotkey", "scroll", "drag", "draw_strokes",
    "focus_window", "wait", "tray_click", "run_shell", "run_detached",
    "write_file", "apply_patch", "clipboard_write", "clipboard_paste", "paste_image_file", "mouse_down",
    "mouse_up", "move_mouse", "hold_key", "run_action_batch", "execute_subgoal",
    "coordinate_click_test",
}
_ACTION_SCREEN_DELTA_TOOLS = {
    "click",
    "type_text",
    "hotkey",
    "scroll",
    "focus_window",
    "tray_click",
    "clipboard_paste",
    "paste_image_file",
    "mouse_down",
    "mouse_up",
    "move_mouse",
    "hold_key",
    "coordinate_click_test",
}
_HARD_REPEAT_GUARD_TOOLS = {"open_app", "launch", "run_shell", "run_detached"}
_DRAFT_SEND_MARKERS = (
    "send", "submit", "reply", "\u53d1\u9001", "\u53d1\u51fa", "\u63d0\u4ea4", "\u56de\u590d",
)


class ToolSession(
    FileToolMixin,
    WorkingSetToolMixin,
    MemoryToolMixin,
    DesktopToolMixin,
    WindowToolMixin,
    PerceptionToolMixin,
    InteractionToolMixin,
    SystemToolMixin,
    FastToolMixin,
    PlanToolMixin,
):
    """Per-run state: latest snapshot + current target window."""

    def __init__(self, stop_event=None, task: str = "", runtime_profile: RuntimeProfile | None = None) -> None:
        self.snapshot: fusion.Snapshot | None = None
        self.hwnd: int | None = None
        self.task = task or ""
        # Shared with the runner so long ops (reuse_skill replay) honor Stop.
        import threading
        self.stop_event = stop_event or threading.Event()
        self.runtime_profile = runtime_profile or build_runtime_profile(self.task)
        self.coding_runtime = (
            CodingSession(
                self.runtime_profile.coding_workspace,
                stop_event=self.stop_event,
                task=self.task,
            )
            if self.runtime_profile.coding and self.runtime_profile.coding_workspace is not None
            else None
        )
        self._pending_visual_advisory = ""
        self._last_visual_advisory = ""
        self.target_hint: str = ""
        # World model is fed from dispatch(); read once per run (runs are
        # short-lived, created per task) so a runtime toggle change applies
        # to the next task rather than mid-run.
        self.world_enabled: bool = bool(CONFIG.get("agent", "world_model", default=True))
        self.world = world.WorldModel()
        # Macro recovery: give the model one forced reconsideration before a
        # fail() actually terminates the run.
        self.replan_on_fail: bool = bool(CONFIG.get("agent", "replan_on_fail", default=True))
        # Real mouse press/release is the default for grounded anchors so the
        # screen-delta press probe can observe transient pointer-down states.
        # Individual actions may opt back into UIA Invoke.
        self.physical_anchor_clicks: bool = bool(
            CONFIG.get("controller", "physical_anchor_clicks", default=True)
            and not CONFIG.get(
                "controller", "multi_input_cooperation", default=False,
            )
        )
        self._fail_softened = False
        self.plan: planner.Plan | None = None
        # Lightweight undo stack: things this run created that can be rolled back.
        self._undo_stack: list[tuple[str, object]] = []
        self._screen_epoch = 0
        self._verified_subgoal_at_epoch: tuple[str, int] | None = None
        self._subgoal_no_progress_counts: dict[str, int] = {}
        # Bind failure debt to the window/canvas generation that produced it.
        # Otherwise two failures before a real focus/canvas correction become a
        # permanent latch and prevent the corrected plan from ever executing.
        self._subgoal_no_progress_contexts: dict[str, str] = {}
        self._research_exploration_actions = 0
        self._observe_cache: dict[str, Any] | None = None
        self._screen_watch_auto_started = False
        self._predictive_ocr_lock = threading.Lock()
        self._predictive_ocr_cache: dict[str, Any] | None = None
        self.visible_evidence_ledger: list[dict[str, Any]] = []
        self._pending_controller_events: list[dict[str, Any]] = []
        self.saved_screenshot_paths: list[str] = []
        # Recipe ids whose replay was actually attempted via reuse_skill.
        # Prompt-injected hints alone must not be scored as successful reuse.
        self.used_memory_recipe_ids: list[str] = []
        # Per-attempt replay outcome: {"id": recipe_id, "replay_ok": bool}.
        # A live fallback after failed replay must not promote the recipe.
        self.memory_recipe_attempts: list[dict[str, Any]] = []

    # ----------------------------------------------------------- dispatch
    def dispatch(self, name: str, args: dict[str, Any]) -> ToolResult:
        handler = getattr(self, f"_t_{name}", None)
        if handler is None:
            return ToolResult(f"未知工具: {name}")
        if name == "hotkey":
            args = dict(args)
            args["keys"] = normalize_key_sequence(args.get("keys"))
        if name == "hotkey":
            blocked_hotkey = _gui_only_blocked_hotkey(self.task, args.get("keys") or [])
            if blocked_hotkey:
                return ToolResult(blocked_hotkey)
        draft_block = self._draft_communication_block(name, args)
        if draft_block:
            return ToolResult(json.dumps({
                "ok": False,
                "verified": False,
                "blocked": True,
                "draft_send_blocked": True,
                "error": draft_block,
                "fast_path_advice": (
                    "The user requested a draft only. Type into the composer without a click on "
                    "Send and without Enter, then verify the draft is still visible."
                ),
            }, ensure_ascii=False))
        if name == "execute_subgoal":
            signature = self._subgoal_signature(args)
            if self._verified_subgoal_at_epoch == (signature, self._screen_epoch):
                return ToolResult(self._fmt({
                    "ok": False,
                    "verified": False,
                    "blocked": True,
                    "duplicate_verified_subgoal": True,
                    "error": (
                        "This exact GUI subgoal was already verified on the current "
                        "screen epoch. Record the evidence, complete the matching plan "
                        "item, or advance to the next distinct subgoal instead of "
                        "repeating it."
                    ),
                    "needs_replan": False,
                }))
            if self._subgoal_no_progress_counts.get(signature, 0) >= 2:
                recovery_context = self._subgoal_recovery_context()
                previous_failure_context = self._subgoal_no_progress_contexts.get(signature)
                environment_changed = bool(
                    previous_failure_context
                    and previous_failure_context != recovery_context
                )
                if environment_changed:
                    # The same semantic request may be reasonable after a real
                    # target/epoch/foreground transition. Apply this uniformly
                    # to GUI work instead of giving one test domain a private
                    # recovery lane.
                    # A handler/scope failure before normal result accounting must
                    # not leave the same recovery transition reusable forever.
                    self._subgoal_no_progress_contexts[signature] = recovery_context
                else:
                    return ToolResult(self._fmt({
                        "ok": False,
                        "verified": False,
                        "blocked": True,
                        "duplicate_no_progress_subgoal": True,
                        "error": (
                            "The same GUI subgoal produced no verified visual progress twice. "
                            "Do not click it again; use the visible evidence already collected, "
                            "choose a different control/strategy, or record unavailable fields."
                        ),
                        "needs_replan": True,
                    }))
        if self._research_budget_blocks(name, args):
            return ToolResult(self._fmt({
                "ok": False,
                "verified": False,
                "blocked": True,
                "research_exploration_budget_exhausted": True,
                "exploration_actions": self._research_exploration_actions,
                "error": (
                    "This source has already received a reasonable GUI exploration budget. "
                    "Call record_facts now with every visibly supported field and explicit "
                    "not-visible values for the rest, then advance to the next source."
                ),
                "needs_replan": False,
            }))
        if (
            self.world_enabled
            and name in _HARD_REPEAT_GUARD_TOOLS
            and self.world.prospective_repeat_count(name, args) >= 4
        ):
            return ToolResult(json.dumps({
                "ok": False,
                "verified": False,
                "blocked": True,
                "error": (
                    "duplicate_action_guard blocked the fourth consecutive identical "
                    f"{name} call. Inspect the previous result and choose a different "
                    "tool or strategy instead of repeating it."
                ),
                "tool": name,
                "repeat_count": self.world.prospective_repeat_count(name, args),
                "needs_replan": True,
            }, ensure_ascii=False))
        guard_result = scope_guard.ensure_scope(self, name, args)
        if guard_result is not None:
            return guard_result
        screen_delta_context = None
        press_probe_previous = None
        press_probe_installed = False
        if self._action_screen_delta_enabled(name, args):
            screen_delta_context = self._prime_action_screen_delta(name, args)
            callback = screen_delta_context.get("_screen_delta_press_probe")
            install_probe = getattr(executor, "install_press_probe", None)
            if callable(callback) and callable(install_probe):
                press_probe_previous = install_probe(callback)
                press_probe_installed = True
        try:
            result = handler(args)
        except Exception as exc:
            logger.exception("工具 %s 执行异常", name)
            return ToolResult(f"工具 {name} 执行异常: {exc}")
        finally:
            if press_probe_installed:
                restore_probe = getattr(executor, "restore_press_probe", None)
                if callable(restore_probe):
                    restore_probe(press_probe_previous)
        if screen_delta_context is not None:
            result = self._attach_action_screen_delta(result, screen_delta_context)
        if self.world_enabled:
            try:
                self.world.record_action(name, args)
                try:
                    outcome = json.loads(result.text or "{}")
                except (json.JSONDecodeError, TypeError):
                    outcome = {}
                if isinstance(outcome, dict):
                    self.world.record_action_outcome(name, outcome)
                if result.is_observe and self.snapshot is not None:
                    self.world.observe_snapshot(self.snapshot)
                screen_status = self._fast_screen_status()
                if screen_status:
                    self.world.record_screen_bulletin(screen_status)
            except Exception:
                logger.exception("世界模型更新失败")  # never block tool flow
        if name in _MUTATING_TOOLS:
            self._screen_epoch += 1
            self._observe_cache = None
        if name == "execute_subgoal":
            try:
                payload = json.loads(result.text or "{}")
            except (json.JSONDecodeError, TypeError):
                payload = {}
            if isinstance(payload, dict) and payload.get("verified") is True:
                self._verified_subgoal_at_epoch = (
                    self._subgoal_signature(args),
                    self._screen_epoch,
                )
                self._subgoal_no_progress_counts.pop(self._subgoal_signature(args), None)
                self._subgoal_no_progress_contexts.pop(self._subgoal_signature(args), None)
            elif isinstance(payload, dict):
                signature = self._subgoal_signature(args)
                if self._subgoal_has_visual_progress(payload):
                    # A failed final palette/verification step does not erase
                    # primitives that the same controller transaction already
                    # delivered.  Clear stale failure debt so a missing-part
                    # repair is not permanently locked out.
                    self._subgoal_no_progress_counts.pop(signature, None)
                    self._subgoal_no_progress_contexts.pop(signature, None)
                else:
                    self._subgoal_no_progress_counts[signature] = (
                        self._subgoal_no_progress_counts.get(signature, 0) + 1
                    )
                    self._subgoal_no_progress_contexts[signature] = (
                        self._subgoal_recovery_context()
                    )
        if _is_multi_source_research_task(self.task):
            if name == "record_facts":
                try:
                    record_payload = json.loads(result.text or "{}")
                except (json.JSONDecodeError, TypeError):
                    record_payload = {}
                if isinstance(record_payload, dict) and record_payload.get("created_new_source") is True:
                    self._research_exploration_actions = 0
            elif name in {"scroll", "observe", "read_text", "execute_subgoal", "run_action_batch"}:
                self._research_exploration_actions += 1
        if name in {"open_app", "launch", "focus_window", "observe", "read_text"}:
            self._ensure_screen_watch(f"after:{name}")
        return result

    def _draft_communication_block(self, name: str, args: dict[str, Any]) -> str:
        if _communication_mode(self.task) != "draft":
            return ""

        def has_send_marker(value: object) -> bool:
            text = str(value or "").strip().casefold()
            return bool(text and any(marker in text for marker in _DRAFT_SEND_MARKERS))

        def keys_submit(value: object) -> bool:
            keys = value if isinstance(value, list) else [value]
            normalized = {str(key or "").strip().casefold() for key in keys}
            return bool(normalized.intersection({"enter", "return", "numpad_enter", "kp_enter"}))

        def inspect(action_name: str, payload: dict[str, Any]) -> bool:
            if action_name == "hotkey" and keys_submit(payload.get("keys")):
                return True
            if action_name in {"click", "click_element"}:
                if has_send_marker(payload.get("label")):
                    return True
                if any(has_send_marker(item) for item in (payload.get("fallback_text") or [])):
                    return True
                element_id = payload.get("element_id")
                x, y = payload.get("x"), payload.get("y")
                for element in (getattr(self.snapshot, "elements", None) or []):
                    if not has_send_marker(getattr(element, "text", "")):
                        continue
                    if element_id is not None and int(getattr(element, "elem_id", -1)) == int(element_id):
                        return True
                    if x is not None and y is not None:
                        right = int(getattr(element, "left", 0)) + int(getattr(element, "width", 0))
                        bottom = int(getattr(element, "top", 0)) + int(getattr(element, "height", 0))
                        if (
                            int(getattr(element, "left", 0)) <= int(x) <= right
                            and int(getattr(element, "top", 0)) <= int(y) <= bottom
                        ):
                            return True
            return False

        if inspect(name, args):
            return "Draft-only task blocked a send/submit action."
        if name == "execute_subgoal":
            clicks = [args.get("click"), *(args.get("clicks") or [])]
            if any(isinstance(item, dict) and inspect("click_element", item) for item in clicks):
                return "Draft-only task blocked a Send click inside execute_subgoal."
            input_spec = args.get("input") if isinstance(args.get("input"), dict) else {}
            if input_spec.get("submit") is True or input_spec.get("press_enter") is True:
                return "Draft-only task blocked input submission."
        if name == "run_action_batch":
            for action in args.get("actions") or []:
                if not isinstance(action, dict):
                    continue
                action_name = str(action.get("action") or action.get("tool") or "")
                payload = dict(action.get("params") or {})
                payload.update({key: value for key, value in action.items() if key != "params"})
                if inspect(action_name, payload):
                    return "Draft-only task blocked a send/submit action inside the batch."
        return ""

    @staticmethod
    def _subgoal_signature(args: dict[str, Any]) -> str:
        semantic = {
            key: value
            for key, value in (args or {}).items()
            if key not in {"target_window", "expect", "timeout_s"}
        }
        return json.dumps(semantic, ensure_ascii=False, sort_keys=True, default=str)

    def _subgoal_recovery_context(self) -> str:
        """Stable evidence that a failed GUI subgoal's environment changed.

        The action epoch is recorded *after* each failed execute_subgoal, so an
        immediate identical retry sees the same value.  Window/canvas metadata,
        explicit document generations, and the world's structural observation
        allow a genuine recovery to earn one retry without treating a newly
        captured but otherwise identical frame as progress.
        """
        snapshot = self.snapshot
        snapshot_region = getattr(snapshot, "region", None)
        snapshot_elements = sorted({
            f"{getattr(element, 'role', '')}:{(getattr(element, 'text', '') or '').strip()}"
            for element in (getattr(snapshot, "elements", None) or [])
            if (getattr(element, "text", "") or "").strip()
        })
        world_observe = getattr(self.world, "_last_observe", None)
        generations = {
            name: getattr(self, name)
            for name in (
                "_canvas_generation",
                "_drawing_generation",
                "_document_generation",
                "_new_blank_generation",
                "_new_blank_applied",
            )
            if hasattr(self, name)
        }
        context = {
            "screen_epoch": int(self._screen_epoch),
            "target_hwnd": int(self.hwnd or 0),
            "snapshot": {
                "hwnd": int(getattr(snapshot, "hwnd", 0) or 0),
                "title": str(getattr(snapshot, "title", "") or ""),
                "region": (
                    {
                        "x": int(getattr(snapshot_region, "left", 0)),
                        "y": int(getattr(snapshot_region, "top", 0)),
                        "width": int(getattr(snapshot_region, "width", 0)),
                        "height": int(getattr(snapshot_region, "height", 0)),
                    }
                    if snapshot_region is not None
                    else None
                ),
                "elements": snapshot_elements,
            },
            "canvas": getattr(self, "_last_canvas_region", None),
            "generations": generations,
            "world": {
                "title": str(getattr(world_observe, "title", "") or ""),
                "fingerprint": sorted(getattr(world_observe, "fingerprint", ()) or ()),
                "element_count": int(getattr(world_observe, "count", 0) or 0),
                # This sequence advances only when the watcher reports a real
                # changed-screen bulletin, not on every sample/observe.
                "screen_change_sequence": int(
                    getattr(self.world, "_screen_notice_seq", 0) or 0
                ),
            },
        }
        return json.dumps(context, ensure_ascii=False, sort_keys=True, default=str)

    @staticmethod
    def _subgoal_has_visual_progress(payload: dict[str, Any]) -> bool:
        if payload.get("verified") is True:
            return True
        packet = payload.get("evidence_packet") or {}
        navigation = packet.get("navigation") or {}
        if navigation.get("ok") is True and navigation.get("required") is True:
            return True
        scene = packet.get("scene_structure") or {}
        try:
            if int(scene.get("executed_draw_count") or 0) > 0:
                return True
        except (TypeError, ValueError):
            pass
        per_draw = [
            *(packet.get("per_draw_color_evidence") or []),
            *(payload.get("per_draw_color_evidence") or []),
        ]
        if any(
            isinstance(item, dict)
            and (item.get("matched") is True or item.get("visual_color_matched") is True)
            for item in per_draw
        ):
            return True
        for item in payload.get("results") or []:
            if not isinstance(item, dict):
                continue
            data = item.get("data") if isinstance(item.get("data"), dict) else {}
            delta = data.get("screen_delta") if isinstance(data.get("screen_delta"), dict) else {}
            wait_delta = data.get("delta") if isinstance(data.get("delta"), dict) else {}
            if (
                data.get("visual_change_detected") is True
                or delta.get("changed") is True
                or wait_delta.get("changed") is True
                or (item.get("action") == "wait_change" and data.get("changed") is True)
                or (
                    item.get("ok") is not False
                    and item.get("action") in {"draw_strokes", "drag"}
                    and data.get("blocked") is not True
                )
            ):
                return True
        return False

    def _research_budget_blocks(self, name: str, args: dict[str, Any]) -> bool:
        if not _is_multi_source_research_task(self.task):
            return False
        if self._research_exploration_actions < 10:
            return False
        # This is a source-stage barrier, not merely a scroll limit. Once the
        # reasonable exploration budget is exhausted, do not let a weak model
        # evade convergence by renaming the same mutation as click, launch,
        # focus, hotkey, or a nested batch. A newly created source record resets
        # the stage and unlocks navigation to the next source.
        return name in _MUTATING_TOOLS

    def _action_screen_delta_enabled(self, name: str, args: dict[str, Any]) -> bool:
        if name not in _ACTION_SCREEN_DELTA_TOOLS:
            return False
        if args.get("skip_screen_delta") is True:
            return False
        return bool(CONFIG.get("fusion", "action_screen_delta", default=True))

    def _prime_action_screen_delta(
        self,
        action_name: str = "",
        action_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = self._action_target_context()
        context["_screen_delta_action"] = action_name
        context["_screen_delta_args"] = dict(action_args or {})
        controller = None
        try:
            self._ensure_screen_watch("visible_action")
            controller = self._fast_controller()
            capture_fn = getattr(controller, "capture_fn", None)
            target_region_fn = getattr(controller, "_target_region", None)
            if callable(capture_fn):
                region = target_region_fn() if callable(target_region_fn) else None
                started = time.monotonic()
                frame = capture_fn(region)
                context["_screen_delta_before_frame"] = frame
                context["_screen_delta_region"] = region
                context["_screen_delta_capture_ms"] = int((time.monotonic() - started) * 1000)
                context["_screen_delta_press_frames"] = []
                context["_screen_delta_press_capture_ms"] = []

                def capture_pressed_state() -> None:
                    probe_started = time.monotonic()
                    pressed_frame = capture_fn(region)
                    probe_capture_ms = int((time.monotonic() - probe_started) * 1000)
                    context["_screen_delta_press_frames"].append(pressed_frame)
                    context["_screen_delta_press_capture_ms"].append(
                        probe_capture_ms
                    )
                    pressed_delta = changed_regions(
                        frame,
                        pressed_frame,
                        root_region=region,
                        pixel_thresh=getattr(controller.watcher, "pixel_thresh", 12),
                    )
                    if pressed_delta.changed and hasattr(controller.watcher, "record_action_delta"):
                        try:
                            controller.watcher.record_action_delta(
                                pressed_delta,
                                region,
                                capture_ms=probe_capture_ms,
                                diff_ms=pressed_delta.elapsed_ms,
                                evidence_frame=pressed_frame,
                                transient=True,
                            )
                        except TypeError:
                            controller.watcher.record_action_delta(
                                pressed_delta,
                                region,
                                capture_ms=probe_capture_ms,
                                diff_ms=pressed_delta.elapsed_ms,
                            )

                context["_screen_delta_press_probe"] = capture_pressed_state
            else:
                controller.watcher.sample_once()
            context["screen_delta_primed"] = True
        except Exception:
            logger.debug("action screen-delta pre-sample failed", exc_info=True)
            context["screen_delta_primed"] = False
        return context

    def _attach_action_screen_delta(
        self,
        result: ToolResult,
        before_context: dict[str, Any],
    ) -> ToolResult:
        try:
            data = json.loads(result.text or "{}")
        except (json.JSONDecodeError, TypeError):
            return result
        if not isinstance(data, dict):
            return result
        try:
            controller = self._fast_controller()
            watcher = controller.watcher
            before_frame = before_context.get("_screen_delta_before_frame")
            source = "after"
            after_delta = None
            selected_capture_ms = 0
            if before_frame is not None and callable(getattr(controller, "capture_fn", None)):
                region = before_context.get("_screen_delta_region")
                capture_started = time.monotonic()
                after_frame = controller.capture_fn(region)
                capture_ms = int((time.monotonic() - capture_started) * 1000)
                after_delta = changed_regions(
                    before_frame,
                    after_frame,
                    root_region=region,
                    pixel_thresh=getattr(watcher, "pixel_thresh", 12),
                )
                candidates = [("after", after_delta, capture_ms, after_frame)]
                press_frames = before_context.get("_screen_delta_press_frames") or []
                press_latencies = before_context.get("_screen_delta_press_capture_ms") or []
                for index, pressed_frame in enumerate(press_frames):
                    pressed_delta = changed_regions(
                        before_frame,
                        pressed_frame,
                        root_region=region,
                        pixel_thresh=getattr(watcher, "pixel_thresh", 12),
                    )
                    latency = int(press_latencies[index]) if index < len(press_latencies) else 0
                    candidates.append(("press", pressed_delta, latency, pressed_frame))
                source, delta, selected_capture_ms, selected_frame = max(
                    candidates,
                    key=lambda item: (
                        bool(item[1].changed),
                        float(item[1].ratio),
                        sum(r.width * r.height for r in item[1].regions),
                        item[0] == "press",
                    ),
                )
                retry_result = None
                if (
                    before_context.get("_screen_delta_action") == "scroll"
                    and not delta.changed
                    and data.get("via") == "post_message"
                ):
                    action_args = before_context.get("_screen_delta_args") or {}
                    retry = getattr(executor, "scroll_sendinput_restored", None)
                    if callable(retry):
                        retry_result = _call_with_optional_hwnd(
                            retry,
                            int(action_args.get("x", 0)),
                            int(action_args.get("y", 0)),
                            int(action_args.get("amount", 0)),
                            hwnd=self.hwnd,
                        )
                        retry_ok = bool(
                            isinstance(retry_result, dict)
                            and retry_result.get("ok") is True
                            and retry_result.get("blocked") is not True
                        )
                        data["scroll_retry"] = {
                            "attempted": True,
                            "via": "sendinput_restored",
                            "ok": retry_ok,
                            "blocked": bool(
                                isinstance(retry_result, dict)
                                and retry_result.get("blocked")
                            ),
                            "changed": False,
                        }
                        if not retry_ok:
                            # A failed ownership check means no physical wheel
                            # action occurred.  Pixel noise after that refusal
                            # must never turn the transaction back into success.
                            data["ok"] = False
                            data["verified"] = False
                            data["blocked"] = bool(
                                isinstance(retry_result, dict)
                                and retry_result.get("blocked")
                            )
                            if isinstance(retry_result, dict):
                                for key in (
                                    "cooperative_input", "target_hwnd",
                                    "foreground_hwnd", "error",
                                ):
                                    if key in retry_result:
                                        data[key] = retry_result[key]
                        else:
                            time.sleep(0.08)
                            retry_started = time.monotonic()
                            retry_frame = controller.capture_fn(region)
                            retry_capture_ms = int((time.monotonic() - retry_started) * 1000)
                            retry_delta = changed_regions(
                                before_frame,
                                retry_frame,
                                root_region=region,
                                pixel_thresh=getattr(watcher, "pixel_thresh", 12),
                            )
                            source = "scroll_retry"
                            delta = retry_delta
                            selected_capture_ms = retry_capture_ms
                            selected_frame = retry_frame
                            data["scroll_retry"]["changed"] = bool(retry_delta.changed)
                            if retry_delta.changed:
                                data["via"] = "sendinput_restored"
                            else:
                                data["ok"] = False
                                data["verified"] = False
                                data["no_visible_effect"] = True
                                data["needs_observe"] = True
                                data["error"] = (
                                    "scroll produced no visible screen change after both "
                                    "window-message and physical-input attempts"
                                )
                if hasattr(watcher, "record_action_delta"):
                    try:
                        watcher.record_action_delta(
                            delta,
                            region,
                            capture_ms=selected_capture_ms,
                            diff_ms=delta.elapsed_ms,
                            evidence_frame=selected_frame,
                            transient=source == "press",
                        )
                    except TypeError:
                        watcher.record_action_delta(
                            delta,
                            region,
                            capture_ms=selected_capture_ms,
                            diff_ms=delta.elapsed_ms,
                        )
                evidence_path = None
                evidence_region = None
                if delta.changed and callable(getattr(watcher, "action_evidence", None)):
                    crops, _ = watcher.action_evidence(5000)
                    if crops:
                        evidence_frame, evidence_roi = crops[0]
                        try:
                            from ..recorder.trace import RECORDER

                            evidence_path = RECORDER.save_evidence_image(
                                evidence_frame,
                                "transient_press_roi" if source == "press" else "action_delta_roi",
                            )
                            evidence_region = self._region_dict(evidence_roi)
                        except Exception:
                            logger.debug("action evidence persistence failed", exc_info=True)
                if delta.changed:
                    self._schedule_predictive_ocr(watcher)
            else:
                capture_ms = 0
                delta = watcher.sample_once()
                after_delta = delta
                evidence_path = None
                evidence_region = None
            status = self._fast_screen_status()
        except Exception:
            logger.debug("action screen-delta post-sample failed", exc_info=True)
            return result
        public_before_context = {
            key: value for key, value in before_context.items()
            if not str(key).startswith("_")
        }
        after_context = self._action_target_context()
        delta_data = delta.to_dict()
        effective_capture_ms = selected_capture_ms if before_frame is not None else capture_ms
        if effective_capture_ms:
            delta_data["capture_latency_ms"] = effective_capture_ms
        if before_frame is not None:
            press_count = len(before_context.get("_screen_delta_press_frames") or [])
            delta_data["press_sample_count"] = press_count
            delta_data["delta_source"] = source
            delta_data["transient_observed"] = bool(
                delta.changed
                and source == "press"
                and after_delta is not None
                and (
                    before_context.get("_screen_delta_action") == "mouse_down"
                    or not after_delta.changed
                    or float(delta.ratio) > float(after_delta.ratio)
                )
            )
        if evidence_path:
            delta_data["evidence_path"] = evidence_path
            delta_data["evidence_region"] = evidence_region
            delta_data["evidence_kind"] = (
                "transient_press_roi" if delta_data.get("transient_observed") else "action_delta_roi"
            )
        target_region = self._region_from_dict(
            after_context.get("target_region") or public_before_context.get("target_region")
        )
        if target_region is not None:
            changed_in_target = [
                r for r in delta.regions
                if self._regions_intersect(r, target_region)
            ]
            delta_data["changed_in_target_region"] = bool(delta.changed and changed_in_target)
            delta_data["target_region_changed_count"] = len(changed_in_target)
        if after_context.get("target_hwnd"):
            delta_data["foreground_matches_target"] = bool(
                after_context.get("foreground_matches_target")
            )
            if delta.changed and not delta_data["foreground_matches_target"]:
                delta_data["target_warning"] = (
                    "screen changed while the foreground window did not match the "
                    "session target; treat this as weak evidence and re-focus/observe "
                    "the target before marking success."
                )
        data.setdefault("screen_delta", delta_data)
        data.setdefault("action_target", {
            "before": public_before_context,
            "after": after_context,
        })
        if status:
            data.setdefault("screen_watch", status)
        BUS.publish(
            "screen_delta",
            run_id=getattr(self, "run_id", None),
            action=data.get("action") or "visible_action",
            changed=bool(delta_data.get("changed")),
            ratio=delta_data.get("ratio", 0.0),
            regions=delta_data.get("regions", []),
            transient_observed=bool(delta_data.get("transient_observed")),
            delta_source=delta_data.get("delta_source", "after"),
            capture_latency_ms=delta_data.get("capture_latency_ms", 0),
            diff_latency_ms=delta_data.get("elapsed_ms", 0),
            screenshot=delta_data.get("evidence_path"),
            screenshot_kind=delta_data.get("evidence_kind"),
            screenshot_region=delta_data.get("evidence_region"),
        )
        result.text = self._fmt(data)
        return result

    def _schedule_predictive_ocr(self, watcher: Any) -> None:
        if not bool(CONFIG.get("fusion", "predictive_ocr_enabled", default=True)):
            return
        # Shared daemon-managed watchers publish every changed ROI to the
        # persistent per-hwnd analyzer. Keep this legacy worker only for
        # injected/standalone watchers used outside that runtime.
        if getattr(watcher, "_perception_daemon_managed", False):
            return
        evidence_reader = getattr(watcher, "action_evidence", None)
        if not callable(evidence_reader):
            return
        crops, transient = evidence_reader(5000)
        if not crops:
            return
        status = watcher.status() if callable(getattr(watcher, "status", None)) else {}
        generation = int(status.get("last_action_change_ms") or 0)
        run_id = getattr(self, "run_id", None)

        def worker() -> None:
            started = time.monotonic()
            elements: list[UIElement] = []
            for image, evidence_region in crops:
                recognized = ocr.recognize(
                    image,
                    origin=(evidence_region.left, evidence_region.top),
                )
                merged = self._merge_ocr_line_elements(recognized)
                for element in [*recognized, *merged]:
                    if "predictive_ocr" not in element.states:
                        element.states.append("predictive_ocr")
                    if transient and "transient_action_evidence" not in element.states:
                        element.states.append("transient_action_evidence")
                elements.extend(recognized)
                elements.extend(merged)
            current = watcher.status() if callable(getattr(watcher, "status", None)) else {}
            if generation and int(current.get("last_action_change_ms") or 0) != generation:
                return
            elapsed_ms = int((time.monotonic() - started) * 1000)
            sample_texts = list(dict.fromkeys(
                str(element.text or "").strip()
                for element in elements
                if str(element.text or "").strip()
            ))[:12]
            with self._predictive_ocr_lock:
                self._predictive_ocr_cache = {
                    "generation": generation,
                    "created_ms": int(time.monotonic() * 1000),
                    "elements": elements,
                    "transient": bool(transient),
                    "elapsed_ms": elapsed_ms,
                    "texts": sample_texts,
                }
            BUS.publish(
                "perception_prefetch",
                run_id=run_id,
                dimension="ocr",
                count=len(elements),
                elapsed_ms=elapsed_ms,
                transient=bool(transient),
                texts=sample_texts,
            )

        threading.Thread(
            target=worker,
            daemon=True,
            name=f"predictive-ocr-{str(run_id or 'session')[-12:]}",
        ).start()

    @staticmethod
    def _region_dict(region: Region | None) -> dict[str, int] | None:
        if region is None:
            return None
        return {
            "left": int(region.left),
            "top": int(region.top),
            "width": int(region.width),
            "height": int(region.height),
        }

    @staticmethod
    def _region_from_dict(data: object) -> Region | None:
        if not isinstance(data, dict):
            return None
        try:
            return Region(
                int(data["left"]),
                int(data["top"]),
                int(data["width"]),
                int(data["height"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _regions_intersect(a: Region, b: Region) -> bool:
        return a.left < b.right and a.right > b.left and a.top < b.bottom and a.bottom > b.top

    def _window_related_to_target(self, hwnd: int | None, target: int | None) -> bool:
        if not hwnd or not target:
            return False
        if int(hwnd) == int(target):
            return True
        try:
            if win32gui.GetAncestor(int(hwnd), _GA_ROOT) == int(target):
                return True
        except Exception:
            pass
        try:
            owner = win32gui.GetWindow(int(hwnd), 4)  # GW_OWNER
            while owner:
                if int(owner) == int(target):
                    return True
                owner = win32gui.GetWindow(int(owner), 4)
        except Exception:
            pass
        return False

    @staticmethod
    def _same_window_process(hwnd: int | None, target: int | None) -> bool:
        if not hwnd or not target:
            return False
        try:
            _thread_a, pid_a = win32process.GetWindowThreadProcessId(int(hwnd))
            _thread_b, pid_b = win32process.GetWindowThreadProcessId(int(target))
            return bool(pid_a and pid_a == pid_b)
        except Exception:
            return False

    def _action_target_context(self) -> dict[str, Any]:
        target = int(self.hwnd) if self._window_alive(self.hwnd) else None
        try:
            fg = int(win32gui.GetForegroundWindow() or 0) or None
        except Exception:
            fg = None
        context: dict[str, Any] = {
            "target_hwnd": target,
            "foreground_hwnd": fg,
            "foreground_matches_target": self._window_related_to_target(fg, target),
        }
        if target:
            try:
                context["target_title"] = win32._window_text(target)
            except Exception:
                pass
            try:
                context["target_region"] = self._region_dict(window_region(target))
            except Exception:
                pass
        if fg:
            try:
                context["foreground_title"] = win32._window_text(fg)
            except Exception:
                pass
        return context

    def cached_observe(self, key: tuple, ttl_s: float) -> fusion.Snapshot | None:
        cache = self._observe_cache
        if not cache or cache.get("key") != key:
            return None
        if time.monotonic() - float(cache.get("created", 0.0)) > ttl_s:
            return None
        snapshot = cache.get("snapshot")
        return snapshot if isinstance(snapshot, fusion.Snapshot) else None

    def store_observe_cache(self, key: tuple, snapshot: fusion.Snapshot) -> None:
        self._observe_cache = {
            "key": key,
            "snapshot": snapshot,
            "created": time.monotonic(),
        }

    @staticmethod
    def _norm_window_text(value: object) -> str:
        return str(value or "").casefold()

    def _matches_target_hint(self, title: object, class_name: object = "") -> bool:
        needle = self._norm_window_text(self.target_hint)
        if not needle:
            return False
        haystacks = [
            self._norm_window_text(title),
            self._norm_window_text(class_name),
        ]
        needles = [self._norm_window_text(item) for item in expand_app_aliases(needle)]
        for item in needles or [needle]:
            if any(item and (item in hay or hay in item) for hay in haystacks):
                return True
        return any(needle and (needle in hay or hay in needle) for hay in haystacks)

    @staticmethod
    def _window_alive(hwnd: int | None) -> bool:
        return bool(hwnd and win32gui.IsWindow(int(hwnd)) and win32gui.IsWindowVisible(int(hwnd)))

    def _foreground_target_window(self) -> int | None:
        try:
            fg = win32gui.GetForegroundWindow()
            root = win32gui.GetAncestor(fg, _GA_ROOT) if fg else 0
        except Exception:
            return None
        for hwnd in (root, fg):
            if not self._window_alive(hwnd):
                continue
            try:
                title = win32._window_text(hwnd)
                class_name = win32gui.GetClassName(hwnd)
            except Exception:
                continue
            if self._matches_target_hint(title, class_name):
                return int(hwnd)
        return None

    def _remember_target(self, hwnd: int | None = None, hint: str = "") -> None:
        if hwnd and self._window_alive(hwnd):
            target_changed = int(self.hwnd or 0) != int(hwnd)
            self.hwnd = int(hwnd)
            if target_changed:
                # Element ids and input anchors are scoped to one observed
                # window. Carrying them across a target transition can act on
                # a stale control from the previous app.
                self._last_input_element = None
                try:
                    snapshot_hwnd = int(getattr(self.snapshot, "hwnd", 0) or 0)
                except (TypeError, ValueError):
                    snapshot_hwnd = 0
                if self.snapshot is not None and snapshot_hwnd != int(hwnd):
                    self.snapshot = None
                cached_snapshot = (
                    self._observe_cache.get("snapshot")
                    if isinstance(self._observe_cache, dict)
                    else None
                )
                try:
                    cached_hwnd = int(getattr(cached_snapshot, "hwnd", 0) or 0)
                except (TypeError, ValueError):
                    cached_hwnd = 0
                if self._observe_cache is not None and cached_hwnd != int(hwnd):
                    self._observe_cache = None
        hint = (hint or "").strip()
        if hint:
            self.target_hint = hint
        if self._window_alive(self.hwnd):
            self._bind_perception_target(int(self.hwnd))
            self._ensure_screen_watch("target")

    def _bind_perception_target(self, hwnd: int) -> None:
        controller = getattr(self, "_fast_gui_controller", None)
        if controller is None:
            return
        current = getattr(self, "_perception_lease", None)
        if current is not None and int(getattr(current, "hwnd", 0) or 0) == int(hwnd):
            return
        try:
            from ..fast.perception_daemon import PERCEPTION_DAEMON

            previous_watcher = getattr(controller, "watcher", None)
            if current is not None:
                PERCEPTION_DAEMON.release(current)
            lease = PERCEPTION_DAEMON.acquire(
                int(hwnd),
                run_id=str(getattr(self, "run_id", "") or ""),
            )
            if (
                current is None
                and previous_watcher is not None
                and previous_watcher is not lease.watcher
                and not getattr(previous_watcher, "_perception_daemon_managed", False)
            ):
                previous_watcher.stop()
            controller.watcher = lease.watcher
            controller.cache = lease.cache
            self._perception_lease = lease
        except Exception:
            logger.debug("perception daemon target bind failed", exc_info=True)

    def _ensure_screen_watch(self, reason: str = "") -> None:
        if not CONFIG.get("fusion", "screen_watch_autostart", default=True):
            return
        if not getattr(self, "run_id", None):
            return
        if not (self._window_alive(self.hwnd) or self.snapshot is not None):
            return
        controller = self._fast_controller()
        watcher = controller.watcher
        try:
            status = watcher.status()
        except Exception:
            status = {}
        if status.get("running"):
            return
        watcher.start()
        self._screen_watch_auto_started = True
        try:
            status = watcher.status()
            if self.world_enabled and status:
                self.world.record_screen_bulletin(status)
            BUS.publish(
                "screen_watch",
                run_id=getattr(self, "run_id", None),
                action="auto_start",
                reason=reason,
                screen_watch=status,
            )
        except Exception:
            logger.debug("screen_watch auto-start status publish failed", exc_info=True)

    def close(self) -> None:
        try:
            released = executor.release_held_mouse_buttons()
            if released:
                BUS.publish(
                    "input_cleanup",
                    run_id=getattr(self, "run_id", None),
                    released_mouse_buttons=released,
                    reason="session_close",
                )
        except Exception:
            logger.debug("held mouse cleanup failed", exc_info=True)
        controller = getattr(self, "_fast_gui_controller", None)
        if controller is None:
            return
        watcher = getattr(controller, "watcher", None)
        if watcher is None:
            return
        lease = getattr(self, "_perception_lease", None)
        if lease is not None:
            try:
                from ..fast.perception_daemon import PERCEPTION_DAEMON

                PERCEPTION_DAEMON.release(lease)
            except Exception:
                logger.debug("perception daemon release failed", exc_info=True)
            self._perception_lease = None
            return
        try:
            status = watcher.status()
        except Exception:
            status = {}
        if not status.get("running"):
            return
        try:
            watcher.stop()
            BUS.publish(
                "screen_watch",
                run_id=getattr(self, "run_id", None),
                action="auto_stop",
                reason="session_close",
                screen_watch=watcher.status(),
            )
        except Exception:
            logger.debug("screen_watch auto-stop failed", exc_info=True)

    def _find_target_window(self) -> int | None:
        if not self._norm_window_text(self.target_hint):
            return None
        for window in win32.list_windows():
            if self._matches_target_hint(window.get("title"), window.get("class_name")):
                hwnd = int(window.get("hwnd") or 0)
                if self._window_alive(hwnd):
                    return hwnd
        return None

    def _resolve_hwnd(self, args: dict) -> int | None:
        if CONFIG.get("controller", "multi_input_cooperation", default=False):
            # Read-only calls must not launder the human's foreground window
            # into an authorised mutation target.  Cooperative target binding
            # is established only by an explicit launch/focus workflow.
            current = self.hwnd if self._window_alive(self.hwnd) else None
            if not current:
                return None
            requested_raw = args.get("hwnd")
            if requested_raw not in (None, ""):
                try:
                    if int(requested_raw) != int(current):
                        return None
                except (TypeError, ValueError):
                    return None
            return int(current)
        if args.get("hwnd"):
            requested = int(args["hwnd"])
            if self._window_alive(requested):
                # A caller often repeats the parent hwnd it observed before a
                # modal dialog opened. Prefer the live owned foreground window
                # so Win32/OCR collection includes the dialog's real controls.
                try:
                    foreground = int(win32gui.GetForegroundWindow() or 0)
                except Exception:
                    foreground = 0
                if (
                    foreground
                    and foreground != requested
                    and self._window_alive(foreground)
                    and self._window_related_to_target(foreground, requested)
                ):
                    self._remember_target(foreground)
                    return foreground
                self._remember_target(requested)
                return requested
            # Stale explicit hwnds must not poison later observe/action calls.
            # Fall through to the remembered hint/current foreground recovery.
        foreground_target = self._foreground_target_window()
        if foreground_target:
            self._remember_target(foreground_target)
            return foreground_target
        try:
            fg = int(win32gui.GetForegroundWindow() or 0)
        except Exception:
            fg = 0
        if (
            fg
            and self._window_alive(fg)
            and self._window_alive(self.hwnd)
            and int(fg) != int(self.hwnd)
            and (
                self._window_related_to_target(fg, self.hwnd)
                or self._same_window_process(fg, self.hwnd)
            )
        ):
            self._remember_target(fg)
            return fg
        if self._window_alive(self.hwnd):
            return self.hwnd
        target = self._find_target_window()
        if target:
            self._remember_target(target)
            return target
        if not fg:
            fg = win32gui.GetForegroundWindow()
        return fg if fg else None

    def _resolve_element(self, elem_id: int):
        if self.snapshot is None:
            return None, "尚未 observe，无元素可用"
        live_target = self.hwnd if self._window_alive(self.hwnd) else None
        try:
            snapshot_hwnd = int(getattr(self.snapshot, "hwnd", 0) or 0)
        except (TypeError, ValueError):
            snapshot_hwnd = 0
        if live_target and snapshot_hwnd != int(live_target):
            return None, (
                f"element id={elem_id} belongs to stale snapshot hwnd={snapshot_hwnd}; "
                f"live target hwnd={int(live_target)}. Observe the live target again."
            )
        element = self.snapshot.by_id(elem_id)
        if element is None:
            return None, f"元素 id={elem_id} 不存在于最近一次 observe（请重新 observe）"
        return element, ""

    @staticmethod
    def _fmt(data: dict) -> str:
        return json.dumps(data, ensure_ascii=False, default=str)
