"""Typed, session-free execution of already-admitted physical input."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
import math
from types import MappingProxyType
from typing import Any

from .replay_policy import MutationOutcome


class MouseButton(str, Enum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


class ClickCount(int, Enum):
    SINGLE = 1
    DOUBLE = 2


class ClickMechanism(str, Enum):
    BACKGROUND_MESSAGE = "background_message"
    PHYSICAL = "physical"
    PREPOSITIONED_PHYSICAL = "prepositioned_physical"


def _scalar_facts(facts: Mapping[str, Any]) -> Mapping[str, Any]:
    frozen: dict[str, Any] = {}
    for key, value in facts.items():
        if type(key) is not str or not key:
            raise TypeError("raw fact keys must be non-empty strings")
        if value is not None and type(value) not in {bool, int, float, str}:
            raise TypeError("raw facts must contain JSON scalar values")
        if type(value) is float and not math.isfinite(value):
            raise TypeError("raw fact numbers must be finite")
        frozen[key] = value
    return MappingProxyType(frozen)


@dataclass(frozen=True, slots=True)
class RawActionContext:
    """Target identity already selected by the Controller."""

    target_hwnd: int
    current_hwnd: int


@dataclass(frozen=True, slots=True)
class RawBackendResult:
    """Explicit mechanical result returned by one physical backend call."""

    input_completed: bool | None
    facts: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.input_completed is not None and type(self.input_completed) is not bool:
            raise TypeError("input_completed must be boolean or null")
        if not isinstance(self.facts, Mapping):
            raise TypeError("raw backend facts must be a mapping")
        object.__setattr__(self, "facts", _scalar_facts(self.facts))

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_completed": self.input_completed,
            "facts": dict(self.facts),
        }


@dataclass(frozen=True, slots=True)
class RawActionReceipt:
    """Mechanical input fact, never proof of a visible or semantic effect."""

    action: str
    outcome: MutationOutcome
    backend_result: RawBackendResult | None = None
    error_kind: str | None = None

    def __post_init__(self) -> None:
        if self.action not in {"scroll", "click"}:
            raise ValueError("unknown raw receipt action")
        if not isinstance(self.outcome, MutationOutcome):
            raise ValueError("raw receipt outcome must be MutationOutcome")
        if self.backend_result is not None and not isinstance(
            self.backend_result, RawBackendResult
        ):
            raise TypeError("backend_result must be RawBackendResult or null")
        if self.backend_result is not None and (
            self.backend_result.input_completed is not self.input_completed
        ):
            raise ValueError("backend_result contradicts raw receipt outcome")
        if self.outcome in {MutationOutcome.APPLIED, MutationOutcome.NOT_APPLIED}:
            if self.backend_result is None:
                raise ValueError("applied/not-applied receipt requires backend_result")
        elif self.outcome is MutationOutcome.NOT_STARTED and self.backend_result is not None:
            raise ValueError("not-started receipt cannot contain backend_result")
        if self.error_kind is not None and (
            type(self.error_kind) is not str or not self.error_kind
        ):
            raise ValueError("error_kind must be non-empty or null")
        if self.outcome in {
            MutationOutcome.NOT_STARTED,
            MutationOutcome.OUTCOME_UNKNOWN,
        } and self.error_kind is None:
            raise ValueError("uncertain/not-started receipt requires error_kind")
        if self.outcome in {MutationOutcome.APPLIED, MutationOutcome.NOT_APPLIED}:
            if self.error_kind is not None:
                raise ValueError("certain raw receipt cannot contain error_kind")

    @property
    def input_completed(self) -> bool | None:
        return {
            MutationOutcome.APPLIED: True,
            MutationOutcome.NOT_APPLIED: False,
            MutationOutcome.NOT_STARTED: False,
            MutationOutcome.OUTCOME_UNKNOWN: None,
        }[self.outcome]

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "outcome": self.outcome.value,
            "input_completed": self.input_completed,
            "backend_result": (
                None if self.backend_result is None else self.backend_result.to_dict()
            ),
            "error_kind": self.error_kind,
        }


RawBackend = Callable[..., RawBackendResult]


@dataclass(frozen=True, slots=True)
class RawDeviceBackends:
    scroll: RawBackend | None = None
    click: RawBackend | None = None

    def __post_init__(self) -> None:
        for name in ("scroll", "click"):
            backend = getattr(self, name)
            if backend is not None and not callable(backend):
                raise TypeError(f"{name} backend must be callable or null")


class RawDeviceExecutor:
    """Always-strict typed primitive executor; no parsing, policy, or replay."""

    def __init__(self, backends: RawDeviceBackends) -> None:
        if not isinstance(backends, RawDeviceBackends):
            raise TypeError("backends must be RawDeviceBackends")
        self._backends = backends

    @staticmethod
    def _receipt(
        action: str,
        outcome: MutationOutcome,
        *,
        backend_result: RawBackendResult | None = None,
        error_kind: str | None = None,
    ) -> RawActionReceipt:
        return RawActionReceipt(action, outcome, backend_result, error_kind)

    @staticmethod
    def _context_error(context: RawActionContext) -> str | None:
        valid_hwnd = lambda value: type(value) is int and value > 0
        if not isinstance(context, RawActionContext) or not all(
            map(valid_hwnd, (context.target_hwnd, context.current_hwnd))
        ):
            return "invalid_context"
        if context.target_hwnd != context.current_hwnd:
            return "target_mismatch"
        return None

    @classmethod
    def _from_backend(cls, action: str, result: object) -> RawActionReceipt:
        if not isinstance(result, RawBackendResult):
            return cls._receipt(
                action, MutationOutcome.OUTCOME_UNKNOWN,
                error_kind="backend_result_uncertain",
            )
        completed = result.input_completed
        outcome = (
            MutationOutcome.OUTCOME_UNKNOWN if completed is None
            else MutationOutcome.APPLIED if completed
            else MutationOutcome.NOT_APPLIED
        )
        return cls._receipt(
            action, outcome, backend_result=result,
            error_kind="backend_result_uncertain" if completed is None else None,
        )

    @classmethod
    def _call(
        cls, action: str, backend: Callable[..., RawBackendResult] | None,
        *args: Any, **kwargs: Any,
    ) -> RawActionReceipt:
        if backend is None:
            return cls._receipt(
                action, MutationOutcome.NOT_STARTED,
                error_kind="backend_unavailable",
            )
        try:
            result = backend(*args, **kwargs)
        except Exception:
            return cls._receipt(
                action, MutationOutcome.OUTCOME_UNKNOWN,
                error_kind="backend_exception",
            )
        return cls._from_backend(action, result)

    def scroll(
        self,
        context: RawActionContext,
        x: int,
        y: int,
        amount: int,
    ) -> RawActionReceipt:
        context_error = self._context_error(context)
        if context_error is not None:
            return self._receipt(
                "scroll", MutationOutcome.NOT_STARTED,
                error_kind=context_error,
            )
        if any(type(value) is not int for value in (x, y, amount)) or amount == 0:
            return self._receipt(
                "scroll", MutationOutcome.NOT_STARTED,
                error_kind="invalid_args",
            )
        return self._call(
            "scroll", self._backends.scroll,
            x, y, amount, hwnd=context.target_hwnd,
        )

    def click(
        self,
        context: RawActionContext,
        x: int,
        y: int,
        button: MouseButton,
        count: ClickCount,
        mechanism: ClickMechanism,
    ) -> RawActionReceipt:
        context_error = self._context_error(context)
        if context_error is not None:
            return self._receipt(
                "click", MutationOutcome.NOT_STARTED,
                error_kind=context_error,
            )
        if not (
            type(x) is int and type(y) is int
            and isinstance(button, MouseButton)
            and isinstance(count, ClickCount)
            and isinstance(mechanism, ClickMechanism)
        ):
            return self._receipt(
                "click", MutationOutcome.NOT_STARTED,
                error_kind="invalid_args",
            )
        return self._call(
            "click", self._backends.click, x, y, button=button, count=count,
            mechanism=mechanism, hwnd=context.target_hwnd,
        )


__all__ = [
    "ClickCount", "ClickMechanism", "MouseButton", "RawActionContext",
    "RawActionReceipt", "RawBackendResult", "RawDeviceBackends", "RawDeviceExecutor",
]
