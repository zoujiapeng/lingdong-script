"""Runtime-private contracts for one exact existing-window activation.

The Visual Actor chooses only a candidate ordinal.  Runtime binds the full
desktop authority and private candidate identity in ``TargetActivationEnvelope``.
This module does not activate windows, create target bindings, or write Mission
events.
"""
from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
import json
import math
import re
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from ...process_identity import ProcessIdentityError, canonical_process_identity
from ...visual_runtime.commit_admission import FrameCommitIdentity
from ..actor.proposals import DesktopActionProposal, parse_actor_proposal
from ..capabilities import PointerDisposition, PointerLeaseState
from ..targeting import (
    ACTIVATE_TARGET_TOOL_NAME,
    MAX_TARGET_CANDIDATES,
    TargetActivation,
    TargetCandidate,
    TargetState,
    canonical_candidate_fingerprint,
)


TARGET_ACTIVATION_ENVELOPE_FORMAT = "target-activation-envelope/0"
ACTIVATED_TARGET_IDENTITY_FORMAT = "activated-target-identity/0"
TARGET_ACTIVATION_TRANSITION_FORMAT = "target-activation-transition/0"
MAX_ACTIVATED_TARGET_TITLE_CHARS = 512
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}")
_CANDIDATE_REF = re.compile(r"target-[0-9a-f]{24}")


class TargetActivationContractError(ValueError):
    """Base error for invalid Runtime activation contracts."""


class InvalidTargetActivationEnvelope(TargetActivationContractError):
    pass


class InvalidTargetActivationTransition(TargetActivationContractError):
    pass


def _identity(value: Any, name: str) -> str:
    if type(value) is not str or not _ID.fullmatch(value.strip()):
        raise ValueError(f"{name} is not a valid Runtime identity")
    return value.strip()


