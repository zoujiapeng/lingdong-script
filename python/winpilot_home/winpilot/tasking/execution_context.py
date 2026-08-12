"""Validated client-to-runtime context for desktop foreground handoff.

The native control surface necessarily becomes foreground while a user submits
a task.  Desktop stages must not mistake that surface for the application the
user was working in.  This module keeps the transport/validation concern out of
the interaction state machine and never reparses task text.
"""
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


def normalize_execution_context(inputs: Any) -> dict[str, Any]:
    source = inputs if isinstance(inputs, dict) else {}
    value = source.get("execution_context")
    if not isinstance(value, dict):
        return {}
    handoff = value.get("desktop_handoff")
    handoff = handoff if isinstance(handoff, dict) else {}
    target = value.get("target_window")
    target = target if isinstance(target, dict) else {}
    return {
        "format": str(value.get("format") or ""),
        "entrypoint": str(value.get("entrypoint") or ""),
        "task_id": str(value.get("task_id") or ""),
        "submitted_at": int(value.get("submitted_at") or 0),
        "desktop_handoff": {
            "required": handoff.get("required") is True,
            "strategy": str(handoff.get("strategy") or ""),
            "yielded": handoff.get("yielded") is True,
            "control_surface_pid": int(
                handoff.get("control_surface_pid") or 0
            ),
            "prepared_at": int(handoff.get("prepared_at") or 0),
            "reason": str(handoff.get("reason") or ""),
        },
        "target_window": {
            "format": str(target.get("format") or ""),
            "hwnd": int(target.get("hwnd") or 0),
            "title": str(target.get("title") or ""),
            "process_id": int(target.get("process_id") or 0),
            "class_name": str(target.get("class_name") or ""),
            "rect": _normalized_rect(target.get("rect")),
            "captured_at": int(target.get("captured_at") or 0),
            "foreground_verified": target.get("foreground_verified") is True,
            "usable": target.get("usable") is True,
            "failure_type": str(target.get("failure_type") or ""),
            "reason": str(target.get("reason") or ""),
            "binding_mode": str(target.get("binding_mode") or "window"),
        },
    }


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


