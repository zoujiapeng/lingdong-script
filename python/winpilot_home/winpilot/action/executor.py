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
    AdmittedPhysicalDragBackend,
    AdmittedPhysicalHotkeyBackend,
    AdmittedPhysicalScrollBackend,
    AdmittedPhysicalTypeTextBackend,
    ClickCount,
    ClickMechanism,
    FinalKeyboardGuard,
    FinalKeyboardGuardDecision,
    FinalPointerGuard,
    FinalPointerGuardDecision,
    HotkeyChord,
    MAX_TYPE_TEXT_CODEPOINTS,
    MouseButton,
    RawBackendResult,
    _canonical_drag_points,
)

user32 = ctypes.windll.user32
imm32 = ctypes.windll.imm32

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
_STRICT_VK_TEXT_CHARS = set(
    string.ascii_letters + string.digits + string.punctuation + " "
)


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


def _should_strict_type_via_vk(text: str) -> bool:
    """Prefer distinct layout-backed key transitions for exact ASCII text."""

    return bool(text) and all(
        char == "\n" or char in _STRICT_VK_TEXT_CHARS
        for char in text
    )


_WM_INPUTLANGCHANGEREQUEST = 0x0050
_ENGLISH_US_LAYOUT = 0x04090409


def _english_layout_switch(
    hwnd: int | None,
) -> tuple[tuple[int | None, bool] | None, bool]:
    """Prepare a VK-safe keyboard state for the foreground window thread.

    Returns ``(restore, safe)``.  ``safe`` is False only when the target is
    unavailable and the caller must fall back to the UNICODE path.  When
    ``restore`` is not None it carries ``(original_layout, ime_was_open)`` to
    restore afterwards.

    SendInput VK transitions are interpreted by the FOREGROUND window thread
    (not the bound target), so layout and IME state are sampled and fixed on
    the foreground window.  Two independent blind spots corrupt exact ASCII
    input on Chinese/Japanese/Korean systems:

    - the layout is 0x0804 (Chinese): digit/punctuation scan codes are
      consumed as IME candidate keys (``0 5 1`` -> gone);
    - the layout is 0x0409 but a Chinese IME is open: the IME composition
      buffer still intercepts VK input and can reorder plain ASCII
      (``gui`` -> ``giu``) because ``GetKeyboardLayout`` cannot see IME
      activation.

    The fix switches the foreground thread to the US English layout when
    needed (waiting for the switch to actually apply), closes the IME for the
    duration of the VK input, then restores both.
    """
    if not hwnd:
        return None, False
    foreground = int(user32.GetForegroundWindow() or 0)
    if not foreground:
        return None, False
    tid = user32.GetWindowThreadProcessId(foreground, None)
    if not tid:
        return None, False
    original = user32.GetKeyboardLayout(tid)
    english = user32.LoadKeyboardLayoutW("00000409", 0x00000100)  # KLF_ACTIVATE
    if not english:
        return None, False

    ime_context = imm32.ImmGetContext(foreground)
    ime_open = False
    if ime_context:
        ime_open = bool(imm32.ImmGetOpenStatus(ime_context))

    layout_is_english = (
        original == _ENGLISH_US_LAYOUT or (original & 0xFFFF) == 0x0409
    )
    if layout_is_english and not ime_open:
        if ime_context:
            imm32.ImmReleaseContext(foreground, ime_context)
        return None, True

    changed = False
    if not layout_is_english:
        user32.PostMessageW(foreground, _WM_INPUTLANGCHANGEREQUEST, 0, english)
        # The switch is async; wait until the foreground thread actually
        # applies it so the first typed character is not interpreted through
        # the old layout.
        deadline = time.monotonic() + 0.15
        while time.monotonic() < deadline:
            if int(user32.GetKeyboardLayout(tid) or 0) == int(english):
                break
            time.sleep(0.01)
        changed = True
    if ime_open and ime_context:
        imm32.ImmSetOpenStatus(ime_context, False)
        changed = True
    if ime_context:
        imm32.ImmReleaseContext(foreground, ime_context)
    if not changed:
        return None, True
    return (original, ime_open), True


def _restore_layout_switch(
    hwnd: int | None,
    restore: tuple[int | None, bool] | None,
) -> None:
    if not hwnd or not restore:
        return
    foreground = int(user32.GetForegroundWindow() or 0)
    if not foreground:
        return
    original, ime_was_open = restore
    if original:
        user32.PostMessageW(foreground, _WM_INPUTLANGCHANGEREQUEST, 0, original)
    if ime_was_open:
        context = imm32.ImmGetContext(foreground)
        if context:
            imm32.ImmSetOpenStatus(context, True)
            imm32.ImmReleaseContext(foreground, context)
    time.sleep(0.02)


def _dry_run() -> bool:
    return bool(CONFIG.get("agent", "dry_run", default=False))


# ---- cursor helpers ---------------------------------------------------------
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
    expected_agent_pos: tuple[int, int] | None = None,
    hwnd: int | None = None,
) -> bool:
    """Restore the shared cursor after an agent input action."""
    _restore_cursor(saved)
    return True


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
    error_kind: str | None = None,
) -> RawBackendResult:
    facts: dict[str, Any] = {"completed_clicks": int(completed_clicks)}
    if error:
        facts.update({"ok": False, "blocked": True, "error": error})
    if error_kind:
        facts["error_kind"] = error_kind
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
    final_guard: FinalPointerGuard | None = None,
    enforce_foreground: bool = True,
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
        return _raw_click(*raw_args, enforce_foreground=enforce_foreground)
    return _raw_click(
        *raw_args,
        enforce_foreground=enforce_foreground,
        final_guard=final_guard,
    )


