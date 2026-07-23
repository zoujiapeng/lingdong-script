"""Injected Windows identity facts for atomic vNext desktop admission.

This module reads mechanical OS identity only.  It never focuses a window,
repairs a target, interprets pixels, or selects another semantic destination.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import math
import ntpath
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
from ..process_identity import (
    ProcessIdentityError,
    canonical_process_identity,
    process_identity_for,
)


Point = tuple[int, int]
Region = tuple[int, int, int, int]


class WindowsDesktopFactsError(ValueError):
    """A complete current Windows identity could not be proved."""


class WindowsDesktopFactsDriftError(WindowsDesktopFactsError):
    """A sampled Windows identity changed during one observation."""


class WindowsDesktopFactsSourceError(WindowsDesktopFactsError):
    """A required Windows fact source could not be read."""


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
    def process_creation_time_ns(self, process_id: int) -> int: ...
    def process_executable_path(self, process_id: int) -> str: ...
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
    def process_creation_time_ns(process_id: int) -> int:
        try:
            import psutil

            value = psutil.Process(int(process_id)).create_time()
        except Exception as exc:
            raise WindowsDesktopFactsSourceError(
                "process creation time is unavailable"
            ) from exc
        if (
            type(value) not in (int, float)
            or not math.isfinite(float(value))
            or float(value) <= 0
        ):
            raise WindowsDesktopFactsSourceError(
                "process creation time is unavailable"
            )
        result = int(float(value) * 1_000_000_000)
        if result <= 0:
            raise WindowsDesktopFactsSourceError(
                "process creation time is unavailable"
            )
        return result

    @staticmethod
    def process_executable_path(process_id: int) -> str:
        try:
            import psutil

            value = psutil.Process(int(process_id)).exe()
        except Exception as exc:
            raise WindowsDesktopFactsSourceError(
                "process executable path is unavailable"
            ) from exc
        if type(value) is not str or not value.strip() or not ntpath.isabs(value.strip()):
            raise WindowsDesktopFactsSourceError(
                "process executable path is unavailable or not absolute"
            )
        return ntpath.normpath(value.strip())

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
    def shell_hwnd() -> int:
        return int(ctypes.windll.user32.GetShellWindow() or 0)

    @staticmethod
    def desktop_hwnd() -> int:
        return int(ctypes.windll.user32.GetDesktopWindow() or 0)

    @staticmethod
    def window_title(hwnd: int) -> str:
        """Return optional display text; title is never an identity fact."""

        import win32gui

        return str(win32gui.GetWindowText(int(hwnd)) or "")

    @staticmethod
    def window_from_point(point: Point) -> int:
        import win32gui

        return int(win32gui.WindowFromPoint(point) or 0)


@dataclass(frozen=True, slots=True)
class WindowIdentity:
    hwnd: int
    root_hwnd: int
    process_id: int
    process_identity: str

    def __post_init__(self) -> None:
        for name in ("hwnd", "root_hwnd", "process_id"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise WindowsDesktopFactsError(f"{name} must be a positive integer")
        try:
            value = canonical_process_identity(self.process_identity)
        except ProcessIdentityError as exc:
            raise WindowsDesktopFactsError(str(exc)) from exc
        object.__setattr__(self, "process_identity", value)


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
class TargetActivationFacts:
    """Fresh strong facts for one unbound existing-window candidate."""

    target: WindowIdentity
    window_region: Region
    foreground: WindowIdentity | None
    title_snapshot: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.target, WindowIdentity):
            raise WindowsDesktopFactsError("activation target identity is invalid")
        if (
            not isinstance(self.window_region, tuple)
            or len(self.window_region) != 4
            or any(type(value) is not int for value in self.window_region)
            or self.window_region[2] <= 0
            or self.window_region[3] <= 0
        ):
            raise WindowsDesktopFactsError("activation target region is invalid")
        if self.foreground is not None and not isinstance(
            self.foreground, WindowIdentity
        ):
            raise WindowsDesktopFactsError("activation foreground identity is invalid")
        if type(self.title_snapshot) is not str or len(self.title_snapshot) > 4096:
            raise WindowsDesktopFactsError("activation title snapshot is invalid")

    @property
    def exact_foreground(self) -> bool:
        return bool(
            self.foreground is not None
            and self.foreground == self.target
        )


@dataclass(frozen=True, slots=True)
class _TargetSample:
    window: WindowIdentity
    region: Region


class WindowsDesktopFactsReader:
    """Read one fail-closed Windows snapshot with target double-sampling."""

    def __init__(
        self, backend: WindowsDesktopFactsBackend | None = None,
    ) -> None:
        resolved = (
            backend
            if backend is not None
            else PyWin32DesktopFactsBackend()
        )
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
        root_hwnd = self._positive(
            self._backend.root_hwnd(value), f"{name} root hwnd",
        )
        process_id = self._positive(
            self._backend.process_id(value), f"{name} process id",
        )
        try:
            process_identity = process_identity_for(
                process_id=process_id,
                creation_time_ns=self._positive(
                    self._backend.process_creation_time_ns(process_id),
                    f"{name} process creation time",
                ),
                executable_path=self._backend.process_executable_path(process_id),
            )
        except (ProcessIdentityError, KeyError) as exc:
            raise WindowsDesktopFactsError(
                f"{name} strong process identity is unavailable"
            ) from exc
        return WindowIdentity(
            value,
            root_hwnd,
            process_id,
            process_identity,
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
            raise WindowsDesktopFactsDriftError(
                "target identity changed during Windows observation"
            )
        return TargetCommitIdentity(
            binding_id=binding_id,
            hwnd=first.window.hwnd,
            root_hwnd=first.window.root_hwnd,
            process_id=first.window.process_id,
            process_identity=first.window.process_identity,
            epoch=epoch,
            window_region=first.region,
        )

    def read_activation_facts(
        self,
        hwnd: int,
        *,
        strict_foreground: bool = False,
    ) -> TargetActivationFacts:
        """Read stable unbound target and foreground facts without mutation.

        The target is sampled on both sides of the foreground read.  An
        optional title read is informational only; failure yields an honest
        empty snapshot and never weakens the strong HWND/process checks.
        """

        if type(strict_foreground) is not bool:
            raise TypeError("strict_foreground must be boolean")
        first = self._target_sample(hwnd)
        foreground_hwnd = self._backend.foreground_hwnd()
        if strict_foreground and type(foreground_hwnd) is int:
            foreground = (
                self._live_window(foreground_hwnd, "foreground")
                if foreground_hwnd > 0
                else None
            )
        elif strict_foreground:
            raise WindowsDesktopFactsError("foreground hwnd is unavailable")
        else:
            foreground = self._optional_window(foreground_hwnd, "foreground")
        title = ""
        read_title = getattr(self._backend, "window_title", None)
        if callable(read_title):
            try:
                raw_title = read_title(hwnd)
                if type(raw_title) is str and len(raw_title) <= 4096:
                    title = raw_title
            except Exception:
                title = ""
        second = self._target_sample(hwnd)
        if second != first:
            raise WindowsDesktopFactsDriftError(
                "activation target changed during Windows observation"
            )
        return TargetActivationFacts(
            target=first.window,
            window_region=first.region,
            foreground=foreground,
            title_snapshot=title,
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
            raise WindowsDesktopFactsDriftError(
                "target identity changed during Windows observation"
            )
        target = TargetCommitIdentity(
            binding_id=expected.binding_id,
            hwnd=first.window.hwnd,
            root_hwnd=first.window.root_hwnd,
            process_id=first.window.process_id,
            process_identity=first.window.process_identity,
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
            raise WindowsDesktopFactsDriftError(
                "target identity changed during final pointer observation"
            )
        return WindowsDesktopFacts(
            target=TargetCommitIdentity(
                binding_id=expected.binding_id,
                hwnd=first.window.hwnd,
                root_hwnd=first.window.root_hwnd,
                process_id=first.window.process_id,
                process_identity=first.window.process_identity,
                epoch=expected.epoch,
                window_region=first.region,
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
        if actual.process_identity != expected.process_identity:
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
            or facts.hit.process_identity != expected.process_identity
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
        if actual.process_identity != expected.process_identity:
            return FinalKeyboardGuardDecision.TARGET_PROCESS_CHANGED
        if actual.window_region != expected.window_region:
            return FinalKeyboardGuardDecision.TARGET_GEOMETRY_CHANGED
        if facts.foreground is None:
            return FinalKeyboardGuardDecision.FOREGROUND_UNAVAILABLE
        if (
            facts.foreground.root_hwnd != expected.root_hwnd
            or facts.foreground.process_id != expected.process_id
            or facts.foreground.process_identity != expected.process_identity
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
            foreground_process_identity=(
                None if foreground is None else foreground.process_identity
            ),
            hit_test_point=facts.hit_test_point,
            hit_hwnd=None if hit is None else hit.hwnd,
            hit_root_hwnd=None if hit is None else hit.root_hwnd,
            hit_process_id=None if hit is None else hit.process_id,
            hit_process_identity=None if hit is None else hit.process_identity,
        )


__all__ = [
    "PyWin32DesktopFactsBackend",
    "RuntimeCommitState",
    "RuntimeCommitStatePort",
    "TargetActivationFacts",
    "WindowIdentity",
    "WindowsPhysicalInputFinalGuard",
    "WindowsDesktopCommitObservation",
    "WindowsDesktopFacts",
    "WindowsDesktopFactsBackend",
    "WindowsDesktopFactsDriftError",
    "WindowsDesktopFactsError",
    "WindowsDesktopFactsReader",
    "WindowsDesktopFactsSourceError",
]
