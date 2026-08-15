"""Action executor: SendInput-based mouse/keyboard plus window management.

All actions are physical-pixel screen coordinates. ``type_text`` uses paired
US-layout scan-code transitions for ASCII and KEYEVENTF_UNICODE for text that
cannot be represented that way, so both raw-key consumers and Chinese/emoji
input work. A UIA fast path (Invoke/SetValue) is attempted first when a live
UIA handle is present on the resolved element — faster and more reliable than
synthetic clicks.

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
from ..visual_runtime.keyboard_contracts import CANONICAL_HOTKEY_KEYS
from .desktop_queue import queued_desktop_action
from .raw_device import (
    AdmittedPhysicalClickBackend,
    AdmittedPhysicalDragBackend,
    AdmittedPhysicalHotkeyBackend,
    AdmittedPhysicalMoveBackend,
    AdmittedPhysicalSequenceBackend,
    AdmittedPhysicalScrollBackend,
    AdmittedPhysicalTypeTextBackend,
    ClickCount,
    ClickMechanism,
    FinalKeyboardGuard,
    FinalKeyboardGuardDecision,
    FinalPointerGuard,
    FinalPointerGuardDecision,
    HotkeyChord,
    InputEventKind,
    MAX_INPUT_SEQUENCE_EVENTS,
    MAX_POINTER_DURATION_MS,
    MAX_TYPE_TEXT_CODEPOINTS,
    MouseButton,
    PhysicalInputEvent,
    RawBackendResult,
    StopSignal,
    _canonical_drag_points,
)

user32 = ctypes.windll.user32
imm32 = ctypes.windll.imm32

# ToolSession installs this callback only around a visible action. Sampling
# after mouse-down makes short-lived pressed states part of action evidence.
_press_probe_local = threading.local()
_held_mouse_lock = threading.Lock()
_held_mouse_buttons: set[str] = set()
_held_mouse_key_vks: dict[str, tuple[int, ...]] = {}
_held_keyboard_vks: set[int] = set()
_held_unicode_units: set[int] = set()


def _valid_stop_signal(value: object) -> bool:
    """Accept Event and lightweight deadline/cancellation objects alike."""

    return value is None or callable(getattr(value, "is_set", None))


def _stop_requested(value: StopSignal | None) -> bool:
    if value is None:
        return False
    try:
        return bool(value.is_set())
    except Exception:
        # Cancellation is advisory. A broken observer must not impersonate a
        # physical input failure.
        return False


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
_KEYEVENTF_SCANCODE = 0x8


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


def _hardware_key_input(vk: int, *, key_up: bool = False) -> _Input:
    """Build a named-key input that also works with scan-code consumers.

    Text entry deliberately keeps its Unicode/layout-aware paths.  Named keys
    and held-key timelines use hardware scan codes because games and other raw
    input consumers commonly ignore synthesized virtual-key-only events.
    ``MapVirtualKeyW`` can legitimately return zero for unusual keys, in which
    case the ordinary VK route remains the least surprising fallback.
    """

    flags = _KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED_VKS else 0
    if key_up:
        flags |= _KEYEVENTF_KEYUP
    try:
        scan = int(user32.MapVirtualKeyW(int(vk), 0))
    except Exception:
        scan = 0
    if scan > 0:
        return _key_input(
            scan=scan & 0xFFFF,
            flags=flags | _KEYEVENTF_SCANCODE,
        )
    return _key_input(vk=vk, flags=flags)


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
    **{f"f{index}": 0x6F + index for index in range(13, 25)},
    "capslock": 0x14, "numlock": 0x90, "scrolllock": 0x91,
    "pause": 0x13, "printscreen": 0x2C, "menu": 0x5D,
    "volumeup": 0xAF, "volumedown": 0xAE, "volumemute": 0xAD,
    "browserback": 0xA6, "browserforward": 0xA7,
    **{f"numpad{index}": 0x60 + index for index in range(10)},
    "multiply": 0x6A, "add": 0x6B, "subtract": 0x6D,
    "decimal": 0x6E, "divide": 0x6F,
    "plus": 0xBB, "minus": 0xBD, "comma": 0xBC, "period": 0xBE, "slash": 0xBF,
}
_EXTENDED_VKS = {
    0x2E, 0x2D, 0x24, 0x23, 0x21, 0x22, 0x26, 0x28, 0x25, 0x27,
    0x5B, 0x5C, 0x5D, 0x6F, 0xA3, 0xA5, 0xA6, 0xA7, 0xAD, 0xAE, 0xAF,
}
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
    del hwnd
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
    del hwnd
    if not restore:
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
    for _attempt in range(2):
        try:
            call(*args)
            return True
        except Exception:
            continue
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
                error_kind="post_message_down_outcome_unknown",
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
                error_kind="post_message_up_outcome_unknown",
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
                    error_kind="post_message_inter_click_wait_failed",
                )
    return _click_progress(True, completed_clicks, via="post_message")


def _cursor_pos() -> tuple[int, int]:
    return win32api.GetCursorPos()


def _restore_cursor_after_agent_input(
    saved: tuple[int, int],
    expected_agent_pos: tuple[int, int] | None = None,
    hwnd: int | None = None,
) -> bool:
    """Leave the cursor at the endpoint selected on the submitted image.

    Snapping back to ``saved`` made the post-action desktop differ from the
    physical state the model had just created.  It also immediately destroyed
    hover, canvas and game interactions.  Keep the compatibility-shaped helper
    so older callers can report the fact, but do not perform a second move.
    """
    del saved, expected_agent_pos, hwnd
    return False


def _target_window_still_foreground(hwnd: int | None) -> bool:
    """Compatibility hook: window metadata never gates global physical input."""
    del hwnd
    return True


def _pointer_near(
    point: tuple[int, int],
    expected: tuple[int, int],
    *,
    tolerance: int = 3,
) -> bool:
    """Allow the tiny rounding error introduced by Windows absolute mapping."""

    return (
        abs(int(point[0]) - int(expected[0])) <= tolerance
        and abs(int(point[1]) - int(expected[1])) <= tolerance
    )


# ---------------------------------------------------------------- mouse API
def _raw_move_mouse(x: int, y: int) -> None:
    _abs_mouse_move(int(x), int(y))


@queued_desktop_action("move_mouse")
def move_mouse(x: int, y: int, hwnd: int | None = None) -> dict:
    _raw_move_mouse(x, y)
    return {"ok": True, "x": int(x), "y": int(y)}


_CLICK_OWNERSHIP_LOST = "physical click did not complete"


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
    held_after: bool | None = None,
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
    if held_after is not None:
        facts["held_after"] = held_after
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
    press_duration_ms: int = 35,
    stop_event: StopSignal | None = None,
) -> RawBackendResult:
    """Execute one strict click after the caller has serialized admission."""
    if not (
        type(x) is int and type(y) is int and isinstance(button, MouseButton)
        and isinstance(count, ClickCount)
        and isinstance(mechanism, ClickMechanism)
        and type(press_duration_ms) is int
        and 0 <= press_duration_ms <= 2_000
        and _valid_stop_signal(stop_event)
    ):
        return _click_progress(
            False, 0, "invalid typed click input", error_kind="invalid_args",
        )
    if _dry_run():
        return RawBackendResult(False, {"ok": True, "dry_run": True})
    if _stop_requested(stop_event):
        return _click_progress(
            False, 0, "click stopped before input", error_kind="stop_requested",
        )
    if mechanism is ClickMechanism.BACKGROUND_MESSAGE:
        return _post_click(x, y, button, count, expected_hwnd=hwnd)
    raw_args = (
        x, y, button, count, hwnd,
        mechanism is ClickMechanism.PREPOSITIONED_PHYSICAL, False,
    )
    return _raw_click(
        *raw_args,
        enforce_foreground=enforce_foreground,
        final_guard=final_guard,
        press_duration_ms=press_duration_ms,
        stop_event=stop_event,
    )


def execute_admitted_physical_click(
    x: int,
    y: int,
    button: MouseButton,
    count: ClickCount,
    mechanism: ClickMechanism,
    hwnd: int | None = None,
    *,
    final_guard: FinalPointerGuard | None = None,
    enforce_foreground: bool = True,
    press_duration_ms: int = 35,
    stop_event: StopSignal | None = None,
) -> RawBackendResult:
    """Run an already-queued physical click at the model-selected pixel."""
    if (
        not _valid_stop_signal(stop_event)
        or mechanism is ClickMechanism.BACKGROUND_MESSAGE
    ):
        return _click_progress(
            False, 0, "invalid admitted physical click", error_kind="invalid_args",
        )
    return _execute_click_input_unqueued(
        x, y, button, count, mechanism, hwnd=hwnd, final_guard=final_guard,
        enforce_foreground=enforce_foreground,
        press_duration_ms=press_duration_ms,
        stop_event=stop_event,
    )


ADMITTED_PHYSICAL_CLICK_BACKEND = AdmittedPhysicalClickBackend(
    execute_admitted_physical_click
)


def _physical_move_result(
    input_completed: bool | None,
    *,
    error: str = "",
    error_kind: str = "",
    guard_decision: FinalPointerGuardDecision | None = None,
    moves_completed: int = 0,
) -> RawBackendResult:
    facts: dict[str, Any] = {
        "ok": input_completed is True,
        "via": (
            "physical_sendinput"
            if guard_decision in {None, FinalPointerGuardDecision.ADMITTED}
            else "final_target_guard"
        ),
        "cursor_restored": False,
        "moves_completed": int(moves_completed),
    }
    if error:
        facts.update(blocked=True, error=error)
    if error_kind:
        facts["error_kind"] = error_kind
    if guard_decision is not None:
        facts["guard_decision"] = guard_decision.value
    return RawBackendResult(input_completed, facts)


def execute_admitted_physical_move(
    x: int,
    y: int,
    *,
    duration_ms: int = 0,
    hwnd: int | None = None,
    final_guard: FinalPointerGuard | None = None,
    stop_event: StopSignal | None = None,
) -> RawBackendResult:
    """Move the real pointer and leave it at the model-selected endpoint."""

    del final_guard

    if (
        any(type(value) is not int for value in (x, y, duration_ms))
        or not 0 <= duration_ms <= MAX_POINTER_DURATION_MS
        or not _valid_stop_signal(stop_event)
    ):
        return _physical_move_result(
            False,
            error="invalid admitted physical move",
            error_kind="invalid_args",
        )
    if _dry_run():
        return RawBackendResult(False, {"ok": True, "dry_run": True})
    if _stop_requested(stop_event):
        return _physical_move_result(
            False,
            error="pointer move stopped before input",
            error_kind="stop_requested",
        )
    try:
        start = tuple(map(int, _cursor_pos()))
    except Exception:
        # An absolute endpoint is still fully actionable without sampling the
        # old cursor. Duration degrades to one reliable move instead of a
        # rejected model action.
        start = (x, y)
    progress: dict[str, int | bool] = {"moves_completed": 0}
    try:
        if start == (x, y):
            _abs_mouse_move(x, y)
            progress["moves_completed"] = 1
            completed = True
        else:
            completed = _move_pointer_path(
                start,
                (x, y),
                duration_ms / 1000.0,
                hwnd=hwnd,
                progress=progress,
                stop_event=stop_event,
            )
    except Exception:
        moves_completed = int(progress["moves_completed"])
        return _physical_move_result(
            None if moves_completed else False,
            error=(
                "pointer move outcome unknown after physical input began"
                if moves_completed
                else "pointer move input did not start"
            ),
            error_kind=(
                "physical_move_outcome_unknown"
                if moves_completed
                else "physical_move_not_started"
            ),
            moves_completed=moves_completed,
        )
    moves_completed = int(progress["moves_completed"])
    if not completed:
        return _physical_move_result(
            None if moves_completed else False,
            error=(
                "pointer move interrupted after physical input began"
                if moves_completed
                else "pointer move did not start"
            ),
            error_kind=(
                "pointer_move_interrupted"
                if moves_completed
                else "pointer_move_not_started"
            ),
            moves_completed=moves_completed,
        )
    # Window ownership is not sampled here. The endpoint is the one chosen by
    # the model in the submitted screen and absolute SendInput is authoritative.
    _publish_admitted_action_summary(
        action="move_mouse",
        input_completed=True,
        via="physical_sendinput",
    )
    return _physical_move_result(
        True,
        moves_completed=moves_completed,
    )


ADMITTED_PHYSICAL_MOVE_BACKEND = AdmittedPhysicalMoveBackend(
    execute_admitted_physical_move
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
    allow_target_focus: bool = False,
    enforce_foreground: bool = False,
    final_guard: FinalPointerGuard | None = None,
    press_duration_ms: int = 35,
    stop_event: StopSignal | None = None,
) -> RawBackendResult:
    # ``hwnd``, ``allow_target_focus`` and ``enforce_foreground`` are retained
    # only for wire compatibility.  A click goes to the model-selected screen
    # point; it never raises or focuses a locally preferred window first.
    del hwnd, prepositioned, allow_target_focus, enforce_foreground, final_guard
    down, up, button_data = _mouse_button_flags(button)
    completed_clicks = 0
    for index in range(count.value):
        if _stop_requested(stop_event):
            return _click_progress(
                False if completed_clicks == 0 else None,
                completed_clicks,
                "click stopped before input",
                error_kind="stop_requested",
            )
        if not _record_button_owned(button):
            return _click_progress(
                False if completed_clicks == 0 else None,
                completed_clicks,
                f"{button.value} mouse button is already held",
                error_kind="mouse_button_already_held",
                held_after=True,
            )
        try:
            # Keep positioning and DOWN in one SendInput batch. There is no
            # observable gap in which a foreground/window heuristic can move
            # the pointer away from the pixel selected in the real frame.
            _send_inputs([
                _absolute_mouse_input(x, y),
                _mouse_button_input(down, button_data),
            ])
        except Exception:
            attempted, released, held_after = _best_effort_release_button(
                button,
                force=True,
            )
            return _click_progress(
                None, completed_clicks, "click down outcome unknown",
                error_kind=(
                    "release_cleanup_incomplete"
                    if attempted and not released
                    else "mouse_down_outcome_unknown"
                ),
                cleanup=released if attempted else False,
                held_after=held_after,
            )
        try:
            if press_duration_ms:
                time.sleep(press_duration_ms / 1000.0)
            sampled = _sample_pressed_state()
        except Exception:
            sampled = False
        attempted, released, held_after = _best_effort_release_button(
            button,
            force=True,
        )
        if not released:
            return _click_progress(
                None, completed_clicks, "click up outcome unknown",
                error_kind="release_cleanup_incomplete",
                cleanup=False if attempted else None,
                held_after=held_after,
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
                    error_kind="inter_click_wait_failed",
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
    _send_inputs([_absolute_mouse_input(x, y)])


def _absolute_mouse_input(x: int, y: int) -> _Input:
    """Build one virtual-desktop absolute move matching screenshot pixels."""

    vx, vy, vw, vh = _virtual_screen_geometry()
    x = max(vx, min(vx + vw - 1, int(x)))
    y = max(vy, min(vy + vh - 1, int(y)))
    nx = max(0, min(65535, round((int(x) - vx) * 65535 / max(1, vw - 1))))
    ny = max(0, min(65535, round((int(y) - vy) * 65535 / max(1, vh - 1))))
    flags = (
        win32con.MOUSEEVENTF_MOVE
        | win32con.MOUSEEVENTF_ABSOLUTE
        | 0x2000  # MOUSEEVENTF_MOVE_NOCOALESCE
        | 0x4000  # MOUSEEVENTF_VIRTUALDESK
    )
    return _mouse_input(flags, dx=nx, dy=ny)


def _virtual_screen_geometry() -> tuple[int, int, int, int]:
    return (
        int(user32.GetSystemMetrics(76)),  # SM_XVIRTUALSCREEN
        int(user32.GetSystemMetrics(77)),  # SM_YVIRTUALSCREEN
        max(1, int(user32.GetSystemMetrics(78))),  # SM_CXVIRTUALSCREEN
        max(1, int(user32.GetSystemMetrics(79))),  # SM_CYVIRTUALSCREEN
    )


def _saturate_virtual_point(x: int, y: int) -> tuple[int, int]:
    vx, vy, vw, vh = _virtual_screen_geometry()
    return (
        max(vx, min(vx + vw - 1, int(x))),
        max(vy, min(vy + vh - 1, int(y))),
    )


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


def _move_pointer_relative(
    dx: int,
    dy: int,
    duration_s: float,
    *,
    progress: dict[str, int | bool] | None = None,
    stop_event: StopSignal | None = None,
) -> bool:
    """Emit a smooth relative mouse path for raw-input/game consumers."""

    _vx, _vy, vw, vh = _virtual_screen_geometry()
    dx = max(-vw, min(vw, int(dx)))
    dy = max(-vh, min(vh, int(dy)))
    distance = math.hypot(dx, dy)
    steps = max(1, int(math.ceil(distance / 8.0)))
    previous_x = previous_y = 0
    for index in range(1, steps + 1):
        if _stop_requested(stop_event):
            return False
        target_x = int(round(dx * index / steps))
        target_y = int(round(dy * index / steps))
        step_x = target_x - previous_x
        step_y = target_y - previous_y
        previous_x, previous_y = target_x, target_y
        if step_x or step_y:
            _send_inputs([
                _mouse_input(
                    win32con.MOUSEEVENTF_MOVE | 0x2000,
                    dx=step_x,
                    dy=step_y,
                )
            ])
            if progress is not None:
                progress["moves_completed"] = (
                    int(progress.get("moves_completed", 0)) + 1
                )
        if duration_s:
            time.sleep(max(0.001, duration_s / steps))
    return True


def _mouse_button_flags(button: MouseButton) -> tuple[int, int, int]:
    if button is MouseButton.LEFT:
        return (
            win32con.MOUSEEVENTF_LEFTDOWN,
            win32con.MOUSEEVENTF_LEFTUP,
            0,
        )
    if button is MouseButton.RIGHT:
        return (
            win32con.MOUSEEVENTF_RIGHTDOWN,
            win32con.MOUSEEVENTF_RIGHTUP,
            0,
        )
    if button is MouseButton.MIDDLE:
        return (
            win32con.MOUSEEVENTF_MIDDLEDOWN,
            win32con.MOUSEEVENTF_MIDDLEUP,
            0,
        )
    if button is MouseButton.X1:
        return (
            win32con.MOUSEEVENTF_XDOWN,
            win32con.MOUSEEVENTF_XUP,
            win32con.XBUTTON1,
        )
    if button is MouseButton.X2:
        return (
            win32con.MOUSEEVENTF_XDOWN,
            win32con.MOUSEEVENTF_XUP,
            win32con.XBUTTON2,
        )
    raise TypeError("button must be MouseButton")


def _mouse_button_input(flags: int, data: int) -> _Input:
    # Keep the ordinary-button call shape compatible with existing mechanical
    # probes; X buttons are the only ones that require mouseData.
    return _mouse_input(flags) if data == 0 else _mouse_input(flags, data=data)


def _move_pointer_path(
    start: tuple[int, int],
    end: tuple[int, int],
    duration_s: float,
    *,
    hwnd: int | None = None,
    progress: dict[str, int | bool] | None = None,
    stop_event: StopSignal | None = None,
) -> bool:
    """Emit a real absolute path without changing any button state."""
    del hwnd
    start = _saturate_virtual_point(*start)
    end = _saturate_virtual_point(*end)
    distance = math.hypot(end[0] - start[0], end[1] - start[1])
    steps = max(1, int(math.ceil(distance / 8.0)))
    for index in range(1, steps + 1):
        if _stop_requested(stop_event):
            return False
        point = (
            int(round(start[0] + (end[0] - start[0]) * index / steps)),
            int(round(start[1] + (end[1] - start[1]) * index / steps)),
        )
        _emit_move(*point)
        if progress is not None:
            progress["moves_completed"] = (
                int(progress.get("moves_completed", 0)) + 1
            )
        if duration_s:
            time.sleep(max(0.001, duration_s / steps))
    return True


def _move_path_while_pressed(
    waypoints: list[tuple[int, int]],
    duration_s: float,
    *,
    hwnd: int | None = None,
    progress: dict[str, int | bool] | None = None,
    stop_event: StopSignal | None = None,
) -> bool:
    """Move an already-held pointer without changing button state."""
    del hwnd
    waypoints = [_saturate_virtual_point(*point) for point in waypoints]
    seg_lens = [((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
                for a, b in zip(waypoints, waypoints[1:])]
    total = max(sum(seg_lens), 1.0)
    for (a, b), seg in zip(zip(waypoints, waypoints[1:]), seg_lens):
        # Keep at most ~8px between events without resampling short paths.
        steps = max(1, int(math.ceil(seg / 8.0)))
        seg_dur = duration_s * (seg / total)
        for i in range(1, steps + 1):
            if _stop_requested(stop_event):
                return False
            expected_cursor = (
                round(a[0] + (b[0] - a[0]) * i / steps),
                round(a[1] + (b[1] - a[1]) * i / steps),
            )
            _emit_move(*expected_cursor)
            if progress is not None:
                progress["moves_completed"] = (
                    int(progress.get("moves_completed", 0)) + 1
                )
            time.sleep(max(0.002, seg_dur / steps))
    time.sleep(0.015)
    return True


def _stroke_path(
    waypoints: list[tuple[int, int]],
    duration_s: float,
    *,
    hwnd: int | None = None,
    before_press: Callable[[], bool] | None = None,
    progress: dict[str, int | bool] | None = None,
    button: MouseButton = MouseButton.LEFT,
    held_keys: tuple[str, ...] = (),
    stop_event: StopSignal | None = None,
) -> bool:
    """Press at the first waypoint, move through the path, release at the end.
    Shared by drag() and draw_strokes() so stroke registration is identical."""
    if progress is not None:
        progress.update(
            button_pressed=False,
            moves_completed=0,
            release_cleanup_attempted=False,
            release_cleanup_completed=False,
        )
    if _stop_requested(stop_event):
        return False
    if before_press is not None:
        try:
            if not before_press():
                return False
        except Exception:
            return False
    held_key_vks = tuple(_vk_of(key) for key in held_keys)
    pressed = False
    down_flag, up_flag, _data = _mouse_button_flags(button)
    if not _record_button_owned(button, held_key_vks=held_key_vks):
        return False
    # Record ownership before SendInput.  If Windows accepts an input and the
    # API reports an error, cleanup still has enough state to release it.
    pressed = True
    if progress is not None:
        progress["button_pressed"] = True
    moved = False
    released = False
    try:
        # One batch ties the model's start pixel to DOWN. This is the critical
        # boundary for drawing, selection, sliders and game gestures.
        _send_inputs([
            *(_hardware_key_input(vk) for vk in held_key_vks),
            _absolute_mouse_input(*waypoints[0]),
            _mouse_button_input(down_flag, _data),
        ])
        time.sleep(0.02)  # let the app register the press before moving
        move_kwargs: dict[str, Any] = {
            "hwnd": hwnd,
            "progress": progress,
        }
        if stop_event is not None:
            move_kwargs["stop_event"] = stop_event
        moved = _move_path_while_pressed(
            waypoints,
            duration_s,
            **move_kwargs,
        )
    finally:
        if pressed:
            attempted, released, _held_after = _best_effort_release_button(
                button,
                force=True,
            )
            if progress is not None:
                progress["release_cleanup_attempted"] = attempted
                progress["release_cleanup_completed"] = released
    return moved and released


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


def _button_owned_by_agent(button: MouseButton) -> bool:
    with _held_mouse_lock:
        return button.value in _held_mouse_buttons


def _record_button_owned(
    button: MouseButton,
    *,
    held_key_vks: tuple[int, ...] = (),
) -> bool:
    with _held_mouse_lock:
        if button.value in _held_mouse_buttons:
            return False
        _held_mouse_buttons.add(button.value)
        if held_key_vks:
            _held_mouse_key_vks[button.value] = held_key_vks
        return True


def _best_effort_release_button(
    button: MouseButton,
    *,
    force: bool = False,
) -> tuple[bool, bool, bool | None]:
    """Return (attempted, completed, held_after) after bounded UP retries."""

    with _held_mouse_lock:
        owned_before = button.value in _held_mouse_buttons
        held_key_vks = _held_mouse_key_vks.get(button.value, ())
    if not force and not owned_before and not held_key_vks:
        return False, False, False
    _down, up, data = _mouse_button_flags(button)
    mouse_released = _try_release(
        _send_inputs,
        [_mouse_button_input(up, data)],
    )
    _keys_attempted, keys_released = _best_effort_key_releases([
        _hardware_key_input(vk, key_up=True)
        for vk in reversed(held_key_vks)
    ])
    with _held_mouse_lock:
        if mouse_released:
            _held_mouse_buttons.discard(button.value)
        if keys_released:
            _held_mouse_key_vks.pop(button.value, None)
    # Mouse UP deliberately precedes reverse-order key UP.  If either part is
    # uncertain, retain only the ownership that may still exist so a later
    # idempotent cleanup can try again.
    completed = mouse_released and keys_released
    return True, completed, not mouse_released if owned_before else None


def _held_drag_failure(
    phase: str,
    *,
    error: str,
    guard_decision: FinalPointerGuardDecision | None,
    held_before: bool,
    moves_completed: int,
    button: MouseButton = MouseButton.LEFT,
    error_kind: str | None = None,
) -> RawBackendResult:
    attempted, released, held_after = _best_effort_release_button(
        button,
        force=True,
    )
    if attempted and not released:
        error_kind = "release_cleanup_incomplete"
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
    final_guard: FinalPointerGuard | None,
    phase: str,
    button: MouseButton = MouseButton.LEFT,
    held_keys: tuple[str, ...] = (),
    duration_s: float = 0.4,
    stop_event: StopSignal | None = None,
) -> RawBackendResult:
    del final_guard
    start = path[0]
    if phase != "begin" and len(path) > 1:
        try:
            start = tuple(map(int, _cursor_pos()))
        except Exception:
            # The caller's remembered point remains a usable fallback, but a
            # successful live cursor sample wins after intervening mouse input.
            pass
        path = [start, *path[1:]]
    held_before = _button_owned_by_agent(button)
    progress: dict[str, int | bool] = {"moves_completed": 0}
    guard_decision = None

    if phase == "begin":
        if held_before:
            return _held_drag_failure(
                phase,
                error=f"{button.value} button already held before drag begin",
                guard_decision=None,
                held_before=True,
                moves_completed=0,
                button=button,
            )
    elif not held_before:
        return _held_drag_failure(
            phase,
            error=f"held drag lost {button.value}-button ownership",
            guard_decision=None,
            held_before=False,
            moves_completed=0,
            button=button,
        )

    if phase != "begin":
        with _held_mouse_lock:
            held_key_vks = _held_mouse_key_vks.get(button.value, ())
        if held_key_vks:
            try:
                _send_inputs([
                    _hardware_key_input(vk) for vk in held_key_vks
                ])
            except Exception:
                return _held_drag_failure(
                    phase,
                    error="held drag modifier outcome unknown",
                    guard_decision=None,
                    held_before=True,
                    moves_completed=0,
                    button=button,
                )

    if phase == "begin":
        if _stop_requested(stop_event):
            return _physical_drag_result(
                False,
                error="held drag stopped before input",
                error_kind="stop_requested",
                segment_phase=phase,
                held_before=False,
                held_after=False,
                moves_completed=0,
            )
        held_key_vks = tuple(_vk_of(key) for key in held_keys)
        if not _record_button_owned(button, held_key_vks=held_key_vks):
            return _held_drag_failure(
                phase,
                error=(
                    f"{button.value} button ownership changed before drag begin"
                ),
                guard_decision=guard_decision,
                held_before=False,
                moves_completed=0,
                button=button,
            )
        try:
            down, _up, data = _mouse_button_flags(button)
            _send_inputs([
                *(_hardware_key_input(vk) for vk in held_key_vks),
                _absolute_mouse_input(*start),
                _mouse_button_input(down, data),
            ])
            time.sleep(0.02)
        except Exception:
            return _held_drag_failure(
                phase,
                error="held drag DOWN outcome unknown",
                guard_decision=guard_decision,
                held_before=False,
                moves_completed=0,
                button=button,
            )

    try:
        move_kwargs: dict[str, Any] = {
            "hwnd": hwnd,
            "progress": progress,
        }
        if stop_event is not None:
            move_kwargs["stop_event"] = stop_event
        completed = _move_path_while_pressed(
            path,
            duration_s,
            **move_kwargs,
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
            button=button,
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

    attempted, released, held_after = _best_effort_release_button(
        button,
        force=True,
    )
    if not released:
        # The required FINISH UP was uncertain; make one additional best-effort
        # UP inside the common failure path, without replaying any movement.
        return _held_drag_failure(
            phase,
            error="held drag final UP outcome unknown",
            guard_decision=guard_decision,
            held_before=held_before,
            moves_completed=moves_completed,
            button=button,
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
    button: MouseButton = MouseButton.LEFT,
    held_keys: tuple[str, ...] = (),
    duration_ms: int = 400,
    final_guard: FinalPointerGuard | None = None,
    pointer_already_held: bool = False,
    keep_holding: bool = False,
    hold_token: str | None = None,
    cleanup_only: bool = False,
    stop_event: StopSignal | None = None,
) -> RawBackendResult:
    """Run one atomic drag or one token-gated held-drag segment."""

    canonical_points = _canonical_drag_points(points)
    canonical_held_keys = (
        held_keys
        if (
            type(held_keys) is tuple
            and len(held_keys) == len(set(held_keys))
            and all(
                type(key) is str and key in CANONICAL_HOTKEY_KEYS
                for key in held_keys
            )
        )
        else None
    )
    held_mode = pointer_already_held or keep_holding
    stationary_path = bool(
        not canonical_points
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
        or not _valid_stop_signal(stop_event)
        or canonical_points is None
        or canonical_held_keys is None
        or not isinstance(button, MouseButton)
        or type(duration_ms) is not int
        or not 0 <= duration_ms <= MAX_POINTER_DURATION_MS
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
                or (held_mode and not valid_hold_token)
                or (not held_mode and hold_token is not None)
            )
        )
    ):
        return _physical_drag_result(
            False,
            error="invalid admitted physical drag",
            error_kind="invalid_args",
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
    if _stop_requested(stop_event) and not cleanup_only:
        return _physical_drag_result(
            False,
            error="drag stopped before input",
            error_kind="stop_requested",
            held_after=False,
            moves_completed=0,
        )

    if cleanup_only:
        held_before = _button_owned_by_agent(button)
        attempted, released, held_after = _best_effort_release_button(
            button,
            force=True,
        )
        return _physical_drag_result(
            True if released else None,
            error="" if released else "held drag cleanup UP outcome unknown",
            error_kind="" if released else "release_cleanup_incomplete",
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
        if stationary_path
        else [start, *canonical_points, end]
    )
    if held_mode:
        return _execute_held_drag_segment(
            path,
            hwnd=hwnd,
            final_guard=final_guard,
            phase=phase,
            button=button,
            held_keys=canonical_held_keys,
            duration_s=duration_ms / 1000.0,
            stop_event=stop_event,
        )

    guard_decision = None
    progress: dict[str, int | bool] = {
        "button_pressed": False,
        "moves_completed": 0,
        "release_cleanup_attempted": False,
        "release_cleanup_completed": False,
    }

    def before_press() -> bool:
        return True

    completed = False
    error = ""
    try:
        stroke_kwargs: dict[str, Any] = {
            "hwnd": hwnd,
            "before_press": before_press,
            "progress": progress,
        }
        if button is not MouseButton.LEFT:
            stroke_kwargs["button"] = button
        if canonical_held_keys:
            stroke_kwargs["held_keys"] = canonical_held_keys
        if stop_event is not None:
            stroke_kwargs["stop_event"] = stop_event
        completed = _stroke_path(
            path,
            duration_ms / 1000.0,
            **stroke_kwargs,
        )
        if not completed:
            if not progress["button_pressed"]:
                error = "drag could not start"
            else:
                error = "drag outcome unknown after physical input began"
    except Exception:
        error = (
            "drag outcome unknown after physical input began"
            if progress["button_pressed"]
            else "drag input did not start"
        )
    finally:
        cursor_restored = False

    input_completed = (
        True
        if completed
        else None
        if progress["button_pressed"]
        else False
    )
    error_kind: str | None = None
    if input_completed is None:
        error_kind = (
            "release_cleanup_incomplete"
            if progress["release_cleanup_attempted"]
            and not progress["release_cleanup_completed"]
            else "physical_drag_outcome_unknown"
        )
    elif input_completed is False:
        error_kind = "physical_drag_not_started"
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
    del hwnd

    # Build the waypoint list: start → optional polyline → end.
    waypoints: list[tuple[int, int]] = [(int(x1), int(y1))]
    if points:
        for p in points:
            try:
                waypoints.append((int(p[0]), int(p[1])))
            except (TypeError, IndexError, ValueError, KeyError):
                continue
    waypoints.append((int(x2), int(y2)))

    completed = _stroke_path(waypoints, duration_s, hwnd=None)
    BUS.publish("action", action="drag", frm=[x1, y1], to=[x2, y2],
                waypoints=len(waypoints))
    result = {"ok": bool(completed)}
    if not completed:
        result.update({
            "blocked": True,
            "verified": False,
            "failure_type": "PHYSICAL_DRAG_INCOMPLETE",
            "error": "physical drag did not complete",
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
    del hwnd
    drawn = 0
    attempted = 0
    interrupted = False
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
        completed = _stroke_path(waypoints, duration_s, hwnd=None)
        if not completed:
            interrupted = True
            break
        drawn += 1
        time.sleep(0.015)  # small gap so the app finalizes each stroke
    all_completed = attempted > 0 and not interrupted and drawn == attempted
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
                "failure_type": "PHYSICAL_DRAW_INCOMPLETE",
                "error": "physical drawing did not complete",
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

    if action not in {
        "move_mouse", "click", "scroll", "drag", "type_text", "hotkey",
        "input_sequence",
    }:
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
    error_kind: str = "",
    via: str = "physical_sendinput",
    cursor_restored: bool | None = None,
) -> RawBackendResult:
    facts: dict[str, Any] = {"ok": input_completed is True, "via": via}
    if error:
        facts.update({"blocked": True, "error": error})
    if error_kind:
        facts["error_kind"] = error_kind
    if cursor_restored is not None:
        facts["cursor_restored"] = cursor_restored
    return RawBackendResult(input_completed, facts)


def execute_admitted_physical_scroll(
    x: int,
    y: int,
    amount: int,
    hwnd: int | None = None,
    *,
    final_guard: FinalPointerGuard | None = None,
    stop_event: StopSignal | None = None,
) -> RawBackendResult:
    """Send one already-queued physical wheel input at a screen point.

    ``amount`` is a native Windows wheel delta: negative scrolls down,
    positive scrolls up, and 120 is one ordinary wheel notch.  Keeping the
    value native avoids turning a model-produced value such as -600 into
    hundreds of accidental notches.
    """

    del hwnd, final_guard

    if (
        any(type(value) is not int for value in (x, y, amount))
        or amount == 0
        or not _valid_stop_signal(stop_event)
    ):
        return _physical_scroll_result(
            False,
            error="invalid admitted physical scroll",
            error_kind="invalid_args",
        )
    if _dry_run():
        return RawBackendResult(False, {"ok": True, "dry_run": True})
    if _stop_requested(stop_event):
        return _physical_scroll_result(
            False,
            error="scroll stopped before input",
            error_kind="stop_requested",
        )

    input_completed: bool | None = False
    error = ""
    error_kind = ""
    via = "physical_sendinput"
    try:
        try:
            _send_inputs([
                _absolute_mouse_input(x, y),
                _mouse_input(
                    win32con.MOUSEEVENTF_WHEEL,
                    data=amount & 0xFFFFFFFF,
                )
            ])
        except Exception:
            input_completed = None
            error = "scroll wheel outcome unknown"
            error_kind = "wheel_outcome_unknown"
        else:
            input_completed = True
    finally:
        cursor_restored = False

    result = _physical_scroll_result(
        input_completed,
        error=error,
        error_kind=error_kind,
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
    """Compatibility entry point using the same native-delta wheel backend."""
    return execute_admitted_physical_scroll(x, y, amount, hwnd=hwnd)


def scroll(
    x: int,
    y: int,
    amount: int,
    hwnd: int | None = None,
) -> dict:
    """Compatibility adapter; ``amount`` is a native Windows wheel delta."""
    return dict(execute_scroll_input(x, y, amount, hwnd=hwnd).facts)


# ------------------------------------------------------------- keyboard API
def _keyboard_result(
    input_completed: bool | None,
    *,
    requested_codepoints: int | None = None,
    completed_codepoints: int | None = None,
    key_count: int | None = None,
    release_cleanup_attempted: bool = False,
    release_cleanup_completed: bool = True,
    error: str = "",
    error_kind: str = "",
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
    if error_kind:
        facts["error_kind"] = error_kind
    if guard_decision is not None:
        facts["guard_decision"] = guard_decision.value
    return RawBackendResult(input_completed, facts)


def _unicode_units(char: str) -> tuple[int, ...]:
    code = ord(char)
    if code <= 0xFFFF:
        return (code,)
    code -= 0x10000
    return (0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF))


def _vk_character_inputs(
    char: str,
) -> tuple[list[_Input], list[_Input], tuple[int, ...]] | None:
    # Ask Windows to map against US English explicitly and emit scan codes:
    # virtual-key-only OEM punctuation (notably ``-``) can otherwise be
    # reinterpreted through a different foreground layout.  The matching
    # foreground layout is prepared once by ``_english_layout_switch``.
    try:
        layout = user32.LoadKeyboardLayoutW("00000409", 0)
        result = (
            user32.VkKeyScanExW(ctypes.c_wchar(char), layout)
            if layout
            else user32.VkKeyScanW(ctypes.c_wchar(char))
        )
    except (AttributeError, OSError):
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
    downs = [_hardware_key_input(modifier) for modifier in modifiers]
    downs.append(_hardware_key_input(vk))
    ups = [_hardware_key_input(vk, key_up=True)]
    ups.extend(
        _hardware_key_input(modifier, key_up=True)
        for modifier in reversed(modifiers)
    )
    return downs, ups, (*modifiers, vk)


def _best_effort_key_releases(ups: list[_Input]) -> tuple[bool, bool]:
    attempted = bool(ups)
    completed = True
    for key_up in ups:
        if not _try_release(_send_inputs, [key_up]):
            completed = False
    return attempted, completed


def _best_effort_owned_key_releases(
    vks: tuple[int, ...],
) -> tuple[bool, bool]:
    attempted = bool(vks)
    completed = True
    for vk in vks:
        if _try_release(
            _send_inputs,
            [_hardware_key_input(vk, key_up=True)],
        ):
            with _held_mouse_lock:
                _held_keyboard_vks.discard(vk)
        else:
            completed = False
    return attempted, completed


def _best_effort_owned_unicode_releases(
    units: tuple[int, ...],
) -> tuple[bool, bool]:
    attempted = bool(units)
    completed = True
    for unit in units:
        if _try_release(
            _send_inputs,
            [_key_input(
                scan=unit,
                flags=_KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP,
            )],
        ):
            with _held_mouse_lock:
                _held_unicode_units.discard(unit)
        else:
            completed = False
    return attempted, completed


def _send_text_codepoint(char: str, *, via_vk: bool) -> tuple[bool | None, bool, bool]:
    if char == "\n":
        vk = _VK["enter"]
        batches = [(
            [_hardware_key_input(vk)],
            [_hardware_key_input(vk, key_up=True)],
            True,
            (vk,),
            (),
        )]
    elif via_vk:
        inputs = _vk_character_inputs(char)
        if inputs is None:
            return False, False, True
        downs, ups, vks = inputs
        batches = [(downs, ups, False, vks, ())]
    else:
        batches = [(
            [_key_input(scan=unit, flags=_KEYEVENTF_UNICODE)],
            [_key_input(
                scan=unit,
                flags=_KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP,
            )],
            False,
            (),
            (unit,),
        ) for unit in _unicode_units(char)]

    for downs, ups, split_release, owned_vks, owned_units in batches:
        with _held_mouse_lock:
            _held_keyboard_vks.update(owned_vks)
            _held_unicode_units.update(owned_units)
        try:
            _send_inputs(downs if split_release else [*downs, *ups])
            if split_release:
                _send_inputs(ups)
        except Exception:
            if owned_vks:
                cleanup_attempted, cleanup_completed = (
                    _best_effort_owned_key_releases(tuple(reversed(owned_vks)))
                )
            else:
                cleanup_attempted, cleanup_completed = (
                    _best_effort_owned_unicode_releases(owned_units)
                )
            return None, cleanup_attempted, cleanup_completed
        with _held_mouse_lock:
            _held_keyboard_vks.difference_update(owned_vks)
            _held_unicode_units.difference_update(owned_units)
    return True, False, True


def _execute_physical_type_text(
    text: str,
    hwnd: int | None = None,
    *,
    final_guard: FinalKeyboardGuard | None = None,
    stop_event: StopSignal | None = None,
    interval_s: float = 0.008,
    publish_action_fact: bool = True,
) -> RawBackendResult:
    """Type one bounded string through the shared physical keyboard owner."""

    del final_guard

    if (
        type(text) is not str
        or type(interval_s) not in {int, float}
        or isinstance(interval_s, bool)
        or not math.isfinite(float(interval_s))
        or not 0 <= float(interval_s) <= 1
        or not _valid_stop_signal(stop_event)
        or type(publish_action_fact) is not bool
    ):
        return _keyboard_result(
            False,
            requested_codepoints=len(text) if type(text) is str else 0,
            completed_codepoints=0,
            error="invalid admitted physical type text",
            error_kind="invalid_args",
        )
    if _dry_run():
        return RawBackendResult(False, {"ok": True, "dry_run": True})

    requested = len(text)
    completed = 0
    layout_switch: tuple[int | None, bool] | None = None
    via_vk = _should_strict_type_via_vk(text)
    if via_vk:
        layout_switch, keyboard_safe = _english_layout_switch(hwnd)
        if not keyboard_safe:
            # The target thread is unavailable, so VK transitions cannot be
            # made safe against the active IME; use the UNICODE path, which
            # bypasses IME composition entirely.
            via_vk = False
    try:
        for char in text:
            if _stop_requested(stop_event):
                return _keyboard_result(
                    False if completed == 0 else None,
                    requested_codepoints=requested,
                    completed_codepoints=completed,
                    error="text input stopped before completion",
                    error_kind="stop_requested",
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
                    error_kind=(
                        "release_cleanup_incomplete"
                        if cleanup_attempted and not cleanup_completed
                        else "text_input_outcome_unknown"
                        if sent is None
                        else "text_input_unavailable"
                    ),
                )
            completed += 1
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
    final_guard: FinalKeyboardGuard | None = None,
    interval_s: float = 0.008,
    stop_event: StopSignal | None = None,
) -> RawBackendResult:
    """Strict vNext boundary around the shared physical text mechanism."""

    if type(text) is not str or not 1 <= len(text) <= MAX_TYPE_TEXT_CODEPOINTS:
        return _keyboard_result(
            False,
            requested_codepoints=len(text) if type(text) is str else 0,
            completed_codepoints=0,
            error="invalid admitted physical type text",
            error_kind="invalid_args",
        )
    return _execute_physical_type_text(
        text,
        hwnd=hwnd,
        final_guard=final_guard,
        interval_s=interval_s,
        publish_action_fact=True,
        stop_event=stop_event,
    )


ADMITTED_PHYSICAL_TYPE_TEXT_BACKEND = AdmittedPhysicalTypeTextBackend(
    execute_admitted_physical_type_text
)


def _execute_physical_hotkey_vks(
    vks: tuple[int, ...],
    *,
    final_guard: FinalKeyboardGuard | None = None,
    release_individually: bool = True,
    stop_event: StopSignal | None = None,
) -> RawBackendResult:
    del final_guard
    if _stop_requested(stop_event):
        return _keyboard_result(
            False,
            key_count=len(vks),
            error="hotkey stopped before input",
            error_kind="stop_requested",
        )
    downs, ups = [], []
    for vk in vks:
        downs.append(_hardware_key_input(vk))
        ups.append(_hardware_key_input(vk, key_up=True))

    down_attempted = False
    down_completed = False
    release_attempted = False
    release_completed = True
    release_had_error = False
    try:
        down_attempted = True
        with _held_mouse_lock:
            _held_keyboard_vks.update(vks)
        _send_inputs(downs)
        down_completed = True
        time.sleep(0.03)
    except Exception:
        pass
    finally:
        if down_attempted:
            ordered_vks = tuple(reversed(vks))
            ordered_ups = list(reversed(ups))
            if release_individually:
                release_attempted, release_completed = (
                    _best_effort_owned_key_releases(ordered_vks)
                )
            else:
                release_attempted = bool(ordered_ups)
                try:
                    _send_inputs(ordered_ups)
                except Exception:
                    release_had_error = True
                    _attempted, release_completed = (
                        _best_effort_owned_key_releases(ordered_vks)
                    )
                else:
                    with _held_mouse_lock:
                        _held_keyboard_vks.difference_update(vks)

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
        )
    return _keyboard_result(
        None,
        key_count=len(vks),
        release_cleanup_attempted=release_attempted,
        release_cleanup_completed=release_completed,
        error="hotkey outcome unknown",
        error_kind=(
            "release_cleanup_incomplete"
            if not release_completed
            else "hotkey_outcome_unknown"
        ),
        guard_decision=FinalKeyboardGuardDecision.TARGET_UNAVAILABLE,
    )


def execute_admitted_physical_hotkey(
    chord: HotkeyChord,
    hwnd: int | None = None,
    *,
    repeat: int = 1,
    final_guard: FinalKeyboardGuard | None = None,
    stop_event: StopSignal | None = None,
) -> RawBackendResult:
    """Send one canonical chord without queueing, parsing, focus, or fallback."""

    if (
        not isinstance(chord, HotkeyChord)
        or type(repeat) is not int
        or not 1 <= repeat <= 100
        or not _valid_stop_signal(stop_event)
    ):
        return _keyboard_result(
            False,
            key_count=0,
            error="invalid admitted hotkey",
            error_kind="invalid_args",
        )
    if _dry_run():
        return RawBackendResult(False, {"ok": True, "dry_run": True})
    try:
        vks = tuple(_vk_of(key) for key in chord.keys)
    except Exception:
        return _keyboard_result(
            False,
            key_count=len(chord.keys),
            error="invalid admitted hotkey",
            error_kind="invalid_key",
        )
    completed = 0
    result: RawBackendResult | None = None
    for _index in range(repeat):
        result = _execute_physical_hotkey_vks(
            vks,
            final_guard=final_guard,
            stop_event=stop_event,
        )
        if result.input_completed is not True:
            break
        completed += 1
        if completed < repeat:
            time.sleep(0.03)
    assert result is not None
    facts = dict(result.facts)
    facts.update(
        requested_repetitions=repeat,
        completed_repetitions=completed,
    )
    completed_result = RawBackendResult(
        True if completed == repeat else None if completed else result.input_completed,
        facts,
    )
    if completed_result.input_completed is True:
        _publish_admitted_action_summary(
            action="hotkey",
            input_completed=True,
            via="physical_sendinput",
            key_count=len(chord.keys),
        )
    return completed_result


ADMITTED_PHYSICAL_HOTKEY_BACKEND = AdmittedPhysicalHotkeyBackend(
    execute_admitted_physical_hotkey
)


def _sequence_result(
    input_completed: bool | None,
    *,
    event_count: int,
    completed_events: int,
    moves_completed: int = 0,
    error: str = "",
    error_kind: str = "",
    release_cleanup_attempted: bool = False,
    release_cleanup_completed: bool = False,
) -> RawBackendResult:
    facts: dict[str, Any] = {
        "ok": input_completed is True,
        "via": "physical_sendinput",
        "event_count": event_count,
        "completed_events": completed_events,
        "moves_completed": int(moves_completed),
        "cursor_restored": False,
    }
    if error:
        facts.update(blocked=True, error=error)
    if error_kind:
        facts["error_kind"] = error_kind
    if release_cleanup_attempted:
        facts.update(
            release_cleanup_attempted=True,
            release_cleanup_completed=release_cleanup_completed,
        )
    return RawBackendResult(input_completed, facts)


def _release_sequence_inputs(
    pressed_buttons: list[MouseButton],
    pressed_keys: list[int],
) -> tuple[bool, bool]:
    attempted = bool(pressed_buttons or pressed_keys)
    completed = True
    for button in reversed(pressed_buttons):
        _attempted, released, _held_after = _best_effort_release_button(
            button,
            force=True,
        )
        if not released:
            completed = False
    for vk in reversed(pressed_keys):
        released = _try_release(
            _send_inputs,
            [_hardware_key_input(vk, key_up=True)],
        )
        if released:
            with _held_mouse_lock:
                _held_keyboard_vks.discard(vk)
        else:
            completed = False
    return attempted, completed


def execute_admitted_physical_sequence(
    events: tuple[PhysicalInputEvent, ...],
    hwnd: int | None = None,
    *,
    final_keyboard_guard: FinalKeyboardGuard | None = None,
    stop_event: StopSignal | None = None,
) -> RawBackendResult:
    """Execute one generic, serialized mouse/keyboard event timeline.

    This is the expressive physical primitive for games, drawing and modifier
    gestures.  It never invents targets or app semantics.  Every key/button
    pressed by this call is released before returning, including exceptions.
    """

    del final_keyboard_guard

    if (
        type(events) is not tuple
        or not 1 <= len(events) <= MAX_INPUT_SEQUENCE_EVENTS
        or any(not isinstance(event, PhysicalInputEvent) for event in events)
        or not _valid_stop_signal(stop_event)
    ):
        return _sequence_result(
            False,
            event_count=len(events) if type(events) is tuple else 0,
            completed_events=0,
            error="invalid admitted physical input sequence",
            error_kind="invalid_args",
        )
    if _dry_run():
        return RawBackendResult(False, {"ok": True, "dry_run": True})

    # This is a device timeline, not a semantic state machine.  Repeated DOWN
    # events are useful to raw-input/game consumers and a standalone UP is a
    # practical way to recover a key or button left down outside this call.
    # Track only what this call still owns so ``finally`` can clean it up.
    pressed_buttons: list[MouseButton] = []
    pressed_keys: list[int] = []
    completed_events = 0
    moves_completed = 0
    physical_attempted = False
    outcome: bool | None = False
    error = ""
    error_kind = ""
    cleanup_attempted = False
    cleanup_completed = True
    active_move_progress: dict[str, int | bool] | None = None
    try:
        current: tuple[int, int] | None = None
        for event in events:
            if _stop_requested(stop_event):
                outcome = None if physical_attempted else False
                error = "input sequence stopped before completion"
                error_kind = "stop_requested"
                break
            if event.kind is InputEventKind.MOVE:
                assert event.x is not None and event.y is not None
                if current is None:
                    try:
                        current = tuple(map(int, _cursor_pos()))
                    except Exception:
                        current = (event.x, event.y)
                destination = (event.x, event.y)
                progress: dict[str, int | bool] = {"moves_completed": 0}
                active_move_progress = progress
                physical_attempted = True
                if current == destination:
                    _abs_mouse_move(*destination)
                    progress["moves_completed"] = 1
                    moved = True
                else:
                    moved = _move_pointer_path(
                        current,
                        destination,
                        event.duration_ms / 1000.0,
                        hwnd=hwnd,
                        progress=progress,
                        stop_event=stop_event,
                    )
                if not moved:
                    moves_completed += int(progress["moves_completed"])
                    active_move_progress = None
                    outcome = None if moves_completed else False
                    error = "input sequence pointer move interrupted"
                    error_kind = "pointer_move_interrupted"
                    break
                moves_completed += int(progress["moves_completed"])
                active_move_progress = None
                current = destination
            elif event.kind is InputEventKind.MOVE_RELATIVE:
                assert event.x is not None and event.y is not None
                progress = {"moves_completed": 0}
                active_move_progress = progress
                physical_attempted = True
                if not _move_pointer_relative(
                    event.x,
                    event.y,
                    event.duration_ms / 1000.0,
                    progress=progress,
                    stop_event=stop_event,
                ):
                    moves_completed += int(progress["moves_completed"])
                    active_move_progress = None
                    outcome = None if moves_completed else False
                    error = "input sequence relative pointer move interrupted"
                    error_kind = "relative_pointer_move_interrupted"
                    break
                moves_completed += int(progress["moves_completed"])
                active_move_progress = None
                # OS acceleration and raw-input consumers deliberately make
                # relative deltas unlike absolute screen coordinates.
                current = None
            elif event.kind is InputEventKind.MOUSE_DOWN:
                assert event.button is not None
                if event.button not in pressed_buttons:
                    if not _record_button_owned(event.button):
                        outcome = None if physical_attempted else False
                        error = f"mouse button {event.button.value} is already held"
                        error_kind = "mouse_button_already_held"
                        break
                    pressed_buttons.append(event.button)
                down, _up, data = _mouse_button_flags(event.button)
                physical_attempted = True
                inputs = []
                if current is not None:
                    inputs.append(_absolute_mouse_input(*current))
                inputs.append(_mouse_button_input(down, data))
                _send_inputs(inputs)
            elif event.kind is InputEventKind.MOUSE_UP:
                assert event.button is not None
                physical_attempted = True
                _attempted, released, _held_after = (
                    _best_effort_release_button(event.button, force=True)
                )
                if not released:
                    raise OSError(
                        f"mouse button {event.button.value} UP was uncertain"
                    )
                if event.button in pressed_buttons:
                    pressed_buttons.remove(event.button)
            elif event.kind is InputEventKind.KEY_DOWN:
                assert event.key is not None
                vk = _vk_of(event.key)
                if vk not in pressed_keys:
                    pressed_keys.append(vk)
                with _held_mouse_lock:
                    _held_keyboard_vks.add(vk)
                physical_attempted = True
                _send_inputs([_hardware_key_input(vk)])
            elif event.kind is InputEventKind.KEY_UP:
                assert event.key is not None
                vk = _vk_of(event.key)
                physical_attempted = True
                _send_inputs([_hardware_key_input(vk, key_up=True)])
                with _held_mouse_lock:
                    _held_keyboard_vks.discard(vk)
                if vk in pressed_keys:
                    pressed_keys.remove(vk)
            elif event.kind in {InputEventKind.WHEEL, InputEventKind.HWHEEL}:
                assert event.amount is not None
                physical_attempted = True
                inputs = []
                if current is not None:
                    inputs.append(_absolute_mouse_input(*current))
                inputs.append(_mouse_input(
                    (
                        win32con.MOUSEEVENTF_WHEEL
                        if event.kind is InputEventKind.WHEEL
                        else getattr(win32con, "MOUSEEVENTF_HWHEEL", 0x01000)
                    ),
                    data=event.amount & 0xFFFFFFFF,
                ))
                _send_inputs(inputs)
            else:
                assert event.kind is InputEventKind.WAIT
                deadline = time.monotonic() + event.duration_ms / 1000.0
                while True:
                    if _stop_requested(stop_event):
                        outcome = None if physical_attempted else False
                        error = "input sequence stopped while waiting"
                        error_kind = "stop_requested"
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    time.sleep(min(0.02, remaining))
                if error:
                    break
            completed_events += 1
        else:
            outcome = True
    except Exception:
        if active_move_progress is not None:
            moves_completed += int(
                active_move_progress.get("moves_completed", 0)
            )
        outcome = None if physical_attempted else False
        error = "input sequence outcome unknown after physical input began"
        error_kind = "physical_sequence_outcome_unknown"
    finally:
        cleanup_attempted, cleanup_completed = _release_sequence_inputs(
            pressed_buttons,
            pressed_keys,
        )
        if cleanup_attempted and not cleanup_completed:
            outcome = None
            error = "input sequence release cleanup was incomplete"
            error_kind = "release_cleanup_incomplete"

    if outcome is True:
        _publish_admitted_action_summary(
            action="input_sequence",
            input_completed=True,
            via="physical_sendinput",
        )
    return _sequence_result(
        outcome,
        event_count=len(events),
        completed_events=completed_events,
        moves_completed=moves_completed,
        error=error,
        error_kind=error_kind,
        release_cleanup_attempted=cleanup_attempted,
        release_cleanup_completed=cleanup_completed,
    )


ADMITTED_PHYSICAL_SEQUENCE_BACKEND = AdmittedPhysicalSequenceBackend(
    execute_admitted_physical_sequence
)


def _legacy_hotkey_guard(hwnd: int | None) -> FinalKeyboardGuardDecision:
    del hwnd
    return FinalKeyboardGuardDecision.ADMITTED


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
    del hwnd
    if _dry_run():
        return {"ok": True, "dry_run": True}
    try:
        typed_button = MouseButton(str(button).casefold())
    except Exception:
        return {"ok": False, "error": f"unknown mouse button: {button}"}
    if not _record_button_owned(typed_button):
        return {
            "ok": False,
            "verified": False,
            "blocked": True,
            "button": typed_button.value,
            "error": "mouse button is already held by this agent transaction",
        }
    down, _up, data = _mouse_button_flags(typed_button)
    try:
        _send_inputs([_mouse_button_input(down, data)])
    except Exception:
        attempted, released, held_after = _best_effort_release_button(
            typed_button,
            force=True,
        )
        return {
            "ok": False,
            "verified": False,
            "blocked": True,
            "button": typed_button.value,
            "input_completed": None,
            "outcome_unknown": True,
            "release_cleanup_attempted": attempted,
            "release_cleanup_completed": released,
            "held_after": held_after,
            "error": "mouse-down outcome unknown",
        }
    press_sampled = _sample_pressed_state()
    BUS.publish("action", action="mouse_down", button=typed_button.value)
    return {
        "ok": True,
        "button": typed_button.value,
        "press_sampled": press_sampled,
    }


@queued_desktop_action("mouse_up")
def mouse_up(button: str = "left") -> dict:
    """Release a mouse button at the current cursor position."""
    if _dry_run():
        return {"ok": True, "dry_run": True}
    try:
        typed_button = MouseButton(str(button).casefold())
    except Exception:
        return {"ok": False, "error": f"unknown mouse button: {button}"}
    if not _button_owned_by_agent(typed_button):
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
    attempted, released, held_after = _best_effort_release_button(typed_button)
    if not released:
        return {
            "ok": False,
            "verified": False,
            "blocked": True,
            "button": typed_button.value,
            "input_completed": None,
            "outcome_unknown": True,
            "release_cleanup_attempted": attempted,
            "release_cleanup_completed": False,
            "held_after": held_after,
            "error": "mouse-up outcome unknown",
        }
    BUS.publish("action", action="mouse_up", button=typed_button.value)
    return {"ok": True, "button": typed_button.value}


def agent_holds_mouse_button(button: str = "left") -> bool:
    """Return whether the current process owns a matching injected press."""
    with _held_mouse_lock:
        return str(button or "left") in _held_mouse_buttons


@queued_desktop_action("release_held_mouse_buttons")
def release_held_mouse_buttons() -> list[str]:
    """Release mouse buttons left held by an interrupted or completed run."""
    with _held_mouse_lock:
        # A mouse UP may have succeeded while a paired held-key UP remained
        # uncertain. Keep that key ownership discoverable by the same ordinary
        # session cleanup path; no separate verifier or state machine needed.
        buttons = sorted(_held_mouse_buttons | set(_held_mouse_key_vks))
    released: list[str] = []
    for button in buttons:
        _attempted, completed, _held_after = _best_effort_release_button(
            MouseButton(button),
            force=True,
        )
        if completed:
            released.append(button)
    return released


@queued_desktop_action("release_held_input_state")
def release_held_input_state() -> bool:
    """Boundedly retry every mouse/key release still owned by this process."""

    release_held_mouse_buttons()
    with _held_mouse_lock:
        keys = tuple(_held_keyboard_vks)
    for vk in reversed(keys):
        if _try_release(
            _send_inputs,
            [_hardware_key_input(vk, key_up=True)],
        ):
            with _held_mouse_lock:
                _held_keyboard_vks.discard(vk)
    with _held_mouse_lock:
        units = tuple(_held_unicode_units)
    _best_effort_owned_unicode_releases(units)
    with _held_mouse_lock:
        return not (
            _held_mouse_buttons
            or _held_mouse_key_vks
            or _held_keyboard_vks
            or _held_unicode_units
        )


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
    downs = [_hardware_key_input(vk) for vk in vks]
    ups = [_hardware_key_input(vk, key_up=True) for vk in vks]
    down_attempted = True
    try:
        _send_inputs(downs)
    except Exception:
        attempted, released = _best_effort_key_releases(list(reversed(ups)))
        return {
            "ok": False,
            "verified": False,
            "blocked": True,
            "input_completed": None,
            "outcome_unknown": True,
            "release_cleanup_attempted": attempted,
            "release_cleanup_completed": released,
            "error": "held-key DOWN outcome unknown",
        }
    interrupted = False
    cleanup_completed = True
    try:
        remaining_s = max(int(duration_ms), 0) / 1000.0
        while remaining_s > 0:
            interval = min(0.025, remaining_s)
            time.sleep(interval)
            remaining_s -= interval
            if stop_event is not None and stop_event.is_set():
                interrupted = True
                break
    finally:
        _attempted, cleanup_completed = _best_effort_key_releases(
            list(reversed(ups))
        )
    if not cleanup_completed:
        return {
            "ok": False,
            "verified": False,
            "blocked": True,
            "input_completed": None,
            "outcome_unknown": True,
            "release_cleanup_attempted": down_attempted,
            "release_cleanup_completed": False,
            "error": "held-key release outcome unknown",
        }
    if interrupted:
        return {
            "ok": False,
            "verified": False,
            "blocked": True,
            "canceled": bool(stop_event is not None and stop_event.is_set()),
            "keys": keys,
            "duration_ms": duration_ms,
            "error": "key hold stopped by cancellation",
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
