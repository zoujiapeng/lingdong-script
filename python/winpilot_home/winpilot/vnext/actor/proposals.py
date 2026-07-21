"""Strict, identity-free P4a Visual Actor proposal contracts."""
from __future__ import annotations

import json
import math
import re
from types import MappingProxyType
from typing import Annotated, Any, Literal, Mapping, TypeAlias, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_serializer,
    field_validator,
    model_validator,
)

from ..capabilities.names import InvalidCapability, canonical_capability


ACTOR_PROPOSAL_FORMAT = "actor-proposal/0"
_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
_RUNTIME_FIELDS = frozenset({
    "mission_id", "run_id", "revision", "mission_revision", "goal_revision",
    "obligation_id", "frame_id", "frame_epoch", "epoch", "evidence_id",
    "evidence_ids", "evidence_ref", "evidence_refs", "broker_projection_id",
    "projection_id", "grant_id", "grant_ids", "active_grant_ids",
    "capability_grant_ids", "target_binding_id", "pointer_lease_id",
    "decision_id", "envelope_id", "action_id", "artifact_id",
    "verification_id", "hwnd", "process_id",
})
_PHYSICAL_COORD_FIELDS = frozenset({
    f"{space}_{part}"
    for space in ("physical", "screen", "desktop", "absolute", "frame")
    for part in (
        "x", "y", "x1", "y1", "x2", "y2", "point", "points",
        "coordinate", "coordinates", "left", "top", "right", "bottom",
    )
})
_BATCH_FIELDS = frozenset({"actions", "action_batch", "batch", "operations", "tool_calls"})
_FORBIDDEN_ACTIONS = frozenset({
    "done", "fail", "failed", "finish", "complete", "complete_task",
    "complete_subgoal", "claim_obligation_done", "run_action_batch",
    "execute_action_batch", "execute_subgoal", "revise_plan",
})


class ActorContractError(ValueError):
    pass


class InvalidActorProposal(ActorContractError):
    pass


