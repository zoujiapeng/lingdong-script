"""Shared strict wire primitives for Runtime target acquisition contracts.

This private module owns no execution behavior. Existing-window activation and
installed-target launch inherit the same authority, identity, serialization,
and replay validation rather than maintaining parallel schemas.
"""
from __future__ import annotations

from collections.abc import Mapping
import json
import math
import re
from typing import Annotated, Any, ClassVar, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from ...process_identity import (
    ProcessIdentityError,
    canonical_process_identity,
)
from ...visual_runtime.commit_admission import FrameCommitIdentity
from ..actor.proposals import DesktopActionProposal, parse_actor_proposal
from ..capabilities import PointerDisposition, PointerLeaseState
from ..targeting import (
    ACTIVATE_TARGET_TOOL_NAME,
    MAX_TARGET_CANDIDATES,
    TargetCandidate,
    canonical_candidate_fingerprint,
)


ACTIVATED_TARGET_IDENTITY_FORMAT = "activated-target-identity/0"
MAX_ACTIVATED_TARGET_TITLE_CHARS = 512
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}")
_CANDIDATE_REF = re.compile(r"target-[0-9a-f]{24}")


class TargetAcquisitionContractError(ValueError):
    """Base error for shared target-acquisition wire contracts."""


def _identity(value: Any, name: str) -> str:
    if type(value) is not str or not _ID.fullmatch(value.strip()):
        raise ValueError(f"{name} is not a valid Runtime identity")
    return value.strip()


def _identities(value: Any, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(
        value, (list, tuple)
    ):
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
    """Reject Python-only values before a replay parser can coerce them."""

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


def _strict_model_wire(
    model: type[BaseModel],
    raw: Mapping[str, Any],
    name: str,
) -> dict[str, Any]:
    """Require every canonical wire field, including constant defaults."""

    value = _wire_object(raw, name)
    expected = set(model.model_fields)
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"{name} fields differ; missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )
    return value


def _canonical_json(model: BaseModel) -> str:
    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class TargetAcquisitionEnvelopeBase(BaseModel):
    """Common Runtime authority bound around one ordinal target choice."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    authority_name: ClassVar[str] = "target acquisition"

    format: str
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

    @field_validator(
        "capability_grant_ids",
        "allowed_evidence_scope",
        mode="before",
    )
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

    def _validate_candidate_contract(self) -> None:
        raise NotImplementedError

    @model_validator(mode="after")
    def _authority_is_exact(self) -> "TargetAcquisitionEnvelopeBase":
        if self.frame.scope != "full_desktop" or self.frame.epoch != 0:
            raise ValueError(
                f"{self.authority_name} requires an epoch-zero "
                "full-desktop frame"
            )
        if (
            self.pointer.disposition is not PointerDisposition.RELEASED
            or self.pointer.lease_id is not None
            or self.pointer.physically_owned
        ):
            raise ValueError(
                f"{self.authority_name} requires a released pointer"
            )
        if self.proposal.action != ACTIVATE_TARGET_TOOL_NAME:
            raise ValueError(
                f"{self.authority_name} requires activate_target proposal"
            )
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
        self._validate_candidate_contract()
        return self

    @field_serializer("frame")
    def _serialize_frame(
        self,
        value: FrameCommitIdentity,
    ) -> dict[str, Any]:
        return value.to_dict()

    @field_serializer("pointer")
    def _serialize_pointer(
        self,
        value: PointerLeaseState,
    ) -> dict[str, Any]:
        return value.to_dict()

    @field_serializer("proposal")
    def _serialize_proposal(
        self,
        value: DesktopActionProposal,
    ) -> dict[str, Any]:
        return value.model_dump(mode="json")

    @field_serializer("candidate")
    def _serialize_candidate(
        self,
        value: TargetCandidate,
    ) -> dict[str, Any]:
        return value.to_dict()

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def to_json(self) -> str:
        return _canonical_json(self)


class ActivatedTargetIdentity(BaseModel):
    """Strong foreground identity observed after target acquisition."""

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
                "window_region must be [left, top, positive width, "
                "positive height]"
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
        self,
        value: tuple[int, int, int, int],
    ) -> list[int]:
        return list(value)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, Any],
    ) -> "ActivatedTargetIdentity":
        if not isinstance(raw, Mapping) or "format" not in raw:
            raise TargetAcquisitionContractError(
                "ActivatedTargetIdentity requires explicit format"
            )
        try:
            return cls.model_validate(
                _wire_object(raw, "ActivatedTargetIdentity")
            )
        except Exception as exc:
            raise TargetAcquisitionContractError(str(exc)) from exc


class TargetAcquisitionTransitionBase(BaseModel):
    """Common immutable facts returned by one target acquisition attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    format: str
    action_id: str
    action: Literal["activate_target"] = ACTIVATE_TARGET_TOOL_NAME
    candidate_ref: str
    activated_target: ActivatedTargetIdentity | None
    post_frame_id: Annotated[int, Field(strict=True, gt=0)] | None
    evidence_refs: tuple[str, ...]
    failure_reason: str = ""

    @field_validator("action_id")
    @classmethod
    def _action_id(cls, value: Any) -> str:
        return _identity(value, "action_id")

    @field_validator("action", mode="before")
    @classmethod
    def _action(cls, value: Any) -> str:
        if value != ACTIVATE_TARGET_TOOL_NAME:
            raise ValueError("target acquisition action must be activate_target")
        return value

    @field_validator("candidate_ref", mode="before")
    @classmethod
    def _candidate_ref(cls, value: Any) -> str:
        if type(value) is not str or not _CANDIDATE_REF.fullmatch(value):
            raise ValueError("candidate_ref is not canonical")
        return value

    @field_validator("activated_target", mode="before")
    @classmethod
    def _activated_target(
        cls,
        value: Any,
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
    def _post_frame_and_evidence_are_atomic(
        self,
    ) -> "TargetAcquisitionTransitionBase":
        if (self.post_frame_id is not None) != bool(self.evidence_refs):
            raise ValueError(
                "post frame and deterministic evidence must be present together"
            )
        return self

    @field_serializer("activated_target")
    def _serialize_activated_target(
        self,
        value: ActivatedTargetIdentity | None,
    ) -> dict[str, Any] | None:
        return None if value is None else value.to_dict()

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def to_json(self) -> str:
        return _canonical_json(self)


__all__ = [
    "ACTIVATED_TARGET_IDENTITY_FORMAT",
    "MAX_ACTIVATED_TARGET_TITLE_CHARS",
    "ActivatedTargetIdentity",
    "TargetAcquisitionContractError",
    "TargetAcquisitionEnvelopeBase",
    "TargetAcquisitionTransitionBase",
    "_canonical_json",
    "_strict_model_wire",
    "_wire_object",
]
