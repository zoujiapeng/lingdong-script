"""Runtime-owned identity envelope for one validated Actor proposal."""
from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .proposals import ActorProposal


ACTOR_ENVELOPE_FORMAT = "actor-envelope/0"
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

    format: Literal["actor-envelope/0"] = ACTOR_ENVELOPE_FORMAT
    decision_id: str
    mission_id: str
    run_id: str
    mission_revision: Annotated[int, Field(strict=True, ge=0)]
    obligation_id: str
    frame_id: Annotated[int, Field(strict=True, ge=0)] | None
    frame_epoch: Annotated[int, Field(strict=True, ge=0)] | None
    target_binding_id: str | None
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

    @field_validator("target_binding_id")
    @classmethod
    def _optional_identity(cls, value: Any) -> str | None:
        return None if value is None else _identity(value, "target_binding_id")

    @field_validator("capability_grant_ids", "allowed_evidence_scope", mode="before")
    @classmethod
    def _identity_arrays(cls, value: Any, info: Any) -> tuple[str, ...]:
        return _identities(value, info.field_name)

    @model_validator(mode="after")
    def _complete_frame_identity(self) -> "ActorDecisionEnvelope":
        if (self.frame_id is None) != (self.frame_epoch is None):
            raise ValueError("frame_id and frame_epoch must be bound together")
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
