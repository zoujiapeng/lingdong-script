"""Runtime-owned identity envelope for one admitted Actor proposal."""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Annotated, Any, Literal, Mapping

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from ..capabilities.argument_validation import ToolArgumentValidator
from ..capabilities import (
    CapabilityBroker,
    BrokerProjection,
    BrokerRuntimeState,
    ExecutionRoute,
    MutationKind,
    PointerDisposition,
    ToolCatalog,
    ToolRoute,
)
from ..mission import (
    MissionDefinition,
    MissionProjection,
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
from ..profiling import ExecutionProfile
from .proposals import (
    ActorProposal,
    BlockedReport,
    CapabilityRequest,
    ClaimObligationDone,
    DesktopActionProposal,
    InspectionRequest,
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


def _ordered_identities(value: Any, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(
        value, (list, tuple)
    ):
        raise ValueError(f"{name} must be an array")
    if len(value) > MAX_JSON_ARRAY_ITEMS:
        raise ValueError(f"{name} exceeds the array item limit")
    result = tuple(_identity(item, f"{name}[]") for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"{name} must not contain duplicates")
    return result


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


def _submitted_coordinate(
    value: Any,
    *,
    name: str,
    upper_bound: int,
) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{name} must be a submitted-image number")
    coordinate = float(value)
    if (
        not math.isfinite(coordinate)
        or coordinate < 0
        or coordinate > upper_bound - 1
    ):
        raise ValueError(
            f"{name} must stay inside the exact submitted image"
        )
    return coordinate


def _validate_point_arguments(
    arguments: Mapping[str, Any],
    submitted_size: tuple[int, int],
) -> None:
    present = ("image_x" in arguments, "image_y" in arguments)
    if any(present) and not all(present):
        raise ValueError(
            "image_x and image_y must be present together"
        )
    if all(present):
        _submitted_coordinate(
            arguments["image_x"],
            name="image_x",
            upper_bound=submitted_size[0],
        )
        _submitted_coordinate(
            arguments["image_y"],
            name="image_y",
            upper_bound=submitted_size[1],
        )


def _validate_image_region(
    region: Any,
    submitted_size: tuple[int, int],
    *,
    name: str,
) -> None:
    if region is None:
        return
    x = _submitted_coordinate(
        region.x,
        name=f"{name}.x",
        upper_bound=submitted_size[0],
    )
    y = _submitted_coordinate(
        region.y,
        name=f"{name}.y",
        upper_bound=submitted_size[1],
    )
    if (
        x + float(region.width) > submitted_size[0]
        or y + float(region.height) > submitted_size[1]
    ):
        raise ValueError(
            f"{name} must stay inside the exact submitted image"
        )


def _canonical_proposal(value: Any) -> ActorProposal:
    try:
        raw = json.loads(actor_proposal_to_json(value))
        return parse_actor_proposal(raw)
    except Exception as exc:
        raise ValueError("proposal is not a canonical ActorProposal") from exc


def proposal_tool_name(proposal: ActorProposal) -> str:
    canonical = _canonical_proposal(proposal)
    if type(canonical) is DesktopActionProposal:
        return canonical.action
    mapping = {
        ObserveProposal: "observe",
        CapabilityRequest: "request_capability",
        InspectionRequest: "request_inspection",
        ClaimObligationDone: "claim_obligation_done",
        BlockedReport: "blocked",
        ReplanRequest: "request_replan",
    }
    try:
        return mapping[type(canonical)]
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


def _mission_projection_sha256(mission: MissionProjection) -> str:
    canonical = json.dumps(
        mission.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ActorDecisionEnvelope(BaseModel):
    """Runtime-bound decision value; it is never model output.

    ``from_dict`` verifies canonical content integrity only. Runtime admission
    must use ``bind_actor_decision`` against current Mission/Broker facts.
    This value is not an execution permit: a GUI consumer must additionally
    pin these exact frame properties through the single FrameAuthority owner
    while invoking the Controller.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Literal["actor-envelope/1"] = ACTOR_ENVELOPE_FORMAT
    decision_id: str
    mission_id: str
    run_id: str
    mission_revision: _NONNEGATIVE_INT
    mission_projection_sha256: str
    accepted_mission_event_ids: tuple[str, ...]
    obligation_id: str
    turn_index: _NONNEGATIVE_INT
    catalog_fingerprint: str
    profile_id: str
    state_fingerprint: str
    frame_id: _POSITIVE_INT
    frame_epoch: _NONNEGATIVE_INT
    frame_hwnd: _POSITIVE_INT
    frame_scope: Literal["target_window"]
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
    broker_projection_id: str
    capability_grant_ids: tuple[str, ...]
    allowed_evidence_scope: tuple[str, ...]
    proposal: ActorProposal

    @field_validator(
        "decision_id",
        "mission_id",
        "run_id",
        "obligation_id",
        "profile_id",
        "target_binding_id",
        "broker_projection_id",
    )
    @classmethod
    def _required_identity(cls, value: Any, info: Any) -> str:
        return _identity(value, info.field_name)

    @field_validator(
        "mission_projection_sha256",
        "catalog_fingerprint",
        "state_fingerprint",
    )
    @classmethod
    def _fingerprint(cls, value: Any, info: Any) -> str:
        return _sha256(value, info.field_name)

    @field_validator("capability_grant_ids", mode="before")
    @classmethod
    def _grant_ids(cls, value: Any) -> tuple[str, ...]:
        return _identities(value, "capability_grant_ids")

    @field_validator("accepted_mission_event_ids", mode="before")
    @classmethod
    def _mission_event_ids(cls, value: Any) -> tuple[str, ...]:
        return _ordered_identities(value, "accepted_mission_event_ids")

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
        return _canonical_proposal(value)

    @model_validator(mode="after")
    def _pointer_contract(self) -> "ActorDecisionEnvelope":
        if self.pointer_disposition == PointerDisposition.RELEASED.value:
            if self.pointer_lease_id is not None:
                raise ValueError("released pointer cannot retain lease_id")
        else:
            if self.pointer_lease_id is None:
                raise ValueError("held pointer requires lease_id")
            if (
                type(self.proposal) is not DesktopActionProposal
                or self.proposal.action != "drag"
            ):
                raise ValueError("held pointer permits only drag")
        return self

    @model_validator(mode="after")
    def _content_identity(self) -> "ActorDecisionEnvelope":
        material = BaseModel.model_dump(self, mode="json")
        claimed = material.pop("decision_id")
        if claimed != _decision_id(material):
            raise ValueError(
                "decision_id does not identify canonical envelope content"
            )
        return self

    def _validated_copy(self) -> "ActorDecisionEnvelope":
        raw = BaseModel.model_dump(self, mode="json")
        try:
            return ActorDecisionEnvelope.from_dict(raw)
        except Exception as exc:
            raise InvalidActorEnvelope(
                "ActorDecisionEnvelope instance is not canonical"
            ) from exc

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> "ActorDecisionEnvelope":
        del _fields_set, values
        raise InvalidActorEnvelope(
            "ActorDecisionEnvelope cannot bypass validated construction"
        )

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return BaseModel.model_dump(
            self._validated_copy(),
            *args,
            **kwargs,
        )

    def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
        return BaseModel.model_dump_json(
            self._validated_copy(),
            *args,
            **kwargs,
        )

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

    def __iter__(self):
        yield from BaseModel.__iter__(self._validated_copy())

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

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
    profile: ExecutionProfile,
    state: BrokerRuntimeState,
    catalog: ToolCatalog,
    projection: BrokerProjection,
    proposal: ActorProposal,
    frame_region: tuple[int, int, int, int],
    submitted_image_size: tuple[int, int],
    frame_image_sha256: str,
    allowed_evidence_scope: tuple[str, ...] = (),
) -> ActorDecisionEnvelope:
    """Admit one proposal against exact current pure Runtime facts."""

    if (
        type(mission_reducer) is not MissionReducer
        or type(profile) is not ExecutionProfile
        or type(state) is not BrokerRuntimeState
        or type(catalog) is not ToolCatalog
        or type(projection) is not BrokerProjection
    ):
        raise InvalidActorAdmission(
            "Actor admission requires canonical MissionReducer/Profile/Broker "
            "values"
        )
    try:
        definition = MissionDefinition.from_dict(
            mission_reducer.definition.to_dict()
        )
        replayed = MissionReducer.replay(
            definition,
            tuple(
                event.to_dict()
                for event in mission_reducer.events
            ),
            evidence=mission_reducer.evidence_scope,
        )
        if (
            replayed.events != mission_reducer.events
            or replayed.projection != mission_reducer.projection
        ):
            raise ValueError(
                "MissionProjection is not rebuildable from accepted events"
            )
        mission = replayed.projection
        # Rebuild every content-addressed Runtime input before trusting it.
        # Frozen dataclasses prevent ordinary mutation, but this also rejects
        # stale identities if privileged in-process code bypasses that guard.
        profile = ExecutionProfile.from_dict(profile.to_dict())
        state = BrokerRuntimeState.from_dict(state.to_dict())
        catalog = ToolCatalog.from_dict(catalog.to_dict())
        projection = BrokerProjection.from_dict(projection.to_dict())
        canonical_proposal = _canonical_proposal(proposal)
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
        if (
            profile.mission_id != mission.mission_id
            or profile.goal_revision != mission.definition.goal_revision
            or projection.profile_id != profile.profile_id
        ):
            raise ValueError("ExecutionProfile identity is not current")
        if (
            projection.mission_id != mission.mission_id
            or projection.run_id != mission.run_id
            or projection.mission_revision != mission.revision
            or projection.obligation_id != active_id
            or projection.turn_index != state.turn_index
        ):
            raise ValueError("BrokerProjection Mission identity is not current")
        if (
            projection.catalog_fingerprint != catalog.fingerprint
            or projection.state_fingerprint != state.fingerprint
        ):
            raise ValueError("BrokerProjection input fingerprints are not current")
        expected_projection = CapabilityBroker(catalog).project(
            mission,
            profile,
            state,
        )
        if projection != expected_projection:
            raise ValueError(
                "BrokerProjection is not the canonical Broker decision"
            )
        if projection.execution_route is not ExecutionRoute.VISUAL_ACTOR:
            raise ValueError("BrokerProjection is not a Visual Actor route")
        if not state.runtime_healthy or state.paused or state.canceled:
            raise ValueError("Runtime is not writable")
        pointer = state.pointer

        target = mission.target_binding
        if (
            target is None
            or not state.target.live
            or state.target.binding_id != target.binding_id
            or state.target.hwnd != target.hwnd
            or state.target.epoch != target.epoch
            or projection.target_binding_id != target.binding_id
        ):
            raise ValueError("target binding is not current")
        frame = state.frame
        if (
            not frame.authoritative
            or not frame.fresh
            or frame.scope != "target_window"
            or frame.frame_id is None
            or frame.hwnd != target.hwnd
            or frame.epoch != target.epoch
            or projection.frame_id != frame.frame_id
            or projection.frame_scope != frame.scope
        ):
            raise ValueError("submitted frame identity is not current")
        tool_name = proposal_tool_name(canonical_proposal)
        if tool_name not in projection.tool_names:
            raise ValueError("proposal tool is outside BrokerProjection")
        try:
            tool = catalog.get(tool_name)
        except KeyError as exc:
            raise ValueError("proposal tool is absent from ToolCatalog") from exc
        if tool.route is not ToolRoute.VISUAL_ACTOR:
            raise ValueError("proposal tool is not owned by Visual Actor")
        expected_mutation = (
            MutationKind.GUI
            if type(canonical_proposal) is DesktopActionProposal
            else MutationKind.NONE
        )
        if tool.mutation_kind is not expected_mutation:
            raise ValueError("proposal kind and tool mutation contract disagree")
        if type(canonical_proposal) is DesktopActionProposal:
            detached_args = json.loads(
                actor_proposal_to_json(canonical_proposal)
            )["args"]
            ToolArgumentValidator(catalog).validate(
                tool_name,
                detached_args,
            )
            _validate_point_arguments(
                detached_args,
                image_size,
            )
        elif type(canonical_proposal) is ObserveProposal:
            _validate_image_region(
                canonical_proposal.image_region,
                image_size,
                name="observe.image_region",
            )
        elif type(canonical_proposal) is InspectionRequest:
            _validate_image_region(
                canonical_proposal.region,
                image_size,
                name="inspection.region",
            )

        visible_evidence = {
            evidence_id
            for evidence_id, evidence in mission.evidence_refs.items()
            if evidence.obligation_id in (None, active_id)
        }
        unknown_evidence = set(evidence_scope) - visible_evidence
        if unknown_evidence:
            raise ValueError(
                "allowed evidence scope is outside the active obligation"
            )
        grant_ids = tuple(
            item.grant_id for item in projection.active_grants
        )
        material = {
            "format": ACTOR_ENVELOPE_FORMAT,
            "mission_id": mission.mission_id,
            "run_id": mission.run_id,
            "mission_revision": mission.revision,
            "mission_projection_sha256": _mission_projection_sha256(
                mission
            ),
            "accepted_mission_event_ids": list(
                mission.accepted_event_ids
            ),
            "obligation_id": active_id,
            "turn_index": state.turn_index,
            "catalog_fingerprint": catalog.fingerprint,
            "profile_id": profile.profile_id,
            "state_fingerprint": state.fingerprint,
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
            "broker_projection_id": projection.projection_id,
            "capability_grant_ids": list(grant_ids),
            "allowed_evidence_scope": list(evidence_scope),
            "proposal": json.loads(
                actor_proposal_to_json(canonical_proposal)
            ),
        }
        return ActorDecisionEnvelope(
            decision_id=_decision_id(material),
            **material,
        )
    except InvalidActorAdmission:
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
