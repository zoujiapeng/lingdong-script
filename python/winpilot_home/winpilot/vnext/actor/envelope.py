"""Runtime-owned identity envelope for one validated Actor proposal."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ...visual_runtime.commit_admission import FrameCommitIdentity, TargetCommitIdentity
from ..capabilities import PointerLeaseState
from .proposals import ActorProposal


ACTOR_ENVELOPE_FORMAT = "actor-envelope/2"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}")


class InvalidActorEnvelope(ValueError):
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


class ActorDecisionEnvelope(BaseModel):
    """Identity bound by Runtime; this schema is never exposed as model output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Literal["actor-envelope/2"] = ACTOR_ENVELOPE_FORMAT
    decision_id: str
    mission_id: str
    run_id: str
    mission_revision: Annotated[int, Field(strict=True, ge=0)]
    obligation_id: str
    frame: FrameCommitIdentity
    target: TargetCommitIdentity | None
    pointer: PointerLeaseState
    broker_projection_id: str
    capability_grant_ids: tuple[str, ...]
    allowed_evidence_scope: tuple[str, ...]
    proposal: ActorProposal

    @field_validator(
        "decision_id", "mission_id", "run_id", "obligation_id",
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

    @field_validator("target", mode="before")
    @classmethod
    def _target(cls, value: Any) -> TargetCommitIdentity | None:
        if value is None or isinstance(value, TargetCommitIdentity):
            return value
        if isinstance(value, Mapping):
            return TargetCommitIdentity.from_dict(value)
        raise ValueError("target must be TargetCommitIdentity or null")

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

    @model_validator(mode="after")
    def _identities_agree(self) -> "ActorDecisionEnvelope":
        if self.target is not None and (
            self.frame.hwnd != self.target.hwnd
            or self.frame.epoch != self.target.epoch
        ):
            raise ValueError("frame and target identity disagree")
        return self

    def to_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ActorDecisionEnvelope":
        if not isinstance(raw, dict) or "format" not in raw:
            raise InvalidActorEnvelope("ActorDecisionEnvelope requires explicit format")
        try:
            return cls.model_validate(raw)
        except Exception as exc:
            raise InvalidActorEnvelope(str(exc)) from exc


__all__ = ["ACTOR_ENVELOPE_FORMAT", "InvalidActorEnvelope", "ActorDecisionEnvelope"]
