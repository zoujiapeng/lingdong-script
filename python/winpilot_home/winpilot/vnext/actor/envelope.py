"""Runtime-owned identity envelope for one admitted Actor proposal."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Any, Literal, Mapping

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from ..capabilities import (
    BrokerRuntimeState,
    PointerDisposition,
)
from ..mission import (
    MissionReducer,
    MissionStatus,
)
from ..json_contract import (
    BoundedJSONError,
    MAX_JSON_ARRAY_ITEMS,
    MAX_SIGNED_64,
    MIN_SIGNED_64,
    bounded_json_copy,
)
from .proposals import (
    ActorProposal,
    BlockedReport,
    CapabilityRequest,
    ClaimObligationDone,
    DesktopActionProposal,
    InspectionRequest,
    InvalidActorProposal,
    ObserveProposal,
    ReplanRequest,
    actor_proposal_to_json,
    parse_actor_proposal,
)


ACTOR_ENVELOPE_FORMAT = "actor-envelope/1"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MAX_INTEGER = MAX_SIGNED_64
_SIGNED_INT = Annotated[
    int,
    Field(strict=True, ge=MIN_SIGNED_64, le=MAX_SIGNED_64),
]
_POSITIVE_INT = Annotated[
    int,
    Field(strict=True, gt=0, le=_MAX_INTEGER),
]
_NONNEGATIVE_INT = Annotated[
    int,
    Field(strict=True, ge=0, le=_MAX_INTEGER),
]


class InvalidActorEnvelope(ValueError):
    pass


class InvalidActorAdmission(InvalidActorEnvelope):
    pass


def _identity(value: Any, name: str) -> str:
    if type(value) is not str or not _ID.fullmatch(value):
        raise ValueError(f"{name} is not a valid Runtime identity")
    return value


def _identities(value: Any, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(
        value, (list, tuple)
    ):
        raise ValueError(f"{name} must be an array")
    if len(value) > MAX_JSON_ARRAY_ITEMS:
        raise ValueError(f"{name} exceeds the array item limit")
    result = tuple(_identity(item, f"{name}[]") for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"{name} must not contain duplicates")
    return tuple(sorted(result))










def _sha256(value: Any, name: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _bounded_integer(
    value: Any,
    name: str,
    *,
    minimum: int,
) -> int:
    if (
        type(value) is not int
        or value < minimum
        or value > _MAX_INTEGER
    ):
        qualifier = "positive" if minimum == 1 else "non-negative"
        raise ValueError(f"{name} must be a {qualifier} integer")
    return value


def _frame_region(value: Any) -> tuple[int, int, int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("frame_region must be a four-integer array")
    x, y, width, height = value
    for coordinate, name in ((x, "x"), (y, "y")):
        if (
            type(coordinate) is not int
            or coordinate < MIN_SIGNED_64
            or coordinate > _MAX_INTEGER
        ):
            raise ValueError(f"frame_region.{name} must be a signed integer")
    return (
        x,
        y,
        _bounded_integer(width, "frame_region.width", minimum=1),
        _bounded_integer(height, "frame_region.height", minimum=1),
    )


def _submitted_size(value: Any) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("submitted_image_size must be a two-integer array")
    return (
        _bounded_integer(value[0], "submitted_image_size.width", minimum=1),
        _bounded_integer(value[1], "submitted_image_size.height", minimum=1),
    )


def proposal_tool_name(proposal: ActorProposal) -> str:
    if type(proposal) is DesktopActionProposal:
        return proposal.action
    mapping = {
        ObserveProposal: "observe",
        CapabilityRequest: "request_capability",
        InspectionRequest: "request_inspection",
        ClaimObligationDone: "claim_obligation_done",
        BlockedReport: "blocked",
        ReplanRequest: "request_replan",
    }
    try:
        return mapping[type(proposal)]
    except KeyError as exc:
        raise ValueError("unsupported ActorProposal type") from exc


def _decision_id(material: dict[str, Any]) -> str:
    canonical = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return "decision-" + hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()[:24]


class ActorDecisionEnvelope(BaseModel):
    """Runtime-bound decision value; it is never model output.

    ``bind_actor_decision`` attaches the current mission/frame/pointer
    identity to one already-parsed proposal. This value is not an execution
    permit: a GUI consumer pins these exact frame properties through the
    single FrameAuthority owner while invoking the Controller.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Literal["actor-envelope/1"] = ACTOR_ENVELOPE_FORMAT
    decision_id: str
    mission_id: str
    run_id: str
    mission_revision: _NONNEGATIVE_INT
    obligation_id: str
    turn_index: _NONNEGATIVE_INT
    frame_id: _POSITIVE_INT
    frame_epoch: _NONNEGATIVE_INT
    frame_hwnd: _POSITIVE_INT
    frame_scope: Literal["target_window", "full_desktop"]
    frame_region: tuple[
        _SIGNED_INT,
        _SIGNED_INT,
        _POSITIVE_INT,
        _POSITIVE_INT,
    ]
    submitted_image_size: tuple[_POSITIVE_INT, _POSITIVE_INT]
    frame_image_sha256: str
    target_binding_id: str
    pointer_disposition: Literal["released", "held"]
    pointer_lease_id: str | None
    allowed_evidence_scope: tuple[str, ...]
    proposal: ActorProposal

    @field_validator(
        "decision_id",
        "mission_id",
        "run_id",
        "obligation_id",
        "target_binding_id",
    )
    @classmethod
    def _required_identity(cls, value: Any, info: Any) -> str:
        return _identity(value, info.field_name)

    @field_validator("allowed_evidence_scope", mode="before")
    @classmethod
    def _evidence_ids(cls, value: Any) -> tuple[str, ...]:
        return _identities(value, "allowed_evidence_scope")

    @field_validator("frame_image_sha256")
    @classmethod
    def _image_sha256(cls, value: Any) -> str:
        return _sha256(value, "frame_image_sha256")

    @field_validator("pointer_lease_id", mode="before")
    @classmethod
    def _pointer_lease(cls, value: Any) -> str | None:
        if value is None:
            return None
        return _identity(value, "pointer_lease_id")

    @field_validator("proposal", mode="before")
    @classmethod
    def _proposal(cls, value: Any) -> ActorProposal:
        if type(value) is dict:
            return parse_actor_proposal(value)
        proposal_tool_name(value)
        return value

    @model_validator(mode="after")
    def _pointer_contract(self) -> "ActorDecisionEnvelope":
        if self.pointer_disposition == PointerDisposition.RELEASED.value:
            if self.pointer_lease_id is not None:
                raise ValueError("released pointer cannot retain lease_id")
        elif self.pointer_lease_id is None:
            raise ValueError("held pointer requires lease_id")
        return self

    def model_copy(
        self,
        *,
        update: dict[str, Any] | None = None,
        deep: bool = False,
    ) -> "ActorDecisionEnvelope":
        del deep
        raw = self.to_dict()
        if update is not None:
            if type(update) is not dict:
                raise InvalidActorEnvelope(
                    "model_copy update must be a plain JSON object"
                )
            try:
                copied_update = bounded_json_copy(update)
            except (BoundedJSONError, RecursionError) as exc:
                raise InvalidActorEnvelope(
                    "model_copy update violates the bounded JSON contract: "
                    f"{exc}"
                ) from exc
            raw.update(copied_update)
        try:
            return ActorDecisionEnvelope.from_dict(raw)
        except Exception as exc:
            raise InvalidActorEnvelope(
                "model_copy update does not form a canonical envelope"
            ) from exc

    def to_dict(self) -> dict[str, Any]:
        return BaseModel.model_dump(self, mode="json")

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    # Structural aliases for FrameAuthority.run_if_current. Keeping this
    # protocol-shaped surface here avoids importing a live authority into the
    # pure kernel or creating a second frame owner.
    @property
    def epoch(self) -> int:
        return self.frame_epoch

    @property
    def hwnd(self) -> int:
        return self.frame_hwnd

    @property
    def scope(self) -> str:
        return self.frame_scope

    @property
    def region(self) -> tuple[int, int, int, int]:
        return self.frame_region

    @property
    def submitted_size(self) -> tuple[int, int]:
        return self.submitted_image_size

    @property
    def image_sha256(self) -> str:
        return self.frame_image_sha256

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ActorDecisionEnvelope":
        if type(raw) is not dict:
            raise InvalidActorEnvelope(
                "ActorDecisionEnvelope requires a plain JSON object"
            )
        try:
            value = bounded_json_copy(raw)
        except (BoundedJSONError, RecursionError) as exc:
            raise InvalidActorEnvelope(
                "ActorDecisionEnvelope violates the bounded JSON contract: "
                f"{exc}"
            ) from exc
        expected = set(cls.model_fields)
        keys = set(value)
        if keys != expected:
            raise InvalidActorEnvelope(
                "ActorDecisionEnvelope fields mismatch: "
                f"missing={sorted(expected - keys)!r}, "
                f"unknown={sorted(keys - expected)!r}"
            )
        try:
            return cls.model_validate(value)
        except Exception as exc:
            raise InvalidActorEnvelope(str(exc)) from exc


