"""Generic foreground/target-window guard for GUI mutations.

The guard is deliberately app-agnostic. It does not know about any particular
desktop application or target page. It only enforces that once a ToolSession
has established a target window, subsequent GUI mutations happen on pixels
owned by that window (or an owned/root-related window).  Foreground HWND alone
is not identity: many real applications use an independent tool/mini-player
window which can temporarily own foreground while the main work surface is
still the visible hit target.
"""
from __future__ import annotations

import json
from typing import Any

import win32gui

from ..action import executor
from ..config import CONFIG
from ..fast.action_contract import iter_atomic_actions
from ..perception import win32
from .tool_result import ToolResult

GUI_SCOPE_MUTATIONS = {
    "click",
    "double_click",
    "click_element",
    "type_text",
    "type_into_element",
    "hotkey",
    "scroll",
    "drag",
    "draw_strokes",
    "ensure_input_mode",
    "select_color",
    "mouse_down",
    "mouse_up",
    "move_mouse",
    "hold_key",
    "coordinate_click_test",
    "run_action_batch",
    "execute_subgoal",
}
_SCOPE_RECOVERY_TOOLS = {"open_app", "launch", "focus_window", "tray_click", "scan_windows", "list_windows"}

# These operations can physically move/click/drag the pointer or resolve a
# semantic anchor and then do so.  They must never adopt an arbitrary
# foreground window merely because the session has not established a target
# yet.  Keyboard-only compatibility paths remain unchanged for now.
_SPATIAL_TARGET_REQUIRED = {
    "click", "double_click", "click_element", "scroll", "move_mouse", "drag",
    "draw_strokes", "ensure_input_mode", "select_color",
    "mouse_down", "mouse_up", "coordinate_click_test",
}


def _batch_establishes_target_before_spatial(args: dict[str, Any]) -> bool:
    """Whether a target-less batch establishes scope before its first pointer action."""
    established = False
    for action in iter_atomic_actions("run_action_batch", args):
        if action.name == "focus_window" and (
            action.inputs.get("hwnd") or str(action.inputs.get("title") or "").strip()
        ):
            established = True
            continue
        if action.name == "launch" and str(action.inputs.get("command") or "").strip():
            # Launch is allowed, but it does not prove a target until a later
            # focus_window binds the concrete hwnd/title.
            continue
        if action.name in _SPATIAL_TARGET_REQUIRED:
            return established
    return True


def _requires_live_target(tool_name: str, args: dict[str, Any]) -> bool:
    if tool_name in _SPATIAL_TARGET_REQUIRED:
        return True
    if tool_name == "run_action_batch":
        return not _batch_establishes_target_before_spatial(args)
    if tool_name == "execute_subgoal":
        return any(
            action.name in _SPATIAL_TARGET_REQUIRED
            for action in iter_atomic_actions(tool_name, args)
        )
    return False


def _missing_target_result(session, tool_name: str) -> ToolResult:
    before = _context(session)
    payload = {
        "ok": False,
        "verified": False,
        "blocked": True,
        "missing_live_target": True,
        "error": (
            "A pointer/spatial mutation requires an explicitly established live "
            "target window. Use open_app/focus_window (or begin a batch with a "
            "concrete focus_window) before injecting input; the current foreground "
            "window was not adopted implicitly."
        ),
        "tool": tool_name,
        "scope_guard": {
            "before": before,
            "after": before,
            "recovery_attempted": False,
            "focus_stolen": False,
        },
        "needs_observe": True,
    }
    return ToolResult(json.dumps(payload, ensure_ascii=False, default=str))
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


def _screen_point(tool_name: str, args: dict[str, Any]) -> tuple[int, int] | None:
    """Return the physical start/hit point of a spatial action when available."""

    key_pairs = (
        (("x1", "y1"),) if tool_name == "drag" else (("x", "y"),)
    )
    for x_key, y_key in key_pairs:
        try:
            if args.get(x_key) is not None and args.get(y_key) is not None:
                return int(round(float(args[x_key]))), int(round(float(args[y_key])))
        except (TypeError, ValueError):
            continue
    if tool_name == "draw_strokes":
        strokes = args.get("strokes") or []
        try:
            first = next(
                point for stroke in strokes if isinstance(stroke, list)
                for point in stroke if isinstance(point, (list, tuple)) and len(point) >= 2
            )
            return int(round(float(first[0]))), int(round(float(first[1])))
        except (StopIteration, TypeError, ValueError):
            return None
    return None


