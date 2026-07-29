"""Typed, default-unreachable contracts for the minimal vNext live Runtime.

These values freeze facts owned by existing kernel components.  They are not
new frame, target, evidence, or completion authorities.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from ..capabilities import (
    AuthoritativeFrameState,
    BrokerProjection,
    BrokerRuntimeState,
    ExecutionRoute,
    MutationKind,
    ToolCatalog,
    ToolRoute,
)
from ..json_contract import BoundedJSONError, bounded_json_copy
from ..mission import (
    ActionEffectSummary,
    EvidenceRef,
    MissionCard,
    TargetBinding,
    is_safe_action_summary,
)
from ..verification import ObligationVerificationRequest


RUNTIME_FRAME_FORMAT = "runtime-frame/0"
RUNTIME_SNAPSHOT_FORMAT = "runtime-snapshot/0"
ACTOR_TURN_FORMAT = "runtime-actor-turn/0"
RUNTIME_VERIFIER_TURN_FORMAT = "runtime-verifier-turn/0"
CONTROLLER_TRANSITION_FORMAT = "controller-transition/0"
MISSION_RUNTIME_RESULT_FORMAT = "mission-runtime-result/0"

CURRENT_FRAME_EVIDENCE_KIND = "current_frame"
POST_ACTION_FRAME_EVIDENCE_KIND = "post_action_frame"
VISUAL_VERIFIER_EVIDENCE_KIND = "visual_obligation_verification"
VISUAL_VERIFIER_TRUST = "independent_visual_model"

APPROVED_DESKTOP_ACTIONS = frozenset(
    {"click", "double_click", "drag", "scroll", "type_text", "hotkey"}
)
APPROVED_OUTCOME_TOOLS = frozenset(
    {"claim_obligation_done", "blocked"}
)
APPROVED_RUNTIME_TOOLS = APPROVED_DESKTOP_ACTIONS | APPROVED_OUTCOME_TOOLS
MAX_COMPLETED_ACTION_HISTORY = 64

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}")
_TOOL = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MAX_TEXT = 16_384
_MAX_FACTS = 64


class RuntimeContractError(ValueError):
    """A Runtime boundary value is not canonical."""


def _identity(value: Any, name: str) -> str:
    if type(value) is not str or not _ID.fullmatch(value.strip()):
        raise RuntimeContractError(f"{name} is not a canonical identity")
    return value.strip()


def _text(value: Any, name: str, *, allow_empty: bool = False) -> str:
    if type(value) is not str:
        raise RuntimeContractError(f"{name} must be a string")
    if not allow_empty and not value.strip():
        raise RuntimeContractError(f"{name} must be non-empty")
    if len(value) > _MAX_TEXT:
        raise RuntimeContractError(f"{name} exceeds the text bound")
    return value


def _ordered_identities(
    values: Iterable[str],
    name: str,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise RuntimeContractError(f"{name} must be an identity array")
    result = tuple(_identity(value, f"{name}[]") for value in values)
    if len(result) != len(set(result)):
        raise RuntimeContractError(f"{name} must not contain duplicates")
    return result


def _strings(
    values: Iterable[str],
    name: str,
    *,
    limit: int = _MAX_FACTS,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise RuntimeContractError(f"{name} must be a string array")
    result = tuple(_text(value, f"{name}[]") for value in values)
    if len(result) > limit:
        raise RuntimeContractError(f"{name} exceeds the item bound")
    if len(result) != len(set(result)):
        raise RuntimeContractError(f"{name} must not contain duplicates")
    return result


def _is_safe_action_summary(value: Any) -> bool:
    return type(value) is str and any(
        is_safe_action_summary(action, value)
        for action in APPROVED_DESKTOP_ACTIONS
    )


def _action_summary_matches(action: str, summary: str) -> bool:
    return is_safe_action_summary(action, summary)


def _freeze_json(value: Any) -> Any:
    if type(value) is list:
        return tuple(_freeze_json(item) for item in value)
    if type(value) is dict:
        return MappingProxyType(
            {key: _freeze_json(item) for key, item in value.items()}
        )
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_thaw_json(item) for item in value]
    return value


def _bounded_mapping(value: Any, name: str) -> Mapping[str, Any]:
    try:
        copied = bounded_json_copy(_thaw_json(value))
    except (BoundedJSONError, RecursionError) as exc:
        raise RuntimeContractError(
            f"{name} violates the bounded JSON contract: {exc}"
        ) from exc
    if type(copied) is not dict:
        raise RuntimeContractError(f"{name} must be a plain JSON object")
    return _freeze_json(copied)


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


@dataclass(frozen=True, slots=True)
class RuntimeFrameIdentity:
    frame_id: int
    epoch: int
    hwnd: int
    region: tuple[int, int, int, int]
    scope: str
    submitted_size: tuple[int, int]
    image_sha256: str
    media_type: str = "image/jpeg"

    def __post_init__(self) -> None:
        if type(self.frame_id) is not int or self.frame_id <= 0:
            raise RuntimeContractError("frame_id must be a positive integer")
        if type(self.epoch) is not int or self.epoch < 0:
            raise RuntimeContractError("frame epoch must be non-negative")
        if type(self.hwnd) is not int or self.hwnd <= 0:
            raise RuntimeContractError("frame hwnd must be positive")
        region = tuple(self.region)
        if (
            len(region) != 4
            or any(type(item) is not int for item in region)
            or region[2] <= 0
            or region[3] <= 0
        ):
            raise RuntimeContractError(
                "frame region must be left, top, positive width, positive height"
            )
        size = tuple(self.submitted_size)
        if (
            len(size) != 2
            or any(type(item) is not int for item in size)
            or min(size) <= 0
        ):
            raise RuntimeContractError(
                "submitted_size must contain two positive integers"
            )
        scope = _identity(self.scope, "frame scope")
        digest = str(self.image_sha256).strip().lower()
        if not _SHA256.fullmatch(digest):
            raise RuntimeContractError(
                "image_sha256 must be a lowercase SHA-256 digest"
            )
        if self.media_type != "image/jpeg":
            raise RuntimeContractError(
                "minimal Runtime accepts only explicit image/jpeg frames"
            )
        object.__setattr__(self, "region", region)
        object.__setattr__(self, "submitted_size", size)
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "image_sha256", digest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "epoch": self.epoch,
            "hwnd": self.hwnd,
            "region": list(self.region),
            "scope": self.scope,
            "submitted_size": list(self.submitted_size),
            "image_sha256": self.image_sha256,
            "media_type": self.media_type,
        }

    @property
    def payload_hash(self) -> str:
        return hashlib.sha256(
            _canonical_json(self.to_dict()).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class RuntimeTargetIdentity:
    binding_id: str
    hwnd: int
    process_id: int
    executable_identity: str
    epoch: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "binding_id", _identity(self.binding_id, "binding_id")
        )
        if type(self.hwnd) is not int or self.hwnd <= 0:
            raise RuntimeContractError("target hwnd must be positive")
        if type(self.process_id) is not int or self.process_id <= 0:
            raise RuntimeContractError("target process_id must be positive")
        object.__setattr__(
            self,
            "executable_identity",
            _text(self.executable_identity, "executable_identity"),
        )
        if type(self.epoch) is not int or self.epoch < 0:
            raise RuntimeContractError("target epoch must be non-negative")

    @classmethod
    def from_binding(
        cls,
        binding: TargetBinding,
    ) -> "RuntimeTargetIdentity":
        if type(binding) is not TargetBinding:
            raise RuntimeContractError(
                "target identity requires canonical TargetBinding"
            )
        return cls(
            binding_id=binding.binding_id,
            hwnd=binding.hwnd,
            process_id=binding.process_id,
            executable_identity=binding.executable_identity,
            epoch=binding.epoch,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "hwnd": self.hwnd,
            "process_id": self.process_id,
            "executable_identity": self.executable_identity,
            "epoch": self.epoch,
        }


@dataclass(frozen=True, slots=True)
class RuntimeCommitIdentity:
    frame: RuntimeFrameIdentity
    target: RuntimeTargetIdentity

    def __post_init__(self) -> None:
        if type(self.frame) is not RuntimeFrameIdentity:
            raise RuntimeContractError(
                "commit identity requires RuntimeFrameIdentity"
            )
        if type(self.target) is not RuntimeTargetIdentity:
            raise RuntimeContractError(
                "commit identity requires RuntimeTargetIdentity"
            )
        if (
            self.frame.hwnd != self.target.hwnd
            or self.frame.epoch != self.target.epoch
            or self.frame.scope != "target_window"
        ):
            raise RuntimeContractError(
                "frame and target commit identities disagree"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame": self.frame.to_dict(),
            "target": self.target.to_dict(),
        }

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            _canonical_json(self.to_dict()).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class RuntimeFrame:
    """Complete pixels behind one existing authoritative frame identity."""

    frame_id: int
    epoch: int
    hwnd: int
    region: tuple[int, int, int, int]
    scope: str
    submitted_size: tuple[int, int]
    image_b64: str
    image_sha256: str
    evidence_refs: tuple[str, ...]
    media_type: str = "image/jpeg"
    format: str = RUNTIME_FRAME_FORMAT

    def __post_init__(self) -> None:
        if self.format != RUNTIME_FRAME_FORMAT:
            raise RuntimeContractError("unsupported RuntimeFrame format")
        identity = RuntimeFrameIdentity(
            frame_id=self.frame_id,
            epoch=self.epoch,
            hwnd=self.hwnd,
            region=self.region,
            scope=self.scope,
            submitted_size=self.submitted_size,
            image_sha256=self.image_sha256,
            media_type=self.media_type,
        )
        if type(self.image_b64) is not str or not self.image_b64:
            raise RuntimeContractError("image_b64 must be non-empty")
        try:
            digest = hashlib.sha256(
                self.image_b64.encode("ascii")
            ).hexdigest()
        except UnicodeEncodeError as exc:
            raise RuntimeContractError("image_b64 must be ASCII") from exc
        if digest != identity.image_sha256:
            raise RuntimeContractError(
                "image_sha256 does not identify image_b64"
            )
        refs = _ordered_identities(self.evidence_refs, "evidence_refs")
        if not refs:
            raise RuntimeContractError(
                "RuntimeFrame requires authoritative frame evidence"
            )
        if tuple(sorted(refs)) != refs:
            raise RuntimeContractError(
                "RuntimeFrame evidence_refs must be sorted"
            )
        object.__setattr__(self, "region", identity.region)
        object.__setattr__(self, "scope", identity.scope)
        object.__setattr__(self, "submitted_size", identity.submitted_size)
        object.__setattr__(self, "image_sha256", identity.image_sha256)
        object.__setattr__(self, "media_type", identity.media_type)
        object.__setattr__(self, "evidence_refs", refs)

    @property
    def identity(self) -> RuntimeFrameIdentity:
        return RuntimeFrameIdentity(
            frame_id=self.frame_id,
            epoch=self.epoch,
            hwnd=self.hwnd,
            region=self.region,
            scope=self.scope,
            submitted_size=self.submitted_size,
            image_sha256=self.image_sha256,
            media_type=self.media_type,
        )

    @property
    def authority_key(self) -> tuple[Any, ...]:
        return (self.identity, self.evidence_refs)

    def broker_frame(
        self,
        *,
        authoritative: bool = True,
        fresh: bool = True,
    ) -> AuthoritativeFrameState:
        return AuthoritativeFrameState(
            frame_id=self.frame_id,
            hwnd=self.hwnd,
            epoch=self.epoch,
            authoritative=authoritative,
            fresh=fresh,
            scope=self.scope,
        )


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    """One injected pre-bound target/frame observation for Broker and Runtime."""

    broker_state: BrokerRuntimeState
    frame: RuntimeFrame
    target: RuntimeTargetIdentity
    format: str = RUNTIME_SNAPSHOT_FORMAT

    def __post_init__(self) -> None:
        if self.format != RUNTIME_SNAPSHOT_FORMAT:
            raise RuntimeContractError("unsupported RuntimeSnapshot format")
        if type(self.broker_state) is not BrokerRuntimeState:
            raise RuntimeContractError(
                "broker_state must be canonical BrokerRuntimeState"
            )
        if type(self.frame) is not RuntimeFrame:
            raise RuntimeContractError("frame must be RuntimeFrame")
        if type(self.target) is not RuntimeTargetIdentity:
            raise RuntimeContractError(
                "target must be RuntimeTargetIdentity"
            )
        state_target = self.broker_state.target
        state_frame = self.broker_state.frame
        if (
            not state_target.live
            or state_target.binding_id != self.target.binding_id
            or state_target.hwnd != self.target.hwnd
            or state_target.epoch != self.target.epoch
        ):
            raise RuntimeContractError(
                "Broker target and full target identity disagree"
            )
        if (
            state_frame.frame_id != self.frame.frame_id
            or state_frame.hwnd != self.frame.hwnd
            or state_frame.epoch != self.frame.epoch
            or state_frame.scope != self.frame.scope
        ):
            raise RuntimeContractError(
                "Broker frame and RuntimeFrame identity disagree"
            )
        RuntimeCommitIdentity(self.frame.identity, self.target)

    @property
    def commit_identity(self) -> RuntimeCommitIdentity:
        return RuntimeCommitIdentity(self.frame.identity, self.target)

    @property
    def authority_key(self) -> tuple[Any, ...]:
        return (
            self.broker_state,
            self.frame.authority_key,
            self.target,
        )


@dataclass(frozen=True, slots=True)
class ActorObligationView:
    desired_state: str
    acceptance_criteria: tuple[str, ...]
    forbidden_effects: tuple[str, ...]
    required_capabilities: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "desired_state",
            _text(self.desired_state, "desired_state"),
        )
        for name in (
            "acceptance_criteria",
            "forbidden_effects",
            "required_capabilities",
        ):
            object.__setattr__(
                self,
                name,
                _strings(getattr(self, name), name),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "desired_state": self.desired_state,
            "acceptance_criteria": list(self.acceptance_criteria),
            "forbidden_effects": list(self.forbidden_effects),
            "required_capabilities": list(self.required_capabilities),
        }


@dataclass(frozen=True, slots=True)
class ActorPreviousActionView:
    """A bounded fact derived from MissionCard, never a second history ledger."""

    action: str
    input_completed: bool
    visible_effect: str
    semantic_summary: str | None = None
    local_visual_change: str | None = None
    frame_comparable: bool | None = None
    target_binding_valid: bool | None = None

    def __post_init__(self) -> None:
        if type(self.action) is not str or not _TOOL.fullmatch(self.action):
            raise RuntimeContractError(
                "previous action must be a canonical tool name"
            )
        if type(self.input_completed) is not bool:
            raise RuntimeContractError(
                "previous action input_completed must be boolean"
            )
        object.__setattr__(
            self,
            "visible_effect",
            _text(
                self.visible_effect,
                "visible_effect",
                allow_empty=True,
            ),
        )
        if (
            self.semantic_summary is not None
            and (
                type(self.semantic_summary) is not str
                or not _action_summary_matches(
                    self.action,
                    self.semantic_summary,
                )
            )
        ):
            raise RuntimeContractError(
                "previous action semantic summary is invalid"
            )
        if self.local_visual_change not in (
            None,
            "changed",
            "unchanged",
            "unknown",
        ):
            raise RuntimeContractError(
                "previous action visual change is invalid"
            )
        for name in ("frame_comparable", "target_binding_valid"):
            value = getattr(self, name)
            if value is not None and type(value) is not bool:
                raise RuntimeContractError(
                    f"previous action {name} must be boolean or null"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "semantic_summary": self.semantic_summary,
            "input_completed": self.input_completed,
            "visible_effect": self.visible_effect,
            "local_visual_change": self.local_visual_change,
            "frame_comparable": self.frame_comparable,
            "target_binding_valid": self.target_binding_valid,
        }


@dataclass(frozen=True, slots=True)
class ActorMissionView:
    original_goal: str
    active_obligation: ActorObligationView
    verified_facts: tuple[str, ...] = ()
    failure_kind: str | None = None
    failure_message: str | None = None
    no_progress_streak: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "original_goal",
            _text(self.original_goal, "original_goal"),
        )
        if type(self.active_obligation) is not ActorObligationView:
            raise RuntimeContractError(
                "active_obligation must be ActorObligationView"
            )
        object.__setattr__(
            self,
            "verified_facts",
            _strings(self.verified_facts, "verified_facts"),
        )
        if (self.failure_kind is None) != (self.failure_message is None):
            raise RuntimeContractError(
                "failure kind and message must be present together"
            )
        if self.failure_kind is not None:
            object.__setattr__(
                self,
                "failure_kind",
                _identity(self.failure_kind, "failure_kind"),
            )
            object.__setattr__(
                self,
                "failure_message",
                _text(
                    self.failure_message,
                    "failure_message",
                    allow_empty=True,
                ),
            )
        if (
            type(self.no_progress_streak) is not int
            or self.no_progress_streak < 0
        ):
            raise RuntimeContractError(
                "no_progress_streak must be non-negative"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_goal": self.original_goal,
            "active_obligation": self.active_obligation.to_dict(),
            "verified_facts": list(self.verified_facts),
            "failure": (
                None
                if self.failure_kind is None
                else {
                    "kind": self.failure_kind,
                    "message": self.failure_message,
                }
            ),
            "no_progress_streak": self.no_progress_streak,
        }


@dataclass(frozen=True, slots=True)
class ActorFrameView:
    image_available: bool
    authoritative: bool
    fresh: bool
    coordinate_space: str = "submitted_image_pixels"

    def __post_init__(self) -> None:
        for name in ("image_available", "authoritative", "fresh"):
            if type(getattr(self, name)) is not bool:
                raise RuntimeContractError(f"{name} must be boolean")
        if self.coordinate_space != "submitted_image_pixels":
            raise RuntimeContractError(
                "Actor frame uses submitted-image pixels only"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_available": self.image_available,
            "authoritative": self.authoritative,
            "fresh": self.fresh,
            "coordinate_space": self.coordinate_space,
        }


@dataclass(frozen=True, slots=True)
class ActorToolView:
    name: str
    schema: Mapping[str, Any]
    requires_local_intent: bool

    def __post_init__(self) -> None:
        if (
            type(self.name) is not str
            or not _TOOL.fullmatch(self.name)
            or self.name not in APPROVED_RUNTIME_TOOLS
        ):
            raise RuntimeContractError("Actor tool is outside minimal Runtime")
        object.__setattr__(
            self, "schema", _bounded_mapping(self.schema, "tool schema")
        )
        if type(self.requires_local_intent) is not bool:
            raise RuntimeContractError(
                "requires_local_intent must be boolean"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "schema": _thaw_json(self.schema),
            "requires_local_intent": self.requires_local_intent,
        }


@dataclass(frozen=True, slots=True)
class ActorTurnInput:
    mission: ActorMissionView
    frame: ActorFrameView
    tools: tuple[ActorToolView, ...]
    completed_action_summaries: tuple[str, ...] = ()
    previous_action: ActorPreviousActionView | None = None
    format: str = ACTOR_TURN_FORMAT

    def __post_init__(self) -> None:
        if self.format != ACTOR_TURN_FORMAT:
            raise RuntimeContractError("unsupported ActorTurnInput format")
        if type(self.mission) is not ActorMissionView:
            raise RuntimeContractError(
                "Actor turn requires ActorMissionView"
            )
        if type(self.frame) is not ActorFrameView:
            raise RuntimeContractError("Actor turn requires ActorFrameView")
        tools = tuple(self.tools)
        if not tools or any(type(tool) is not ActorToolView for tool in tools):
            raise RuntimeContractError(
                "Actor turn requires projected Actor tools"
            )
        names = tuple(tool.name for tool in tools)
        if len(names) != len(set(names)):
            raise RuntimeContractError("Actor tools must be unique")
        if (
            self.previous_action is not None
            and type(self.previous_action) is not ActorPreviousActionView
        ):
            raise RuntimeContractError(
                "previous_action must be ActorPreviousActionView or null"
            )
        completed_action_summaries = tuple(
            self.completed_action_summaries
        )
        if (
            len(completed_action_summaries)
            > MAX_COMPLETED_ACTION_HISTORY
            or any(
                not _is_safe_action_summary(summary)
                for summary in completed_action_summaries
            )
        ):
            raise RuntimeContractError(
                "completed_action_summaries is outside the bounded "
                "privacy-safe history"
            )
        object.__setattr__(self, "tools", tools)
        object.__setattr__(
            self,
            "completed_action_summaries",
            completed_action_summaries,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "mission": self.mission.to_dict(),
            "frame": self.frame.to_dict(),
            "tools": [tool.to_dict() for tool in self.tools],
            "completed_action_summaries": list(
                self.completed_action_summaries
            ),
            "previous_action": (
                None
                if self.previous_action is None
                else self.previous_action.to_dict()
            ),
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())


@dataclass(frozen=True, slots=True)
class RuntimeVerifierTurn:
    """Exact read-only claim/frame input; the verifier cannot mutate either."""

    request: ObligationVerificationRequest
    frame: RuntimeFrame
    completed_action_summaries: tuple[str, ...] = ()
    format: str = RUNTIME_VERIFIER_TURN_FORMAT

    def __post_init__(self) -> None:
        if self.format != RUNTIME_VERIFIER_TURN_FORMAT:
            raise RuntimeContractError(
                "unsupported RuntimeVerifierTurn format"
            )
        if type(self.request) is not ObligationVerificationRequest:
            raise RuntimeContractError(
                "verifier turn requires ObligationVerificationRequest"
            )
        if type(self.frame) is not RuntimeFrame:
            raise RuntimeContractError(
                "verifier turn requires RuntimeFrame"
            )
        completed_action_summaries = tuple(
            self.completed_action_summaries
        )
        if (
            len(completed_action_summaries)
            > MAX_COMPLETED_ACTION_HISTORY
            or any(
                not _is_safe_action_summary(summary)
                for summary in completed_action_summaries
            )
        ):
            raise RuntimeContractError(
                "completed_action_summaries is outside the bounded "
                "privacy-safe history"
            )
        object.__setattr__(
            self,
            "completed_action_summaries",
            completed_action_summaries,
        )
        request_ids = {ref.id for ref in self.request.evidence_refs}
        if not request_ids <= set(self.frame.evidence_refs):
            raise RuntimeContractError(
                "verifier evidence is outside the frozen current frame"
            )
        if any(
            ref.frame_id not in (None, self.frame.frame_id)
            for ref in self.request.evidence_refs
        ):
            raise RuntimeContractError(
                "verifier evidence belongs to another frame"
            )


def project_actor_turn_input(
    card: MissionCard,
    frame_state: AuthoritativeFrameState,
    catalog: ToolCatalog,
    projection: BrokerProjection,
    *,
    completed_action_summaries: Iterable[str] = (),
) -> ActorTurnInput:
    """Project one identity-free bounded view from canonical Runtime facts."""

    completed_summaries = tuple(completed_action_summaries)
    if type(card) is not MissionCard or card.active_obligation is None:
        raise RuntimeContractError(
            "Visual Actor requires one active MissionCard obligation"
        )
    if type(frame_state) is not AuthoritativeFrameState:
        raise RuntimeContractError(
            "Actor projection requires AuthoritativeFrameState"
        )
    if type(catalog) is not ToolCatalog:
        raise RuntimeContractError("Actor projection requires ToolCatalog")
    if type(projection) is not BrokerProjection:
        raise RuntimeContractError(
            "Actor projection requires BrokerProjection"
        )
    if (
        card.mission_revision != projection.mission_revision
        or card.active_obligation_id != projection.obligation_id
    ):
        raise RuntimeContractError(
            "MissionCard and BrokerProjection identity disagree"
        )
    binding = card.target_binding
    if (
        binding is None
        or binding.binding_id != projection.target_binding_id
        or frame_state.frame_id != projection.frame_id
        or frame_state.hwnd != binding.hwnd
        or frame_state.epoch != binding.epoch
        or frame_state.scope != "target_window"
        or not frame_state.authoritative
        or not frame_state.fresh
    ):
        raise RuntimeContractError(
            "Actor projection requires the pre-bound fresh target frame"
        )
    obligation = card.active_obligation
    mission = ActorMissionView(
        original_goal=card.original_goal,
        active_obligation=ActorObligationView(
            desired_state=obligation.desired_state,
            acceptance_criteria=obligation.acceptance_criteria,
            forbidden_effects=obligation.forbidden_effects,
            required_capabilities=obligation.required_capabilities,
        ),
        verified_facts=tuple(
            fact.statement for fact in card.verified_facts
        ),
        failure_kind=(
            None if card.failure_summary is None else card.failure_summary.kind
        ),
        failure_message=(
            None
            if card.failure_summary is None
            else card.failure_summary.message
        ),
        no_progress_streak=card.no_progress_streak,
    )
    previous = None
    transition = card.last_transition
    if transition is not None:
        effect = transition.action_effect
        previous = ActorPreviousActionView(
            action=transition.action,
            semantic_summary=(
                completed_summaries[-1]
                if completed_summaries
                and _action_summary_matches(
                    transition.action,
                    completed_summaries[-1],
                )
                else None
            ),
            input_completed=transition.input_completed,
            visible_effect=transition.visible_effect,
            local_visual_change=(
                None
                if effect is None
                else effect.local_visual_change.value
            ),
            frame_comparable=(
                None if effect is None else effect.frame_comparable
            ),
            target_binding_valid=(
                None if effect is None else effect.target_binding_valid
            ),
        )
    tools: list[ActorToolView] = []
    for name in projection.tool_names:
        if name not in APPROVED_RUNTIME_TOOLS:
            raise RuntimeContractError(
                f"Broker projected unsupported Runtime tool {name!r}"
            )
        spec = catalog.get(name)
        if spec.route is not ToolRoute.VISUAL_ACTOR:
            raise RuntimeContractError(
                f"tool {name!r} is not a Visual Actor tool"
            )
        expected_mutation = (
            MutationKind.GUI
            if name in APPROVED_DESKTOP_ACTIONS
            else MutationKind.NONE
        )
        if spec.mutation_kind is not expected_mutation:
            raise RuntimeContractError(
                f"tool {name!r} mutation contract is invalid"
            )
        tools.append(
            ActorToolView(
                name=name,
                schema=spec.schema,
                requires_local_intent=(
                    spec.mutation_kind is MutationKind.GUI
                ),
            )
        )
    return ActorTurnInput(
        mission=mission,
        frame=ActorFrameView(
            image_available=frame_state.frame_id is not None,
            authoritative=frame_state.authoritative,
            fresh=frame_state.fresh,
        ),
        tools=tuple(tools),
        completed_action_summaries=completed_summaries,
        previous_action=previous,
    )


class ControllerOutcome(str, Enum):
    COMPLETED = "completed"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ControllerTransition:
    """Preserve input tri-state separately from post-action reconciliation."""

    outcome: ControllerOutcome | str
    action_id: str
    action: str
    input_completed: bool | None
    visible_effect: str = ""
    post_frame_evidence: tuple[EvidenceRef, ...] = ()
    post_frame_id: int | None = None
    action_effect: ActionEffectSummary | None = None
    action_effect_evidence: EvidenceRef | None = None
    format: str = CONTROLLER_TRANSITION_FORMAT

    def __post_init__(self) -> None:
        if self.format != CONTROLLER_TRANSITION_FORMAT:
            raise RuntimeContractError(
                "unsupported ControllerTransition format"
            )
        try:
            outcome = ControllerOutcome(self.outcome)
        except Exception as exc:
            raise RuntimeContractError(
                "invalid Controller outcome"
            ) from exc
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(
            self, "action_id", _identity(self.action_id, "action_id")
        )
        if type(self.action) is not str or not _TOOL.fullmatch(self.action):
            raise RuntimeContractError(
                "Controller action must be a canonical tool name"
            )
        if self.action not in APPROVED_DESKTOP_ACTIONS:
            raise RuntimeContractError(
                "Controller action is outside minimal Runtime"
            )
        if (
            self.input_completed is not None
            and type(self.input_completed) is not bool
        ):
            raise RuntimeContractError(
                "input_completed must be boolean or null"
            )
        object.__setattr__(
            self,
            "visible_effect",
            _text(
                self.visible_effect,
                "visible_effect",
                allow_empty=True,
            ),
        )
        post_evidence = tuple(self.post_frame_evidence)
        if any(type(ref) is not EvidenceRef for ref in post_evidence):
            raise RuntimeContractError(
                "post_frame_evidence must contain canonical EvidenceRef values"
            )
        post_ids = tuple(ref.id for ref in post_evidence)
        if len(post_ids) != len(set(post_ids)):
            raise RuntimeContractError(
                "post_frame_evidence must not contain duplicate ids"
            )
        if tuple(sorted(post_ids)) != post_ids:
            raise RuntimeContractError(
                "post_frame_evidence must be sorted by id"
            )
        object.__setattr__(self, "post_frame_evidence", post_evidence)
        if self.post_frame_id is not None and (
            type(self.post_frame_id) is not int
            or self.post_frame_id <= 0
        ):
            raise RuntimeContractError(
                "post_frame_id must be positive or null"
            )
        if outcome is ControllerOutcome.COMPLETED:
            if (
                self.input_completed is not True
                or self.post_frame_id is None
                or not post_evidence
            ):
                raise RuntimeContractError(
                    "completed transition requires input, post frame, and evidence"
                )
        elif outcome is ControllerOutcome.REJECTED:
            if self.input_completed is not False:
                raise RuntimeContractError(
                    "rejected transition must prove input did not execute"
                )
            if (self.post_frame_id is None) != (not post_evidence):
                raise RuntimeContractError(
                    "rejected post frame and evidence must be paired"
                )
        if self.action_effect is not None:
            if type(self.action_effect) is not ActionEffectSummary:
                raise RuntimeContractError(
                    "action_effect must be ActionEffectSummary"
                )
            if (
                outcome is not ControllerOutcome.COMPLETED
                or self.action_effect.action_id != self.action_id
                or self.action_effect.after_frame_id != self.post_frame_id
            ):
                raise RuntimeContractError(
                    "action effect does not bind the completed transition"
                )
            if (
                type(self.action_effect_evidence) is not EvidenceRef
                or self.action_effect_evidence.id
                != self.action_effect.evidence_ref
            ):
                raise RuntimeContractError(
                    "action effect requires its canonical EvidenceRef"
                )
        elif self.action_effect_evidence is not None:
            raise RuntimeContractError(
                "action_effect_evidence requires ActionEffectSummary"
            )

    @property
    def post_frame_evidence_refs(self) -> tuple[str, ...]:
        return tuple(ref.id for ref in self.post_frame_evidence)

    @property
    def produced_evidence(self) -> tuple[EvidenceRef, ...]:
        effect_ref = self.action_effect_evidence
        return (
            self.post_frame_evidence
            if effect_ref is None
            else (*self.post_frame_evidence, effect_ref)
        )

    @property
    def event_evidence_refs(self) -> tuple[str, ...]:
        refs = list(self.post_frame_evidence_refs)
        effect = self.action_effect
        if effect is not None:
            refs.extend(
                (
                    effect.before_frame_evidence_ref,
                    effect.after_frame_evidence_ref,
                    effect.evidence_ref,
                )
            )
        return tuple(dict.fromkeys(refs))

    def to_dict(self) -> dict[str, Any]:
        """Return the exact reasoning-free Controller transition snapshot."""

        return {
            "format": self.format,
            "outcome": self.outcome.value,
            "action_id": self.action_id,
            "action": self.action,
            "input_completed": self.input_completed,
            "visible_effect": self.visible_effect,
            "post_frame_evidence": [
                ref.to_dict() for ref in self.post_frame_evidence
            ],
            "post_frame_id": self.post_frame_id,
            "action_effect": (
                None
                if self.action_effect is None
                else self.action_effect.to_dict()
            ),
            "action_effect_evidence": (
                None
                if self.action_effect_evidence is None
                else self.action_effect_evidence.to_dict()
            ),
        }


class RuntimeStatus(str, Enum):
    ACTION_RECORDED = "action_recorded"
    PROPOSAL_SUPPRESSED = "proposal_suppressed"
    VERIFICATION_REQUIRED = "verification_required"
    VERIFICATION_RECORDED = "verification_recorded"
    MISSION_VERIFIED = "mission_verified"
    BLOCKED = "blocked"
    STALE_DISCARDED = "stale_discarded"
    CANCELED = "canceled"
    INVALID_PROPOSAL = "invalid_proposal"
    ACTOR_ERROR = "actor_error"
    STATE_REJECTED = "state_rejected"
    UNAVAILABLE = "unavailable"
    BUSY = "busy"


@dataclass(frozen=True, slots=True)
class MissionRuntimeResult:
    status: RuntimeStatus | str
    route: ExecutionRoute | str | None = None
    proposal_kind: str | None = None
    decision_id: str | None = None
    accepted_event_ids: tuple[str, ...] = ()
    detail: str = ""
    format: str = MISSION_RUNTIME_RESULT_FORMAT

    def __post_init__(self) -> None:
        if self.format != MISSION_RUNTIME_RESULT_FORMAT:
            raise RuntimeContractError(
                "unsupported MissionRuntimeResult format"
            )
        try:
            object.__setattr__(self, "status", RuntimeStatus(self.status))
        except Exception as exc:
            raise RuntimeContractError("invalid Runtime status") from exc
        if self.route is not None:
            try:
                object.__setattr__(
                    self, "route", ExecutionRoute(self.route)
                )
            except Exception as exc:
                raise RuntimeContractError("invalid execution route") from exc
        if self.proposal_kind is not None:
            object.__setattr__(
                self,
                "proposal_kind",
                _identity(self.proposal_kind, "proposal_kind"),
            )
        if self.decision_id is not None:
            object.__setattr__(
                self,
                "decision_id",
                _identity(self.decision_id, "decision_id"),
            )
        object.__setattr__(
            self,
            "accepted_event_ids",
            _ordered_identities(
                self.accepted_event_ids,
                "accepted_event_ids",
            ),
        )
        object.__setattr__(
            self,
            "detail",
            _text(self.detail, "detail", allow_empty=True),
        )


def frame_identity_payload_hash(frame: RuntimeFrameIdentity) -> str:
    if type(frame) is not RuntimeFrameIdentity:
        raise RuntimeContractError(
            "frame payload hash requires RuntimeFrameIdentity"
        )
    return frame.payload_hash


__all__ = [
    "RUNTIME_FRAME_FORMAT",
    "RUNTIME_SNAPSHOT_FORMAT",
    "ACTOR_TURN_FORMAT",
    "RUNTIME_VERIFIER_TURN_FORMAT",
    "CONTROLLER_TRANSITION_FORMAT",
    "MISSION_RUNTIME_RESULT_FORMAT",
    "CURRENT_FRAME_EVIDENCE_KIND",
    "POST_ACTION_FRAME_EVIDENCE_KIND",
    "VISUAL_VERIFIER_EVIDENCE_KIND",
    "VISUAL_VERIFIER_TRUST",
    "APPROVED_DESKTOP_ACTIONS",
    "APPROVED_OUTCOME_TOOLS",
    "APPROVED_RUNTIME_TOOLS",
    "MAX_COMPLETED_ACTION_HISTORY",
    "RuntimeContractError",
    "RuntimeFrameIdentity",
    "RuntimeTargetIdentity",
    "RuntimeCommitIdentity",
    "RuntimeFrame",
    "RuntimeSnapshot",
    "ActorObligationView",
    "ActorPreviousActionView",
    "ActorMissionView",
    "ActorFrameView",
    "ActorToolView",
    "ActorTurnInput",
    "RuntimeVerifierTurn",
    "project_actor_turn_input",
    "ControllerOutcome",
    "ControllerTransition",
    "RuntimeStatus",
    "MissionRuntimeResult",
    "frame_identity_payload_hash",
]