def bind_actor_decision(
    *,
    mission_reducer: MissionReducer,
    state: BrokerRuntimeState,
    proposal: ActorProposal,
    frame_region: tuple[int, int, int, int],
    submitted_image_size: tuple[int, int],
    frame_image_sha256: str,
    allowed_evidence_scope: tuple[str, ...] = (),
) -> ActorDecisionEnvelope:
    """Attach current mission/frame/pointer identity to one parsed proposal.

    The proposal has already been typed-parsed exactly once by MissionRuntime.
    This binder only records the current snapshot identity; it performs no
    schema, freshness, or permission admission (none of those can change in
    the single-threaded live loop, and their rejections could only produce
    fake failures).
    """

    if (
        type(mission_reducer) is not MissionReducer
        or type(state) is not BrokerRuntimeState
    ):
        raise InvalidActorAdmission(
            "Actor admission requires canonical MissionReducer/Broker values"
        )
    try:
        mission = mission_reducer.projection
        canonical_proposal = proposal
        proposal_tool_name(canonical_proposal)
        region = _frame_region(frame_region)
        image_size = _submitted_size(submitted_image_size)
        image_sha = _sha256(
            frame_image_sha256, "frame_image_sha256"
        )
        evidence_scope = _identities(
            allowed_evidence_scope, "allowed_evidence_scope"
        )

        active_id = mission.active_obligation_id
        if mission.status is not MissionStatus.RUNNING or active_id is None:
            raise ValueError("Mission is not actionable")
        pointer = state.pointer
        target = mission.target_binding
        frame = state.frame
        material = {
            "format": ACTOR_ENVELOPE_FORMAT,
            "mission_id": mission.mission_id,
            "run_id": mission.run_id,
            "mission_revision": mission.revision,
            "obligation_id": active_id,
            "turn_index": state.turn_index,
            "frame_id": frame.frame_id,
            "frame_epoch": frame.epoch,
            "frame_hwnd": frame.hwnd,
            "frame_scope": frame.scope,
            "frame_region": list(region),
            "submitted_image_size": list(image_size),
            "frame_image_sha256": image_sha,
            "target_binding_id": target.binding_id,
            "pointer_disposition": pointer.disposition.value,
            "pointer_lease_id": pointer.lease_id,
            "allowed_evidence_scope": list(evidence_scope),
            "proposal": canonical_proposal,
        }
        decision_material = {
            **material,
            "proposal": actor_proposal_to_json(canonical_proposal),
        }
        return ActorDecisionEnvelope(
            decision_id=_decision_id(decision_material),
            **material,
        )
    except InvalidActorAdmission:
        raise
    except InvalidActorProposal:
        raise
    except Exception as exc:
        raise InvalidActorAdmission(str(exc)) from exc


__all__ = [
    "ACTOR_ENVELOPE_FORMAT",
    "InvalidActorEnvelope",
    "InvalidActorAdmission",
    "ActorDecisionEnvelope",
    "proposal_tool_name",
    "bind_actor_decision",
]
