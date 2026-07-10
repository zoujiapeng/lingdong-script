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
import os
import re
import string
import subprocess
import threading
import time
from ctypes import wintypes
from io import BytesIO
from typing import Any, Callable

import win32api
import win32con
import win32gui

from ..config import CONFIG
from ..utils.log import BUS, logger
from .desktop_queue import queued_desktop_action

user32 = ctypes.windll.user32

# ToolSession installs this callback only around a visible action. Sampling
# after mouse-down makes short-lived pressed states part of action evidence.
_press_probe_local = threading.local()


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


def _should_type_via_vk(text: str) -> bool:
    return bool(text) and all(char in _VK_TEXT_CHARS for char in text)


def _dry_run() -> bool:
    return bool(CONFIG.get("agent", "dry_run", default=False))


def _no_mouse_steal() -> bool:
    """Experimental: act without hijacking the user's physical cursor."""
    return bool(CONFIG.get("agent", "no_mouse_steal", default=False))


# ---- no-steal helpers: background message clicks + cursor save/restore ----
# Windows has a single system cursor, so a true "second mouse" is impossible;
# instead we click via PostMessage (no cursor move) and, when SendInput is
# unavoidable, snap the cursor back to where the user left it.
_WM = {  # button -> (down, up, mk_flag)
    "left": (win32con.WM_LBUTTONDOWN, win32con.WM_LBUTTONUP, win32con.MK_LBUTTON),
    "right": (win32con.WM_RBUTTONDOWN, win32con.WM_RBUTTONUP, win32con.MK_RBUTTON),
    "middle": (win32con.WM_MBUTTONDOWN, win32con.WM_MBUTTONUP, win32con.MK_MBUTTON),
}


def _post_click(x: int, y: int, button: str, double: bool) -> bool:
    """Background click at screen (x,y) via window messages — no cursor move.
    Returns False if no target window resolved (caller falls back)."""
    try:
        target = win32gui.WindowFromPoint((int(x), int(y)))
        if not target:
            return False
        cx, cy = win32gui.ScreenToClient(target, (int(x), int(y)))
        lparam = (cy << 16) | (cx & 0xFFFF)
        down, up, mk = _WM.get(button, _WM["left"])
        for _ in range(2 if double else 1):
            win32gui.PostMessage(target, down, mk, lparam)
            sampled = False
            try:
                sampled = _sample_pressed_state()
            finally:
                win32gui.PostMessage(target, up, 0, lparam)
            if sampled:
                _settle_after_release()
            if double:
                time.sleep(0.05)
        return True
    except Exception as exc:
        logger.debug("post_click 失败: %s", exc)
        return False


def _cursor_pos() -> tuple[int, int]:
    return win32api.GetCursorPos()


def _restore_cursor(pos: tuple[int, int]) -> None:
    try:
        win32api.SetCursorPos(pos)
    except Exception:
        pass


# ---------------------------------------------------------------- mouse API
def _raw_move_mouse(x: int, y: int) -> None:
    user32.SetCursorPos(int(x), int(y))


@queued_desktop_action("move_mouse")
def move_mouse(x: int, y: int) -> None:
    _raw_move_mouse(x, y)


@queued_desktop_action("click")
def click(x: int, y: int, button: str = "left", double: bool = False, hwnd: int | None = None) -> dict:
    """Click at screen coords. Brings target window forward first if given.

    In no-mouse-steal mode, clicks go through background window messages (the
    user's physical cursor is never moved); falls back to SendInput with a
    cursor snap-back if the message click can't resolve a target.
    """
    if _dry_run():
        return {"ok": True, "dry_run": True}
    if _no_mouse_steal():
        if _post_click(x, y, button, double):
            BUS.publish("action", action="click", x=x, y=y, button=button,
                        double=double, via="post_message")
            return {"ok": True, "x": x, "y": y, "via": "post_message"}
        # fallback: must use SendInput, but restore the user's cursor afterwards
        saved = _cursor_pos()
        try:
            _raw_click(x, y, button, double, hwnd)
        finally:
            _restore_cursor(saved)
        BUS.publish("action", action="click", x=x, y=y, button=button,
                    double=double, via="sendinput_restored")
        return {"ok": True, "x": x, "y": y, "via": "sendinput_restored"}
    _raw_click(x, y, button, double, hwnd)
    BUS.publish("action", action="click", x=x, y=y, button=button, double=double)
    return {"ok": True, "x": x, "y": y}


