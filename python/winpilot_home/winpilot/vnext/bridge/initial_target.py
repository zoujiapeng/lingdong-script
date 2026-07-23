"""Strict initial desktop-target resolution for one vNext run.

This module classifies only the client handoff contract.  It does not discover,
select, bind, focus, or launch a target.  The raw execution context is checked
before normalization so malformed values cannot collapse into an apparently
valid targetless state.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
import time
from types import MappingProxyType
from typing import Any

from ...tasking.execution_context import (
    foreground_handoff_conflict,
    normalize_execution_context,
    validated_handoff_target,
    window_process_id,
)


INITIAL_TARGET_RESOLUTION_FORMAT = "initial-target-resolution/0"
_NATIVE_ENTRYPOINT = "native_agent_cockpit"
_FOREGROUND_HANDOFF = "foreground_after_control_surface_yield"
_NO_HANDOFF = "not_required"
_MISSING = object()
_CONTEXT_FIELDS = frozenset({
    "format",
    "entrypoint",
    "task_id",
    "submitted_at",
    "desktop_handoff",
    "target_window",
})
_HANDOFF_FIELDS = frozenset({
    "required",
    "strategy",
    "yielded",
    "control_surface_pid",
    "prepared_at",
    "reason",
})
_TARGET_FIELDS = frozenset({
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


class InitialTargetResolutionError(ValueError):
    """The supplied execution context cannot safely select an initial mode."""

    def __init__(self, failure_type: str, reason: str) -> None:
        if type(failure_type) is not str or not failure_type.strip():
            raise TypeError("failure_type must be non-empty text")
        if type(reason) is not str or not reason.strip():
            raise TypeError("reason must be non-empty text")
        self.failure_type = failure_type.strip()
        self.reason = reason.strip()
        super().__init__(self.reason)


class InitialTargetMode(str, Enum):
    BOUND = "bound"
    TARGETLESS = "targetless"


@dataclass(frozen=True, slots=True)
class InitialTargetResolution:
    mode: InitialTargetMode
    hwnd: int
    target: Mapping[str, Any] | None
    targetless_reason: str | None
    format: str = INITIAL_TARGET_RESOLUTION_FORMAT

    def __post_init__(self) -> None:
        if self.format != INITIAL_TARGET_RESOLUTION_FORMAT:
            raise ValueError("unsupported initial target resolution format")
        if not isinstance(self.mode, InitialTargetMode):
            raise TypeError("mode must be InitialTargetMode")
        if type(self.hwnd) is not int or self.hwnd < 0:
            raise TypeError("hwnd must be a non-negative integer")
        if self.mode is InitialTargetMode.BOUND:
            if self.hwnd <= 0 or not isinstance(self.target, Mapping):
                raise ValueError("bound resolution requires one positive target")
            if self.targetless_reason is not None:
                raise ValueError("bound resolution cannot have a targetless reason")
            frozen_target = dict(self.target)
            rect = frozen_target.get("rect")
            if isinstance(rect, list):
                frozen_target["rect"] = tuple(rect)
            object.__setattr__(
                self, "target", MappingProxyType(frozen_target),
            )
            return
        if (
            self.hwnd != 0
            or self.target is not None
            or self.targetless_reason not in {"optional", "absent"}
        ):
            raise ValueError(
                "targetless resolution requires one canonical targetless reason"
            )

    def target_dict(self) -> dict[str, Any] | None:
        """Return a canonical JSON-ready copy of the immutable bound target."""

        if self.target is None:
            return None
        result = dict(self.target)
        rect = result.get("rect")
        if isinstance(rect, tuple):
            result["rect"] = list(rect)
        return result


def _reject(failure_type: str, reason: str) -> InitialTargetResolutionError:
    return InitialTargetResolutionError(failure_type, reason)


def _normalized(raw: Mapping[str, Any]) -> dict[str, Any]:
    handoff = raw.get("desktop_handoff")
    target = raw.get("target_window", _MISSING)
    copied = dict(raw)
    if isinstance(handoff, Mapping):
        copied["desktop_handoff"] = dict(handoff)
    if isinstance(target, Mapping):
        copied["target_window"] = dict(target)
    return normalize_execution_context({"execution_context": copied})


def resolve_initial_target(
    raw_execution_context: object,
    *,
    window_exists_resolver: Callable[[int], bool] | None = None,
    process_id_resolver: Callable[[int], int] = window_process_id,
    now_ms: int | None = None,
    max_age_ms: int = 15_000,
) -> InitialTargetResolution:
    """Classify one raw client context as bound or explicitly targetless.

    Targetless execution is admitted only for an exact optional handoff or a
    fresh all-zero absence report produced after a required foreground yield.
    Malformed or partially populated target reports remain fail-closed.
    """

    if not isinstance(raw_execution_context, Mapping):
        raise _reject(
            "execution_context_contract_invalid",
            "execution context must be an object",
        )
    raw = raw_execution_context
    if (
        any(type(key) is not str for key in raw)
        or set(raw) not in {
            _CONTEXT_FIELDS,
            _CONTEXT_FIELDS - {"target_window"},
        }
    ):
        raise _reject(
            "execution_context_contract_invalid",
            "execution context fields do not match the canonical schema",
        )
    if raw.get("format") != "execution-context/1":
        raise _reject(
            "execution_context_contract_invalid",
            "execution context uses an unsupported contract",
        )
    if raw.get("entrypoint") != _NATIVE_ENTRYPOINT:
        raise _reject(
            "execution_context_contract_invalid",
            "execution context entrypoint is unsupported",
        )
    task_id = raw.get("task_id")
    submitted_at = raw.get("submitted_at")
    if (
        type(task_id) is not str
        or not task_id.strip()
        or len(task_id.strip()) > 192
        or type(submitted_at) is not int
        or submitted_at < 0
    ):
        raise _reject(
            "execution_context_contract_invalid",
            "execution context task identity is invalid",
        )
    handoff = raw.get("desktop_handoff")
    if not isinstance(handoff, Mapping):
        raise _reject(
            "execution_context_contract_invalid",
            "desktop handoff must be an object",
        )
    if (
        any(type(key) is not str for key in handoff)
        or set(handoff) != _HANDOFF_FIELDS
    ):
        raise _reject(
            "execution_context_contract_invalid",
            "desktop handoff fields do not match the canonical schema",
        )
    required = handoff.get("required")
    strategy = handoff.get("strategy")
    yielded = handoff.get("yielded")
    if type(required) is not bool:
        raise _reject(
            "execution_context_contract_invalid",
            "desktop handoff required must be boolean",
        )
    if type(strategy) is not str:
        raise _reject(
            "execution_context_contract_invalid",
            "desktop handoff strategy must be text",
        )
    if type(yielded) is not bool:
        raise _reject(
            "execution_context_contract_invalid",
            "desktop handoff yielded must be boolean",
        )
    control_surface_pid = handoff.get("control_surface_pid")
    prepared_at = handoff.get("prepared_at")
    reason = handoff.get("reason")
    if (
        type(control_surface_pid) is not int
        or control_surface_pid < 0
        or type(prepared_at) is not int
        or prepared_at < 0
        or type(reason) is not str
        or len(reason) > 512
    ):
        raise _reject(
            "execution_context_contract_invalid",
            "desktop handoff metadata is invalid",
        )
    if window_exists_resolver is not None and not callable(
        window_exists_resolver
    ):
        raise TypeError("window_exists_resolver must be callable or null")
    if not callable(process_id_resolver):
        raise TypeError("process_id_resolver must be callable")
    if now_ms is not None and (type(now_ms) is not int or now_ms < 0):
        raise TypeError("now_ms must be a non-negative integer or null")
    if type(max_age_ms) is not int or max_age_ms < 0:
        raise TypeError("max_age_ms must be a non-negative integer")
    current_ms = int(now_ms if now_ms is not None else time.time() * 1000)

    raw_target = raw.get("target_window", _MISSING)
    if not required:
        if strategy != _NO_HANDOFF:
            raise _reject(
                "execution_context_contract_invalid",
                "optional desktop handoff must use not_required",
            )
        if yielded:
            raise _reject(
                "execution_context_contract_invalid",
                "optional desktop handoff cannot report a foreground yield",
            )
        if control_surface_pid != 0 or prepared_at != 0:
            raise _reject(
                "execution_context_contract_invalid",
                "optional desktop handoff cannot carry yield metadata",
            )
        if raw_target is not _MISSING and raw_target is not None:
            raise _reject(
                "target_binding_contract_invalid",
                "optional desktop handoff cannot carry a target",
            )
        return InitialTargetResolution(
            mode=InitialTargetMode.TARGETLESS,
            hwnd=0,
            target=None,
            targetless_reason="optional",
        )

    if strategy != _FOREGROUND_HANDOFF:
        raise _reject(
            "execution_context_contract_invalid",
            "required desktop handoff uses an unsupported strategy",
        )
    if not yielded:
        raise _reject(
            "control_surface_handoff_failed",
            "required desktop handoff did not yield foreground",
        )
    if control_surface_pid <= 0 or prepared_at <= 0:
        raise _reject(
            "execution_context_contract_invalid",
            "required desktop handoff has no valid yield metadata",
        )
    if prepared_at > current_ms or current_ms - prepared_at > max_age_ms:
        raise _reject(
            "target_binding_stale",
            "required desktop handoff yield metadata is stale",
        )
    if raw_target is not _MISSING and raw_target is not None and not isinstance(
        raw_target, Mapping
    ):
        raise _reject(
            "target_binding_contract_invalid",
            "required desktop handoff target must be an object",
        )

    if isinstance(raw_target, Mapping) and set(raw_target) == _TARGET_FIELDS:
        captured_at = raw_target.get("captured_at")
        strict_absence = bool(
            raw_target.get("format") == "desktop-target/2"
            and raw_target.get("usable") is False
            and raw_target.get("foreground_verified") is False
            and raw_target.get("failure_type") == "target_binding_missing"
            and raw_target.get("hwnd") == 0
            and raw_target.get("root_hwnd") == 0
            and raw_target.get("process_id") == 0
            and raw_target.get("process_identity") == ""
            and raw_target.get("title") == ""
            and raw_target.get("class_name") == ""
            and raw_target.get("rect") == []
            and type(raw_target.get("reason")) is str
            and type(captured_at) is int
            and captured_at > 0
            and captured_at >= prepared_at
            and captured_at <= current_ms
            and current_ms - captured_at <= max_age_ms
        )
        if strict_absence:
            return InitialTargetResolution(
                mode=InitialTargetMode.TARGETLESS,
                hwnd=0,
                target=None,
                targetless_reason="absent",
            )

    context = _normalized(raw)
    validator_kwargs: dict[str, Any] = {
        "now_ms": now_ms,
        "max_age_ms": max_age_ms,
    }
    if window_exists_resolver is not None:
        validator_kwargs["window_exists_resolver"] = window_exists_resolver
    hwnd, failure = validated_handoff_target(context, **validator_kwargs)
    if not hwnd:
        failure = failure if isinstance(failure, Mapping) else {}
        raise _reject(
            str(failure.get("failure_type") or "target_binding_missing"),
            str(
                failure.get("reason")
                or "desktop target was not captured after handoff"
            ),
        )
    try:
        conflict = foreground_handoff_conflict(
            context,
            hwnd,
            process_id_resolver=process_id_resolver,
        )
    except Exception as exc:
        raise _reject(
            "target_binding_unverified",
            "desktop target process could not be verified",
        ) from exc
    if conflict is not None:
        raise _reject(
            str(
                conflict.get("failure_type")
                or "control_surface_handoff_failed"
            ),
            str(
                conflict.get("reason")
                or "desktop handoff target conflicts with the control surface"
            ),
        )
    target = context.get("target_window")
    if not isinstance(target, Mapping):
        raise _reject(
            "target_binding_contract_invalid",
            "validated desktop target is unavailable",
        )
    return InitialTargetResolution(
        mode=InitialTargetMode.BOUND,
        hwnd=hwnd,
        target=target,
        targetless_reason=None,
    )


__all__ = [
    "INITIAL_TARGET_RESOLUTION_FORMAT",
    "InitialTargetMode",
    "InitialTargetResolution",
    "InitialTargetResolutionError",
    "resolve_initial_target",
]
