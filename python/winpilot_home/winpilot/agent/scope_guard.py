"""Generic foreground/target-window guard for GUI mutations.

The guard is deliberately app-agnostic. It does not know about QQ, Calculator,
Steam, browsers, or any target page. It only enforces that once a ToolSession
has established a target window, subsequent GUI mutations happen in that window
(or an owned/root-related foreground window) unless the action itself is a
focus/launch/window-discovery step.
"""
from __future__ import annotations

import json
import time
from typing import Any

import win32gui

from ..action import executor
from ..perception import win32
from .tool_result import ToolResult

GUI_SCOPE_MUTATIONS = {
    "click",
    "type_text",
    "hotkey",
    "scroll",
    "drag",
    "draw_strokes",
    "clipboard_paste",
    "mouse_down",
    "mouse_up",
    "move_mouse",
    "hold_key",
    "run_action_batch",
    "execute_subgoal",
}
_SCOPE_RECOVERY_TOOLS = {"launch", "focus_window", "tray_click", "scan_windows", "list_windows"}


def _title(hwnd: int | None) -> str:
    if not hwnd:
        return ""
    try:
        return win32._window_text(int(hwnd))
    except Exception:
        try:
            return win32gui.GetWindowText(int(hwnd)) or ""
        except Exception:
            return ""


def _class_name(hwnd: int | None) -> str:
    if not hwnd:
        return ""
    try:
        return win32gui.GetClassName(int(hwnd)) or ""
    except Exception:
        return ""


def _window_alive(hwnd: int | None) -> bool:
    try:
        return bool(hwnd and win32gui.IsWindow(int(hwnd)) and win32gui.IsWindowVisible(int(hwnd)))
    except Exception:
        return False


def _same_or_related(session, foreground: int | None, target: int | None) -> bool:
    try:
        return bool(session._window_related_to_target(foreground, target))
    except Exception:
        return bool(foreground and target and int(foreground) == int(target))


def _focus(hwnd: int | None) -> bool:
    if not _window_alive(hwnd):
        return False
    try:
        result = executor.focus_window(int(hwnd))
        return bool(result.get("ok"))
    except Exception:
        return False


def _context(session) -> dict[str, Any]:
    try:
        ctx = session._action_target_context()
    except Exception:
        ctx = {}
    target = ctx.get("target_hwnd")
    foreground = ctx.get("foreground_hwnd")
    ctx.setdefault("target_title", _title(target))
    ctx.setdefault("foreground_title", _title(foreground))
    if target:
        ctx["target_class"] = _class_name(target)
    if foreground:
        ctx["foreground_class"] = _class_name(foreground)
    return ctx


def ensure_scope(session, tool_name: str, args: dict[str, Any]) -> ToolResult | None:
    """Return a blocking ToolResult when a GUI action is out of scope.

    Returning None means the caller may proceed. A mismatched foreground is
    recovered once by focusing the remembered target, then blocked if recovery
    cannot prove the foreground is target-related.
    """
    if tool_name in _SCOPE_RECOVERY_TOOLS or tool_name not in GUI_SCOPE_MUTATIONS:
        return None
    # The first GUI action after launch/list/focus may establish target. Do not
    # block if no target exists yet.
    target = getattr(session, "hwnd", None)
    if not _window_alive(target):
        try:
            target = session._find_target_window()
        except Exception:
            target = None
        if target and _window_alive(target):
            try:
                session._remember_target(target)
            except Exception:
                pass
        else:
            return None

    before = _context(session)
    foreground = before.get("foreground_hwnd")
    target = before.get("target_hwnd") or target
    if _same_or_related(session, foreground, target):
        return None

    recovered = _focus(target)
    time.sleep(0.12)
    after = _context(session)
    if recovered and _same_or_related(session, after.get("foreground_hwnd"), after.get("target_hwnd") or target):
        return None

    payload = {
        "ok": False,
        "verified": False,
        "blocked": True,
        "error": "scope_guard blocked GUI action because foreground window is not the remembered target window after one focus recovery attempt.",
        "tool": tool_name,
        "scope_guard": {
            "before": before,
            "after": after,
            "recovery_attempted": True,
            "recovery_focus_ok": bool(recovered),
        },
        "needs_observe": True,
    }
    return ToolResult(json.dumps(payload, ensure_ascii=False, default=str))
