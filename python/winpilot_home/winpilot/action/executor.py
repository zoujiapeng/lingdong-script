"""Action executor: SendInput-based mouse/keyboard plus window management.

All actions are physical-pixel screen coordinates. ``type_text`` uses
KEYEVENTF_UNICODE so Chinese/emoji input works without an IME. A UIA fast
path (Invoke/SetValue) is attempted first when a live UIA handle is present
on the resolved element — faster and more reliable than synthetic clicks.

Clipboard, mouse fine-control, and screenshot utilities ported from
cc-haha ``runtime/win_helper.py`` for full computer-use parity.
"""
from __future__ import annotations

import base64
import ctypes
import inspect
import math
import os
import re
import string
import subprocess
import threading
import time
from ctypes import wintypes
from io import BytesIO
from pathlib import Path
from typing import Any, Callable

import win32api
import win32con
import win32gui

from ..config import CONFIG
from ..utils.log import BUS, logger
from .desktop_queue import queued_desktop_action
from .raw_device import (
    AdmittedPhysicalClickBackend,
    ClickCount,
    ClickMechanism,
    FinalClickGuard,
    FinalClickGuardDecision,
    MouseButton,
    RawBackendResult,
)

user32 = ctypes.windll.user32

# ToolSession installs this callback only around a visible action. Sampling
# after mouse-down makes short-lived pressed states part of action evidence.
_press_probe_local = threading.local()
_held_mouse_lock = threading.Lock()
_held_mouse_buttons: set[str] = set()


def install_press_probe(callback: Callable[[], Any] | None) -> Callable[[], Any] | None:
    previous = getattr(_press_probe_local, "callback", None)
    _press_probe_local.callback = callback
    return previous


def restore_press_probe(previous: Callable[[], Any] | None) -> None:
    _press_probe_local.callback = previous


def _sample_pressed_state() -> bool:
    callback = getattr(_press_probe_local, "callback", None)
    if not callable(callback):
        return False
    try:
        settle_ms = max(0, min(80, int(CONFIG.get("fusion", "press_probe_settle_ms", default=18))))
        if settle_ms:
            time.sleep(settle_ms / 1000.0)
        try:
            ctypes.windll.dwmapi.DwmFlush()
        except (AttributeError, OSError):
            pass
        callback()
        return True
    except Exception:
        logger.debug("pressed-state visual probe failed", exc_info=True)
        return False


def _settle_after_release() -> None:
    settle_ms = max(0, min(80, int(CONFIG.get("fusion", "press_release_settle_ms", default=24))))
    if settle_ms:
        time.sleep(settle_ms / 1000.0)
    try:
        ctypes.windll.dwmapi.DwmFlush()
    except (AttributeError, OSError):
        pass

# ---------------------------------------------------------------- SendInput
_PUL = ctypes.POINTER(ctypes.c_ulong)


class _KeyBdInput(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
        ("dwExtraInfo", _PUL),
    ]


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long), ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong), ("dwExtraInfo", _PUL),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("ki", _KeyBdInput), ("mi", _MouseInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("union", _InputUnion)]


_INPUT_MOUSE, _INPUT_KEYBOARD = 0, 1
_KEYEVENTF_UNICODE, _KEYEVENTF_KEYUP, _KEYEVENTF_EXTENDEDKEY = 0x4, 0x2, 0x1


def _send_inputs(inputs: list[_Input]) -> None:
    array = (_Input * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), array, ctypes.sizeof(_Input))
    if sent != len(inputs):
        raise OSError(f"SendInput 只发送了 {sent}/{len(inputs)}")


def _key_input(vk: int = 0, scan: int = 0, flags: int = 0) -> _Input:
    inp = _Input()
    inp.type = _INPUT_KEYBOARD
    inp.union.ki = _KeyBdInput(vk, scan, flags, 0, ctypes.pointer(ctypes.c_ulong(0)))
    return inp


def _mouse_input(flags: int, data: int = 0, dx: int = 0, dy: int = 0) -> _Input:
    inp = _Input()
    inp.type = _INPUT_MOUSE
    inp.union.mi = _MouseInput(dx, dy, data, flags, 0, ctypes.pointer(ctypes.c_ulong(0)))
    return inp


# ----------------------------------------------------------------- VK table
_VK = {
    "ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B,
    "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09,
    "space": 0x20, "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
    "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    "capslock": 0x14, "numlock": 0x90, "printscreen": 0x2C, "menu": 0x5D,
    "plus": 0xBB, "minus": 0xBD, "comma": 0xBC, "period": 0xBE, "slash": 0xBF,
}
_EXTENDED_VKS = {0x2E, 0x2D, 0x24, 0x23, 0x21, 0x22, 0x26, 0x28, 0x25, 0x27, 0x5B, 0x5D}
_VK_SHIFT, _VK_CTRL, _VK_ALT = 0x10, 0x11, 0x12
_VK_TEXT_CHARS = set(string.digits + "+-*/=.,")


def _vk_of(key: str) -> int:
    key = key.strip().lower()
    if key in _VK:
        return _VK[key]
    if len(key) == 1:
        # VkKeyScanW maps a character to VK (low byte)
        res = user32.VkKeyScanW(ctypes.c_wchar(key))
        if res != -1:
            return res & 0xFF
    raise ValueError(f"未知按键: {key}")


def _press_char_vk(char: str) -> bool:
    """Press one printable char through the active keyboard layout."""
    res = user32.VkKeyScanW(ctypes.c_wchar(char))
    if res == -1:
        return False
    vk = res & 0xFF
    state = (res >> 8) & 0xFF
    modifiers: list[int] = []
    if state & 1:
        modifiers.append(_VK_SHIFT)
    if state & 2:
        modifiers.append(_VK_CTRL)
    if state & 4:
        modifiers.append(_VK_ALT)

    flags = _KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED_VKS else 0
    inputs = [_key_input(vk=m) for m in modifiers]
    inputs.extend([
        _key_input(vk=vk, flags=flags),
        _key_input(vk=vk, flags=flags | _KEYEVENTF_KEYUP),
    ])
    inputs.extend(_key_input(vk=m, flags=_KEYEVENTF_KEYUP) for m in reversed(modifiers))
    _send_inputs(inputs)
    return True


def _press_vk_code(vk: int) -> None:
    flags = _KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED_VKS or vk == 0x6F else 0
    _send_inputs([
        _key_input(vk=vk, flags=flags),
        _key_input(vk=vk, flags=flags | _KEYEVENTF_KEYUP),
    ])


def _should_type_via_vk(text: str) -> bool:
    return bool(text) and all(char in _VK_TEXT_CHARS for char in text)


def _dry_run() -> bool:
    return bool(CONFIG.get("agent", "dry_run", default=False))


def _no_mouse_steal() -> bool:
    """Experimental: act without hijacking the user's physical cursor."""
    return bool(
        CONFIG.get("agent", "no_mouse_steal", default=False)
        or multi_input_cooperation_enabled()
    )


def multi_input_cooperation_enabled() -> bool:
    """Whether the experimental human/agent input arbitration is enabled.

    Windows exposes one system cursor and one foreground keyboard focus, so
    this mode cannot create a literal second pointer.  It instead prefers
    window-message input and refuses to steal keyboard focus from a different
    foreground window.  The legacy ``no_mouse_steal`` flag remains supported.
    """
    return bool(CONFIG.get(
        "controller", "multi_input_cooperation", default=False,
    ))


def cooperative_input_guard(
    action: str,
    hwnd: int | None,
    *,
    foreground_hwnd: int | None = None,
) -> dict[str, Any] | None:
    """Fail closed before a physical keyboard/focus action can disturb a user."""
    if not multi_input_cooperation_enabled():
        return None
    target = int(hwnd or 0)
    try:
        foreground = int(
            foreground_hwnd
            if foreground_hwnd is not None
            else (win32gui.GetForegroundWindow() or 0)
        )
    except Exception:
        foreground = 0
    if target and foreground == target:
        return None
    return {
        "ok": False,
        "verified": False,
        "blocked": True,
        "cooperative_input": True,
        "action": str(action),
        "target_hwnd": target,
        "foreground_hwnd": foreground,
        "error": (
            "cooperative input blocked a physical focus/keyboard action because "
            "the target is not the current foreground window; background-safe "
            "UIA/window-message actions may continue without taking over the "
            "user's mouse or keyboard"
        ),
    }