def _identities(value: Any, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be an array")
    result = tuple(_identity(item, f"{name}[]") for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"{name} must not contain duplicates")
    return tuple(sorted(result))


def _require_json_wire(
    value: Any,
    path: str,
    ancestors: set[int] | None = None,
) -> None:
    """Reject Python-only values before a replay/wire parser can coerce them."""

    if value is None or type(value) in {str, bool, int}:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-JSON number")
        return
    if type(value) not in {list, dict}:
        raise ValueError(f"{path} contains a non-JSON value")
    seen = ancestors if ancestors is not None else set()
    marker = id(value)
    if marker in seen:
        raise ValueError(f"{path} contains a cyclic JSON value")
    seen.add(marker)
    try:
        values = enumerate(value) if type(value) is list else value.items()
        for key, item in values:
            if type(value) is dict and type(key) is not str:
                raise ValueError(f"{path} object keys must be strings")
            _require_json_wire(item, f"{path}.{key}", seen)
    finally:
        seen.remove(marker)


def _wire_object(raw: Mapping[str, Any], name: str) -> dict[str, Any]:
    if type(raw) is not dict:
        raise ValueError(f"{name} must be a JSON object")
    _require_json_wire(raw, name)
    return raw


def _canonical_json(model: BaseModel) -> str:
    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class TargetActivationEnvelope(BaseModel):
    """Exact Runtime authority for one private existing-window choice."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    format: Literal["target-activation-envelope/0"] = (
        TARGET_ACTIVATION_ENVELOPE_FORMAT
    )
    decision_id: str
    mission_id: str
    run_id: str
    mission_revision: Annotated[int, Field(strict=True, ge=0)]
    obligation_id: str
    frame: FrameCommitIdentity
    pointer: PointerLeaseState
    broker_projection_id: str
    capability_grant_ids: tuple[str, ...]
    allowed_evidence_scope: tuple[str, ...]
    candidate_fingerprint: str
    proposal: DesktopActionProposal
    candidate: TargetCandidate

    @field_validator(
        "decision_id",
        "mission_id",
        "run_id",
        "obligation_id",
        "broker_projection_id",
    )
    @classmethod
    def _required_identity(cls, value: Any, info: Any) -> str:
        return _identity(value, info.field_name)

    @field_validator("frame", mode="before")
    @classmethod
    def _frame(cls, value: Any) -> FrameCommitIdentity:
        if isinstance(value, FrameCommitIdentity):
            return value
        if isinstance(value, Mapping):
            return FrameCommitIdentity.from_dict(value)
        raise ValueError("frame must be FrameCommitIdentity")

    @field_validator("pointer", mode="before")
    @classmethod
    def _pointer(cls, value: Any) -> PointerLeaseState:
        if isinstance(value, PointerLeaseState):
            return value
        if isinstance(value, Mapping):
            return PointerLeaseState.from_dict(value)
        raise ValueError("pointer must be PointerLeaseState")

    @field_validator("capability_grant_ids", "allowed_evidence_scope", mode="before")
    @classmethod
    def _identity_arrays(cls, value: Any, info: Any) -> tuple[str, ...]:
        return _identities(value, info.field_name)

    @field_validator("candidate_fingerprint", mode="before")
    @classmethod
    def _candidate_fingerprint(cls, value: Any) -> str:
        return canonical_candidate_fingerprint(value)

    @field_validator("proposal", mode="before")
    @classmethod
    def _proposal(cls, value: Any) -> DesktopActionProposal:
        if isinstance(value, Mapping):
            value = parse_actor_proposal(dict(value))
        if not isinstance(value, DesktopActionProposal):
            raise ValueError("proposal must be DesktopActionProposal")
        return value

    @field_validator("candidate", mode="before")
    @classmethod
    def _candidate(cls, value: Any) -> TargetCandidate:
        if isinstance(value, Mapping):
            value = TargetCandidate.from_dict(value)
        if not isinstance(value, TargetCandidate):
            raise ValueError("candidate must be TargetCandidate")
        return value

    @model_validator(mode="after")
    def _activation_authority_is_exact(self) -> "TargetActivationEnvelope":
        if self.frame.scope != "full_desktop" or self.frame.epoch != 0:
            raise ValueError(
                "target activation requires an epoch-zero full-desktop frame"
            )
        if (
            self.pointer.disposition is not PointerDisposition.RELEASED
            or self.pointer.lease_id is not None
            or self.pointer.physically_owned
        ):
            raise ValueError("target activation requires a released pointer")
        if self.proposal.action != ACTIVATE_TARGET_TOOL_NAME:
            raise ValueError("target activation requires activate_target proposal")
        args = dict(self.proposal.args)
        if set(args) != {"candidate_ordinal"}:
            raise ValueError(
                "activate_target proposal requires only candidate_ordinal"
            )
        ordinal = args["candidate_ordinal"]
        if (
            type(ordinal) is not int
            or ordinal < 1
            or ordinal > MAX_TARGET_CANDIDATES
        ):
            raise ValueError("candidate_ordinal is outside the canonical range")
        if (
            self.candidate.activation is not TargetActivation.SWITCH_WINDOW
            or self.candidate.state
            not in {TargetState.VISIBLE, TargetState.RECOVERABLE}
        ):
            raise ValueError(
                "this contract accepts only an existing-window candidate"
            )
        return self

    @field_serializer("frame")
    def _serialize_frame(self, value: FrameCommitIdentity) -> dict[str, Any]:
        return value.to_dict()

    @field_serializer("pointer")
    def _serialize_pointer(self, value: PointerLeaseState) -> dict[str, Any]:
        return value.to_dict()

    @field_serializer("proposal")
    def _serialize_proposal(
        self, value: DesktopActionProposal,
    ) -> dict[str, Any]:
        return value.model_dump(mode="json")

    @field_serializer("candidate")
    def _serialize_candidate(self, value: TargetCandidate) -> dict[str, Any]:
        return value.to_dict()

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def to_json(self) -> str:
        return _canonical_json(self)

    @classmethod
    def from_dict(
        cls, raw: Mapping[str, Any],
    ) -> "TargetActivationEnvelope":
        if not isinstance(raw, Mapping) or "format" not in raw:
            raise InvalidTargetActivationEnvelope(
                "TargetActivationEnvelope requires explicit format"
            )
        try:
            return cls.model_validate(_wire_object(raw, "TargetActivationEnvelope"))
        except Exception as exc:
            raise InvalidTargetActivationEnvelope(str(exc)) from exc


class ActivatedTargetIdentity(BaseModel):
    """Strong foreground identity observed after an activation attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Literal["activated-target-identity/0"] = (
        ACTIVATED_TARGET_IDENTITY_FORMAT
    )
    hwnd: Annotated[int, Field(strict=True, gt=0)]
    root_hwnd: Annotated[int, Field(strict=True, gt=0)]
    process_id: Annotated[int, Field(strict=True, gt=0)]
    process_identity: str
    window_region: tuple[int, int, int, int]
    title_snapshot: str

    @field_validator("process_identity", mode="before")
    @classmethod
    def _process_identity(cls, value: Any) -> str:
        try:
            return canonical_process_identity(value)
        except ProcessIdentityError as exc:
            raise ValueError(
                "activated target requires canonical process identity"
            ) from exc

    @field_validator("window_region", mode="before")
    @classmethod
    def _window_region(cls, value: Any) -> tuple[int, int, int, int]:
        if (
            isinstance(value, (str, bytes))
            or not isinstance(value, (list, tuple))
            or len(value) != 4
            or any(type(item) is not int for item in value)
            or value[2] <= 0
            or value[3] <= 0
        ):
            raise ValueError(
                "window_region must be [left, top, positive width, positive height]"
            )
        return tuple(value)

    @field_validator("title_snapshot", mode="before")
    @classmethod
    def _title(cls, value: Any) -> str:
        if (
            type(value) is not str
            or len(value) > MAX_ACTIVATED_TARGET_TITLE_CHARS
        ):
            raise ValueError("title_snapshot must be a bounded string")
        return value

    @field_serializer("window_region")
    def _serialize_region(
        self, value: tuple[int, int, int, int],
    ) -> list[int]:
        return list(value)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(
        cls, raw: Mapping[str, Any],
    ) -> "ActivatedTargetIdentity":
        if not isinstance(raw, Mapping) or "format" not in raw:
            raise TargetActivationContractError(
                "ActivatedTargetIdentity requires explicit format"
            )
        try:
            return cls.model_validate(_wire_object(raw, "ActivatedTargetIdentity"))
        except Exception as exc:
            raise TargetActivationContractError(str(exc)) from exc


class TargetActivationOutcome(str, Enum):
    COMPLETED = "completed"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class TargetActivationTransition(BaseModel):
    """Controller fact for one activation attempt; never a TargetBinding."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Literal["target-activation-transition/0"] = (
        TARGET_ACTIVATION_TRANSITION_FORMAT
    )
    outcome: TargetActivationOutcome
    action_id: str
    action: Literal["activate_target"] = ACTIVATE_TARGET_TOOL_NAME
    candidate_ref: str
    mutation_completed: bool | None
    activated_target: ActivatedTargetIdentity | None
    post_frame_id: Annotated[int, Field(strict=True, gt=0)] | None
    evidence_refs: tuple[str, ...]
    failure_reason: str = ""

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
            raise ValueError("unsupported target activation outcome") from exc

    @field_validator("action_id")
    @classmethod
    def _action_id(cls, value: Any) -> str:
        return _identity(value, "action_id")

    @field_validator("candidate_ref", mode="before")
    @classmethod
    def _candidate_ref(cls, value: Any) -> str:
        if type(value) is not str or not _CANDIDATE_REF.fullmatch(value):
            raise ValueError("candidate_ref is not canonical")
        return value

    @field_validator("mutation_completed", mode="before")
    @classmethod
    def _mutation_completed(cls, value: Any) -> bool | None:
        if value is not None and type(value) is not bool:
            raise ValueError("mutation_completed must be boolean or null")
        return value

    @field_validator("activated_target", mode="before")
    @classmethod
    def _activated_target(
        cls, value: Any,
    ) -> ActivatedTargetIdentity | None:
        if value is None or isinstance(value, ActivatedTargetIdentity):
            return value
        if isinstance(value, Mapping):
            return ActivatedTargetIdentity.from_dict(value)
        raise ValueError(
            "activated_target must be ActivatedTargetIdentity or null"
        )

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _evidence_refs(cls, value: Any) -> tuple[str, ...]:
        return _identities(value, "evidence_refs")

    @field_validator("failure_reason", mode="before")
    @classmethod
    def _failure_reason(cls, value: Any) -> str:
        if type(value) is not str:
            raise ValueError("failure_reason must be a string")
        reason = value.strip()
        if len(reason) > 4096:
            raise ValueError("failure_reason is too long")
        return reason

    @model_validator(mode="after")
    def _three_state_contract(self) -> "TargetActivationTransition":
        if (self.post_frame_id is not None) != bool(self.evidence_refs):
            raise ValueError(
                "post frame and deterministic evidence must be present together"
            )
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

    @field_serializer("activated_target")
    def _serialize_activated_target(
        self, value: ActivatedTargetIdentity | None,
    ) -> dict[str, Any] | None:
        return None if value is None else value.to_dict()

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def to_json(self) -> str:
        return _canonical_json(self)

    @classmethod
    def from_dict(
        cls, raw: Mapping[str, Any],
    ) -> "TargetActivationTransition":
        if not isinstance(raw, Mapping) or "format" not in raw:
            raise InvalidTargetActivationTransition(
                "TargetActivationTransition requires explicit format"
            )
        try:
            return cls.model_validate(_wire_object(raw, "TargetActivationTransition"))
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
