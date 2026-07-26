"""Strict, versioned P2a profiling contracts.

The package is intentionally a sidecar with no model, GUI, controller,
runtime, or legacy imports.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

from ..capability_names import (
    CAPABILITY_PATTERN,
    InvalidCapability,
    canonical_capabilities,
)
from ..json_contract import (
    BoundedJSONError,
    MAX_SIGNED_64,
    bounded_json_copy,
)


TASK_PROFILE_PROPOSAL_FORMAT = "task-profile-proposal/0"
EXECUTION_PROFILE_FORMAT = "execution-profile/0"


class ExecutionProfileError(ValueError):
    """Base error raised by the pure P2a contracts."""


class InvalidProfileSchema(ExecutionProfileError):
    """The wire value is not the canonical schema for its contract."""


class UnsupportedCapability(ExecutionProfileError):
    """A proposal contains a capability outside the injected catalog."""


class ExecutionDepth(str, Enum):
    DIRECT = "direct"
    STANDARD = "standard"
    ORCHESTRATED = "orchestrated"

    @property
    def rank(self) -> int:
        return {"direct": 0, "standard": 1, "orchestrated": 2}[self.value]


_DEPTH_VALUES = tuple(item.value for item in ExecutionDepth)
_DEPTH_BY_VALUE = {item.value: item for item in ExecutionDepth}


def _bounded_object(value: Any, name: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise InvalidProfileSchema(f"{name} must be a plain JSON object")
    try:
        canonical = bounded_json_copy(value)
    except (BoundedJSONError, RecursionError) as exc:
        raise InvalidProfileSchema(
            f"{name} violates the bounded JSON contract: {exc}"
        ) from exc
    if type(canonical) is not dict:  # pragma: no cover - validator invariant
        raise InvalidProfileSchema(f"{name} must be a plain JSON object")
    return canonical


def _strict_keys(value: Mapping[str, Any], expected: Iterable[str], name: str) -> None:
    expected_set = set(expected)
    actual = set(value)
    missing = expected_set - actual
    extra = actual - expected_set
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing={sorted(missing)!r}")
        if extra:
            details.append(f"unknown={sorted(extra)!r}")
        raise InvalidProfileSchema(f"{name} has non-canonical fields ({', '.join(details)})")


def _text(value: Any, name: str) -> str:
    if type(value) is not str:
        raise InvalidProfileSchema(f"{name} must be a literal string")
    if not value.strip():
        raise InvalidProfileSchema(f"{name} must be a non-empty string")
    return value


def _string_tuple(value: Any, name: str, *, unique: bool = True) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise InvalidProfileSchema(f"{name} must be an array of strings")
    result = tuple(_text(item, f"{name}[]") for item in value)
    if unique and len(result) != len(set(result)):
        raise InvalidProfileSchema(f"{name} must not contain duplicates")
    return result


def _finite_confidence(value: Any, name: str, *, nullable: bool = False) -> float | None:
    if value is None and nullable:
        return None
    if type(value) is not float or not math.isfinite(value):
        raise InvalidProfileSchema(f"{name} must be a finite literal float in [0, 1]")
    if not 0.0 <= value <= 1.0:
        raise InvalidProfileSchema(f"{name} must be a finite literal float in [0, 1]")
    return 0.0 if value == 0.0 else value


def _bool(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise InvalidProfileSchema(f"{name} must be a boolean")
    return value


def _depth(value: Any, name: str) -> ExecutionDepth:
    if isinstance(value, ExecutionDepth):
        return value
    if not isinstance(value, str) or value not in _DEPTH_BY_VALUE:
        raise InvalidProfileSchema(f"{name} must be one of {_DEPTH_VALUES!r}")
    return _DEPTH_BY_VALUE[value]


_DIMENSION_VALUES: dict[str, frozenset[str]] = {
    "step_dependency": frozenset(("atomic", "local_sequence", "multi_stage")),
    "application_scope": frozenset(("current_surface", "single_application", "cross_application", "unknown")),
    "artifact_flow": frozenset(("none", "produce", "consume", "handoff")),
    "external_wait": frozenset(("none", "bounded", "persistent")),
    "irreversibility": frozenset(("low", "medium", "high")),
    "visual_precision": frozenset(("ordinary", "fine", "geometric")),
    "continuous_pointer": frozenset(("none", "atomic_drag", "cross_frame")),
    "completion_observability": frozenset(("deterministic", "visible", "mixed", "weak")),
    "identity_binding": frozenset(("none", "target", "recipient", "artifact", "multiple")),
    "user_interaction": frozenset(("none", "approval", "clarification", "ongoing")),
}
@dataclass(frozen=True, slots=True)
class TaskDimensions:
    step_dependency: str = "atomic"
    application_scope: str = "current_surface"
    artifact_flow: str = "none"
    external_wait: str = "none"
    irreversibility: str = "low"
    visual_precision: str = "ordinary"
    continuous_pointer: str = "none"
    completion_observability: str = "visible"
    identity_binding: str = "none"
    user_interaction: str = "none"

    def __post_init__(self) -> None:
        for name, allowed in _DIMENSION_VALUES.items():
            value = _text(getattr(self, name), f"dimensions.{name}")
            if value not in allowed:
                raise InvalidProfileSchema(f"dimensions.{name} must be one of {sorted(allowed)!r}")
            object.__setattr__(self, name, value)

    def to_dict(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in _DIMENSION_VALUES}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TaskDimensions":
        value = _bounded_object(raw, "dimensions")
        _strict_keys(value, _DIMENSION_VALUES, "dimensions")
        return cls(**{name: value[name] for name in _DIMENSION_VALUES})


@dataclass(frozen=True, slots=True)
class CognitiveServices:
    planner: bool = False
    obligation_verifier: bool = True
    mission_verifier: bool = False
    visual_inspector: bool = False
    deep_reasoning: bool = False

    _FIELDS = ("planner", "obligation_verifier", "mission_verifier", "visual_inspector", "deep_reasoning")

    def __post_init__(self) -> None:
        for name in self._FIELDS:
            object.__setattr__(self, name, _bool(getattr(self, name), f"services.{name}"))

    def to_dict(self) -> dict[str, bool]:
        return {name: getattr(self, name) for name in self._FIELDS}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CognitiveServices":
        value = _bounded_object(raw, "requested_services")
        _strict_keys(value, cls._FIELDS, "requested_services")
        return cls(**{name: _bool(value[name], f"services.{name}") for name in cls._FIELDS})


@dataclass(frozen=True, slots=True)
class ProfileTrigger:
    """A semantic runtime condition; it intentionally has no GUI route."""

    condition: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "condition", _text(self.condition, "trigger.condition"))

    def to_dict(self) -> dict[str, str]:
        return {"condition": self.condition}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ProfileTrigger":
        value = _bounded_object(raw, "trigger")
        _strict_keys(value, ("condition",), "trigger")
        return cls(value["condition"])


_PROPOSAL_FIELDS = (
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
)
_FORBIDDEN_PROPOSAL_KEYS = frozenset({
    "runtime_version", "runtime_id", "application_workflow", "tool_sequence", "tool", "tools",
    "tool_name", "button", "buttons", "selector", "selectors", "coordinate", "coordinates",
    "click_point", "drag_path", "mouse", "scroll", "keypress", "next_action", "action",
    "actions", "mission_patch", "obligation_patch", "obligations", "obligation_id",
    "obligation_ids", "mission_id", "run_id", "revision", "run_revision", "goal_revision",
    "active_obligation_id", "frame_id", "frame_epoch", "epoch", "hwnd", "process_id",
    "window_handle", "window_title", "evidence_refs", "profile_id", "execution_profile_id",
    "target_binding",
})


def _reject_forbidden_keys(value: Any) -> None:
    stack = [value]
    while stack:
        item = stack.pop()
        if type(item) is dict:
            for key, nested in item.items():
                if key in _FORBIDDEN_PROPOSAL_KEYS:
                    raise InvalidProfileSchema(
                        f"proposal contains forbidden field {key!r}"
                    )
                stack.append(nested)
        elif type(item) is list:
            stack.extend(item)


def _capability_tuple(value: Any) -> tuple[str, ...]:
    result = _string_tuple(value, "broad_capability_hypotheses")
    for capability in result:
        if not CAPABILITY_PATTERN.fullmatch(capability):
            raise InvalidProfileSchema(f"non-canonical capability {capability!r}")
    return tuple(sorted(result))


def _accepted_capability_tuple(value: Any) -> tuple[str, ...]:
    result = _capability_tuple(value)
    try:
        return canonical_capabilities(result)
    except InvalidCapability as exc:
        raise InvalidProfileSchema(
            f"accepted_capabilities is not canonical: {exc}"
        ) from exc


@dataclass(frozen=True, slots=True)
class TaskProfileProposal:
    dimensions: TaskDimensions
    recommended_depth: ExecutionDepth | str = ExecutionDepth.STANDARD
    minimum_depth: ExecutionDepth | str = ExecutionDepth.DIRECT
    requested_services: CognitiveServices = CognitiveServices()
    broad_capability_hypotheses: tuple[str, ...] = ()
    upgrade_triggers: tuple[str, ...] = ()
    downgrade_conditions: tuple[str, ...] = ()
    confidence: float = 0.0
    rationale: tuple[str, ...] = ()
    format: str = TASK_PROFILE_PROPOSAL_FORMAT

    def __post_init__(self) -> None:
        object.__setattr__(self, "format", _text(self.format, "proposal.format"))
        if self.format != TASK_PROFILE_PROPOSAL_FORMAT:
            raise InvalidProfileSchema(f"unsupported proposal format {self.format!r}")
        dimensions = self.dimensions
        if isinstance(dimensions, Mapping):
            dimensions = TaskDimensions.from_dict(dimensions)
        if not isinstance(dimensions, TaskDimensions):
            raise InvalidProfileSchema("proposal.dimensions must be TaskDimensions")
        object.__setattr__(self, "dimensions", dimensions)
        recommended = _depth(self.recommended_depth, "recommended_depth")
        minimum = _depth(self.minimum_depth, "minimum_depth")
        if minimum.rank > recommended.rank:
            raise InvalidProfileSchema("minimum_depth cannot exceed recommended_depth")
        object.__setattr__(self, "recommended_depth", recommended)
        object.__setattr__(self, "minimum_depth", minimum)
        services = self.requested_services
        if isinstance(services, Mapping):
            services = CognitiveServices.from_dict(services)
        if not isinstance(services, CognitiveServices):
            raise InvalidProfileSchema("requested_services must be CognitiveServices")
        object.__setattr__(self, "requested_services", services)
        object.__setattr__(self, "broad_capability_hypotheses", _capability_tuple(self.broad_capability_hypotheses))
        object.__setattr__(self, "upgrade_triggers", _string_tuple(self.upgrade_triggers, "upgrade_triggers"))
        object.__setattr__(self, "downgrade_conditions", _string_tuple(self.downgrade_conditions, "downgrade_conditions"))
        object.__setattr__(self, "confidence", _finite_confidence(self.confidence, "confidence"))
        object.__setattr__(self, "rationale", _string_tuple(self.rationale, "rationale"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "dimensions": self.dimensions.to_dict(),
            "recommended_depth": self.recommended_depth.value,
            "minimum_depth": self.minimum_depth.value,
            "requested_services": self.requested_services.to_dict(),
            "broad_capability_hypotheses": list(self.broad_capability_hypotheses),
            "upgrade_triggers": list(self.upgrade_triggers),
            "downgrade_conditions": list(self.downgrade_conditions),
            "confidence": self.confidence,
            "rationale": list(self.rationale),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TaskProfileProposal":
        value = _bounded_object(raw, "proposal")
        _reject_forbidden_keys(value)
        _strict_keys(value, _PROPOSAL_FIELDS, "proposal")
        for name in (
            "broad_capability_hypotheses",
            "upgrade_triggers",
            "downgrade_conditions",
            "rationale",
        ):
            if type(value[name]) is not list:
                raise InvalidProfileSchema(f"proposal.{name} must be a JSON array")
        return cls(
            format=value["format"],
            dimensions=TaskDimensions.from_dict(value["dimensions"]),
            recommended_depth=value["recommended_depth"],
            minimum_depth=value["minimum_depth"],
            requested_services=CognitiveServices.from_dict(value["requested_services"]),
            broad_capability_hypotheses=value["broad_capability_hypotheses"],
            upgrade_triggers=value["upgrade_triggers"],
            downgrade_conditions=value["downgrade_conditions"],
            confidence=value["confidence"],
            rationale=value["rationale"],
        )


_PROFILE_FIELDS = (
    "format", "profile_id", "mission_id", "goal_revision", "accepted_depth", "services",
    "accepted_capabilities", "upgrade_triggers", "downgrade_conditions", "source", "confidence",
    "policy_reasons",
)
_PROFILE_SOURCES = frozenset(("task_profiler", "safe_default", "runtime_revision"))


def _execution_profile_id(
    *,
    mission_id: str,
    goal_revision: int,
    accepted_depth: ExecutionDepth,
    services: CognitiveServices,
    accepted_capabilities: tuple[str, ...],
    upgrade_triggers: tuple[ProfileTrigger, ...],
    downgrade_conditions: tuple[ProfileTrigger, ...],
    source: str,
    confidence: float | None,
    policy_reasons: tuple[str, ...],
) -> str:
    canonical = json.dumps(
        {
            "format": EXECUTION_PROFILE_FORMAT,
            "mission_id": mission_id,
            "goal_revision": goal_revision,
            "accepted_depth": accepted_depth.value,
            "services": services.to_dict(),
            "accepted_capabilities": list(accepted_capabilities),
            "upgrade_triggers": [item.to_dict() for item in upgrade_triggers],
            "downgrade_conditions": [
                item.to_dict() for item in downgrade_conditions
            ],
            "source": source,
            "confidence": confidence,
            "policy_reasons": list(policy_reasons),
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return "profile-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True, slots=True)
class ExecutionProfile:
    """Immutable, content-addressed policy decision.

    Runtime projection identities belong to Runtime envelopes, not this value.
    """

    format: str
    profile_id: str
    mission_id: str
    goal_revision: int
    accepted_depth: ExecutionDepth | str
    services: CognitiveServices
    accepted_capabilities: tuple[str, ...]
    upgrade_triggers: tuple[ProfileTrigger, ...]
    downgrade_conditions: tuple[ProfileTrigger, ...]
    source: str
    confidence: float | None
    policy_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.format != EXECUTION_PROFILE_FORMAT:
            raise InvalidProfileSchema(f"unsupported profile format {self.format!r}")
        object.__setattr__(self, "format", _text(self.format, "profile.format"))
        claimed_profile_id = _text(self.profile_id, "profile.profile_id")
        object.__setattr__(self, "profile_id", claimed_profile_id)
        object.__setattr__(self, "mission_id", _text(self.mission_id, "profile.mission_id"))
        if (
            type(self.goal_revision) is not int
            or not 0 <= self.goal_revision <= MAX_SIGNED_64
        ):
            raise InvalidProfileSchema(
                "profile.goal_revision must be a non-negative signed-64 integer"
            )
        object.__setattr__(self, "accepted_depth", _depth(self.accepted_depth, "accepted_depth"))
        services = self.services
        if isinstance(services, Mapping):
            services = CognitiveServices.from_dict(services)
        if not isinstance(services, CognitiveServices):
            raise InvalidProfileSchema("profile.services must be CognitiveServices")
        object.__setattr__(self, "services", services)
        object.__setattr__(
            self,
            "accepted_capabilities",
            _accepted_capability_tuple(self.accepted_capabilities),
        )
        for name in ("upgrade_triggers", "downgrade_conditions"):
            raw_triggers = getattr(self, name)
            if isinstance(raw_triggers, (str, bytes)) or not isinstance(raw_triggers, (list, tuple)):
                raise InvalidProfileSchema(f"profile.{name} must be an array")
        triggers = tuple(self.upgrade_triggers)
        downgrades = tuple(self.downgrade_conditions)
        if any(type(item) is not ProfileTrigger for item in (*triggers, *downgrades)):
            raise InvalidProfileSchema(
                "profile triggers must be ProfileTrigger values"
            )
        object.__setattr__(self, "upgrade_triggers", triggers)
        object.__setattr__(self, "downgrade_conditions", downgrades)
        source = _text(self.source, "profile.source")
        if source not in _PROFILE_SOURCES:
            raise InvalidProfileSchema(f"profile.source must be one of {sorted(_PROFILE_SOURCES)!r}")
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "confidence", _finite_confidence(self.confidence, "profile.confidence", nullable=True))
        object.__setattr__(self, "policy_reasons", _string_tuple(self.policy_reasons, "policy_reasons"))
        expected_profile_id = _execution_profile_id(
            mission_id=self.mission_id,
            goal_revision=self.goal_revision,
            accepted_depth=self.accepted_depth,
            services=self.services,
            accepted_capabilities=self.accepted_capabilities,
            upgrade_triggers=self.upgrade_triggers,
            downgrade_conditions=self.downgrade_conditions,
            source=self.source,
            confidence=self.confidence,
            policy_reasons=self.policy_reasons,
        )
        if claimed_profile_id != expected_profile_id:
            raise InvalidProfileSchema(
                "profile.profile_id does not identify the canonical profile content"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "profile_id": self.profile_id,
            "mission_id": self.mission_id,
            "goal_revision": self.goal_revision,
            "accepted_depth": self.accepted_depth.value,
            "services": self.services.to_dict(),
            "accepted_capabilities": list(self.accepted_capabilities),
            "upgrade_triggers": [item.to_dict() for item in self.upgrade_triggers],
            "downgrade_conditions": [item.to_dict() for item in self.downgrade_conditions],
            "source": self.source,
            "confidence": self.confidence,
            "policy_reasons": list(self.policy_reasons),
        }

    def structural_dict(self) -> dict[str, Any]:
        """Return routing structure without run identity or audit provenance."""
        return {
            "accepted_depth": self.accepted_depth.value,
            "services": self.services.to_dict(),
            "accepted_capabilities": list(self.accepted_capabilities),
            "upgrade_triggers": [item.to_dict() for item in self.upgrade_triggers],
            "downgrade_conditions": [item.to_dict() for item in self.downgrade_conditions],
        }

    @property
    def structural_fingerprint(self) -> str:
        canonical = json.dumps(
            self.structural_dict(), sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ExecutionProfile":
        value = _bounded_object(raw, "profile")
        _strict_keys(value, _PROFILE_FIELDS, "profile")
        for name in (
            "accepted_capabilities",
            "upgrade_triggers",
            "downgrade_conditions",
            "policy_reasons",
        ):
            if type(value[name]) is not list:
                raise InvalidProfileSchema(f"profile.{name} must be a JSON array")
        return cls(
            format=value["format"],
            profile_id=value["profile_id"],
            mission_id=value["mission_id"],
            goal_revision=value["goal_revision"],
            accepted_depth=value["accepted_depth"],
            services=CognitiveServices.from_dict(value["services"]),
            accepted_capabilities=value["accepted_capabilities"],
            upgrade_triggers=tuple(ProfileTrigger.from_dict(item) for item in value["upgrade_triggers"]),
            downgrade_conditions=tuple(ProfileTrigger.from_dict(item) for item in value["downgrade_conditions"]),
            source=value["source"],
            confidence=value["confidence"],
            policy_reasons=value["policy_reasons"],
        )

__all__ = [
    "TASK_PROFILE_PROPOSAL_FORMAT",
    "EXECUTION_PROFILE_FORMAT",
    "ExecutionProfileError",
    "InvalidProfileSchema",
    "UnsupportedCapability",
    "ExecutionDepth",
    "TaskDimensions",
    "CognitiveServices",
    "ProfileTrigger",
    "TaskProfileProposal",
    "ExecutionProfile",
]
