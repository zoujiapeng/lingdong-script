"""Read-only Task Profiler contract and injected model boundary.

The profiler receives no runtime identity, tools, frames, application routes,
or mutable mission state.  It makes one provider request per invocation and
returns a proposal that still has to pass :class:`ExecutionPolicy`.
"""
from __future__ import annotations

import threading
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .contracts import (
    TASK_PROFILE_PROPOSAL_FORMAT,
    InvalidProfileSchema,
    TaskProfileProposal,
    _require_mapping,
    _strict_keys,
    _string_tuple,
    _text,
)
from .policy import DEFAULT_CAPABILITY_CATALOG


TASK_PROFILER_INPUT_FORMAT = "task-profiler-input/0"
TASK_PROFILER_REQUEST_FORMAT = "task-profiler-request/0"
TASK_PROFILER_ROLE = "task_profiler"

TASK_PROFILER_SYSTEM_CONTRACT = """You are a read-only Task Profiler.
Describe only structural task dimensions, recommended/minimum execution depth,
read-only cognitive services, broad semantic capability hypotheses, and
semantic upgrade/downgrade conditions. Do not create or modify obligations.
Do not output application workflows, GUI steps, buttons, selectors, tools,
coordinates, next actions, runtime versions, or run/mission/frame identities.
Return exactly one JSON object matching the supplied schema."""


TASK_PROFILE_PROPOSAL_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "format",
        "dimensions",
        "recommended_depth",
        "minimum_depth",
        "requested_services",
        "broad_capability_hypotheses",
        "upgrade_triggers",
        "downgrade_conditions",
        "confidence",
        "rationale",
    ],
    "properties": {
        "format": {"const": TASK_PROFILE_PROPOSAL_FORMAT},
        "dimensions": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "step_dependency",
                "application_scope",
                "artifact_flow",
                "external_wait",
                "irreversibility",
                "visual_precision",
                "continuous_pointer",
                "completion_observability",
                "identity_binding",
                "user_interaction",
            ],
            "properties": {
                "step_dependency": {"enum": ["atomic", "local_sequence", "multi_stage"]},
                "application_scope": {"enum": ["current_surface", "single_application", "cross_application", "unknown"]},
                "artifact_flow": {"enum": ["none", "produce", "consume", "handoff"]},
                "external_wait": {"enum": ["none", "bounded", "persistent"]},
                "irreversibility": {"enum": ["low", "medium", "high"]},
                "visual_precision": {"enum": ["ordinary", "fine", "geometric"]},
                "continuous_pointer": {"enum": ["none", "atomic_drag", "cross_frame"]},
                "completion_observability": {"enum": ["deterministic", "visible", "mixed", "weak"]},
                "identity_binding": {"enum": ["none", "target", "recipient", "artifact", "multiple"]},
                "user_interaction": {"enum": ["none", "approval", "clarification", "ongoing"]},
            },
        },
        "recommended_depth": {"enum": ["direct", "standard", "orchestrated"]},
        "minimum_depth": {"enum": ["direct", "standard", "orchestrated"]},
        "requested_services": {
            "type": "object",
            "additionalProperties": False,
            "required": ["planner", "obligation_verifier", "mission_verifier", "visual_inspector", "deep_reasoning"],
            "properties": {
                name: {"type": "boolean"}
                for name in (
                    "planner",
                    "obligation_verifier",
                    "mission_verifier",
                    "visual_inspector",
                    "deep_reasoning",
                )
            },
        },
        "broad_capability_hypotheses": {
            "type": "array",
            "uniqueItems": True,
            "items": {"enum": sorted(DEFAULT_CAPABILITY_CATALOG)},
        },
        "upgrade_triggers": {"type": "array", "uniqueItems": True, "items": {"type": "string", "minLength": 1}},
        "downgrade_conditions": {"type": "array", "uniqueItems": True, "items": {"type": "string", "minLength": 1}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "rationale": {"type": "array", "uniqueItems": True, "items": {"type": "string", "minLength": 1}},
    },
}


