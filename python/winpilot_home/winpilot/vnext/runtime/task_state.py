"""Small in-memory working state for the single Visual Actor loop."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

_SCRATCHPAD_CHARS = 8192
_TODO_STATUSES = ("pending", "in_progress", "completed")
_MAX_TODOS = 32
_TODO_CONTENT_CHARS = 256
_TODO_ACTIVEFORM_CHARS = 256


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


def _bounded_text(value: Any, name: str, limit: int) -> str:
    if type(value) is not str or not value.strip() or len(value) > limit:
        raise ActorTaskStateError(
            f"{name} must be bounded non-empty text"
        )
    return value


def _validate_todo_list(items: tuple["ActorTodoItem", ...], name: str) -> None:
    if len(items) > _MAX_TODOS:
        raise ActorTaskStateError(
            f"{name} must be at most {_MAX_TODOS} items"
        )
    if sum(1 for item in items if item.status == "in_progress") > 1:
        raise ActorTaskStateError(
            f"{name} allows at most one in_progress item"
        )


def _todo_items(value: Any, name: str) -> tuple["ActorTodoItem", ...]:
    if type(value) is not list:
        raise ActorTaskStateError(
            f"{name} must be a bounded list of todo items"
        )
    items: list[ActorTodoItem] = []
    for item in value:
        if (
            type(item) is not dict
            or set(item) != {"content", "activeForm", "status"}
        ):
            raise ActorTaskStateError(
                f"{name} items must be "
                "{content, activeForm, status} objects"
            )
        items.append(
            ActorTodoItem(
                content=item["content"],
                activeForm=item["activeForm"],
                status=item["status"],
            )
        )
    result = tuple(items)
    _validate_todo_list(result, name)
    return result


@dataclass(frozen=True, slots=True)
class ActorTodoItem:
    """One user-visible todo entry with imperative and active-form text."""

    content: str
    activeForm: str
    status: str

    def __post_init__(self) -> None:
        _bounded_text(self.content, "todo content", _TODO_CONTENT_CHARS)
        _bounded_text(
            self.activeForm, "todo activeForm", _TODO_ACTIVEFORM_CHARS
        )
        if type(self.status) is not str or self.status not in _TODO_STATUSES:
            raise ActorTaskStateError(
                f"todo status must be one of {', '.join(_TODO_STATUSES)}"
            )

    def to_dict(self) -> dict[str, str]:
        return {
            "content": self.content,
            "activeForm": self.activeForm,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class ActorTaskStateUpdate:
    """Field patch; ``None`` retains and an empty list clears the checklist."""

    scratchpad: str | None = None
    todos: tuple[ActorTodoItem, ...] | None = None

    @classmethod
    def from_dict(cls, value: Any) -> "ActorTaskStateUpdate":
        if value is None:
            return cls()
        allowed = {"scratchpad", "todos"}
        if (
            type(value) is not dict
            or not set(value) <= allowed
        ):
            raise ActorTaskStateError(
                "task_state_update allows only scratchpad and todos"
            )
        scratchpad = value.get("scratchpad")
        todos = value.get("todos")
        return cls(
            scratchpad=(
                None
                if scratchpad is None
                else _scratchpad(scratchpad, "scratchpad")
            ),
            todos=(
                None
                if todos is None
                else _todo_items(todos, "todos")
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
            todos=(
                self.todos
                if newer.todos is None
                else newer.todos
            ),
        )

    @property
    def changes_state(self) -> bool:
        return self.scratchpad is not None or self.todos is not None


@dataclass(frozen=True, slots=True)
class ActorTaskState:
    scratchpad: str
    todos: tuple[ActorTodoItem, ...] = ()

    def __post_init__(self) -> None:
        _scratchpad(self.scratchpad, "scratchpad")
        if type(self.todos) is not tuple or any(
            type(item) is not ActorTodoItem for item in self.todos
        ):
            raise ActorTaskStateError(
                "todos state must be a tuple of todo items"
            )
        _validate_todo_list(self.todos, "todos")

    @property
    def remaining_items(self) -> tuple[str, ...]:
        """Derived view of unfinished work; never a second source of truth."""

        return tuple(
            item.content
            for item in self.todos
            if item.status != "completed"
        )

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
            todos=(
                self.todos
                if patch.todos is None
                else patch.todos
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "scratchpad": self.scratchpad,
            "todos": [item.to_dict() for item in self.todos],
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
                "recovery notes. Keep the user-visible checklist in todos."
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
        "todos": {
            "description": (
                "Your user-visible todo checklist. Send the full list each "
                "time; each item is {content (imperative), activeForm "
                "(present continuous), status}. Keep at most one in_progress. "
                "Null or omission preserves it; [] clears it."
            ),
            "anyOf": [
                {
                    "type": "array",
                    "maxItems": _MAX_TODOS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": _TODO_CONTENT_CHARS,
                            },
                            "activeForm": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": _TODO_ACTIVEFORM_CHARS,
                            },
                            "status": {
                                "type": "string",
                                "enum": list(_TODO_STATUSES),
                            },
                        },
                        "required": ["content", "activeForm", "status"],
                        "additionalProperties": False,
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
    "ActorTodoItem", "ActorTurnDecision", "TASK_STATE_UPDATE_SCHEMA",
]
