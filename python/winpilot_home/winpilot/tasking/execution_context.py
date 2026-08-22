"""Neutral Windows desktop workspace capture for the live Visual Actor."""
from __future__ import annotations

import ctypes
import time
from typing import Any, Callable


def _normalized_rect(value: Any) -> list[int]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[int] = []
    for item in value[:4]:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            return []
    return result


def _window_title(hwnd: int) -> str:
    try:
        length = int(
            ctypes.windll.user32.GetWindowTextLengthW(int(hwnd)) or 0
        )
        buffer = ctypes.create_unicode_buffer(max(1, length + 1))
        ctypes.windll.user32.GetWindowTextW(
            int(hwnd), buffer, len(buffer),
        )
        return str(buffer.value or "")
    except Exception:
        return ""


def _window_class_name(hwnd: int) -> str:
    try:
        buffer = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(
            int(hwnd), buffer, len(buffer),
        )
        return str(buffer.value or "")
    except Exception:
        return ""


def windows_shell_desktop_context(
    *,
    region_provider: Callable[[], list[int]] | None = None,
    hwnd_resolver: Callable[[], int] | None = None,
    process_id_resolver: Callable[[int], int] | None = None,
) -> dict[str, Any]:
    """Return the Windows Shell desktop (``Progman``) as a bindable target."""
    if hwnd_resolver is None:
        hwnd_resolver = lambda: int(
            ctypes.windll.user32.FindWindowW("Progman", None) or 0
        )
    if process_id_resolver is None:
        process_id_resolver = window_process_id
    if region_provider is None:
        from ..utils.screenshot import virtual_screen_region

        region_provider = lambda: [
            virtual_screen_region().left,
            virtual_screen_region().top,
            virtual_screen_region().width,
            virtual_screen_region().height,
        ]
    try:
        hwnd = int(hwnd_resolver() or 0)
    except Exception:
        return {}
    if not hwnd:
        return {}
    try:
        process_id = int(process_id_resolver(hwnd) or 0)
    except Exception:
        process_id = 0
    class_name = _window_class_name(hwnd)
    if not class_name:
        return {}
    title = _window_title(hwnd) or class_name
    rect = _normalized_rect(region_provider() or [])
    if len(rect) != 4 or rect[2] <= rect[0] or rect[3] <= rect[1]:
        return {}
    return {
        "format": "desktop-target/1",
        "hwnd": hwnd,
        "title": title,
        "process_id": process_id,
        "class_name": class_name,
        "rect": rect,
        "foreground_verified": False,
        "usable": True,
        "failure_type": "",
        "reason": "windows_shell_desktop_fallback",
        "binding_mode": "desktop_shell",
    }


def capture_desktop_workspace_context(
    *,
    time_provider: Callable[[], float] = time.time,
    desktop_shell_resolver: Callable[[], dict[str, Any]] = (
        windows_shell_desktop_context
    ),
) -> dict[str, Any]:
    """Return a neutral full-desktop anchor for the native Visual Actor.

    The anchor only keeps compatibility with the existing runtime identity
    contract.  It does not nominate the foreground window as task owner; the
    model sees the whole screen and physical input follows the point it chose.
    """

    captured_at = int(time_provider() * 1000)
    try:
        shell = desktop_shell_resolver()
    except Exception:
        shell = {}
    if not isinstance(shell, dict) or int(shell.get("hwnd") or 0) <= 0:
        return {
            "format": "desktop-target/1",
            "hwnd": 0,
            "title": "",
            "process_id": 0,
            "class_name": "",
            "rect": [],
            "captured_at": captured_at,
            "foreground_verified": False,
            "usable": False,
            "failure_type": "target_binding_missing",
            "reason": "Windows desktop workspace is unavailable",
            "binding_mode": "desktop_shell",
        }
    return {
        "format": "desktop-target/1",
        "hwnd": int(shell.get("hwnd") or 0),
        "title": str(shell.get("title") or ""),
        "process_id": int(shell.get("process_id") or 0),
        "class_name": str(shell.get("class_name") or ""),
        "rect": _normalized_rect(shell.get("rect")),
        "captured_at": captured_at,
        "foreground_verified": False,
        "usable": shell.get("usable") is True,
        "failure_type": str(shell.get("failure_type") or ""),
        "reason": "",
        "binding_mode": "desktop_shell",
    }


def window_process_id(hwnd: int) -> int:
    if not hwnd:
        return 0
    process_id = ctypes.c_ulong(0)
    try:
        ctypes.windll.user32.GetWindowThreadProcessId(
            int(hwnd), ctypes.byref(process_id),
        )
    except Exception:
        return 0
    return int(process_id.value or 0)
