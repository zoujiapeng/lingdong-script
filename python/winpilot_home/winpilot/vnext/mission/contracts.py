"""Small, single-goal state used by the live desktop runtime.

The Visual Actor owns task reasoning and completion judgment.  This module
only keeps the few facts that must survive between physical turns: the
original goal, the initial target binding, the last mechanical result, and
the terminal state.  It deliberately has no task DAG, evidence registry,
artifact graph, or event replay protocol.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


_MAX_TEXT = 16_384
_SAFE_ACTIONS = frozenset(
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


class MissionError(ValueError):
    """The one live goal is no longer in a state that can change."""


class IllegalTransitionError(MissionError):
    """A state change was requested after the goal became terminal."""


def _text(value: Any, name: str, *, allow_empty: bool = False) -> str:
    if type(value) is not str:
        raise MissionError(f"{name} must be a string")
    result = value.strip()
    if not allow_empty and not result:
        raise MissionError(f"{name} must not be empty")
    if len(result) > _MAX_TEXT:
        raise MissionError(f"{name} is too long")
    return result


def _positive_int(value: Any, name: str, *, allow_zero: bool = False) -> int:
    if type(value) is not int or value < (0 if allow_zero else 1):
        qualifier = "non-negative" if allow_zero else "positive"
        raise MissionError(f"{name} must be a {qualifier} integer")
    return value


class MissionStatus(str, Enum):
    RUNNING = "running"
    BLOCKED = "blocked"
    VERIFIED = "verified"
    CANCELED = "canceled"


@dataclass(frozen=True, slots=True)
class TargetBinding:
    """The target identity frozen before the first desktop frame."""

    binding_id: str
    hwnd: int
    process_id: int
    executable_identity: str
    title_snapshot: str
    epoch: int

    _FIELDS = (
        "binding_id",
        "hwnd",
        "process_id",
        "executable_identity",
        "title_snapshot",
        "epoch",
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "binding_id", _text(self.binding_id, "binding_id"))
        object.__setattr__(self, "hwnd", _positive_int(self.hwnd, "hwnd"))
        object.__setattr__(self, "process_id", _positive_int(self.process_id, "process_id"))
        object.__setattr__(
            self,
            "executable_identity",
            _text(self.executable_identity, "executable_identity"),
        )
        object.__setattr__(
            self,
            "title_snapshot",
            _text(self.title_snapshot, "title_snapshot", allow_empty=True),
        )
        object.__setattr__(self, "epoch", _positive_int(self.epoch, "epoch", allow_zero=True))

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "hwnd": self.hwnd,
            "process_id": self.process_id,
            "executable_identity": self.executable_identity,
            "title_snapshot": self.title_snapshot,
            "epoch": self.epoch,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "TargetBinding":
        if not isinstance(raw, Mapping) or set(raw) != set(cls._FIELDS):
            raise MissionError("TargetBinding fields must match exactly")
        return cls(
            binding_id=raw["binding_id"],
            hwnd=raw["hwnd"],
            process_id=raw["process_id"],
            executable_identity=raw["executable_identity"],
            title_snapshot=raw["title_snapshot"],
            epoch=raw["epoch"],
        )


@dataclass(frozen=True, slots=True)
class TransitionSummary:
    """The last proven physical-input result shown back to the Actor."""

    action: str
    input_completed: bool
    visible_effect: str = ""
    frame_id: int | None = None
    action_id: str | None = None
    error_kind: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", _text(self.action, "action"))
        if type(self.input_completed) is not bool:
            raise MissionError("input_completed must be boolean")
        object.__setattr__(
            self,
            "visible_effect",
            _text(self.visible_effect, "visible_effect", allow_empty=True),
        )
        if self.frame_id is not None:
            object.__setattr__(
                self,
                "frame_id",
                _positive_int(self.frame_id, "frame_id", allow_zero=True),
            )
        for name in ("action_id", "error_kind"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(
                    self,
                    name,
                    _text(value, name, allow_empty=name == "error_kind"),
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "input_completed": self.input_completed,
            "visible_effect": self.visible_effect,
            "frame_id": self.frame_id,
            "action_id": self.action_id,
            "error_kind": self.error_kind,
        }


@dataclass(frozen=True, slots=True)
class FailureSummary:
    kind: str
    message: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _text(self.kind, "failure kind"))
        object.__setattr__(
            self,
            "message",
            _text(self.message, "failure message", allow_empty=True),
        )

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "message": self.message}


@dataclass(frozen=True, slots=True)
class MissionCard:
    """Identity-free per-turn context for the one Visual Actor."""

    mission_id: str
    mission_revision: int
    original_goal: str
    target_binding: TargetBinding | None
    last_transition: TransitionSummary | None = None
    failure_summary: FailureSummary | None = None
    format: str = "mission-card/1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "mission_id", _text(self.mission_id, "mission_id"))
        object.__setattr__(self, "original_goal", _text(self.original_goal, "original_goal"))
        object.__setattr__(
            self,
            "mission_revision",
            _positive_int(self.mission_revision, "mission_revision", allow_zero=True),
        )
        if type(self.target_binding) not in {TargetBinding, type(None)}:
            raise MissionError("target_binding must be TargetBinding or null")
        if self.last_transition is not None and type(self.last_transition) is not TransitionSummary:
            raise MissionError("last_transition must be TransitionSummary or null")
        if self.failure_summary is not None and type(self.failure_summary) is not FailureSummary:
            raise MissionError("failure_summary must be FailureSummary or null")

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "mission_id": self.mission_id,
            "mission_revision": self.mission_revision,
            "original_goal": self.original_goal,
            "target_binding": (
                None if self.target_binding is None else self.target_binding.to_dict()
            ),
            "last_transition": (
                None if self.last_transition is None else self.last_transition.to_dict()
            ),
            "failure_summary": (
                None if self.failure_summary is None else self.failure_summary.to_dict()
            ),
        }


class MissionState:
    """Mutable state for one admitted goal, owned by MissionRuntime's turn lock."""

    def __init__(
        self,
        *,
        mission_id: str,
        run_id: str,
        original_goal: str,
        target_binding: TargetBinding | None,
    ) -> None:
        self.mission_id = _text(mission_id, "mission_id")
        self.run_id = _text(run_id, "run_id")
        self.original_goal = _text(original_goal, "original_goal")
        if type(target_binding) not in {TargetBinding, type(None)}:
            raise MissionError("target_binding must be TargetBinding or null")
        self.target_binding = target_binding
        self.revision = 0
        self.status = MissionStatus.RUNNING
        self.last_transition: TransitionSummary | None = None
        self.failure_summary: FailureSummary | None = None
        self.last_error = ""

    @property
    def card(self) -> MissionCard:
        return MissionCard(
            mission_id=self.mission_id,
            mission_revision=self.revision,
            original_goal=self.original_goal,
            target_binding=self.target_binding,
            last_transition=self.last_transition,
            failure_summary=self.failure_summary,
        )

    def _require_running(self) -> None:
        if self.status is not MissionStatus.RUNNING:
            raise IllegalTransitionError(
                f"mission is terminal: {self.status.value}"
            )

    def record_transition(
        self,
        *,
        action: str,
        input_completed: bool,
        visible_effect: str = "",
        frame_id: int | None = None,
        action_id: str | None = None,
        error_kind: str | None = None,
    ) -> None:
        self._require_running()
        self.last_transition = TransitionSummary(
            action=action,
            input_completed=input_completed,
            visible_effect=visible_effect,
            frame_id=frame_id,
            action_id=action_id,
            error_kind=error_kind,
        )
        self.revision += 1

    def complete(self) -> None:
        self._require_running()
        self.status = MissionStatus.VERIFIED
        self.failure_summary = None
        self.revision += 1

    def block(self, *, kind: str, message: str) -> None:
        self._require_running()
        self.status = MissionStatus.BLOCKED
        self.failure_summary = FailureSummary(kind=kind, message=message)
        self.revision += 1

    def cancel(self, message: str) -> None:
        self._require_running()
        self.status = MissionStatus.CANCELED
        self.last_error = _text(message, "cancellation message", allow_empty=True)
        self.revision += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "run_id": self.run_id,
            "original_goal": self.original_goal,
            "target_binding": (
                None if self.target_binding is None else self.target_binding.to_dict()
            ),
            "revision": self.revision,
            "status": self.status.value,
            "last_transition": (
                None if self.last_transition is None else self.last_transition.to_dict()
            ),
            "failure_summary": (
                None if self.failure_summary is None else self.failure_summary.to_dict()
            ),
            "last_error": self.last_error,
        }


def is_safe_action_summary(action: str, summary: str) -> bool:
    """Allow only the action name in durable mechanical summaries."""

    return type(action) is str and type(summary) is str and action in _SAFE_ACTIONS and summary == action


__all__ = [
    "MissionError",
    "IllegalTransitionError",
    "MissionStatus",
    "TargetBinding",
    "TransitionSummary",
    "FailureSummary",
    "MissionCard",
    "MissionState",
    "is_safe_action_summary",
]
