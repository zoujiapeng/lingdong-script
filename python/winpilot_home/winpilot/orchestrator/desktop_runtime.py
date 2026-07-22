"""Strict product seam for one selected desktop runtime coordinator.

The coordinator receives detached read-only task snapshots plus the existing
run cancellation and pause boundary.  It cannot reach ARP handlers or mutate an
``ArpExecution`` directly; terminal state returns through one typed result.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
import math
import re
import threading
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable


DESKTOP_COORDINATOR_CONTEXT_FORMAT = "desktop-runtime-coordinator-context/1"
DESKTOP_COORDINATOR_RESULT_FORMAT = "desktop-runtime-coordinator-result/0"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}")


class DesktopRuntimeCoordinatorContractError(ValueError):
    pass


def _freeze_json(value: Any, path: str) -> Any:
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise DesktopRuntimeCoordinatorContractError(f"{path} must be finite")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise DesktopRuntimeCoordinatorContractError(
                    f"{path} object keys must be strings"
                )
            frozen[key] = _freeze_json(item, f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, f"{path}[]") for item in value)
    raise DesktopRuntimeCoordinatorContractError(f"{path} must be JSON-compatible")


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


class DesktopRuntimeCoordinatorStatus(str, Enum):
    DONE = "done"
    FAILED = "failed"
    CANCELED = "canceled"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class DesktopRuntimeCoordinatorContext:
    """Detached inputs and the existing run-owned cooperative controls."""

    run_id: str
    graph: Mapping[str, Any]
    inputs: Mapping[str, Any]
    stop_event: threading.Event
    wait_if_paused: Callable[[], bool]
    is_paused: Callable[[], bool]
    format: str = DESKTOP_COORDINATOR_CONTEXT_FORMAT

    def __post_init__(self) -> None:
        if self.format != DESKTOP_COORDINATOR_CONTEXT_FORMAT:
            raise DesktopRuntimeCoordinatorContractError(
                "unsupported desktop coordinator context format"
            )
        if type(self.run_id) is not str or not _ID.fullmatch(self.run_id):
            raise DesktopRuntimeCoordinatorContractError("run_id is not a valid identity")
        if not isinstance(self.stop_event, threading.Event):
            raise DesktopRuntimeCoordinatorContractError("stop_event must be threading.Event")
        if not callable(self.wait_if_paused):
            raise DesktopRuntimeCoordinatorContractError("wait_if_paused must be callable")
        if not callable(self.is_paused):
            raise DesktopRuntimeCoordinatorContractError("is_paused must be callable")
        graph = _freeze_json(self.graph, "graph")
        inputs = _freeze_json(self.inputs, "inputs")
        if not isinstance(graph, Mapping) or not isinstance(inputs, Mapping):
            raise DesktopRuntimeCoordinatorContractError("graph and inputs must be objects")
        object.__setattr__(self, "graph", graph)
        object.__setattr__(self, "inputs", inputs)


@dataclass(frozen=True, slots=True)
class DesktopRuntimeCoordinatorResult:
    """One terminal result; non-success never becomes a legacy fallback."""

    status: DesktopRuntimeCoordinatorStatus | str
    output: Any = None
    error: str = ""
    format: str = DESKTOP_COORDINATOR_RESULT_FORMAT

    def __post_init__(self) -> None:
        if self.format != DESKTOP_COORDINATOR_RESULT_FORMAT:
            raise DesktopRuntimeCoordinatorContractError(
                "unsupported desktop coordinator result format"
            )
        try:
            status = DesktopRuntimeCoordinatorStatus(self.status)
        except (TypeError, ValueError) as exc:
            raise DesktopRuntimeCoordinatorContractError(
                "desktop coordinator returned a non-terminal status"
            ) from exc
        error = self.error.strip() if type(self.error) is str else ""
        if status is DesktopRuntimeCoordinatorStatus.DONE:
            if error:
                raise DesktopRuntimeCoordinatorContractError("done result cannot carry an error")
        elif not error or self.output is not None:
            raise DesktopRuntimeCoordinatorContractError(
                "non-success result requires an error and cannot carry output"
            )
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "error", error)
        object.__setattr__(self, "output", _freeze_json(self.output, "output"))

    def output_copy(self) -> Any:
        return _thaw_json(self.output)


@runtime_checkable
class DesktopRuntimeCoordinatorPort(Protocol):
    def run(
        self, context: DesktopRuntimeCoordinatorContext,
    ) -> DesktopRuntimeCoordinatorResult: ...


__all__ = [
    "DESKTOP_COORDINATOR_CONTEXT_FORMAT",
    "DESKTOP_COORDINATOR_RESULT_FORMAT",
    "DesktopRuntimeCoordinatorContractError",
    "DesktopRuntimeCoordinatorContext",
    "DesktopRuntimeCoordinatorPort",
    "DesktopRuntimeCoordinatorResult",
    "DesktopRuntimeCoordinatorStatus",
]
