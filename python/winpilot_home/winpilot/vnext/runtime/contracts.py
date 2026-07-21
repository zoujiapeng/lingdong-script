"""Pure contracts at the P4 Mission Runtime boundary."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import re
from typing import Any, Iterable

from ..capabilities import AuthoritativeFrameState, BrokerRuntimeState, ExecutionRoute
from ...visual_runtime.commit_admission import (
    FrameCommitIdentity,
    TargetCommitIdentity,
)


RUNTIME_FRAME_FORMAT = "runtime-frame/0"
RUNTIME_SNAPSHOT_FORMAT = "runtime-snapshot/0"
CONTROLLER_TRANSITION_FORMAT = "controller-transition/0"
MISSION_RUNTIME_RESULT_FORMAT = "mission-runtime-result/0"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class RuntimeContractError(ValueError):
    pass


def _identity(value: Any, name: str) -> str:
    if type(value) is not str or not _ID.fullmatch(value.strip()):
        raise RuntimeContractError(f"{name} is not a valid identity")
    return value.strip()


def _identities(values: Iterable[str], name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise RuntimeContractError(f"{name} must be an identity array")
    result = tuple(_identity(value, f"{name}[]") for value in values)
    if len(result) != len(set(result)):
        raise RuntimeContractError(f"{name} must not contain duplicates")
    return tuple(sorted(result))


@dataclass(frozen=True, slots=True)
class RuntimeFrame:
    """Complete pixels and geometry behind one authoritative frame identity."""

    frame_id: int
    epoch: int
    hwnd: int
    region: tuple[int, int, int, int]
    scope: str
    submitted_size: tuple[int, int]
    image_b64: str
    image_sha256: str
    evidence_refs: tuple[str, ...] = ()
    format: str = RUNTIME_FRAME_FORMAT

    def __post_init__(self) -> None:
        if self.format != RUNTIME_FRAME_FORMAT:
            raise RuntimeContractError("unsupported RuntimeFrame format")
        for name in ("frame_id", "epoch"):
            value = getattr(self, name)
            minimum = 1 if name == "frame_id" else 0
            if type(value) is not int or value < minimum:
                qualifier = "positive" if minimum else "non-negative"
                raise RuntimeContractError(f"{name} must be a {qualifier} integer")
        if type(self.hwnd) is not int or self.hwnd <= 0:
            raise RuntimeContractError("hwnd must be a positive integer")
        region = tuple(self.region)
        size = tuple(self.submitted_size)
        if (
            len(region) != 4
            or any(type(value) is not int for value in region)
            or region[2] <= 0
            or region[3] <= 0
        ):
            raise RuntimeContractError("region must be [left, top, positive width, positive height]")
        if (
            len(size) != 2
            or any(type(value) is not int for value in size)
            or min(size) <= 0
        ):
            raise RuntimeContractError("submitted_size must contain two positive integers")
        if type(self.image_b64) is not str or not self.image_b64:
            raise RuntimeContractError("image_b64 cannot be empty")
        scope = _identity(self.scope, "scope")
        try:
            digest = hashlib.sha256(self.image_b64.encode("ascii")).hexdigest()
        except UnicodeEncodeError as exc:
            raise RuntimeContractError("image_b64 must be ASCII") from exc
        claimed_digest = str(self.image_sha256).strip().lower()
        if not _SHA256.fullmatch(claimed_digest) or claimed_digest != digest:
            raise RuntimeContractError("image_sha256 does not identify image_b64")
        object.__setattr__(self, "region", region)
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "submitted_size", size)
        object.__setattr__(self, "image_sha256", claimed_digest)
        object.__setattr__(self, "evidence_refs", _identities(self.evidence_refs, "evidence_refs"))

    @property
    def commit_identity(self) -> FrameCommitIdentity:
        return FrameCommitIdentity(
            frame_id=self.frame_id,
            epoch=self.epoch,
            hwnd=self.hwnd,
            region=self.region,
            scope=self.scope,
            submitted_size=self.submitted_size,
            image_sha256=self.image_sha256,
        )

    @property
    def authority_key(self) -> tuple[Any, ...]:
        return (
            self.commit_identity,
            self.evidence_refs,
        )

    def broker_frame(self, *, authoritative: bool = True, fresh: bool = True) -> AuthoritativeFrameState:
        return AuthoritativeFrameState(
            frame_id=self.frame_id,
            hwnd=self.hwnd,
            epoch=self.epoch,
            authoritative=authoritative,
            fresh=fresh,
        )


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    broker_state: BrokerRuntimeState
    frame: RuntimeFrame | None
    target: TargetCommitIdentity | None
    format: str = RUNTIME_SNAPSHOT_FORMAT

    def __post_init__(self) -> None:
        if self.format != RUNTIME_SNAPSHOT_FORMAT:
            raise RuntimeContractError("unsupported RuntimeSnapshot format")
        if not isinstance(self.broker_state, BrokerRuntimeState):
            raise RuntimeContractError("broker_state must be BrokerRuntimeState")
        frame_state = self.broker_state.frame
        target_state = self.broker_state.target
        if target_state.live:
            if self.target is None:
                raise RuntimeContractError("live Broker target requires commit identity")
            if not isinstance(self.target, TargetCommitIdentity):
                raise RuntimeContractError("target must be TargetCommitIdentity")
            if (
                target_state.binding_id != self.target.binding_id
                or target_state.hwnd != self.target.hwnd
                or target_state.epoch != self.target.epoch
            ):
                raise RuntimeContractError("target payload and Broker state identity disagree")
        elif self.target is not None:
            raise RuntimeContractError("non-live Broker target cannot have commit identity")
        if self.frame is None:
            if frame_state.frame_id is not None:
                raise RuntimeContractError("frame payload is missing for broker frame identity")
            return
        if not isinstance(self.frame, RuntimeFrame):
            raise RuntimeContractError("frame must be RuntimeFrame")
        if (
            frame_state.frame_id != self.frame.frame_id
            or frame_state.epoch != self.frame.epoch
            or frame_state.hwnd != self.frame.hwnd
        ):
            raise RuntimeContractError("frame payload and Broker state identity disagree")
        if self.target is not None and (
            self.target.hwnd != self.frame.hwnd
            or self.target.epoch != self.frame.epoch
        ):
            raise RuntimeContractError("frame and target commit identity disagree")

    @property
    def authority_key(self) -> tuple[Any, ...]:
        return (
            self.broker_state,
            None if self.frame is None else self.frame.authority_key,
            self.target,
        )


class ControllerOutcome(str, Enum):
    COMPLETED = "completed"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ControllerTransition:
    outcome: ControllerOutcome | str
    action_id: str
    action: str
    input_completed: bool | None
    visible_effect: str = ""
    evidence_refs: tuple[str, ...] = ()
    post_frame_id: int | None = None
    format: str = CONTROLLER_TRANSITION_FORMAT

    def __post_init__(self) -> None:
        if self.format != CONTROLLER_TRANSITION_FORMAT:
            raise RuntimeContractError("unsupported ControllerTransition format")
        object.__setattr__(self, "outcome", ControllerOutcome(self.outcome))
        object.__setattr__(self, "action_id", _identity(self.action_id, "action_id"))
        action = str(self.action).strip()
        if not action:
            raise RuntimeContractError("controller action cannot be empty")
        object.__setattr__(self, "action", action)
        if self.input_completed is not None and type(self.input_completed) is not bool:
            raise RuntimeContractError("input_completed must be boolean or null")
        object.__setattr__(self, "visible_effect", str(self.visible_effect or ""))
        object.__setattr__(self, "evidence_refs", _identities(self.evidence_refs, "evidence_refs"))
        if self.post_frame_id is not None and (
            type(self.post_frame_id) is not int or self.post_frame_id < 0
        ):
            raise RuntimeContractError("post_frame_id must be a non-negative integer")
        if self.outcome is ControllerOutcome.COMPLETED:
            if self.input_completed is not True or not self.evidence_refs or self.post_frame_id is None:
                raise RuntimeContractError(
                    "completed transition requires input, evidence, and a post frame"
                )
        elif self.outcome is ControllerOutcome.REJECTED:
            if self.input_completed is not False or self.post_frame_id is None:
                raise RuntimeContractError("rejected transition requires a post frame and no input")
        elif self.input_completed is not None:
            raise RuntimeContractError("unknown transition cannot claim whether input executed")


class RuntimeStatus(str, Enum):
    ACTION_RECORDED = "action_recorded"
    VERIFICATION_REQUIRED = "verification_required"
    BLOCKED = "blocked"
    OBSERVED = "observed"
    UNAVAILABLE = "unavailable"
    ROUTE_DEFERRED = "route_deferred"
    STALE_DISCARDED = "stale_discarded"
    CANCELED = "canceled"
    INVALID_PROPOSAL = "invalid_proposal"
    ACTOR_ERROR = "actor_error"
    STATE_REJECTED = "state_rejected"
    BUSY = "busy"


@dataclass(frozen=True, slots=True)
class MissionRuntimeResult:
    status: RuntimeStatus | str
    route: ExecutionRoute | str | None = None
    proposal_kind: str | None = None
    decision_id: str | None = None
    accepted_event_ids: tuple[str, ...] = ()
    detail: str = ""
    pointer_cleanup_attempted: bool = False
    format: str = MISSION_RUNTIME_RESULT_FORMAT

    def __post_init__(self) -> None:
        if self.format != MISSION_RUNTIME_RESULT_FORMAT:
            raise RuntimeContractError("unsupported MissionRuntimeResult format")
        object.__setattr__(self, "status", RuntimeStatus(self.status))
        if self.route is not None:
            object.__setattr__(self, "route", ExecutionRoute(self.route))
        if self.proposal_kind is not None:
            object.__setattr__(self, "proposal_kind", str(self.proposal_kind))
        if self.decision_id is not None:
            object.__setattr__(self, "decision_id", _identity(self.decision_id, "decision_id"))
        object.__setattr__(
            self, "accepted_event_ids", _identities(self.accepted_event_ids, "accepted_event_ids")
        )
        object.__setattr__(self, "detail", str(self.detail or ""))
        if type(self.pointer_cleanup_attempted) is not bool:
            raise RuntimeContractError("pointer_cleanup_attempted must be boolean")


__all__ = [
    "RUNTIME_FRAME_FORMAT", "RUNTIME_SNAPSHOT_FORMAT",
    "CONTROLLER_TRANSITION_FORMAT", "MISSION_RUNTIME_RESULT_FORMAT",
    "RuntimeContractError", "RuntimeFrame", "RuntimeSnapshot",
    "FrameCommitIdentity", "TargetCommitIdentity",
    "ControllerOutcome", "ControllerTransition", "RuntimeStatus",
    "MissionRuntimeResult",
]