def execute_admitted_physical_click(
    x: int,
    y: int,
    button: MouseButton,
    count: ClickCount,
    mechanism: ClickMechanism,
    hwnd: int | None = None,
    *,
    final_guard: FinalPointerGuard,
    enforce_foreground: bool = True,
) -> RawBackendResult:
    """Run an already-queued physical click with a true pre-down guard."""
    if not callable(final_guard) or mechanism is ClickMechanism.BACKGROUND_MESSAGE:
        return _click_progress(False, 0, "invalid admitted physical click")
    return _execute_click_input_unqueued(
        x, y, button, count, mechanism, hwnd=hwnd, final_guard=final_guard,
        enforce_foreground=enforce_foreground,
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
    if prepositioned:
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
    enforce_foreground: bool = False,
    final_guard: FinalPointerGuard | None = None,
) -> RawBackendResult:
    if hwnd:
        if not prepositioned and allow_target_focus:
            _raw_focus_window(hwnd)
        if enforce_foreground and not _target_window_still_foreground(hwnd):
            return _click_progress(
                False, 0, "click target is not the current foreground window",
                error_kind="target_window_lost",
            )
    if prepositioned:
        current_x, current_y = _cursor_pos()
        if abs(int(current_x) - int(x)) > 3 or abs(int(current_y) - int(y)) > 3:
            _raw_move_mouse(x, y)
        time.sleep(0.008)
    else:
        _raw_move_mouse(x, y)
        time.sleep(0.05)
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
        if (
            enforce_foreground
            and hwnd
            and not _target_window_still_foreground(hwnd)
        ):
            return _click_progress(
                False if completed_clicks == 0 else None,
                completed_clicks,
                "click target lost foreground ownership before mouse-down",
                error_kind=(
                    "target_window_lost"
                    if completed_clicks == 0
                    else None
                ),
            )
        if final_guard is not None:
            try:
                cursor_before = tuple(map(int, _cursor_pos()))
                guard_decision = final_guard(*cursor_before)
                cursor_after = tuple(map(int, _cursor_pos()))
            except Exception:
                guard_decision = FinalPointerGuardDecision.TARGET_UNAVAILABLE
                cursor_before = cursor_after = ()
            if not isinstance(guard_decision, FinalPointerGuardDecision):
                guard_decision = FinalPointerGuardDecision.TARGET_UNAVAILABLE
            if (
                guard_decision is FinalPointerGuardDecision.ADMITTED
                and (
                    cursor_before != (int(x), int(y))
                    or cursor_after != cursor_before
                )
            ):
                guard_decision = FinalPointerGuardDecision.POINTER_POSITION_CHANGED
            if guard_decision is not FinalPointerGuardDecision.ADMITTED:
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
        except Exception:
            sampled = False
        try:
            _send_inputs([_mouse_input(up)])
        except Exception:
            return _click_progress(
                None, completed_clicks, "click up outcome unknown",
                cleanup=_try_release(_send_inputs, [_mouse_input(up)]),
            )
        completed_clicks += 1
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


def _move_path_while_pressed(
    waypoints: list[tuple[int, int]],
    duration_s: float,
    *,
    hwnd: int | None = None,
    progress: dict[str, int | bool] | None = None,
) -> bool:
    """Move an already-held pointer without changing button state."""

    expected_cursor = waypoints[0]
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
            ):
                return False
            expected_cursor = (
                int(a[0] + (b[0] - a[0]) * i / steps),
                int(a[1] + (b[1] - a[1]) * i / steps),
            )
            _emit_move(*expected_cursor)
            if progress is not None:
                progress["moves_completed"] = (
                    int(progress.get("moves_completed", 0)) + 1
                )
            time.sleep(max(0.002, seg_dur / steps))
    time.sleep(0.015)
    return bool(
        _target_window_still_foreground(hwnd)
    )


def _stroke_path(
    waypoints: list[tuple[int, int]],
    duration_s: float,
    *,
    hwnd: int | None = None,
    before_press: Callable[[], bool] | None = None,
    progress: dict[str, int | bool] | None = None,
) -> bool:
    """Press at the first waypoint, move through the path, release at the end.
    Shared by drag() and draw_strokes() so stroke registration is identical."""
    if (
        not _target_window_still_foreground(hwnd)
    ):
        return False
    if progress is not None:
        progress.update(
            button_pressed=False,
            moves_completed=0,
            release_cleanup_attempted=False,
            release_cleanup_completed=False,
        )
    _abs_mouse_move(*waypoints[0])  # absolute move so the press lands as real input
    time.sleep(0.04)
    expected_cursor = waypoints[0]
    if (
        not _target_window_still_foreground(hwnd)
    ):
        return False
    if before_press is not None:
        try:
            if not before_press():
                return False
        except Exception:
            return False
    pressed = False
    _send_inputs([_mouse_input(win32con.MOUSEEVENTF_LEFTDOWN)])
    pressed = True
    if progress is not None:
        progress["button_pressed"] = True
    time.sleep(0.02)  # let the app register the press before moving

    try:
        return _move_path_while_pressed(
            waypoints,
            duration_s,
            hwnd=hwnd,
            progress=progress,
        )
    finally:
        if pressed:
            if progress is not None:
                progress["release_cleanup_attempted"] = True
            _send_inputs([_mouse_input(win32con.MOUSEEVENTF_LEFTUP)])
            if progress is not None:
                progress["release_cleanup_completed"] = True


def _physical_drag_result(
    input_completed: bool | None,
    *,
    error: str = "",
    guard_decision: FinalPointerGuardDecision | None = None,
    cursor_restored: bool | None = None,
    release_cleanup_attempted: bool = False,
    release_cleanup_completed: bool = False,
    segment_phase: str | None = None,
    held_before: bool | None = None,
    held_after: bool | None = None,
    moves_completed: int | None = None,
    error_kind: str | None = None,
) -> RawBackendResult:
    facts: dict[str, Any] = {
        "ok": input_completed is True,
        "via": (
            "physical_sendinput"
            if guard_decision in {None, FinalPointerGuardDecision.ADMITTED}
            else "final_target_guard"
        ),
    }
    if error:
        facts.update({"blocked": True, "error": error})
    if error_kind:
        facts["error_kind"] = error_kind
    if guard_decision is not None:
        facts["guard_decision"] = guard_decision.value
    if cursor_restored is not None:
        facts["cursor_restored"] = cursor_restored
    if segment_phase is not None:
        facts["segment_phase"] = segment_phase
    if held_before is not None:
        facts["held_before"] = held_before
    if held_after is not None:
        facts["held_after"] = held_after
    if moves_completed is not None:
        facts["moves_completed"] = moves_completed
    if release_cleanup_attempted:
        facts.update(
            release_cleanup_attempted=True,
            release_cleanup_completed=release_cleanup_completed,
        )
    return RawBackendResult(input_completed, facts)