# ---- no-steal helpers: background message clicks + cursor save/restore ----
# Windows has a single system cursor, so a true "second mouse" is impossible;
# instead we click via PostMessage (no cursor move) and, when SendInput is
# unavoidable, snap the cursor back to where the user left it.
_WM = {  # button -> (down, up, mk_flag)
    "left": (win32con.WM_LBUTTONDOWN, win32con.WM_LBUTTONUP, win32con.MK_LBUTTON),
    "right": (win32con.WM_RBUTTONDOWN, win32con.WM_RBUTTONUP, win32con.MK_RBUTTON),
    "middle": (win32con.WM_MBUTTONDOWN, win32con.WM_MBUTTONUP, win32con.MK_MBUTTON),
}
_WM_DOUBLE = {
    "left": win32con.WM_LBUTTONDBLCLK,
    "right": win32con.WM_RBUTTONDBLCLK,
    "middle": win32con.WM_MBUTTONDBLCLK,
}


def _same_window_root(candidate: int, expected: int | None) -> bool:
    if not expected:
        return True
    try:
        candidate_root = int(win32gui.GetAncestor(int(candidate), 2) or candidate)
        expected_root = int(win32gui.GetAncestor(int(expected), 2) or expected)
        return candidate_root == expected_root
    except Exception:
        return int(candidate or 0) == int(expected or 0)


def _try_release(call: Callable[..., Any], *args: Any) -> bool:
    try:
        call(*args)
        return True
    except Exception:
        return False


def _post_click(
    x: int,
    y: int,
    button: MouseButton,
    count: ClickCount,
    expected_hwnd: int | None = None,
) -> RawBackendResult:
    """Attempt one message click; only a zero-message result permits fallback."""
    try:
        target = win32gui.WindowFromPoint((int(x), int(y)))
        if not target or not _same_window_root(int(target), expected_hwnd):
            return RawBackendResult(False, {"route_available": False})
        cx, cy = win32gui.ScreenToClient(target, (int(x), int(y)))
        lparam = (cy << 16) | (cx & 0xFFFF)
        down, up, mk = _WM[button.value]
    except Exception:
        return RawBackendResult(False, {"route_available": False})

    completed_clicks = 0
    for index in range(count.value):
        down_message = _WM_DOUBLE[button.value] if index == 1 else down
        try:
            win32gui.PostMessage(target, down_message, mk, lparam)
        except Exception:
            return _click_progress(
                None, completed_clicks, "post_message_down_outcome_unknown",
                cleanup=_try_release(win32gui.PostMessage, target, up, 0, lparam),
                via="post_message",
            )
        try:
            sampled = _sample_pressed_state()
        except Exception:
            sampled = False
        try:
            win32gui.PostMessage(target, up, 0, lparam)
        except Exception:
            return _click_progress(
                None, completed_clicks, "post_message_up_outcome_unknown",
                cleanup=_try_release(win32gui.PostMessage, target, up, 0, lparam),
                via="post_message",
            )
        completed_clicks += 1
        if sampled:
            try:
                _settle_after_release()
            except Exception:
                logger.debug("post-click settle failed", exc_info=True)
        if index + 1 < count.value:
            try:
                time.sleep(0.05)
            except Exception:
                return _click_progress(
                    None, completed_clicks,
                    "post_message_inter_click_wait_failed", via="post_message",
                )
    return _click_progress(True, completed_clicks, via="post_message")


def _cursor_pos() -> tuple[int, int]:
    return win32api.GetCursorPos()


def _restore_cursor(pos: tuple[int, int]) -> None:
    try:
        win32api.SetCursorPos(pos)
    except Exception:
        pass


def _restore_cursor_after_agent_input(
    saved: tuple[int, int],
    expected_agent_pos: tuple[int, int] | None,
    hwnd: int | None = None,
) -> bool:
    """Restore only if a human did not move the shared cursor mid-action."""
    if multi_input_cooperation_enabled() and expected_agent_pos is not None:
        if hwnd is not None and not _cooperative_action_still_owned(
            hwnd, expected_cursor=expected_agent_pos,
        ):
            return False
        try:
            current = _cursor_pos()
            tolerance = max(2, min(80, int(CONFIG.get(
                "controller",
                "cooperative_cursor_tolerance_px",
                default=12,
            ))))
            if (
                abs(int(current[0]) - int(expected_agent_pos[0])) > tolerance
                or abs(int(current[1]) - int(expected_agent_pos[1])) > tolerance
            ):
                BUS.publish(
                    "cooperative_input",
                    state="human_cursor_won",
                    current=list(current),
                    expected_agent_pos=list(expected_agent_pos),
                    saved=list(saved),
                )
                return False
        except Exception:
            return False
    _restore_cursor(saved)
    return True


def _cooperative_action_still_owned(
    hwnd: int | None,
    *,
    expected_cursor: tuple[int, int] | None = None,
) -> bool:
    """Recheck foreground/cursor ownership during a long physical action."""
    if not multi_input_cooperation_enabled():
        return True
    if cooperative_input_guard("in_progress_physical_input", hwnd) is not None:
        return False
    if expected_cursor is None:
        return True
    try:
        current = _cursor_pos()
        tolerance = max(2, min(80, int(CONFIG.get(
            "controller", "cooperative_cursor_tolerance_px", default=12,
        ))))
        if (
            abs(int(current[0]) - int(expected_cursor[0])) <= tolerance
            and abs(int(current[1]) - int(expected_cursor[1])) <= tolerance
        ):
            return True
        BUS.publish(
            "cooperative_input",
            state="human_cursor_won",
            phase="physical_action",
            current=list(current),
            expected_agent_pos=list(expected_cursor),
        )
    except Exception:
        pass
    return False


def _target_window_still_foreground(hwnd: int | None) -> bool:
    """Fail closed when a long physical gesture loses its target window.

    This guard is independent of the optional human/agent cooperation mode.
    A drag or stroke can span hundreds of input samples; checking scope only
    before button-down lets a focus change redirect the remaining samples into
    another application.  Comparing root windows also accepts a bound child
    control while rejecting unrelated foreground windows.
    """
    target = int(hwnd or 0)
    if not target:
        return True
    try:
        foreground = int(win32gui.GetForegroundWindow() or 0)
        target_root = int(win32gui.GetAncestor(target, 2) or target)
        foreground_root = int(
            win32gui.GetAncestor(foreground, 2) or foreground
        )
    except Exception:
        return False
    if foreground_root == target_root:
        return True
    BUS.publish(
        "physical_input_guard",
        state="target_window_lost",
        target_hwnd=target,
        target_root=target_root,
        foreground_hwnd=foreground,
        foreground_root=foreground_root,
    )
    return False


# ---------------------------------------------------------------- mouse API
def _raw_move_mouse(x: int, y: int) -> None:
    user32.SetCursorPos(int(x), int(y))


@queued_desktop_action("move_mouse")
def move_mouse(x: int, y: int, hwnd: int | None = None) -> dict:
    if multi_input_cooperation_enabled():
        return {
            "ok": False,
            "verified": False,
            "blocked": True,
            "cooperative_input": True,
            "action": "move_mouse",
            "error": (
                "cooperative input does not expose standalone movement of the shared "
                "system cursor; use a background-safe click or one atomic guarded action"
            ),
        }
    cooperative_block = cooperative_input_guard("move_mouse", hwnd)
    if cooperative_block is not None:
        return cooperative_block
    _raw_move_mouse(x, y)
    return {"ok": True, "x": int(x), "y": int(y)}


_CLICK_OWNERSHIP_LOST = "click stopped because the user took foreground/cursor ownership"


def _legacy_click_failure(
    result: RawBackendResult,
    *,
    cursor_restored: bool | None = None,
) -> dict[str, Any]:
    facts = dict(result.facts)
    facts.update({
        "ok": False, "verified": False, "blocked": True,
        "error": str(result.facts.get("error") or _CLICK_OWNERSHIP_LOST),
    })
    if cursor_restored is not None:
        facts["cursor_restored"] = cursor_restored
    if result.input_completed is not None:
        facts.pop("completed_clicks", None)
    else:
        facts.update(input_completed=None, outcome_unknown=True)
    return facts


def _click_progress(
    input_completed: bool | None,
    completed_clicks: int,
    error: str = "",
    *,
    cleanup: bool | None = None,
    via: str = "",
) -> RawBackendResult:
    facts: dict[str, Any] = {"completed_clicks": int(completed_clicks)}
    if error:
        facts.update({"ok": False, "blocked": True, "error": error})
        if error == _CLICK_OWNERSHIP_LOST:
            facts["cooperative_input"] = True
    if cleanup is not None:
        facts.update(release_cleanup_attempted=True,
                     release_cleanup_completed=cleanup)
    if via:
        facts["via"] = via
    return RawBackendResult(input_completed, facts)


