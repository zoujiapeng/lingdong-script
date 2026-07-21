"""Injectable, replayable P2a shadow evaluation.

Cases contain injected proposals and provider/runtime facts.  Nothing here can
call a model or affect the legacy execution path.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .contracts import (
    CognitiveServices,
    ExecutionDepth,
    ExecutionProfile,
    InvalidProfileSchema,
    TaskProfileProposal,
    _depth,
    _require_mapping,
    _strict_keys,
    _string_tuple,
    _text,
)
from .policy import ExecutionPolicy


PROFILE_SHADOW_CASE_FORMAT = "profile-shadow-case/0"
PROFILE_SHADOW_RESULT_FORMAT = "profile-shadow-result/0"


@dataclass(frozen=True, slots=True)
class ShadowExpectation:
    minimum_depth: ExecutionDepth | str = ExecutionDepth.DIRECT
    maximum_depth: ExecutionDepth | str = ExecutionDepth.ORCHESTRATED
    required_services: tuple[str, ...] = ()
    forbidden_services: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        minimum = _depth(self.minimum_depth, "expectation.minimum_depth")
        maximum = _depth(self.maximum_depth, "expectation.maximum_depth")
        if minimum.rank > maximum.rank:
            raise InvalidProfileSchema(
                "expectation.minimum_depth cannot exceed maximum_depth"
            )
        object.__setattr__(self, "minimum_depth", minimum)
        object.__setattr__(self, "maximum_depth", maximum)
        known_services = frozenset(CognitiveServices._FIELDS)
        for field in ("required_services", "forbidden_services"):
            values = _string_tuple(getattr(self, field), f"expectation.{field}")
            if not set(values) <= known_services:
                raise InvalidProfileSchema(f"expectation.{field} contains unknown services")
            object.__setattr__(self, field, values)
        if set(self.required_services) & set(self.forbidden_services):
            raise InvalidProfileSchema(
                "expectation cannot require and forbid the same service"
            )
        object.__setattr__(
            self,
            "required_capabilities",
            _string_tuple(
                self.required_capabilities, "expectation.required_capabilities"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "minimum_depth": self.minimum_depth.value,
            "maximum_depth": self.maximum_depth.value,
            "required_services": list(self.required_services),
            "forbidden_services": list(self.forbidden_services),
            "required_capabilities": list(self.required_capabilities),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ShadowExpectation":
        value = _require_mapping(raw, "shadow expectation")
        _strict_keys(
            value,
            (
                "minimum_depth",
                "maximum_depth",
                "required_services",
                "forbidden_services",
                "required_capabilities",
            ),
            "shadow expectation",
        )
        return cls(**value)


@dataclass(frozen=True, slots=True)
class ProfileShadowCase:
    case_id: str
    mission_id: str
    goal_revision: int
    proposal: TaskProfileProposal | None
    available_capabilities: tuple[str, ...]
    provider_availability: tuple[str, ...]
    expectation: ShadowExpectation = ShadowExpectation()
    comparison_group: str | None = None
    format: str = PROFILE_SHADOW_CASE_FORMAT

    def __post_init__(self) -> None:
        if self.format != PROFILE_SHADOW_CASE_FORMAT:
            raise InvalidProfileSchema(f"unsupported shadow case format {self.format!r}")
        object.__setattr__(self, "case_id", _text(self.case_id, "case_id"))
        object.__setattr__(self, "mission_id", _text(self.mission_id, "mission_id"))
        if type(self.goal_revision) is not int or self.goal_revision < 0:
            raise InvalidProfileSchema("goal_revision must be a non-negative integer")
        proposal = self.proposal
        if isinstance(proposal, Mapping):
            proposal = TaskProfileProposal.from_dict(proposal)
        if proposal is not None and not isinstance(proposal, TaskProfileProposal):
            raise InvalidProfileSchema("shadow proposal must be canonical or null")
        object.__setattr__(self, "proposal", proposal)
        for field in ("available_capabilities", "provider_availability"):
            object.__setattr__(
                self, field, _string_tuple(getattr(self, field), f"case.{field}")
            )
        expectation = self.expectation
        if isinstance(expectation, Mapping):
            expectation = ShadowExpectation.from_dict(expectation)
        if not isinstance(expectation, ShadowExpectation):
            raise InvalidProfileSchema("case.expectation must be ShadowExpectation")
        object.__setattr__(self, "expectation", expectation)
        if self.comparison_group is not None:
            object.__setattr__(
                self,
                "comparison_group",
                _text(self.comparison_group, "comparison_group"),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "case_id": self.case_id,
            "mission_id": self.mission_id,
            "goal_revision": self.goal_revision,
            "proposal": None if self.proposal is None else self.proposal.to_dict(),
            "available_capabilities": list(self.available_capabilities),
            "provider_availability": list(self.provider_availability),
            "expectation": self.expectation.to_dict(),
            "comparison_group": self.comparison_group,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ProfileShadowCase":
        value = _require_mapping(raw, "shadow case")
        fields = (
            "format",
            "case_id",
            "mission_id",
            "goal_revision",
            "proposal",
            "available_capabilities",
            "provider_availability",
            "expectation",
            "comparison_group",
        )
        _strict_keys(value, fields, "shadow case")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class ProfileShadowResult:
    case_id: str
    profile: ExecutionProfile
    over_profile_reasons: tuple[str, ...]
    under_profile_reasons: tuple[str, ...]
    comparison_group: str | None = None
    format: str = PROFILE_SHADOW_RESULT_FORMAT

    def __post_init__(self) -> None:
        if self.format != PROFILE_SHADOW_RESULT_FORMAT:
            raise InvalidProfileSchema(
                f"unsupported shadow result format {self.format!r}"
            )
        object.__setattr__(self, "case_id", _text(self.case_id, "case_id"))
        profile = self.profile
        if isinstance(profile, Mapping):
            profile = ExecutionProfile.from_dict(profile)
        if not isinstance(profile, ExecutionProfile):
            raise InvalidProfileSchema("shadow result profile must be ExecutionProfile")
        object.__setattr__(self, "profile", profile)
        for field in ("over_profile_reasons", "under_profile_reasons"):
            object.__setattr__(
                self, field, _string_tuple(getattr(self, field), f"result.{field}")
            )
        if self.comparison_group is not None:
            object.__setattr__(
                self,
                "comparison_group",
                _text(self.comparison_group, "comparison_group"),
            )

    @property
    def over_profile(self) -> bool:
        return bool(self.over_profile_reasons)

    @property
    def under_profile(self) -> bool:
        return bool(self.under_profile_reasons)

    @property
    def structural_fingerprint(self) -> str:
        return self.profile.structural_fingerprint

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "case_id": self.case_id,
            "profile": self.profile.to_dict(),
            "over_profile_reasons": list(self.over_profile_reasons),
            "under_profile_reasons": list(self.under_profile_reasons),
            "structural_fingerprint": self.structural_fingerprint,
            "comparison_group": self.comparison_group,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ProfileShadowResult":
        value = _require_mapping(raw, "shadow result")
        fields = (
            "format",
            "case_id",
            "profile",
            "over_profile_reasons",
            "under_profile_reasons",
            "structural_fingerprint",
            "comparison_group",
        )
        _strict_keys(value, fields, "shadow result")
        profile = ExecutionProfile.from_dict(value["profile"])
        fingerprint = _text(
            value["structural_fingerprint"], "structural_fingerprint"
        )
        if fingerprint != profile.structural_fingerprint:
            raise InvalidProfileSchema("shadow result structural fingerprint mismatch")
        return cls(
            format=value["format"],
            case_id=value["case_id"],
            profile=profile,
            over_profile_reasons=value["over_profile_reasons"],
            under_profile_reasons=value["under_profile_reasons"],
            comparison_group=value["comparison_group"],
        )


class ProfileShadowEvaluator:
    """Runs injected cases through a supplied deterministic policy."""

    def __init__(self, policy: ExecutionPolicy) -> None:
        if not isinstance(policy, ExecutionPolicy):
            raise InvalidProfileSchema("shadow evaluator requires ExecutionPolicy")
        self.policy = policy

    def evaluate(self, case: ProfileShadowCase) -> ProfileShadowResult:
        if not isinstance(case, ProfileShadowCase):
            raise InvalidProfileSchema("shadow case must be ProfileShadowCase")
        profile = self.policy.evaluate(
            case.proposal,
            mission_id=case.mission_id,
            goal_revision=case.goal_revision,
            available_capabilities=case.available_capabilities,
            provider_availability=case.provider_availability,
        )
        expectation = case.expectation
        over: list[str] = []
        under: list[str] = []
        if profile.accepted_depth.rank > expectation.maximum_depth.rank:
            over.append("depth_above_expected_maximum")
        if profile.accepted_depth.rank < expectation.minimum_depth.rank:
            under.append("depth_below_expected_minimum")
        for service in expectation.forbidden_services:
            if getattr(profile.services, service):
                over.append(f"forbidden_service_enabled:{service}")
        for service in expectation.required_services:
            if not getattr(profile.services, service):
                under.append(f"required_service_missing:{service}")
        for capability in expectation.required_capabilities:
            if capability not in profile.accepted_capabilities:
                under.append(f"required_capability_missing:{capability}")
        return ProfileShadowResult(
            case_id=case.case_id,
            profile=profile,
            over_profile_reasons=tuple(over),
            under_profile_reasons=tuple(under),
            comparison_group=case.comparison_group,
        )

    def replay(
        self, cases: Iterable[ProfileShadowCase]
    ) -> tuple[ProfileShadowResult, ...]:
        return tuple(self.evaluate(case) for case in cases)


__all__ = [
    "PROFILE_SHADOW_CASE_FORMAT",
    "PROFILE_SHADOW_RESULT_FORMAT",
    "ShadowExpectation",
    "ProfileShadowCase",
    "ProfileShadowResult",
    "ProfileShadowEvaluator",
]