def _left_button_owned_by_agent() -> bool:
    with _held_mouse_lock:
        return "left" in _held_mouse_buttons


def _record_left_button_owned() -> bool:
    with _held_mouse_lock:
        if "left" in _held_mouse_buttons:
            return False
        _held_mouse_buttons.add("left")
        return True


def _best_effort_release_left(*, force: bool = False) -> tuple[bool, bool, bool | None]:
    """Return (attempted, completed, held_after) for one unconditional UP."""

    if not force and not _left_button_owned_by_agent():
        return False, False, False
    try:
        _send_inputs([_mouse_input(win32con.MOUSEEVENTF_LEFTUP)])
    except Exception:
        return True, False, None
    with _held_mouse_lock:
        _held_mouse_buttons.discard("left")
    return True, True, False


def _held_drag_guard_decision(
    start: tuple[int, int],
    final_guard: FinalPointerGuard,
) -> FinalPointerGuardDecision:
    try:
        cursor_before = tuple(map(int, _cursor_pos()))
        decision = final_guard(*cursor_before)
        cursor_after = tuple(map(int, _cursor_pos()))
    except Exception:
        return FinalPointerGuardDecision.TARGET_UNAVAILABLE
    canonical = (
        decision
        if isinstance(decision, FinalPointerGuardDecision)
        else FinalPointerGuardDecision.TARGET_UNAVAILABLE
    )
    if (
        canonical is FinalPointerGuardDecision.ADMITTED
        and (cursor_before != start or cursor_after != cursor_before)
    ):
        return FinalPointerGuardDecision.POINTER_POSITION_CHANGED
    return canonical


def _held_drag_failure(
    phase: str,
    *,
    error: str,
    guard_decision: FinalPointerGuardDecision | None,
    held_before: bool,
    moves_completed: int,
    error_kind: str | None = None,
) -> RawBackendResult:
    attempted, released, held_after = _best_effort_release_left(force=True)
    return _physical_drag_result(
        None,
        error=error,
        guard_decision=guard_decision,
        release_cleanup_attempted=attempted,
        release_cleanup_completed=released,
        segment_phase=phase,
        held_before=held_before,
        held_after=held_after,
        moves_completed=moves_completed,
        error_kind=error_kind,
    )


def _execute_held_drag_segment(
    path: list[tuple[int, int]],
    *,
    hwnd: int | None,
    final_guard: FinalPointerGuard,
    phase: str,
) -> RawBackendResult:
    start = path[0]
    held_before = _left_button_owned_by_agent()
    progress: dict[str, int | bool] = {"moves_completed": 0}
    guard_decision = FinalPointerGuardDecision.TARGET_UNAVAILABLE

    if phase == "begin":
        if held_before:
            return _held_drag_failure(
                phase,
                error="left button already held before drag begin",
                guard_decision=None,
                held_before=True,
                moves_completed=0,
            )
        if (
            not _target_window_still_foreground(hwnd)
        ):
            return _held_drag_failure(
                phase,
                error="held drag target unavailable before input",
                guard_decision=guard_decision,
                held_before=False,
                moves_completed=0,
                error_kind="target_window_lost",
            )
        try:
            _abs_mouse_move(*start)
            time.sleep(0.04)
        except Exception:
            return _held_drag_failure(
                phase,
                error="held drag could not reach start point",
                guard_decision=guard_decision,
                held_before=False,
                moves_completed=0,
            )
    elif not held_before:
        return _held_drag_failure(
            phase,
            error="held drag lost left-button ownership",
            guard_decision=None,
            held_before=False,
            moves_completed=0,
        )

    if (
        not _target_window_still_foreground(hwnd)
    ):
        return _held_drag_failure(
            phase,
            error="held drag target changed",
            guard_decision=guard_decision,
            held_before=held_before,
            moves_completed=0,
            error_kind="target_window_lost",
        )

    guard_decision = _held_drag_guard_decision(start, final_guard)
    if guard_decision is not FinalPointerGuardDecision.ADMITTED:
        return _held_drag_failure(
            phase,
            error=f"final pointer guard rejected: {guard_decision.value}",
            guard_decision=guard_decision,
            held_before=held_before,
            moves_completed=0,
        )

    if phase == "begin":
        if not _record_left_button_owned():
            return _held_drag_failure(
                phase,
                error="left button ownership changed before drag begin",
                guard_decision=guard_decision,
                held_before=False,
                moves_completed=0,
            )
        try:
            _send_inputs([_mouse_input(win32con.MOUSEEVENTF_LEFTDOWN)])
            time.sleep(0.02)
        except Exception:
            return _held_drag_failure(
                phase,
                error="held drag DOWN outcome unknown",
                guard_decision=guard_decision,
                held_before=False,
                moves_completed=0,
            )

    try:
        completed = _move_path_while_pressed(
            path,
            0.4,
            hwnd=hwnd,
            progress=progress,
        )
    except Exception:
        completed = False
    moves_completed = int(progress["moves_completed"])
    if not completed:
        return _held_drag_failure(
            phase,
            error="held drag outcome unknown after physical input began",
            guard_decision=guard_decision,
            held_before=held_before,
            moves_completed=moves_completed,
        )

    if phase in {"begin", "continue"}:
        return _physical_drag_result(
            True,
            guard_decision=guard_decision,
            segment_phase=phase,
            held_before=held_before,
            held_after=True,
            moves_completed=moves_completed,
        )

    attempted, released, held_after = _best_effort_release_left(force=True)
    if not released:
        # The required FINISH UP was uncertain; make one additional best-effort
        # UP inside the common failure path, without replaying any movement.
        return _held_drag_failure(
            phase,
            error="held drag final UP outcome unknown",
            guard_decision=guard_decision,
            held_before=held_before,
            moves_completed=moves_completed,
        )
    return _physical_drag_result(
        True,
        guard_decision=guard_decision,
        release_cleanup_attempted=attempted,
        release_cleanup_completed=released,
        segment_phase=phase,
        held_before=held_before,
        held_after=held_after,
        moves_completed=moves_completed,
    )