def _execute_click_input_unqueued(
    x: int,
    y: int,
    button: MouseButton,
    count: ClickCount,
    mechanism: ClickMechanism,
    hwnd: int | None = None,
    *,
    final_guard: FinalClickGuard | None = None,
) -> RawBackendResult:
    """Execute one strict click after the caller has serialized admission."""
    if not (
        type(x) is int and type(y) is int and isinstance(button, MouseButton)
        and isinstance(count, ClickCount)
        and isinstance(mechanism, ClickMechanism)
    ):
        return _click_progress(False, 0, "invalid typed click input")
    if _dry_run():
        return RawBackendResult(False, {"ok": True, "dry_run": True})
    if mechanism is ClickMechanism.BACKGROUND_MESSAGE:
        return _post_click(x, y, button, count, expected_hwnd=hwnd)
    raw_args = (
        x, y, button, count, hwnd,
        mechanism is ClickMechanism.PREPOSITIONED_PHYSICAL, False,
    )
    if final_guard is None:
        return _raw_click(*raw_args)
    return _raw_click(*raw_args, final_guard=final_guard)


def execute_admitted_physical_click(
    x: int,
    y: int,
    button: MouseButton,
    count: ClickCount,
    mechanism: ClickMechanism,
    hwnd: int | None = None,
    *,
    final_guard: FinalClickGuard,
) -> RawBackendResult:
    """Run an already-queued physical click with a true pre-down guard."""
    if not callable(final_guard) or mechanism is ClickMechanism.BACKGROUND_MESSAGE:
        return _click_progress(False, 0, "invalid admitted physical click")
    return _execute_click_input_unqueued(
        x, y, button, count, mechanism, hwnd=hwnd, final_guard=final_guard,
    )


ADMITTED_PHYSICAL_CLICK_BACKEND = AdmittedPhysicalClickBackend(
    execute_admitted_physical_click
)


@queued_desktop_action("click")
def execute_click_input(
    x: int,
    y: int,
    button: MouseButton,
    count: ClickCount,
    mechanism: ClickMechanism,
    hwnd: int | None = None,
) -> RawBackendResult:
    """Queue one strict single-mechanism click for RawDevice."""
    return _execute_click_input_unqueued(
        x, y, button, count, mechanism, hwnd=hwnd,
    )


@queued_desktop_action("click")
def click(
    x: int,
    y: int,
    button: str = "left",
    double: bool = False,
    hwnd: int | None = None,
    prepositioned: bool = False,
    force_physical: bool = False,
) -> dict:
    """Click at screen coords. Brings target window forward first if given.

    In no-mouse-steal mode, clicks go through background window messages (the
    user's physical cursor is never moved); falls back to SendInput with a
    cursor snap-back if the message click can't resolve a target.
    """
    if _dry_run():
        return {"ok": True, "dry_run": True}
    typed_button = MouseButton(button)
    count = ClickCount.DOUBLE if bool(double) else ClickCount.SINGLE
    # A visual-servo click deliberately owns the physical cursor: its final
    # confirmation is only valid for the cursor position it just measured.
    # Replacing that mouse-down with a background window message would break
    # the move -> confirm -> click atomicity and can target a stale child HWND.
    if _no_mouse_steal() and not prepositioned and not force_physical:
        if multi_input_cooperation_enabled() and not hwnd:
            return cooperative_input_guard("click", hwnd) or {
                "ok": False,
                "blocked": True,
                "cooperative_input": True,
            }
        posted = _post_click(
            int(x), int(y), typed_button, count, expected_hwnd=hwnd,
        )
        if posted.input_completed is True:
            _publish_action_fact("click", x=x, y=y, button=button,
                                 double=double, via="post_message")
            return {"ok": True, "x": x, "y": y, "via": "post_message"}
        if posted.input_completed is None:
            return _legacy_click_failure(posted)
        # fallback: must use SendInput, but restore the user's cursor afterwards
        cooperative_block = cooperative_input_guard("click", hwnd)
        if cooperative_block is not None:
            return cooperative_block
        saved = _cursor_pos()
        try:
            completed = _raw_click(int(x), int(y), typed_button, count, hwnd)
        finally:
            try:
                cursor_restored = _restore_cursor_after_agent_input(
                    saved, (int(x), int(y)), hwnd,
                )
            except Exception:
                cursor_restored = False
        if completed.input_completed is not True or completed.facts.get("blocked"):
            return _legacy_click_failure(completed, cursor_restored=cursor_restored)
        _publish_action_fact("click", x=x, y=y, button=button,
                             double=double, via="sendinput_restored")
        return {
            "ok": True, "x": x, "y": y, "via": "sendinput_restored",
            "prepositioned": bool(prepositioned),
            "cursor_restored": cursor_restored,
        }
    if prepositioned:
        cooperative_block = cooperative_input_guard("visual_servo_click", hwnd)
        if cooperative_block is not None:
            return cooperative_block
        completed = _raw_click(
            int(x), int(y), typed_button, count, hwnd, True, False,
        )
    else:
        completed = _raw_click(int(x), int(y), typed_button, count, hwnd)
    if completed.input_completed is not True or completed.facts.get("blocked"):
        return _legacy_click_failure(completed)
    _publish_action_fact("click", x=x, y=y, button=button, double=double)
    return {
        "ok": True,
        "x": x,
        "y": y,
        "prepositioned": bool(prepositioned),
        "via": "physical_sendinput" if force_physical else "sendinput",
    }


def _raw_click(
    x: int,
    y: int,
    button: MouseButton,
    count: ClickCount,
    hwnd: int | None,
    prepositioned: bool = False,
    allow_target_focus: bool = True,
    final_guard: FinalClickGuard | None = None,
) -> RawBackendResult:
    cooperative_mode = multi_input_cooperation_enabled()
    if hwnd and not cooperative_mode:
        if not prepositioned and allow_target_focus:
            _raw_focus_window(hwnd)
        if not _target_window_still_foreground(hwnd):
            return _click_progress(
                False, 0, "click target is not the current foreground window",
            )
    if prepositioned:
        current_x, current_y = _cursor_pos()
        if abs(int(current_x) - int(x)) > 3 or abs(int(current_y) - int(y)) > 3:
            _raw_move_mouse(x, y)
        time.sleep(0.008)
    else:
        _raw_move_mouse(x, y)
        time.sleep(0.05)
    if cooperative_mode and not _cooperative_action_still_owned(
        hwnd, expected_cursor=(int(x), int(y)),
    ):
        return _click_progress(
            False, 0, _CLICK_OWNERSHIP_LOST,
        )
    if button is MouseButton.LEFT:
        down, up = win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP
    elif button is MouseButton.RIGHT:
        down, up = win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP
    elif button is MouseButton.MIDDLE:
        down, up = win32con.MOUSEEVENTF_MIDDLEDOWN, win32con.MOUSEEVENTF_MIDDLEUP
    else:
        raise TypeError("button must be MouseButton")
    completed_clicks = 0
    for index in range(count.value):
        if hwnd and not cooperative_mode and not _target_window_still_foreground(hwnd):
            return _click_progress(
                False if completed_clicks == 0 else None,
                completed_clicks,
                "click target lost foreground ownership before mouse-down",
            )
        if cooperative_mode and not _cooperative_action_still_owned(
            hwnd, expected_cursor=(int(x), int(y)),
        ):
            return _click_progress(
                False if completed_clicks == 0 else None,
                completed_clicks,
                _CLICK_OWNERSHIP_LOST,
            )
        if final_guard is not None:
            try:
                cursor_before = tuple(map(int, _cursor_pos()))
                guard_decision = final_guard(*cursor_before)
                cursor_after = tuple(map(int, _cursor_pos()))
            except Exception:
                guard_decision = FinalClickGuardDecision.TARGET_UNAVAILABLE
                cursor_before = cursor_after = ()
            if not isinstance(guard_decision, FinalClickGuardDecision):
                guard_decision = FinalClickGuardDecision.TARGET_UNAVAILABLE
            if (
                guard_decision is FinalClickGuardDecision.ADMITTED
                and (
                    cursor_before != (int(x), int(y))
                    or cursor_after != cursor_before
                )
            ):
                guard_decision = FinalClickGuardDecision.POINTER_POSITION_CHANGED
            if guard_decision is not FinalClickGuardDecision.ADMITTED:
                return _click_progress(
                    False if completed_clicks == 0 else None,
                    completed_clicks,
                    f"final click guard rejected: {guard_decision.value}",
                    via="final_target_guard",
                )
        try:
            _send_inputs([_mouse_input(down)])
        except Exception:
            return _click_progress(
                None, completed_clicks, "click down outcome unknown",
                cleanup=_try_release(_send_inputs, [_mouse_input(up)]),
            )
        try:
            sampled = _sample_pressed_state()
            still_owned = not cooperative_mode or _cooperative_action_still_owned(
                hwnd, expected_cursor=(int(x), int(y)),
            )
        except Exception:
            sampled, still_owned = False, False
        try:
            _send_inputs([_mouse_input(up)])
        except Exception:
            return _click_progress(
                None, completed_clicks, "click up outcome unknown",
                cleanup=_try_release(_send_inputs, [_mouse_input(up)]),
            )
        completed_clicks += 1
        if cooperative_mode and (
            not still_owned
            or not _cooperative_action_still_owned(
                hwnd, expected_cursor=(int(x), int(y)),
            )
        ):
            return _click_progress(
                True if completed_clicks == count.value else None,
                completed_clicks,
                _CLICK_OWNERSHIP_LOST,
            )
        if sampled:
            try:
                _settle_after_release()
            except Exception:
                logger.debug("physical-click settle failed", exc_info=True)
        if index + 1 < count.value:
            try:
                time.sleep(0.06)
            except Exception:
                return _click_progress(
                    None, completed_clicks, "click inter-click wait failed",
                )
    return _click_progress(True, completed_clicks)


