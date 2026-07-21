"""At-most-once Task Profiler shadow coordinator.

The coordinator binds mission identity outside the model request and records a
candidate profile.  It never updates MissionProjection or legacy execution.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .contracts import (
    ExecutionProfile,
    InvalidProfileSchema,
    TaskProfileProposal,
    _require_mapping,
    _strict_keys,
    _text,
)
from .policy import ExecutionPolicy
from .profiler import TaskProfiler, TaskProfilerInput


TASK_PROFILER_SHADOW_RECORD_FORMAT = "task-profiler-shadow-record/0"


@dataclass(frozen=True, slots=True)
class TaskProfilerShadowRecord:
    mission_key: str
    status: str
    profile_call_count: int
    elapsed_ms: int | None = None
    proposal: TaskProfileProposal | None = None
    profile: ExecutionProfile | None = None
    error_kind: str | None = None
    format: str = TASK_PROFILER_SHADOW_RECORD_FORMAT

    def __post_init__(self) -> None:
        if self.format != TASK_PROFILER_SHADOW_RECORD_FORMAT:
            raise InvalidProfileSchema(f"unsupported profiler shadow record format {self.format!r}")
        object.__setattr__(self, "mission_key", _text(self.mission_key, "mission_key"))
        if self.status not in {"in_progress", "completed", "safe_default"}:
            raise InvalidProfileSchema("invalid profiler shadow status")
        if self.profile_call_count not in {0, 1}:
            raise InvalidProfileSchema("profile_call_count must be zero or one")
        if self.elapsed_ms is not None and (type(self.elapsed_ms) is not int or self.elapsed_ms < 0):
            raise InvalidProfileSchema("elapsed_ms must be a non-negative integer or null")
        if self.status == "completed" and (self.proposal is None or self.profile is None):
            raise InvalidProfileSchema("completed profiler shadow requires proposal and profile")
        if self.status == "safe_default" and self.profile is None:
            raise InvalidProfileSchema("safe_default profiler shadow requires profile")

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "mission_key": self.mission_key,
            "status": self.status,
            "profile_call_count": self.profile_call_count,
            "elapsed_ms": self.elapsed_ms,
            "proposal": None if self.proposal is None else self.proposal.to_dict(),
            "profile": None if self.profile is None else self.profile.to_dict(),
            "error_kind": self.error_kind,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "TaskProfilerShadowRecord":
        value = _require_mapping(raw, "profiler shadow record")
        fields = (
            "format",
            "mission_key",
            "status",
            "profile_call_count",
            "elapsed_ms",
            "proposal",
            "profile",
            "error_kind",
        )
        _strict_keys(value, fields, "profiler shadow record")
        proposal = (
            None
            if value["proposal"] is None
            else TaskProfileProposal.from_dict(value["proposal"])
        )
        profile = (
            None
            if value["profile"] is None
            else ExecutionProfile.from_dict(value["profile"])
        )
        return cls(
            format=value["format"],
            mission_key=value["mission_key"],
            status=value["status"],
            profile_call_count=value["profile_call_count"],
            elapsed_ms=value["elapsed_ms"],
            proposal=proposal,
            profile=profile,
            error_kind=value["error_kind"],
        )


class TaskProfilerShadowRuntime:
    """Thread-safe, bounded, at-most-once profiler shadow state."""

    def __init__(
        self,
        profiler: TaskProfiler,
        policy: ExecutionPolicy,
        *,
        max_records: int = 128,
    ) -> None:
        if not isinstance(profiler, TaskProfiler):
            raise TypeError("profiler must be TaskProfiler")
        if not isinstance(policy, ExecutionPolicy):
            raise TypeError("policy must be ExecutionPolicy")
        self._profiler = profiler
        self._policy = policy
        self._max_records = max(1, int(max_records))
        self._records: OrderedDict[str, TaskProfilerShadowRecord] = OrderedDict()
        self._lock = threading.RLock()

    def _put(self, key: str, record: TaskProfilerShadowRecord) -> None:
        self._records[key] = record
        self._records.move_to_end(key)
        while len(self._records) > self._max_records:
            self._records.popitem(last=False)

    def profile_once(
        self,
        *,
        mission_key: str,
        mission_id: str,
        goal_revision: int,
        profiler_input: TaskProfilerInput,
        available_capabilities: Iterable[str] | None = None,
        provider_availability: Iterable[str] | Mapping[str, bool] | None = None,
        stop_event: threading.Event | None = None,
    ) -> TaskProfilerShadowRecord:
        key = _text(mission_key, "mission_key")
        with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                self._records.move_to_end(key)
                return existing
            pending = TaskProfilerShadowRecord(
                mission_key=key,
                status="in_progress",
                profile_call_count=1,
            )
            # Register before the provider call. Failure, timeout, cancellation,
            # or concurrency therefore cannot schedule a second model request.
            self._put(key, pending)

        started = time.monotonic()
        try:
            proposal = self._profiler.profile(
                profiler_input,
                stop_event=stop_event,
            )
            profile = self._policy.evaluate(
                proposal,
                mission_id=mission_id,
                goal_revision=goal_revision,
                available_capabilities=available_capabilities,
                provider_availability=provider_availability,
            )
            record = TaskProfilerShadowRecord(
                mission_key=key,
                status="completed",
                profile_call_count=1,
                elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
                proposal=proposal,
                profile=profile,
            )
        except Exception as exc:
            profile = self._policy.safe_default(
                mission_id,
                goal_revision,
                reason="profiler_shadow_unavailable",
                available_capabilities=available_capabilities,
                provider_availability=provider_availability,
            )
            record = TaskProfilerShadowRecord(
                mission_key=key,
                status="safe_default",
                profile_call_count=1,
                elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
                profile=profile,
                error_kind=type(exc).__name__,
            )
        with self._lock:
            self._put(key, record)
        return record

    def get(self, mission_key: str) -> TaskProfilerShadowRecord | None:
        with self._lock:
            return self._records.get(str(mission_key))

    def close(self, mission_key: str) -> None:
        with self._lock:
            self._records.pop(str(mission_key), None)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)


__all__ = [
    "TASK_PROFILER_SHADOW_RECORD_FORMAT",
    "TaskProfilerShadowRecord",
    "TaskProfilerShadowRuntime",
]