def execute_admitted_physical_drag(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    hwnd: int | None = None,
    *,
    points: list[tuple[int, int]] | tuple[tuple[int, int], ...] = (),
    final_guard: FinalPointerGuard | None = None,
    pointer_already_held: bool = False,
    keep_holding: bool = False,
    hold_token: str | None = None,
    cleanup_only: bool = False,
) -> RawBackendResult:
    """Run one atomic drag or one token-gated held-drag segment."""

    canonical_points = _canonical_drag_points(points)
    held_mode = pointer_already_held or keep_holding
    stationary_held = bool(
        pointer_already_held is True
        and not canonical_points
        and (x1, y1) == (x2, y2)
    )
    closed_path = bool(
        canonical_points
        and (x1, y1) == (x2, y2)
    )
    valid_hold_token = (
        type(hold_token) is str
        and bool(hold_token)
        and len(hold_token) <= 256
    )
    if (
        type(pointer_already_held) is not bool
        or type(keep_holding) is not bool
        or type(cleanup_only) is not bool
        or canonical_points is None
        or (
            cleanup_only
            and (
                not pointer_already_held
                or keep_holding
                or not valid_hold_token
            )
        )
        or (
            not cleanup_only
            and (
                any(type(value) is not int for value in (x1, y1, x2, y2))
                or (
                    (x1, y1) == (x2, y2)
                    and not stationary_held
                    and not closed_path
                )
                or not callable(final_guard)
                or (held_mode and not valid_hold_token)
                or (not held_mode and hold_token is not None)
            )
        )
    ):
        return _physical_drag_result(
            False,
            error="invalid admitted physical drag",
        )

    phase = (
        "cleanup"
        if cleanup_only
        else "continue"
        if pointer_already_held and keep_holding
        else "finish"
        if pointer_already_held
        else "begin"
        if keep_holding
        else "atomic"
    )
    if _dry_run():
        if phase != "atomic":
            return _physical_drag_result(
                True,
                segment_phase=phase,
                held_before=pointer_already_held,
                held_after=keep_holding if phase != "cleanup" else False,
                moves_completed=0,
            )
        return RawBackendResult(False, {"ok": True, "dry_run": True})

    if cleanup_only:
        held_before = _left_button_owned_by_agent()
        attempted, released, held_after = _best_effort_release_left(force=True)
        return _physical_drag_result(
            True if released else None,
            error="" if released else "held drag cleanup UP outcome unknown",
            release_cleanup_attempted=attempted,
            release_cleanup_completed=released,
            segment_phase=phase,
            held_before=held_before,
            held_after=held_after,
            moves_completed=0,
        )

    start = (x1, y1)
    end = (x2, y2)
    path = (
        [start]
        if stationary_held
        else [start, *canonical_points, end]
    )
    if held_mode:
        return _execute_held_drag_segment(
            path,
            hwnd=hwnd,
            final_guard=final_guard,
            phase=phase,
        )

    try:
        saved = tuple(map(int, _cursor_pos()))
    except Exception:
        return _physical_drag_result(
            False,
            error="drag cursor position unavailable",
        )

    guard_decision = FinalPointerGuardDecision.TARGET_UNAVAILABLE
    progress: dict[str, int | bool] = {
        "button_pressed": False,
        "moves_completed": 0,
        "release_cleanup_attempted": False,
        "release_cleanup_completed": False,
    }

    def before_press() -> bool:
        nonlocal guard_decision
        try:
            cursor_before = tuple(map(int, _cursor_pos()))
            decision = final_guard(*cursor_before)
            cursor_after = tuple(map(int, _cursor_pos()))
        except Exception:
            decision = FinalPointerGuardDecision.TARGET_UNAVAILABLE
            cursor_before = cursor_after = ()
        guard_decision = (
            decision
            if isinstance(decision, FinalPointerGuardDecision)
            else FinalPointerGuardDecision.TARGET_UNAVAILABLE
        )
        if (
            guard_decision is FinalPointerGuardDecision.ADMITTED
            and (cursor_before != start or cursor_after != cursor_before)
        ):
            guard_decision = FinalPointerGuardDecision.POINTER_POSITION_CHANGED
        return guard_decision is FinalPointerGuardDecision.ADMITTED

    completed = False
    error = ""
    try:
        completed = _stroke_path(
            path,
            0.4,
            hwnd=hwnd,
            before_press=before_press,
            progress=progress,
        )
        if not completed:
            if not progress["button_pressed"]:
                error = (
                    f"final pointer guard rejected: {guard_decision.value}"
                    if guard_decision is not FinalPointerGuardDecision.ADMITTED
                    else "drag could not start"
                )
            else:
                error = "drag outcome unknown after physical input began"
    except Exception:
        error = (
            "drag outcome unknown after physical input began"
            if progress["button_pressed"]
            else "drag input did not start"
        )
    finally:
        cursor_restored = _restore_cursor_after_agent_input(saved, end, hwnd)

    input_completed = (
        True
        if completed
        else None
        if progress["button_pressed"]
        else False
    )
    error_kind: str | None = None
    if input_completed is False and not _target_window_still_foreground(hwnd):
        error_kind = "target_window_lost"
    result = _physical_drag_result(
        input_completed,
        error=error,
        guard_decision=guard_decision,
        cursor_restored=cursor_restored,
        release_cleanup_attempted=bool(progress["button_pressed"]),
        release_cleanup_completed=bool(
            progress["release_cleanup_completed"]
        ),
        error_kind=error_kind,
    )
    if input_completed is True:
        _publish_admitted_action_summary(
            action="drag",
            input_completed=True,
            via="physical_sendinput",
        )
    return result