def _abs_mouse_move(x: int, y: int) -> None:
    """Send an ABSOLUTE hardware-level mouse move through the input queue.

    SetCursorPos repositions the cursor but is "silent": Win11 Paint and other
    WinUI/pointer-input canvases track WM_MOUSEMOVE/WM_POINTER events from the
    input queue, and a relative MOUSEEVENTF_MOVE with dx=dy=0 is a zero-delta
    no-op that never registers a stroke. An absolute move (normalized to the
    virtual desktop, 0..65535) generates a real move event at the target point,
    so press → moves → release draws a continuous stroke. Verified on Win11
    Paint: relative-zero gives 0.0 visual change, absolute draws the line.
    """
    vx = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
    vy = user32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
    vw = user32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
    vh = user32.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
    nx = int((int(x) - vx) * 65535 / max(1, vw - 1))
    ny = int((int(y) - vy) * 65535 / max(1, vh - 1))
    flags = (win32con.MOUSEEVENTF_MOVE | win32con.MOUSEEVENTF_ABSOLUTE
             | 0x4000)  # MOUSEEVENTF_VIRTUALDESK
    _send_inputs([_mouse_input(flags, dx=nx, dy=ny)])


def _emit_move(x: int, y: int) -> None:
    """Move the cursor through a single ABSOLUTE SendInput event so canvas apps
    register continuous motion between button-down and up.

    Uses ONLY the absolute move — not SetCursorPos followed by it. Issuing both
    per interpolation step makes Win11 Paint's stroke registration flaky: the
    two moves to (nearly) the same point in rapid succession get coalesced and
    the stroke intermittently lands as a click (a dot) instead of a line.
    The absolute SendInput move already repositions the system cursor, so
    GetCursorPos-based readers stay consistent. Verified on Win11 Paint:
    pure-absolute draws every stroke; the combo dropped ~half of them."""
    _abs_mouse_move(x, y)


