"""Deterministic P2a execution policy.

The policy validates semantic proposals against injected runtime facts.  It
does not understand applications, inspect pixels, call providers, or produce
plans and GUI actions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ..capability_names import CANONICAL_CAPABILITIES
from ..json_contract import MAX_SIGNED_64

from .contracts import (
    CognitiveServices,
    EXECUTION_PROFILE_FORMAT,
    ExecutionDepth,
    ExecutionProfile,
    ExecutionProfileError,
    InvalidProfileSchema,
    ProfileTrigger,
    TaskProfileProposal,
    UnsupportedCapability,
    _execution_profile_id,
    _finite_confidence,
    _text,
)

DEFAULT_CAPABILITY_CATALOG = CANONICAL_CAPABILITIES
_SERVICE_NAMES = tuple(CognitiveServices._FIELDS)
_SERVICE_FIELDS = frozenset(_SERVICE_NAMES)


def _normalize_available(values: Any, *, name: str) -> frozenset[str]:
    if values is None:
        return frozenset()
    if isinstance(values, Mapping):
        if type(values) is not dict:
            raise InvalidProfileSchema(f"{name} must be a plain JSON object")
        result: set[str] = set()
        seen: set[str] = set()
        for raw_key, enabled in values.items():
            key = _text(raw_key, f"{name}[]").strip()
            if not key:
                raise InvalidProfileSchema(f"{name}[] must be a non-empty string")
            if key in seen:
                raise InvalidProfileSchema(
                    f"{name} contains duplicate normalized key {key!r}"
                )
            seen.add(key)
            if type(enabled) is not bool:
                raise InvalidProfileSchema(f"{name}[{key!r}] must be boolean")
            if enabled:
                result.add(key)
        return frozenset(result)
    if isinstance(values, (str, bytes)):
        raise InvalidProfileSchema(f"{name} must be a collection, not a string")
    if not isinstance(values, (list, tuple, set, frozenset)):
        raise InvalidProfileSchema(f"{name} must be a finite collection")
    normalized = tuple(_text(item, f"{name}[]").strip() for item in values)
    if any(not item for item in normalized):
        raise InvalidProfileSchema(f"{name}[] must be a non-empty string")
    if len(normalized) != len(set(normalized)):
        raise InvalidProfileSchema(f"{name} must not contain duplicate values")
    return frozenset(normalized)


def _validate_mission_identity(mission_id: str, goal_revision: int) -> tuple[str, int]:
    mission = _text(mission_id, "mission_id")
    if type(goal_revision) is not int or not 0 <= goal_revision <= MAX_SIGNED_64:
        raise InvalidProfileSchema(
            "goal_revision must be a non-negative signed-64 integer"
        )
    return mission, goal_revision


@dataclass(frozen=True, slots=True, init=False)
class ExecutionPolicy:
    """Fail-closed deterministic gate from proposal to accepted profile."""

    provider_availability: frozenset[str]
    capability_catalog: frozenset[str]
    min_confidence: float

    def __init__(
        self,
        *,
        capability_catalog: Iterable[str] | None = None,
        provider_availability: Iterable[str] | Mapping[str, bool] | None = None,
        min_confidence: float = 0.5,
    ) -> None:
        catalog = (
            DEFAULT_CAPABILITY_CATALOG
            if capability_catalog is None
            else _normalize_available(capability_catalog, name="capability_catalog")
        )
        if not catalog:
            raise InvalidProfileSchema("capability_catalog cannot be empty")
        unknown_catalog = sorted(catalog - CANONICAL_CAPABILITIES)
        if unknown_catalog:
            raise InvalidProfileSchema(
                "capability_catalog contains unknown canonical capabilities: "
                f"{unknown_catalog!r}"
            )
        # P2a has no connected cognitive provider.  Availability must therefore
        # be explicitly injected instead of being inferred from requested roles.
        normalized_providers = _normalize_available(
            provider_availability, name="provider_availability"
        )
        if not normalized_providers <= _SERVICE_FIELDS:
            raise InvalidProfileSchema(
                "provider_availability contains an unknown cognitive service"
            )
        confidence = _finite_confidence(min_confidence, "min_confidence")
        assert confidence is not None
        object.__setattr__(self, "provider_availability", normalized_providers)
        object.__setattr__(self, "capability_catalog", frozenset(catalog))
        object.__setattr__(self, "min_confidence", confidence)

    def _runtime_capabilities(self, values: Iterable[str] | None) -> frozenset[str]:
        available = _normalize_available(values, name="available_capabilities")
        unknown = sorted(available - self.capability_catalog)
        if unknown:
            raise UnsupportedCapability(
                f"runtime availability contains unknown capabilities: {unknown!r}"
            )
        return available

    def _runtime_providers(
        self,
        provider_availability: Iterable[str] | Mapping[str, bool] | None,
    ) -> frozenset[str]:
        providers = (
            self.provider_availability
            if provider_availability is None
            else _normalize_available(
                provider_availability, name="provider_availability"
            )
        )
        if not providers <= _SERVICE_FIELDS:
            raise InvalidProfileSchema(
                "provider_availability contains an unknown cognitive service"
            )
        expanded = sorted(providers - self.provider_availability)
        if expanded:
            raise InvalidProfileSchema(
                "provider_availability may only tighten the policy snapshot: "
                f"{expanded!r}"
            )
        return providers

    def validate_proposal(
        self, proposal: TaskProfileProposal | Mapping[str, Any]
    ) -> TaskProfileProposal:
        if isinstance(proposal, Mapping):
            proposal = TaskProfileProposal.from_dict(proposal)
        if not isinstance(proposal, TaskProfileProposal):
            raise InvalidProfileSchema(
                "proposal must be TaskProfileProposal or its canonical mapping"
            )
        unsupported = sorted(
            set(proposal.broad_capability_hypotheses) - self.capability_catalog
        )
        if unsupported:
            raise UnsupportedCapability(
                f"unsupported capability hypotheses: {unsupported!r}"
            )
        return proposal

    def safe_default(
        self,
        mission_id: str,
        goal_revision: int,
        *,
        reason: str = "safe_standard_default",
        available_capabilities: Iterable[str] | None = None,
        provider_availability: Iterable[str] | Mapping[str, bool] | None = None,
    ) -> ExecutionProfile:
        mission, revision = _validate_mission_identity(mission_id, goal_revision)
        available = self._runtime_capabilities(available_capabilities)
        providers = self._runtime_providers(provider_availability)
        capabilities = tuple(
            sorted({"visual.interact"} & self.capability_catalog & available)
        )
        services = CognitiveServices(
            obligation_verifier="obligation_verifier" in providers
        )
        reason_values = ["safe_standard_default", _text(reason, "reason")]
        if not services.obligation_verifier:
            reason_values.append("provider_unavailable:obligation_verifier")
        if not capabilities:
            reason_values.append("capability_unavailable:visual.interact")
        reasons = tuple(dict.fromkeys(reason_values))
        upgrade_triggers: tuple[ProfileTrigger, ...] = ()
        downgrade_conditions: tuple[ProfileTrigger, ...] = ()
        pid = _execution_profile_id(
            mission_id=mission,
            goal_revision=revision,
            accepted_depth=ExecutionDepth.STANDARD,
            services=services,
            accepted_capabilities=capabilities,
            upgrade_triggers=upgrade_triggers,
            downgrade_conditions=downgrade_conditions,
            source="safe_default",
            confidence=None,
            policy_reasons=reasons,
        )
        return ExecutionProfile(
            format=EXECUTION_PROFILE_FORMAT,
            profile_id=pid,
            mission_id=mission,
            goal_revision=revision,
            accepted_depth=ExecutionDepth.STANDARD,
            services=services,
            accepted_capabilities=capabilities,
            upgrade_triggers=upgrade_triggers,
            downgrade_conditions=downgrade_conditions,
            source="safe_default",
            confidence=None,
            policy_reasons=reasons,
        )

    def evaluate(
        self,
        proposal: TaskProfileProposal | Mapping[str, Any] | None,
        *,
        mission_id: str,
        goal_revision: int,
        available_capabilities: Iterable[str] | None = None,
        provider_availability: Iterable[str] | Mapping[str, bool] | None = None,
    ) -> ExecutionProfile:
        mission, revision = _validate_mission_identity(mission_id, goal_revision)
        available = self._runtime_capabilities(available_capabilities)
        providers = self._runtime_providers(provider_availability)
        if proposal is None:
            return self.safe_default(
                mission,
                revision,
                reason="proposal_missing",
                available_capabilities=available,
                provider_availability=providers,
            )
        try:
            accepted = self.validate_proposal(proposal)
        except UnsupportedCapability as exc:
            capability = str(exc).split(":", 1)[-1].strip()
            return self.safe_default(
                mission,
                revision,
                reason=f"proposal_unknown_capability:{capability}",
                available_capabilities=available,
                provider_availability=providers,
            )
        except (ExecutionProfileError, TypeError, ValueError):
            return self.safe_default(
                mission,
                revision,
                reason="proposal_schema_invalid",
                available_capabilities=available,
                provider_availability=providers,
            )
        if accepted.confidence < self.min_confidence:
            return self.safe_default(
                mission,
                revision,
                reason="proposal_low_confidence",
                available_capabilities=available,
                provider_availability=providers,
            )

        reasons: list[str] = ["proposal_accepted"]
        dimensions = accepted.dimensions
        severe_pressure = (
            dimensions.step_dependency == "multi_stage"
            or dimensions.application_scope == "cross_application"
            or dimensions.artifact_flow == "handoff"
            or dimensions.external_wait == "persistent"
            or dimensions.identity_binding == "multiple"
            or dimensions.user_interaction in {"approval", "ongoing"}
        )
        visual_pressure = (
            dimensions.visual_precision == "geometric"
            or dimensions.continuous_pointer == "cross_frame"
        )
        elevated_pressure = (
            severe_pressure
            or visual_pressure
            or dimensions.irreversibility == "high"
        )
        depth = max(
            (accepted.minimum_depth, accepted.recommended_depth),
            key=lambda item: item.rank,
        )
        if (
            depth is ExecutionDepth.ORCHESTRATED
            and not severe_pressure
            and accepted.minimum_depth is not ExecutionDepth.ORCHESTRATED
        ):
            depth = ExecutionDepth.STANDARD
            reasons.append("depth_reduced:no_orchestration_pressure")
        if depth is ExecutionDepth.DIRECT and elevated_pressure:
            depth = (
                ExecutionDepth.ORCHESTRATED
                if severe_pressure
                else ExecutionDepth.STANDARD
            )
            reasons.append("depth_raised:task_pressure")
        elif severe_pressure and depth is not ExecutionDepth.ORCHESTRATED:
            depth = ExecutionDepth.ORCHESTRATED
            reasons.append("depth_raised:structural_pressure")
        if depth is accepted.recommended_depth:
            reasons.append(f"depth_accepted:{depth.value}")
        else:
            reasons.append(f"depth_selected:{depth.value}")

        requested = accepted.requested_services
        service_values: dict[str, bool] = {
            "planner": requested.planner
            and depth is ExecutionDepth.ORCHESTRATED
            and severe_pressure,
            "obligation_verifier": requested.obligation_verifier
            or depth is not ExecutionDepth.DIRECT
            or dimensions.irreversibility == "high",
            "mission_verifier": requested.mission_verifier
            and (depth is ExecutionDepth.ORCHESTRATED or severe_pressure),
            "visual_inspector": requested.visual_inspector and visual_pressure,
            "deep_reasoning": requested.deep_reasoning and elevated_pressure,
        }
        restrictions = {
            "planner": "planner_restricted:no_orchestration_pressure",
            "mission_verifier": "mission_verifier_restricted:no_mission_pressure",
            "visual_inspector": "visual_inspector_restricted:no_visual_pressure",
            "deep_reasoning": "deep_reasoning_restricted:no_elevated_pressure",
        }
        for name, restriction in restrictions.items():
            if getattr(requested, name) and not service_values[name]:
                reasons.append(restriction)
        for name in _SERVICE_NAMES:
            if service_values[name] and name not in providers:
                service_values[name] = False
                reasons.append(f"provider_unavailable:{name}")
        profile_services = CognitiveServices(**service_values)

        proposed = set(accepted.broad_capability_hypotheses)
        capabilities = tuple(sorted(proposed & available))
        for capability in sorted(proposed - available):
            reasons.append(f"capability_unavailable:{capability}")
        if (
            not capabilities
            and "visual.interact" in self.capability_catalog
            and "visual.interact" in available
        ):
            capabilities = ("visual.interact",)
            reasons.append("capability_defaulted:visual.interact")
        reasons_tuple = tuple(dict.fromkeys(reasons))
        upgrade_triggers = tuple(
            ProfileTrigger(item) for item in accepted.upgrade_triggers
        )
        downgrade_conditions = tuple(
            ProfileTrigger(item) for item in accepted.downgrade_conditions
        )
        pid = _execution_profile_id(
            mission_id=mission,
            goal_revision=revision,
            accepted_depth=depth,
            services=profile_services,
            accepted_capabilities=capabilities,
            upgrade_triggers=upgrade_triggers,
            downgrade_conditions=downgrade_conditions,
            source="task_profiler",
            confidence=accepted.confidence,
            policy_reasons=reasons_tuple,
        )
        return ExecutionProfile(
            format=EXECUTION_PROFILE_FORMAT,
            profile_id=pid,
            mission_id=mission,
            goal_revision=revision,
            accepted_depth=depth,
            services=profile_services,
            accepted_capabilities=capabilities,
            upgrade_triggers=upgrade_triggers,
            downgrade_conditions=downgrade_conditions,
            source="task_profiler",
            confidence=accepted.confidence,
            policy_reasons=reasons_tuple,
        )

__all__ = ["DEFAULT_CAPABILITY_CATALOG", "ExecutionPolicy"]
