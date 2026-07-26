#!/usr/bin/env python3
"""Run generic visual gestures through the visible Electron client.

The task is entered through the real client with cold start enabled.  After the
Run button is clicked, the controlled browser target is returned to the
foreground so the desktop agent receives the same visible scene a user would.
Success is decided by trusted browser pointer events, never by the agent's own
completion text or by a direct backend invocation.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import psutil
import websocket
import win32api
import win32con
import win32gui
import win32ui
from PIL import Image, ImageGrab


WORKSPACE = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import client_e2e_run as client_harness  # noqa: E402


TARGET_SERVER = (
    WORKSPACE
    / "python"
    / "winpilot_home"
    / "eval_targets"
    / "visual_interaction_server.py"
)
DEFAULT_OUTPUT = WORKSPACE / "output" / "playwright" / "client-visual-e2e"

# The benchmark prompt deliberately carries no interaction recipe.  Rules live
# only in the visible randomized page; a passing run must understand that frame
# and its later state changes just as a user would.
VISUAL_TEST_TASK = "完成当前界面上的测试。"

VISUAL_CASES: dict[str, dict[str, Any]] = {
    "click-double-click": {
        "scenario": "click_double_click",
        "task": VISUAL_TEST_TASK,
        "seed": 22015,
        "max_steps": 24,
        "timeout": 360,
    },
    "scroll-bidirectional": {
        "scenario": "bidirectional_scroll",
        "task": VISUAL_TEST_TASK,
        "seed": 22022,
        "max_steps": 28,
        "timeout": 420,
    },
    "text-hotkey": {
        "scenario": "text_hotkey",
        "task": VISUAL_TEST_TASK,
        "seed": 22025,
        "max_steps": 24,
        "timeout": 360,
    },
    "stop-cancel": {
        "scenario": "cancel_guard",
        "task": VISUAL_TEST_TASK,
        "seed": 22026,
        "max_steps": 24,
        "timeout": 240,
        "expected": "canceled",
        # Longer than both the 75s per-attempt timeout and the runtime's 120s
        # effective total timeout when request_total_timeout_s is unset.
        "post_cancel_quiet_seconds": 125,
    },
    "drag-fit": {
        "scenario": "drag_fit",
        "task": VISUAL_TEST_TASK,
        "seed": 271828,
        "max_steps": 30,
        "timeout": 360,
    },
    "slider": {
        "scenario": "slider",
        "task": VISUAL_TEST_TASK,
        "seed": 314159,
        "max_steps": 30,
        "timeout": 360,
    },
    "dynamic-hold": {
        "scenario": "dynamic_hold",
        "task": VISUAL_TEST_TASK,
        "seed": 161803,
        "max_steps": 36,
        "timeout": 420,
    },
    # These deterministic seeds exercise every mirrored/reversed corridor.
    "corridor-v0": {
        "scenario": "corridor_drag",
        "task": VISUAL_TEST_TASK,
        "seed": 7,
        "expected_variant": 0,
        "max_steps": 36,
        "timeout": 420,
    },
    "corridor-v1": {
        "scenario": "corridor_drag",
        "task": VISUAL_TEST_TASK,
        "seed": 12,
        "expected_variant": 1,
        "max_steps": 36,
        "timeout": 420,
    },
    "game-drag-v2": {
        "scenario": "corridor_drag",
        "task": VISUAL_TEST_TASK,
        "seed": 1,
        "expected_variant": 2,
        "max_steps": 36,
        "timeout": 420,
    },
    "corridor-v3": {
        "scenario": "corridor_drag",
        "task": VISUAL_TEST_TASK,
        "seed": 4,
        "expected_variant": 3,
        "max_steps": 36,
        "timeout": 420,
    },
}

SUITES = {
    "smoke": ("drag-fit", "slider", "dynamic-hold", "game-drag-v2"),
    "pr15-legacy": (
        "click-double-click",
        "scroll-bidirectional",
        "text-hotkey",
        "dynamic-hold",
        "stop-cancel",
    ),
    "all": tuple(VISUAL_CASES),
}

MODIFIER_KEYS = {
    "left_ctrl": win32con.VK_LCONTROL,
    "right_ctrl": win32con.VK_RCONTROL,
    "left_shift": win32con.VK_LSHIFT,
    "right_shift": win32con.VK_RSHIFT,
    "left_alt": win32con.VK_LMENU,
    "right_alt": win32con.VK_RMENU,
    "left_win": win32con.VK_LWIN,
    "right_win": win32con.VK_RWIN,
}
_MOUSE_MESSAGES = {
    0x0200: "mouse_move",
    0x0201: "left_down",
    0x0202: "left_up",
    0x0204: "right_down",
    0x0205: "right_up",
    0x0207: "middle_down",
    0x0208: "middle_up",
    0x020A: "wheel",
    0x020B: "x_down",
    0x020C: "x_up",
    0x020E: "horizontal_wheel",
}
_KEYBOARD_MESSAGES = {
    0x0100: "key_down",
    0x0101: "key_up",
    0x0104: "system_key_down",
    0x0105: "system_key_up",
}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _json_get(url: str, *, timeout: float = 3.0) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-store"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise RuntimeError(f"expected object from {url}")
    return value


def _wait_json(url: str, predicate, *, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            last = _json_get(url)
            if predicate(last):
                return last
        except Exception:
            pass
        time.sleep(0.15)
    raise RuntimeError(f"timed out waiting for {url}; last={last}")


def _event_stream_marker(state: dict[str, Any]) -> tuple[int, Any, Any]:
    events = state.get("events") or []
    if not isinstance(events, list) or not events:
        return (0, None, None)
    tail = events[-1] if isinstance(events[-1], dict) else {}
    return (len(events), tail.get("received_at"), tail.get("type"))


def _settle_external_state(
    url: str,
    initial: dict[str, Any],
    *,
    quiet_seconds: float = 0.8,
    timeout: float = 4.0,
) -> tuple[dict[str, Any], bool, str | None]:
    """Drain queued reports so duplicate input after success cannot be hidden."""

    latest = initial
    marker = _event_stream_marker(latest)
    quiet_since = time.monotonic()
    deadline = quiet_since + timeout
    last_error: str | None = None
    while time.monotonic() < deadline:
        time.sleep(0.1)
        try:
            observed = _json_get(url)
            last_error = None
        except Exception as error:
            last_error = f"{type(error).__name__}: {error}"
            quiet_since = time.monotonic()
            continue
        latest = observed
        observed_marker = _event_stream_marker(observed)
        if observed_marker != marker:
            marker = observed_marker
            quiet_since = time.monotonic()
            continue
        if time.monotonic() - quiet_since >= quiet_seconds:
            return latest, True, None
    return latest, False, last_error


def _modifier_state() -> dict[str, bool]:
    """Read physical modifier state without changing the desktop."""

    return {
        name: bool(win32api.GetAsyncKeyState(virtual_key) & 0x8000)
        for name, virtual_key in MODIFIER_KEYS.items()
    }


def _case_events(
    state: dict[str, Any],
    *,
    case_id: str,
    scenario: str,
    seed: int,
) -> list[dict[str, Any]]:
    return [
        item
        for item in (state.get("events") or [])
        if isinstance(item, dict)
        and item.get("case_id") == case_id
        and item.get("scenario") == scenario
        and int(item.get("seed") or 0) == int(seed)
    ]


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _valid_box(value: Any) -> bool:
    return isinstance(value, dict) and all(
        _is_number(value.get(key))
        for key in ("left", "top", "right", "bottom", "width", "height")
    )


def _point_inside(event: dict[str, Any], target: dict[str, Any]) -> bool:
    return bool(
        _valid_box(target)
        and _is_number(event.get("client_x"))
        and _is_number(event.get("client_y"))
        and target["left"] <= event["client_x"] < target["right"]
        and target["top"] <= event["client_y"] < target["bottom"]
    )


def _git_revision(path: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        text=True,
        encoding="utf-8",
    ).strip()


def harness_metadata() -> dict[str, Any]:
    artifacts = [
        WORKSPACE / "scripts" / "client_e2e_run.py",
        Path(__file__).resolve(),
        TARGET_SERVER,
        TARGET_SERVER.with_name("visual_interaction_target.html"),
    ]
    return {
        "workspace": str(WORKSPACE),
        "revision": _git_revision(WORKSPACE),
        "tracked_worktree_clean": not subprocess.check_output(
            [
                "git", "-C", str(WORKSPACE), "status",
                "--porcelain", "--untracked-files=normal",
            ],
            text=True,
            encoding="utf-8",
        ).strip(),
        "branch": subprocess.check_output(
            ["git", "-C", str(WORKSPACE), "branch", "--show-current"],
            text=True,
            encoding="utf-8",
        ).strip(),
        "artifact_sha256": {
            str(path.relative_to(WORKSPACE)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in artifacts
        },
    }


class RuntimeEventStream:
    """Continuously retain BUS events even after the outer recorder closes."""

    def __init__(self, runtime_url: str, token: str) -> None:
        scheme = "wss" if runtime_url.startswith("https://") else "ws"
        suffix = runtime_url.split("://", 1)[-1].rstrip("/")
        self.url = f"{scheme}://{suffix}/ws/events?token={quote(token)}"
        self.events: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.connected_at = 0.0
        self.disconnected_at = 0.0
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._socket: websocket.WebSocket | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._socket = websocket.create_connection(self.url, timeout=4)
        self._socket.settimeout(1)
        self.connected_at = time.time()
        self._thread = threading.Thread(
            target=self._read_loop,
            name="pr15-runtime-event-stream",
            daemon=True,
        )
        self._thread.start()

    def _read_loop(self) -> None:
        assert self._socket is not None
        while not self._stop.is_set():
            try:
                raw = self._socket.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except Exception as error:
                if not self._stop.is_set():
                    self.errors.append(f"{type(error).__name__}: {error}")
                break
            try:
                value = json.loads(raw)
                if isinstance(value, dict):
                    with self._lock:
                        self.events.append(
                            {**value, "_observed_at": time.time()},
                        )
                        self.events = self.events[-8000:]
            except Exception as error:
                self.errors.append(f"{type(error).__name__}: {error}")
        self.disconnected_at = time.time()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            events = [dict(item) for item in self.events]
        return {
            "connected_at": self.connected_at,
            "disconnected_at": self.disconnected_at,
            "events": events,
            "errors": list(self.errors),
        }

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        if self._socket is not None:
            try:
                self._socket.close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=3)
        return self.snapshot()


class _Point(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _MouseHookData(ctypes.Structure):
    _fields_ = [
        ("pt", _Point),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _KeyboardHookData(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class LowLevelInputMonitor:
    """Passively audit host input during the post-cancel quiet window."""

    _callback_type = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t,
        ctypes.c_int,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.started_at = 0.0
        self.stopped_at = 0.0
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._mouse_callback: Any = None
        self._keyboard_callback: Any = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name="pr15-low-level-input-monitor",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("low-level input monitor did not start")
        if self.errors:
            raise RuntimeError("; ".join(self.errors))
        self.started_at = time.time()

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int,
            self._callback_type,
            wintypes.HINSTANCE,
            wintypes.DWORD,
        ]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.CallNextHookEx.argtypes = [
            wintypes.HHOOK,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        self._thread_id = int(kernel32.GetCurrentThreadId())

        def mouse_callback(
            code: int,
            message: int,
            data_pointer: int,
        ) -> int:
            if code >= 0:
                data = ctypes.cast(
                    data_pointer,
                    ctypes.POINTER(_MouseHookData),
                ).contents
                self.events.append({
                    "at": time.time(),
                    "kind": "mouse",
                    "message": _MOUSE_MESSAGES.get(int(message), hex(int(message))),
                    "x": int(data.pt.x),
                    "y": int(data.pt.y),
                    "injected": bool(int(data.flags) & 0x1),
                    "lower_integrity_injected": bool(int(data.flags) & 0x2),
                    "foreground_hwnd": int(user32.GetForegroundWindow() or 0),
                })
            return int(user32.CallNextHookEx(None, code, message, data_pointer))

        def keyboard_callback(
            code: int,
            message: int,
            data_pointer: int,
        ) -> int:
            if code >= 0:
                data = ctypes.cast(
                    data_pointer,
                    ctypes.POINTER(_KeyboardHookData),
                ).contents
                self.events.append({
                    "at": time.time(),
                    "kind": "keyboard",
                    "message": _KEYBOARD_MESSAGES.get(int(message), hex(int(message))),
                    "vk_code": int(data.vkCode),
                    "scan_code": int(data.scanCode),
                    "injected": bool(int(data.flags) & 0x10),
                    "lower_integrity_injected": bool(int(data.flags) & 0x2),
                    "foreground_hwnd": int(user32.GetForegroundWindow() or 0),
                })
            return int(user32.CallNextHookEx(None, code, message, data_pointer))

        self._mouse_callback = self._callback_type(mouse_callback)
        self._keyboard_callback = self._callback_type(keyboard_callback)
        mouse_hook = user32.SetWindowsHookExW(
            14, self._mouse_callback, None, 0,
        )
        keyboard_hook = user32.SetWindowsHookExW(
            13, self._keyboard_callback, None, 0,
        )
        if not mouse_hook or not keyboard_hook:
            self.errors.append(
                f"SetWindowsHookExW failed: {ctypes.get_last_error()}",
            )
            if mouse_hook:
                user32.UnhookWindowsHookEx(mouse_hook)
            if keyboard_hook:
                user32.UnhookWindowsHookEx(keyboard_hook)
            self._ready.set()
            return
        self._ready.set()
        message = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            user32.UnhookWindowsHookEx(mouse_hook)
            user32.UnhookWindowsHookEx(keyboard_hook)

    def stop(self) -> dict[str, Any]:
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(
                self._thread_id,
                win32con.WM_QUIT,
                0,
                0,
            )
        if self._thread is not None:
            self._thread.join(timeout=3)
        self.stopped_at = time.time()
        return {
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "events": [dict(item) for item in self.events],
            "errors": list(self.errors),
        }


def _browser_executable() -> Path:
    explicit = os.environ.get("WINPILOT_REAL_VISUAL_BROWSER", "").strip()
    candidates = (
        explicit,
        shutil.which("msedge") or "",
        shutil.which("chrome") or "",
        str(Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe"),
        str(Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe"),
        str(Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe"),
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe"),
    )
    for candidate in candidates:
        path = Path(candidate) if candidate else None
        if path is not None and path.is_file():
            return path
    raise RuntimeError("Edge or Chrome was not found")


def _find_window(title: str, *, timeout: float = 25.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matches: list[int] = []

        def visit(hwnd: int, _unused: Any) -> None:
            if win32gui.IsWindowVisible(hwnd) and title in win32gui.GetWindowText(hwnd):
                matches.append(int(hwnd))

        win32gui.EnumWindows(visit, None)
        if matches:
            return matches[-1]
        time.sleep(0.2)
    raise RuntimeError(f"visible target window was not found: {title}")


def _focus_window(hwnd: int) -> dict[str, Any]:
    if not hwnd or not win32gui.IsWindow(hwnd):
        raise RuntimeError("target window no longer exists")
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.BringWindowToTop(hwnd)
    focused = False
    errors: list[str] = []
    for _attempt in range(3):
        try:
            # The temporary ALT press is the generic Windows foreground-unlock
            # path; it does not send input into the evaluation surface.
            win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
            win32gui.SetForegroundWindow(hwnd)
        except Exception as error:
            errors.append(f"{type(error).__name__}: {error}")
        finally:
            win32api.keybd_event(
                win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0,
            )
        time.sleep(0.18)
        focused = int(win32gui.GetForegroundWindow() or 0) == int(hwnd)
        if focused:
            break
    if not focused:
        raise RuntimeError(f"could not focus target window; errors={errors}")
    return {
        "target_hwnd": int(hwnd),
        "foreground_hwnd": int(win32gui.GetForegroundWindow() or 0),
        "focused": True,
        "title": win32gui.GetWindowText(hwnd),
    }


def _capture_window(hwnd: int, path: Path) -> str:
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    ImageGrab.grab(
        bbox=(left, top, right, bottom), all_screens=True,
    ).save(path)
    return str(path)


def _image_visually_nonblank(path: Path) -> bool:
    try:
        with Image.open(path) as captured:
            sample = captured.convert("RGB")
            sample.thumbnail((96, 96))
            extrema = sample.getextrema()
        return bool(
            extrema
            and any(
                channel_min != channel_max
                for channel_min, channel_max in extrema
            )
        )
    except Exception:
        return False


def _capture_window_without_focus(hwnd: int, path: Path) -> dict[str, Any]:
    """Render an occluded window without activating it or generating input."""

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = max(1, right - left), max(1, bottom - top)
    window_dc_handle = win32gui.GetWindowDC(hwnd)
    window_dc = win32ui.CreateDCFromHandle(window_dc_handle)
    memory_dc = window_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(window_dc, width, height)
    memory_dc.SelectObject(bitmap)
    printed = False
    error = ""
    visually_nonblank = False
    try:
        ctypes.windll.user32.PrintWindow.argtypes = [
            wintypes.HWND,
            wintypes.HDC,
            wintypes.UINT,
        ]
        ctypes.windll.user32.PrintWindow.restype = wintypes.BOOL
        printed = bool(
            ctypes.windll.user32.PrintWindow(
                hwnd,
                memory_dc.GetSafeHdc(),
                2,
            ),
        )
        bitmap.SaveBitmapFile(memory_dc, str(path))
        visually_nonblank = _image_visually_nonblank(path)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        memory_dc.DeleteDC()
        window_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, window_dc_handle)
    return {
        "path": str(path),
        "print_window_succeeded": printed,
        "visually_nonblank": visually_nonblank,
        "error": error,
        "foreground_unchanged": True,
    }


def _terminate_tree(process: subprocess.Popen[Any] | None) -> None:
    if process is None:
        return
    try:
        root = psutil.Process(process.pid)
    except psutil.Error:
        return
    descendants = root.children(recursive=True)
    for child in reversed(descendants):
        try:
            child.terminate()
        except psutil.Error:
            pass
    try:
        root.terminate()
    except psutil.Error:
        pass
    _gone, alive = psutil.wait_procs([*descendants, root], timeout=4)
    for item in alive:
        try:
            item.kill()
        except psutil.Error:
            pass


def external_verdict(
    state: dict[str, Any],
    *,
    case_id: str,
    scenario: str,
    seed: int,
    expected_variant: int | None = None,
) -> dict[str, Any]:
    """Validate final state and settled trusted browser event chains."""

    errors: list[str] = []
    events = sorted(
        _case_events(state, case_id=case_id, scenario=scenario, seed=seed),
        key=lambda item: int((item.get("details") or {}).get("event_sequence") or 0),
    )
    ready_events = [item for item in events if item.get("type") == "page_ready"]
    success_events = [
        item for item in events if item.get("type") == "scenario_success"
    ]
    event = state.get("success_event") or {}
    details = event.get("details") or {}
    if state.get("success") is not True:
        errors.append("no trusted scenario_success event")
    if state.get("case_id") != case_id:
        errors.append("case id mismatch")
    if state.get("scenario") != scenario:
        errors.append("scenario mismatch")
    if int(state.get("seed") or 0) != int(seed):
        errors.append("seed mismatch")
    if len(ready_events) != 1:
        errors.append("case does not have exactly one page_ready identity")
    if len(success_events) != 1 or success_events[0] != event:
        errors.append("case does not have one unique terminal success")
    if event.get("trusted") is not True or event.get("succeeded") is not True:
        errors.append("success event is not trusted and successful")
    if details.get("succeeded") is not True:
        errors.append("scenario details are not successful")

    ready = ready_events[0] if len(ready_events) == 1 else {}
    ready_details = ready.get("details") or {}
    terminal_sequence = int(details.get("event_sequence") or 0)
    after_terminal = [
        item
        for item in events
        if int((item.get("details") or {}).get("event_sequence") or 0)
        > terminal_sequence
    ]
    move_count = int(event.get("move_count") or 0)
    duplicate_input_count = 0

    if scenario == "click_double_click":
        causal_types = {"pointerdown", "pointerup", "click", "dblclick", "wrong_click"}
        causal = [item for item in events if item.get("type") in causal_types]
        expected_chain = (
            ("pointerdown", "single-target"),
            ("pointerup", "single-target"),
            ("click", "single-target"),
            ("pointerdown", "double-target"),
            ("pointerup", "double-target"),
            ("click", "double-target"),
            ("pointerdown", "double-target"),
            ("pointerup", "double-target"),
            ("click", "double-target"),
            ("dblclick", "double-target"),
        )
        observed_chain = [
            (item.get("type"), (item.get("details") or {}).get("target_id"))
            for item in causal
        ]
        if observed_chain != list(expected_chain):
            errors.append("trusted click/double-click causal chain is not exact")
        single_box = ready_details.get("single_target") or {}
        double_box = ready_details.get("double_target") or {}
        for item, (_event_type, target_id) in zip(causal, expected_chain):
            expected_box = single_box if target_id == "single-target" else double_box
            if item.get("trusted") is not True:
                errors.append("click chain contains an untrusted input event")
                break
            if not _point_inside(item, expected_box):
                errors.append("click chain coordinates are outside the identified target")
                break
        expected_counts = {
            "pointerdown": 3,
            "pointerup": 3,
            "click": 3,
            "dblclick": 1,
        }
        for event_type, expected_count in expected_counts.items():
            observed_count = sum(item.get("type") == event_type for item in causal)
            duplicate_input_count += max(0, observed_count - expected_count)
            if observed_count != expected_count:
                errors.append(
                    f"{event_type} count is {observed_count}, expected {expected_count}",
                )
        duplicate_input_count += sum(
            item.get("type") == "wrong_click" for item in causal
        )
        if not (
            details.get("target_id") == "double-target"
            and int(details.get("single_click_count") or 0) == 1
            and int(details.get("double_click_event_count") or 0) == 2
            and int(details.get("dblclick_count") or 0) == 1
            and int(details.get("wrong_click_count") or 0) == 0
            and _point_inside(event, double_box)
        ):
            errors.append("click terminal identity, coordinates, or counts are wrong")
        if any(item.get("type") in causal_types for item in after_terminal):
            errors.append("trusted input arrived after click success")
            duplicate_input_count += 1
    elif scenario == "bidirectional_scroll":
        pane = ready_details.get("pane") or {}
        wheels = [item for item in events if item.get("type") == "wheel"]
        checkpoints = [
            item
            for item in events
            if item.get("type") == "scroll_checkpoint"
            and (item.get("details") or {}).get("checkpoint") == "bottom"
        ]
        scroll_states = [
            item for item in events if item.get("type") == "scroll_state"
        ]
        if not wheels:
            errors.append("scroll case has no wheel events")
        if len(checkpoints) != 1:
            errors.append("scroll case has no unique bottom checkpoint")
        checkpoint_sequence = int(
            ((checkpoints[0] if checkpoints else {}).get("details") or {}).get(
                "event_sequence",
            )
            or 0,
        )
        wrong_direction = 0
        for item in wheels:
            value = item.get("details") or {}
            sequence = int(value.get("event_sequence") or 0)
            delta = value.get("delta_y")
            if item.get("trusted") is not True:
                errors.append("scroll case contains an untrusted wheel")
                break
            if value.get("target_id") != "scroll-pane" or not _point_inside(item, pane):
                errors.append("wheel target identity or coordinates are wrong")
                break
            direction_ok = bool(
                _is_number(delta)
                and (
                    sequence < checkpoint_sequence
                    and delta > 0
                    and value.get("direction") == "down"
                    or sequence > checkpoint_sequence
                    and delta < 0
                    and value.get("direction") == "up"
                )
            )
            if not direction_ok:
                wrong_direction += 1
        duplicate_input_count = wrong_direction
        if wrong_direction:
            errors.append("wheel directions are not a single down-then-up transaction")
        if not any((item.get("details") or {}).get("delta_y", 0) > 0 for item in wheels):
            errors.append("no trusted downward wheel event")
        if not any((item.get("details") or {}).get("delta_y", 0) < 0 for item in wheels):
            errors.append("no trusted upward wheel event")
        observed_tops = [
            int((item.get("details") or {}).get("scroll_top") or 0)
            for item in scroll_states
        ]
        start_top = int(ready_details.get("start_scroll_top") or 0)
        if not observed_tops or max(observed_tops) - start_top < 250:
            errors.append("trusted wheel events did not produce a real downward scrollTop change")
        if not (
            int(details.get("down_wheel_count") or 0) > 0
            and int(details.get("up_wheel_count") or 0) > 0
            and int(details.get("observed_max_scroll_top") or 0) - start_top >= 250
            and int(details.get("final_scroll_top") or 0) <= 24
            and details.get("scroll_top_changed") is True
        ):
            errors.append("final bidirectional scrollTop state is not exact")
        if any(item.get("type") == "wheel" for item in after_terminal):
            errors.append("wheel input arrived after scroll success")
            duplicate_input_count += 1
    elif scenario == "text_hotkey":
        expected = ready_details.get("expected_text")
        input_events = [item for item in events if item.get("type") == "input"]
        before_events = [
            item for item in events if item.get("type") == "beforeinput"
        ]
        forbidden_types = {
            "paste", "cut", "drop",
            "compositionstart", "compositionupdate", "compositionend",
        }
        forbidden = [item for item in events if item.get("type") in forbidden_types]
        if forbidden:
            errors.append("text entry used clipboard, drop, cut, or composition input")
        if not isinstance(expected, str) or expected.count("\n") != 1:
            errors.append("page-ready expected text identity is invalid")
            expected = ""
        if not input_events or len(before_events) != len(input_events):
            errors.append("beforeinput/input evidence is not exactly paired")
        values = [
            str((item.get("details") or {}).get("value_after") or "")
            for item in input_events
        ]
        if any(item.get("trusted") is not True for item in [*before_events, *input_events]):
            errors.append("text entry contains untrusted input events")
        if any(
            (item.get("details") or {}).get("target_id") != "exact-editor"
            for item in [*before_events, *input_events]
        ):
            errors.append("text input target identity is wrong")
        allowed_input_types = {"insertText", "insertLineBreak"}
        if any(
            str((item.get("details") or {}).get("input_type") or "")
            not in allowed_input_types
            for item in [*before_events, *input_events]
        ):
            errors.append("text entry used a non-typing mutation mechanism")
        prefix_chain_ok = bool(
            values
            and all(
                expected.startswith(value)
                and (index == 0 or value.startswith(values[index - 1]))
                and (index == 0 or value != values[index - 1])
                for index, value in enumerate(values)
            )
            and values[-1] == expected
        )
        if not prefix_chain_ok:
            errors.append("input events do not reconstruct the exact two-line text")
            duplicate_input_count += 1
        relevant_keys = [
            item
            for item in events
            if (
                str((item.get("details") or {}).get("key") or "").casefold()
                == "control"
                or (
                    str((item.get("details") or {}).get("key") or "").casefold()
                    == "enter"
                    and (item.get("details") or {}).get("ctrl_key") is True
                )
            )
        ]
        expected_keys = (
            ("keydown", "control", True),
            ("hotkey_keydown", "enter", True),
            ("hotkey_enter_keyup", "enter", True),
            ("modifier_keyup", "control", False),
        )
        observed_keys = [
            (
                item.get("type"),
                str((item.get("details") or {}).get("key") or "").casefold(),
                (item.get("details") or {}).get("ctrl_key"),
            )
            for item in relevant_keys
        ]
        if observed_keys != list(expected_keys):
            errors.append("Ctrl+Enter and modifier release sequence is not exact")
            duplicate_input_count += max(1, len(relevant_keys) - len(expected_keys))
        if any(
            item.get("trusted") is not True
            or (item.get("details") or {}).get("target_id") != "exact-editor"
            or (item.get("details") or {}).get("alt_key") is not False
            or (item.get("details") or {}).get("shift_key") is not False
            or (item.get("details") or {}).get("meta_key") is not False
            or (item.get("details") or {}).get("repeat") is not False
            for item in relevant_keys
        ):
            errors.append("hotkey events are not trusted on the exact editor")
        if not (
            details.get("final_text") == expected
            and details.get("value") == expected
            and details.get("modifier_released") is True
            and int(details.get("hotkey_down_count") or 0) == 1
            and int(details.get("hotkey_enter_up_count") or 0) == 1
            and int(details.get("control_release_count") or 0) == 1
            and details.get("duplicate_input_count") == 0
        ):
            errors.append("final text, hotkey count, or modifier state is wrong")
        if any(
            item.get("type") in {"beforeinput", "input", *forbidden_types}
            for item in after_terminal
        ):
            errors.append("text changed after terminal success")
            duplicate_input_count += 1
    elif scenario == "drag_fit":
        token = details.get("token") or {}
        target = details.get("target") or {}
        if move_count < 2:
            errors.append("drag did not produce enough pointer moves")
        if not _valid_box(token) or not _valid_box(target):
            errors.append("missing drag placement geometry")
        elif not (
            token["left"] >= target["left"]
            and token["top"] >= target["top"]
            and token["right"] <= target["right"]
            and token["bottom"] <= target["bottom"]
        ):
            errors.append("object is not completely inside target")
    elif scenario == "slider":
        goal = details.get("goal") or {}
        center = int(details.get("center_x") or -1)
        if move_count < 2:
            errors.append("slider did not produce enough pointer moves")
        if not goal or not int(goal.get("left") or 0) <= center <= int(goal.get("right") or -1):
            errors.append("slider center is outside goal band")
    elif scenario == "corridor_drag":
        if move_count < 3:
            errors.append("corridor gesture did not produce enough pointer moves")
        if details.get("path_valid") is not True:
            errors.append("gesture left the visible corridor")
        clearance_value = details.get("min_clearance")
        if clearance_value is None or int(clearance_value) < 0:
            errors.append("dragged object footprint has negative corridor clearance")
        if len(details.get("path_points") or []) < 3:
            errors.append("corridor path evidence is incomplete")
        if expected_variant is not None:
            raw_variant = ready_details.get("variant")
            variant = int(raw_variant) if raw_variant is not None else -1
            if variant != int(expected_variant):
                errors.append(
                    f"expected corridor variant {expected_variant}, observed {variant}",
                )
    elif scenario == "dynamic_hold":
        down_events = [
            item
            for item in events
            if item.get("type") == "pointerdown" and item.get("trusted") is True
        ]
        checkpoint_events = [
            item
            for item in events
            if item.get("type") == "checkpoint_reached" and item.get("trusted") is True
        ]
        release_events = [
            item
            for item in events
            if item.get("type") == "pointerup" and item.get("trusted") is True
        ]
        duplicate_input_count = max(0, len(down_events) - 1) + max(
            0,
            len(release_events) - 1,
        )
        if len(down_events) != 1 or int(details.get("down_count") or 0) != 1:
            errors.append("dynamic gesture was not completed with exactly one held press")
        if len(checkpoint_events) != 1 or details.get("checkpoint_reached") is not True:
            errors.append("checkpoint was not reached once while the pointer was held")
        if details.get("goal_revealed_after_down") is not True:
            errors.append("goal was not revealed after the trusted pointerdown")
        if details.get("inside_goal") is not True:
            errors.append("pointer was not released inside the newly revealed goal")
        if details.get("completion_event") != "pointerup":
            errors.append("dynamic success was not emitted by pointerup")
        if len(release_events) != 1:
            errors.append("dynamic gesture has no unique trusted pointerup evidence")
        else:
            release_details = release_events[0].get("details") or {}
            if (
                release_events[0].get("succeeded") is not True
                or release_details.get("succeeded") is not True
                or int(release_details.get("down_count") or 0) != 1
                or release_details.get("checkpoint_reached") is not True
                or release_details.get("goal_revealed_after_down") is not True
                or release_details.get("inside_goal") is not True
                or release_details.get("completion_event") != "pointerup"
            ):
                errors.append("trusted pointerup did not complete inside the final goal")
        if move_count < 2:
            errors.append("dynamic held gesture did not span multiple pointer moves")
        if any(
            item.get("type") in {"pointerdown", "pointerup", "pointermove"}
            for item in after_terminal
        ):
            errors.append("pointer input arrived after dynamic-hold success")
            duplicate_input_count += 1
    else:
        errors.append(f"unsupported scenario: {scenario}")

    return {
        "passed": not errors,
        "valid": True,
        "oracle_status": "conclusive",
        "errors": errors,
        "trusted": event.get("trusted") is True,
        "pointer_type": event.get("pointer_type"),
        "move_count": move_count,
        "action_count": None,
        "duplicate_input_count": duplicate_input_count,
        "details": details,
    }


def _trace_action_summary(client_result: dict[str, Any]) -> dict[str, Any]:
    trace_file = Path(
        str((client_result.get("trace_binding") or {}).get("trace_file") or ""),
    )
    starts: list[dict[str, Any]] = []
    parse_errors: list[str] = []
    if trace_file.is_file():
        for line_number, raw_line in enumerate(
            trace_file.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not raw_line.strip():
                continue
            try:
                event = json.loads(raw_line)
            except Exception as error:
                parse_errors.append(
                    f"line {line_number}: {type(error).__name__}: {error}",
                )
                continue
            if isinstance(event, dict) and event.get("type") in {
                "agent_tool_start",
                "agent_batch_action_start",
            }:
                starts.append(event)
    batch_children = [
        item for item in starts if item.get("type") == "agent_batch_action_start"
    ]
    top_level = [
        item
        for item in starts
        if item.get("type") == "agent_tool_start"
        and item.get("tool") != "run_action_batch"
    ]
    physical_names = {
        "click", "double_click", "scroll", "type_text", "hotkey",
        "drag", "move_mouse", "move_pointer", "move_to",
        "mouse_down", "mouse_up",
    }
    physical = [
        item
        for item in [*batch_children, *top_level]
        if str(item.get("tool") or "") in physical_names
    ]
    return {
        "trace_file": str(trace_file) if trace_file.is_file() else "",
        "tool_start_count": len(starts),
        "normalized_action_count": len([*batch_children, *top_level]),
        "physical_action_count": len(physical),
        "tools": [str(item.get("tool") or "") for item in starts],
        "parse_errors": parse_errors,
    }


def _first_failure_owner(
    client_result: dict[str, Any],
    verdict: dict[str, Any],
) -> str | None:
    if verdict.get("valid") is False:
        return str(verdict.get("first_failure_owner") or "harness_or_interference")
    if client_result.get("runner_error"):
        return "harness_client_driver"
    if (client_result.get("trace_binding") or {}).get("passed") is not True:
        return "harness_trace_binding"
    if (client_result.get("memory_isolation") or {}).get("passed") is not True:
        return "legacy_runtime_cold_start_contract"
    if verdict.get("passed") is not True:
        return "model_or_legacy_input_execution"
    if client_result.get("execution_valid") is not True:
        return "legacy_runtime_terminal_contract"
    if client_result.get("passed") is not True:
        return "legacy_runtime_grounding_contract"
    return None


def _debug_timeline(run_id: str) -> list[dict[str, Any]]:
    root = client_harness.TRACE_ROOT / str(run_id or "")
    if not root.is_dir():
        return []
    result: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        stem_parts = path.stem.split("_", 1)
        result.append({
            "file": str(path),
            "index": int(stem_parts[0]) if stem_parts[0].isdigit() else -1,
            "kind": stem_parts[1] if len(stem_parts) == 2 else path.stem,
            "mtime": path.stat().st_mtime,
        })
    return result


def _wait_for_inflight_model_request(
    driver: client_harness.ElectronClientDriver,
    *,
    after_epoch: float,
    event_stream: RuntimeEventStream | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    terminal_kinds = {"llm_response", "llm_error", "llm_timeout", "llm_abandoned"}
    while time.monotonic() < deadline:
        status = driver.runtime_status()
        run_ids = [
            str((status.get("current_run") or {}).get("run_id") or ""),
        ]
        if event_stream is not None:
            for event in event_stream.snapshot().get("events") or []:
                if event.get("type") == "agent_start":
                    run_ids.append(str(event.get("run_id") or ""))
                if event.get("type") == "arp_child_run":
                    run_ids.append(str(event.get("child_run_id") or ""))
        for run_id in dict.fromkeys(item for item in run_ids if item):
            timeline = _debug_timeline(run_id)
            requests = [
                item
                for item in timeline
                if item["kind"] == "llm_request"
                # Browser POST receipt and local trace-file timestamps are
                # separate observers.  Admit a one-second skew, then still
                # require that this exact request has no later terminal file.
                and float(item["mtime"]) >= after_epoch - 1.0
            ]
            if requests:
                request = requests[-1]
                later_terminal = [
                    item
                    for item in timeline
                    if int(item["index"]) > int(request["index"])
                    and item["kind"] in terminal_kinds
                ]
                last = {
                    "run_id": run_id,
                    "request": request,
                    "later_terminal": later_terminal,
                    "timeline": timeline,
                }
                if not later_terminal:
                    return {**last, "observed": True, "observed_at": time.time()}
        time.sleep(0.05)
    return {**last, "observed": False, "timed_out_at": time.time()}


def cancel_external_verdict(
    state: dict[str, Any],
    client_result: dict[str, Any],
    evidence: dict[str, Any],
    *,
    case_id: str,
    scenario: str,
    seed: int,
    quiet_seconds: float,
) -> dict[str, Any]:
    errors: list[str] = []
    invalid_reasons: list[str] = []
    events = sorted(
        _case_events(state, case_id=case_id, scenario=scenario, seed=seed),
        key=lambda item: int((item.get("details") or {}).get("event_sequence") or 0),
    )
    ready = [item for item in events if item.get("type") == "page_ready"]
    checkpoints = [
        item for item in events if item.get("type") == "cancel_checkpoint"
    ]
    post_checkpoint = [
        item for item in events if item.get("type") == "post_checkpoint_input"
    ]
    if len(ready) != 1:
        errors.append("cancel case lacks one page-ready identity")
    if len(checkpoints) != 1 or checkpoints[0].get("trusted") is not True:
        errors.append("cancel case lacks one trusted checkpoint")
    elif not (
        (checkpoints[0].get("details") or {}).get("target_id") == "cancel-start"
        and int((checkpoints[0].get("details") or {}).get("start_click_count") or 0)
        == 1
    ):
        errors.append("cancel checkpoint target identity or click count is wrong")
    if post_checkpoint:
        errors.append("target received physical input after the cancel checkpoint")

    stop = evidence.get("stop") or {}
    cutoff = float(stop.get("click_started_at") or 0)
    if not (
        stop.get("requested_stop") is True
        and stop.get("run_button_restored") is True
        and stop.get("runtime_stopped") is True
        and cutoff > 0
        and float(stop.get("confirmed_at") or 0) >= cutoff
    ):
        errors.append("visible Stop did not produce a confirmed canceled runtime")
    stop_control = stop.get("stop_control") or {}
    stop_rectangle = stop_control.get("rectangle") or {}
    if not (
        stop_control.get("name") == client_harness.STOP_TASK
        and stop_control.get("control_type") == "ButtonControl"
        and stop_control.get("visible") is True
        and int(stop_rectangle.get("right") or 0)
        > int(stop_rectangle.get("left") or 0)
        and int(stop_rectangle.get("bottom") or 0)
        > int(stop_rectangle.get("top") or 0)
    ):
        errors.append("visible Stop control identity or geometry is missing")
    checkpoint_sequence = int(
        (((checkpoints[0] if checkpoints else {}).get("details") or {}).get(
            "event_sequence",
        ))
        or 0,
    )
    # HTTP report arrival can reorder across concurrent fetches.  Browser-side
    # event_sequence is the causal clock; the low-level hook supplies the
    # independent wall-clock Stop boundary.
    late_target = [
        item
        for item in events
        if item.get("trusted") is True
        and int((item.get("details") or {}).get("event_sequence") or 0)
        > checkpoint_sequence
    ]
    if late_target:
        errors.append("trusted browser input arrived after the cancel checkpoint")
    quiet_end = physical_cutoff = float(stop.get("click_finished_at") or cutoff)
    quiet_end += quiet_seconds
    heartbeats = [
        item
        for item in events
        if item.get("type") == "target_heartbeat"
        and physical_cutoff <= float(item.get("received_at") or 0) <= quiet_end + 2
    ]
    heartbeat_times = [
        float(item.get("received_at") or 0) for item in heartbeats
    ]
    heartbeat_gaps = [
        later - earlier
        for earlier, later in zip(heartbeat_times, heartbeat_times[1:])
    ]
    if (
        not heartbeat_times
        or heartbeat_times[0] > physical_cutoff + 2.5
        or heartbeat_times[-1] < quiet_end - 2.5
        or any(gap > 2.5 for gap in heartbeat_gaps)
    ):
        invalid_reasons.append("target heartbeat has a gap during quiet window")

    inflight = evidence.get("inflight_model_request") or {}
    if inflight.get("observed") is not True:
        errors.append("cancellation was not anchored to an in-flight model request")
    trigger_observed_at = float(inflight.get("observed_at") or 0)

    hook = evidence.get("quiet_input_hook") or {}
    all_hook_events = list(hook.get("events") or [])
    hook_events = [
        item
        for item in all_hook_events
        if float(item.get("at") or 0) >= physical_cutoff
    ]
    human_events = [
        item for item in all_hook_events if item.get("injected") is False
    ]
    injected_events = [item for item in hook_events if item.get("injected") is True]
    if human_events:
        invalid_reasons.append("non-injected host input occurred during quiet window")
    if injected_events:
        errors.append("injected physical input occurred during post-cancel quiet window")
    if hook.get("errors"):
        invalid_reasons.append("low-level input monitor failed")
    if not (
        float(hook.get("started_at") or 0) <= physical_cutoff
        and float(hook.get("stopped_at") or 0) >= quiet_end - 0.1
    ):
        invalid_reasons.append("low-level input monitor did not span quiet window")

    foreground = evidence.get("foreground_samples") or []
    foreground_drift = any(
        int(item.get("hwnd") or 0)
        != int(evidence.get("expected_foreground_hwnd") or 0)
        for item in foreground
    )
    if foreground_drift and injected_events:
        errors.append("late injected input changed the foreground window")
    elif foreground_drift:
        invalid_reasons.append("external window stole foreground during quiet window")
    quiet_elapsed = float(evidence.get("quiet_elapsed_seconds") or 0)
    if quiet_elapsed + 0.05 < quiet_seconds:
        errors.append("post-cancel quiet window was shorter than configured")
    product = client_result.get("product") or {}
    effective_total_timeout = float(
        product.get("request_total_timeout_s") or 120,
    )
    if quiet_seconds < effective_total_timeout + 5:
        errors.append(
            "configured quiet window does not cover effective model total timeout",
        )
    target_after = evidence.get("target_after_quiet") or {}
    if not (
        target_after.get("print_window_succeeded") is True
        and target_after.get("visually_nonblank") is True
    ):
        invalid_reasons.append("post-cancel target screenshot is unavailable or blank")

    stream = evidence.get("runtime_event_stream") or {}
    if stream.get("errors"):
        invalid_reasons.append("runtime event stream disconnected before audit finished")
    stream_events = [
        item
        for item in (stream.get("events") or [])
        if _is_number(item.get("ts")) and float(item["ts"]) >= cutoff
    ]
    trigger_missed_proposals = [
        item
        for item in (stream.get("events") or [])
        if trigger_observed_at
        <= float(item.get("ts") or 0)
        < cutoff
        and item.get("type") == "agent_decision"
        and item.get("ok") is True
        and item.get("canceled") is not True
    ]
    if trigger_missed_proposals:
        invalid_reasons.append(
            "model proposal completed before the visible Stop click",
        )
    stream_observations = sorted(
        float(item.get("_observed_at") or 0)
        for item in (stream.get("events") or [])
        if physical_cutoff
        <= float(item.get("_observed_at") or 0)
        <= quiet_end + 2
    )
    stream_gaps = [
        later - earlier
        for earlier, later in zip(stream_observations, stream_observations[1:])
    ]
    if (
        not stream_observations
        or stream_observations[0] > physical_cutoff + 2.5
        or stream_observations[-1] < quiet_end - 2.5
        or any(gap > 2.5 for gap in stream_gaps)
    ):
        invalid_reasons.append("runtime event stream has a quiet-window gap")
    late_proposals = [
        item
        for item in stream_events
        if (
            item.get("type") == "agent_decision"
            and item.get("ok") is True
            and item.get("canceled") is not True
        )
        or item.get("type") == "agent_model_call"
    ]
    late_action_starts = [
        item
        for item in stream_events
        if item.get("type") in {"agent_tool_start", "agent_batch_action_start"}
        or (
            item.get("type") == "desktop_action_queue"
            and item.get("state") == "start"
        )
    ]
    canceled_decisions = [
        item
        for item in stream_events
        if item.get("type") == "agent_decision"
        and item.get("canceled") is True
        and item.get("ok") is False
    ]
    if late_proposals:
        errors.append("a model proposal was accepted after Stop")
    if late_action_starts:
        errors.append("a new action dispatch started after Stop")
    if not canceled_decisions:
        errors.append("live event stream has no canceled in-flight decision")

    child_run_ids = list(
        (client_result.get("trace_binding") or {}).get("child_run_ids") or [],
    )
    debug_after = [
        item
        for run_id in child_run_ids
        for item in _debug_timeline(str(run_id))
        if float(item.get("mtime") or 0) >= cutoff
    ]
    trigger_request_index = int(
        ((inflight.get("request") or {}).get("index") or -1),
    )
    trigger_run_id = str(inflight.get("run_id") or "")
    trigger_completed_before_stop = [
        item
        for item in _debug_timeline(trigger_run_id)
        if int(item.get("index") or -1) > trigger_request_index
        and item.get("kind") in {"llm_response", "llm_error", "llm_timeout"}
        and float(item.get("mtime") or 0) < cutoff
    ]
    if trigger_request_index >= 0 and trigger_completed_before_stop:
        invalid_reasons.append(
            "in-flight trigger completed before Stop reached the client",
        )
    if any(item.get("kind") == "llm_response" for item in debug_after):
        errors.append("an LLM response was recorded after Stop")
    if not any(item.get("kind") == "llm_abandoned" for item in debug_after):
        errors.append("debug trace has no abandoned in-flight model request")

    modifier_after = evidence.get("modifier_state_after_quiet") or {}
    if any(modifier_after.values()):
        errors.append("a physical modifier remains held after cancellation")
    result_state = client_result.get("result") or {}
    if not (
        result_state.get("outcome") == "terminal"
        and result_state.get("status") == "canceled"
    ):
        errors.append("outer client run is not grounded as canceled")

    valid = not invalid_reasons
    return {
        "source": "external_browser_hook_and_live_runtime_stream",
        "oracle_status": "conclusive" if valid else "invalid",
        "valid": valid,
        "passed": valid and not errors,
        "errors": errors,
        "invalid_reasons": invalid_reasons,
        "duplicate_input_count": len(post_checkpoint) + len(late_target),
        "action_count": (_trace_action_summary(client_result)).get(
            "physical_action_count",
        ),
        "late_model_proposal_count": len(late_proposals),
        "late_action_start_count": len(late_action_starts),
        "post_cancel_injected_input_count": len(injected_events),
        "post_cancel_human_input_count": len(human_events),
        "quiet_elapsed_seconds": quiet_elapsed,
        "target_heartbeat_count": len(heartbeat_times),
        "target_heartbeat_max_gap_seconds": max(heartbeat_gaps, default=0.0),
        "runtime_stream_observation_count": len(stream_observations),
        "runtime_stream_max_gap_seconds": max(stream_gaps, default=0.0),
        "first_failure_owner": (
            "external_interference"
            if invalid_reasons
            else "legacy_product"
            if errors
            else None
        ),
    }


def run_visual_case(
    name: str,
    *,
    output_root: Path,
    seed: int | None = None,
    max_steps: int | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    if os.name != "nt" or not ctypes.windll.user32.GetForegroundWindow():
        raise RuntimeError("an unlocked interactive Windows desktop is required")
    source = dict(VISUAL_CASES[name])
    if seed is not None:
        source["seed"] = int(seed)
    if max_steps is not None:
        source["max_steps"] = int(max_steps)
    if timeout is not None:
        source["timeout"] = int(timeout)

    case_id = f"client-{name}-{uuid.uuid4().hex[:10]}"
    scenario = str(source["scenario"])
    seed = int(source["seed"])
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    state_url = f"{base}/state?case={case_id}"
    url = (
        f"{base}/visual_interaction_target.html?case={case_id}"
        f"&scenario={scenario}&seed={seed}"
    )
    case_dir = output_root / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = Path(tempfile.mkdtemp(prefix=f"winpilot-client-visual-{case_id}-"))
    server_process: subprocess.Popen[Any] | None = None
    browser_process: subprocess.Popen[Any] | None = None
    target_hwnd = 0
    runtime_stream: RuntimeEventStream | None = None
    runtime_stream_start_error = ""
    input_monitor: LowLevelInputMonitor | None = None
    input_monitor_start_error = ""
    input_monitor_stopped = False
    input_causal_started_at = 0.0
    cancel_evidence: dict[str, Any] = {}
    oracle_result: dict[str, Any] = {}
    started_at = datetime.now().astimezone().isoformat()
    initial_modifier_state = _modifier_state()
    result: dict[str, Any] = {
        "name": name,
        "case_id": case_id,
        "scenario": scenario,
        "seed": seed,
        "task": source["task"],
        "budget": {
            "max_steps": int(source["max_steps"]),
            "timeout_seconds": int(source["timeout"]),
        },
        "entrypoint": "electron_client_windows_uia",
        "cold_start_required": True,
        "ground_truth": "trusted_external_events_and_final_state",
        "harness": harness_metadata(),
        "product": client_harness.product_metadata(),
        "modifier_state_before": initial_modifier_state,
        "started_at": started_at,
    }
    try:
        if any(initial_modifier_state.values()):
            result.update({
                "valid": False,
                "passed": False,
                "grounded_verdict": False,
                "first_failure_owner": "external_interference",
                "external_verdict": {
                    "source": "windows_physical_modifier_state",
                    "oracle_status": "invalid",
                    "valid": False,
                    "passed": False,
                    "errors": [],
                    "invalid_reasons": [
                        "a physical modifier was already held before the case",
                    ],
                    "duplicate_input_count": None,
                },
            })
            return result
        server_process = subprocess.Popen(
            [sys.executable, "-u", str(TARGET_SERVER), str(port)],
            cwd=str(TARGET_SERVER.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _wait_json(f"{base}/state", lambda value: value.get("ready") is True, timeout=12)
        browser_process = subprocess.Popen(
            [
                str(_browser_executable()),
                f"--app={url}",
                "--new-window",
                "--window-size=1100,760",
                "--window-position=80,60",
                "--force-device-scale-factor=1",
                f"--user-data-dir={profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                "--disable-background-mode",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-features=CalculateNativeWinOcclusion",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        title = f"WinPilot Visual Interaction Target {case_id}"
        target_hwnd = _find_window(title)
        ready_state = _wait_json(
            state_url,
            lambda value: any(
                item.get("type") == "page_ready"
                and item.get("scenario") == scenario
                for item in value.get("events") or []
            ),
            timeout=12,
        )
        result["target_ready"] = ready_state
        result["target_before_focus"] = _focus_window(target_hwnd)
        result["target_before"] = _capture_window(
            target_hwnd, case_dir / "target-before-submit.png",
        )
        result["target_before_evidence"] = {
            "path": result["target_before"],
            "foreground_hwnd": int(win32gui.GetForegroundWindow() or 0),
            "expected_hwnd": int(target_hwnd),
            "visually_nonblank": _image_visually_nonblank(
                Path(result["target_before"]),
            ),
        }
        if not (
            result["target_before_evidence"]["foreground_hwnd"] == int(target_hwnd)
            and result["target_before_evidence"]["visually_nonblank"] is True
        ):
            result.update({
                "valid": False,
                "passed": False,
                "grounded_verdict": False,
                "first_failure_owner": "external_interference",
                "external_verdict": {
                    "source": "visible_target_before_screenshot",
                    "oracle_status": "invalid",
                    "valid": False,
                    "passed": False,
                    "errors": [],
                    "invalid_reasons": [
                        "target before screenshot was occluded, blank, or not foreground",
                    ],
                    "duplicate_input_count": None,
                },
            })
            return result
        try:
            dpi = int(ctypes.windll.user32.GetDpiForWindow(target_hwnd))
        except Exception:
            dpi = 0
        result["desktop"] = {
            "target_hwnd": int(target_hwnd),
            "target_dpi": dpi,
            "virtual_screen": {
                "left": int(win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)),
                "top": int(win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)),
                "width": int(win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)),
                "height": int(win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)),
            },
        }

        def prepare_before_submit(
            driver: client_harness.ElectronClientDriver,
            _client_result: dict[str, Any],
        ) -> dict[str, Any] | None:
            nonlocal runtime_stream, runtime_stream_start_error
            nonlocal input_monitor, input_monitor_start_error
            preparation: dict[str, Any] = {}
            input_monitor = LowLevelInputMonitor()
            try:
                input_monitor.start()
                preparation["input_monitor_started_at"] = input_monitor.started_at
            except Exception as error:
                input_monitor_start_error = f"{type(error).__name__}: {error}"
                preparation["input_monitor_error"] = input_monitor_start_error
            if scenario == "cancel_guard":
                runtime_stream = RuntimeEventStream(
                    driver._runtime_url,
                    driver._runtime_token,
                )
                try:
                    runtime_stream.start()
                    preparation.update({
                        "runtime_event_stream_connected": True,
                        "connected_at": runtime_stream.connected_at,
                        "endpoint": "/ws/events",
                    })
                except Exception as error:
                    runtime_stream_start_error = (
                        f"{type(error).__name__}: {error}"
                    )
                    preparation["runtime_event_stream_error"] = (
                        runtime_stream_start_error
                    )
                    runtime_stream = None
            return preparation

        def handoff(
            driver: client_harness.ElectronClientDriver,
            _client_result: dict[str, Any],
        ) -> dict[str, Any]:
            nonlocal input_causal_started_at, input_monitor_stopped, runtime_stream
            evidence = _focus_window(target_hwnd)
            evidence["screenshot"] = _capture_window(
                target_hwnd,
                driver.output_dir / "target-after-submit.png",
            )
            evidence["modifier_state_at_handoff"] = _modifier_state()
            input_causal_started_at = time.time()
            evidence["input_causal_started_at"] = input_causal_started_at
            if input_monitor_start_error:
                evidence["input_monitor_error"] = input_monitor_start_error
            if scenario != "cancel_guard":
                return evidence

            checkpoint_state = _wait_json(
                state_url,
                lambda value: any(
                    item.get("type") == "cancel_checkpoint"
                    and item.get("trusted") is True
                    for item in value.get("events") or []
                ),
                timeout=min(180, int(source["timeout"])),
            )
            checkpoint = next(
                item
                for item in reversed(checkpoint_state.get("events") or [])
                if item.get("type") == "cancel_checkpoint"
                and item.get("trusted") is True
            )
            cancel_evidence["checkpoint"] = checkpoint
            cancel_evidence["inflight_model_request"] = (
                _wait_for_inflight_model_request(
                    driver,
                    after_epoch=float(checkpoint.get("received_at") or time.time()),
                    event_stream=runtime_stream,
                )
            )
            cancel_evidence["stop"] = driver.stop_active_run()
            stop_finished = float(
                (cancel_evidence["stop"] or {}).get("click_finished_at")
                or time.time(),
            )
            quiet_seconds = float(source["post_cancel_quiet_seconds"])
            quiet_deadline = stop_finished + quiet_seconds
            expected_foreground = int(win32gui.GetForegroundWindow() or 0)
            foreground_samples: list[dict[str, Any]] = []
            while time.time() < quiet_deadline:
                hwnd = int(win32gui.GetForegroundWindow() or 0)
                if (
                    not foreground_samples
                    or foreground_samples[-1].get("hwnd") != hwnd
                    or time.time() - float(foreground_samples[-1].get("at") or 0)
                    >= 2.0
                ):
                    foreground_samples.append({
                        "at": time.time(),
                        "hwnd": hwnd,
                        "title": win32gui.GetWindowText(hwnd) if hwnd else "",
                    })
                time.sleep(min(0.2, max(0.01, quiet_deadline - time.time())))
            cancel_evidence["quiet_elapsed_seconds"] = max(
                0.0,
                time.time() - stop_finished,
            )
            cancel_evidence["foreground_samples"] = foreground_samples
            cancel_evidence["expected_foreground_hwnd"] = expected_foreground
            cancel_evidence["target_hwnd"] = int(target_hwnd)
            cancel_evidence["desktop_after_quiet"] = str(
                driver.output_dir / "desktop-after-cancel-quiet.png",
            )
            ImageGrab.grab(all_screens=True).save(
                cancel_evidence["desktop_after_quiet"],
            )
            cancel_evidence["target_after_quiet"] = _capture_window_without_focus(
                target_hwnd,
                driver.output_dir / "target-after-cancel-quiet.bmp",
            )
            cancel_evidence["modifier_state_after_quiet"] = _modifier_state()
            if input_monitor is not None:
                cancel_evidence["quiet_input_hook"] = input_monitor.stop()
                input_monitor_stopped = True
            if runtime_stream is not None:
                cancel_evidence["runtime_event_stream"] = runtime_stream.stop()
                runtime_stream = None
            elif runtime_stream_start_error:
                cancel_evidence["runtime_event_stream"] = {
                    "events": [],
                    "errors": [runtime_stream_start_error],
                }
            cancel_evidence["target_state_after_quiet"] = _json_get(state_url)
            evidence_path = driver.output_dir / "cancel-observation.json"
            evidence_path.write_text(
                json.dumps(cancel_evidence, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            evidence.update({
                "cancel_observation": str(evidence_path),
                "checkpoint_received_at": checkpoint.get("received_at"),
                "inflight_model_request_observed": (
                    cancel_evidence["inflight_model_request"].get("observed")
                    is True
                ),
                "stop": cancel_evidence["stop"],
                "quiet_elapsed_seconds": cancel_evidence["quiet_elapsed_seconds"],
            })
            return evidence

        def verify_external_state(client_result: dict[str, Any]) -> dict[str, Any]:
            nonlocal input_monitor_stopped, runtime_stream
            observation_error = ""
            try:
                state = _wait_json(
                    state_url,
                    (
                        (lambda value: bool(value.get("events")))
                        if scenario == "cancel_guard"
                        else (lambda value: value.get("success") is True)
                    ),
                    timeout=5,
                )
            except Exception as error:
                observation_error = f"{type(error).__name__}: {error}"
                state = _json_get(state_url)
            stream_quiet: bool | None = None
            settle_error: str | None = None
            if scenario != "cancel_guard" and state.get("success") is True:
                state, stream_quiet, settle_error = _settle_external_state(
                    state_url,
                    state,
                )
            if input_monitor is not None and not input_monitor_stopped:
                input_evidence = input_monitor.stop()
                input_monitor_stopped = True
            else:
                input_evidence = (
                    cancel_evidence.get("quiet_input_hook") or {}
                )

            if scenario == "cancel_guard":
                verdict = cancel_external_verdict(
                    state,
                    client_result,
                    cancel_evidence,
                    case_id=case_id,
                    scenario=scenario,
                    seed=seed,
                    quiet_seconds=float(source["post_cancel_quiet_seconds"]),
                )
            else:
                verdict = external_verdict(
                    state,
                    case_id=case_id,
                    scenario=scenario,
                    seed=seed,
                    expected_variant=source.get("expected_variant"),
                )
                terminal_time = float(
                    ((state.get("success_event") or {}).get("received_at") or 0),
                )
                causal_input = [
                    item
                    for item in (input_evidence.get("events") or [])
                    if input_causal_started_at
                    <= float(item.get("at") or 0)
                    <= terminal_time + 0.02
                ]
                human_input = [
                    item
                    for item in (input_evidence.get("events") or [])
                    if float(item.get("at") or 0) <= terminal_time + 0.02
                    and item.get("injected") is False
                ]
                invalid_reasons: list[str] = []
                if human_input:
                    invalid_reasons.append(
                        "non-injected host input overlapped the causal input window",
                    )
                if input_evidence.get("errors"):
                    invalid_reasons.append("low-level input monitor failed")
                if stream_quiet is not True:
                    invalid_reasons.append("external event stream did not settle")
                if observation_error:
                    verdict["errors"].append(observation_error)
                if settle_error:
                    invalid_reasons.append(settle_error)
                if any(_modifier_state().values()):
                    verdict["errors"].append(
                        "a physical modifier remains held after the case",
                    )
                verdict.update({
                    "source": "external_browser_state_and_low_level_input_hook",
                    "valid": not invalid_reasons,
                    "oracle_status": (
                        "conclusive" if not invalid_reasons else "invalid"
                    ),
                    "invalid_reasons": invalid_reasons,
                    "causal_low_level_input_count": len(causal_input),
                    "causal_human_input_count": len(human_input),
                    "passed": (
                        verdict.get("passed") is True and not invalid_reasons
                    ),
                })
                verdict["first_failure_owner"] = (
                    "external_interference"
                    if invalid_reasons
                    else "legacy_product"
                    if verdict.get("errors")
                    else None
                )
            action_summary = _trace_action_summary(client_result)
            verdict["action_count"] = action_summary["physical_action_count"]
            verdict["trace_action_summary"] = action_summary
            oracle_result.update({
                "state": state,
                "verdict": verdict,
                "input_evidence": input_evidence,
            })
            return verdict

        client_spec = {
            "task": source["task"],
            "max_steps": int(source["max_steps"]),
            "timeout": int(source["timeout"]),
            "expected": str(source.get("expected") or "terminal"),
        }
        client_result = client_harness.run_case(
            name,
            client_spec,
            output_root=case_dir / "client",
            reset_session=True,
            before_submit=prepare_before_submit,
            after_submit=handoff,
            independent_verifier=verify_external_state,
        )
        result["client_result"] = client_result
        state = dict(oracle_result.get("state") or {})
        result["external_state"] = state
        verdict = dict(oracle_result.get("verdict") or {})
        result["input_evidence"] = oracle_result.get("input_evidence") or {}
        result["target_after"] = _capture_window_without_focus(
            target_hwnd,
            case_dir / "target-after-terminal.bmp",
        )
        if not (
            result["target_after"].get("print_window_succeeded") is True
            and result["target_after"].get("visually_nonblank") is True
        ):
            verdict.setdefault("invalid_reasons", []).append(
                "terminal target screenshot is unavailable or blank",
            )
            verdict["valid"] = False
            verdict["oracle_status"] = "invalid"
            verdict["passed"] = False
            verdict["first_failure_owner"] = "harness"
        result["external_verdict"] = verdict
        result["outer_run_ids"] = list(
            (client_result.get("trace_binding") or {}).get("outer_run_ids") or [],
        )
        result["child_run_ids"] = list(
            (client_result.get("trace_binding") or {}).get("child_run_ids") or [],
        )
        result["trace"] = {
            "ids": list(client_result.get("bound_trace_ids") or []),
            "file": str(
                (client_result.get("trace_binding") or {}).get("trace_file") or "",
            ),
        }
        result["action_count"] = verdict.get("action_count")
        result["duplicate_input_count"] = verdict.get("duplicate_input_count")
        result["valid"] = verdict.get("valid") is True
        result["grounded_verdict"] = bool(
            result["valid"] and client_result.get("passed") is True
        )
        result["first_failure_owner"] = _first_failure_owner(
            client_result,
            verdict,
        )
        result["modifier_state_after"] = _modifier_state()
        result["passed"] = result["grounded_verdict"]
    except Exception as error:
        result["runner_error"] = f"{type(error).__name__}: {error}"
        result["valid"] = False
        result["first_failure_owner"] = "harness"
        result["passed"] = False
        try:
            result["external_state"] = _json_get(state_url)
        except Exception:
            pass
    finally:
        if input_monitor is not None and not input_monitor_stopped:
            try:
                result["input_monitor_cleanup"] = input_monitor.stop()
            except Exception as error:
                result["input_monitor_cleanup_error"] = (
                    f"{type(error).__name__}: {error}"
                )
        if runtime_stream is not None:
            try:
                result["runtime_event_stream_cleanup"] = runtime_stream.stop()
            except Exception as error:
                result["runtime_event_stream_cleanup_error"] = (
                    f"{type(error).__name__}: {error}"
                )
        if target_hwnd and win32gui.IsWindow(target_hwnd):
            try:
                win32gui.PostMessage(target_hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception:
                pass
        _terminate_tree(browser_process)
        _terminate_tree(server_process)
        shutil.rmtree(profile_dir, ignore_errors=True)
        result["finished_at"] = datetime.now().astimezone().isoformat()
        report = case_dir / "report.json"
        report.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        result["report"] = str(report)
        print(json.dumps({"event": "visual_case_finished", **result}, ensure_ascii=False), flush=True)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=tuple(VISUAL_CASES))
    parser.add_argument("--suite", choices=tuple(SUITES))
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--seed", type=int, help="Override the deterministic layout seed.")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--product-root",
        type=Path,
        default=client_harness.PRODUCT_WORKSPACE,
        help="Clean product worktree whose Electron client is under test.",
    )
    parser.add_argument(
        "--expected-product-sha",
        default="",
        help="Require the product worktree to resolve to this exact commit.",
    )
    parser.add_argument("--list", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.list:
        print(json.dumps({"cases": VISUAL_CASES, "suites": SUITES}, ensure_ascii=False, indent=2))
        return 0
    product_root = args.product_root.resolve()
    if not product_root.is_dir():
        raise RuntimeError(f"product worktree does not exist: {product_root}")
    client_harness.PRODUCT_WORKSPACE = product_root
    client_harness.TRACE_ROOT = (
        product_root / "python" / "winpilot_home" / "traces"
    )
    product_revision = _git_revision(product_root)
    expected_revision = str(args.expected_product_sha or "").strip()
    if expected_revision and product_revision != expected_revision:
        raise RuntimeError(
            f"product revision mismatch: expected {expected_revision}, "
            f"observed {product_revision}",
        )
    tracked_status = subprocess.check_output(
        [
            "git", "-C", str(product_root), "status",
            "--porcelain", "--untracked-files=normal",
        ],
        text=True,
        encoding="utf-8",
    ).strip()
    if tracked_status:
        raise RuntimeError(
            f"product worktree has tracked changes: {product_root}",
        )
    names = (args.case,) if args.case else SUITES.get(args.suite or "smoke", ())
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_root = args.output_root.resolve() / f"{timestamp}-suite"
    results: list[dict[str, Any]] = []
    for name in names:
        item = run_visual_case(
            str(name),
            output_root=output_root,
            seed=args.seed,
            max_steps=args.max_steps,
            timeout=args.timeout,
        )
        results.append(item)
        if args.fail_fast and not item.get("passed"):
            break
    report = {
        "entrypoint": "electron_client_windows_uia",
        "cold_start_required": True,
        "harness": harness_metadata(),
        "product": client_harness.product_metadata(),
        "case_count": len(results),
        "passed_count": sum(item.get("passed") is True for item in results),
        "invalid_count": sum(item.get("valid") is False for item in results),
        "results": results,
    }
    report["passed"] = bool(results) and report["passed_count"] == len(results)
    output_root.mkdir(parents=True, exist_ok=True)
    report_path = output_root / "suite-report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    report["report"] = str(report_path)
    print(json.dumps({"event": "visual_suite_finished", **report}, ensure_ascii=False), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