def capture_foreground_window_context(
    exclude_process_id: int = 0,
    *,
    foreground_resolver: Callable[[], int] | None = None,
    process_id_resolver: Callable[[int], int] = window_process_id,
    windows_provider: Callable[[], list[dict[str, Any]]] | None = None,
    time_provider: Callable[[], float] = time.time,
    desktop_shell_resolver: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Capture the foreground target immediately after a control-surface yield.

    When no usable application window exists, the Windows Shell desktop
    (``Progman``) is bound instead so a generic launch stage can start an
    application.  That is a real visible target window, not a virtual desktop
    shortcut.
    """
    if foreground_resolver is None:
        foreground_resolver = lambda: int(
            ctypes.windll.user32.GetForegroundWindow() or 0
        )
    if windows_provider is None:
        from ..perception import win32

        windows_provider = win32.list_windows

    captured_at = int(time_provider() * 1000)
    try:
        hwnd = int(foreground_resolver() or 0)
    except Exception:
        hwnd = 0
    try:
        process_id = int(process_id_resolver(hwnd) or 0) if hwnd else 0
    except Exception:
        process_id = 0
    try:
        windows = windows_provider() if hwnd else []
    except Exception:
        windows = []
    window = next((
        item for item in windows
        if isinstance(item, dict) and int(item.get("hwnd") or 0) == hwnd
    ), {})
    failure_type = ""
    reason = ""
    shell: dict[str, Any] = {}
    binding_mode = "window"
    if not hwnd or not window:
        if desktop_shell_resolver is not None:
            try:
                shell = desktop_shell_resolver()
            except Exception:
                shell = {}
        if not isinstance(shell, dict) or int(shell.get("hwnd") or 0) <= 0:
            failure_type = "target_binding_missing"
            reason = "no usable foreground application was visible after handoff"
            shell = {}
        else:
            hwnd = int(shell.get("hwnd") or 0)
            process_id = int(shell.get("process_id") or 0)
            window = shell
            binding_mode = "desktop_shell"
    elif exclude_process_id and process_id == int(exclude_process_id):
        failure_type = "control_surface_handoff_failed"
        reason = "native control surface still owns foreground"
    return {
        "format": "desktop-target/1",
        "hwnd": hwnd,
        "title": str(window.get("title") or ""),
        "process_id": process_id,
        "class_name": str(window.get("class_name") or ""),
        "rect": _normalized_rect(window.get("rect")),
        "captured_at": captured_at,
        "foreground_verified": bool(
            hwnd and window and binding_mode == "window"
        ),
        "usable": not failure_type,
        "failure_type": failure_type,
        "reason": reason,
        "binding_mode": binding_mode,
    }


def validated_handoff_target(
    execution_context: dict[str, Any],
    *,
    window_exists_resolver: Callable[[int], bool] | None = None,
    now_ms: int | None = None,
    max_age_ms: int = 60_000,
) -> tuple[int, dict[str, Any] | None]:
    """Return a live compiler-independent HWND or a precise binding failure."""
    if str(execution_context.get("entrypoint") or "") != "native_agent_cockpit":
        return 0, None
    handoff = execution_context.get("desktop_handoff")
    if not isinstance(handoff, dict) or handoff.get("required") is not True:
        return 0, None
    target = execution_context.get("target_window")
    target = target if isinstance(target, dict) else {}
    hwnd = int(target.get("hwnd") or 0)
    if target.get("usable") is not True or not hwnd:
        return 0, {
            "failure_type": str(target.get("failure_type") or "target_binding_missing"),
            "reason": str(target.get("reason") or "desktop target was not captured after handoff"),
        }
    if window_exists_resolver is None:
        window_exists_resolver = lambda value: bool(
            ctypes.windll.user32.IsWindow(int(value))
        )
    if not window_exists_resolver(hwnd):
        return 0, {
            "failure_type": "target_binding_stale",
            "reason": "captured desktop target window no longer exists",
            "target_hwnd": hwnd,
        }
    captured_at = int(target.get("captured_at") or 0)
    current_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    if not captured_at or current_ms - captured_at > max(0, int(max_age_ms)):
        return 0, {
            "failure_type": "target_binding_stale",
            "reason": "captured desktop target expired before execution began",
            "target_hwnd": hwnd,
            "captured_at": captured_at,
            "age_ms": max(0, current_ms - captured_at),
        }
    return hwnd, None


def foreground_handoff_conflict(
    execution_context: dict[str, Any],
    target_hwnd: int,
    *,
    process_id_resolver: Callable[[int], int] = window_process_id,
) -> dict[str, Any] | None:
    """Return a fail-closed reason when the native console still owns target.

    The guard is scoped to the native cockpit's explicit handoff contract. API
    callers without that context retain their existing foreground semantics.
    """
    if str(execution_context.get("entrypoint") or "") != "native_agent_cockpit":
        return None
    handoff = execution_context.get("desktop_handoff")
    if not isinstance(handoff, dict) or handoff.get("required") is not True:
        return None
    if handoff.get("yielded") is not True:
        return {
            "failure_type": "control_surface_handoff_failed",
            "reason": str(handoff.get("reason") or "control surface did not yield foreground"),
            "target_hwnd": int(target_hwnd or 0),
        }
    control_pid = int(handoff.get("control_surface_pid") or 0)
    target_pid = int(process_id_resolver(int(target_hwnd or 0)) or 0)
    if control_pid and target_pid == control_pid:
        return {
            "failure_type": "control_surface_handoff_failed",
            "reason": "native control surface still owns the foreground target",
            "target_hwnd": int(target_hwnd or 0),
            "target_process_id": target_pid,
            "control_surface_pid": control_pid,
        }
    return None
