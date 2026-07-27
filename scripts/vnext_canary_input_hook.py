"""Passive, in-memory Windows input telemetry for the vNext canary.

The hook never suppresses, rewrites, synthesizes, or replays input.  Its only
side effect is appending bounded normalized records to process memory.  The
pure normalization helpers are intentionally usable without installing a real
Windows hook so the evidence contract can be tested safely.
"""
from __future__ import annotations

from collections import deque
import copy
import ctypes
from ctypes import wintypes
import math
import os
import threading
import time
from typing import Any


WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
HC_ACTION = 0
WM_QUIT = 0x0012

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
WM_MOUSEHWHEEL = 0x020E

LLMHF_INJECTED = 0x00000001
LLMHF_LOWER_IL_INJECTED = 0x00000002
LLKHF_EXTENDED = 0x00000001
LLKHF_LOWER_IL_INJECTED = 0x00000002
LLKHF_INJECTED = 0x00000010
LLKHF_ALTDOWN = 0x00000020
LLKHF_UP = 0x00000080

GA_ROOT = 2
PM_NOREMOVE = 0x0000

_FORMAT = "vnext-canary-passive-input-hook/1"
_DEFAULT_MAX_EVENTS = 20_000
_DEFAULT_STARTUP_TIMEOUT_S = 5.0
_DEFAULT_STOP_TIMEOUT_S = 5.0
_MAX_TIMEOUT_S = 30.0

