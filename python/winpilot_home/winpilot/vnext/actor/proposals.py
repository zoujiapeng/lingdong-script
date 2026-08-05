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

from ..capability_names import InvalidCapability, canonical_capability
from ..json_contract import (
    BoundedJSONError,
    MAX_JSON_ARRAY_ITEMS,
    bounded_json_copy,
)


ACTOR_PROPOSAL_FORMAT = "actor-proposal/0"
_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
_MAX_TEXT_LENGTH = 16_384
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
    for space in (
        "physical", "screen", "desktop", "absolute", "frame", "client",
        "window", "native", "global", "monitor", "cursor",
    )
    for part in (
        "x", "y", "x1", "y1", "x2", "y2", "point", "points",
        "coordinate", "coordinates", "left", "top", "right", "bottom",
    )
})
_BATCH_FIELDS = frozenset({
    "actions", "action_batch", "batch", "operations", "tool_calls",
})
_FORBIDDEN_ACTIONS = frozenset({
    "done", "fail", "failed", "finish", "complete", "complete_task",
    "complete_subgoal", "claim_obligation_done", "blocked", "observe",
    "request_capability", "request_inspection", "request_replan",
    "run_action_batch", "execute_action_batch", "execute_subgoal", "revise_plan",
})


class ActorContractError(ValueError):
    pass


class InvalidActorProposal(ActorContractError):
    pass