ADMITTED_PHYSICAL_DRAG_BACKEND = AdmittedPhysicalDragBackend(
    execute_admitted_physical_drag
)


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
    _raw_focus_window(hwnd)
    time.sleep(0.05)
    saved = None

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
            "error": "drag stopped because target-window ownership was lost",
        })
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
    _raw_focus_window(hwnd)
    time.sleep(0.05)
    saved = None
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
                "error": "drawing stopped because target-window ownership was lost",
            })
    return result


def _publish_action_fact(action: str, **payload: Any) -> None:
    """Telemetry cannot turn one completed input into a fallback replay."""
    try:
        BUS.publish("action", action=action, **payload)
    except Exception:
        logger.debug("%s action telemetry failed", action, exc_info=True)


def _publish_admitted_action_summary(
    *,
    action: str,
    input_completed: bool,
    via: str,
    completed_codepoints: int | None = None,
    key_count: int | None = None,
) -> None:
    """Publish a fixed, replay-ineligible summary of one admitted input."""

    if action not in {"click", "scroll", "drag", "type_text", "hotkey"}:
        raise ValueError("unknown admitted action summary")
    if input_completed is not True:
        raise ValueError("admitted action summary requires completed input")
    if via != "physical_sendinput":
        raise ValueError("admitted action summary via is not public")
    if completed_codepoints is not None and (
        type(completed_codepoints) is not int or completed_codepoints < 0
    ):
        raise ValueError("completed_codepoints must be a non-negative integer")
    if key_count is not None and (
        type(key_count) is not int or key_count < 0
    ):
        raise ValueError("key_count must be a non-negative integer")
    if action == "type_text":
        if completed_codepoints is None or key_count is not None:
            raise ValueError("type_text summary requires only completed_codepoints")
    elif action == "hotkey":
        if key_count is None or completed_codepoints is not None:
            raise ValueError("hotkey summary requires only key_count")
    elif completed_codepoints is not None or key_count is not None:
        raise ValueError(f"{action} summary cannot contain parameter statistics")

    try:
        if action == "type_text":
            BUS.publish(
                "action",
                action=action,
                input_completed=input_completed,
                via=via,
                completed_codepoints=completed_codepoints,
                parameter_visibility="private_runtime_evidence",
            )
        elif action == "hotkey":
            BUS.publish(
                "action",
                action=action,
                input_completed=input_completed,
                via=via,
                key_count=key_count,
                parameter_visibility="private_runtime_evidence",
            )
        else:
            BUS.publish(
                "action",
                action=action,
                input_completed=input_completed,
                via=via,
                parameter_visibility="private_runtime_evidence",
            )
    except Exception:
        logger.debug(
            "%s admitted action telemetry failed",
            action,
            exc_info=True,
        )


def _physical_scroll_result(
    input_completed: bool | None,
    *,
    error: str = "",
    via: str = "physical_sendinput",
    cursor_restored: bool | None = None,
) -> RawBackendResult:
    facts: dict[str, Any] = {"ok": input_completed is True, "via": via}
    if error:
        facts.update({"blocked": True, "error": error})
    if cursor_restored is not None:
        facts["cursor_restored"] = cursor_restored
    return RawBackendResult(input_completed, facts)


def execute_admitted_physical_scroll(
    x: int,
    y: int,
    amount: int,
    hwnd: int | None = None,
    *,
    final_guard: FinalPointerGuard,
) -> RawBackendResult:
    """Send one already-queued physical wheel input after its final guard.

    ``amount`` keeps WinPilot's canonical wheel convention: negative scrolls
    down and positive scrolls up.
    """

    if (
        any(type(value) is not int for value in (x, y, amount))
        or amount == 0
        or not callable(final_guard)
    ):
        return _physical_scroll_result(False, error="invalid admitted physical scroll")
    if _dry_run():
        return RawBackendResult(False, {"ok": True, "dry_run": True})

    try:
        saved = tuple(map(int, _cursor_pos()))
    except Exception:
        return _physical_scroll_result(False, error="scroll cursor position unavailable")

    input_completed: bool | None = False
    error = ""
    via = "physical_sendinput"
    expected = (int(x), int(y))
    try:
        try:
            _raw_move_mouse(*expected)
            time.sleep(0.04)
        except Exception:
            error = "scroll pointer positioning failed"
        if not error:
            try:
                cursor_before = tuple(map(int, _cursor_pos()))
                guard_decision = final_guard(*cursor_before)
                cursor_after = tuple(map(int, _cursor_pos()))
            except Exception:
                guard_decision = FinalPointerGuardDecision.TARGET_UNAVAILABLE
                cursor_before = cursor_after = ()
            if not isinstance(guard_decision, FinalPointerGuardDecision):
                guard_decision = FinalPointerGuardDecision.TARGET_UNAVAILABLE
            if (
                guard_decision is FinalPointerGuardDecision.ADMITTED
                and (cursor_before != expected or cursor_after != cursor_before)
            ):
                guard_decision = FinalPointerGuardDecision.POINTER_POSITION_CHANGED
            if guard_decision is not FinalPointerGuardDecision.ADMITTED:
                via = "final_target_guard"
                error = f"final pointer guard rejected: {guard_decision.value}"
        if not error:
            try:
                _send_inputs([
                    _mouse_input(
                        win32con.MOUSEEVENTF_WHEEL,
                        data=amount * 120 & 0xFFFFFFFF,
                    )
                ])
            except Exception:
                input_completed = None
                error = "scroll wheel outcome unknown"
            else:
                input_completed = True
    finally:
        cursor_restored = _restore_cursor_after_agent_input(saved, expected, hwnd)

    result = _physical_scroll_result(
        input_completed,
        error=error,
        via=via,
        cursor_restored=cursor_restored,
    )
    if input_completed is True:
        _publish_admitted_action_summary(
            action="scroll",
            input_completed=True,
            via="physical_sendinput",
        )
    return result


