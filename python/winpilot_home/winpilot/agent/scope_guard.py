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
from ..config import CONFIG
from ..perception import win32
from .tool_result import ToolResult

GUI_SCOPE_MUTATIONS = {
    "click",
    "click_element",
    "type_text",
    "type_into_element",
    "hotkey",
    "scroll",
    "drag",
    "draw_strokes",
    "select_color",
    "ensure_document_surface",
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
_SCOPE_RECOVERY_TOOLS = {"open_app", "launch", "focus_window", "tray_click", "scan_windows", "list_windows"}

# These operations can physically move/click/drag the pointer or resolve a
# semantic anchor and then do so.  They must never adopt an arbitrary
# foreground window merely because the session has not established a target
# yet.  Keyboard-only compatibility paths remain unchanged for now.
_SPATIAL_TARGET_REQUIRED = {
    "click", "click_element", "scroll", "move_mouse", "drag",
    "draw_strokes", "select_color", "ensure_document_surface",
    "mouse_down", "mouse_up", "coordinate_click_test", "paste_image_file",
}


def _batch_action_parts(value: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return "", {}
    name = str(value.get("action") or value.get("tool") or "").strip()
    if name:
        nested = value.get("params") if isinstance(value.get("params"), dict) else {}
        return name, {
            **nested,
            **{key: item for key, item in value.items() if key not in {"action", "tool", "params"}},
        }
    if len(value) == 1:
        name, nested = next(iter(value.items()))
        return str(name), dict(nested) if isinstance(nested, dict) else {"value": nested}
    return "", {}


def _batch_establishes_target_before_spatial(args: dict[str, Any]) -> bool:
    """Whether a target-less batch establishes scope before its first pointer action."""
    established = False
    for value in args.get("actions") or []:
        name, child = _batch_action_parts(value)
        if name == "focus_window" and (
            child.get("hwnd") or str(child.get("title") or "").strip()
        ):
            established = True
            continue
        if name == "launch" and str(child.get("command") or "").strip():
            # Launch is allowed, but it does not prove a target until a later
            # focus_window binds the concrete hwnd/title.
            continue
        if name in _SPATIAL_TARGET_REQUIRED:
            return established
    return True


def _requires_live_target(tool_name: str, args: dict[str, Any]) -> bool:
    if tool_name in _SPATIAL_TARGET_REQUIRED:
        return True
    if tool_name == "run_action_batch":
        return not _batch_establishes_target_before_spatial(args)
    if tool_name == "execute_subgoal":
        return bool(
            any(isinstance(args.get(key), dict) and args.get(key) for key in ("click", "draw"))
            or any(isinstance(args.get(key), list) and args.get(key) for key in ("clicks", "draws"))
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
_STRONG_CONTROL_PLANE_TITLES = (
    "灵动脚本",
    "lingdong script",
    "lingdong-script",
)
_EXPLICIT_CONTROL_PLANE_TASK_MARKERS = (
    "winpilot control-plane",
    "winpilot control plane",
    "winpilot 控制面",
    "winpilot控制面",
    "灵动脚本",
    "lingdong script",
    "lingdong-script",
)


def _is_control_plane_title(title: str) -> bool:
    folded = str(title or "").strip().casefold()
    # ``WinPilot`` occurs legitimately in page titles, document names, eval
    # targets, and user content.  A substring match therefore blocks the very
    # target window the agent was asked to operate.  Keep branded Lingdong
    # titles as strong markers and accept WinPilot only as the complete title.
    return bool(
        any(marker.casefold() in folded for marker in _STRONG_CONTROL_PLANE_TITLES)
        or folded in {"winpilot", "winpilot agent"}
    )


def _task_targets_control_plane(session) -> bool:
    task = str(getattr(session, "task", "") or "").casefold()
    return any(marker.casefold() in task for marker in _EXPLICIT_CONTROL_PLANE_TASK_MARKERS)


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


def take_scope_recovery(session) -> dict[str, Any] | None:
    """Consume evidence from the most recent successful foreground recovery.

    ``ensure_scope`` historically returned only ``None`` on success, which made
    an in-transaction refocus invisible to the Controller.  Keeping the public
    return contract intact while exposing one-shot evidence lets callers
    invalidate stale perception and record the recovery in the batch trace.
    """

    value = getattr(session, "_last_scope_recovery", None)
    try:
        session._last_scope_recovery = None
    except Exception:
        pass
    return dict(value) if isinstance(value, dict) else None


def _recover_target_focus(
    session,
    target: int,
    tool_name: str,
    before: dict[str, Any],
) -> tuple[bool, dict[str, Any], int, bool]:
    """Refocus a remembered target and prove stable ownership before input.

    Windows activation is asynchronous and a foreground process can briefly
    reclaim focus.  A single API ``ok`` followed by one sample is therefore not
    sufficient evidence.  This bounded loop requires two consecutive target-
    related foreground samples.  It is app-agnostic and runs only after an
    actual scope mismatch.
    """

    try:
        max_attempts = int(CONFIG.get(
            "controller", "focus_recovery_attempts", default=3,
        ) or 3)
    except (TypeError, ValueError):
        max_attempts = 3
    max_attempts = max(1, min(3, max_attempts))
    try:
        settle_s = float(CONFIG.get(
            "controller", "focus_recovery_settle_s", default=0.08,
        ) or 0.08)
    except (TypeError, ValueError):
        settle_s = 0.08
    settle_s = max(0.02, min(0.2, settle_s))

    after = dict(before)
    focus_api_ok = False
    for attempt in range(1, max_attempts + 1):
        focus_api_ok = bool(_focus(target)) or focus_api_ok
        time.sleep(settle_s)
        first = _context(session)
        first_target = first.get("target_hwnd") or target
        if _same_or_related(session, first.get("foreground_hwnd"), first_target):
            time.sleep(settle_s)
            after = _context(session)
            after_target = after.get("target_hwnd") or target
            if _same_or_related(session, after.get("foreground_hwnd"), after_target):
                evidence = {
                    "tool": str(tool_name or ""),
                    "target_hwnd": int(target),
                    "attempts": attempt,
                    "focus_api_ok": focus_api_ok,
                    "before": before,
                    "after": after,
                    "authoritative_frame_required": True,
                }
                try:
                    session._last_scope_recovery = evidence
                except Exception:
                    pass
                return True, after, attempt, focus_api_ok
        else:
            after = first
        if attempt < max_attempts:
            time.sleep(settle_s)
    return False, after, max_attempts, focus_api_ok


def ensure_scope(session, tool_name: str, args: dict[str, Any]) -> ToolResult | None:
    """Return a blocking ToolResult when a GUI action is out of scope.

    Returning None means the caller may proceed. A mismatched foreground is
    recovered once by focusing the remembered target, then blocked if recovery
    cannot prove the foreground is target-related.
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

    # Pointer/spatial actions are fail-closed without an explicit binding.  In
    # particular, do not call _find_target_window here: choosing whichever
    # foreground/task-hint happens to match is exactly the cross-application
    # state leak this guard is meant to prevent.
    if not _window_alive(target):
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
    if not _task_targets_control_plane(session) and (
        _is_control_plane_title(str(before.get("foreground_title") or ""))
        or _is_control_plane_title(str(before.get("target_title") or ""))
    ):
        payload = {
            "ok": False,
            "verified": False,
            "blocked": True,
            "error": (
                "scope_guard blocked a desktop mutation targeting WinPilot's own "
                "control-plane window. Launch/focus the user's target application first."
            ),
            "tool": tool_name,
            "scope_guard": {
                "before": before,
                "after": before,
                "recovery_attempted": False,
                "self_control_plane": True,
            },
            "needs_observe": True,
        }
        return ToolResult(json.dumps(payload, ensure_ascii=False, default=str))
    if _same_or_related(session, foreground, target):
        return None

    if cooperative:
        # User foreground ownership wins. Do not adopt a same-process sibling
        # and never steal focus back. Background-safe paths may continue to the
        # executor, which binds PostMessage/UIA to the remembered hwnd; every
        # physical/keyboard path below is rejected before it can inject input.
        background_safe = bool(
            tool_name in {
                "click", "click_element", "scroll",
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

    # Modern apps frequently replace their top-level hwnd during navigation,
    # document creation, or tab/window promotion.  A visible foreground sibling
    # from the same process is a stronger app-identity signal than a stale hwnd.
    # Adopt it before trying to drag focus back to the obsolete window.
    try:
        same_process = bool(session._same_window_process(foreground, target))
    except Exception:
        same_process = False
    if same_process and _window_alive(foreground):
        try:
            session._remember_target(foreground, str(before.get("foreground_title") or ""))
        except Exception:
            pass
        return None

    recovered, after, recovery_attempts, focus_api_ok = _recover_target_focus(
        session, int(target), tool_name, before,
    )
    if recovered:
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
            "recovery_attempts": recovery_attempts,
            "recovery_focus_ok": bool(focus_api_ok),
        },
        "needs_observe": True,
    }
    return ToolResult(json.dumps(payload, ensure_ascii=False, default=str))
