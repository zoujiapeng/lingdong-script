"""Typed execution of already-admitted physical input."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
import math
import threading
from types import MappingProxyType
from typing import Any

from ..visual_runtime.keyboard_contracts import (
    CANONICAL_HOTKEY_KEYS,
    CANONICAL_HOTKEY_MODIFIERS,
    HotkeyChord,
    MAX_TYPE_TEXT_CODEPOINTS,
)
from .replay_policy import MutationOutcome


MAX_DRAG_INTERMEDIATE_POINTS = 32


def _canonical_drag_points(value: Any) -> tuple[tuple[int, int], ...] | None:
    if not isinstance(value, (list, tuple)):
        return None
    if len(value) > MAX_DRAG_INTERMEDIATE_POINTS:
        return None
    points: list[tuple[int, int]] = []
    for point in value:
        if (
            not isinstance(point, (list, tuple))
            or len(point) != 2
            or any(type(coordinate) is not int for coordinate in point)
        ):
            return None
        points.append((point[0], point[1]))
    return tuple(points)


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


class FinalPointerGuardDecision(str, Enum):
    """Mechanical target verdict sampled immediately before pointer input."""

    ADMITTED = "admitted"
    TARGET_UNAVAILABLE = "target_unavailable"
    TARGET_ROOT_CHANGED = "target_root_changed"
    TARGET_PROCESS_CHANGED = "target_process_changed"
    TARGET_GEOMETRY_CHANGED = "target_geometry_changed"
    POINT_OUTSIDE_TARGET = "point_outside_target"
    POINTER_POSITION_CHANGED = "pointer_position_changed"
    STOP_REQUESTED = "stop_requested"
    HIT_TEST_UNAVAILABLE = "hit_test_unavailable"
    HIT_TEST_CHANGED = "hit_test_changed"


class FinalKeyboardGuardDecision(str, Enum):
    """Mechanical target verdict sampled immediately before keyboard input."""

    ADMITTED = "admitted"
    TARGET_UNAVAILABLE = "target_unavailable"
    TARGET_ROOT_CHANGED = "target_root_changed"
    TARGET_PROCESS_CHANGED = "target_process_changed"
    TARGET_GEOMETRY_CHANGED = "target_geometry_changed"
    FOREGROUND_UNAVAILABLE = "foreground_unavailable"
    FOREGROUND_CHANGED = "foreground_changed"
    STOP_REQUESTED = "stop_requested"


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
        if self.action not in {
            "scroll",
            "click",
            "drag",
            "type_text",
            "hotkey",
            "launch_application",
        }:
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
FinalPointerGuard = Callable[[int, int], FinalPointerGuardDecision]
FinalKeyboardGuard = Callable[[], FinalKeyboardGuardDecision]


@dataclass(frozen=True, slots=True)
class AdmittedPhysicalClickBackend:
    """Explicitly marks a backend which never acquires DesktopActionQueue."""

    execute: RawBackend

    def __post_init__(self) -> None:
        if not callable(self.execute):
            raise TypeError("admitted physical click backend must be callable")
        if hasattr(self.execute, "__wrapped__"):
            raise TypeError("admitted physical click backend cannot wrap a decorated entry")

    def __call__(self, *args: Any, **kwargs: Any) -> RawBackendResult:
        return self.execute(*args, **kwargs)


@dataclass(frozen=True, slots=True)
class AdmittedPhysicalScrollBackend:
    """Explicitly marks a physical wheel backend which never acquires the queue."""

    execute: RawBackend

    def __post_init__(self) -> None:
        if not callable(self.execute):
            raise TypeError("admitted physical scroll backend must be callable")
        if hasattr(self.execute, "__wrapped__"):
            raise TypeError("admitted physical scroll backend cannot wrap a decorated entry")

    def __call__(self, *args: Any, **kwargs: Any) -> RawBackendResult:
        return self.execute(*args, **kwargs)


@dataclass(frozen=True, slots=True)
class AdmittedPhysicalDragBackend:
    """Marks an unqueued physical press-move-release backend."""

    execute: RawBackend

    def __post_init__(self) -> None:
        if not callable(self.execute):
            raise TypeError("admitted physical drag backend must be callable")
        if hasattr(self.execute, "__wrapped__"):
            raise TypeError("admitted physical drag backend cannot wrap a decorated entry")

    def __call__(self, *args: Any, **kwargs: Any) -> RawBackendResult:
        return self.execute(*args, **kwargs)


@dataclass(frozen=True, slots=True)
class AdmittedPhysicalTypeTextBackend:
    """Marks an unqueued, single-mechanism physical text backend."""

    execute: RawBackend

    def __post_init__(self) -> None:
        if not callable(self.execute):
            raise TypeError("admitted physical type-text backend must be callable")
        if hasattr(self.execute, "__wrapped__"):
            raise TypeError("admitted physical type-text backend cannot wrap a decorated entry")

    def __call__(self, *args: Any, **kwargs: Any) -> RawBackendResult:
        return self.execute(*args, **kwargs)


@dataclass(frozen=True, slots=True)
class AdmittedPhysicalHotkeyBackend:
    """Marks an unqueued, single-mechanism physical hotkey backend."""

    execute: RawBackend

    def __post_init__(self) -> None:
        if not callable(self.execute):
            raise TypeError("admitted physical hotkey backend must be callable")
        if hasattr(self.execute, "__wrapped__"):
            raise TypeError("admitted physical hotkey backend cannot wrap a decorated entry")

    def __call__(self, *args: Any, **kwargs: Any) -> RawBackendResult:
        return self.execute(*args, **kwargs)


@dataclass(frozen=True, slots=True)
class RawDeviceBackends:
    scroll: RawBackend | None = None
    click: RawBackend | None = None
    admitted_physical_click: AdmittedPhysicalClickBackend | None = None
    admitted_physical_drag: AdmittedPhysicalDragBackend | None = None
    admitted_physical_scroll: AdmittedPhysicalScrollBackend | None = None
    admitted_physical_type_text: AdmittedPhysicalTypeTextBackend | None = None
    admitted_physical_hotkey: AdmittedPhysicalHotkeyBackend | None = None

    def __post_init__(self) -> None:
        for name in ("scroll", "click"):
            backend = getattr(self, name)
            if backend is not None and not callable(backend):
                raise TypeError(f"{name} backend must be callable or null")
        if self.admitted_physical_click is not None and not isinstance(
            self.admitted_physical_click, AdmittedPhysicalClickBackend
        ):
            raise TypeError(
                "admitted_physical_click must be AdmittedPhysicalClickBackend or null"
            )
        if self.admitted_physical_scroll is not None and not isinstance(
            self.admitted_physical_scroll, AdmittedPhysicalScrollBackend
        ):
            raise TypeError(
                "admitted_physical_scroll must be AdmittedPhysicalScrollBackend or null"
            )
        if self.admitted_physical_drag is not None and not isinstance(
            self.admitted_physical_drag, AdmittedPhysicalDragBackend
        ):
            raise TypeError(
                "admitted_physical_drag must be AdmittedPhysicalDragBackend or null"
            )
        if self.admitted_physical_type_text is not None and not isinstance(
            self.admitted_physical_type_text, AdmittedPhysicalTypeTextBackend
        ):
            raise TypeError(
                "admitted_physical_type_text must be "
                "AdmittedPhysicalTypeTextBackend or null"
            )
        if self.admitted_physical_hotkey is not None and not isinstance(
            self.admitted_physical_hotkey, AdmittedPhysicalHotkeyBackend
        ):
            raise TypeError(
                "admitted_physical_hotkey must be AdmittedPhysicalHotkeyBackend or null"
            )


@dataclass(slots=True)
class _HeldDragOwner:
    token: str
    hwnd: int
    point: tuple[int, int]
    backend: AdmittedPhysicalDragBackend


_held_drag_state_lock = threading.Lock()
_held_drag_operation_lock = threading.Lock()
_held_drag_owner: _HeldDragOwner | None = None


class RawDeviceExecutor:
    """Always-strict typed primitive executor; no parsing, policy, or replay."""

    def __init__(self, backends: RawDeviceBackends) -> None:
        if not isinstance(backends, RawDeviceBackends):
            raise TypeError("backends must be RawDeviceBackends")
        self._backends = backends

    @property
    def admitted_action_names(self) -> tuple[str, ...]:
        """Return the immutable inventory of queue-admitted physical primitives.

        This exposes names only, never backend callables.  Product composition
        uses it to prove that every advertised Controller primitive has one
        concrete RawDevice route before the Visual Actor is constructed.
        """

        pairs = (
            ("click", self._backends.admitted_physical_click),
            ("drag", self._backends.admitted_physical_drag),
            ("scroll", self._backends.admitted_physical_scroll),
            ("type_text", self._backends.admitted_physical_type_text),
            ("hotkey", self._backends.admitted_physical_hotkey),
        )
        return tuple(sorted(name for name, backend in pairs if backend is not None))

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
        if action in {"type_text", "hotkey"}:
            result = cls._sanitize_keyboard_result(action, result)
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

    @staticmethod
    def _sanitize_keyboard_result(
        action: str,
        result: RawBackendResult,
    ) -> RawBackendResult:
        """Keep only bounded non-content diagnostics at the RawDevice boundary."""

        source = result.facts
        safe: dict[str, Any] = {}
        for key in (
            "ok", "dry_run", "release_cleanup_attempted",
            "release_cleanup_completed",
        ):
            if type(source.get(key)) is bool:
                safe[key] = source[key]
        if source.get("via") == "physical_sendinput":
            safe["via"] = "physical_sendinput"
        decision = source.get("guard_decision")
        if type(decision) is str and decision in {
            item.value for item in FinalKeyboardGuardDecision
        }:
            safe["guard_decision"] = decision
        if action == "type_text":
            for key in ("requested_codepoints", "completed_codepoints"):
                value = source.get(key)
                if type(value) is int and 0 <= value <= MAX_TYPE_TEXT_CODEPOINTS:
                    safe[key] = value
        else:
            value = source.get("key_count")
            if type(value) is int and 0 <= value <= 6:
                safe["key_count"] = value
        return RawBackendResult(result.input_completed, safe)

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

    @classmethod
    def _held_drag_blocked(cls, action: str) -> RawActionReceipt | None:
        with _held_drag_state_lock:
            active = _held_drag_owner is not None
        if not active:
            return None
        return cls._receipt(
            action,
            MutationOutcome.NOT_STARTED,
            error_kind="held_drag_active",
        )

    @classmethod
    def _held_drag_unknown(
        cls,
        receipt: RawActionReceipt,
        *,
        error_kind: str = "held_drag_outcome_uncertain",
    ) -> RawActionReceipt:
        if receipt.outcome is MutationOutcome.OUTCOME_UNKNOWN:
            return receipt
        backend_result = receipt.backend_result
        uncertain_result = (
            None
            if backend_result is None
            else RawBackendResult(None, backend_result.facts)
        )
        return cls._receipt(
            "drag",
            MutationOutcome.OUTCOME_UNKNOWN,
            backend_result=uncertain_result,
            error_kind=error_kind,
        )

    def scroll(
        self,
        context: RawActionContext,
        x: int,
        y: int,
        amount: int,
        *,
        final_guard: FinalPointerGuard | None = None,
        enforce_foreground: bool = True,
    ) -> RawActionReceipt:
        context_error = self._context_error(context)
        if context_error is not None:
            return self._receipt(
                "scroll", MutationOutcome.NOT_STARTED,
                error_kind=context_error,
            )
        held_block = self._held_drag_blocked("scroll")
        if held_block is not None:
            return held_block
        if any(type(value) is not int for value in (x, y, amount)) or amount == 0:
            return self._receipt(
                "scroll", MutationOutcome.NOT_STARTED,
                error_kind="invalid_args",
            )
        backend = self._backends.scroll
        call_kwargs: dict[str, Any] = {
            "hwnd": context.target_hwnd if enforce_foreground else None,
        }
        if final_guard is not None:
            if not callable(final_guard):
                return self._receipt(
                    "scroll", MutationOutcome.NOT_STARTED,
                    error_kind="invalid_final_guard",
                )
            backend = self._backends.admitted_physical_scroll
            call_kwargs["final_guard"] = final_guard
        return self._call(
            "scroll", backend, x, y, amount, **call_kwargs,
        )

    def click(
        self,
        context: RawActionContext,
        x: int,
        y: int,
        button: MouseButton,
        count: ClickCount,
        mechanism: ClickMechanism,
        *,
        final_guard: FinalPointerGuard | None = None,
        enforce_foreground: bool = True,
    ) -> RawActionReceipt:
        context_error = self._context_error(context)
        if context_error is not None:
            return self._receipt(
                "click", MutationOutcome.NOT_STARTED,
                error_kind=context_error,
            )
        held_block = self._held_drag_blocked("click")
        if held_block is not None:
            return held_block
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
        backend = self._backends.click
        call_kwargs: dict[str, Any] = {
            "hwnd": context.target_hwnd if enforce_foreground else None,
        }
        if final_guard is not None:
            if (
                not callable(final_guard)
                or mechanism is ClickMechanism.BACKGROUND_MESSAGE
            ):
                return self._receipt(
                    "click", MutationOutcome.NOT_STARTED,
                    error_kind="invalid_final_guard",
                )
            backend = self._backends.admitted_physical_click
            call_kwargs["final_guard"] = final_guard
            call_kwargs["enforce_foreground"] = enforce_foreground
        return self._call(
            "click", backend, x, y, button=button, count=count,
            mechanism=mechanism, **call_kwargs,
        )

    def drag(
        self,
        context: RawActionContext,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        *,
        points: list[tuple[int, int]] | tuple[tuple[int, int], ...] = (),
        final_guard: FinalPointerGuard,
        pointer_already_held: bool = False,
        keep_holding: bool = False,
        hold_token: str | None = None,
        enforce_foreground: bool = True,
    ) -> RawActionReceipt:
        global _held_drag_owner

        context_error = self._context_error(context)
        if context_error is not None:
            return self._receipt(
                "drag", MutationOutcome.NOT_STARTED,
                error_kind=context_error,
            )
        canonical_points = _canonical_drag_points(points)
        stationary_finish = bool(
            pointer_already_held is True
            and keep_holding is False
            and not canonical_points
            and (x1, y1) == (x2, y2)
        )
        closed_path = bool(
            canonical_points
            and (x1, y1) == (x2, y2)
        )
        if (
            any(type(value) is not int for value in (x1, y1, x2, y2))
            or (
                (x1, y1) == (x2, y2)
                and not stationary_finish
                and not closed_path
            )
            or canonical_points is None
            or not callable(final_guard)
            or type(pointer_already_held) is not bool
            or type(keep_holding) is not bool
            or type(enforce_foreground) is not bool
        ):
            return self._receipt(
                "drag", MutationOutcome.NOT_STARTED,
                error_kind="invalid_args",
            )
        is_held_segment = pointer_already_held or keep_holding
        if (
            (is_held_segment and (
                type(hold_token) is not str or not hold_token or len(hold_token) > 256
            ))
            or (not is_held_segment and hold_token is not None)
        ):
            return self._receipt(
                "drag",
                MutationOutcome.NOT_STARTED,
                error_kind="invalid_held_drag_args",
            )
        call_kwargs: dict[str, Any] = {
            "hwnd": context.target_hwnd if enforce_foreground else None,
            "final_guard": final_guard,
        }
        if canonical_points:
            call_kwargs["points"] = canonical_points
        if not is_held_segment:
            held_block = self._held_drag_blocked("drag")
            if held_block is not None:
                return held_block
            return self._call(
                "drag",
                self._backends.admitted_physical_drag,
                x1,
                y1,
                x2,
                y2,
                **call_kwargs,
            )

        backend = self._backends.admitted_physical_drag
        if backend is None:
            return self._receipt(
                "drag",
                MutationOutcome.NOT_STARTED,
                error_kind="backend_unavailable",
            )

        with _held_drag_operation_lock:
            with _held_drag_state_lock:
                owner = _held_drag_owner
                if not pointer_already_held:
                    if owner is not None:
                        return self._receipt(
                            "drag",
                            MutationOutcome.NOT_STARTED,
                            error_kind="held_drag_active",
                        )
                    _held_drag_owner = _HeldDragOwner(
                        token=hold_token,
                        hwnd=context.target_hwnd,
                        point=(x1, y1),
                        backend=backend,
                    )
                else:
                    if owner is None:
                        return self._receipt(
                            "drag",
                            MutationOutcome.NOT_STARTED,
                            error_kind="held_drag_not_active",
                        )
                    if owner.token != hold_token:
                        return self._receipt(
                            "drag",
                            MutationOutcome.NOT_STARTED,
                            error_kind="held_drag_token_mismatch",
                        )
                    if (
                        owner.hwnd != context.target_hwnd
                        or owner.backend is not backend
                    ):
                        return self._receipt(
                            "drag",
                            MutationOutcome.NOT_STARTED,
                            error_kind="held_drag_owner_mismatch",
                        )
                    if owner.point != (x1, y1):
                        return self._receipt(
                            "drag",
                            MutationOutcome.NOT_STARTED,
                            error_kind="held_drag_start_mismatch",
                        )

            call_kwargs.update(
                pointer_already_held=pointer_already_held,
                keep_holding=keep_holding,
                hold_token=hold_token,
            )
            receipt = self._call(
                "drag",
                backend,
                x1,
                y1,
                x2,
                y2,
                **call_kwargs,
            )

            expected_held_after = keep_holding
            reported_held_after = (
                None
                if receipt.backend_result is None
                else receipt.backend_result.facts.get("held_after")
            )
            completed_consistently = (
                receipt.outcome is MutationOutcome.APPLIED
                and (
                    reported_held_after is None
                    or (
                        type(reported_held_after) is bool
                        and reported_held_after is expected_held_after
                    )
                )
            )
            with _held_drag_state_lock:
                owner = _held_drag_owner
                if owner is not None and owner.token == hold_token:
                    # The no-op failure path deliberately retains the owner:
                    # DOWN, MOVE, or UP may have been injected, so only an
                    # explicit cleanup may reopen the process-wide gate.
                    if completed_consistently and keep_holding:
                        owner.point = (x2, y2)
                    elif completed_consistently:
                        _held_drag_owner = None
            if completed_consistently:
                return receipt
            return self._held_drag_unknown(receipt)

    def release_held_drag(self, hold_token: str) -> RawActionReceipt:
        """Best-effort, idempotent release for an interrupted held drag."""

        global _held_drag_owner

        if (
            type(hold_token) is not str
            or not hold_token
            or len(hold_token) > 256
        ):
            return self._receipt(
                "drag",
                MutationOutcome.NOT_STARTED,
                error_kind="invalid_held_drag_args",
            )

        with _held_drag_operation_lock:
            with _held_drag_state_lock:
                owner = _held_drag_owner
                if owner is None:
                    return self._from_backend(
                        "drag",
                        RawBackendResult(True, {
                            "ok": True,
                            "cleanup_noop": True,
                            "held_after": False,
                            "segment_phase": "cleanup",
                        }),
                    )
                if owner.token != hold_token:
                    return self._receipt(
                        "drag",
                        MutationOutcome.NOT_STARTED,
                        error_kind="held_drag_token_mismatch",
                    )
                point = owner.point
                hwnd = owner.hwnd
                backend = owner.backend

            receipt = self._call(
                "drag",
                backend,
                point[0],
                point[1],
                point[0],
                point[1],
                hwnd=hwnd,
                final_guard=lambda _x, _y: FinalPointerGuardDecision.ADMITTED,
                pointer_already_held=True,
                keep_holding=False,
                hold_token=hold_token,
                cleanup_only=True,
            )
            reported_held_after = (
                None
                if receipt.backend_result is None
                else receipt.backend_result.facts.get("held_after")
            )
            cleanup_completed = (
                receipt.outcome is MutationOutcome.APPLIED
                and (
                    reported_held_after is None
                    or (
                        type(reported_held_after) is bool
                        and reported_held_after is False
                    )
                )
            )
            with _held_drag_state_lock:
                owner = _held_drag_owner
                if owner is not None and owner.token == hold_token:
                    if cleanup_completed:
                        _held_drag_owner = None
            if cleanup_completed:
                return receipt
            return self._held_drag_unknown(
                receipt,
                error_kind="held_drag_cleanup_uncertain",
            )

    def type_text(
        self,
        context: RawActionContext,
        text: str,
        *,
        final_guard: FinalKeyboardGuard,
    ) -> RawActionReceipt:
        context_error = self._context_error(context)
        if context_error is not None:
            return self._receipt(
                "type_text", MutationOutcome.NOT_STARTED,
                error_kind=context_error,
            )
        held_block = self._held_drag_blocked("type_text")
        if held_block is not None:
            return held_block
        if (
            type(text) is not str
            or not 1 <= len(text) <= MAX_TYPE_TEXT_CODEPOINTS
            or not callable(final_guard)
        ):
            return self._receipt(
                "type_text", MutationOutcome.NOT_STARTED,
                error_kind="invalid_args",
            )
        return self._call(
            "type_text",
            self._backends.admitted_physical_type_text,
            text,
            hwnd=context.target_hwnd,
            final_guard=final_guard,
        )

    def hotkey(
        self,
        context: RawActionContext,
        chord: HotkeyChord,
        *,
        final_guard: FinalKeyboardGuard,
    ) -> RawActionReceipt:
        context_error = self._context_error(context)
        if context_error is not None:
            return self._receipt(
                "hotkey", MutationOutcome.NOT_STARTED,
                error_kind=context_error,
            )
        held_block = self._held_drag_blocked("hotkey")
        if held_block is not None:
            return held_block
        if not isinstance(chord, HotkeyChord) or not callable(final_guard):
            return self._receipt(
                "hotkey", MutationOutcome.NOT_STARTED,
                error_kind="invalid_args",
            )
        return self._call(
            "hotkey",
            self._backends.admitted_physical_hotkey,
            chord,
            hwnd=context.target_hwnd,
            final_guard=final_guard,
        )


__all__ = [
    "AdmittedPhysicalClickBackend", "AdmittedPhysicalDragBackend",
    "AdmittedPhysicalHotkeyBackend",
    "AdmittedPhysicalScrollBackend", "AdmittedPhysicalTypeTextBackend",
    "CANONICAL_HOTKEY_KEYS", "CANONICAL_HOTKEY_MODIFIERS",
    "ClickCount", "ClickMechanism",
    "FinalKeyboardGuard", "FinalKeyboardGuardDecision", "FinalPointerGuard",
    "FinalPointerGuardDecision", "HotkeyChord", "MAX_TYPE_TEXT_CODEPOINTS",
    "MAX_DRAG_INTERMEDIATE_POINTS",
    "MouseButton",
    "RawActionContext", "RawActionReceipt", "RawBackendResult",
    "RawDeviceBackends", "RawDeviceExecutor",
]