ADMITTED_PHYSICAL_SCROLL_BACKEND = AdmittedPhysicalScrollBackend(
    execute_admitted_physical_scroll
)


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
def _keyboard_guard_decision(
    final_guard: FinalKeyboardGuard,
) -> FinalKeyboardGuardDecision:
    try:
        decision = final_guard()
    except Exception:
        return FinalKeyboardGuardDecision.TARGET_UNAVAILABLE
    return (
        decision
        if isinstance(decision, FinalKeyboardGuardDecision)
        else FinalKeyboardGuardDecision.TARGET_UNAVAILABLE
    )


def _keyboard_result(
    input_completed: bool | None,
    *,
    requested_codepoints: int | None = None,
    completed_codepoints: int | None = None,
    key_count: int | None = None,
    release_cleanup_attempted: bool = False,
    release_cleanup_completed: bool = True,
    error: str = "",
    guard_decision: FinalKeyboardGuardDecision | None = None,
) -> RawBackendResult:
    facts: dict[str, Any] = {
        "ok": input_completed is True,
        "via": "physical_sendinput",
        "release_cleanup_attempted": release_cleanup_attempted,
        "release_cleanup_completed": release_cleanup_completed,
    }
    if requested_codepoints is not None:
        facts["requested_codepoints"] = requested_codepoints
    if completed_codepoints is not None:
        facts["completed_codepoints"] = completed_codepoints
    if key_count is not None:
        facts["key_count"] = key_count
    if error:
        facts["error"] = error
    if guard_decision is not None:
        facts["guard_decision"] = guard_decision.value
    return RawBackendResult(input_completed, facts)


def _unicode_units(char: str) -> tuple[int, ...]:
    code = ord(char)
    if code <= 0xFFFF:
        return (code,)
    code -= 0x10000
    return (0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF))


def _vk_character_inputs(char: str) -> tuple[list[_Input], list[_Input]] | None:
    result = user32.VkKeyScanW(ctypes.c_wchar(char))
    if result == -1:
        return None
    vk = result & 0xFF
    state = (result >> 8) & 0xFF
    modifiers: list[int] = []
    if state & 1:
        modifiers.append(_VK_SHIFT)
    if state & 2:
        modifiers.append(_VK_CTRL)
    if state & 4:
        modifiers.append(_VK_ALT)
    flags = _KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED_VKS else 0
    downs = [_key_input(vk=modifier) for modifier in modifiers]
    downs.append(_key_input(vk=vk, flags=flags))
    ups = [_key_input(vk=vk, flags=flags | _KEYEVENTF_KEYUP)]
    ups.extend(
        _key_input(vk=modifier, flags=_KEYEVENTF_KEYUP)
        for modifier in reversed(modifiers)
    )
    return downs, ups


def _best_effort_key_releases(ups: list[_Input]) -> tuple[bool, bool]:
    attempted = bool(ups)
    completed = True
    for key_up in ups:
        try:
            _send_inputs([key_up])
        except Exception:
            completed = False
    return attempted, completed


def _send_text_codepoint(char: str, *, via_vk: bool) -> tuple[bool | None, bool, bool]:
    if char == "\n":
        vk = _VK["enter"]
        batches = [(
            [_key_input(vk=vk)],
            [_key_input(vk=vk, flags=_KEYEVENTF_KEYUP)],
            True,
        )]
    elif via_vk:
        inputs = _vk_character_inputs(char)
        if inputs is None:
            return False, False, True
        downs, ups = inputs
        batches = [(downs, ups, False)]
    else:
        batches = [(
            [_key_input(scan=unit, flags=_KEYEVENTF_UNICODE)],
            [_key_input(
                scan=unit,
                flags=_KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP,
            )],
            False,
        ) for unit in _unicode_units(char)]

    for downs, ups, split_release in batches:
        try:
            _send_inputs(downs if split_release else [*downs, *ups])
            if split_release:
                _send_inputs(ups)
        except Exception:
            cleanup_attempted, cleanup_completed = _best_effort_key_releases(ups)
            return None, cleanup_attempted, cleanup_completed
    return True, False, True


def _legacy_physical_type_text(
    text: str,
    hwnd: int | None,
    interval_s: float,
) -> RawBackendResult:
    """Preserve master's focus, newline-chord, and pacing behavior."""

    requested = len(text)
    typed = 0

    def result(
        input_completed: bool | None,
        *,
        error: str = "",
        via: str = "",
    ) -> RawBackendResult:
        facts: dict[str, Any] = {
            "ok": input_completed is True,
            "completed_codepoints": typed,
            "requested_codepoints": requested,
        }
        if error:
            facts.update({"blocked": True, "error": error})
        if via:
            facts["via"] = via
        return RawBackendResult(input_completed, facts)

    def refocus_if_needed(index: int) -> bool:
        if hwnd and index > 0 and index % 8 == 0:
            if win32gui.GetForegroundWindow() != hwnd:
                _raw_focus_window(hwnd)
                time.sleep(0.08)
        return True

    via_vk = _should_type_via_vk(text)
    for char in text:
        if not refocus_if_needed(typed):
            return result(
                False if typed == 0 else None,
                error=(
                    "foreground changed while typing; stopped instead of "
                    "stealing keyboard focus"
                ),
                via="vk" if via_vk else "",
            )
        if via_vk:
            if not _press_char_vk(char):
                return result(
                    False if typed == 0 else None,
                    error="character input unavailable",
                    via="vk",
                )
            typed += 1
            time.sleep(max(interval_s, 0.03))
            continue
        if char == "\n":
            key_result = _raw_press_keys_for_target(["enter"], hwnd)
            if key_result.get("ok") is not True:
                completed = key_result.get("input_completed")
                if (
                    completed is not True
                    and completed is not False
                    and completed is not None
                ):
                    completed = False if typed == 0 else None
                return result(
                    completed,
                    error=str(
                        key_result.get("error")
                        or "newline key chord was not completed"
                    ),
                )
            typed += 1
            continue
        for unit in _unicode_units(char):
            _send_inputs([
                _key_input(scan=unit, flags=_KEYEVENTF_UNICODE),
                _key_input(
                    scan=unit,
                    flags=_KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP,
                ),
            ])
        typed += 1
        time.sleep(interval_s)
    return result(True, via="vk" if via_vk else "")


