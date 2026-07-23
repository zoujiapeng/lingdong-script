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


def capture_foreground_window_context(
    exclude_process_id: int = 0,
    *,
    facts_backend: Any | None = None,
    time_provider: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Capture one strict foreground target after a control-surface yield.

    The exact foreground HWND is read through the existing Windows desktop
    facts authority.  Filtered window inventory is never binding or absence
    evidence.
    """

    def result(
        *,
        hwnd: int,
        root_hwnd: int = 0,
        title: str = "",
        process_id: int = 0,
        process_identity: str = "",
        class_name: str = "",
        rect: list[int] | None = None,
        foreground_verified: bool = False,
        usable: bool = False,
        failure_type: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        return {
            "format": "desktop-target/2",
            "hwnd": int(hwnd),
            "root_hwnd": int(root_hwnd),
            "title": str(title),
            "process_id": int(process_id),
            "process_identity": str(process_identity),
            "class_name": str(class_name),
            "rect": list(rect or []),
            # Completion time, not observation-start time.
            "captured_at": int(time_provider() * 1000),
            "foreground_verified": bool(foreground_verified),
            "usable": bool(usable),
            "failure_type": str(failure_type),
            "reason": str(reason),
        }

    from ..visual_runtime.windows_desktop_facts import (
        PyWin32DesktopFactsBackend,
        WindowsDesktopFactsDriftError,
        WindowsDesktopFactsError,
        WindowsDesktopFactsReader,
        WindowsDesktopFactsSourceError,
    )

    backend = (
        facts_backend
        if facts_backend is not None
        else PyWin32DesktopFactsBackend()
    )
    facts_reader = WindowsDesktopFactsReader(backend)
    foreground_resolver = backend.foreground_hwnd
    shell_resolver = getattr(backend, "shell_hwnd", None)
    desktop_resolver = getattr(backend, "desktop_hwnd", None)
    if not callable(shell_resolver) or not callable(desktop_resolver):
        return result(
            hwnd=0,
            failure_type="target_observation_source_unavailable",
            reason="foreground shell identity source is unavailable",
        )

    def surface_snapshot() -> tuple[int, int, int]:
        return (
            int(foreground_resolver() or 0),
            int(shell_resolver() or 0),
            int(desktop_resolver() or 0),
        )

    observed_hwnd = 0
    try:
        observed_hwnd = int(foreground_resolver() or 0)
        initial_surface = (
            observed_hwnd,
            int(shell_resolver() or 0),
            int(desktop_resolver() or 0),
        )
    except Exception:
        return result(
            hwnd=observed_hwnd,
            failure_type="target_observation_source_unavailable",
            reason="foreground shell identity source is unavailable",
        )
    hwnd, shell_hwnd, desktop_hwnd = initial_surface
    if not hwnd:
        try:
            final_surface = surface_snapshot()
        except Exception:
            return result(
                hwnd=0,
                failure_type="target_observation_source_unavailable",
                reason="foreground shell identity source is unavailable",
            )
        if final_surface != initial_surface:
            return result(
                hwnd=final_surface[0],
                failure_type="target_identity_changed_during_capture",
                reason="foreground or shell identity changed while it was captured",
            )
        return result(
            hwnd=0,
            failure_type="target_binding_missing",
            reason="no foreground application was present after handoff",
        )
    if hwnd in {value for value in (shell_hwnd, desktop_hwnd) if value > 0}:
        try:
            final_surface = surface_snapshot()
        except Exception:
            return result(
                hwnd=hwnd,
                failure_type="target_observation_source_unavailable",
                reason="foreground shell identity source is unavailable",
            )
        if final_surface != initial_surface:
            return result(
                hwnd=final_surface[0] or hwnd,
                failure_type="target_identity_changed_during_capture",
                reason="foreground or shell identity changed while it was captured",
            )
        return result(
            hwnd=0,
            failure_type="target_binding_missing",
            reason="Windows shell or desktop owns foreground after handoff",
        )

    try:
        facts = facts_reader.read_activation_facts(
            hwnd,
            strict_foreground=True,
        )
        final_surface = surface_snapshot()
    except WindowsDesktopFactsSourceError:
        return result(
            hwnd=hwnd,
            failure_type="target_observation_source_unavailable",
            reason="foreground target facts source is unavailable",
        )
    except WindowsDesktopFactsDriftError:
        return result(
            hwnd=hwnd,
            failure_type="target_identity_changed_during_capture",
            reason="foreground target identity changed while it was captured",
        )
    except WindowsDesktopFactsError:
        return result(
            hwnd=hwnd,
            failure_type="target_binding_unverified",
            reason="nonzero foreground window could not be bound",
        )
    except Exception:
        return result(
            hwnd=hwnd,
            failure_type="target_observation_source_unavailable",
            reason="foreground target facts source is unavailable",
        )
    final_foreground, final_shell, final_desktop = final_surface
    if (
        final_surface != initial_surface
        or final_foreground != hwnd
        or final_foreground in {
            value for value in (final_shell, final_desktop) if value > 0
        }
        or not facts.exact_foreground
    ):
        return result(
            hwnd=final_foreground or hwnd,
            failure_type="target_identity_changed_during_capture",
            reason="foreground or shell identity changed while it was captured",
        )
    target = facts.target
    left, top, width, height = facts.window_region
    common = {
        "hwnd": target.hwnd,
        "root_hwnd": target.root_hwnd,
        "title": facts.title_snapshot,
        "process_id": target.process_id,
        "process_identity": target.process_identity,
        "rect": [left, top, left + width, top + height],
    }
    if exclude_process_id and target.process_id == int(exclude_process_id):
        return result(
            **common,
            failure_type="control_surface_handoff_failed",
            reason="native control surface still owns foreground",
        )
    return result(
        **common,
        foreground_verified=True,
        usable=True,
    )


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
