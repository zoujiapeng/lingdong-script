"""Typed, default-unreachable contracts for the minimal vNext live Runtime.

These values freeze facts owned by existing kernel components.  They are not
new frame, target, evidence, or completion authorities.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from ..json_contract import BoundedJSONError, bounded_json_copy
from ..mission import (
    MissionCard,
    TargetBinding,
    is_safe_action_summary,
)
from .task_state import (
    ActorTaskState,
    ActorTaskStateError,
    ActorTaskStateUpdate,
    ActorTodoItem,
    ActorTurnDecision,
    TASK_STATE_UPDATE_SCHEMA,
)


RUNTIME_FRAME_FORMAT = "runtime-frame/0"
RUNTIME_SNAPSHOT_FORMAT = "runtime-snapshot/0"
ACTOR_TURN_FORMAT = "runtime-actor-turn/0"
CONTROLLER_TRANSITION_FORMAT = "controller-transition/0"
MISSION_RUNTIME_RESULT_FORMAT = "mission-runtime-result/0"

APPROVED_DESKTOP_ACTIONS = frozenset(
    {
        "click",
        "double_click",
        "move_mouse",
        "drag",
        "scroll",
        "type_text",
        "hotkey",
        "input_sequence",
        "launch_application",
    }
)
APPROVED_OUTCOME_TOOLS = frozenset(
    {"claim_obligation_done", "blocked"}
)
APPROVED_OBSERVATION_TOOLS = frozenset({"observe"})
APPROVED_RUNTIME_TOOLS = (
    APPROVED_DESKTOP_ACTIONS
    | APPROVED_OBSERVATION_TOOLS
    | APPROVED_OUTCOME_TOOLS
)
MAX_COMPLETED_ACTION_SUMMARY_LENGTH = 256

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}")
_TOOL = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MAX_TEXT = 16_384
_MAX_FACTS = 64


class RuntimeContractError(ValueError):
    """A Runtime boundary value is not canonical."""


class PointerDisposition(str, Enum):
    RELEASED = "released"
    HELD = "held"
    UNKNOWN = "unknown"


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
    if type(value) is not str or len(value) > MAX_COMPLETED_ACTION_SUMMARY_LENGTH:
        return False
    action, separator, intent = value.partition(": ")
    if action not in APPROVED_DESKTOP_ACTIONS:
        return False
    if not separator:
        return is_safe_action_summary(action, value)
    return bool(intent) and " ".join(intent.split()) == intent


def _action_summary_matches(action: str, summary: str) -> bool:
    if not _is_safe_action_summary(summary):
        return False
    summary_action, _, _ = summary.partition(": ")
    return summary_action == action


def _previous_action_summary(
    action: str,
    local_intent: str | None,
) -> str | None:
    """One bounded identity-free summary of the immediately preceding action."""
    intent = " ".join(str(local_intent or "").split())
    if not intent:
        return action
    prefix = f"{action}: "
    return prefix + intent[
        : MAX_COMPLETED_ACTION_SUMMARY_LENGTH - len(prefix)
    ].rstrip()


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
            or self.frame.scope not in {"target_window", "full_desktop"}
        ):
            raise RuntimeContractError(
                "frame and target commit identities disagree"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame": self.frame.to_dict(),
            "target": self.target.to_dict(),
        }

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
    # 上一张权威帧到这一帧的改动比例（0..1）。只作为事实带给模型，
    # 让"连续几步画面没动"这件事在对话里可见；runtime 不据此判断对错。
    change_ratio_from_previous: float | None = None
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
        object.__setattr__(self, "region", identity.region)
        object.__setattr__(self, "scope", identity.scope)
        object.__setattr__(self, "submitted_size", identity.submitted_size)
        object.__setattr__(self, "image_sha256", identity.image_sha256)
        object.__setattr__(self, "media_type", identity.media_type)

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
        return (self.identity,)



@dataclass(frozen=True, slots=True)
class PointerLeaseState:
    disposition: PointerDisposition | str = PointerDisposition.UNKNOWN
    lease_id: str | None = None
    physically_owned: bool = False

    def __post_init__(self) -> None:
        try:
            disposition = PointerDisposition(self.disposition)
        except Exception as exc:
            raise RuntimeContractError(
                "invalid pointer disposition"
            ) from exc
        if type(self.physically_owned) is not bool:
            raise RuntimeContractError(
                "pointer physically_owned must be boolean"
            )
        lease_id = self.lease_id
        if lease_id is not None:
            lease_id = _identity(lease_id, "lease_id")
        if disposition is PointerDisposition.HELD and lease_id is None:
            raise RuntimeContractError("held pointer requires lease_id")
        if disposition is PointerDisposition.RELEASED and lease_id is not None:
            raise RuntimeContractError(
                "released pointer cannot retain lease_id"
            )
        if self.physically_owned and disposition is not PointerDisposition.HELD:
            raise RuntimeContractError(
                "only a held pointer may be physically owned"
            )
        object.__setattr__(self, "disposition", disposition)
        object.__setattr__(self, "lease_id", lease_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition.value,
            "lease_id": self.lease_id,
            "physically_owned": self.physically_owned,
        }


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    """One fresh target/frame observation plus physical runtime state."""

    frame: RuntimeFrame
    target: RuntimeTargetIdentity
    pointer: PointerLeaseState
    paused: bool = False
    canceled: bool = False
    format: str = RUNTIME_SNAPSHOT_FORMAT

    def __post_init__(self) -> None:
        if self.format != RUNTIME_SNAPSHOT_FORMAT:
            raise RuntimeContractError("unsupported RuntimeSnapshot format")
        if type(self.frame) is not RuntimeFrame:
            raise RuntimeContractError("frame must be RuntimeFrame")
        if type(self.target) is not RuntimeTargetIdentity:
            raise RuntimeContractError(
                "target must be RuntimeTargetIdentity"
            )
        if type(self.pointer) is not PointerLeaseState:
            raise RuntimeContractError(
                "pointer must be PointerLeaseState"
            )
        for name in ("paused", "canceled"):
            if type(getattr(self, name)) is not bool:
                raise RuntimeContractError(f"{name} must be boolean")
        RuntimeCommitIdentity(self.frame.identity, self.target)

    @property
    def commit_identity(self) -> RuntimeCommitIdentity:
        return RuntimeCommitIdentity(self.frame.identity, self.target)

    @property
    def authority_key(self) -> tuple[Any, ...]:
        return (
            self.frame.authority_key,
            self.target,
            self.pointer,
            self.paused,
            self.canceled,
        )


@dataclass(frozen=True, slots=True)
class ActorPreviousActionView:
    """One bounded prior-action fact, never a second history ledger."""

    action: str
    input_completed: bool
    visible_effect: str
    semantic_summary: str | None = None
    local_intent: str | None = None
    error_kind: str | None = None
    # How many times this mission has already attempted exactly this action with
    # exactly these arguments, counting this one. The runtime only counts; it
    # never decides what the count means or what to do about it.
    same_action_attempts: int = 1

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
        if self.local_intent is not None:
            object.__setattr__(
                self,
                "local_intent",
                _text(self.local_intent, "local_intent", allow_empty=True),
            )
        if self.error_kind is not None:
            object.__setattr__(
                self,
                "error_kind",
                _text(self.error_kind, "error_kind", allow_empty=True),
            )
        if type(self.same_action_attempts) is not int or self.same_action_attempts < 1:
            raise RuntimeContractError(
                "previous action same_action_attempts must be an integer >= 1"
            )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "action": self.action,
            "semantic_summary": self.semantic_summary,
            "local_intent": self.local_intent,
            "input_completed": self.input_completed,
            "visible_effect": self.visible_effect,
            "same_action_attempts": self.same_action_attempts,
        }
        if self.error_kind is not None:
            result["error_kind"] = self.error_kind
        return result


@dataclass(frozen=True, slots=True)
class ActorBudgetView:
    """One mechanical fuel-gauge fact, never a spending policy.

    The Runtime reports how much of the frozen run budget is already spent
    (turns, model calls, wall time) and how much remains.  Whether to slow
    down, skim, skip or stop is the Actor's own semantic decision; the gauge
    only makes the facts visible every turn.
    """

    turns_used: int
    max_turns: int
    model_calls_used: int
    max_model_calls: int
    elapsed_s: int
    max_runtime_s: int

    def __post_init__(self) -> None:
        for name, minimum in (
            ("turns_used", 0),
            ("max_turns", 1),
            ("model_calls_used", 0),
            ("max_model_calls", 1),
            ("elapsed_s", 0),
            ("max_runtime_s", 1),
        ):
            value = getattr(self, name)
            if type(value) is not int or value < minimum:
                raise RuntimeContractError(
                    f"budget {name} must be an integer >= {minimum}"
                )
        if self.turns_used > self.max_turns:
            raise RuntimeContractError(
                "budget turns_used exceeds max_turns"
            )
        if self.model_calls_used > self.max_model_calls:
            raise RuntimeContractError(
                "budget model_calls_used exceeds max_model_calls"
            )
        if self.elapsed_s > self.max_runtime_s:
            raise RuntimeContractError(
                "budget elapsed_s exceeds max_runtime_s"
            )

    def to_dict(self) -> dict[str, int]:
        return {
            "turns_used": self.turns_used,
            "max_turns": self.max_turns,
            "model_calls_used": self.model_calls_used,
            "max_model_calls": self.max_model_calls,
            "elapsed_s": self.elapsed_s,
            "max_runtime_s": self.max_runtime_s,
        }


@dataclass(frozen=True, slots=True)
class ActorMissionView:
    original_goal: str
    failure_kind: str | None = None
    failure_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "original_goal",
            _text(self.original_goal, "original_goal"),
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
    def to_dict(self) -> dict[str, Any]:
        return {
            "original_goal": self.original_goal,
            "failure": (
                None
                if self.failure_kind is None
                else {
                    "kind": self.failure_kind,
                    "message": self.failure_message,
                }
            ),
        }


@dataclass(frozen=True, slots=True)
class ActorFrameView:
    image_available: bool
    authoritative: bool
    fresh: bool
    coordinate_space: str = "submitted_image_pixels"
    window_hwnd: int | None = None
    change_ratio_from_previous: float | None = None

    def __post_init__(self) -> None:
        for name in ("image_available", "authoritative", "fresh"):
            if type(getattr(self, name)) is not bool:
                raise RuntimeContractError(f"{name} must be boolean")
        if self.coordinate_space != "submitted_image_pixels":
            raise RuntimeContractError(
                "Actor frame uses submitted-image pixels only"
            )
        if self.window_hwnd is not None and type(self.window_hwnd) is not int:
            raise RuntimeContractError("window_hwnd must be an int or None")

    def to_dict(self) -> dict[str, Any]:
        # window_hwnd stays an internal reader handle and is intentionally not
        # part of the identity-free model-facing view.
        return {
            "image_available": self.image_available,
            "authoritative": self.authoritative,
            "fresh": self.fresh,
            "coordinate_space": self.coordinate_space,
            "change_ratio_from_previous": self.change_ratio_from_previous,
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
    task_state: ActorTaskState | None = None
    budget: ActorBudgetView | None = None
    previous_action: ActorPreviousActionView | None = None
    pointer_held: bool = False
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
        if self.task_state is None:
            object.__setattr__(self, "task_state", ActorTaskState.initial())
        elif type(self.task_state) is not ActorTaskState:
            raise RuntimeContractError("Actor turn requires ActorTaskState")
        if self.budget is not None and type(self.budget) is not ActorBudgetView:
            raise RuntimeContractError(
                "Actor turn requires ActorBudgetView or null"
            )
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
        if type(self.pointer_held) is not bool:
            raise RuntimeContractError("pointer_held must be boolean")
        object.__setattr__(self, "tools", tools)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "mission": self.mission.to_dict(),
            "frame": self.frame.to_dict(),
            "task_state": self.task_state.to_dict(),
            "budget": (
                None if self.budget is None else self.budget.to_dict()
            ),
            "tools": [tool.to_dict() for tool in self.tools],
            "previous_action": (
                None
                if self.previous_action is None
                else self.previous_action.to_dict()
            ),
            "pointer_held": self.pointer_held,
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())


def project_actor_turn_input(
    card: MissionCard,
    snapshot: RuntimeSnapshot,
    tools: tuple[ActorToolView, ...],
    *,
    task_state: ActorTaskState | None = None,
    previous_action_local_intent: str | None = None,
    budget: ActorBudgetView | None = None,
) -> ActorTurnInput:
    """Project one identity-free bounded view from canonical Runtime facts."""

    if type(card) is not MissionCard:
        raise RuntimeContractError(
            "Visual Actor requires a canonical MissionCard"
        )
    if budget is not None and type(budget) is not ActorBudgetView:
        raise RuntimeContractError(
            "Actor projection requires ActorBudgetView or null"
        )
    if type(snapshot) is not RuntimeSnapshot:
        raise RuntimeContractError("Actor projection requires RuntimeSnapshot")
    if snapshot.frame.scope == "target_window":
        binding = card.target_binding
        if (
            binding is None
            or binding.binding_id != snapshot.target.binding_id
            or snapshot.frame.hwnd != binding.hwnd
            or snapshot.frame.epoch != binding.epoch
        ):
            raise RuntimeContractError(
                "Target-window projection requires its current binding"
            )
    mission = ActorMissionView(
        original_goal=card.original_goal,
        failure_kind=(
            None if card.failure_summary is None else card.failure_summary.kind
        ),
        failure_message=(
            None
            if card.failure_summary is None
            else card.failure_summary.message
        ),
    )
    previous = None
    transition = card.last_transition
    if transition is not None:
        previous = ActorPreviousActionView(
            action=transition.action,
            semantic_summary=_previous_action_summary(
                transition.action,
                previous_action_local_intent,
            ),
            local_intent=previous_action_local_intent,
            input_completed=transition.input_completed,
            visible_effect=transition.visible_effect,
            error_kind=transition.error_kind,
            same_action_attempts=card.same_action_attempts,
        )
    return ActorTurnInput(
        mission=mission,
        frame=ActorFrameView(
            image_available=True,
            authoritative=True,
            fresh=True,
            window_hwnd=snapshot.frame.hwnd,
            change_ratio_from_previous=(
                snapshot.frame.change_ratio_from_previous
            ),
        ),
        tools=tools,
        task_state=task_state,
        budget=budget,
        previous_action=previous,
        pointer_held=(
            snapshot.pointer.disposition is PointerDisposition.HELD
        ),
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
    post_frame_id: int | None = None
    error_kind: str | None = None
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
        if self.error_kind is not None:
            object.__setattr__(
                self,
                "error_kind",
                _text(self.error_kind, "error_kind", allow_empty=True),
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
        if self.post_frame_id is not None and (
            type(self.post_frame_id) is not int
            or self.post_frame_id <= 0
        ):
            raise RuntimeContractError(
                "post_frame_id must be positive or null"
            )
        if outcome is ControllerOutcome.COMPLETED:
            if self.input_completed is not True:
                raise RuntimeContractError(
                    "completed transition requires completed input"
                )
        elif outcome is ControllerOutcome.REJECTED:
            if self.input_completed is not False:
                raise RuntimeContractError(
                    "rejected transition must prove input did not execute"
                )
    def to_dict(self) -> dict[str, Any]:
        """Return the exact reasoning-free Controller transition snapshot."""

        return {
            "format": self.format,
            "outcome": self.outcome.value,
            "action_id": self.action_id,
            "action": self.action,
            "input_completed": self.input_completed,
            "visible_effect": self.visible_effect,
            "post_frame_id": self.post_frame_id,
            "error_kind": self.error_kind,
        }


class RuntimeStatus(str, Enum):
    ACTION_RECORDED = "action_recorded"
    OBSERVATION_REQUESTED = "observation_requested"
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
    proposal_kind: str | None = None
    decision_id: str | None = None
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
            "detail",
            _text(self.detail, "detail", allow_empty=True),
        )


__all__ = [
    "RUNTIME_FRAME_FORMAT",
    "RUNTIME_SNAPSHOT_FORMAT",
    "ACTOR_TURN_FORMAT",
    "CONTROLLER_TRANSITION_FORMAT",
    "MISSION_RUNTIME_RESULT_FORMAT",
    "APPROVED_DESKTOP_ACTIONS",
    "APPROVED_OBSERVATION_TOOLS",
    "APPROVED_OUTCOME_TOOLS",
    "APPROVED_RUNTIME_TOOLS",
    "RuntimeContractError",
    "PointerDisposition",
    "PointerLeaseState",
    "RuntimeFrameIdentity",
    "RuntimeTargetIdentity",
    "RuntimeCommitIdentity",
    "RuntimeFrame",
    "RuntimeSnapshot",
    "ActorPreviousActionView",
    "ActorMissionView",
    "ActorFrameView",
    "ActorToolView",
    "ActorTurnInput",
    "ActorBudgetView",
    "ActorTaskState",
    "ActorTaskStateError",
    "ActorTaskStateUpdate",
    "ActorTodoItem",
    "ActorTurnDecision",
    "TASK_STATE_UPDATE_SCHEMA",
    "project_actor_turn_input",
    "ControllerOutcome",
    "ControllerTransition",
    "RuntimeStatus",
    "MissionRuntimeResult",
]