def _execute_physical_type_text(
    text: str,
    hwnd: int | None = None,
    *,
    final_guard: FinalKeyboardGuard,
    interval_s: float = 0.008,
    publish_action_fact: bool = True,
    legacy_semantics: bool = False,
) -> RawBackendResult:
    """Type one bounded string without queueing; strict mode never refocuses."""

    if (
        type(text) is not str
        or type(interval_s) not in {int, float}
        or isinstance(interval_s, bool)
        or not math.isfinite(float(interval_s))
        or not 0 <= float(interval_s) <= 1
        or not callable(final_guard)
        or type(publish_action_fact) is not bool
        or type(legacy_semantics) is not bool
    ):
        return _keyboard_result(
            False,
            requested_codepoints=len(text) if type(text) is str else 0,
            completed_codepoints=0,
            error="invalid admitted physical type text",
        )
    if _dry_run():
        return RawBackendResult(False, {"ok": True, "dry_run": True})
    if legacy_semantics:
        return _legacy_physical_type_text(text, hwnd, float(interval_s))

    requested = len(text)
    completed = 0
    layout_switch: tuple[int | None, bool] | None = None
    via_vk = _should_strict_type_via_vk(text)
    if via_vk and not legacy_semantics:
        layout_switch, keyboard_safe = _english_layout_switch(hwnd)
        if not keyboard_safe:
            # The target thread is unavailable, so VK transitions cannot be
            # made safe against the active IME; use the UNICODE path, which
            # bypasses IME composition entirely.
            via_vk = False
    try:
        for char in text:
            decision = _keyboard_guard_decision(final_guard)
            if decision is not FinalKeyboardGuardDecision.ADMITTED:
                return _keyboard_result(
                    False if completed == 0 else None,
                    requested_codepoints=requested,
                    completed_codepoints=completed,
                    error="final keyboard guard rejected before text input",
                    guard_decision=decision,
                )
            sent, cleanup_attempted, cleanup_completed = _send_text_codepoint(
                char, via_vk=via_vk,
            )
            if sent is not True:
                return _keyboard_result(
                    None if sent is None or completed else False,
                    requested_codepoints=requested,
                    completed_codepoints=completed,
                    release_cleanup_attempted=cleanup_attempted,
                    release_cleanup_completed=cleanup_completed,
                    error=(
                        "text input outcome unknown"
                        if sent is None
                        else "text input unavailable"
                    ),
                )
            completed += 1
            decision = _keyboard_guard_decision(final_guard)
            if decision is not FinalKeyboardGuardDecision.ADMITTED:
                return _keyboard_result(
                    None,
                    requested_codepoints=requested,
                    completed_codepoints=completed,
                    error="final keyboard guard changed after text input",
                    guard_decision=decision,
                )
            time.sleep(max(float(interval_s), 0.03) if via_vk else float(interval_s))
    finally:
        if layout_switch is not None and layout_switch[0] is not None:
            _restore_layout_switch(hwnd, layout_switch)

    result = _keyboard_result(
        True,
        requested_codepoints=requested,
        completed_codepoints=completed,
    )
    if publish_action_fact:
        _publish_admitted_action_summary(
            action="type_text",
            input_completed=True,
            via="physical_sendinput",
            completed_codepoints=completed,
        )
    return result


def execute_admitted_physical_type_text(
    text: str,
    hwnd: int | None = None,
    *,
    final_guard: FinalKeyboardGuard,
    interval_s: float = 0.008,
) -> RawBackendResult:
    """Strict vNext boundary around the shared physical text mechanism."""

    if type(text) is not str or not 1 <= len(text) <= MAX_TYPE_TEXT_CODEPOINTS:
        return _keyboard_result(
            False,
            requested_codepoints=len(text) if type(text) is str else 0,
            completed_codepoints=0,
            error="invalid admitted physical type text",
        )
    return _execute_physical_type_text(
        text,
        hwnd=hwnd,
        final_guard=final_guard,
        interval_s=interval_s,
        publish_action_fact=True,
    )


ADMITTED_PHYSICAL_TYPE_TEXT_BACKEND = AdmittedPhysicalTypeTextBackend(
    execute_admitted_physical_type_text
)


