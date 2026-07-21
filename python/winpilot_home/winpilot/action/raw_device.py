"""Typed, session-free execution of already-admitted physical input."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Any

from .replay_policy import MutationOutcome


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

    outcome: MutationOutcome
    backend_result: RawBackendResult | None = None
    error_kind: str | None = None

    def __post_init__(self) -> None:
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
    def action(self) -> str:
        return "scroll"

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


ScrollBackend = Callable[..., RawBackendResult]


class RawDeviceExecutor:
    """Always-strict typed primitive executor; no parsing, policy, or replay."""

    def __init__(self, *, scroll_backend: ScrollBackend) -> None:
        if not callable(scroll_backend):
            raise TypeError("scroll_backend must be callable")
        self._scroll_backend = scroll_backend

    @staticmethod
    def _receipt(
        outcome: MutationOutcome,
        *,
        backend_result: RawBackendResult | None = None,
        error_kind: str | None = None,
    ) -> RawActionReceipt:
        return RawActionReceipt(outcome, backend_result, error_kind)

    def scroll(
        self,
        context: RawActionContext,
        x: int,
        y: int,
        amount: int,
    ) -> RawActionReceipt:
        valid_hwnd = lambda value: type(value) is int and value > 0
        if not isinstance(context, RawActionContext) or not all(
            map(valid_hwnd, (context.target_hwnd, context.current_hwnd))
        ):
            return self._receipt(
                MutationOutcome.NOT_STARTED, error_kind="invalid_context",
            )
        if context.target_hwnd != context.current_hwnd:
            return self._receipt(
                MutationOutcome.NOT_STARTED, error_kind="target_mismatch",
            )
        if any(type(value) is not int for value in (x, y, amount)) or amount == 0:
            return self._receipt(
                MutationOutcome.NOT_STARTED, error_kind="invalid_args",
            )
        try:
            result = self._scroll_backend(
                x, y, amount, hwnd=context.target_hwnd,
            )
        except Exception:
            return self._receipt(
                MutationOutcome.OUTCOME_UNKNOWN, error_kind="backend_exception",
            )
        if not isinstance(result, RawBackendResult):
            return self._receipt(
                MutationOutcome.OUTCOME_UNKNOWN,
                error_kind="backend_result_uncertain",
            )
        if result.input_completed is True:
            return self._receipt(MutationOutcome.APPLIED, backend_result=result)
        if result.input_completed is False:
            return self._receipt(MutationOutcome.NOT_APPLIED, backend_result=result)
        return self._receipt(
            MutationOutcome.OUTCOME_UNKNOWN,
            backend_result=result,
            error_kind="backend_result_uncertain",
        )


__all__ = [
    "RawActionContext", "RawActionReceipt", "RawBackendResult",
    "RawDeviceExecutor",
]