_LRESULT = ctypes.c_ssize_t
_ULONG_PTR = ctypes.c_size_t
_HOOK_FACTORY = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
_HOOK_PROC = _HOOK_FACTORY(
    _LRESULT,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = (
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    )


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = (
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    )


def _strict_int(value: Any, name: str, *, minimum: int | None = None) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _timeout(value: Any, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise TypeError(f"{name} must be a finite number")
    normalized = float(value)
    if not 0.1 <= normalized <= _MAX_TIMEOUT_S:
        raise ValueError(f"{name} must be in [0.1, {_MAX_TIMEOUT_S}]")
    return normalized


def _signed_high_word(value: int) -> int:
    return int(ctypes.c_int16((int(value) >> 16) & 0xFFFF).value)


def _unsigned_high_word(value: int) -> int:
    return (int(value) >> 16) & 0xFFFF


def mouse_event_from_raw(
    message: int,
    *,
    time_ns: int,
    x: int,
    y: int,
    mouse_data: int,
    flags: int,
    os_event_time_ms: int,
    foreground_hwnd: int,
    foreground_root_hwnd: int,
    window_from_point_hwnd: int,
) -> dict[str, Any]:
    """Normalize one raw low-level mouse callback without touching Windows."""

    message = _strict_int(message, "message", minimum=0)
    time_ns = _strict_int(time_ns, "time_ns", minimum=0)
    x = _strict_int(x, "x")
    y = _strict_int(y, "y")
    mouse_data = _strict_int(mouse_data, "mouse_data", minimum=0)
    flags = _strict_int(flags, "flags", minimum=0)
    os_event_time_ms = _strict_int(
        os_event_time_ms,
        "os_event_time_ms",
        minimum=0,
    )
    foreground_hwnd = _strict_int(
        foreground_hwnd,
        "foreground_hwnd",
        minimum=0,
    )
    foreground_root_hwnd = _strict_int(
        foreground_root_hwnd,
        "foreground_root_hwnd",
        minimum=0,
    )
    window_from_point_hwnd = _strict_int(
        window_from_point_hwnd,
        "window_from_point_hwnd",
        minimum=0,
    )

    kinds: dict[int, tuple[str, bool, str]] = {
        WM_MOUSEMOVE: ("move", False, ""),
        WM_LBUTTONDOWN: ("button_down", True, "left"),
        WM_LBUTTONUP: ("button_up", True, "left"),
        WM_RBUTTONDOWN: ("button_down", True, "right"),
        WM_RBUTTONUP: ("button_up", True, "right"),
        WM_MBUTTONDOWN: ("button_down", True, "middle"),
        WM_MBUTTONUP: ("button_up", True, "middle"),
        WM_XBUTTONDOWN: ("button_down", True, "x"),
        WM_XBUTTONUP: ("button_up", True, "x"),
        WM_MOUSEWHEEL: ("wheel", True, ""),
        WM_MOUSEHWHEEL: ("horizontal_wheel", True, ""),
    }
    kind, mutation, button = kinds.get(message, ("unknown", False, ""))
    if message in {WM_XBUTTONDOWN, WM_XBUTTONUP}:
        x_button = _unsigned_high_word(mouse_data)
        button = "x1" if x_button == 1 else "x2" if x_button == 2 else "x"
    wheel_delta = (
        _signed_high_word(mouse_data)
        if message in {WM_MOUSEWHEEL, WM_MOUSEHWHEEL}
        else 0
    )
    return {
        "time_ns": time_ns,
        "device": "mouse",
        "kind": kind,
        "message": message,
        "mutation": mutation,
        "injected": bool(flags & LLMHF_INJECTED),
        "lower_integrity_injected": bool(
            flags & LLMHF_LOWER_IL_INJECTED
        ),
        "screen_x": x,
        "screen_y": y,
        "wheel_delta": wheel_delta,
        "button": button,
        "os_event_time_ms": os_event_time_ms,
        "foreground_hwnd": foreground_hwnd,
        "foreground_root_hwnd": foreground_root_hwnd,
        "window_from_point_hwnd": window_from_point_hwnd,
    }


def keyboard_event_from_raw(
    message: int,
    *,
    time_ns: int,
    vk_code: int,
    scan_code: int,
    flags: int,
    os_event_time_ms: int,
    foreground_hwnd: int,
    foreground_root_hwnd: int,
    window_from_point_hwnd: int,
) -> dict[str, Any]:
    """Normalize one raw low-level keyboard callback without touching Windows."""

    message = _strict_int(message, "message", minimum=0)
    time_ns = _strict_int(time_ns, "time_ns", minimum=0)
    vk_code = _strict_int(vk_code, "vk_code", minimum=0)
    scan_code = _strict_int(scan_code, "scan_code", minimum=0)
    flags = _strict_int(flags, "flags", minimum=0)
    os_event_time_ms = _strict_int(
        os_event_time_ms,
        "os_event_time_ms",
        minimum=0,
    )
    foreground_hwnd = _strict_int(
        foreground_hwnd,
        "foreground_hwnd",
        minimum=0,
    )
    foreground_root_hwnd = _strict_int(
        foreground_root_hwnd,
        "foreground_root_hwnd",
        minimum=0,
    )
    window_from_point_hwnd = _strict_int(
        window_from_point_hwnd,
        "window_from_point_hwnd",
        minimum=0,
    )

    kinds = {
        WM_KEYDOWN: ("key_down", False),
        WM_KEYUP: ("key_up", True),
        WM_SYSKEYDOWN: ("system_key_down", False),
        WM_SYSKEYUP: ("system_key_up", True),
    }
    kind, message_up = kinds.get(message, ("unknown", False))
    recognized = message in kinds
    return {
        "time_ns": time_ns,
        "device": "keyboard",
        "kind": kind,
        "message": message,
        "mutation": recognized,
        "injected": bool(flags & LLKHF_INJECTED),
        "lower_integrity_injected": bool(
            flags & LLKHF_LOWER_IL_INJECTED
        ),
        "vk_code": vk_code,
        "scan_code": scan_code,
        "up": bool(message_up or (flags & LLKHF_UP)),
        "extended": bool(flags & LLKHF_EXTENDED),
        "alt_down": bool(flags & LLKHF_ALTDOWN),
        "os_event_time_ms": os_event_time_ms,
        "foreground_hwnd": foreground_hwnd,
        "foreground_root_hwnd": foreground_root_hwnd,
        "window_from_point_hwnd": window_from_point_hwnd,
    }


class _WindowsHookApi:
    """Small ctypes boundary kept out of the pure normalization helpers."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("passive input hooks require Windows")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        self.user32.SetWindowsHookExW.argtypes = (
            ctypes.c_int,
            _HOOK_PROC,
            ctypes.c_void_p,
            wintypes.DWORD,
        )
        self.user32.SetWindowsHookExW.restype = ctypes.c_void_p
        self.user32.UnhookWindowsHookEx.argtypes = (ctypes.c_void_p,)
        self.user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        self.user32.CallNextHookEx.argtypes = (
            ctypes.c_void_p,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        self.user32.CallNextHookEx.restype = _LRESULT
        self.user32.GetMessageW.argtypes = (
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        )
        self.user32.GetMessageW.restype = ctypes.c_int
        self.user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
        self.user32.TranslateMessage.restype = wintypes.BOOL
        self.user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
        self.user32.DispatchMessageW.restype = _LRESULT
        self.user32.PeekMessageW.argtypes = (
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.UINT,
        )
        self.user32.PeekMessageW.restype = wintypes.BOOL
        self.user32.PostThreadMessageW.argtypes = (
            wintypes.DWORD,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        self.user32.PostThreadMessageW.restype = wintypes.BOOL
        self.user32.GetForegroundWindow.argtypes = ()
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        self.user32.GetAncestor.restype = wintypes.HWND
        self.user32.WindowFromPoint.argtypes = (wintypes.POINT,)
        self.user32.WindowFromPoint.restype = wintypes.HWND
        self.user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
        self.user32.GetCursorPos.restype = wintypes.BOOL

        self.kernel32.GetCurrentThreadId.argtypes = ()
        self.kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        self.kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
        self.kernel32.GetModuleHandleW.restype = ctypes.c_void_p

    @staticmethod
    def _hwnd(value: Any) -> int:
        return int(value or 0)

    def window_context(self, x: int, y: int) -> tuple[int, int, int]:
        foreground = self._hwnd(self.user32.GetForegroundWindow())
        root = self._hwnd(
            self.user32.GetAncestor(foreground, GA_ROOT)
        ) if foreground else 0
        point_window = self._hwnd(
            self.user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
        )
        return foreground, root, point_window

    def cursor_position(self) -> tuple[int, int]:
        point = wintypes.POINT()
        if not self.user32.GetCursorPos(ctypes.byref(point)):
            return 0, 0
        return int(point.x), int(point.y)

    def call_next(
        self,
        n_code: int,
        w_param: int,
        l_param: int,
    ) -> int:
        try:
            return int(
                self.user32.CallNextHookEx(
                    None,
                    n_code,
                    w_param,
                    l_param,
                )
            )
        except BaseException:
            # Returning zero is a last-resort pass-through result if Windows
            # itself rejects CallNextHookEx.  The callback never returns a
            # nonzero value intended to consume input.
            return 0


class PassiveInputHook:
    """Context-managed passive low-level hook with bounded lifecycle waits."""

    def __init__(
        self,
        *,
        max_events: int = _DEFAULT_MAX_EVENTS,
        startup_timeout_s: float = _DEFAULT_STARTUP_TIMEOUT_S,
        stop_timeout_s: float = _DEFAULT_STOP_TIMEOUT_S,
    ) -> None:
        self._max_events = _strict_int(
            max_events,
            "max_events",
            minimum=1,
        )
        self._startup_timeout_s = _timeout(
            startup_timeout_s,
            "startup_timeout_s",
        )
        self._stop_timeout_s = _timeout(stop_timeout_s, "stop_timeout_s")

        self._events: deque[dict[str, Any]] = deque(maxlen=self._max_events)
        self._events_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._thread_error = ""
        self._running = False
        self._hook_seq = 0
        self._dropped_events = 0
        self._mouse_hook = 0
        self._keyboard_hook = 0

        # Strong references are retained for at least the full hook lifetime.
        self._mouse_callback: Any = None
        self._keyboard_callback: Any = None

    def __enter__(self) -> "PassiveInputHook":
        return self.start()

    def __exit__(
        self,
        _exc_type: Any,
        _exc: Any,
        _traceback: Any,
    ) -> None:
        self.stop()

    def start(self) -> "PassiveInputHook":
        """Install both hooks on a dedicated message-loop thread."""

        if os.name != "nt":
            raise OSError("passive input hooks require Windows")
        with self._state_lock:
            if self._running and self._thread is not None:
                return self
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("passive input hook thread is already starting")
            self._ready = threading.Event()
            self._thread_id = 0
            self._thread_error = ""
            self._running = False
            self._mouse_hook = 0
            self._keyboard_hook = 0
            thread = threading.Thread(
                target=self._thread_main,
                name="WinPilotVNextPassiveInputHook",
                daemon=True,
            )
            self._thread = thread
            thread.start()

        if not self._ready.wait(self._startup_timeout_s):
            self._request_quit()
            thread.join(self._stop_timeout_s)
            raise TimeoutError("passive input hook startup timed out")
        with self._state_lock:
            error = self._thread_error
            running = self._running
        if error or not running:
            thread.join(self._stop_timeout_s)
            raise RuntimeError(
                error or "passive input hook stopped during startup"
            )
        return self

    def stop(self) -> None:
        """Request WM_QUIT and wait a bounded interval for hook removal."""

        with self._state_lock:
            thread = self._thread
        if thread is None:
            return
        if thread.is_alive():
            self._request_quit()
            thread.join(self._stop_timeout_s)
        if thread.is_alive():
            raise TimeoutError("passive input hook stop timed out")
        with self._state_lock:
            error = self._thread_error
            self._thread = None
        if error:
            raise RuntimeError(error)

    def snapshot(self) -> dict[str, Any]:
        """Return an in-memory copy; this method never writes or clears data."""

        with self._events_lock:
            events = copy.deepcopy(list(self._events))
            hook_seq = self._hook_seq
            dropped = self._dropped_events
        with self._state_lock:
            running = bool(
                self._running
                and self._thread is not None
                and self._thread.is_alive()
            )
            thread_error = self._thread_error
        return {
            "format": _FORMAT,
            "running": running,
            "hook_seq": hook_seq,
            "event_count": len(events),
            "dropped_event_count": dropped,
            "thread_error": thread_error,
            "events": events,
        }

    def flush(self) -> dict[str, Any]:
        """Return the same memory snapshot; no filesystem flush exists."""

        return self.snapshot()

    def _record(self, event: dict[str, Any]) -> None:
        with self._events_lock:
            self._hook_seq += 1
            if len(self._events) == self._max_events:
                self._dropped_events += 1
            stored = dict(event)
            stored["hook_seq"] = self._hook_seq
            self._events.append(stored)

    def _request_quit(self) -> bool:
        with self._state_lock:
            thread_id = self._thread_id
        if not thread_id or os.name != "nt":
            return False
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.PostThreadMessageW.argtypes = (
                wintypes.DWORD,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )
            user32.PostThreadMessageW.restype = wintypes.BOOL
            return bool(user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0))
        except BaseException:
            return False

    def _mouse_proc(
        self,
        api: _WindowsHookApi,
        n_code: int,
        w_param: int,
        l_param: int,
    ) -> int:
        try:
            if n_code >= HC_ACTION:
                raw = ctypes.cast(
                    l_param,
                    ctypes.POINTER(_MSLLHOOKSTRUCT),
                ).contents
                x, y = int(raw.pt.x), int(raw.pt.y)
                foreground, root, point_window = api.window_context(x, y)
                self._record(
                    mouse_event_from_raw(
                        int(w_param),
                        time_ns=time.time_ns(),
                        x=x,
                        y=y,
                        mouse_data=int(raw.mouseData),
                        flags=int(raw.flags),
                        os_event_time_ms=int(raw.time),
                        foreground_hwnd=foreground,
                        foreground_root_hwnd=root,
                        window_from_point_hwnd=point_window,
                    )
                )
        except BaseException:
            pass
        return api.call_next(n_code, w_param, l_param)

    def _keyboard_proc(
        self,
        api: _WindowsHookApi,
        n_code: int,
        w_param: int,
        l_param: int,
    ) -> int:
        try:
            if n_code >= HC_ACTION:
                raw = ctypes.cast(
                    l_param,
                    ctypes.POINTER(_KBDLLHOOKSTRUCT),
                ).contents
                x, y = api.cursor_position()
                foreground, root, point_window = api.window_context(x, y)
                self._record(
                    keyboard_event_from_raw(
                        int(w_param),
                        time_ns=time.time_ns(),
                        vk_code=int(raw.vkCode),
                        scan_code=int(raw.scanCode),
                        flags=int(raw.flags),
                        os_event_time_ms=int(raw.time),
                        foreground_hwnd=foreground,
                        foreground_root_hwnd=root,
                        window_from_point_hwnd=point_window,
                    )
                )
        except BaseException:
            pass
        return api.call_next(n_code, w_param, l_param)

    def _thread_main(self) -> None:
        api: _WindowsHookApi | None = None
        mouse_hook = 0
        keyboard_hook = 0
        ready_published = False
        try:
            api = _WindowsHookApi()
            thread_id = int(api.kernel32.GetCurrentThreadId())
            message = wintypes.MSG()
            # Force creation of the thread message queue before start() may
            # need to post WM_QUIT.
            api.user32.PeekMessageW(
                ctypes.byref(message),
                None,
                0,
                0,
                PM_NOREMOVE,
            )
            mouse_callback = _HOOK_PROC(
                lambda n, w, l: self._mouse_proc(api, n, w, l)
            )
            keyboard_callback = _HOOK_PROC(
                lambda n, w, l: self._keyboard_proc(api, n, w, l)
            )
            module = api.kernel32.GetModuleHandleW(None)
            mouse_hook = int(
                api.user32.SetWindowsHookExW(
                    WH_MOUSE_LL,
                    mouse_callback,
                    module,
                    0,
                )
                or 0
            )
            if not mouse_hook:
                raise OSError(
                    ctypes.get_last_error(),
                    "SetWindowsHookExW(WH_MOUSE_LL) failed",
                )
            keyboard_hook = int(
                api.user32.SetWindowsHookExW(
                    WH_KEYBOARD_LL,
                    keyboard_callback,
                    module,
                    0,
                )
                or 0
            )
            if not keyboard_hook:
                raise OSError(
                    ctypes.get_last_error(),
                    "SetWindowsHookExW(WH_KEYBOARD_LL) failed",
                )
            with self._state_lock:
                self._thread_id = thread_id
                self._mouse_callback = mouse_callback
                self._keyboard_callback = keyboard_callback
                self._mouse_hook = mouse_hook
                self._keyboard_hook = keyboard_hook
                self._running = True
            self._ready.set()
            ready_published = True

            while True:
                status = int(
                    api.user32.GetMessageW(
                        ctypes.byref(message),
                        None,
                        0,
                        0,
                    )
                )
                if status == 0:
                    break
                if status < 0:
                    raise OSError(
                        ctypes.get_last_error(),
                        "passive input hook GetMessageW failed",
                    )
                api.user32.TranslateMessage(ctypes.byref(message))
                api.user32.DispatchMessageW(ctypes.byref(message))
        except BaseException as exc:
            with self._state_lock:
                self._thread_error = f"{type(exc).__name__}: {exc}"
        finally:
            if api is not None:
                if keyboard_hook:
                    try:
                        api.user32.UnhookWindowsHookEx(
                            ctypes.c_void_p(keyboard_hook)
                        )
                    except BaseException:
                        pass
                if mouse_hook:
                    try:
                        api.user32.UnhookWindowsHookEx(
                            ctypes.c_void_p(mouse_hook)
                        )
                    except BaseException:
                        pass
            with self._state_lock:
                self._running = False
                self._thread_id = 0
                self._mouse_hook = 0
                self._keyboard_hook = 0
            if not ready_published:
                self._ready.set()


__all__ = [
    "PassiveInputHook",
    "keyboard_event_from_raw",
    "mouse_event_from_raw",
]
