"""Small in-memory scratchpad for the single Visual Actor loop."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

_SCRATCHPAD_CHARS = 4096


class ActorTaskStateError(ValueError):
    """The untrusted Actor scratchpad is outside its small contract."""


def _scratchpad(value: Any, name: str) -> str:
    if (
        type(value) is not str
        or len(value) > _SCRATCHPAD_CHARS
        or (value and not value.strip())
    ):
        raise ActorTaskStateError(
            f"{name} must be bounded text or an empty string"
        )
    return value


@dataclass(frozen=True, slots=True)
class ActorTaskStateUpdate:
    """``None`` retains the current scratchpad; a string replaces it."""

    scratchpad: str | None

    @classmethod
    def from_dict(cls, value: Any) -> "ActorTaskStateUpdate":
        if value is None:
            return cls(None)
        if type(value) is not dict or set(value) != {"scratchpad"}:
            raise ActorTaskStateError(
                "task_state_update must contain exactly scratchpad"
            )
        scratchpad = value["scratchpad"]
        return cls(
            None
            if scratchpad is None
            else _scratchpad(scratchpad, "scratchpad")
        )


@dataclass(frozen=True, slots=True)
class ActorTaskState:
    scratchpad: str

    def __post_init__(self) -> None:
        _scratchpad(self.scratchpad, "scratchpad")

    @classmethod
    def initial(cls) -> "ActorTaskState":
        return cls("")

    def apply(self, patch: ActorTaskStateUpdate) -> "ActorTaskState":
        if type(patch) is not ActorTaskStateUpdate:
            raise ActorTaskStateError("task state requires a typed update")
        return ActorTaskState(
            self.scratchpad
            if patch.scratchpad is None
            else patch.scratchpad
        )

    def to_dict(self) -> dict[str, Any]:
        return {"scratchpad": self.scratchpad}


@dataclass(frozen=True, slots=True)
class ActorTurnDecision:
    """Transient pair; task_state_update never enters the bound proposal."""

    proposal: Mapping[str, Any]
    task_state_update: ActorTaskStateUpdate
    narration: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.proposal, Mapping):
            raise ActorTaskStateError("Actor proposal must be a mapping")
        if type(self.task_state_update) is not ActorTaskStateUpdate:
            raise ActorTaskStateError("Actor decision requires a typed update")
        if type(self.narration) is not str:
            raise ActorTaskStateError("Actor narration must be text")
        object.__setattr__(self, "proposal", MappingProxyType(dict(self.proposal)))


TASK_STATE_UPDATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "scratchpad": {
            "anyOf": [
                {
                    "type": "string",
                    "maxLength": _SCRATCHPAD_CHARS,
                },
                {"type": "null"},
            ]
        },
    },
    "required": ["scratchpad"],
}

__all__ = [
    "ActorTaskState", "ActorTaskStateError", "ActorTaskStateUpdate",
    "ActorTurnDecision", "TASK_STATE_UPDATE_SCHEMA",
]