@dataclass(frozen=True, slots=True)
class ProfilerObligationView:
    desired_state: str
    depends_on_count: int = 0
    required_capabilities: tuple[str, ...] = ()
    acceptance_criteria_count: int = 0
    forbidden_effect_count: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "desired_state", _text(self.desired_state, "desired_state"))
        for name in ("depends_on_count", "acceptance_criteria_count", "forbidden_effect_count"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise InvalidProfileSchema(f"{name} must be a non-negative integer")
        capabilities = _string_tuple(self.required_capabilities, "required_capabilities")
        unknown = set(capabilities) - DEFAULT_CAPABILITY_CATALOG
        if unknown:
            raise InvalidProfileSchema(f"unknown obligation capabilities: {sorted(unknown)!r}")
        object.__setattr__(self, "required_capabilities", capabilities)

    def to_dict(self) -> dict[str, Any]:
        return {
            "desired_state": self.desired_state,
            "depends_on_count": self.depends_on_count,
            "required_capabilities": list(self.required_capabilities),
            "acceptance_criteria_count": self.acceptance_criteria_count,
            "forbidden_effect_count": self.forbidden_effect_count,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ProfilerObligationView":
        value = _require_mapping(raw, "profiler obligation")
        _strict_keys(
            value,
            (
                "desired_state",
                "depends_on_count",
                "required_capabilities",
                "acceptance_criteria_count",
                "forbidden_effect_count",
            ),
            "profiler obligation",
        )
        return cls(**value)


@dataclass(frozen=True, slots=True)
class TaskProfilerInput:
    original_goal: str
    initial_obligations: tuple[ProfilerObligationView, ...]
    verified_session_facts: tuple[str, ...] = ()
    available_runtime_capabilities: tuple[str, ...] = ()
    safety_constraints: tuple[str, ...] = ()
    current_surface_summary: str | None = None
    format: str = TASK_PROFILER_INPUT_FORMAT

    def __post_init__(self) -> None:
        if self.format != TASK_PROFILER_INPUT_FORMAT:
            raise InvalidProfileSchema(f"unsupported profiler input format {self.format!r}")
        object.__setattr__(self, "original_goal", _text(self.original_goal, "original_goal"))
        if not isinstance(self.initial_obligations, (tuple, list)) or not self.initial_obligations:
            raise InvalidProfileSchema("initial_obligations must be a non-empty array")
        obligations = tuple(
            item if isinstance(item, ProfilerObligationView) else ProfilerObligationView.from_dict(item)
            for item in self.initial_obligations
        )
        object.__setattr__(self, "initial_obligations", obligations)
        for name in ("verified_session_facts", "available_runtime_capabilities", "safety_constraints"):
            object.__setattr__(self, name, _string_tuple(getattr(self, name), name))
        unknown = set(self.available_runtime_capabilities) - DEFAULT_CAPABILITY_CATALOG
        if unknown:
            raise InvalidProfileSchema(f"unknown runtime capabilities: {sorted(unknown)!r}")
        if self.current_surface_summary is not None:
            object.__setattr__(
                self,
                "current_surface_summary",
                _text(self.current_surface_summary, "current_surface_summary"),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "original_goal": self.original_goal,
            "initial_obligations": [item.to_dict() for item in self.initial_obligations],
            "verified_session_facts": list(self.verified_session_facts),
            "available_runtime_capabilities": list(self.available_runtime_capabilities),
            "safety_constraints": list(self.safety_constraints),
            "current_surface_summary": self.current_surface_summary,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "TaskProfilerInput":
        value = _require_mapping(raw, "profiler input")
        fields = (
            "format",
            "original_goal",
            "initial_obligations",
            "verified_session_facts",
            "available_runtime_capabilities",
            "safety_constraints",
            "current_surface_summary",
        )
        _strict_keys(value, fields, "profiler input")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class TaskProfilerRequest:
    input: TaskProfilerInput
    format: str = TASK_PROFILER_REQUEST_FORMAT
    model_role: str = TASK_PROFILER_ROLE
    system_contract: str = TASK_PROFILER_SYSTEM_CONTRACT
    tool_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.format != TASK_PROFILER_REQUEST_FORMAT:
            raise InvalidProfileSchema(f"unsupported profiler request format {self.format!r}")
        if self.model_role != TASK_PROFILER_ROLE:
            raise InvalidProfileSchema("profiler request has the wrong model role")
        if not isinstance(self.input, TaskProfilerInput):
            raise InvalidProfileSchema("profiler request input must be TaskProfilerInput")
        object.__setattr__(self, "system_contract", _text(self.system_contract, "system_contract"))
        if tuple(self.tool_names):
            raise InvalidProfileSchema("Task Profiler must not receive tools")
        object.__setattr__(self, "tool_names", ())

    def model_payload(self) -> dict[str, Any]:
        return {
            "model_role": self.model_role,
            "system_contract": self.system_contract,
            "input": self.input.to_dict(),
            "response_schema": deepcopy(TASK_PROFILE_PROPOSAL_JSON_SCHEMA),
            "tool_names": [],
        }


class TaskProfilerModel(Protocol):
    """Injected provider boundary. Implementations perform exactly one call."""

    def propose(
        self,
        request: TaskProfilerRequest,
        *,
        stop_event: threading.Event | None = None,
    ) -> Mapping[str, Any]: ...


class TaskProfiler:
    """One-shot read-only profiler; retry and lifecycle belong to Runtime."""

    def __init__(self, model: TaskProfilerModel) -> None:
        if not callable(getattr(model, "propose", None)):
            raise InvalidProfileSchema("TaskProfiler requires a propose(request) provider")
        self._model = model

    def profile(
        self,
        profiler_input: TaskProfilerInput,
        *,
        stop_event: threading.Event | None = None,
    ) -> TaskProfileProposal:
        request = TaskProfilerRequest(input=profiler_input)
        raw = self._model.propose(request, stop_event=stop_event)
        if not isinstance(raw, Mapping):
            raise InvalidProfileSchema("Task Profiler model output must be a JSON object")
        return TaskProfileProposal.from_dict(raw)


__all__ = [
    "TASK_PROFILER_INPUT_FORMAT",
    "TASK_PROFILER_REQUEST_FORMAT",
    "TASK_PROFILER_ROLE",
    "TASK_PROFILER_SYSTEM_CONTRACT",
    "TASK_PROFILE_PROPOSAL_JSON_SCHEMA",
    "ProfilerObligationView",
    "TaskProfilerInput",
    "TaskProfilerRequest",
    "TaskProfilerModel",
    "TaskProfiler",
]