def _stroke_path(
    waypoints: list[tuple[int, int]],
    duration_s: float,
    *,
    hwnd: int | None = None,
) -> bool:
    """Press at the first waypoint, move through the path, release at the end.
    Shared by drag() and draw_strokes() so stroke registration is identical."""
    if (
        not _target_window_still_foreground(hwnd)
        or not _cooperative_action_still_owned(hwnd)
    ):
        return False
    _abs_mouse_move(*waypoints[0])  # absolute move so the press lands as real input
    time.sleep(0.04)
    expected_cursor = waypoints[0]
    if (
        not _target_window_still_foreground(hwnd)
        or not _cooperative_action_still_owned(
            hwnd, expected_cursor=expected_cursor,
        )
    ):
        return False
    pressed = False
    _send_inputs([_mouse_input(win32con.MOUSEEVENTF_LEFTDOWN)])
    pressed = True
    time.sleep(0.02)  # let the app register the press before moving

    try:
        seg_lens = [((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
                    for a, b in zip(waypoints, waypoints[1:])]
        total = max(sum(seg_lens), 1.0)
        for (a, b), seg in zip(zip(waypoints, waypoints[1:]), seg_lens):
            # Keep at most ~8px between events without resampling short paths.
            steps = max(1, int(math.ceil(seg / 8.0)))
            seg_dur = duration_s * (seg / total)
            for i in range(1, steps + 1):
                if (
                    not _target_window_still_foreground(hwnd)
                    or not _cooperative_action_still_owned(
                        hwnd, expected_cursor=expected_cursor,
                    )
                ):
                    return False
                expected_cursor = (
                    int(a[0] + (b[0] - a[0]) * i / steps),
                    int(a[1] + (b[1] - a[1]) * i / steps),
                )
                _emit_move(*expected_cursor)
                time.sleep(max(0.002, seg_dur / steps))
        time.sleep(0.015)
        return bool(
            _target_window_still_foreground(hwnd)
            and _cooperative_action_still_owned(
                hwnd, expected_cursor=expected_cursor,
            )
        )
    finally:
        if pressed:
            _send_inputs([_mouse_input(win32con.MOUSEEVENTF_LEFTUP)])


@queued_desktop_action("drag")
def drag(x1: int, y1: int, x2: int, y2: int, duration_s: float = 0.4,
         points: list | None = None, hwnd: int | None = None) -> dict:
    """Press at the start, move through the path, release at the end.

    Emits MOUSEEVENTF_MOVE on every interpolation step (not just SetCursorPos)
    so paint/canvas apps register a continuous stroke instead of a single
    down→up jump. ``points`` (optional) is a polyline of [x, y] waypoints for
    freehand drawing; when given, the full path is (x1,y1) → points… → (x2,y2).
    """
    if _dry_run():
        return {"ok": True, "dry_run": True}
    cooperative_block = cooperative_input_guard("drag", hwnd)
    if cooperative_block is not None:
        return cooperative_block
    cooperative_mode = multi_input_cooperation_enabled()
    if hwnd and not cooperative_mode:
        _raw_focus_window(hwnd)
        time.sleep(0.05)
    # drag inherently needs a moving cursor; in no-steal mode we still snap the
    # user's cursor back afterwards (a true background drag isn't reliable).
    saved = _cursor_pos() if _no_mouse_steal() else None

    # Build the waypoint list: start → optional polyline → end.
    waypoints: list[tuple[int, int]] = [(int(x1), int(y1))]
    if points:
        for p in points:
            try:
                waypoints.append((int(p[0]), int(p[1])))
            except (TypeError, IndexError, ValueError, KeyError):
                continue
    waypoints.append((int(x2), int(y2)))

    completed = _stroke_path(waypoints, duration_s, hwnd=hwnd)
    if saved:
        cursor_restored = _restore_cursor_after_agent_input(
            saved, waypoints[-1], hwnd,
        )
    BUS.publish("action", action="drag", frm=[x1, y1], to=[x2, y2],
                waypoints=len(waypoints))
    result = {"ok": bool(completed)}
    if not completed:
        result.update({
            "blocked": True,
            "verified": False,
            "failure_type": "POINTER_OWNERSHIP_LOST",
            "cooperative_input": cooperative_mode,
            "error": "drag stopped because target-window or cursor ownership was lost",
        })
    if saved:
        result["cursor_restored"] = cursor_restored
    return result


@queued_desktop_action("draw_strokes")
def draw_strokes(strokes: list, duration_s: float = 0.4,
                 hwnd: int | None = None) -> dict:
    """Draw multiple SEPARATE strokes in one call, lifting the pen between them.

    Each stroke is a polyline [[x,y],...]; the pen presses at its first point,
    moves through the rest, and releases — so strokes don't connect. Multiple
    explicitly grounded paths can be committed in one transaction. Returns the
    count of strokes actually drawn (those with >= 2 valid points)."""
    if _dry_run():
        return {"ok": True, "dry_run": True}
    cooperative_block = cooperative_input_guard("draw_strokes", hwnd)
    if cooperative_block is not None:
        return cooperative_block
    cooperative_mode = multi_input_cooperation_enabled()
    if hwnd and not cooperative_mode:
        _raw_focus_window(hwnd)
        time.sleep(0.05)
    saved = _cursor_pos() if _no_mouse_steal() else None
    drawn = 0
    attempted = 0
    interrupted = False
    last_point: tuple[int, int] | None = None
    for stroke in strokes or []:
        waypoints: list[tuple[int, int]] = []
        for p in stroke or []:
            try:
                waypoints.append((int(p[0]), int(p[1])))
            except (TypeError, IndexError, ValueError, KeyError):
                continue
        if len(waypoints) < 2:
            continue
        if (
            cooperative_mode
            and last_point is not None
            and not _cooperative_action_still_owned(
                hwnd, expected_cursor=last_point,
            )
        ):
            interrupted = True
            break
        attempted += 1
        completed = _stroke_path(waypoints, duration_s, hwnd=hwnd)
        if not completed:
            interrupted = True
            break
        drawn += 1
        last_point = waypoints[-1]
        time.sleep(0.015)  # small gap so the app finalizes each stroke
    all_completed = attempted > 0 and not interrupted and drawn == attempted
    if saved:
        # An interrupted cooperative stroke means the user took ownership.
        # Never snap the shared cursor back in that case, even when interruption
        # happened before the first completed path and last_point is still None.
        cursor_restored = (
            _restore_cursor_after_agent_input(saved, last_point, hwnd)
            if all_completed else False
        )
    BUS.publish("action", action="draw_strokes", strokes=drawn)
    result = {"ok": all_completed, "strokes_drawn": drawn}
    if not all_completed:
        result.update({"verified": False, "blocked": True})
        if attempted == 0:
            result.update({
                "invalid_geometry": True,
                "error": "draw_strokes contained no executable stroke with at least two valid points",
            })
        else:
            result.update({
                "failure_type": "POINTER_OWNERSHIP_LOST",
                "cooperative_input": cooperative_mode,
                "error": "drawing stopped because target-window or cursor ownership was lost",
            })
    if saved:
        result["cursor_restored"] = cursor_restored
    return result


def _publish_action_fact(action: str, **payload: Any) -> None:
    """Telemetry cannot turn one completed input into a fallback replay."""
    try:
        BUS.publish("action", action=action, **payload)
    except Exception:
        logger.debug("%s action telemetry failed", action, exc_info=True)


@queued_desktop_action("scroll")
def execute_scroll_input(
    x: int,
    y: int,
    amount: int,
    hwnd: int | None = None,
) -> RawBackendResult:
    """Execute one scroll attempt and report explicit mechanical completion."""
    if _dry_run():
        return RawBackendResult(False, {"ok": True, "dry_run": True})
    if _no_mouse_steal():
        if multi_input_cooperation_enabled() and not hwnd:
            result = cooperative_input_guard("scroll", hwnd) or {
                "ok": False,
                "blocked": True,
                "cooperative_input": True,
            }
            return RawBackendResult(False, result)
        # WM_MOUSEWHEEL to the window under the point — no cursor move.
        target = 0
        try:
            target = win32gui.WindowFromPoint((int(x), int(y)))
            target_matches = bool(target and _same_window_root(int(target), hwnd))
        except Exception as exc:
            logger.debug("post scroll target resolution failed: %s", exc)
            target_matches = False
        if target_matches:
            wparam = (int(amount) * 120) << 16
            lparam = (int(y) << 16) | (int(x) & 0xFFFF)  # screen coords for wheel
            try:
                win32gui.PostMessage(target, win32con.WM_MOUSEWHEEL, wparam, lparam)
            except Exception:
                return RawBackendResult(None, {
                    "ok": False,
                    "via": "post_message",
                    "error": "post_message_outcome_unknown",
                })
            _publish_action_fact("scroll", x=x, y=y, amount=amount,
                                 via="post_message")
            return RawBackendResult(True, {"ok": True, "via": "post_message"})
        cooperative_block = cooperative_input_guard("scroll", hwnd)
        if cooperative_block is not None:
            return RawBackendResult(False, cooperative_block)
        saved = _cursor_pos()
        completed = True
        wheel_sent = False
        try:
            _raw_move_mouse(x, y)
            time.sleep(0.04)
            if multi_input_cooperation_enabled() and not _cooperative_action_still_owned(
                hwnd, expected_cursor=(int(x), int(y)),
            ):
                completed = False
            else:
                _send_inputs([_mouse_input(win32con.MOUSEEVENTF_WHEEL, data=amount * 120 & 0xFFFFFFFF)])
                wheel_sent = True
                if multi_input_cooperation_enabled() and not _cooperative_action_still_owned(
                    hwnd, expected_cursor=(int(x), int(y)),
                ):
                    completed = False
        finally:
            cursor_restored = _restore_cursor_after_agent_input(
                saved, (int(x), int(y)), hwnd,
            )
        if not completed:
            return RawBackendResult(wheel_sent, {
                "ok": False,
                "verified": False,
                "blocked": True,
                "cooperative_input": True,
                "cursor_restored": cursor_restored,
                "error": "scroll stopped because the user took foreground/cursor ownership",
            })
        _publish_action_fact("scroll", x=x, y=y, amount=amount,
                             via="sendinput_restored")
        return RawBackendResult(True, {
            "ok": True,
            "via": "sendinput_restored",
            "cursor_restored": cursor_restored,
        })
    _raw_move_mouse(x, y)
    time.sleep(0.04)
    _send_inputs([_mouse_input(win32con.MOUSEEVENTF_WHEEL, data=amount * 120 & 0xFFFFFFFF)])
    _publish_action_fact("scroll", x=x, y=y, amount=amount)
    return RawBackendResult(True, {"ok": True})


def scroll(
    x: int,
    y: int,
    amount: int,
    hwnd: int | None = None,
) -> dict:
    """Legacy mapping adapter; amount is positive=up, negative=down."""
    return dict(execute_scroll_input(x, y, amount, hwnd=hwnd).facts)


# ------------------------------------------------------------- keyboard API
@queued_desktop_action("type_text")
def type_text(
    text: str,
    interval_s: float = 0.008,
    hwnd: int | None = None,
) -> dict:
    """Unicode injection — IME-independent, supports Chinese.

    hwnd: if given, re-focus the window every 8 characters so focus theft
    (another window activating mid-type) does not silently swallow keystrokes.
    """
    if _dry_run():
        return {"ok": True, "dry_run": True}

    cooperative_block = cooperative_input_guard("type_text", hwnd)
    if cooperative_block is not None:
        return cooperative_block

    def _refocus_if_needed(i: int) -> bool:
        if hwnd and multi_input_cooperation_enabled():
            try:
                return int(win32gui.GetForegroundWindow() or 0) == int(hwnd)
            except Exception:
                return False
        if hwnd and i > 0 and i % 8 == 0:
            if win32gui.GetForegroundWindow() != hwnd:
                _raw_focus_window(hwnd)
                time.sleep(0.08)
        return True

    if _should_type_via_vk(text):
        typed = 0
        for char in text:
            if not _refocus_if_needed(typed):
                return {
                    "ok": False,
                    "verified": False,
                    "blocked": True,
                    "cooperative_input": True,
                    "typed": typed,
                    "requested": len(text),
                    "error": "foreground changed while typing; stopped instead of stealing keyboard focus",
                }
            if not _press_char_vk(char):
                break
            typed += 1
            if (
                multi_input_cooperation_enabled()
                and not _cooperative_action_still_owned(hwnd)
            ):
                return {
                    "ok": False,
                    "verified": False,
                    "blocked": True,
                    "cooperative_input": True,
                    "typed": typed,
                    "requested": len(text),
                    "error": (
                        "foreground changed immediately after a typed key; "
                        "stopped without sending further input"
                    ),
                }
            time.sleep(max(interval_s, 0.03))
        BUS.publish("action", action="type_text", length=typed, via="vk")
        return {"ok": typed == len(text), "typed": typed,
                "requested": len(text), "via": "vk"}
    typed = 0
    for char in text:
        if not _refocus_if_needed(typed):
            return {
                "ok": False,
                "verified": False,
                "blocked": True,
                "cooperative_input": True,
                "typed": typed,
                "requested": len(text),
                "error": "foreground changed while typing; stopped instead of stealing keyboard focus",
            }
        if char == "\n":
            key_result = _raw_press_keys_for_target(["enter"], hwnd)
            if key_result.get("ok") is not True:
                key_result.setdefault("typed", typed)
                key_result.setdefault("requested", len(text))
                return key_result
            typed += 1
            continue
        code = ord(char)
        if code > 0xFFFF:  # surrogate pair for emoji etc.
            code -= 0x10000
            pair = (0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF))
        else:
            pair = (code,)
        for unit in pair:
            _send_inputs([
                _key_input(scan=unit, flags=_KEYEVENTF_UNICODE),
                _key_input(scan=unit, flags=_KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP),
            ])
        typed += 1
        if (
            multi_input_cooperation_enabled()
            and not _cooperative_action_still_owned(hwnd)
        ):
            return {
                "ok": False,
                "verified": False,
                "blocked": True,
                "cooperative_input": True,
                "typed": typed,
                "requested": len(text),
                "error": (
                    "foreground changed immediately after a typed character; "
                    "stopped without sending further input"
                ),
            }
        time.sleep(interval_s)
    BUS.publish("action", action="type_text", length=typed)
    return {"ok": True, "typed": typed}


def _raw_press_keys(keys: list[str], hwnd: int | None = None) -> dict:
    vks = [_vk_of(k) for k in keys]
    downs, ups = [], []
    for vk in vks:
        flags = _KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED_VKS else 0
        downs.append(_key_input(vk=vk, flags=flags))
        ups.append(_key_input(vk=vk, flags=flags | _KEYEVENTF_KEYUP))
    if multi_input_cooperation_enabled():
        if not _cooperative_action_still_owned(hwnd):
            return {
                "ok": False,
                "verified": False,
                "blocked": True,
                "cooperative_input": True,
                "keys": keys,
                "error": "foreground ownership changed before the key chord",
            }
        pressed = False
        still_owned = True
        try:
            _send_inputs(downs)
            pressed = True
            time.sleep(0.03)
            still_owned = _cooperative_action_still_owned(hwnd)
        finally:
            if pressed:
                _send_inputs(list(reversed(ups)))
        still_owned = bool(
            still_owned and _cooperative_action_still_owned(hwnd)
        )
        if not still_owned:
            return {
                "ok": False,
                "verified": False,
                "blocked": True,
                "cooperative_input": True,
                "keys": keys,
                "error": (
                    "foreground ownership changed during the key chord; "
                    "the agent released its keys and stopped"
                ),
            }
    else:
        _send_inputs(downs)
        time.sleep(0.03)
        _send_inputs(list(reversed(ups)))
    return {"ok": True, "keys": keys}


def _raw_press_keys_for_target(keys: list[str], hwnd: int | None) -> dict:
    """Pass target ownership to modern executors while preserving adapters."""
    try:
        parameters = inspect.signature(_raw_press_keys).parameters.values()
        accepts_hwnd = any(
            parameter.name == "hwnd"
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
    except (TypeError, ValueError):
        accepts_hwnd = True
    result = (
        _raw_press_keys(keys, hwnd=hwnd)
        if accepts_hwnd
        else _raw_press_keys(keys)
    )
    return result if isinstance(result, dict) else {"ok": True, "keys": keys}


@queued_desktop_action("press_keys")
def press_keys(keys: list[str], hwnd: int | None = None) -> dict:
    """Press a chord, e.g. ["ctrl","s"] or single ["enter"]."""
    if _dry_run():
        return {"ok": True, "dry_run": True}
    cooperative_block = cooperative_input_guard("hotkey", hwnd)
    if cooperative_block is not None:
        return cooperative_block
    result = _raw_press_keys_for_target(keys, hwnd)
    if result.get("ok") is not True:
        return result
    BUS.publish("action", action="hotkey", keys=keys)
    return {"ok": True, "keys": keys}


# --------------------------------------------------------------- window API
def _raw_focus_window(hwnd: int) -> dict:
    """Bring one live window forward through bounded Windows fallbacks.

    ``SetForegroundWindow`` can raise while a shell surface temporarily owns
    the foreground lock.  Every activation route is therefore isolated: one
    failed API cannot prevent the remaining exact-window fallbacks.
    """
    attempts: list[dict[str, Any]] = []

    def foreground_matches() -> bool:
        try:
            return int(win32gui.GetForegroundWindow() or 0) == int(hwnd)
        except Exception:
            return False

    def attempt(
        route: str,
        operation: Callable[[], Any],
        settle: float = 0.08,
    ) -> bool:
        try:
            operation()
            attempts.append({"route": route, "called": True})
        except Exception as exc:
            attempts.append({
                "route": route,
                "called": False,
                "error": str(exc)[:240],
            })
        if settle:
            time.sleep(settle)
        return foreground_matches()

    try:
        if not win32gui.IsWindow(int(hwnd)):
            return {
                "ok": False,
                "error": "focus target hwnd is no longer a live window",
                "hwnd": int(hwnd),
                "attempts": attempts,
            }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"could not validate focus target hwnd: {exc}",
            "hwnd": int(hwnd),
            "attempts": attempts,
        }

    try:
        if win32gui.IsIconic(hwnd):
            attempt(
                "restore_minimized",
                lambda: win32gui.ShowWindow(hwnd, win32con.SW_RESTORE),
                settle=0.15,
            )
    except Exception as exc:
        attempts.append({
            "route": "inspect_minimized",
            "called": False,
            "error": str(exc)[:240],
        })
    if foreground_matches():
        return {"ok": True, "route": "already_foreground", "attempts": attempts}

    # Start/search/notification flyouts are hosted by a CoreWindow that can
    # retain the foreground lock while rejecting every activation API.  A
    # single Escape is the normal, reversible way to dismiss that transient
    # shell surface.  Restrict it to this system window class; ordinary app
    # dialogs and windows never receive the key.
    try:
        foreground_hwnd = int(win32gui.GetForegroundWindow() or 0)
        foreground_class = str(
            win32gui.GetClassName(foreground_hwnd) if foreground_hwnd else ""
        ).strip().casefold()
    except Exception:
        foreground_class = ""
    if foreground_class == "windows.ui.core.corewindow":
        attempt(
            "dismiss_transient_shell",
            lambda: _send_inputs([
                _key_input(vk=0x1B),
                _key_input(vk=0x1B, flags=_KEYEVENTF_KEYUP),
            ]),
            settle=0.15,
        )
        if foreground_matches():
            return {
                "ok": True,
                "route": "dismiss_transient_shell",
                "attempts": attempts,
            }

    try:
        _send_inputs([
            _key_input(vk=0x12),
            _key_input(vk=0x12, flags=_KEYEVENTF_KEYUP),
        ])
        attempts.append({"route": "alt_unlock", "called": True})
    except Exception as exc:
        attempts.append({
            "route": "alt_unlock",
            "called": False,
            "error": str(exc)[:240],
        })
    if attempt("set_foreground", lambda: win32gui.SetForegroundWindow(hwnd), 0.1):
        return {"ok": True, "route": "set_foreground", "attempts": attempts}
    if attempt("bring_to_top", lambda: win32gui.BringWindowToTop(hwnd), 0.08):
        return {"ok": True, "route": "bring_to_top", "attempts": attempts}

    # Join input queues only for this activation attempt, then detach every
    # successful attachment in reverse order even if a Win32 call raises.
    attached_threads: list[int] = []
    try:
        current_thread = int(ctypes.windll.kernel32.GetCurrentThreadId() or 0)
        foreground_hwnd = int(win32gui.GetForegroundWindow() or 0)
        thread_ids = {
            int(user32.GetWindowThreadProcessId(int(hwnd), None) or 0),
            int(user32.GetWindowThreadProcessId(foreground_hwnd, None) or 0)
            if foreground_hwnd else 0,
        }
        for thread_id in sorted(thread_ids):
            if not thread_id or thread_id == current_thread:
                continue
            if bool(user32.AttachThreadInput(current_thread, thread_id, True)):
                attached_threads.append(thread_id)

        def attached_activation() -> None:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetActiveWindow(hwnd)
            win32gui.SetForegroundWindow(hwnd)
            try:
                win32gui.SetFocus(hwnd)
            except Exception:
                pass

        if attempt("attach_thread_input", attached_activation, 0.1):
            return {
                "ok": True,
                "route": "attach_thread_input",
                "attempts": attempts,
            }
    except Exception as exc:
        attempts.append({
            "route": "attach_thread_input_setup",
            "called": False,
            "error": str(exc)[:240],
        })
    finally:
        try:
            current_thread = int(ctypes.windll.kernel32.GetCurrentThreadId() or 0)
            for thread_id in reversed(attached_threads):
                try:
                    user32.AttachThreadInput(
                        current_thread, int(thread_id), False,
                    )
                except Exception:
                    pass
        except Exception:
            pass

    if attempt(
        "switch_to_this_window",
        lambda: user32.SwitchToThisWindow(int(hwnd), True),
        0.1,
    ):
        return {
            "ok": True,
            "route": "switch_to_this_window",
            "attempts": attempts,
        }

    def topmost_pulse() -> None:
        flags = (
            win32con.SWP_NOMOVE
            | win32con.SWP_NOSIZE
            | win32con.SWP_SHOWWINDOW
        )
        win32gui.SetWindowPos(
            hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, flags,
        )
        win32gui.SetWindowPos(
            hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags,
        )
        win32gui.SetForegroundWindow(hwnd)

    if attempt("topmost_pulse", topmost_pulse, 0.1):
        return {"ok": True, "route": "topmost_pulse", "attempts": attempts}

    error = next(
        (
            str(item.get("error") or "")
            for item in reversed(attempts)
            if str(item.get("error") or "")
        ),
        "Windows did not grant foreground ownership",
    )
    logger.warning("focus_window failed hwnd=%s: %s", hwnd, error)
    return {
        "ok": False,
        "error": error,
        "hwnd": int(hwnd),
        "foreground_hwnd": int(win32gui.GetForegroundWindow() or 0),
        "attempts": attempts,
    }


@queued_desktop_action("focus_window")
def focus_window(hwnd: int) -> dict:
    cooperative_block = cooperative_input_guard("focus_window", hwnd)
    if cooperative_block is not None:
        return cooperative_block
    return _raw_focus_window(hwnd)


_URL_RE = re.compile(r"(?:https?://|file://|microsoft-edge:)[^\s\"']+", re.IGNORECASE)
_BROWSER_LAUNCH_MARKERS = (
    "start ",
    "msedge",
    "chrome",
    "firefox",
    "iexplore",
    "browser",
)


def _url_from_launch_command(command: str) -> str:
    text = (command or "").strip()
    if _URL_RE.match(text):
        return text.strip('"')
    match = _URL_RE.search(text)
    if not match:
        return ""
    lower = text.casefold()
    if any(marker in lower for marker in _BROWSER_LAUNCH_MARKERS):
        return match.group(0).strip('"')
    return ""


@queued_desktop_action("launch")
def launch(command: str) -> dict:
    """Launch an app: exe name, full path, or shell command."""
    if _dry_run():
        return {"ok": True, "dry_run": True}
    try:
        url = _url_from_launch_command(command)
        if url:
            os.startfile(url)  # noqa: S606 - Windows shell URL/protocol handler
            BUS.publish("action", action="launch", command=command, url=url, via="os.startfile")
            return {"ok": True, "command": command, "target": url, "via": "url"}
        subprocess.Popen(command, shell=True)
        BUS.publish("action", action="launch", command=command)
        return {"ok": True, "command": command}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@queued_desktop_action("close_window", scope="window")
def close_window(hwnd: int) -> dict:
    if _dry_run():
        return {"ok": True, "dry_run": True}
    try:
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ------------------------------------------------------------ UIA fast path
def uia_invoke(element) -> bool:
    """Invoke/Toggle via UIA pattern if the element carries a live handle."""
    ctrl = element.extra.get("uia_ctrl")
    if ctrl is None:
        return False
    for getter in ("GetInvokePattern", "GetTogglePattern", "GetSelectionItemPattern"):
        try:
            pattern = getattr(ctrl, getter)()
            if pattern:
                if getter == "GetInvokePattern":
                    pattern.Invoke()
                elif getter == "GetTogglePattern":
                    pattern.Toggle()
                else:
                    pattern.Select()
                return True
        except Exception:
            continue
    return False


def uia_set_value(element, value: str) -> bool:
    ctrl = element.extra.get("uia_ctrl")
    if ctrl is None:
        return False
    try:
        pattern = ctrl.GetValuePattern()
        if pattern:
            pattern.SetValue(value)
            return True
    except Exception:
        pass
    return False


# ------------------------------------------------------------------- clipboard
# Ported from cc-haha runtime/win_helper.py

@queued_desktop_action("clipboard_read", scope="clipboard")
def clipboard_read() -> str:
    """Read the current clipboard text content."""
    try:
        import pyperclip
        return pyperclip.paste() or ""
    except Exception as exc:
        logger.debug("clipboard_read 失败: %s", exc)
        # Fallback: Win32 API
        try:
            win32gui.OpenClipboard(None)
            data = win32gui.GetClipboardData(win32con.CF_UNICODETEXT)
            win32gui.CloseClipboard()
            return data or ""
        except Exception:
            return ""


@queued_desktop_action("clipboard_write", scope="clipboard")
def clipboard_write(text: str) -> dict:
    """Write text to the system clipboard."""
    if _dry_run():
        return {"ok": True, "dry_run": True}
    try:
        import pyperclip
        pyperclip.copy(text)
        BUS.publish("action", action="clipboard_write", length=len(text))
        return {"ok": True, "length": len(text)}
    except Exception as exc:
        logger.debug("clipboard_write 失败: %s", exc)
        # Fallback: Win32 API
        try:
            win32gui.OpenClipboard(None)
            win32gui.EmptyClipboard()
            win32gui.SetClipboardData(win32con.CF_UNICODETEXT, text)
            win32gui.CloseClipboard()
            BUS.publish("action", action="clipboard_write", length=len(text), via="win32")
            return {"ok": True, "length": len(text), "via": "win32"}
        except Exception as exc2:
            return {"ok": False, "error": str(exc2)}


def _image_file_to_dib(path: Path) -> tuple[bytes, int, int]:
    """Read a raster image into the CF_DIB payload accepted by Windows apps."""
    from PIL import Image

    with Image.open(path) as source:
        image = source.convert("RGB")
        width, height = image.size
        buffer = BytesIO()
        image.save(buffer, format="BMP")
    # CF_DIB contains a BITMAPINFOHEADER, while BMP files prefix it with file
    # metadata that clipboard consumers do not expect.
    return buffer.getvalue()[14:], int(width), int(height)


@queued_desktop_action("paste_image_file", scope="physical_clipboard")
def paste_image_file(path: str, hwnd: int | None = None) -> dict:
    """Paste an existing PNG/JPEG as an image, without opening an attachment UI."""
    if _dry_run():
        return {"ok": True, "dry_run": True, "path": str(path)}
    image_path = Path(str(path or "")).expanduser()
    if not image_path.is_file():
        return {"ok": False, "error": f"image file not found: {image_path}"}
    if image_path.suffix.casefold() not in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}:
        return {"ok": False, "error": "paste_image_file requires a PNG, JPEG, BMP, or WebP file"}
    cooperative_block = cooperative_input_guard("paste_image_file", hwnd)
    if cooperative_block is not None:
        return cooperative_block
    try:
        import win32clipboard

        dib, width, height = _image_file_to_dib(image_path)
        if hwnd and not multi_input_cooperation_enabled():
            focused = _raw_focus_window(int(hwnd))
            if not focused.get("ok"):
                return {"ok": False, "error": focused.get("error", "failed to focus target window")}
        opened = False
        last_error: Exception | None = None
        for _attempt in range(4):
            try:
                win32clipboard.OpenClipboard()
                opened = True
                break
            except Exception as exc:
                last_error = exc
                time.sleep(0.04)
        if not opened:
            raise RuntimeError(f"clipboard is busy: {last_error}")
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_DIB, dib)
        finally:
            win32clipboard.CloseClipboard()
        cooperative_block = cooperative_input_guard("paste_image_file", hwnd)
        if cooperative_block is not None:
            return cooperative_block
        chord_result = _raw_press_keys_for_target(["ctrl", "v"], hwnd)
        if chord_result.get("ok") is not True:
            return chord_result
        BUS.publish(
            "action",
            action="paste_image_file",
            path=str(image_path),
            width=width,
            height=height,
        )
        return {
            "ok": True,
            "path": str(image_path),
            "width": width,
            "height": height,
            "via": "clipboard_dib",
        }
    except Exception as exc:
        logger.debug("paste_image_file failed: %s", exc)
        return {"ok": False, "error": str(exc)}


