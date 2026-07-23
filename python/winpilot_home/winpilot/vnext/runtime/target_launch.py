"""Runtime-private contracts for one exact installed-target launch.

The Visual Actor still chooses only a candidate ordinal. Runtime binds the
complete private candidate and shared target-acquisition authority. This module
does not launch, poll Windows, create a target binding, or write Mission events.
"""
from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Annotated, Any, ClassVar, Literal

from pydantic import Field, field_validator, model_validator

from ...process_identity import (
    ProcessIdentityError,
    canonical_executable_path,
    canonical_process_identity,
)
from ..targeting import TargetActivation, TargetState
from ._target_acquisition import (
    TargetAcquisitionContractError,
    TargetAcquisitionEnvelopeBase,
    TargetAcquisitionTransitionBase,
    _strict_model_wire,
)


TARGET_LAUNCH_ENVELOPE_FORMAT = "target-launch-envelope/0"
TARGET_LAUNCH_TRANSITION_FORMAT = "target-launch-transition/0"


class TargetLaunchContractError(TargetAcquisitionContractError):
    """Base error for invalid Runtime launch contracts."""


class InvalidTargetLaunchEnvelope(TargetLaunchContractError):
    pass


class InvalidTargetLaunchTransition(TargetLaunchContractError):
    pass


class TargetLaunchEnvelope(TargetAcquisitionEnvelopeBase):
    """Exact Runtime authority for one private installed-target choice."""

    authority_name: ClassVar[str] = "target launch"
    format: Literal["target-launch-envelope/0"] = (
        TARGET_LAUNCH_ENVELOPE_FORMAT
    )

    def _validate_candidate_contract(self) -> None:
        if (
            self.candidate.activation is not TargetActivation.OPEN_APP
            or self.candidate.state is not TargetState.INSTALLED
        ):
            raise ValueError(
                "this contract accepts only an installed open-app candidate"
            )
        if self.candidate.launch_kind != "exe":
            raise ValueError(
                "target launch requires a canonical absolute .exe candidate"
            )
        canonical_executable_path(self.candidate.launch_target)

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, Any],
    ) -> "TargetLaunchEnvelope":
        if not isinstance(raw, Mapping) or "format" not in raw:
            raise InvalidTargetLaunchEnvelope(
                "TargetLaunchEnvelope requires explicit format"
            )
        try:
            return cls.model_validate(
                _strict_model_wire(cls, raw, "TargetLaunchEnvelope")
            )
        except Exception as exc:
            raise InvalidTargetLaunchEnvelope(str(exc)) from exc


class TargetLaunchOutcome(str, Enum):
    COMPLETED = "completed"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class TargetLaunchTransition(TargetAcquisitionTransitionBase):
    """Controller fact for one launch attempt; never a TargetBinding."""

    format: Literal["target-launch-transition/0"] = (
        TARGET_LAUNCH_TRANSITION_FORMAT
    )
    outcome: TargetLaunchOutcome
    launch_request_count: Annotated[int, Field(strict=True, ge=0, le=1)] | None
    launch_process_id: Annotated[int, Field(strict=True, gt=0)] | None
    launch_process_identity: str | None

    @field_validator("outcome", mode="before")
    @classmethod
    def _outcome(cls, value: Any) -> TargetLaunchOutcome:
        if isinstance(value, TargetLaunchOutcome):
            return value
        if type(value) is not str:
            raise ValueError("outcome must be a string")
        try:
            return TargetLaunchOutcome(value)
        except ValueError as exc:
            raise ValueError("unsupported target launch outcome") from exc

    @field_validator("launch_request_count", mode="before")
    @classmethod
    def _launch_request_count(cls, value: Any) -> int | None:
        if value is not None and (
            type(value) is not int or value not in {0, 1}
        ):
            raise ValueError("launch_request_count must be zero, one, or null")
        return value

    @field_validator("launch_process_identity", mode="before")
    @classmethod
    def _launch_process_identity(cls, value: Any) -> str | None:
        if value is None:
            return None
        try:
            return canonical_process_identity(value)
        except ProcessIdentityError as exc:
            raise ValueError(
                "launch_process_identity must be canonical"
            ) from exc

    @model_validator(mode="after")
    def _three_state_contract(self) -> "TargetLaunchTransition":
        if (
            (self.launch_process_id is None)
            != (self.launch_process_identity is None)
        ):
            raise ValueError(
                "launch PID and process identity must be present together"
            )

        if self.outcome is TargetLaunchOutcome.COMPLETED:
            if (
                self.launch_request_count != 1
                or self.launch_process_id is None
                or self.launch_process_identity is None
                or self.activated_target is None
                or self.post_frame_id is None
                or not self.evidence_refs
                or self.failure_reason
            ):
                raise ValueError(
                    "completed launch requires exactly one request, reliable "
                    "process identity, activated target, post frame, evidence, "
                    "and no failure"
                )
            if (
                self.activated_target.process_id != self.launch_process_id
                or self.activated_target.process_identity
                != self.launch_process_identity
            ):
                raise ValueError(
                    "completed launch process must be the activated target "
                    "process"
                )
        elif self.outcome is TargetLaunchOutcome.REJECTED:
            if (
                self.launch_request_count != 0
                or self.launch_process_id is not None
                or self.activated_target is not None
                or self.post_frame_id is not None
                or self.evidence_refs
                or not self.failure_reason
            ):
                raise ValueError(
                    "rejected launch must prove zero requests and carry no "
                    "launch, target, frame, or positive evidence"
                )
        elif (
            self.launch_request_count == 0
            or self.launch_process_id is not None
            or self.activated_target is not None
            or not self.failure_reason
        ):
            raise ValueError(
                "unknown launch cannot claim a bindable process or target, "
                "cannot prove zero requests, and must explain uncertainty"
            )
        return self

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, Any],
    ) -> "TargetLaunchTransition":
        if not isinstance(raw, Mapping) or "format" not in raw:
            raise InvalidTargetLaunchTransition(
                "TargetLaunchTransition requires explicit format"
            )
        try:
            return cls.model_validate(
                _strict_model_wire(cls, raw, "TargetLaunchTransition")
            )
        except Exception as exc:
            raise InvalidTargetLaunchTransition(str(exc)) from exc


__all__ = [
    "TARGET_LAUNCH_ENVELOPE_FORMAT",
    "TARGET_LAUNCH_TRANSITION_FORMAT",
    "TargetLaunchContractError",
    "InvalidTargetLaunchEnvelope",
    "InvalidTargetLaunchTransition",
    "TargetLaunchEnvelope",
    "TargetLaunchOutcome",
    "TargetLaunchTransition",
]
