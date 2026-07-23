"""Runtime-private contracts for one exact existing-window activation.

The Visual Actor chooses only a candidate ordinal. Runtime binds the complete
private candidate and shared target-acquisition authority. This module does not
activate windows, create target bindings, or write Mission events.
"""
from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any, ClassVar, Literal

from pydantic import field_validator, model_validator

from ..targeting import TargetActivation, TargetState
from ._target_acquisition import (
    ACTIVATED_TARGET_IDENTITY_FORMAT,
    MAX_ACTIVATED_TARGET_TITLE_CHARS,
    ActivatedTargetIdentity,
    TargetAcquisitionContractError,
    TargetAcquisitionEnvelopeBase,
    TargetAcquisitionTransitionBase,
    _strict_model_wire,
)


TARGET_ACTIVATION_ENVELOPE_FORMAT = "target-activation-envelope/0"
TARGET_ACTIVATION_TRANSITION_FORMAT = "target-activation-transition/0"

# Compatibility alias: ActivatedTargetIdentity historically raised this base.
TargetActivationContractError = TargetAcquisitionContractError


class InvalidTargetActivationEnvelope(TargetActivationContractError):
    pass


class InvalidTargetActivationTransition(TargetActivationContractError):
    pass


class TargetActivationEnvelope(TargetAcquisitionEnvelopeBase):
    """Exact Runtime authority for one private existing-window choice."""

    authority_name: ClassVar[str] = "target activation"
    format: Literal["target-activation-envelope/0"] = (
        TARGET_ACTIVATION_ENVELOPE_FORMAT
    )

    def _validate_candidate_contract(self) -> None:
        if (
            self.candidate.activation is not TargetActivation.SWITCH_WINDOW
            or self.candidate.state
            not in {TargetState.VISIBLE, TargetState.RECOVERABLE}
        ):
            raise ValueError(
                "this contract accepts only an existing-window candidate"
            )

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, Any],
    ) -> "TargetActivationEnvelope":
        if not isinstance(raw, Mapping) or "format" not in raw:
            raise InvalidTargetActivationEnvelope(
                "TargetActivationEnvelope requires explicit format"
            )
        try:
            return cls.model_validate(
                _strict_model_wire(cls, raw, "TargetActivationEnvelope")
            )
        except Exception as exc:
            raise InvalidTargetActivationEnvelope(str(exc)) from exc


class TargetActivationOutcome(str, Enum):
    COMPLETED = "completed"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class TargetActivationTransition(TargetAcquisitionTransitionBase):
    """Controller fact for one activation attempt; never a TargetBinding."""

    format: Literal["target-activation-transition/0"] = (
        TARGET_ACTIVATION_TRANSITION_FORMAT
    )
    outcome: TargetActivationOutcome
    mutation_completed: bool | None

    @field_validator("outcome", mode="before")
    @classmethod
    def _outcome(cls, value: Any) -> TargetActivationOutcome:
        if isinstance(value, TargetActivationOutcome):
            return value
        if type(value) is not str:
            raise ValueError("outcome must be a string")
        try:
            return TargetActivationOutcome(value)
        except ValueError as exc:
            raise ValueError(
                "unsupported target activation outcome"
            ) from exc

    @field_validator("mutation_completed", mode="before")
    @classmethod
    def _mutation_completed(cls, value: Any) -> bool | None:
        if value is not None and type(value) is not bool:
            raise ValueError("mutation_completed must be boolean or null")
        return value

    @model_validator(mode="after")
    def _three_state_contract(self) -> "TargetActivationTransition":
        if self.outcome is TargetActivationOutcome.COMPLETED:
            if (
                type(self.mutation_completed) is not bool
                or self.activated_target is None
                or self.post_frame_id is None
                or not self.evidence_refs
                or self.failure_reason
            ):
                raise ValueError(
                    "completed activation requires a known mutation state, "
                    "activated identity, post frame, evidence, and no failure"
                )
        elif self.outcome is TargetActivationOutcome.REJECTED:
            if (
                self.mutation_completed is not False
                or self.activated_target is not None
                or not self.failure_reason
            ):
                raise ValueError(
                    "rejected activation must prove no mutation, carry no "
                    "activated identity, and explain the rejection"
                )
        elif (
            self.mutation_completed is not None
            or self.activated_target is not None
            or not self.failure_reason
        ):
            raise ValueError(
                "unknown activation cannot claim mutation or target identity "
                "and must explain the uncertainty"
            )
        return self

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, Any],
    ) -> "TargetActivationTransition":
        if not isinstance(raw, Mapping) or "format" not in raw:
            raise InvalidTargetActivationTransition(
                "TargetActivationTransition requires explicit format"
            )
        try:
            return cls.model_validate(
                _strict_model_wire(cls, raw, "TargetActivationTransition")
            )
        except Exception as exc:
            raise InvalidTargetActivationTransition(str(exc)) from exc


__all__ = [
    "TARGET_ACTIVATION_ENVELOPE_FORMAT",
    "ACTIVATED_TARGET_IDENTITY_FORMAT",
    "TARGET_ACTIVATION_TRANSITION_FORMAT",
    "MAX_ACTIVATED_TARGET_TITLE_CHARS",
    "TargetActivationContractError",
    "InvalidTargetActivationEnvelope",
    "InvalidTargetActivationTransition",
    "TargetActivationEnvelope",
    "ActivatedTargetIdentity",
    "TargetActivationOutcome",
    "TargetActivationTransition",
]
