"""Agent tool dispatcher and per-run tool session state.

Function-calling schemas live in ``tool_schemas.py``. This module keeps the
runtime state: latest Snapshot, current target hwnd, world model, undo stack,
dispatch, and shared target/element resolution. Concrete tool handlers live in
the sibling ``*_tools.py`` mixins. Every action tool can accept an ``expect``
condition (see action/verify.py DSL) and runs through Action→Verify→Retry.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

import win32gui

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
from .task_rules import _gui_only_blocked_hotkey

# Several imports above are retained as stable monkeypatch targets for older
# tests and scripts that patch winpilot.agent.tools.<module>. The mixins read
# them through their _tools_attr compatibility helper.
from .coding_tools import CodingSession
from .desktop_tools import DesktopToolMixin
from .file_tools import FileToolMixin
from .fast_tools import FastToolMixin
from .interaction_tools import InteractionToolMixin, normalize_key_sequence
from .memory_tools import MemoryToolMixin
from .perception_tools import PerceptionToolMixin
from .plan_tools import PlanToolMixin
from .system_tools import SystemToolMixin
from .tool_schemas import tool_schemas
from .tool_result import ToolResult
from .runtime_profile import RuntimeProfile, build_runtime_profile
from .window_tools import WindowToolMixin

_MUTATING_TOOLS = {
    "launch", "click", "type_text", "hotkey", "scroll", "drag", "draw_strokes",
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


class ToolSession(
    FileToolMixin,
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
        )
        self._fail_softened = False
        self.plan: planner.Plan | None = None
        # Lightweight undo stack: things this run created that can be rolled back.
        self._undo_stack: list[tuple[str, object]] = []
        self._screen_epoch = 0
        self._observe_cache: dict[str, Any] | None = None
        self._screen_watch_auto_started = False
        self._predictive_ocr_lock = threading.Lock()
        self._predictive_ocr_cache: dict[str, Any] | None = None
        self.visible_evidence_ledger: list[dict[str, Any]] = []
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
        guard_result = scope_guard.ensure_scope(self, name, args)
        if guard_result is not None:
            return guard_result
        screen_delta_context = None
        press_probe_previous = None
        press_probe_installed = False
        if self._action_screen_delta_enabled(name, args):
            screen_delta_context = self._prime_action_screen_delta()
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
        if name in {"launch", "focus_window", "observe", "read_text"}:
            self._ensure_screen_watch(f"after:{name}")
        return result

    def _action_screen_delta_enabled(self, name: str, args: dict[str, Any]) -> bool:
        if name not in _ACTION_SCREEN_DELTA_TOOLS:
            return False
        if args.get("skip_screen_delta") is True:
            return False
        return bool(CONFIG.get("fusion", "action_screen_delta", default=True))

    def _prime_action_screen_delta(self) -> dict[str, Any]:
        context = self._action_target_context()
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
        try:
            data = json.loads(result.text or "{}")
        except (json.JSONDecodeError, TypeError):
            return result
        if not isinstance(data, dict):
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
                and (not after_delta.changed or float(delta.ratio) > float(after_delta.ratio))
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
            self.hwnd = int(hwnd)
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
        if args.get("hwnd"):
            hwnd = int(args["hwnd"])
            if self._window_alive(hwnd):
                self._remember_target(hwnd)
            return hwnd
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
            and self._window_related_to_target(fg, self.hwnd)
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
        element = self.snapshot.by_id(elem_id)
        if element is None:
            return None, f"元素 id={elem_id} 不存在于最近一次 observe（请重新 observe）"
        return element, ""

    @staticmethod
    def _fmt(data: dict) -> str:
        return json.dumps(data, ensure_ascii=False, default=str)