def _raw_click(x: int, y: int, button: str, double: bool, hwnd: int | None) -> None:
    if hwnd:
        _raw_focus_window(hwnd)
    _raw_move_mouse(x, y)
    time.sleep(0.05)
    if button == "left":
        down, up = win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP
    elif button == "right":
        down, up = win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP
    elif button == "middle":
        down, up = win32con.MOUSEEVENTF_MIDDLEDOWN, win32con.MOUSEEVENTF_MIDDLEUP
    else:
        raise ValueError(f"未知鼠标键: {button}")
    for _ in range(2 if double else 1):
        _send_inputs([_mouse_input(down)])
        sampled = False
        try:
            sampled = _sample_pressed_state()
        finally:
            _send_inputs([_mouse_input(up)])
        if sampled:
            _settle_after_release()
        if double:
            time.sleep(0.06)


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


def _stroke_path(waypoints: list[tuple[int, int]], duration_s: float) -> None:
    """Press at the first waypoint, move through the path, release at the end.
    Shared by drag() and draw_strokes() so stroke registration is identical."""
    _abs_mouse_move(*waypoints[0])  # absolute move so the press lands as real input
    time.sleep(0.08)
    _send_inputs([_mouse_input(win32con.MOUSEEVENTF_LEFTDOWN)])
    time.sleep(0.04)  # let the app register the press before moving

    seg_lens = [((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
                for a, b in zip(waypoints, waypoints[1:])]
    total = max(sum(seg_lens), 1.0)
    for (a, b), seg in zip(zip(waypoints, waypoints[1:]), seg_lens):
        steps = max(4, int(seg / 8))  # ~8px per move event for a smooth stroke
        seg_dur = duration_s * (seg / total)
        for i in range(1, steps + 1):
            _emit_move(int(a[0] + (b[0] - a[0]) * i / steps),
                       int(a[1] + (b[1] - a[1]) * i / steps))
            time.sleep(max(0.008, seg_dur / steps))

    time.sleep(0.03)
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
    if hwnd:
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

    _stroke_path(waypoints, duration_s)
    if saved:
        _restore_cursor(saved)
    BUS.publish("action", action="drag", frm=[x1, y1], to=[x2, y2],
                waypoints=len(waypoints))
    return {"ok": True}


@queued_desktop_action("draw_strokes")
def draw_strokes(strokes: list, duration_s: float = 0.4,
                 hwnd: int | None = None) -> dict:
    """Draw multiple SEPARATE strokes in one call, lifting the pen between them.

    Each stroke is a polyline [[x,y],...]; the pen presses at its first point,
    moves through the rest, and releases — so strokes don't connect. This lets
    the agent commit a whole figure (face + ears + eyes + whiskers) from a
    single observation instead of one LLM round-trip per stroke. Returns the
    count of strokes actually drawn (those with >= 2 valid points)."""
    if _dry_run():
        return {"ok": True, "dry_run": True}
    if hwnd:
        _raw_focus_window(hwnd)
        time.sleep(0.05)
    saved = _cursor_pos() if _no_mouse_steal() else None
    drawn = 0
    for stroke in strokes or []:
        waypoints: list[tuple[int, int]] = []
        for p in stroke or []:
            try:
                waypoints.append((int(p[0]), int(p[1])))
            except (TypeError, IndexError, ValueError, KeyError):
                continue
        if len(waypoints) < 2:
            continue
        _stroke_path(waypoints, duration_s)
        drawn += 1
        time.sleep(0.04)  # small gap so the app finalizes each stroke
    if saved:
        _restore_cursor(saved)
    BUS.publish("action", action="draw_strokes", strokes=drawn)
    return {"ok": True, "strokes_drawn": drawn}


@queued_desktop_action("scroll")
def scroll(x: int, y: int, amount: int) -> dict:
    """amount: positive=up, negative=down; in notches."""
    if _dry_run():
        return {"ok": True, "dry_run": True}
    if _no_mouse_steal():
        # WM_MOUSEWHEEL to the window under the point — no cursor move.
        try:
            target = win32gui.WindowFromPoint((int(x), int(y)))
            if target:
                wparam = (int(amount) * 120) << 16
                lparam = (int(y) << 16) | (int(x) & 0xFFFF)  # screen coords for wheel
                win32gui.PostMessage(target, win32con.WM_MOUSEWHEEL, wparam, lparam)
                BUS.publish("action", action="scroll", x=x, y=y, amount=amount,
                            via="post_message")
                return {"ok": True, "via": "post_message"}
        except Exception as exc:
            logger.debug("post scroll 失败: %s", exc)
        saved = _cursor_pos()
        try:
            _raw_move_mouse(x, y)
            time.sleep(0.04)
            _send_inputs([_mouse_input(win32con.MOUSEEVENTF_WHEEL, data=amount * 120 & 0xFFFFFFFF)])
        finally:
            _restore_cursor(saved)
        BUS.publish("action", action="scroll", x=x, y=y, amount=amount,
                    via="sendinput_restored")
        return {"ok": True, "via": "sendinput_restored"}
    _raw_move_mouse(x, y)
    time.sleep(0.04)
    _send_inputs([_mouse_input(win32con.MOUSEEVENTF_WHEEL, data=amount * 120 & 0xFFFFFFFF)])
    BUS.publish("action", action="scroll", x=x, y=y, amount=amount)
    return {"ok": True}


# ------------------------------------------------------------- keyboard API
@queued_desktop_action("type_text")
def type_text(text: str, interval_s: float = 0.008, hwnd: int | None = None) -> dict:
    """Unicode injection — IME-independent, supports Chinese.

    hwnd: if given, re-focus the window every 8 characters so focus theft
    (another window activating mid-type) does not silently swallow keystrokes.
    """
    if _dry_run():
        return {"ok": True, "dry_run": True}

    def _refocus_if_needed(i: int) -> None:
        if hwnd and i > 0 and i % 8 == 0:
            if win32gui.GetForegroundWindow() != hwnd:
                _raw_focus_window(hwnd)
                time.sleep(0.08)

    if _should_type_via_vk(text):
        typed = 0
        for char in text:
            _refocus_if_needed(typed)
            if not _press_char_vk(char):
                break
            typed += 1
            time.sleep(max(interval_s, 0.03))
        BUS.publish("action", action="type_text", length=typed, via="vk")
        return {"ok": typed == len(text), "typed": typed,
                "requested": len(text), "via": "vk"}
    for i, char in enumerate(text):
        _refocus_if_needed(i)
        if char == "\n":
            _raw_press_keys(["enter"])
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
        time.sleep(interval_s)
    BUS.publish("action", action="type_text", length=len(text))
    return {"ok": True, "typed": len(text)}


def _raw_press_keys(keys: list[str]) -> dict:
    vks = [_vk_of(k) for k in keys]
    downs, ups = [], []
    for vk in vks:
        flags = _KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED_VKS else 0
        downs.append(_key_input(vk=vk, flags=flags))
        ups.append(_key_input(vk=vk, flags=flags | _KEYEVENTF_KEYUP))
    _send_inputs(downs)
    time.sleep(0.03)
    _send_inputs(list(reversed(ups)))
    return {"ok": True, "keys": keys}


@queued_desktop_action("press_keys")
def press_keys(keys: list[str]) -> dict:
    """Press a chord, e.g. ["ctrl","s"] or single ["enter"]."""
    if _dry_run():
        return {"ok": True, "dry_run": True}
    _raw_press_keys(keys)
    BUS.publish("action", action="hotkey", keys=keys)
    return {"ok": True, "keys": keys}


# --------------------------------------------------------------- window API
def _raw_focus_window(hwnd: int) -> dict:
    """Robust foreground switch (works around SetForegroundWindow locks)."""
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.15)
        if win32gui.GetForegroundWindow() == hwnd:
            return {"ok": True}
        # ALT tap trick: unlocks SetForegroundWindow for our process
        _send_inputs([
            _key_input(vk=0x12), _key_input(vk=0x12, flags=_KEYEVENTF_KEYUP),
        ])
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.12)
        ok = win32gui.GetForegroundWindow() == hwnd
        if not ok:
            win32gui.BringWindowToTop(hwnd)
            time.sleep(0.1)
            ok = win32gui.GetForegroundWindow() == hwnd
        return {"ok": ok}
    except Exception as exc:
        logger.warning("focus_window 失败 hwnd=%s: %s", hwnd, exc)
        return {"ok": False, "error": str(exc)}


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


