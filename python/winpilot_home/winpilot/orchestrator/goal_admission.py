"""Server-owned admission contract for one natural-language product goal.

This boundary deliberately decides only the execution route.  It does not
compile a legacy task contract, decompose the goal, or infer application-
specific steps.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .contracts import ArpRuntimeVersion


GOAL_ADMISSION_FORMAT = "goal-admission/1"
_CLIENT_EXECUTION_CONTEXT_FIELDS = {
    "format",
    "entrypoint",
    "submitted_at",
    "desktop_handoff",
    "target_window",
}


class GoalAdmissionError(ValueError):
    """The submitted goal cannot be admitted under the requested constraint."""


class GoalAdmissionUnavailable(GoalAdmissionError):
    """The selected server-owned route is not currently available."""


class GoalExecutionRoute(str, Enum):
    LIVE_AGENT = "live_agent"


class GoalRunRequest(BaseModel):
    """Product-goal request accepted before graph/runtime projection."""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1)
    thread_id: str = Field(default="", max_length=128)


class DesktopLeaseRequest(BaseModel):
    """One exact external target captured after the control surface yields."""

    model_config = ConfigDict(extra="forbid")

    lease_id: str = Field(min_length=1, max_length=128)
    execution_context: dict[str, Any]


class DesktopLeaseFailureRequest(BaseModel):
    """Client-side failure to prepare one requested desktop lease."""

    model_config = ConfigDict(extra="forbid")

    lease_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=1024)


@dataclass(frozen=True, slots=True)
class GoalAdmissionDecision:
    goal: str
    route: GoalExecutionRoute
    runtime_version: ArpRuntimeVersion
    reason: str
    no_legacy_fallback: bool = True
    format: str = GOAL_ADMISSION_FORMAT

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "goal": self.goal,
            "route": self.route.value,
            "runtime_version": self.runtime_version.value,
            "reason": self.reason,
            "no_legacy_fallback": self.no_legacy_fallback,
        }


def admit_goal(
    goal: str,
    *,
    live_gui_enabled: bool,
) -> GoalAdmissionDecision:
    """Choose the current server-owned route without inspecting task keywords."""

    if type(goal) is not str:
        raise GoalAdmissionError("goal must be a literal string")
    normalized_goal = goal.strip()
    if not normalized_goal:
        raise GoalAdmissionError("goal must not be empty")
    if live_gui_enabled is not True:
        raise GoalAdmissionUnavailable(
            "vNext live Agent is disabled; no legacy fallback was run"
        )
    return GoalAdmissionDecision(
        goal=normalized_goal,
        route=GoalExecutionRoute.LIVE_AGENT,
        runtime_version=ArpRuntimeVersion.VNEXT,
        reason="single_semantic_agent",
    )


def bind_client_execution_context(
    execution_context: dict[str, Any],
    *,
    task_id: str,
) -> dict[str, Any]:
    """Bind a client-owned handoff context to the server-owned graph identity."""

    if type(execution_context) is not dict:
        raise GoalAdmissionError("execution_context must be a plain JSON object")
    context = deepcopy(execution_context)
    if type(context) is not dict:  # pragma: no cover - validator invariant
        raise GoalAdmissionError("execution_context must be a plain JSON object")
    actual = set(context)
    if actual != _CLIENT_EXECUTION_CONTEXT_FIELDS:
        raise GoalAdmissionError(
            "execution_context fields mismatch: "
            f"missing={sorted(_CLIENT_EXECUTION_CONTEXT_FIELDS - actual)!r}, "
            f"unknown={sorted(actual - _CLIENT_EXECUTION_CONTEXT_FIELDS)!r}"
        )
    if type(task_id) is not str or not task_id:
        raise GoalAdmissionError("server-owned task_id must be non-empty")
    return {
        "execution_context": {
            **context,
            "task_id": task_id,
        },
    }


__all__ = [
    "GOAL_ADMISSION_FORMAT",
    "GoalAdmissionDecision",
    "GoalAdmissionError",
    "GoalAdmissionUnavailable",
    "GoalExecutionRoute",
    "GoalRunRequest",
    "DesktopLeaseFailureRequest",
    "DesktopLeaseRequest",
    "admit_goal",
    "bind_client_execution_context",
]
