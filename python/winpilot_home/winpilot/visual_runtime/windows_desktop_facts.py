"""Injected Windows identity facts for atomic vNext desktop admission.

This module reads mechanical OS identity only.  It never focuses a window,
repairs a target, interprets pixels, or selects another semantic destination.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..action.raw_device import (
    FinalKeyboardGuardDecision,
    FinalPointerGuardDecision,
)
from .commit_admission import (
    CommitInputKind,
    CommitAdmissionRequest,
    CommitEnvironment,
    TargetCommitIdentity,
)


Point = tuple[int, int]
Region = tuple[int, int, int, int]


class WindowsDesktopFactsError(ValueError):
    """A complete current Windows identity could not be proved."""


@dataclass(frozen=True, slots=True)
class RuntimeCommitState:
    """Read-only Runtime facts which Windows APIs cannot establish."""

    pointer_released: bool
    mutation_allowed: bool
    stop_requested: bool
    canceled: bool

    def __post_init__(self) -> None:
        for name in (
            "pointer_released", "mutation_allowed", "stop_requested", "canceled",
        ):
            if type(getattr(self, name)) is not bool:
                raise WindowsDesktopFactsError(f"{name} must be boolean")


@runtime_checkable
class RuntimeCommitStatePort(Protocol):
    """Return current Runtime authority without mutating desktop or Mission."""

    def read_commit_state(
        self, request: CommitAdmissionRequest,
    ) -> RuntimeCommitState: ...


@runtime_checkable
class WindowsDesktopFactsBackend(Protocol):
    """Small injectable boundary around the required Windows calls."""

    def is_window(self, hwnd: int) -> bool: ...
    def root_hwnd(self, hwnd: int) -> int: ...
    def process_id(self, hwnd: int) -> int: ...
    def window_region(self, hwnd: int) -> Region: ...
    def foreground_hwnd(self) -> int: ...
    def window_from_point(self, point: Point) -> int: ...


class PyWin32DesktopFactsBackend:
    """Production backend; imports stay behind the injectable boundary."""

    @staticmethod
    def is_window(hwnd: int) -> bool:
        import win32gui

        return bool(win32gui.IsWindow(int(hwnd)))

    @staticmethod
    def root_hwnd(hwnd: int) -> int:
        import win32gui

        value = int(win32gui.GetAncestor(int(hwnd), 2) or 0)
        return value or int(hwnd)

    @staticmethod
    def process_id(hwnd: int) -> int:
        import win32process

        _thread_id, process_id = win32process.GetWindowThreadProcessId(int(hwnd))
        return int(process_id or 0)

    @staticmethod
    def window_region(hwnd: int) -> Region:
        from ..utils.screenshot import window_region

        region = window_region(int(hwnd))
        return (region.left, region.top, region.width, region.height)

    @staticmethod
    def foreground_hwnd() -> int:
        import win32gui

        return int(win32gui.GetForegroundWindow() or 0)

    @staticmethod
    def window_from_point(point: Point) -> int:
        import win32gui

        return int(win32gui.WindowFromPoint(point) or 0)


@dataclass(frozen=True, slots=True)
class WindowIdentity:
    hwnd: int
    root_hwnd: int
    process_id: int

    def __post_init__(self) -> None:
        for name in ("hwnd", "root_hwnd", "process_id"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise WindowsDesktopFactsError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class WindowsDesktopFacts:
    target: TargetCommitIdentity
    foreground: WindowIdentity | None
    hit_test_point: Point | None
    hit: WindowIdentity | None

    def __post_init__(self) -> None:
        if not isinstance(self.target, TargetCommitIdentity):
            raise WindowsDesktopFactsError("target must be TargetCommitIdentity")
        if self.foreground is not None and not isinstance(
            self.foreground, WindowIdentity
        ):
            raise WindowsDesktopFactsError("foreground identity is invalid")
        if (self.hit_test_point is None) != (self.hit is None):
            raise WindowsDesktopFactsError(
                "hit point and hit identity must be present together"
            )
        if self.hit_test_point is not None and (
            not isinstance(self.hit_test_point, tuple)
            or len(self.hit_test_point) != 2
            or any(type(value) is not int for value in self.hit_test_point)
        ):
            raise WindowsDesktopFactsError("hit_test_point must contain two integers")


@dataclass(frozen=True, slots=True)
class _TargetSample:
    window: WindowIdentity
    region: Region


class WindowsDesktopFactsReader:
    """Read one fail-closed Windows snapshot with target double-sampling."""

    def __init__(
        self, backend: WindowsDesktopFactsBackend | None = None,
    ) -> None:
        resolved = backend or PyWin32DesktopFactsBackend()
        if not isinstance(resolved, WindowsDesktopFactsBackend):
            raise TypeError("backend must implement WindowsDesktopFactsBackend")
        self._backend = resolved

    @staticmethod
    def _positive(value: object, name: str) -> int:
        if type(value) is not int or value <= 0:
            raise WindowsDesktopFactsError(f"{name} is unavailable")
        return value

    def _live_window(self, hwnd: object, name: str) -> WindowIdentity:
        value = self._positive(hwnd, f"{name} hwnd")
        if self._backend.is_window(value) is not True:
            raise WindowsDesktopFactsError(f"{name} hwnd is not a live window")
        return WindowIdentity(
            value,
            self._positive(self._backend.root_hwnd(value), f"{name} root hwnd"),
            self._positive(self._backend.process_id(value), f"{name} process id"),
        )

    def _optional_window(self, hwnd: object, name: str) -> WindowIdentity | None:
        if type(hwnd) is not int or hwnd <= 0:
            return None
        try:
            return self._live_window(hwnd, name)
        except WindowsDesktopFactsError:
            return None

    def _target_sample(self, hwnd: int) -> _TargetSample:
        window = self._live_window(hwnd, "target")
        raw_region = self._backend.window_region(hwnd)
        if (
            not isinstance(raw_region, tuple)
            or len(raw_region) != 4
            or any(type(value) is not int for value in raw_region)
            or raw_region[2] <= 0
            or raw_region[3] <= 0
        ):
            raise WindowsDesktopFactsError("target DWM region is unavailable")
        return _TargetSample(window, raw_region)

    def read_target_identity(
        self,
        hwnd: int,
        *,
        binding_id: str,
        epoch: int,
    ) -> TargetCommitIdentity:
        """Read one stable target identity without selecting or focusing it.

        Runtime composition needs the same mechanical HWND/root/process/DWM
        facts before it can publish its first frame.  Keep that read inside
        this existing Windows-facts owner instead of duplicating Win32 calls
        in a coordinator.
        """

        first = self._target_sample(hwnd)
        second = self._target_sample(hwnd)
        if second != first:
            raise WindowsDesktopFactsError(
                "target identity changed during Windows observation"
            )
        return TargetCommitIdentity(
            binding_id=binding_id,
            hwnd=first.window.hwnd,
            root_hwnd=first.window.root_hwnd,
            process_id=first.window.process_id,
            epoch=epoch,
            window_region=first.region,
        )

    def read(self, request: CommitAdmissionRequest) -> WindowsDesktopFacts:
        if not isinstance(request, CommitAdmissionRequest):
            raise TypeError("request must be CommitAdmissionRequest")
        expected = request.target
        first = self._target_sample(expected.hwnd)
        foreground = self._optional_window(
            self._backend.foreground_hwnd(), "foreground",
        )
        hit = None
        if request.input_kind is CommitInputKind.POINT:
            assert request.screen_point is not None
            hit = self._optional_window(
                self._backend.window_from_point(request.screen_point), "hit",
            )
        second = self._target_sample(expected.hwnd)
        if second != first:
            raise WindowsDesktopFactsError(
                "target identity changed during Windows observation"
            )
        target = TargetCommitIdentity(
            binding_id=expected.binding_id,
            hwnd=first.window.hwnd,
            root_hwnd=first.window.root_hwnd,
            process_id=first.window.process_id,
            epoch=expected.epoch,
            window_region=first.region,
        )
        return WindowsDesktopFacts(
            target=target,
            foreground=foreground,
            hit_test_point=request.screen_point if hit is not None else None,
            hit=hit,
        )

    def read_final_pointer(self, request: CommitAdmissionRequest) -> WindowsDesktopFacts:
        """Sample only target geometry/identity and point hit around final guard."""
        if not isinstance(request, CommitAdmissionRequest):
            raise TypeError("request must be CommitAdmissionRequest")
        if request.input_kind is not CommitInputKind.POINT:
            raise WindowsDesktopFactsError("final pointer guard requires point input")
        assert request.screen_point is not None
        expected = request.target
        first = self._target_sample(expected.hwnd)
        hit = self._optional_window(
            self._backend.window_from_point(request.screen_point), "hit",
        )
        second = self._target_sample(expected.hwnd)
        if second != first:
            raise WindowsDesktopFactsError(
                "target identity changed during final pointer observation"
            )
        return WindowsDesktopFacts(
            target=TargetCommitIdentity(
                expected.binding_id,
                first.window.hwnd,
                first.window.root_hwnd,
                first.window.process_id,
                expected.epoch,
                first.region,
            ),
            foreground=None,
            hit_test_point=request.screen_point if hit is not None else None,
            hit=hit,
        )


class WindowsPhysicalInputFinalGuard:
    """Fail-closed mechanical identity check; never selects or moves a target."""

    def __init__(self, reader: WindowsDesktopFactsReader) -> None:
        if not isinstance(reader, WindowsDesktopFactsReader):
            raise TypeError("reader must be WindowsDesktopFactsReader")
        self._reader = reader

    def check_before_pointer_input(
        self, request: CommitAdmissionRequest, screen_point: Point,
    ) -> FinalPointerGuardDecision:
        if screen_point != request.screen_point:
            return FinalPointerGuardDecision.HIT_TEST_CHANGED
        try:
            facts = self._reader.read_final_pointer(request)
        except Exception:
            return FinalPointerGuardDecision.TARGET_UNAVAILABLE
        expected, actual = request.target, facts.target
        if actual.root_hwnd != expected.root_hwnd:
            return FinalPointerGuardDecision.TARGET_ROOT_CHANGED
        if actual.process_id != expected.process_id:
            return FinalPointerGuardDecision.TARGET_PROCESS_CHANGED
        if actual.window_region != expected.window_region:
            return FinalPointerGuardDecision.TARGET_GEOMETRY_CHANGED
        left, top, width, height = expected.window_region
        x, y = request.screen_point
        if not (left <= x < left + width and top <= y < top + height):
            return FinalPointerGuardDecision.POINT_OUTSIDE_TARGET
        if facts.hit is None or facts.hit_test_point != request.screen_point:
            return FinalPointerGuardDecision.HIT_TEST_UNAVAILABLE
        if (
            facts.hit.root_hwnd != expected.root_hwnd
            or facts.hit.process_id != expected.process_id
        ):
            return FinalPointerGuardDecision.HIT_TEST_CHANGED
        return FinalPointerGuardDecision.ADMITTED

    def check_before_keyboard_input(
        self, request: CommitAdmissionRequest,
    ) -> FinalKeyboardGuardDecision:
        if (
            not isinstance(request, CommitAdmissionRequest)
            or request.input_kind is not CommitInputKind.KEYBOARD
            or request.screen_point is not None
        ):
            return FinalKeyboardGuardDecision.TARGET_UNAVAILABLE
        try:
            facts = self._reader.read(request)
        except Exception:
            return FinalKeyboardGuardDecision.TARGET_UNAVAILABLE
        expected, actual = request.target, facts.target
        if actual.root_hwnd != expected.root_hwnd:
            return FinalKeyboardGuardDecision.TARGET_ROOT_CHANGED
        if actual.process_id != expected.process_id:
            return FinalKeyboardGuardDecision.TARGET_PROCESS_CHANGED
        if actual.window_region != expected.window_region:
            return FinalKeyboardGuardDecision.TARGET_GEOMETRY_CHANGED
        if facts.foreground is None:
            return FinalKeyboardGuardDecision.FOREGROUND_UNAVAILABLE
        if (
            facts.foreground.root_hwnd != expected.root_hwnd
            or facts.foreground.process_id != expected.process_id
        ):
            return FinalKeyboardGuardDecision.FOREGROUND_CHANGED
        return FinalKeyboardGuardDecision.ADMITTED


class WindowsDesktopCommitObservation:
    """AtomicDesktopController observation adapter; no mutation or recovery."""

    def __init__(
        self,
        reader: WindowsDesktopFactsReader,
        runtime_state: RuntimeCommitStatePort,
    ) -> None:
        if not isinstance(reader, WindowsDesktopFactsReader):
            raise TypeError("reader must be WindowsDesktopFactsReader")
        if not isinstance(runtime_state, RuntimeCommitStatePort):
            raise TypeError("runtime_state must implement RuntimeCommitStatePort")
        self._reader = reader
        self._runtime_state = runtime_state

    def observe_commit(self, request: CommitAdmissionRequest) -> CommitEnvironment:
        facts = self._reader.read(request)
        state = self._runtime_state.read_commit_state(request)
        if not isinstance(state, RuntimeCommitState):
            raise WindowsDesktopFactsError(
                "RuntimeCommitStatePort returned an invalid state"
            )
        foreground, hit = facts.foreground, facts.hit
        return CommitEnvironment(
            target=facts.target,
            pointer_released=state.pointer_released,
            mutation_allowed=state.mutation_allowed,
            stop_requested=state.stop_requested,
            canceled=state.canceled,
            foreground_hwnd=None if foreground is None else foreground.hwnd,
            foreground_root_hwnd=(
                None if foreground is None else foreground.root_hwnd
            ),
            foreground_process_id=(
                None if foreground is None else foreground.process_id
            ),
            hit_test_point=facts.hit_test_point,
            hit_hwnd=None if hit is None else hit.hwnd,
            hit_root_hwnd=None if hit is None else hit.root_hwnd,
            hit_process_id=None if hit is None else hit.process_id,
        )


__all__ = [
    "PyWin32DesktopFactsBackend",
    "RuntimeCommitState",
    "RuntimeCommitStatePort",
    "WindowIdentity",
    "WindowsPhysicalInputFinalGuard",
    "WindowsDesktopCommitObservation",
    "WindowsDesktopFacts",
    "WindowsDesktopFactsBackend",
    "WindowsDesktopFactsError",
    "WindowsDesktopFactsReader",
]