def _text(value: Any, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _unique_text(values: Any, name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise ValueError(f"{name} must be an array of strings")
    result = tuple(_text(value, f"{name}[]") for value in values)
    if len(result) != len(set(result)):
        raise ValueError(f"{name} must not contain duplicates")
    return result


def _json_value(value: Any, path: str = "value", ancestors: set[int] | None = None) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        return value
    if not isinstance(value, (list, tuple, Mapping)):
        raise ValueError(f"{path} contains a non-JSON value")
    seen = ancestors if ancestors is not None else set()
    marker = id(value)
    if marker in seen:
        raise ValueError(f"{path} contains a cyclic JSON value")
    seen.add(marker)
    try:
        if isinstance(value, (list, tuple)):
            return [_json_value(item, f"{path}[]", seen) for item in value]
        if any(type(key) is not str for key in value):
            raise ValueError(f"{path} keys must be strings")
        return {key: _json_value(item, f"{path}.{key}", seen) for key, item in value.items()}
    finally:
        seen.remove(marker)


def _freeze(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _canonical_key(value: str) -> str:
    value = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value.replace("-", "_"))
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return value.lower()


def _reject_overreach(value: Any, path: str = "proposal") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = _canonical_key(key)
            if normalized in _RUNTIME_FIELDS:
                raise ValueError(f"{path}.{key} is Runtime-owned identity")
            if normalized in _PHYSICAL_COORD_FIELDS:
                raise ValueError(f"{path}.{key} is not a submitted-image coordinate")
            if normalized in _BATCH_FIELDS:
                raise ValueError(f"{path}.{key} attempts multiple action outcomes")
            if normalized == "coordinate_space" and isinstance(item, str):
                if item.strip().lower() not in {"image", "submitted_image", "current_image"}:
                    raise ValueError(f"{path}.{key} must refer to the submitted image")
            _reject_overreach(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_overreach(item, f"{path}[{index}]")


class _ActorModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    format: Literal["actor-proposal/0"] = ACTOR_PROPOSAL_FORMAT


class ImageRegion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    x: Annotated[float, Field(strict=True, ge=0)]
    y: Annotated[float, Field(strict=True, ge=0)]
    width: Annotated[float, Field(strict=True, gt=0)]
    height: Annotated[float, Field(strict=True, gt=0)]

    @field_validator("x", "y", "width", "height", mode="before")
    @classmethod
    def _finite_number(cls, value: Any, info: Any) -> Any:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{info.field_name} must be a finite number")
        if not math.isfinite(float(value)):
            raise ValueError(f"{info.field_name} must be a finite number")
        return value


class DesktopActionProposal(_ActorModel):
    kind: Literal["desktop_action"] = "desktop_action"
    action: str
    args: Mapping[str, Any]
    local_intent: str

    @field_validator("action")
    @classmethod
    def _action(cls, value: Any) -> str:
        action = _text(value, "action")
        if not _NAME.fullmatch(action):
            raise ValueError("action must be a lowercase tool name")
        if (
            action in _FORBIDDEN_ACTIONS or "batch" in action
            or action.startswith(("complete_", "finish_", "fail_"))
            or action.endswith(("_done", "_failed"))
        ):
            raise ValueError(f"action {action!r} is not one GUI proposal")
        return action

    @field_validator("args", mode="before")
    @classmethod
    def _args(cls, value: Any) -> Mapping[str, Any]:
        normalized = _json_value(value, "proposal.args")
        if not isinstance(normalized, dict):
            raise ValueError("proposal.args must be a JSON object")
        _reject_overreach(normalized, "proposal.args")
        return normalized

    @field_validator("args", mode="after")
    @classmethod
    def _freeze_args(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        # Pydantic normalizes Mapping input to dict after a before-validator;
        # freeze only after that normalization so caller mutation cannot alter
        # the already-validated transaction or its deterministic serialization.
        return _freeze(dict(value))

    @field_serializer("args")
    def _serialize_args(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return _thaw(value)

    @field_validator("local_intent")
    @classmethod
    def _intent(cls, value: Any) -> str:
        return _text(value, "local_intent")


class ObserveProposal(_ActorModel):
    kind: Literal["observe"] = "observe"
    observation_kind: Literal["full", "image_region"]
    image_region: ImageRegion | None
    question: str

    @field_validator("question")
    @classmethod
    def _question(cls, value: Any) -> str:
        return _text(value, "question")

    @model_validator(mode="after")
    def _region_matches_kind(self) -> "ObserveProposal":
        if (self.observation_kind == "image_region") != (self.image_region is not None):
            raise ValueError("image_region must be present exactly for image_region observation")
        return self


class CapabilityRequest(_ActorModel):
    kind: Literal["request_capability"] = "request_capability"
    capability: str
    reason: str

    @field_validator("capability")
    @classmethod
    def _capability(cls, value: Any) -> str:
        try:
            return canonical_capability(value)
        except InvalidCapability as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: Any) -> str:
        return _text(value, "reason")


class InspectionRequest(_ActorModel):
    kind: Literal["request_inspection"] = "request_inspection"
    region: ImageRegion
    question: str

    @field_validator("question")
    @classmethod
    def _question(cls, value: Any) -> str:
        return _text(value, "question")


class ClaimObligationDone(_ActorModel):
    kind: Literal["claim_obligation_done"] = "claim_obligation_done"
    concise_claim: str

    @field_validator("concise_claim")
    @classmethod
    def _claim(cls, value: Any) -> str:
        return _text(value, "concise_claim")


class BlockedReport(_ActorModel):
    kind: Literal["blocked"] = "blocked"
    blocker_kind: str
    observed_facts: tuple[str, ...]

    @field_validator("blocker_kind")
    @classmethod
    def _blocker(cls, value: Any) -> str:
        token = _text(value, "blocker_kind")
        if not _NAME.fullmatch(token):
            raise ValueError("blocker_kind must be a lowercase semantic token")
        return token

    @field_validator("observed_facts", mode="before")
    @classmethod
    def _facts(cls, value: Any) -> tuple[str, ...]:
        return _unique_text(value, "observed_facts")


class ReplanRequest(_ActorModel):
    kind: Literal["request_replan"] = "request_replan"
    reason_kind: str
    observed_conflicts: tuple[str, ...]
    failed_strategy_summaries: tuple[str, ...]

    @field_validator("reason_kind")
    @classmethod
    def _reason_kind(cls, value: Any) -> str:
        token = _text(value, "reason_kind")
        if not _NAME.fullmatch(token):
            raise ValueError("reason_kind must be a lowercase semantic token")
        return token

    @field_validator("observed_conflicts", "failed_strategy_summaries", mode="before")
    @classmethod
    def _summaries(cls, value: Any, info: Any) -> tuple[str, ...]:
        return _unique_text(value, info.field_name)


ActorProposal: TypeAlias = Annotated[
    Union[
        DesktopActionProposal, ObserveProposal, CapabilityRequest,
        InspectionRequest, ClaimObligationDone, BlockedReport, ReplanRequest,
    ],
    Field(discriminator="kind"),
]
_ADAPTER = TypeAdapter(ActorProposal)
_KNOWN_KINDS = frozenset({
    "desktop_action", "observe", "request_capability", "request_inspection",
    "claim_obligation_done", "blocked", "request_replan",
})


def parse_actor_proposal(raw: Mapping[str, Any]) -> ActorProposal:
    if not isinstance(raw, Mapping) or "format" not in raw or "kind" not in raw:
        raise InvalidActorProposal("ActorProposal requires explicit format and kind")
    if raw.get("kind") not in _KNOWN_KINDS:
        raise InvalidActorProposal(f"unsupported actor proposal kind: {raw.get('kind')!r}")
    try:
        return _ADAPTER.validate_python(raw)
    except Exception as exc:
        raise InvalidActorProposal(str(exc)) from exc


def actor_proposal_to_json(proposal: ActorProposal) -> str:
    try:
        value = _ADAPTER.dump_python(proposal, mode="json")
    except Exception as exc:
        raise InvalidActorProposal("value is not an ActorProposal") from exc
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = [
    "ACTOR_PROPOSAL_FORMAT", "ActorContractError", "InvalidActorProposal",
    "ImageRegion", "DesktopActionProposal", "ObserveProposal", "CapabilityRequest",
    "InspectionRequest", "ClaimObligationDone", "BlockedReport", "ReplanRequest",
    "ActorProposal", "parse_actor_proposal", "actor_proposal_to_json",
]
