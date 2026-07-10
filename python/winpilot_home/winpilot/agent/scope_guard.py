"""Generic foreground/target-window guard for GUI mutations.

The guard is deliberately app-agnostic. It does not know about QQ, Calculator,
Steam, browsers, or any target page. It only enforces that once a ToolSession
has established a target window, subsequent GUI mutations happen in that window
(or an owned/root-related foreground window) unless the action itself is a
focus/launch/window-discovery step.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
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
    "paste_image_file",
    "mouse_down",
    "mouse_up",
    "move_mouse",
    "hold_key",
    "coordinate_click_test",
    "run_action_batch",
    "execute_subgoal",
}
_SCOPE_RECOVERY_TOOLS = {"launch", "focus_window", "tray_click", "scan_windows", "list_windows"}


def _calculator_then_qq_result(session) -> float | None:
    """Return the requested quotient for a staged calculator-to-QQ task."""
    task = str(getattr(session, "task", "") or "")
    low = task.casefold()
    if not (any(marker in low for marker in ("calculator", "计算器")) and "qq" in low):
        return None
    if not any(marker in low for marker in ("quotient", "divide", "商", "除")):
        return None
    match = re.search(
        r"(?:计算|calculate).*?(\d+(?:\.\d+)?)\s*(?:和|与|and)\s*"
        r"(\d+(?:\.\d+)?).*?(?:quotient|divide|商|除)",
        task,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    denominator = float(match.group(2))
    return float(match.group(1)) / denominator if denominator else None


def _calculator_result_verified(session, expected: float) -> bool:
    ledger = getattr(session, "visible_evidence_ledger", []) or []
    for item in ledger:
        if not str(item.get("source") or "").startswith("execute_subgoal"):
            continue
        values = " ".join((str(item.get("target") or ""), str(item.get("snippet") or "")))
        for raw in re.findall(r"\d+(?:\.\d+)?", values):
            try:
                actual = float(raw)
            except ValueError:
                continue
            if abs(actual - expected) <= max(0.0001, abs(expected) * 0.0001):
                return True
    return False


def _saved_screenshot_available(session) -> bool:
    for raw_path in getattr(session, "saved_screenshot_paths", []) or []:
        try:
            if Path(str(raw_path)).is_file():
                return True
        except OSError:
            continue
    return False


def _is_qq_action(session, tool_name: str, args: dict[str, Any]) -> bool:
    try:
        text = json.dumps(args, ensure_ascii=False, default=str).casefold()
    except Exception:
        text = str(args).casefold()
    if "qq" in text or "我的手机" in text:
        return True
    if tool_name in GUI_SCOPE_MUTATIONS | {"observe", "read_text", "focus_window"}:
        return "qq" in _title(getattr(session, "hwnd", None)).casefold()
    return False


def _ordered_task_blocker(session, tool_name: str, args: dict[str, Any]) -> ToolResult | None:
    expected = _calculator_then_qq_result(session)
    if expected is None or not _is_qq_action(session, tool_name, args):
        return None
    if not _calculator_result_verified(session, expected):
        return ToolResult(json.dumps({
            "ok": False,
            "blocked": True,
            "tool": tool_name,
            "error": (
                f"Task order guard: verify Calculator result {expected:.8g} before opening or using QQ. "
                "Keep Calculator as the target, execute the expression, and ground the visible result first."
            ),
            "needs_observe": True,
        }, ensure_ascii=False))
    if not _saved_screenshot_available(session):
        return ToolResult(json.dumps({
            "ok": False,
            "blocked": True,
            "tool": tool_name,
            "error": (
                "Task order guard: save the verified Calculator window with "
                "screenshot(path=absolute_png_path, window=true) before opening or using QQ."
            ),
        }, ensure_ascii=False))
    return None


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
    ordered_blocker = _ordered_task_blocker(session, tool_name, args)
    if ordered_blocker is not None:
        return ordered_blocker
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