def _point_hits_target(session, point: tuple[int, int] | None, target: int | None) -> bool:
    """Use the real z-ordered hit window instead of foreground as a proxy."""

    if point is None or not target:
        return False
    try:
        hit = int(win32gui.WindowFromPoint((int(point[0]), int(point[1]))) or 0)
    except Exception:
        return False
    return _same_or_related(session, hit, target)


def _record_focus_transition(session) -> None:
    """Invalidate pixel/structured authority after an internal focus repair."""

    try:
        session._screen_epoch = int(getattr(session, "_screen_epoch", 0) or 0) + 1
    except Exception:
        pass
    for attr in ("snapshot", "_observe_cache", "_draw_surface_cache"):
        try:
            setattr(session, attr, None)
        except Exception:
            pass


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


def take_scope_recovery(session) -> dict[str, Any] | None:
    """Consume legacy recovery evidence; native scope checks never create it."""

    value = getattr(session, "_last_scope_recovery", None)
    try:
        session._last_scope_recovery = None
    except Exception:
        pass
    return dict(value) if isinstance(value, dict) else None


def ensure_scope(session, tool_name: str, args: dict[str, Any]) -> ToolResult | None:
    """Return a blocking ToolResult when a GUI action is out of scope.

    Returning None means the caller may proceed. Spatial actions are validated
    against the actual window under their physical hit point. In autonomous
    mode a known target may also be refocused once; if that changes which
    pixels are exposed, the proposed spatial action is withheld until a fresh
    authoritative frame is captured.
    """
    if tool_name in _SCOPE_RECOVERY_TOOLS or tool_name not in GUI_SCOPE_MUTATIONS:
        return None
    if tool_name == "mouse_up" and executor.agent_holds_mouse_button(
        str(args.get("button") or "left"),
    ):
        # A release for a press owned by this process is cleanup, not a new
        # target mutation.  Never leave the global button held merely because
        # the target closed, moved, or lost foreground during the gesture.
        return None
    # FastGuiController is also unit-tested/embeddable with tiny executor
    # adapters.  Only a real ToolSession exposes the target-context contract;
    # without it there is no foreground identity for this guard to evaluate.
    # Production Agent/MCP paths always use ToolSession and therefore remain
    # fail-closed below.
    if not callable(getattr(session, "_action_target_context", None)):
        return None
    cooperative = bool(CONFIG.get(
        "controller", "multi_input_cooperation", default=False,
    ))
    # Cooperative mutations may only use a target established by an explicit
    # launch/focus/discovery step.  Inferring the first target here would adopt
    # whichever foreground window the human happens to own.
    target = getattr(session, "hwnd", None)
    if target and not _window_alive(target):
        recover_bound_target = getattr(session, "_recover_bound_target_window", None)
        if callable(recover_bound_target):
            try:
                rebound = recover_bound_target()
            except Exception:
                rebound = None
            if rebound and _window_alive(rebound):
                target = int(rebound)
    explicit_scope = args.get("target_window") if isinstance(args.get("target_window"), dict) else {}
    if not _window_alive(target) and _window_alive(explicit_scope.get("hwnd")):
        target = int(explicit_scope["hwnd"])
        try:
            session._remember_target(target, str(explicit_scope.get("title") or ""))
        except Exception:
            try:
                session.hwnd = target
            except Exception:
                pass
    if cooperative and not _window_alive(target):
        before = _context(session)
        payload = {
            "ok": False,
            "verified": False,
            "blocked": True,
            "cooperative_input": True,
            "missing_live_target": True,
            "error": (
                "Cooperative input requires an already established live target "
                "window before any GUI mutation. Use launch/focus_window first; "
                "the current foreground window was not adopted."
            ),
            "tool": tool_name,
            "scope_guard": {
                "before": before,
                "after": before,
                "recovery_attempted": False,
                "focus_stolen": False,
            },
            "needs_observe": True,
        }
        return ToolResult(json.dumps(payload, ensure_ascii=False, default=str))

    authority = getattr(session, "_frame_authority", None)
    frame_state = getattr(authority, "current", None)
    native_frame_grounded = bool(
        getattr(session, "native_visual_runtime", False)
        and frame_state is not None
        and frame_state.is_fresh_for(
            epoch=int(getattr(session, "_screen_epoch", -1) or 0),
        )
    )

    # Pointer/spatial actions are fail-closed without an explicit binding.  In
    # particular, do not call _find_target_window here: choosing whichever
    # foreground/task-hint happens to match is exactly the cross-application
    # state leak this guard is meant to prevent.
    if not _window_alive(target):
        if native_frame_grounded:
            # A current full-desktop/frame authority is itself an explicit
            # visual binding. Execute against the foreground represented in
            # those pixels; never invent a window from task text or UIA.
            return None
        if _requires_live_target(tool_name, args):
            return _missing_target_result(session, tool_name)
        # Non-spatial compatibility actions may retain the historical
        # target-hint recovery behaviour.
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

    point = _screen_point(tool_name, args) if tool_name in _SPATIAL_TARGET_REQUIRED else None
    if _point_hits_target(session, point, target):
        # A click/scroll/drag on the proven target surface naturally transfers
        # foreground ownership. Rejecting it merely because an auxiliary tool
        # window owns foreground creates a false deadlock.
        return None

    if cooperative:
        # User foreground ownership wins. Do not adopt a same-process sibling
        # and never steal focus back. Background-safe paths may continue to the
        # executor, which binds PostMessage/UIA to the remembered hwnd; every
        # physical/keyboard path below is rejected before it can inject input.
        background_safe = bool(
            tool_name in {
                "click", "double_click", "click_element", "scroll",
                "type_into_element", "run_action_batch", "execute_subgoal",
            }
            or (
                tool_name == "type_text"
                and args.get("element_id") is not None
            )
        )
        if background_safe:
            return None
        payload = {
            "ok": False,
            "verified": False,
            "blocked": True,
            "cooperative_input": True,
            "error": (
                "Cooperative input blocked a foreground-dependent GUI action because "
                "the user currently owns a different window. Focus was not changed."
            ),
            "tool": tool_name,
            "scope_guard": {
                "before": before,
                "after": before,
                "recovery_attempted": False,
                "focus_stolen": False,
            },
            "needs_observe": True,
        }
        return ToolResult(json.dumps(payload, ensure_ascii=False, default=str))

    # Modern apps may replace their top-level HWND while keeping the same
    # process. If the real hit-test also lands on that new foreground surface,
    # rebind it and require a fresh frame. The hit-test qualification prevents
    # a same-process palette/mini window from replacing the main work surface
    # merely because it briefly owns foreground.
    try:
        same_process = bool(session._same_window_process(foreground, target))
    except Exception:
        same_process = False
    if (
        same_process
        and _window_alive(foreground)
        and _point_hits_target(session, point, foreground)
    ):
        try:
            session._remember_target(foreground, str(before.get("foreground_title") or ""))
        except Exception:
            pass
        after = _context(session)
        return ToolResult(json.dumps({
            "ok": False,
            "verified": False,
            "blocked": True,
            "action_executed": False,
            "target_rebound": True,
            "error": "the application replaced its foreground window after the authoritative frame was captured",
            "tool": tool_name,
            "scope_guard": {
                "before": before,
                "after": after,
                "recovery_attempted": False,
            },
            "needs_observe": True,
        }, ensure_ascii=False, default=str))

    # The autonomous task owns foreground. Attempt one exact-window focus
    # repair before treating a transient shell/tool-window foreground as a
    # hard failure. Keyboard actions can safely continue after verified focus;
    # spatial coordinates are withheld when the focus transition exposed
    # different pixels and must be grounded again.
    focus_result: dict[str, Any]
    try:
        focus_result = executor.focus_window(int(target))
    except Exception as exc:
        focus_result = {"ok": False, "error": str(exc)}
    after_focus = _context(session)
    focus_verified = bool(
        focus_result.get("ok")
        and _same_or_related(
            session,
            after_focus.get("foreground_hwnd"),
            after_focus.get("target_hwnd") or target,
        )
    )
    if focus_verified:
        if point is None:
            return None
        _record_focus_transition(session)
        return ToolResult(json.dumps({
            "ok": False,
            "verified": False,
            "blocked": True,
            "action_executed": False,
            "recovery_action_executed": True,
            "target_refocused": True,
            "error": (
                "The remembered target was refocused, changing the visible hit "
                "surface. The proposed spatial action was not executed; use the "
                "fresh authoritative frame for the next decision."
            ),
            "tool": tool_name,
            "scope_guard": {
                "before": before,
                "after": after_focus,
                "recovery_attempted": True,
                "focus": focus_result,
            },
            "needs_observe": True,
        }, ensure_ascii=False, default=str))

    payload = {
        "ok": False,
        "verified": False,
        "blocked": True,
        "action_executed": False,
        "error": "scope_guard blocked GUI action because the foreground window is not the remembered target window.",
        "tool": tool_name,
        "scope_guard": {
            "before": before,
            "after": after_focus,
            "recovery_attempted": True,
            "focus": focus_result,
        },
        "needs_observe": True,
    }
    return ToolResult(json.dumps(payload, ensure_ascii=False, default=str))
