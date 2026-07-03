"""Agent tool dispatcher and per-run tool session state.

Function-calling schemas live in ``tool_schemas.py``. This module keeps the
runtime state: latest Snapshot, current target hwnd, world model, undo stack,
dispatch, and shared target/element resolution. Concrete tool handlers live in
the sibling ``*_tools.py`` mixins. Every action tool can accept an ``expect``
condition (see action/verify.py DSL) and runs through Action→Verify→Retry.
"""
from __future__ import annotations

import json
import time
from typing import Any

import win32gui

_GA_ROOT = 2

from ..action import executor, shell, verify
from ..config import CONFIG
from ..fast.vision import changed_regions
from ..perception import apps, fusion, ocr, search, tray, vision_cv, win32, world
from ..perception.app_aliases import expand as expand_app_aliases
from ..utils.log import BUS, logger
from ..utils.screenshot import Region, capture, window_region
from . import planner, scope_guard

# Several imports above are retained as stable monkeypatch targets for older
# tests and scripts that patch winpilot.agent.tools.<module>. The mixins read
# them through their _tools_attr compatibility helper.
from .coding_tools import CodingSession
from .desktop_tools import DesktopToolMixin
from .file_tools import FileToolMixin
from .fast_tools import FastToolMixin
from .interaction_tools import InteractionToolMixin
from .memory_tools import MemoryToolMixin
from .perception_tools import PerceptionToolMixin
from .plan_tools import PlanToolMixin
from .system_tools import SystemToolMixin
from .tool_schemas import tool_schemas
from .tool_result import ToolResult
from .runtime_profile import build_runtime_profile
from .window_tools import WindowToolMixin

_MUTATING_TOOLS = {
    "launch", "click", "type_text", "hotkey", "scroll", "drag", "draw_strokes",
    "focus_window", "wait", "tray_click", "run_shell", "run_detached",
    "write_file", "apply_patch", "clipboard_write", "clipboard_paste", "mouse_down",
    "mouse_up", "move_mouse", "hold_key", "run_action_batch", "execute_subgoal",
}
_ACTION_SCREEN_DELTA_TOOLS = {
    "click",
    "type_text",
    "hotkey",
    "scroll",
    "focus_window",
    "tray_click",
    "clipboard_paste",
    "mouse_down",
    "mouse_up",
    "move_mouse",
    "hold_key",
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

    def __init__(self, stop_event=None, task: str = "") -> None:
        self.snapshot: fusion.Snapshot | None = None
        self.hwnd: int | None = None
        self.task = task or ""
        # Shared with the runner so long ops (reuse_skill replay) honor Stop.
        import threading
        self.stop_event = stop_event or threading.Event()
        self.runtime_profile = build_runtime_profile(self.task)
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
        self._fail_softened = False
        self.plan: planner.Plan | None = None
        # Lightweight undo stack: things this run created that can be rolled back.
        self._undo_stack: list[tuple[str, object]] = []
        self._screen_epoch = 0
        self._observe_cache: dict[str, Any] | None = None
        self._screen_watch_auto_started = False
        self.visible_evidence_ledger: list[dict[str, Any]] = []
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
        guard_result = scope_guard.ensure_scope(self, name, args)
        if guard_result is not None:
            return guard_result
        screen_delta_context = None
        if self._action_screen_delta_enabled(name, args):
            screen_delta_context = self._prime_action_screen_delta()
        try:
            result = handler(args)
        except Exception as exc:
            logger.exception("工具 %s 执行异常", name)
            return ToolResult(f"工具 {name} 执行异常: {exc}")
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
            if before_frame is not None and callable(getattr(controller, "capture_fn", None)):
                region = before_context.get("_screen_delta_region")
                capture_started = time.monotonic()
                after_frame = controller.capture_fn(region)
                capture_ms = int((time.monotonic() - capture_started) * 1000)
                delta = changed_regions(
                    before_frame,
                    after_frame,
                    root_region=region,
                    pixel_thresh=getattr(watcher, "pixel_thresh", 12),
                )
                if hasattr(watcher, "record_action_delta"):
                    watcher.record_action_delta(
                        delta,
                        region,
                        capture_ms=capture_ms,
                        diff_ms=delta.elapsed_ms,
                    )
            else:
                capture_ms = 0
                delta = watcher.sample_once()
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
        if capture_ms:
            delta_data["capture_latency_ms"] = capture_ms
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
        result.text = self._fmt(data)
        return result

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
            self._ensure_screen_watch("target")

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