def _execute_physical_hotkey_vks(
    vks: tuple[int, ...],
    *,
    final_guard: FinalKeyboardGuard,
    release_individually: bool = True,
) -> RawBackendResult:
    decision = _keyboard_guard_decision(final_guard)
    if decision is not FinalKeyboardGuardDecision.ADMITTED:
        return _keyboard_result(
            False,
            key_count=len(vks),
            error="final keyboard guard rejected before hotkey",
            guard_decision=decision,
        )
    downs, ups = [], []
    for vk in vks:
        flags = _KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED_VKS else 0
        downs.append(_key_input(vk=vk, flags=flags))
        ups.append(_key_input(vk=vk, flags=flags | _KEYEVENTF_KEYUP))

    down_attempted = False
    down_completed = False
    release_attempted = False
    release_completed = True
    release_had_error = False
    guard_after = FinalKeyboardGuardDecision.ADMITTED
    try:
        down_attempted = True
        _send_inputs(downs)
        down_completed = True
        time.sleep(0.03)
        guard_after = _keyboard_guard_decision(final_guard)
    except Exception:
        guard_after = FinalKeyboardGuardDecision.TARGET_UNAVAILABLE
    finally:
        if down_attempted:
            ordered_ups = list(reversed(ups))
            if release_individually:
                release_attempted, release_completed = _best_effort_key_releases(
                    ordered_ups
                )
            else:
                release_attempted = bool(ordered_ups)
                try:
                    _send_inputs(ordered_ups)
                except Exception:
                    release_had_error = True
                    _attempted, release_completed = _best_effort_key_releases(
                        ordered_ups
                    )

    if (
        down_completed
        and release_completed
        and guard_after is FinalKeyboardGuardDecision.ADMITTED
    ):
        guard_after = _keyboard_guard_decision(final_guard)

    # ``input_completed`` answers whether the complete physical chord reached
    # SendInput, not whether the original window stayed foreground afterwards.
    # Focus-changing chords (Win, Alt+Tab, Enter in launchers/dialogs) are
    # successful precisely when that post-input guard changes.  Preserve the
    # guard decision as a diagnostic, but do not turn a fully sent and fully
    # released chord into an unknown physical outcome.
    completed = bool(
        down_completed
        and release_completed
        and not release_had_error
    )
    if completed:
        return _keyboard_result(
            True,
            key_count=len(vks),
            release_cleanup_attempted=release_attempted,
            release_cleanup_completed=release_completed,
            guard_decision=(
                guard_after
                if guard_after is not FinalKeyboardGuardDecision.ADMITTED
                else None
            ),
        )
    return _keyboard_result(
        None,
        key_count=len(vks),
        release_cleanup_attempted=release_attempted,
        release_cleanup_completed=release_completed,
        error="hotkey outcome unknown",
        guard_decision=guard_after,
    )


def execute_admitted_physical_hotkey(
    chord: HotkeyChord,
    hwnd: int | None = None,
    *,
    final_guard: FinalKeyboardGuard,
) -> RawBackendResult:
    """Send one canonical chord without queueing, parsing, focus, or fallback."""

    if not isinstance(chord, HotkeyChord) or not callable(final_guard):
        return _keyboard_result(False, key_count=0, error="invalid admitted hotkey")
    if _dry_run():
        return RawBackendResult(False, {"ok": True, "dry_run": True})
    try:
        vks = tuple(_vk_of(key) for key in chord.keys)
    except Exception:
        return _keyboard_result(
            False, key_count=len(chord.keys), error="invalid admitted hotkey",
        )
    result = _execute_physical_hotkey_vks(vks, final_guard=final_guard)
    if result.input_completed is True:
        _publish_admitted_action_summary(
            action="hotkey",
            input_completed=True,
            via="physical_sendinput",
            key_count=len(chord.keys),
        )
    return result


ADMITTED_PHYSICAL_HOTKEY_BACKEND = AdmittedPhysicalHotkeyBackend(
    execute_admitted_physical_hotkey
)


def _legacy_hotkey_guard(hwnd: int | None) -> FinalKeyboardGuardDecision:
    try:
        if hwnd is None or not win32gui.IsWindow(int(hwnd)):
            return FinalKeyboardGuardDecision.ADMITTED
        owned = _target_window_still_foreground(hwnd)
    except Exception:
        owned = False
    return (
        FinalKeyboardGuardDecision.ADMITTED
        if owned
        else FinalKeyboardGuardDecision.FOREGROUND_CHANGED
    )


@queued_desktop_action("type_text")
def type_text(
    text: str,
    interval_s: float = 0.008,
    hwnd: int | None = None,
) -> dict:
    """Legacy adapter around the shared unqueued physical text owner."""
    if _dry_run():
        return {"ok": True, "dry_run": True}

    result = _execute_physical_type_text(
        text,
        hwnd=hwnd,
        final_guard=lambda: _legacy_hotkey_guard(hwnd),
        interval_s=interval_s,
        publish_action_fact=False,
        legacy_semantics=True,
    )
    facts = dict(result.facts)
    typed = int(facts.get("completed_codepoints", 0) or 0)
    if result.input_completed is True:
        if _should_type_via_vk(text):
            _publish_action_fact("type_text", length=typed, via="vk")
            return {
                "ok": True,
                "typed": typed,
                "requested": len(text),
                "via": "vk",
            }
        _publish_action_fact("type_text", length=typed)
        return {"ok": True, "typed": typed}
    return {
        "ok": False,
        "verified": False,
        "blocked": True,
        "typed": typed,
        "requested": len(text) if type(text) is str else 0,
        "input_completed": result.input_completed,
        "outcome_unknown": result.input_completed is None,
        "error": str(facts.get("error") or "keyboard input was not completed"),
    }


def _raw_press_keys(keys: list[str], hwnd: int | None = None) -> dict:
    vks = tuple(_vk_of(key) for key in keys)
    result = _execute_physical_hotkey_vks(
        vks,
        final_guard=lambda: _legacy_hotkey_guard(hwnd),
        release_individually=False,
    )
    if result.input_completed is True:
        return {"ok": True, "keys": keys}
    return {
        "ok": False,
        "verified": False,
        "blocked": True,
        "keys": keys,
        "input_completed": result.input_completed,
        "outcome_unknown": result.input_completed is None,
        "error": str(result.facts.get("error") or "hotkey was not completed"),
    }


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
    result = _raw_press_keys_for_target(keys, hwnd)
    if result.get("ok") is not True:
        return result
    _publish_action_fact("hotkey", keys=keys)
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


@queued_desktop_action("clipboard_paste", scope="physical_clipboard")
def clipboard_paste(hwnd: int | None = None) -> dict:
    """Paste from clipboard via Ctrl+V."""
    if _dry_run():
        return {"ok": True, "dry_run": True}
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
            if not _target_window_still_foreground(hwnd):
                interrupted = True
                break
    finally:
        _send_inputs(list(reversed(ups)))
    if interrupted:
        return {
            "ok": False,
            "verified": False,
            "blocked": True,
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