def _text(value: Any, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    if len(value) > _MAX_TEXT_LENGTH:
        raise ValueError(f"{name} exceeds the text length limit")
    return value


def _unique_text(values: Any, name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(
        values, (list, tuple)
    ):
        raise ValueError(f"{name} must be an array of strings")
    if len(values) > MAX_JSON_ARRAY_ITEMS:
        raise ValueError(f"{name} exceeds the array item limit")
    result = tuple(_text(value, f"{name}[]") for value in values)
    if len(result) != len(set(result)):
        raise ValueError(f"{name} must not contain duplicates")
    return result


def _freeze(value: Any) -> Any:
    if type(value) is list:
        return tuple(_freeze(item) for item in value)
    if type(value) is dict:
        return MappingProxyType({
            key: _freeze(item) for key, item in value.items()
        })
    return value


def _thaw(value: Any) -> Any:
    if type(value) is MappingProxyType:
        return {key: _thaw(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_thaw(item) for item in value]
    if value is None or type(value) in (str, bool, int, float):
        return value
    raise ValueError("proposal.args contains a non-canonical JSON value")


def _canonical_key(value: str) -> str:
    value = re.sub(
        r"(.)([A-Z][a-z]+)", r"\1_\2", value.replace("-", "_")
    )
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return value.lower()


def _reject_overreach(value: Any, path: str = "proposal") -> None:
    if type(value) is dict:
        for key, item in value.items():
            normalized = _canonical_key(key)
            if (
                normalized in _RUNTIME_FIELDS
                or normalized.endswith((
                    "_id", "_ids", "_fingerprint", "_sha256",
                ))
                or normalized in {
                    "epoch", "revision", "turn_index", "frame_scope", "hwnd",
                    "process", "process_id", "executor", "executor_id",
                }
            ):
                raise ValueError(f"{path}.{key} is Runtime-owned identity")
            if normalized in _PHYSICAL_COORD_FIELDS:
                raise ValueError(
                    f"{path}.{key} is not a submitted-image coordinate"
                )
            if normalized in _BATCH_FIELDS:
                raise ValueError(
                    f"{path}.{key} attempts multiple action outcomes"
                )
            if normalized == "coordinate_space" and type(item) is str:
                if item.strip().lower() not in {
                    "image", "submitted_image", "current_image",
                }:
                    raise ValueError(
                        f"{path}.{key} must refer to the submitted image"
                    )
            _reject_overreach(item, f"{path}.{key}")
    elif type(value) is list:
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
        if type(value) is not float or not math.isfinite(value):
            raise ValueError(
                f"{info.field_name} must be an exact finite float"
            )
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
            action in _FORBIDDEN_ACTIONS
            or "batch" in action
            or action.startswith(("complete_", "finish_", "fail_"))
            or action.endswith(("_done", "_failed"))
        ):
            raise ValueError(f"action {action!r} is not one GUI proposal")
        return action

    @field_validator("args", mode="before")
    @classmethod
    def _args(cls, value: Any) -> Mapping[str, Any]:
        try:
            normalized = bounded_json_copy(value)
        except (BoundedJSONError, RecursionError) as exc:
            raise ValueError(
                f"proposal.args violates the bounded JSON contract: {exc}"
            ) from exc
        if type(normalized) is not dict:
            raise ValueError("proposal.args must be a plain JSON object")
        _reject_overreach(normalized, "proposal.args")
        return normalized

    @field_validator("args", mode="after")
    @classmethod
    def _freeze_args(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        return _freeze(dict(value))

    @field_serializer("args")
    def _serialize_args(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return _thaw(value)

    @field_validator("local_intent")
    @classmethod
    def _intent(cls, value: Any) -> str:
        if type(value) is not str:
            raise ValueError("local_intent must be a string")
        if len(value) > _MAX_TEXT_LENGTH:
            raise ValueError("local_intent exceeds the text length limit")
        if value and not value.strip():
            raise ValueError("local_intent must not be blank")
        return value


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
        if (self.observation_kind == "image_region") != (
            self.image_region is not None
        ):
            raise ValueError(
                "image_region must be present exactly for image_region observation"
            )
        return self


class CapabilityRequest(_ActorModel):
    kind: Literal["request_capability"] = "request_capability"
    capability: str
    reason: str

    @field_validator("capability")
    @classmethod
    def _capability(cls, value: Any) -> str:
        try:
            result = canonical_capability(value)
        except InvalidCapability as exc:
            raise ValueError(str(exc)) from exc
        if result != value:
            raise ValueError("capability must already be canonical")
        return result

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
            raise ValueError(
                "blocker_kind must be a lowercase semantic token"
            )
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
            raise ValueError(
                "reason_kind must be a lowercase semantic token"
            )
        return token

    @field_validator(
        "observed_conflicts",
        "failed_strategy_summaries",
        mode="before",
    )
    @classmethod
    def _summaries(cls, value: Any, info: Any) -> tuple[str, ...]:
        return _unique_text(value, info.field_name)


ActorProposal: TypeAlias = Annotated[
    Union[
        DesktopActionProposal,
        ObserveProposal,
        CapabilityRequest,
        InspectionRequest,
        ClaimObligationDone,
        BlockedReport,
        ReplanRequest,
    ],
    Field(discriminator="kind"),
]
_ADAPTER = TypeAdapter(ActorProposal)
_PROPOSAL_TYPES = (
    DesktopActionProposal,
    ObserveProposal,
    CapabilityRequest,
    InspectionRequest,
    ClaimObligationDone,
    BlockedReport,
    ReplanRequest,
)
_KNOWN_KINDS = frozenset({
    "desktop_action",
    "observe",
    "request_capability",
    "request_inspection",
    "claim_obligation_done",
    "blocked",
    "request_replan",
})

_WIRE_FIELDS = {
    "desktop_action": frozenset({
        "format", "kind", "action", "args", "local_intent",
    }),
    "observe": frozenset({
        "format", "kind", "observation_kind", "image_region", "question",
    }),
    "request_capability": frozenset({
        "format", "kind", "capability", "reason",
    }),
    "request_inspection": frozenset({
        "format", "kind", "region", "question",
    }),
    "claim_obligation_done": frozenset({
        "format", "kind", "concise_claim",
    }),
    "blocked": frozenset({
        "format", "kind", "blocker_kind", "observed_facts",
    }),
    "request_replan": frozenset({
        "format", "kind", "reason_kind", "observed_conflicts",
        "failed_strategy_summaries",
    }),
}

_WIRE_STRING_FIELDS = {
    "desktop_action": ("format", "kind", "action", "local_intent"),
    "observe": ("format", "kind", "observation_kind", "question"),
    "request_capability": ("format", "kind", "capability", "reason"),
    "request_inspection": ("format", "kind", "question"),
    "claim_obligation_done": ("format", "kind", "concise_claim"),
    "blocked": ("format", "kind", "blocker_kind"),
    "request_replan": ("format", "kind", "reason_kind"),
}


def _wire_string(value: Any, name: str) -> None:
    if type(value) is not str:
        raise InvalidActorProposal(f"{name} must be a literal JSON string")


def _wire_string_array(value: Any, name: str) -> None:
    if type(value) is not list:
        raise InvalidActorProposal(f"{name} must be a literal JSON array")
    if any(type(item) is not str for item in value):
        raise InvalidActorProposal(
            f"{name} must contain only literal JSON strings"
        )


def _wire_image_region(value: Any, name: str) -> None:
    if type(value) is not dict:
        raise InvalidActorProposal(f"{name} must be a plain JSON object")
    expected = {"x", "y", "width", "height"}
    keys = set(value)
    if keys != expected:
        raise InvalidActorProposal(
            f"{name} fields mismatch: missing={sorted(expected - keys)!r}, "
            f"unknown={sorted(keys - expected)!r}"
        )
    for field in ("x", "y", "width", "height"):
        if type(value[field]) is not float:
            raise InvalidActorProposal(
                f"{name}.{field} must be an exact finite float"
            )


def _validate_wire_shape(value: dict[str, Any], kind: str) -> None:
    expected = _WIRE_FIELDS[kind]
    keys = set(value)
    if keys != expected:
        raise InvalidActorProposal(
            f"ActorProposal fields mismatch: "
            f"missing={sorted(expected - keys)!r}, "
            f"unknown={sorted(keys - expected)!r}"
        )
    for field in _WIRE_STRING_FIELDS[kind]:
        _wire_string(value[field], f"ActorProposal.{field}")
    if kind == "desktop_action":
        if type(value["args"]) is not dict:
            raise InvalidActorProposal(
                "ActorProposal.args must be a plain JSON object"
            )
    elif kind == "observe":
        image_region = value["image_region"]
        if image_region is not None:
            _wire_image_region(image_region, "ActorProposal.image_region")
    elif kind == "request_inspection":
        _wire_image_region(value["region"], "ActorProposal.region")
    elif kind == "blocked":
        _wire_string_array(
            value["observed_facts"],
            "ActorProposal.observed_facts",
        )
    elif kind == "request_replan":
        _wire_string_array(
            value["observed_conflicts"],
            "ActorProposal.observed_conflicts",
        )
        _wire_string_array(
            value["failed_strategy_summaries"],
            "ActorProposal.failed_strategy_summaries",
        )


def parse_actor_proposal(raw: object) -> ActorProposal:
    if type(raw) is not dict:
        raise InvalidActorProposal(
            "ActorProposal requires a plain JSON object"
        )
    try:
        value = bounded_json_copy(raw)
    except (BoundedJSONError, RecursionError) as exc:
        raise InvalidActorProposal(
            f"ActorProposal violates the bounded JSON contract: {exc}"
        ) from exc
    if type(value) is not dict:  # pragma: no cover - validator invariant
        raise InvalidActorProposal("ActorProposal requires a plain JSON object")
    if "format" not in value or "kind" not in value:
        raise InvalidActorProposal(
            "ActorProposal requires explicit format and kind"
        )
    _wire_string(value["format"], "ActorProposal.format")
    _wire_string(value["kind"], "ActorProposal.kind")
    if value["format"] != ACTOR_PROPOSAL_FORMAT:
        raise InvalidActorProposal("unsupported ActorProposal format")
    if value["kind"] not in _KNOWN_KINDS:
        raise InvalidActorProposal(
            f"unsupported actor proposal kind: {value['kind']!r}"
        )
    _validate_wire_shape(value, value["kind"])
    try:
        return _ADAPTER.validate_python(value)
    except InvalidActorProposal:
        raise
    except Exception as exc:
        raise InvalidActorProposal(str(exc)) from exc


def actor_proposal_to_json(proposal: ActorProposal) -> str:
    if type(proposal) not in _PROPOSAL_TYPES:
        raise InvalidActorProposal("value is not an ActorProposal")
    try:
        value = _ADAPTER.dump_python(proposal, mode="json")
        validated = parse_actor_proposal(value)
        value = _ADAPTER.dump_python(validated, mode="json")
    except Exception as exc:
        raise InvalidActorProposal("value is not an ActorProposal") from exc
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


__all__ = [
    "ACTOR_PROPOSAL_FORMAT",
    "ActorContractError",
    "InvalidActorProposal",
    "ImageRegion",
    "DesktopActionProposal",
    "ObserveProposal",
    "CapabilityRequest",
    "InspectionRequest",
    "ClaimObligationDone",
    "BlockedReport",
    "ReplanRequest",
    "ActorProposal",
    "parse_actor_proposal",
    "actor_proposal_to_json",
]
