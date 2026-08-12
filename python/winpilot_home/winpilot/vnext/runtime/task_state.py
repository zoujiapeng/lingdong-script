"""Small in-memory working state for the single Visual Actor loop."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

_SCRATCHPAD_CHARS = 8192
_MAX_REMAINING_ITEMS = 32
_REMAINING_ITEM_CHARS = 256


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


def _remaining_items(value: Any, name: str) -> tuple[str, ...]:
    if (
        type(value) is not list
        or len(value) > _MAX_REMAINING_ITEMS
    ):
        raise ActorTaskStateError(
            f"{name} must be a bounded list of unfinished work"
        )
    items = tuple(value)
    if any(
        type(item) is not str
        or not item.strip()
        or len(item) > _REMAINING_ITEM_CHARS
        for item in items
    ):
        raise ActorTaskStateError(
            f"{name} items must be bounded non-empty text"
        )
    return items


@dataclass(frozen=True, slots=True)
class ActorTaskStateUpdate:
    """Field patch; ``None`` retains and an empty list clears remaining work."""

    scratchpad: str | None = None
    remaining_items: tuple[str, ...] | None = None

    @classmethod
    def from_dict(cls, value: Any) -> "ActorTaskStateUpdate":
        if value is None:
            return cls()
        allowed = {"scratchpad", "remaining_items"}
        if (
            type(value) is not dict
            or not set(value) <= allowed
        ):
            raise ActorTaskStateError(
                "task_state_update allows only scratchpad and "
                "remaining_items"
            )
        scratchpad = value.get("scratchpad")
        remaining_items = value.get("remaining_items")
        return cls(
            scratchpad=(
                None
                if scratchpad is None
                else _scratchpad(scratchpad, "scratchpad")
            ),
            remaining_items=(
                None
                if remaining_items is None
                else _remaining_items(remaining_items, "remaining_items")
            ),
        )

    def merged_with(
        self,
        newer: "ActorTaskStateUpdate",
    ) -> "ActorTaskStateUpdate":
        """Overlay a later model patch without erasing omitted fields."""

        if type(newer) is not ActorTaskStateUpdate:
            raise ActorTaskStateError("task state merge requires a typed update")
        return ActorTaskStateUpdate(
            scratchpad=(
                self.scratchpad
                if newer.scratchpad is None
                else newer.scratchpad
            ),
            remaining_items=(
                self.remaining_items
                if newer.remaining_items is None
                else newer.remaining_items
            ),
        )

    @property
    def changes_state(self) -> bool:
        return self.scratchpad is not None or self.remaining_items is not None


@dataclass(frozen=True, slots=True)
class ActorTaskState:
    scratchpad: str
    remaining_items: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _scratchpad(self.scratchpad, "scratchpad")
        if type(self.remaining_items) is not tuple:
            raise ActorTaskStateError("remaining_items state must be a tuple")
        _remaining_items(list(self.remaining_items), "remaining_items")

    @classmethod
    def initial(cls) -> "ActorTaskState":
        return cls("")

    def apply(self, patch: ActorTaskStateUpdate) -> "ActorTaskState":
        if type(patch) is not ActorTaskStateUpdate:
            raise ActorTaskStateError("task state requires a typed update")
        return ActorTaskState(
            scratchpad=(
                self.scratchpad
                if patch.scratchpad is None
                else patch.scratchpad
            ),
            remaining_items=(
                self.remaining_items
                if patch.remaining_items is None
                else patch.remaining_items
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "scratchpad": self.scratchpad,
            "remaining_items": list(self.remaining_items),
        }


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
            "description": (
                "Mutable current phase, stage rules, facts, artifacts, or "
                "recovery notes. Use remaining_items for dependent "
                "unfinished work."
            ),
            "anyOf": [
                {
                    "type": "string",
                    "maxLength": _SCRATCHPAD_CHARS,
                },
                {"type": "null"},
            ],
            "default": None,
        },
        "remaining_items": {
            "description": (
                "Your editable ordered list of unfinished work for this goal. "
                "Null or omission preserves it; [] clears it when none remain."
            ),
            "anyOf": [
                {
                    "type": "array",
                    "maxItems": _MAX_REMAINING_ITEMS,
                    "items": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": _REMAINING_ITEM_CHARS,
                    },
                },
                {"type": "null"},
            ],
            "default": None,
        },
    },
    "required": [],
}

__all__ = [
    "ActorTaskState", "ActorTaskStateError", "ActorTaskStateUpdate",
    "ActorTurnDecision", "TASK_STATE_UPDATE_SCHEMA",
]
