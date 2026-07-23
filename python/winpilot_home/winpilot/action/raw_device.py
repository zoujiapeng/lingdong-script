"""Typed, session-free execution of already-admitted physical input."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
import math
from types import MappingProxyType
from typing import Any

from ..process_identity import (
    ProcessIdentityError,
    canonical_executable_path,
    canonical_process_identity,
)
from ..visual_runtime.keyboard_contracts import (
    CANONICAL_HOTKEY_KEYS,
    CANONICAL_HOTKEY_MODIFIERS,
    HotkeyChord,
    MAX_TYPE_TEXT_CODEPOINTS,
)
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


class FinalTargetActivationGuardDecision(str, Enum):
    """Mechanical identity verdict immediately before window activation."""

    ADMITTED = "admitted"
    TARGET_UNAVAILABLE = "target_unavailable"
    TARGET_ROOT_CHANGED = "target_root_changed"
    TARGET_PROCESS_CHANGED = "target_process_changed"
    AUTHORITY_CHANGED = "authority_changed"
    STOP_REQUESTED = "stop_requested"


class FinalTargetLaunchGuardDecision(str, Enum):
    """Mechanical authority verdict immediately before one EXE launch."""

    ADMITTED = "admitted"
    AUTHORITY_CHANGED = "authority_changed"
    EXISTING_PROCESS = "existing_process"
    PROCESS_STATE_UNKNOWN = "process_state_unknown"
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
class RawTargetActivationContext:
    """Strong existing-window identity already selected by the Controller."""

    target_hwnd: int
    target_root_hwnd: int
    target_process_id: int
    target_process_identity: str

    def __post_init__(self) -> None:
        if type(self.target_hwnd) is not int or self.target_hwnd <= 0:
            raise ValueError("target_hwnd must be a positive integer")
        if type(self.target_root_hwnd) is not int or self.target_root_hwnd <= 0:
            raise ValueError("target_root_hwnd must be a positive integer")
        if type(self.target_process_id) is not int or self.target_process_id <= 0:
            raise ValueError("target_process_id must be a positive integer")
        try:
            identity = canonical_process_identity(self.target_process_identity)
        except ProcessIdentityError as exc:
            raise ValueError("target_process_identity must be canonical") from exc
        object.__setattr__(self, "target_process_identity", identity)


@dataclass(frozen=True, slots=True)
class RawTargetLaunchContext:
    """One exact executable path; arguments and Runtime identities are absent."""

    executable_path: str

    def __post_init__(self) -> None:
        try:
            path = canonical_executable_path(self.executable_path)
        except ProcessIdentityError as exc:
            raise ValueError("executable_path must be an absolute .exe path") from exc
        object.__setattr__(self, "executable_path", path)


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
            "scroll", "click", "type_text", "hotkey", "activate_target",
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


@dataclass(frozen=True, slots=True)
class RawTargetLaunchReceipt:
    """Strict result of at most one CreateProcessW request."""

    outcome: MutationOutcome
    launch_request_count: int | None
    process_id: int | None = None
    process_identity: str | None = None
    error_kind: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, MutationOutcome):
            raise ValueError("launch outcome must be MutationOutcome")
        if self.launch_request_count is not None and (
            type(self.launch_request_count) is not int
            or self.launch_request_count not in {0, 1}
        ):
            raise ValueError("launch_request_count must be zero, one, or null")
        paired = self.process_id is not None or self.process_identity is not None
        if paired:
            if type(self.process_id) is not int or self.process_id <= 0:
                raise ValueError("launch process_id must be positive")
            try:
                identity = canonical_process_identity(self.process_identity)
            except ProcessIdentityError as exc:
                raise ValueError("launch process_identity must be canonical") from exc
            object.__setattr__(self, "process_identity", identity)
        error = self.error_kind
        if error is not None and (
            type(error) is not str or not error.strip() or len(error) > 192
        ):
            raise ValueError("error_kind must be bounded non-empty text or null")
        object.__setattr__(self, "error_kind", None if error is None else error.strip())
        if self.outcome is MutationOutcome.APPLIED:
            valid = self.launch_request_count == 1 and paired and error is None
        elif self.outcome is MutationOutcome.NOT_STARTED:
            valid = self.launch_request_count == 0 and not paired and error is not None
        elif self.outcome is MutationOutcome.OUTCOME_UNKNOWN:
            valid = self.launch_request_count in {None, 1} and not paired and error is not None
        else:
            valid = False
        if not valid:
            raise ValueError("launch receipt fields contradict its outcome")

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "launch_request_count": self.launch_request_count,
            "process_id": self.process_id,
            "process_identity": self.process_identity,
            "error_kind": self.error_kind,
        }


RawBackend = Callable[..., RawBackendResult]
FinalPointerGuard = Callable[[int, int], FinalPointerGuardDecision]
FinalKeyboardGuard = Callable[[], FinalKeyboardGuardDecision]
FinalTargetActivationGuard = Callable[[], FinalTargetActivationGuardDecision]
FinalTargetLaunchGuard = Callable[[], FinalTargetLaunchGuardDecision]


@dataclass(frozen=True, slots=True)
class _AdmittedBackend:
    """Queue-free backend marker shared by the typed raw primitives."""

    execute: Callable[..., Any]
    description = "admitted physical backend"

    def __post_init__(self) -> None:
        if not callable(self.execute):
            raise TypeError(f"{self.description} must be callable")
        if hasattr(self.execute, "__wrapped__"):
            raise TypeError(f"{self.description} cannot wrap a decorated entry")

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.execute(*args, **kwargs)


class AdmittedPhysicalClickBackend(_AdmittedBackend):
    description = "admitted physical click backend"


class AdmittedPhysicalScrollBackend(_AdmittedBackend):
    description = "admitted physical scroll backend"


class AdmittedPhysicalTypeTextBackend(_AdmittedBackend):
    description = "admitted physical type-text backend"


class AdmittedPhysicalHotkeyBackend(_AdmittedBackend):
    description = "admitted physical hotkey backend"


class AdmittedPhysicalTargetActivationBackend(_AdmittedBackend):
    description = "admitted physical target activation backend"


class AdmittedPhysicalTargetLaunchBackend(_AdmittedBackend):
    description = "admitted physical target launch backend"


@dataclass(frozen=True, slots=True)
class RawDeviceBackends:
    scroll: RawBackend | None = None
    click: RawBackend | None = None
    admitted_physical_click: AdmittedPhysicalClickBackend | None = None
    admitted_physical_scroll: AdmittedPhysicalScrollBackend | None = None
    admitted_physical_type_text: AdmittedPhysicalTypeTextBackend | None = None
    admitted_physical_hotkey: AdmittedPhysicalHotkeyBackend | None = None
    admitted_physical_target_activation: (
        AdmittedPhysicalTargetActivationBackend | None
    ) = None
    admitted_physical_target_launch: AdmittedPhysicalTargetLaunchBackend | None = None

    def __post_init__(self) -> None:
        for name in ("scroll", "click"):
            backend = getattr(self, name)
            if backend is not None and not callable(backend):
                raise TypeError(f"{name} backend must be callable or null")
        admitted_types = (
            ("admitted_physical_click", AdmittedPhysicalClickBackend),
            ("admitted_physical_scroll", AdmittedPhysicalScrollBackend),
            ("admitted_physical_type_text", AdmittedPhysicalTypeTextBackend),
            ("admitted_physical_hotkey", AdmittedPhysicalHotkeyBackend),
            (
                "admitted_physical_target_activation",
                AdmittedPhysicalTargetActivationBackend,
            ),
            ("admitted_physical_target_launch", AdmittedPhysicalTargetLaunchBackend),
        )
        for name, expected in admitted_types:
            value = getattr(self, name)
            if value is not None and not isinstance(value, expected):
                raise TypeError(f"{name} must be {expected.__name__} or null")


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
            ("scroll", self._backends.admitted_physical_scroll),
            ("type_text", self._backends.admitted_physical_type_text),
            ("hotkey", self._backends.admitted_physical_hotkey),
            (
                "activate_target",
                self._backends.admitted_physical_target_activation,
            ),
            ("launch_target", self._backends.admitted_physical_target_launch),
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

    def scroll(
        self,
        context: RawActionContext,
        x: int,
        y: int,
        amount: int,
        *,
        final_guard: FinalPointerGuard | None = None,
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
        backend = self._backends.scroll
        call_kwargs: dict[str, Any] = {"hwnd": context.target_hwnd}
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
        backend = self._backends.click
        call_kwargs: dict[str, Any] = {"hwnd": context.target_hwnd}
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
        return self._call(
            "click", backend, x, y, button=button, count=count,
            mechanism=mechanism, **call_kwargs,
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

    def activate_target(
        self,
        context: RawTargetActivationContext,
        *,
        final_guard: FinalTargetActivationGuard,
    ) -> RawActionReceipt:
        """Invoke one admitted activation backend without fallback or replay."""

        if not isinstance(context, RawTargetActivationContext):
            return self._receipt(
                "activate_target",
                MutationOutcome.NOT_STARTED,
                error_kind="invalid_context",
            )
        if not callable(final_guard):
            return self._receipt(
                "activate_target",
                MutationOutcome.NOT_STARTED,
                error_kind="invalid_final_guard",
            )
        return self._call(
            "activate_target",
            self._backends.admitted_physical_target_activation,
            context,
            final_guard=final_guard,
        )

    def launch_target(
        self,
        context: RawTargetLaunchContext,
        *,
        final_guard: FinalTargetLaunchGuard,
    ) -> RawTargetLaunchReceipt:
        """Invoke one exact EXE backend without fallback or replay."""

        if not isinstance(context, RawTargetLaunchContext):
            return RawTargetLaunchReceipt(
                MutationOutcome.NOT_STARTED, 0, error_kind="invalid_context"
            )
        if not callable(final_guard):
            return RawTargetLaunchReceipt(
                MutationOutcome.NOT_STARTED, 0, error_kind="invalid_final_guard"
            )
        backend = self._backends.admitted_physical_target_launch
        if backend is None:
            return RawTargetLaunchReceipt(
                MutationOutcome.NOT_STARTED, 0, error_kind="backend_unavailable"
            )
        try:
            result = backend(context, final_guard=final_guard)
        except Exception:
            return RawTargetLaunchReceipt(
                MutationOutcome.OUTCOME_UNKNOWN,
                None,
                error_kind="backend_exception",
            )
        if not isinstance(result, RawTargetLaunchReceipt):
            return RawTargetLaunchReceipt(
                MutationOutcome.OUTCOME_UNKNOWN,
                None,
                error_kind="backend_result_uncertain",
            )
        return result


__all__ = [
    "AdmittedPhysicalClickBackend", "AdmittedPhysicalHotkeyBackend",
    "AdmittedPhysicalScrollBackend", "AdmittedPhysicalTargetActivationBackend",
    "AdmittedPhysicalTargetLaunchBackend",
    "AdmittedPhysicalTypeTextBackend",
    "CANONICAL_HOTKEY_KEYS", "CANONICAL_HOTKEY_MODIFIERS",
    "ClickCount", "ClickMechanism",
    "FinalKeyboardGuard", "FinalKeyboardGuardDecision", "FinalPointerGuard",
    "FinalPointerGuardDecision", "FinalTargetActivationGuard",
    "FinalTargetActivationGuardDecision", "FinalTargetLaunchGuard",
    "FinalTargetLaunchGuardDecision", "HotkeyChord",
    "MAX_TYPE_TEXT_CODEPOINTS",
    "MouseButton",
    "RawActionContext", "RawActionReceipt", "RawBackendResult",
    "RawDeviceBackends", "RawDeviceExecutor", "RawTargetActivationContext",
    "RawTargetLaunchContext", "RawTargetLaunchReceipt",
]
