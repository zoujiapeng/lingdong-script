"""Agent tool dispatcher and per-run tool session state.

Function-calling schemas live in ``tool_schemas.py``. This module keeps the
runtime state: latest Snapshot, current target hwnd, world model, undo stack,
dispatch, and shared target/element resolution. Concrete tool handlers live in
the sibling ``*_tools.py`` mixins. Every action tool can accept an ``expect``
condition (see action/verify.py DSL) and runs through Action→Verify→Retry.
"""
from __future__ import annotations

import json
from typing import Any

import win32gui

_GA_ROOT = 2

from ..action import executor, shell, verify
from ..config import CONFIG
from ..perception import apps, fusion, ocr, search, tray, vision_cv, win32, world
from ..perception.app_aliases import expand as expand_app_aliases
from ..utils.log import BUS, logger
from ..utils.screenshot import Region, capture, window_region
from . import planner

# Several imports above are retained as stable monkeypatch targets for older
# tests and scripts that patch winpilot.agent.tools.<module>. The mixins read
# them through their _tools_attr compatibility helper.
from .desktop_tools import DesktopToolMixin
from .file_tools import FileToolMixin
from .interaction_tools import InteractionToolMixin
from .memory_tools import MemoryToolMixin
from .perception_tools import PerceptionToolMixin
from .plan_tools import PlanToolMixin
from .system_tools import SystemToolMixin
from .tool_schemas import tool_schemas
from .tool_result import ToolResult
from .window_tools import WindowToolMixin


class ToolSession(
    FileToolMixin,
    MemoryToolMixin,
    DesktopToolMixin,
    WindowToolMixin,
    PerceptionToolMixin,
    InteractionToolMixin,
    SystemToolMixin,
    PlanToolMixin,
):
    """Per-run state: latest snapshot + current target window."""

    def __init__(self, stop_event=None, task: str = "") -> None:
        self.snapshot: fusion.Snapshot | None = None
        self.hwnd: int | None = None
        self.task = task or ""
        self._pending_visual_advisory = ""
        self._last_visual_advisory = ""
        self.target_hint: str = ""
        # Shared with the runner so long ops (reuse_skill replay) honor Stop.
        import threading
        self.stop_event = stop_event or threading.Event()
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

    # ----------------------------------------------------------- dispatch
    def dispatch(self, name: str, args: dict[str, Any]) -> ToolResult:
        handler = getattr(self, f"_t_{name}", None)
        if handler is None:
            return ToolResult(f"未知工具: {name}")
        try:
            result = handler(args)
        except Exception as exc:
            logger.exception("工具 %s 执行异常", name)
            return ToolResult(f"工具 {name} 执行异常: {exc}")
        if self.world_enabled:
            try:
                self.world.record_action(name, args)
                if result.is_observe and self.snapshot is not None:
                    self.world.observe_snapshot(self.snapshot)
            except Exception:
                logger.exception("世界模型更新失败")  # never block tool flow
        return result

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
        if self._window_alive(self.hwnd):
            return self.hwnd
        target = self._find_target_window()
        if target:
            self._remember_target(target)
            return target
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
