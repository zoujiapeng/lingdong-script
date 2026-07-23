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

from ..process_identity import (
    ProcessIdentityError,
    canonical_process_identity,
    process_identity_for,
)


_TARGET_WINDOW_FIELDS = frozenset({
    "format",
    "hwnd",
    "root_hwnd",
    "title",
    "process_id",
    "process_identity",
    "class_name",
    "rect",
    "captured_at",
    "foreground_verified",
    "usable",
    "failure_type",
    "reason",
})


def _normalized_rect(value: Any) -> list[int]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 4
        or any(type(item) is not int for item in value)
    ):
        return []
    return list(value)


def _literal_int(value: Any) -> int:
    return value if type(value) is int else 0


def normalize_execution_context(inputs: Any) -> dict[str, Any]:
    source = inputs if isinstance(inputs, dict) else {}
    value = source.get("execution_context")
    if not isinstance(value, dict):
        return {}
    handoff = value.get("desktop_handoff")
    handoff = handoff if isinstance(handoff, dict) else {}
    target = value.get("target_window")
    target = target if isinstance(target, dict) else {}
    target_schema_valid = not target or (
        set(target) == _TARGET_WINDOW_FIELDS
        and all(type(key) is str for key in target)
    )
    return {
        "format": str(value.get("format") or ""),
        "entrypoint": str(value.get("entrypoint") or ""),
        "task_id": str(value.get("task_id") or ""),
        "submitted_at": _literal_int(value.get("submitted_at")),
        "desktop_handoff": {
            "required": handoff.get("required") is True,
            "strategy": str(handoff.get("strategy") or ""),
            "yielded": handoff.get("yielded") is True,
            "control_surface_pid": _literal_int(
                handoff.get("control_surface_pid")
            ),
            "prepared_at": _literal_int(handoff.get("prepared_at")),
            "reason": str(handoff.get("reason") or ""),
        },
        "target_window": {
            "format": str(target.get("format") or ""),
            "hwnd": _literal_int(target.get("hwnd")),
            "root_hwnd": _literal_int(target.get("root_hwnd")),
            "title": str(target.get("title") or ""),
            "process_id": _literal_int(target.get("process_id")),
            "process_identity": str(target.get("process_identity") or ""),
            "class_name": str(target.get("class_name") or ""),
            "rect": _normalized_rect(target.get("rect")),
            "captured_at": _literal_int(target.get("captured_at")),
            "foreground_verified": target.get("foreground_verified") is True,
            "usable": target.get("usable") is True and target_schema_valid,
            "failure_type": (
                str(target.get("failure_type") or "")
                if target_schema_valid
                else "target_binding_contract_invalid"
            ),
            "reason": (
                str(target.get("reason") or "")
                if target_schema_valid
                else "captured desktop target fields do not match desktop-target/2"
            ),
        },
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


def window_root_hwnd(hwnd: int) -> int:
    if type(hwnd) is not int or hwnd <= 0:
        return 0
    try:
        value = int(ctypes.windll.user32.GetAncestor(hwnd, 2) or 0)
    except Exception:
        return 0
    return value or hwnd


def window_process_identity(process_id: int) -> str:
    """Read one canonical Windows process-instance identity.

    The OS reader remains behind this injectable transport boundary. Identity
    derivation is shared with commit admission, so capture and bind compare the
    same canonical fact without introducing another target owner.
    """

    if type(process_id) is not int or process_id <= 0:
        raise ProcessIdentityError("process_id must be a positive integer")
    from ..visual_runtime.windows_desktop_facts import (
        PyWin32DesktopFactsBackend,
    )

    backend = PyWin32DesktopFactsBackend()
    return process_identity_for(
        process_id=process_id,
        creation_time_ns=backend.process_creation_time_ns(process_id),
        executable_path=backend.process_executable_path(process_id),
    )


def capture_foreground_window_context(
    exclude_process_id: int = 0,
    *,
    foreground_resolver: Callable[[], int] | None = None,
    root_hwnd_resolver: Callable[[int], int] = window_root_hwnd,
    process_id_resolver: Callable[[int], int] = window_process_id,
    process_identity_resolver: Callable[[int], str] = window_process_identity,
    windows_provider: Callable[[], list[dict[str, Any]]] | None = None,
    time_provider: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Capture the foreground target immediately after a control-surface yield."""
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
        root_hwnd = int(root_hwnd_resolver(hwnd) or 0) if hwnd else 0
    except Exception:
        root_hwnd = 0
    try:
        process_id = int(process_id_resolver(hwnd) or 0) if hwnd else 0
    except Exception:
        process_id = 0
    process_identity = ""
    if process_id > 0:
        try:
            process_identity = canonical_process_identity(
                process_identity_resolver(process_id)
            )
        except (ImportError, OSError, RuntimeError, ValueError):
            process_identity = ""
    try:
        windows = windows_provider() if hwnd else []
    except Exception:
        windows = []
    window = next((
        item for item in windows
        if isinstance(item, dict) and int(item.get("hwnd") or 0) == hwnd
    ), {})
    rect = _normalized_rect(window.get("rect"))
    failure_type = ""
    reason = ""
    if not hwnd or not window:
        failure_type = "target_binding_missing"
        reason = "no usable foreground application was visible after handoff"
    elif (
        len(rect) != 4
        or rect[2] <= rect[0]
        or rect[3] <= rect[1]
    ):
        failure_type = "target_binding_contract_invalid"
        reason = "foreground target geometry could not be established"
    elif exclude_process_id and process_id == int(exclude_process_id):
        failure_type = "control_surface_handoff_failed"
        reason = "native control surface still owns foreground"
    elif root_hwnd <= 0 or process_id <= 0 or not process_identity:
        failure_type = "target_process_identity_unavailable"
        reason = "foreground process instance identity could not be established"
    else:
        try:
            confirmed_hwnd = int(foreground_resolver() or 0)
            confirmed_root_hwnd = int(
                root_hwnd_resolver(confirmed_hwnd) or 0
            )
            confirmed_process_id = int(
                process_id_resolver(confirmed_hwnd) or 0
            )
            confirmed_process_identity = canonical_process_identity(
                process_identity_resolver(confirmed_process_id)
            )
        except (ImportError, OSError, RuntimeError, ValueError):
            confirmed_hwnd = 0
            confirmed_root_hwnd = 0
            confirmed_process_id = 0
            confirmed_process_identity = ""
        if (
            confirmed_hwnd != hwnd
            or confirmed_root_hwnd != root_hwnd
            or confirmed_process_id != process_id
            or confirmed_process_identity != process_identity
        ):
            failure_type = "target_identity_changed_during_capture"
            reason = "foreground target identity changed while it was captured"
    return {
        "format": "desktop-target/2",
        "hwnd": hwnd,
        "root_hwnd": root_hwnd,
        "title": str(window.get("title") or ""),
        "process_id": process_id,
        "process_identity": process_identity,
        "class_name": str(window.get("class_name") or ""),
        "rect": rect,
        "captured_at": captured_at,
        "foreground_verified": bool(hwnd and window),
        "usable": not failure_type,
        "failure_type": failure_type,
        "reason": reason,
    }


def validated_handoff_target(
    execution_context: dict[str, Any],
    *,
    window_exists_resolver: Callable[[int], bool] | None = None,
    now_ms: int | None = None,
    max_age_ms: int = 15_000,
) -> tuple[int, dict[str, Any] | None]:
    """Return a live compiler-independent HWND or a precise binding failure."""
    if str(execution_context.get("entrypoint") or "") != "native_agent_cockpit":
        return 0, None
    if execution_context.get("format") != "execution-context/1":
        return 0, {
            "failure_type": "execution_context_contract_invalid",
            "reason": "native desktop handoff uses an unsupported contract",
        }
    handoff = execution_context.get("desktop_handoff")
    if not isinstance(handoff, dict) or handoff.get("required") is not True:
        return 0, None
    if handoff.get("strategy") != "foreground_after_control_surface_yield":
        return 0, {
            "failure_type": "execution_context_contract_invalid",
            "reason": "native desktop handoff uses an unsupported strategy",
        }
    target = execution_context.get("target_window")
    target = target if isinstance(target, dict) else {}
    hwnd = int(target.get("hwnd") or 0)
    if target.get("usable") is not True or not hwnd:
        return 0, {
            "failure_type": str(target.get("failure_type") or "target_binding_missing"),
            "reason": str(target.get("reason") or "desktop target was not captured after handoff"),
        }
    if target.get("format") != "desktop-target/2":
        return 0, {
            "failure_type": "target_binding_contract_invalid",
            "reason": "captured desktop target uses an unsupported contract",
            "target_hwnd": hwnd,
        }
    process_id = target.get("process_id")
    root_hwnd = target.get("root_hwnd")
    try:
        canonical_process_identity(target.get("process_identity"))
    except ProcessIdentityError:
        return 0, {
            "failure_type": "target_process_identity_unavailable",
            "reason": "captured desktop target has no canonical process identity",
            "target_hwnd": hwnd,
        }
    if type(process_id) is not int or process_id <= 0:
        return 0, {
            "failure_type": "target_process_identity_unavailable",
            "reason": "captured desktop target has no valid process id",
            "target_hwnd": hwnd,
        }
    if type(root_hwnd) is not int or root_hwnd <= 0:
        return 0, {
            "failure_type": "target_binding_contract_invalid",
            "reason": "captured desktop target has no valid root window",
            "target_hwnd": hwnd,
        }
    if target.get("foreground_verified") is not True:
        return 0, {
            "failure_type": "target_binding_unverified",
            "reason": "captured desktop target was not foreground-verified",
            "target_hwnd": hwnd,
        }
    rect = target.get("rect")
    if (
        not isinstance(rect, list)
        or len(rect) != 4
        or any(type(value) is not int for value in rect)
        or rect[2] <= rect[0]
        or rect[3] <= rect[1]
    ):
        return 0, {
            "failure_type": "target_binding_contract_invalid",
            "reason": "captured desktop target geometry is invalid",
            "target_hwnd": hwnd,
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
    if (
        not captured_at
        or captured_at > current_ms
        or current_ms - captured_at > max(0, int(max_age_ms))
    ):
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