@queued_desktop_action("close_window")
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


@queued_desktop_action("clipboard_write")
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


@queued_desktop_action("clipboard_paste")
def clipboard_paste() -> dict:
    """Paste from clipboard via Ctrl+V."""
    if _dry_run():
        return {"ok": True, "dry_run": True}
    _raw_press_keys(["ctrl", "v"])
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
def mouse_down(button: str = "left") -> dict:
    """Press and hold a mouse button at the current cursor position."""
    if _dry_run():
        return {"ok": True, "dry_run": True}
    if button == "left":
        _send_inputs([_mouse_input(win32con.MOUSEEVENTF_LEFTDOWN)])
    elif button == "right":
        _send_inputs([_mouse_input(win32con.MOUSEEVENTF_RIGHTDOWN)])
    elif button == "middle":
        _send_inputs([_mouse_input(win32con.MOUSEEVENTF_MIDDLEDOWN)])
    else:
        return {"ok": False, "error": f"未知鼠标键: {button}"}
    BUS.publish("action", action="mouse_down", button=button)
    return {"ok": True, "button": button}


@queued_desktop_action("mouse_up")
def mouse_up(button: str = "left") -> dict:
    """Release a mouse button at the current cursor position."""
    if _dry_run():
        return {"ok": True, "dry_run": True}
    if button == "left":
        _send_inputs([_mouse_input(win32con.MOUSEEVENTF_LEFTUP)])
    elif button == "right":
        _send_inputs([_mouse_input(win32con.MOUSEEVENTF_RIGHTUP)])
    elif button == "middle":
        _send_inputs([_mouse_input(win32con.MOUSEEVENTF_MIDDLEUP)])
    else:
        return {"ok": False, "error": f"未知鼠标键: {button}"}
    BUS.publish("action", action="mouse_up", button=button)
    return {"ok": True, "button": button}


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
            # If display_id specified, grab that monitor; otherwise primary
            if display_id is not None and display_id < len(sct.monitors) - 1:
                monitor = sct.monitors[display_id + 1]  # sct.monitors[0] is "all"
            else:
                monitor = sct.monitors[1]  # primary monitor
            raw = sct.grab(monitor)
            image = Image.frombytes("RGB", raw.size, raw.rgb)
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=quality, optimize=True)
        b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
        return {
            "ok": True,
            "base64": b64,
            "width": image.width,
            "height": image.height,
            "size_bytes": len(buffer.getvalue()),
        }
    except ImportError:
        # mss not available — fall back to win32 screenshot
        try:
            from ..utils.screenshot import capture, Region, screen_size
            sw, sh = screen_size()
            region = Region(0, 0, sw, sh)
            image = capture(region)
            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=quality, optimize=True)
            b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
            return {
                "ok": True,
                "base64": b64,
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
def hold_keys(keys: list[str], duration_ms: int) -> dict:
    """Press and hold a chord for ``duration_ms`` then release.

    Useful for games / accelerated UI where a key must stay down (e.g. hold
    Shift while clicking, or hold an arrow to scroll a long list).
    """
    if _dry_run():
        return {"ok": True, "dry_run": True}
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
    try:
        time.sleep(max(int(duration_ms), 0) / 1000.0)
    finally:
        _send_inputs(list(reversed(ups)))
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