@queued_desktop_action("clipboard_paste", scope="physical_clipboard")
def clipboard_paste(hwnd: int | None = None) -> dict:
    """Paste from clipboard via Ctrl+V."""
    if _dry_run():
        return {"ok": True, "dry_run": True}
    cooperative_block = cooperative_input_guard("clipboard_paste", hwnd)
    if cooperative_block is not None:
        return cooperative_block
    chord_result = _raw_press_keys_for_target(["ctrl", "v"], hwnd)
    if chord_result.get("ok") is not True:
        return chord_result
    BUS.publish("action", action="clipboard_paste")
    return {"ok": True}


# ---------------------------------------------------------------- mouse fine-control
# Ported from cc-haha runtime/win_helper.py

def cursor_position() -> dict:
    """Return the current cursor (x, y) position."""
    try:
        x, y = win32api.GetCursorPos()
        return {"ok": True, "x": int(x), "y": int(y)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@queued_desktop_action("mouse_down")
def mouse_down(button: str = "left", hwnd: int | None = None) -> dict:
    """Press and hold a mouse button at the current cursor position."""
    if _dry_run():
        return {"ok": True, "dry_run": True}
    if multi_input_cooperation_enabled():
        return {
            "ok": False,
            "verified": False,
            "blocked": True,
            "cooperative_input": True,
            "action": "mouse_down",
            "error": (
                "cooperative input forbids a mouse button remaining globally held across "
                "tool/model turns; use atomic drag or draw_strokes instead"
            ),
        }
    cooperative_block = cooperative_input_guard("mouse_down", hwnd)
    if cooperative_block is not None:
        return cooperative_block
    flags = {
        "left": win32con.MOUSEEVENTF_LEFTDOWN,
        "right": win32con.MOUSEEVENTF_RIGHTDOWN,
        "middle": win32con.MOUSEEVENTF_MIDDLEDOWN,
    }
    if button not in flags:
        return {"ok": False, "error": f"未知鼠标键: {button}"}
    with _held_mouse_lock:
        if button in _held_mouse_buttons:
            return {
                "ok": False,
                "verified": False,
                "blocked": True,
                "button": button,
                "error": "mouse button is already held by this agent transaction",
            }
        # Record ownership before injection so an exception immediately after
        # SendInput can still be recovered by the Controller/session cleanup.
        _held_mouse_buttons.add(button)
    try:
        _send_inputs([_mouse_input(flags[button])])
    except Exception:
        release_flag = {
            "left": win32con.MOUSEEVENTF_LEFTUP,
            "right": win32con.MOUSEEVENTF_RIGHTUP,
            "middle": win32con.MOUSEEVENTF_MIDDLEUP,
        }[button]
        try:
            _send_inputs([_mouse_input(release_flag)])
        finally:
            with _held_mouse_lock:
                _held_mouse_buttons.discard(button)
        raise
    press_sampled = _sample_pressed_state()
    BUS.publish("action", action="mouse_down", button=button)
    return {"ok": True, "button": button, "press_sampled": press_sampled}


@queued_desktop_action("mouse_up")
def mouse_up(button: str = "left") -> dict:
    """Release a mouse button at the current cursor position."""
    if _dry_run():
        return {"ok": True, "dry_run": True}
    with _held_mouse_lock:
        owned_by_agent = button in _held_mouse_buttons
    if not owned_by_agent:
        return {
            "ok": False,
            "verified": False,
            "blocked": True,
            "cooperative_input": multi_input_cooperation_enabled(),
            "action": "mouse_up",
            "error": (
                "mouse_up will not release a globally held mouse button unless "
                "this agent transaction previously pressed that button"
            ),
        }
    if button == "left":
        _send_inputs([_mouse_input(win32con.MOUSEEVENTF_LEFTUP)])
    elif button == "right":
        _send_inputs([_mouse_input(win32con.MOUSEEVENTF_RIGHTUP)])
    elif button == "middle":
        _send_inputs([_mouse_input(win32con.MOUSEEVENTF_MIDDLEUP)])
    else:
        return {"ok": False, "error": f"未知鼠标键: {button}"}
    with _held_mouse_lock:
        _held_mouse_buttons.discard(button)
    BUS.publish("action", action="mouse_up", button=button)
    return {"ok": True, "button": button}


def agent_holds_mouse_button(button: str = "left") -> bool:
    """Return whether the current process owns a matching injected press."""
    with _held_mouse_lock:
        return str(button or "left") in _held_mouse_buttons


def release_held_mouse_buttons() -> list[str]:
    """Release mouse buttons left held by an interrupted or completed run."""
    with _held_mouse_lock:
        buttons = sorted(_held_mouse_buttons)
    released: list[str] = []
    for button in buttons:
        result = mouse_up(button)
        if result.get("ok"):
            released.append(button)
    return released


# --------------------------------------------------------------- screenshot base64

def screenshot_base64(display_id: int | None = None,
                      quality: int = 75) -> dict:
    """Capture the full screen (or a specific display) and return JPEG base64.

    Ported from cc-haha runtime/win_helper.py capture_display().
    Uses mss for cross-platform capture; falls back to win32 API if mss is
    not available.
    """
    if _dry_run():
        return {"ok": True, "dry_run": True, "base64": "", "width": 0, "height": 0}
    try:
        import mss
        from PIL import Image

        with mss.mss() as sct:
            # If display_id is omitted capture the complete virtual desktop.
            if display_id is not None and display_id < len(sct.monitors) - 1:
                monitor = sct.monitors[display_id + 1]  # sct.monitors[0] is "all"
            else:
                monitor = sct.monitors[0]
            raw = sct.grab(monitor)
            image = Image.frombytes("RGB", raw.size, raw.rgb)
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=quality, optimize=True)
        b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
        return {
            "ok": True,
            "base64": b64,
            "left": int(monitor["left"]),
            "top": int(monitor["top"]),
            "width": image.width,
            "height": image.height,
            "size_bytes": len(buffer.getvalue()),
        }
    except ImportError:
        # mss not available — fall back to win32 screenshot
        try:
            from ..utils.screenshot import capture, virtual_screen_region
            region = virtual_screen_region()
            frame = capture(region)
            image = Image.fromarray(frame[:, :, ::-1].copy())
            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=quality, optimize=True)
            b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
            return {
                "ok": True,
                "base64": b64,
                "left": region.left,
                "top": region.top,
                "width": image.width,
                "height": image.height,
                "size_bytes": len(buffer.getvalue()),
                "via": "win32_fallback",
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ----------------------------------------------------------------- hold keys
# Ported from cc-haha runtime/win_helper.py hold_keys()

@queued_desktop_action("hold_keys")
def hold_keys(
    keys: list[str],
    duration_ms: int,
    hwnd: int | None = None,
    stop_event=None,
) -> dict:
    """Press and hold a chord for ``duration_ms`` then release.

    Useful for games / accelerated UI where a key must stay down (e.g. hold
    Shift while clicking, or hold an arrow to scroll a long list).
    """
    if _dry_run():
        return {"ok": True, "dry_run": True}
    try:
        duration_ms = int(duration_ms)
    except (TypeError, ValueError):
        return {"ok": False, "error": "duration_ms must be an integer in 1..30000"}
    if not keys:
        return {"ok": False, "error": "hold_keys requires at least one key"}
    if not 1 <= duration_ms <= 30_000:
        return {"ok": False, "error": "duration_ms must be in 1..30000"}
    cooperative_block = cooperative_input_guard("hold_keys", hwnd)
    if cooperative_block is not None:
        return cooperative_block
    try:
        vks = [_vk_of(k) for k in keys]
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    downs, ups = [], []
    for vk in vks:
        flags = _KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED_VKS else 0
        downs.append(_key_input(vk=vk, flags=flags))
        ups.append(_key_input(vk=vk, flags=flags | _KEYEVENTF_KEYUP))
    _send_inputs(downs)
    interrupted = False
    try:
        remaining_s = max(int(duration_ms), 0) / 1000.0
        while remaining_s > 0:
            interval = min(0.025, remaining_s)
            time.sleep(interval)
            remaining_s -= interval
            if stop_event is not None and stop_event.is_set():
                interrupted = True
                break
            if not _cooperative_action_still_owned(hwnd):
                interrupted = True
                break
    finally:
        _send_inputs(list(reversed(ups)))
    if interrupted:
        return {
            "ok": False,
            "verified": False,
            "blocked": True,
            "cooperative_input": True,
            "canceled": bool(stop_event is not None and stop_event.is_set()),
            "keys": keys,
            "duration_ms": duration_ms,
            "error": "key hold stopped because foreground ownership changed",
        }
    BUS.publish("action", action="hold_keys", keys=keys, duration_ms=duration_ms)
    return {"ok": True, "keys": keys, "duration_ms": duration_ms}


# --------------------------------------------------------------- displays
# Ported from cc-haha runtime/win_helper.py get_displays() (mss-based here)

def list_displays() -> dict:
    """Enumerate monitors (id, geometry, origin, primary flag).

    cc-haha used screeninfo; we reuse mss (already a dependency) so no new
    package is needed. ``id`` matches the index ``screenshot``/``zoom`` accept.
    """
    try:
        import mss
        with mss.mss() as sct:
            all_mon = sct.monitors  # [0]=virtual bounding box, [1..]=real
            primary = all_mon[1] if len(all_mon) > 1 else all_mon[0]
            displays = []
            for idx, mon in enumerate(all_mon[1:]):
                displays.append({
                    "id": idx,
                    "width": mon["width"],
                    "height": mon["height"],
                    "originX": mon["left"],
                    "originY": mon["top"],
                    "isPrimary": (mon == primary),
                })
        return {"ok": True, "count": len(displays), "displays": displays}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# --------------------------------------------------------------- zoom region

def zoom_region(x: int, y: int, width: int, height: int,
                quality: int = 80) -> dict:
    """Capture an arbitrary screen rectangle and return JPEG base64.

    Ported from cc-haha runtime/win_helper.py capture_region(). Use to inspect
    a small area (a status icon, a number) at full resolution instead of
    downscaling the whole screen.
    """
    if _dry_run():
        return {"ok": True, "dry_run": True, "base64": "", "width": 0, "height": 0}
    if width <= 0 or height <= 0:
        return {"ok": False, "error": "width/height 必须为正"}
    try:
        import mss
        from PIL import Image
        with mss.mss() as sct:
            region = {"left": int(x), "top": int(y),
                      "width": int(width), "height": int(height)}
            raw = sct.grab(region)
            image = Image.frombytes("RGB", raw.size, raw.rgb)
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=quality, optimize=True)
        b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
        return {
            "ok": True,
            "base64": b64,
            "width": image.width,
            "height": image.height,
            "region": {"x": int(x), "y": int(y),
                       "width": int(width), "height": int(height)},
            "size_bytes": len(buffer.getvalue()),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
